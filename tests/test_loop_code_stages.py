"""Code kind's per-cycle stage-gate orchestration (Slice 2c.iv.b) — on_new_cycle
advances SDLC stages on a passed gate, completes on the last, and handles the
no-stage-plan project-level gate. The judge + verify command are stubbed."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.loop import kinds, store
from personalclaw.loop.loop import Loop


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    kinds.ensure_loaded()
    return tmp_path


class _FakeSvc:
    """Minimal AutoNudgeService stub — no live task-workers (sequential tests)."""
    _loops: dict = {}

    def get_by_session(self, session_name):
        return None


class _Ctx:
    def __init__(self):
        self.events = []
        self.completed = None
        self.svc = _FakeSvc()
        self.state = type("S", (), {"_sessions": {}})()

    def publish(self, loop_id, event, data=None):
        self.events.append((loop_id, event, data))

    async def complete(self, loop_id, reason=""):
        self.completed = (loop_id, reason)


def _code(**over):
    base = dict(id="", name="C", kind="code", task="add oauth login to the app", kind_config={})
    base.update(over)
    return store.create(Loop(**base))


class TestStageAdvance:
    def test_no_criteria_advances_on_work_happened(self):
        c = _code(plan=[{"stage": "design", "title": "D"}, {"stage": "implementation", "title": "I"}],
                  phase_status={"design": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(store.get(c.id), [{"cycle": 1, "stage": "design", "summary": "x"}], ctx))
        assert done is False
        ps = store.get(c.id).phase_status
        assert ps["design"] == "done" and ps["implementation"] == "active"
        assert any(e[1] == "stage_advance" for e in ctx.events)

    def test_completes_on_last_stage(self):
        c = _code(plan=[{"stage": "implementation", "title": "I"}], phase_status={"implementation": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(store.get(c.id), [{"cycle": 1, "stage": "implementation", "summary": "built"}], ctx))
        assert done is True and ctx.completed[0] == c.id

    def test_advances_when_finding_stage_has_ordinal_prefix(self):
        # The stage directive names the active stage "stage 1/2 — Scaffold & Engine",
        # so the worker records the finding's stage as "1 — Scaffold & Engine" — neither
        # the phase_key ("implementation") nor the bare title ("Scaffold & Engine").
        # Without ordinal-tolerant matching the stage's findings read as empty → gate
        # can't pass → the stage never advances (the live ee704f3f stall). It must match.
        c = _code(plan=[{"stage": "implementation", "title": "Scaffold & Engine"},
                        {"stage": "verification", "title": "Tests & QA"}],
                  phase_status={"implementation": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(
            store.get(c.id),
            [{"cycle": 1, "stage": "1 — Scaffold & Engine", "summary": "scaffolded + engine"}],
            ctx))
        assert done is False
        ps = store.get(c.id).phase_status
        assert ps["implementation"] == "done" and ps["verification"] == "active"
        assert any(e[1] == "stage_advance" for e in ctx.events)

    def test_strip_stage_ordinal_forms(self):
        strip = kinds.get("code")._strip_stage_ordinal
        assert strip("1 — Scaffold & Engine") == "scaffold & engine"
        assert strip("2. Tests & QA") == "tests & qa"
        assert strip("Stage 1: Scaffold") == "scaffold"
        assert strip("implementation") == "implementation"   # no ordinal → unchanged (lowered)
        # "N/M <title>" progress-chip shape the directive's "stage N/M …" induces — a live
        # Code loop wedged in 'implementation' because every finding was tagged
        # "2/3 Implementation" and the bare "N<sep>" stripper left the "2/3 " prefix.
        assert strip("2/3 Implementation") == "implementation"
        assert strip("1/3 Decomposition") == "decomposition"
        assert strip("2/3 — Implementation") == "implementation"
        assert strip("Stage 3/3: Verification") == "verification"
        # Separator-slug folding: an LLM slugifies the directive's title "Test suite" →
        # test_suite / test-suite; these MUST canonicalize to the spaced title so the
        # finding matches its plan stage (else the metric gate goes inert — see
        # test_metric_gate_matches_slugified_stage_finding).
        assert strip("test_suite") == "test suite"
        assert strip("Test-Suite") == "test suite"
        assert strip("2/2 test_suite") == "test suite"

    def test_metric_gate_matches_slugified_stage_finding(self):
        """A verification finding tagged with a SLUGIFIED title (``test_suite``) must still
        attribute to the ``verification`` plan stage (id) whose title is ``Test suite``.
        Regression for live loop 4fb50978: the worker recorded stage ``test_suite`` (a
        reasonable slug of "Test suite"), but the matcher only accepted ``verification`` or
        ``test suite`` (space) → the verification stage saw ZERO findings →
        _observe_stage_metric scored nothing → P6 metric gate + rollback went silently
        inert (loop completed with quality_scores=None, verdicts=0)."""
        s = kinds.get("code")
        c = _code(plan=[{"stage": "implementation", "title": "Implement conversions"},
                        {"stage": "verification", "title": "Test suite", "metric_pass": 3.5}],
                  phase_status={"implementation": "done", "verification": "active"})
        findings = [{"cycle": 1, "stage": "implementation", "summary": "impl"},
                    {"cycle": 2, "stage": "test_suite", "summary": "wrote tests"},
                    {"cycle": 3, "stage": "test-suite", "summary": "green"}]
        matched = s._findings_for_stage(store.get(c.id), 1, findings)
        cycles = sorted(f["cycle"] for f in matched)
        assert cycles == [2, 3], f"verification stage should match the slugified findings, got {cycles}"
        # And the implementation stage must NOT swallow them (still matches only its own).
        impl = s._findings_for_stage(store.get(c.id), 0, findings)
        assert sorted(f["cycle"] for f in impl) == [1]

    def test_trailing_decoration_attributes_to_titled_stage(self):
        """A finding tagged with the stage TITLE plus a trailing per-item decoration
        (``Build All Modules — store.py``) must attribute to that stage (``implementation``,
        title ``Build All Modules``), NOT fall through to the active stage. Regression for
        the live TaskVault multi-module build: the worker labelled each implementation
        cycle ``"Build All Modules — <file>"``; the matcher folded that to a key matching
        NO plan stage, so every implementation finding was attributed to the ACTIVE stage
        (decomposition, idx 0), polluting its judge gate with implementation evidence and
        starving the implementation/verification stages → the loop wedged in decomposition
        and tripped the 5-finding spin cap despite building + committing 4 modules."""
        s = kinds.get("code")
        c = _code(plan=[{"stage": "decomposition", "title": "Task Breakdown"},
                        {"stage": "implementation", "title": "Build All Modules"},
                        {"stage": "verification", "title": "Full Test & Quality Pass"}],
                  phase_status={})  # decomposition is the active (first not-done) stage
        findings = [
            {"cycle": 1, "stage": "Task Breakdown", "summary": "decomposed"},
            {"cycle": 2, "stage": "Task Breakdown (guidance: begin Stage 2)", "summary": "model.py"},
            {"cycle": 3, "stage": "Build All Modules — store.py", "summary": "store"},
            {"cycle": 4, "stage": "Build All Modules — service.py", "summary": "service"},
            {"cycle": 5, "stage": "Full Test & Quality Pass", "summary": "tests"},
        ]
        loop = store.get(c.id)
        decomp = sorted(f["cycle"] for f in s._findings_for_stage(loop, 0, findings))
        impl = sorted(f["cycle"] for f in s._findings_for_stage(loop, 1, findings))
        verif = sorted(f["cycle"] for f in s._findings_for_stage(loop, 2, findings))
        assert decomp == [1, 2], f"decomposition should own only its own findings, got {decomp}"
        assert impl == [3, 4], f"implementation must claim the 'Build All Modules — X' findings, got {impl}"
        assert verif == [5], f"verification must claim its titled finding, got {verif}"

    def test_trailing_decoration_prefers_most_specific_stage(self):
        """When one stage title is a word-prefix of another (``Build`` vs ``Build All
        Modules``), a decorated finding (``Build All Modules — x``) attributes to the
        MOST SPECIFIC (longest-matching) stage, not the shorter prefix — so the fix can't
        double-count a decorated label across two stages."""
        s = kinds.get("code")
        c = _code(plan=[{"stage": "build", "title": "Build"},
                        {"stage": "build_all", "title": "Build All Modules"}],
                  phase_status={"build": "done"})  # build_all is active
        findings = [{"cycle": 1, "stage": "Build All Modules — x", "summary": "impl"},
                    {"cycle": 2, "stage": "Build", "summary": "short"}]
        loop = store.get(c.id)
        assert sorted(f["cycle"] for f in s._findings_for_stage(loop, 0, findings)) == [2]
        assert sorted(f["cycle"] for f in s._findings_for_stage(loop, 1, findings)) == [1]

    def test_unrecognized_stage_label_falls_to_active_stage(self):
        """A finding whose stage label matches NO plan stage (empty, OR an LLM label the
        engine can't reconcile) falls to the ACTIVE stage — a finding is written during a
        cycle, and a cycle always operates on the active stage. Regression for live loop
        d8777439: the verification stage was titled "Test suite & verification" and the
        worker slugified it to ``test_suite_and_verification`` (expanding & → and), which
        string-normalization alone can't fold → the verification stage would otherwise see
        zero findings and the metric gate stays inert. String-normalization is whack-a-mole
        (fold _/- and the model substitutes a synonym); active-stage fallback ends the class."""
        s = kinds.get("code")
        c = _code(plan=[{"stage": "implementation", "title": "Impl"},
                        {"stage": "verification", "title": "Test suite & verification", "metric_pass": 3.5}],
                  phase_status={"implementation": "done", "verification": "active"})
        findings = [{"cycle": 1, "stage": "implementation", "summary": "built"},
                    {"cycle": 2, "stage": "test_suite_and_verification", "summary": "&→and drift"},
                    {"cycle": 3, "stage": "totally unrelated label", "summary": "arbitrary"}]
        # The active verification stage (idx 1) claims both unrecognized-label findings.
        verif = sorted(f["cycle"] for f in s._findings_for_stage(store.get(c.id), 1, findings))
        assert verif == [2, 3], f"active stage should claim unrecognized-label findings, got {verif}"
        # The NON-active implementation stage (idx 0) must NOT absorb the unrecognized labels
        # (only findings that actually name it) — else every stage double-counts them.
        impl = sorted(f["cycle"] for f in s._findings_for_stage(store.get(c.id), 0, findings))
        assert impl == [1], f"non-active stage must only match findings naming it, got {impl}"

    def test_advances_when_finding_stage_has_progress_chip_prefix(self):
        # Regression for the live 28e32fdb wedge: the worker recorded its findings as
        # "2/3 Implementation" (the directive's progress-chip shape). The bare "N<sep>"
        # stripper didn't strip "2/3 ", so findings never matched the implementation
        # stage → work_done False → gate starved → stage never advanced despite all
        # tasks done + green node --test. With "N/M" stripping it must advance.
        c = _code(plan=[{"stage": "implementation", "title": "Implementation"},
                        {"stage": "verification", "title": "Verification"}],
                  phase_status={"implementation": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(
            store.get(c.id),
            [{"cycle": 5, "stage": "2/3 Implementation", "summary": "engine+ai+ui built"}],
            ctx))
        assert done is False
        ps = store.get(c.id).phase_status
        assert ps["implementation"] == "done" and ps["verification"] == "active"
        assert any(e[1] == "stage_advance" for e in ctx.events)

    def test_gate_with_criteria_blocks_until_judge_passes(self, monkeypatch):
        from personalclaw.loop import gates
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["all tests pass"]}],
                  phase_status={"implementation": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        findings = [{"cycle": 1, "stage": "implementation", "summary": "wip"}]
        # judge says FAIL → no advance
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k: _async("FAIL"))
        assert _run(s.on_new_cycle(store.get(c.id), findings, ctx)) is False
        assert store.get(c.id).phase_status.get("implementation") == "active"
        # judge says PASS → completes (last stage)
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k: _async("PASS"))
        assert _run(s.on_new_cycle(store.get(c.id), findings, ctx)) is True

    def test_flaky_judge_does_not_block_a_stage_whose_command_gate_passed(self, monkeypatch):
        # Robustness gap (found in the clean-slate TicTacToe run): when the verify_command
        # PASSES but the LLM judge errors/times out (judge_verdict → "" on provider
        # failure), the stage must NOT be hard-blocked — else a flaky judge permanently
        # wedges a complete stage (build green + tasks done). An EMPTY judge verdict +
        # a passed deterministic check → advance; a real FAIL still blocks.
        from personalclaw.loop import gates, kinds as _k
        import personalclaw.loop.kinds.sdlc as sdlc_mod
        monkeypatch.setattr(sdlc_mod, "_command_runnable_here", lambda *_a, **_k: True)
        monkeypatch.setattr(gates, "run_verify_command", lambda *_a, **_k: _async(True))  # build PASSES
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["builds"]}],
                  phase_status={"implementation": "active"},
                  kind_config={"verify_command": "npm run build"})
        s = type(_k.get("code"))(); ctx = _Ctx()
        findings = [{"cycle": 1, "stage": "implementation", "summary": "built"}]
        # judge ERRORED (empty) but the build gate passed → advance (here: completes, last stage)
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async(""))
        assert _run(s.on_new_cycle(store.get(c.id), findings, ctx)) is True
        # contrast: a genuine FAIL verdict still blocks even with the command passing
        c2 = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["builds"]}],
                   phase_status={"implementation": "active"},
                   kind_config={"verify_command": "npm run build"})
        s2 = type(_k.get("code"))(); ctx2 = _Ctx()
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("FAIL: not really"))
        assert _run(s2.on_new_cycle(store.get(c2.id), findings, ctx2)) is False
        assert store.get(c2.id).phase_status.get("implementation") == "active"

    def test_stuck_stage_escalates_to_blocked_once(self, monkeypatch):
        from personalclaw.loop import gates, kinds as _k
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["all tests pass"]}],
                  phase_status={"implementation": "active"})
        # fresh strategy instance so _stall_notified doesn't carry across tests
        s = type(_k.get("code"))(); ctx = _Ctx()
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("FAIL"))
        # grind 5 in-stage findings without the gate clearing → stall escalation
        findings = [{"cycle": i, "stage": "implementation", "summary": f"wip {i}"} for i in range(1, 6)]
        assert _run(s.on_new_cycle(store.get(c.id), findings, ctx)) is False
        assert store.get(c.id).status == "blocked"
        assert any(e[1] == "stage_stalled" for e in ctx.events)
        # one-shot: a further cycle doesn't re-escalate (no second stage_stalled)
        store.update_status(c.id, store.LoopStatus.RUNNING)  # simulate a resume
        ctx.events.clear()
        _run(s.on_new_cycle(store.get(c.id), findings + [{"cycle": 6, "stage": "implementation", "summary": "wip 6"}], ctx))
        assert not any(e[1] == "stage_stalled" for e in ctx.events)

    def test_progressing_stage_does_not_false_stall(self, monkeypatch):
        """A stage grinding 5+ findings but STILL resolving tasks (one per cycle) is
        making real progress, not spinning — it must NOT escalate to blocked. Guards
        the observed multi-module build that tripped the spin cap while every cycle
        completed + committed a task."""
        from personalclaw.loop import gates, kinds as _k, tasks_link
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["all tests pass"]}],
                  phase_status={"implementation": "active"})
        s = type(_k.get("code"))(); ctx = _Ctx()
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("FAIL"))
        # Each cycle the resolved-task count rises (model→store→service→cli→tests).
        counter = {"n": 0}

        async def _rising(_loop, _stage):
            counter["n"] += 1
            return counter["n"]
        monkeypatch.setattr(tasks_link, "resolved_stage_task_count", _rising)
        findings = [{"cycle": i, "stage": "implementation", "summary": f"wip {i}"} for i in range(1, 6)]
        assert _run(s.on_new_cycle(store.get(c.id), findings, ctx)) is False
        # progress observed → NOT blocked, NO stall event
        assert store.get(c.id).status != "blocked"
        assert not any(e[1] == "stage_stalled" for e in ctx.events)
        assert f"{c.id}:implementation" not in s._stall_notified

    def test_flat_progress_still_escalates_stall(self, monkeypatch):
        """The complement: 5+ findings with a FLAT resolved-task count (no progress)
        is a genuine spin — it must still escalate to blocked."""
        from personalclaw.loop import gates, kinds as _k, tasks_link
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["all tests pass"]}],
                  phase_status={"implementation": "active"})
        s = type(_k.get("code"))(); ctx = _Ctx()
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("FAIL"))
        # A stuck stage: some tasks resolved early but the count is FLAT thereafter.
        monkeypatch.setattr(tasks_link, "resolved_stage_task_count",
                            lambda _loop, _stage: _async(2))
        findings = [{"cycle": i, "stage": "implementation", "summary": f"wip {i}"} for i in range(1, 6)]
        # first check: baseline 0 -> 2 defers once (progress); grind more, count stays flat
        _run(s.on_new_cycle(store.get(c.id), findings, ctx))
        store.update_status(c.id, store.LoopStatus.RUNNING)
        more = findings + [{"cycle": i, "stage": "implementation", "summary": f"wip {i}"} for i in range(6, 9)]
        _run(s.on_new_cycle(store.get(c.id), more, ctx))
        assert store.get(c.id).status == "blocked"
        assert any(e[1] == "stage_stalled" for e in ctx.events)

    def test_stage_advance_clears_stall_flag(self, monkeypatch):
        from personalclaw.loop import gates, kinds as _k
        c = _code(plan=[{"stage": "design", "title": "D", "exit_criteria": ["spec approved"]},
                        {"stage": "implementation", "title": "I"}],
                  phase_status={"design": "active"})
        s = type(_k.get("code"))(); ctx = _Ctx()
        # stall design → blocked + flag set
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("FAIL"))
        findings = [{"cycle": i, "stage": "design", "summary": f"wip {i}"} for i in range(1, 6)]
        _run(s.on_new_cycle(store.get(c.id), findings, ctx))
        assert f"{c.id}:design" in s._stall_notified
        # resume + the gate now PASSES → stage advances → its stall flag is cleared
        store.update_status(c.id, store.LoopStatus.RUNNING)
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("PASS"))
        _run(s.on_new_cycle(store.get(c.id), findings, ctx))
        assert f"{c.id}:design" not in s._stall_notified   # cleared on advance, no leak
        assert store.get(c.id).phase_status["design"] == "done"

    def test_failing_verify_command_blocks_gate(self, monkeypatch):
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["builds"]}],
                  phase_status={"implementation": "active"},
                  kind_config={"verify_command": "false"})  # real non-zero → gate fail
        s, ctx = kinds.get("code"), _Ctx()
        assert _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "implementation", "summary": "x"}], ctx)) is False
        assert any(e[1] == "gate_check" and e[2].get("ok") is False for e in ctx.events)

    # ── Slice A: stage-appropriate gate (the planning-stage hard-fail fix) ──

    def test_planning_stage_verify_command_skipped_when_not_buildable(self, monkeypatch, tmp_path):
        """The bug: a planning/scaffold stage runs before package.json exists, so the
        verify_command (`npm run build`) exits ENOENT(254) → hard FALSE → the stage
        never advances. Fix: the command is SKIPPED when its project isn't buildable yet
        (no manifest); the stage gates on the judge instead. With the planning deliverable
        present + judge PASS, it advances — instead of grinding forever."""
        from personalclaw.loop import gates, kinds as _k
        ws = tmp_path / "ws"; ws.mkdir()
        (ws / "PLAN.md").write_text("# the plan")          # deliverable exists (ground truth)
        # NO package.json → `npm run build` can't run here yet
        c = _code(plan=[{"stage": "design", "title": "Decompose & Scaffold Plan",
                         "exit_criteria": ["plan complete"], "deliverable": "PLAN.md"},
                        {"stage": "implementation", "title": "I"}],
                  phase_status={"design": "active"}, workspace_dir=str(ws),
                  kind_config={"verify_command": "npm run lint && npm run build"})
        s = type(_k.get("code"))(); ctx = _Ctx()
        # Guard: run_verify_command must NOT be invoked for the skipped build command.
        called = {"n": 0}
        async def _spy(*_a, **_k2):
            called["n"] += 1; return False
        monkeypatch.setattr(gates, "run_verify_command", _spy)
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("PASS"))
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "design", "summary": "wrote PLAN.md"}], ctx))
        assert done is False  # not the last stage
        assert called["n"] == 0, "verify_command must be SKIPPED on a non-buildable planning stage"
        assert store.get(c.id).phase_status["design"] == "done"          # advanced, didn't grind
        assert store.get(c.id).phase_status["implementation"] == "active"
        # the skip is observable as a gate_check with ok=None + a skipped reason
        assert any(e[1] == "gate_check" and e[2].get("ok") is None and e[2].get("skipped")
                   for e in ctx.events)

    def test_planning_stage_blocks_when_deliverable_missing(self, monkeypatch, tmp_path):
        """Independent ground-truth: a stage declaring a doc deliverable must NOT pass
        until that file exists on disk — the gate observes it, never trusts the worker's
        'I wrote PLAN.md' self-report."""
        from personalclaw.loop import gates, kinds as _k
        ws = tmp_path / "ws"; ws.mkdir()  # PLAN.md absent
        c = _code(plan=[{"stage": "design", "title": "Plan", "exit_criteria": ["plan complete"],
                         "deliverable": "PLAN.md"}, {"stage": "implementation", "title": "I"}],
                  phase_status={"design": "active"}, workspace_dir=str(ws))
        s = type(_k.get("code"))(); ctx = _Ctx()
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("PASS"))  # judge would pass
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "design", "summary": "claims PLAN.md done"}], ctx))
        assert done is False
        assert store.get(c.id).phase_status["design"] == "active"  # blocked: deliverable absent
        assert any(e[1] == "gate_check" and e[2].get("label") == "deliverable" and e[2].get("ok") is False
                   for e in ctx.events)

    def test_deliverable_resolve_honors_subdirectory_path(self, tmp_path):
        """A deliverable filename with a SUBDIR path (e.g. 'src/engine.ts') must resolve
        under that subdir — checking only the basename at the workspace ROOT misses
        <ws>/src/engine.ts and hard-fails the gate forever (observed live: a Code loop's
        implementation stage stuck because deliverable was 'src/engine.ts').

        _resolve_deliverable returns (verifiable, path): (True, <path>) when a named file
        is found, (True, None) when a named file is missing (→ block), (False, None) when
        the label has no concrete filename (→ nothing to verify)."""
        from personalclaw.loop import kinds as _k
        s = type(_k.get("code"))()
        ws = tmp_path / "ws"; (ws / "src").mkdir(parents=True)
        (ws / "src" / "engine.ts").write_text("export const x = 1\n")
        # as-given subdir path resolves to the concrete file
        ok, p = s._resolve_deliverable(str(ws), "A complete, DOM-free src/engine.ts plus tests")
        assert ok is True and p is not None and p.endswith("src/engine.ts")
        # basename-anywhere fallback: label says bare 'engine.ts', file is under src/
        ok, p = s._resolve_deliverable(str(ws), "deliver engine.ts")
        assert ok is True and p is not None and p.endswith("engine.ts")
        # genuinely absent → verifiable but no path (the gate blocks)
        ok, p = s._resolve_deliverable(str(ws), "ship dist/bundle.js")
        assert ok is True and p is None
        # a label with no concrete filename → unverifiable (don't block)
        ok, p = s._resolve_deliverable(str(ws), "a working prototype")
        assert ok is False and p is None
        # root-level file (the common PLAN.md case) still resolves
        (ws / "PLAN.md").write_text("# plan\n")
        ok, p = s._resolve_deliverable(str(ws), "PLAN.md")
        assert ok is True and p is not None and p.endswith("PLAN.md")

    def test_read_deliverable_bounded_and_binary_safe(self, tmp_path):
        """_read_deliverable returns bounded text; binary/empty files return ''."""
        from personalclaw.loop import kinds as _k
        s = type(_k.get("code"))()
        f = tmp_path / "PLAN.md"; f.write_text("# Plan\nStep 1\n")
        assert "Step 1" in s._read_deliverable(str(f))
        # empty file → ""
        (tmp_path / "empty.md").write_text("")
        assert s._read_deliverable(str(tmp_path / "empty.md")) == ""
        # binary → "" (undecodable)
        (tmp_path / "b.bin").write_bytes(b"\x89PNG\r\n\x00\xff\xfe")
        assert s._read_deliverable(str(tmp_path / "b.bin")) == ""
        # bounded: a big file is truncated with a marker
        (tmp_path / "big.md").write_text("x" * 9000)
        out = s._read_deliverable(str(tmp_path / "big.md"), max_chars=1000)
        assert len(out) < 2000 and "truncated" in out

    def test_implementation_stage_advances_with_subdir_deliverable(self, monkeypatch, tmp_path):
        """End-to-end: an implementation stage whose deliverable is 'src/engine.ts' (file
        present) + a green verify_command must ADVANCE, not wedge on the basename-at-root
        miss. Regression for the live eb13864c wedge."""
        from personalclaw.loop import gates, kinds as _k
        ws = tmp_path / "ws"; (ws / "src").mkdir(parents=True)
        (ws / "src" / "engine.ts").write_text("export const x = 1\n")
        (ws / "package.json").write_text("{}")
        c = _code(plan=[{"stage": "implementation", "title": "Engine & AI",
                         "exit_criteria": ["engine implemented"],
                         "deliverable": "A complete, DOM-free src/engine.ts plus its unit tests passing"},
                        {"stage": "verification", "title": "Proof"}],
                  phase_status={"implementation": "active"},
                  kind_config={"entry_stage": "implementation", "verify_command": "true"},
                  workspace_dir=str(ws))
        s = type(_k.get("code"))(); ctx = _Ctx()
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k2: _async("PASS"))
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "implementation", "summary": "engine + AI built, tests green"}], ctx))
        assert done is False
        ps = store.get(c.id).phase_status
        assert ps["implementation"] == "done" and ps["verification"] == "active"

    def test_slice_b_feeds_deliverable_content_to_judge(self, monkeypatch, tmp_path):
        """Slice B: the gate reads the deliverable file's REAL content and feeds it into
        the judge prompt — the judge scores the observed artifact, not the worker's
        summary. Captures the prompt the judge received and asserts the content is in it,
        and that a gate_check surfaces the observed byte count."""
        from personalclaw.loop import gates, kinds as _k
        ws = tmp_path / "ws"; ws.mkdir()
        (ws / "PLAN.md").write_text("# Architecture Plan\nUNIQUE_MARKER_XYZ decomposition here.\n")
        c = _code(plan=[{"stage": "design", "title": "Decompose", "exit_criteria": ["plan authored"],
                         "deliverable": "PLAN.md"},
                        {"stage": "implementation", "title": "Build"}],
                  phase_status={"design": "active"}, workspace_dir=str(ws),
                  kind_config={"entry_stage": "design"})
        s = type(_k.get("code"))(); ctx = _Ctx()
        seen = {}
        def _capture(prompt, *_a, **_k2):
            seen["prompt"] = prompt
            return _async("PASS")
        monkeypatch.setattr(gates, "judge_verdict", _capture)
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "design", "summary": "wrote the plan"}], ctx))
        assert done is False
        # the judge saw the ACTUAL deliverable content, not just the worker's "wrote the plan"
        assert "UNIQUE_MARKER_XYZ" in seen.get("prompt", "")
        assert "observed directly by the supervisor" in seen.get("prompt", "")
        # gate_check surfaces the observed content
        assert any(e[1] == "gate_check" and e[2].get("label") == "deliverable"
                   and e[2].get("ok") is True and e[2].get("content_bytes", 0) > 0
                   for e in ctx.events)

    def test_verify_command_gates_once_project_is_buildable(self, monkeypatch, tmp_path):
        """Once the scaffold stage has created the manifest, the same verify_command
        DOES gate — a real non-zero exit blocks the stage (the command is no longer
        skipped). Confirms the fix doesn't suppress genuine build failures."""
        from personalclaw.loop import gates, kinds as _k
        ws = tmp_path / "ws"; ws.mkdir()
        (ws / "package.json").write_text("{}")            # now buildable
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["builds"]}],
                  phase_status={"implementation": "active"}, workspace_dir=str(ws),
                  kind_config={"verify_command": "npm run build"})
        s = type(_k.get("code"))(); ctx = _Ctx()
        async def _fail(*_a, **_k2):
            return False
        monkeypatch.setattr(gates, "run_verify_command", _fail)  # build genuinely fails
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "implementation", "summary": "x"}], ctx))
        assert done is False
        assert any(e[1] == "gate_check" and e[2].get("ok") is False and e[2].get("label") == "build"
                   for e in ctx.events)


class TestAutopilotQueue:
    """Autopilot queues the active stage's tasks each cycle; the gate won't advance
    while ready queued tasks are still unrun (don't skip the user's queued work)."""

    @pytest.fixture(autouse=True)
    def _wire_tasks(self, monkeypatch, tmp_path):
        monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
        import personalclaw.tasks.native as nat
        monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)

    def test_queues_active_stage_and_holds_gate_until_done(self):
        from personalclaw.loop import tasks_link
        from personalclaw.tasks import registry
        c = _code(autopilot=True,
                  plan=[{"stage": "implementation", "title": "Impl",
                         "tasks": [{"title": "A"}, {"title": "B"}]}],
                  phase_status={"implementation": "active"})
        tasks_link.provision(c.id)
        _run(tasks_link.seed_phase_tasks(c.id))
        s, ctx = kinds.get("code"), _Ctx()
        # cycle 1: autopilot queues A+B; ready-pending → no advance
        assert _run(s.on_new_cycle(store.get(c.id), [{"cycle": 1, "stage": "implementation", "summary": "x"}], ctx)) is False
        q = store.get(c.id).kind_config.get("queued_task_ids", [])
        assert len(q) == 2
        # finish both → gate (no criteria) advances → last stage → complete
        for tid in q:
            _run(registry.update_task(tid, provider_name="native", status="done"))
        assert _run(s.on_new_cycle(store.get(c.id), [{"cycle": 2, "stage": "implementation", "summary": "y"}], ctx)) is True

    def test_one_by_one_mode_does_not_autoqueue(self):
        from personalclaw.loop import tasks_link
        c = _code(autopilot=False,
                  plan=[{"stage": "implementation", "title": "Impl", "tasks": [{"title": "A"}]}],
                  phase_status={"implementation": "active"})
        tasks_link.provision(c.id)
        _run(tasks_link.seed_phase_tasks(c.id))
        s, ctx = kinds.get("code"), _Ctx()
        _run(s.on_new_cycle(store.get(c.id), [{"cycle": 1, "stage": "implementation", "summary": "x"}], ctx))
        assert store.get(c.id).kind_config.get("queued_task_ids", []) == []  # user queues, not autopilot

    def test_stage_advance_force_closes_the_stage_tasks(self):
        # When a stage's gate passes, its still-open tasks (worker left them open) must
        # be force-closed — else the cockpit shows a done stage with open tasks.
        from personalclaw.loop import tasks_link
        from personalclaw.tasks import registry
        c = _code(autopilot=False,
                  plan=[{"stage": "design", "title": "D", "tasks": [{"title": "sketch"}]},
                        {"stage": "implementation", "title": "I"}],
                  phase_status={"design": "active"})
        tasks_link.provision(c.id)
        _run(tasks_link.seed_phase_tasks(c.id))
        list_id = store.get(c.id).task_list_ids["design"]
        # design has no exit_criteria + nothing queued (one-by-one) → gate advances on work
        s, ctx = kinds.get("code"), _Ctx()
        _run(s.on_new_cycle(store.get(c.id), [{"cycle": 1, "stage": "design", "summary": "sketched"}], ctx))
        assert store.get(c.id).phase_status["design"] == "done"
        tasks, _ = _run(registry.list_all_tasks(task_list_id=list_id, limit=50))
        assert tasks and all(t.status.value == "done" for t in tasks)  # the open task was closed


class TestParallelScheduler:
    """Parallel mode (git workspace + queued work): the scheduler spawns a task-worker
    per ready task in its own worktree, merges on done, advances when all drain."""

    @pytest.fixture(autouse=True)
    def _wire_tasks(self, monkeypatch, tmp_path):
        monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
        import personalclaw.tasks.native as nat
        monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)

    def _git_repo(self, tmp_path):
        import subprocess
        ws = tmp_path / "repo"; ws.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "--allow-empty", "-m", "init"], cwd=ws, check=True, capture_output=True)
        return str(ws)

    class _SchedSvc:
        def __init__(self):
            self._loops = {}; self._n = 0
        async def add(self, *, session_name, **kw):
            self._n += 1
            from types import SimpleNamespace
            lp = SimpleNamespace(id=f"N{self._n}", session_name=session_name, active=True, cycle_count=0)
            self._loops[lp.id] = lp; return lp
        def get_by_session(self, session_name):
            return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)
        async def update(self, lid, **kw):
            lp = self._loops.get(lid)
            [setattr(lp, k, v) for k, v in kw.items()] if lp else None
        async def remove(self, lid):
            self._loops.pop(lid, None)

    def test_spawns_task_workers_in_worktrees(self, tmp_path):
        from personalclaw.loop import tasks_link
        from personalclaw.loop.manager import task_session_key
        ws = self._git_repo(tmp_path)
        c = _code(autopilot=True, workspace_dir=ws,
                  plan=[{"stage": "implementation", "title": "Impl",
                         "tasks": [{"title": "A"}, {"title": "B"}]}],
                  phase_status={"implementation": "active"})
        tasks_link.provision(c.id)
        _run(tasks_link.seed_phase_tasks(c.id))
        svc = self._SchedSvc()
        ctx = _Ctx(); ctx.svc = svc
        ctx.state = type("S", (), {"_sessions": {},
                                   "get_or_create_session": lambda self, **k: _sess(k["name"]),
                                   "push_sessions_update": lambda self: None})()
        # cycle 1: autopilot queues A+B → parallel scheduler spawns 2 task-workers
        done = _run(kinds.get("code").on_new_cycle(store.get(c.id), [{"cycle": 1, "stage": "implementation", "summary": "x"}], ctx))
        assert done is False
        q = store.get(c.id).kind_config.get("queued_task_ids", [])
        assert len(q) == 2
        assert all(svc.get_by_session(task_session_key(c.id, tid)) is not None for tid in q)
        assert sum(1 for e in ctx.events if e[1] == "task_started") == 2


def _sess(name):
    from types import SimpleNamespace
    return SimpleNamespace(key=name, _trust=False, running=False, acp_provider="",
                           acp_provider_agent="", reasoning_effort="", acp_mode="",
                           _extra_tool_roots=None)


class TestNoStagePlan:
    def test_completes_once_a_finding_lands(self):
        c = _code(plan=[])
        s, ctx = kinds.get("code"), _Ctx()
        assert _run(s.on_new_cycle(store.get(c.id), [{"cycle": 1, "summary": "fixed"}], ctx)) is True

    def test_failing_verify_holds_completion(self):
        c = _code(plan=[], kind_config={"verify_command": "false"})
        s, ctx = kinds.get("code"), _Ctx()
        assert _run(s.on_new_cycle(store.get(c.id), [{"cycle": 1, "summary": "x"}], ctx)) is False


async def _async(v):
    return v


class TestP6TickDecision:
    """P6 step-4: the stepwise lifecycle decision IS the pure ``tick.evaluate``.
    ``_tick_decide`` builds the TickState snapshot from persisted state + the adapter's
    observed (gate, metric) and returns the authoritative Decision. A stage with no
    metric_pass degrades to the pre-P6 behavior (gate passed → advance/complete)."""

    def _one_metric_stage(self):
        # Single metric-gated stage (advance clears the whole plan → COMPLETE).
        return _code(plan=[{"stage": "verification", "title": "V", "metric_pass": 3.5, "metric_hold": 2.0}],
                     phase_status={"verification": "active"})

    def _two_stages_metric_second(self):
        return _code(plan=[{"stage": "implementation", "title": "I", "metric_pass": 3.0},
                           {"stage": "verification", "title": "V", "metric_pass": 3.5, "metric_hold": 2.0}],
                     phase_status={"implementation": "done", "verification": "active"})

    def test_holds_below_metric_pass(self):
        from personalclaw.loop import tick
        c = self._one_metric_stage()
        d = kinds.get("code")._tick_decide(c, 0, [{"cycle": 1, "stage": "verification"}],
                                           gate_passed=True, metric=2.5)  # below 3.5, above hold 2.0
        assert d.action is tick.Action.HOLD

    def test_completes_at_or_above_metric_pass_on_last_stage(self):
        from personalclaw.loop import tick
        c = self._one_metric_stage()
        d = kinds.get("code")._tick_decide(c, 0, [{"cycle": 1, "stage": "verification"}],
                                           gate_passed=True, metric=4.0)  # ≥ 3.5, last stage
        assert d.action is tick.Action.COMPLETE

    def test_advances_metric_clear_non_last_stage(self):
        from personalclaw.loop import tick
        c = _code(plan=[{"stage": "verification", "title": "V", "metric_pass": 3.5},
                        {"stage": "release", "title": "R"}],
                  phase_status={"verification": "active"})
        d = kinds.get("code")._tick_decide(c, 0, [{"cycle": 1, "stage": "verification"}],
                                           gate_passed=True, metric=4.0)
        assert d.action is tick.Action.ADVANCE and d.step_index == 1

    def test_inert_when_not_metric_gated(self):
        from personalclaw.loop import tick
        c = _code(plan=[{"stage": "implementation", "title": "I"}, {"stage": "verification", "title": "V"}],
                  phase_status={"implementation": "active"})
        # No metric_pass → gate passed advances regardless of metric (structural gate is all).
        d = kinds.get("code")._tick_decide(c, 0, [{"cycle": 1, "stage": "implementation"}],
                                           gate_passed=True, metric=None)
        assert d.action is tick.Action.ADVANCE and d.step_index == 1

    def test_gate_not_passed_executes(self):
        from personalclaw.loop import tick
        c = self._one_metric_stage()
        d = kinds.get("code")._tick_decide(c, 0, [{"cycle": 1, "stage": "verification"}],
                                           gate_passed=False, metric=None)
        assert d.action is tick.Action.EXECUTE

    def test_metric_regression_rolls_back_to_prior_stage(self):
        from personalclaw.loop import tick
        c = self._two_stages_metric_second()  # prior stage floor = 3.0
        # On the verification stage, metric craters below the prior implementation floor.
        d = kinds.get("code")._tick_decide(c, 1, [{"cycle": 5, "stage": "verification"}],
                                           gate_passed=True, metric=1.0)
        assert d.action is tick.Action.ROLLBACK and d.step_index == 0

    def test_rollback_cap_blocks_after_repeated_regressions(self):
        from personalclaw.loop import tick
        c = _code(plan=[{"stage": "implementation", "title": "I", "metric_pass": 3.0},
                        {"stage": "verification", "title": "V", "metric_pass": 3.5}],
                  phase_status={"implementation": "done", "verification": "active"},
                  kind_config={"rollbacks_on_stage": {"1": 3}})  # already at default cap
        d = kinds.get("code")._tick_decide(c, 1, [{"cycle": 9, "stage": "verification"}],
                                           gate_passed=True, metric=1.0)
        assert d.action is tick.Action.COMPLETE and "cap" in d.reason


class TestP6EndToEnd:
    """P6 step-4 through the live ``on_new_cycle`` path: a metric-gated stage OBSERVES a
    scored quality metric (P4 judge), PERSISTS it to the quality trail, and the tick
    Decision drives advance/hold/rollback — the single authoritative decision path."""

    def _stub_scored_judge(self, monkeypatch, score: float):
        # _observe_stage_metric calls loop.judge.assess_cycle for the graded metric.
        from personalclaw.loop import judge as judge_mod
        v = judge_mod.CycleVerdict(done=False, done_reason="", marginal_value=score,
                                   quality_score=score, regressed=False)
        monkeypatch.setattr(judge_mod, "assess_cycle", lambda *_a, **_k: _async(v))

    def _stub_structural_gate_pass(self, monkeypatch):
        from personalclaw.loop import gates
        monkeypatch.setattr(gates, "judge_verdict", lambda *_a, **_k: _async("PASS"))

    def test_metric_gated_stage_holds_below_bar_and_persists_metric(self, monkeypatch):
        self._stub_structural_gate_pass(monkeypatch)
        self._stub_scored_judge(monkeypatch, 2.5)   # below metric_pass 3.5, above hold 2.0
        c = _code(plan=[{"stage": "verification", "title": "V", "metric_pass": 3.5,
                         "metric_hold": 2.0, "exit_criteria": ["works"]}],
                  phase_status={"verification": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "verification", "summary": "x"}], ctx))
        assert done is False
        # HOLD → stage stays active (not advanced/done)
        assert (store.get(c.id).phase_status or {}).get("verification") == "active"
        # The graded metric was persisted to the quality trail (was dead before this fix).
        assert store.get_quality_scores(c.id) == [2.5]
        # A cycle_verdict was published (FE ROI rail), NOT a stage_advance.
        events = [e[1] for e in ctx.events]
        assert "cycle_verdict" in events and "stage_advance" not in events

    def test_metric_gated_stage_completes_above_bar(self, monkeypatch):
        self._stub_structural_gate_pass(monkeypatch)
        self._stub_scored_judge(monkeypatch, 4.0)   # ≥ metric_pass 3.5
        c = _code(plan=[{"stage": "verification", "title": "V", "metric_pass": 3.5,
                         "exit_criteria": ["works"]}],
                  phase_status={"verification": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "verification", "summary": "x"}], ctx))
        assert done is True                          # last stage cleared → COMPLETE
        assert ctx.completed is not None and ctx.completed[0] == c.id
        assert store.get_quality_scores(c.id) == [4.0]

    def test_regression_rolls_back_to_prior_stage_end_to_end(self, monkeypatch):
        self._stub_structural_gate_pass(monkeypatch)
        self._stub_scored_judge(monkeypatch, 1.0)   # craters below prior floor 3.0 → ROLLBACK
        c = _code(plan=[{"stage": "implementation", "title": "I", "metric_pass": 3.0},
                        {"stage": "verification", "title": "V", "metric_pass": 3.5,
                         "exit_criteria": ["works"]}],
                  phase_status={"implementation": "done", "verification": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 5, "stage": "verification", "summary": "regressed"}], ctx))
        assert done is False
        ps = store.get(c.id).phase_status or {}
        assert ps.get("verification") == "pending"   # current stage reset
        assert ps.get("implementation") == "active"  # rolled back to prior stage
        assert (store.get(c.id).kind_config or {}).get("rollbacks_on_stage", {}).get("1") == 1
        assert "rolled_back" in [e[1] for e in ctx.events]

    def test_persistent_metric_hold_escalates_stall_with_metric_cause(self, monkeypatch):
        # A stage whose quality metric never clears metric_pass HOLDs every cycle; after
        # _STALL_FINDINGS it must escalate a stall (else it burns silently to budget) —
        # tagged cause="metric" so the steer message names the quality bar, not exit criteria.
        from personalclaw.loop import kinds as _k
        self._stub_structural_gate_pass(monkeypatch)
        self._stub_scored_judge(monkeypatch, 2.5)   # below pass 3.5, above hold 2.0 → HOLD
        c = _code(plan=[{"stage": "verification", "title": "V", "metric_pass": 3.5,
                         "metric_hold": 2.0, "exit_criteria": ["works"]}],
                  phase_status={"verification": "active"})
        s = type(_k.get("code"))(); ctx = _Ctx()   # fresh instance (clean _stall_notified)
        findings = [{"cycle": i, "stage": "verification", "summary": f"refine {i}"} for i in range(1, 6)]
        assert _run(s.on_new_cycle(store.get(c.id), findings, ctx)) is False
        assert store.get(c.id).status == "blocked"
        stalled = [e for e in ctx.events if e[1] == "stage_stalled"]
        assert stalled and stalled[0][2].get("cause") == "metric"

    def test_ungated_plan_advances_identically_to_pre_p6(self, monkeypatch):
        # A plan with NO tick keys must behave exactly as before: gate passed → advance.
        self._stub_structural_gate_pass(monkeypatch)
        # assess_cycle must NOT be consulted for an ungated stage (metric branch inert).
        from personalclaw.loop import judge as judge_mod
        def _boom(*_a, **_k):
            raise AssertionError("scored judge must not run for an ungated stage")
        monkeypatch.setattr(judge_mod, "assess_cycle", _boom)
        c = _code(plan=[{"stage": "implementation", "title": "I", "exit_criteria": ["done"]},
                        {"stage": "verification", "title": "V"}],
                  phase_status={"implementation": "active"})
        s, ctx = kinds.get("code"), _Ctx()
        done = _run(s.on_new_cycle(store.get(c.id),
                                   [{"cycle": 1, "stage": "implementation", "summary": "x"}], ctx))
        assert done is False
        ps = store.get(c.id).phase_status or {}
        assert ps.get("implementation") == "done" and ps.get("verification") == "active"
