"""Unified Loop watchdog (Slice 2c.iii) — the kind-agnostic supervisor poll loop.
Drives _poll_once against fake state/svc; done-ness is delegated to the kind
strategy's is_done_signal."""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.loop import manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    return tmp_path


class _FakeSession:
    def __init__(self, key, running=False):
        self.key = key
        self._running = running
        self._trust = True
        self.messages = []

    @property
    def running(self):
        return self._running


class _FakeState:
    def __init__(self):
        self._sessions = {}
        self.notes = []
        self.refreshed = []
        from personalclaw.dashboard.sse import SseRegistry

        self._sse = SseRegistry()

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *kinds):
        self.refreshed.append(kinds)

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body, meta or {}))


class _FakeNudge:
    def __init__(self, lid, session_name):
        self.id, self.session_name, self.active, self.cycle_count = lid, session_name, True, 0


class _FakeSvc:
    def __init__(self):
        self._loops = {}
        self._n = 0

    async def add(
        self, *, session_name, message, idle_secs, max_cycles, stop_sentinel_path, first_idle_secs=0
    ):
        self._n += 1
        lp = _FakeNudge(f"N{self._n}", session_name)
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)

    async def update(self, loop_id, **kw):
        lp = self._loops.get(loop_id)
        if lp:
            for k, v in kw.items():
                setattr(lp, k, v)

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


def _wd():
    return W.LoopWatchdog(_FakeState(), _FakeSvc())


def _running(**over):
    base = dict(
        id="",
        name="L",
        kind="goal",
        task="investigate the latency regression",
        kind_config={"goal_type": "open_ended"},
        idle_secs=120,
        max_cycles=20,
    )
    base.update(over)
    loop = store.create(Loop(**base))
    store.update_status(loop.id, LoopStatus.RUNNING)
    return store.get(loop.id)


def _write_finding(cid, cycle, **extra):
    d = store.loop_dir(cid)
    (d / "findings" / f"cycle_{cycle:03d}.json").write_text(
        json.dumps(
            {"cycle": cycle, "new_findings_count": extra.pop("new_findings_count", 1), **extra}
        )
    )


class TestVerifiableCompletes:
    def test_verifiable_completes_when_check_passes(self):
        c = _running(
            kind_config={"goal_type": "verifiable", "verify_command": "true"}, max_cycles=20
        )
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())  # seed liveness
        _write_finding(c.id, 1)
        _run(wd._poll_once())  # new finding → verify passes → complete
        assert store.get(c.id).status == LoopStatus.COMPLETE.value

    def test_verifiable_keeps_running_when_check_fails(self):
        c = _running(
            kind_config={"goal_type": "verifiable", "verify_command": "false"}, max_cycles=20
        )
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())
        _write_finding(c.id, 1)
        _run(wd._poll_once())
        assert store.get(c.id).status == LoopStatus.RUNNING.value


class TestVerifiableMultiSubGoalGate:
    """A verifiable goal with MULTIPLE sub-goals must not complete on a green
    verify_command alone (the command can cover only a subset — e.g. `npm test` green
    after only the engine phase). A judge must confirm every sub-goal is met. Regression
    for the live b7abd778 wedge: goal marked done after phase 1/3."""

    def _loop(self):
        return _running(
            kind="goal",
            task="build unbeatable tic-tac-toe",
            kind_config={
                "goal_type": "verifiable",
                "verify_command": "npm test",
                "sub_goals": [
                    "engine",
                    "minimax AI",
                    "accessible UI",
                    "exhaustive never-lose proof",
                ],
            },
        )

    def _kind(self):
        from personalclaw.loop import kinds

        kinds.ensure_loaded()
        return kinds.get("goal")

    def test_green_check_but_judge_fails_does_not_complete(self, monkeypatch):
        # Command passes, but only the engine is built → judge FAILs → NOT done.
        import personalclaw.loop.gates as gates

        monkeypatch.setattr(gates, "run_verify_command", lambda *a, **k: _coro(True))
        monkeypatch.setattr(gates, "judge_verdict", lambda *a, **k: _coro("FAIL"))
        loop = self._loop()
        findings = [{"cycle": 1, "summary": "Phase 1/3 engine complete; AI/UI/proof remain"}]
        assert _run(self._kind().is_done_signal(loop, findings)) is False

    def test_green_check_and_judge_passes_completes(self, monkeypatch):
        import personalclaw.loop.gates as gates

        monkeypatch.setattr(gates, "run_verify_command", lambda *a, **k: _coro(True))
        monkeypatch.setattr(gates, "judge_verdict", lambda *a, **k: _coro("PASS"))
        loop = self._loop()
        findings = [
            {"cycle": 9, "summary": "engine+AI+UI+never-lose proof all built; all tests green"}
        ]
        assert _run(self._kind().is_done_signal(loop, findings)) is True

    def test_judge_unavailable_defers_not_false(self, monkeypatch):
        # Empty/unrendered verdict (provider down) → None (defer), not a clean pass/fail.
        import personalclaw.loop.gates as gates

        monkeypatch.setattr(gates, "run_verify_command", lambda *a, **k: _coro(True))
        monkeypatch.setattr(gates, "judge_verdict", lambda *a, **k: _coro(""))
        loop = self._loop()
        assert (
            _run(self._kind().is_done_signal(loop, [{"cycle": 1, "summary": "engine only"}]))
            is None
        )

    def test_single_sub_goal_keeps_pure_command_behavior(self, monkeypatch):
        # 0/1 sub-goal → the command IS the whole goal; no judge required.
        import personalclaw.loop.gates as gates

        monkeypatch.setattr(gates, "run_verify_command", lambda *a, **k: _coro(True))
        loop = _running(
            kind="goal",
            task="make tests pass",
            kind_config={
                "goal_type": "verifiable",
                "verify_command": "npm test",
                "sub_goals": ["all tests pass"],
            },
        )
        assert _run(self._kind().is_done_signal(loop, [{"cycle": 1, "summary": "green"}])) is True


def _coro(v):
    async def _c(*a, **k):
        return v

    return _c()


class TestNewKindsRunEndToEnd:
    """general + design have no legacy engine behind them — drive each through the poll
    to lock in that they run on the unified engine (is_done_signal defers → budget caps,
    no on_new_cycle hook). A regression here would only surface in production."""

    @pytest.mark.parametrize("kind", ["general", "design"])
    def test_new_kind_completes_on_budget(self, kind):
        c = _running(kind=kind, kind_config={}, max_cycles=1)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())  # seed liveness
        _write_finding(c.id, 1)
        _run(wd._poll_once())  # no done-signal / no hook → budget (1) caps it
        assert store.get(c.id).status == LoopStatus.COMPLETE.value


class TestBudgetCap:
    def test_completes_at_budget(self):
        c = _running(kind_config={"goal_type": "open_ended"}, max_cycles=1)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())
        _write_finding(c.id, 1)
        _run(wd._poll_once())  # open_ended judge defers (no model) → budget (1) caps it
        assert store.get(c.id).status == LoopStatus.COMPLETE.value
        # A budget stop is NON-genuine: error_message records WHY, so the cockpit can
        # distinguish "stopped on budget" from a clean done after a reload.
        assert "budget" in (store.get(c.id).error_message or "").lower()

    def test_genuine_complete_has_no_error_note(self):
        c = _running(
            kind_config={"goal_type": "verifiable", "verify_command": "true"}, max_cycles=20
        )
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())
        _write_finding(c.id, 1)
        _run(wd._poll_once())  # verify passes → genuine completion
        assert store.get(c.id).status == LoopStatus.COMPLETE.value
        assert (store.get(c.id).error_message or "") == ""  # clean done, no note

    def test_monitor_budget_stop_is_genuine(self):
        # A monitor's cycle budget IS its watch window — reaching it is a clean
        # completion, NOT the error-flavored "stopped before done" an open_ended budget
        # stop gets. So no error_message even though it hit the cap without self-completing.
        c = _running(kind_config={"goal_type": "monitor"}, max_cycles=1)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())
        _write_finding(c.id, 1)
        _run(wd._poll_once())  # monitor never self-completes → budget (1) caps it, genuinely
        assert store.get(c.id).status == LoopStatus.COMPLETE.value
        assert (store.get(c.id).error_message or "") == ""  # clean — the watch window ended


class TestNeedsInput:
    def test_attended_question_pauses(self):
        c = _running(attended=True)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        store.write_question(c.id, "which db?")
        _run(wd._poll_once())
        assert store.get(c.id).status == LoopStatus.NEEDS_INPUT.value

    def test_unattended_question_is_discarded(self):
        c = _running(attended=False)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        store.write_question(c.id, "which db?")
        _run(wd._poll_once())
        assert store.get(c.id).status == LoopStatus.RUNNING.value
        assert store.pending_question(c.id) is None  # discarded


class TestTrustTtl:
    def test_expired_trust_pauses_for_reauth(self, monkeypatch):
        c = _running()
        # force started_at far in the past so the trust window is exceeded
        store.update_status(c.id, LoopStatus.RUNNING, started_at=1.0)
        wd = _wd()
        sess = _FakeSession(manager.session_key(c.id))
        wd._state._sessions[sess.key] = sess
        _run(wd._poll_once())
        assert store.get(c.id).status == LoopStatus.NEEDS_INPUT.value
        assert sess._trust is False


class TestCycleHook:
    """A kind with multi-cycle orchestration (code stage-advance, design steps)
    implements on_new_cycle, which OWNS the cycle's done-ness — the watchdog skips
    its generic is_done_signal path when the hook returns a bool."""

    def test_hook_completing_completes_the_loop(self, monkeypatch):
        from personalclaw.loop import kinds

        kinds.ensure_loaded()
        calls = {}

        async def _hook(loop, findings, ctx):
            calls["ran"] = True
            # the hook drives its own completion via the ctx (like code's final stage)
            await ctx.complete(loop.id, "all stages complete")
            return True

        monkeypatch.setattr(kinds.get("code"), "on_new_cycle", _hook, raising=False)
        c = _running(kind="code", kind_config={"entry_stage": "design"}, max_cycles=20)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())
        _write_finding(c.id, 1)
        _run(wd._poll_once())
        assert calls.get("ran") is True
        assert store.get(c.id).status == LoopStatus.COMPLETE.value

    def test_hook_returning_false_keeps_running(self, monkeypatch):
        from personalclaw.loop import kinds

        kinds.ensure_loaded()

        async def _hook(loop, findings, ctx):
            return False  # advanced a stage, not done yet

        monkeypatch.setattr(kinds.get("code"), "on_new_cycle", _hook, raising=False)
        c = _running(kind="code", kind_config={"entry_stage": "design"}, max_cycles=20)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())
        _write_finding(c.id, 1)
        _run(wd._poll_once())
        assert store.get(c.id).status == LoopStatus.RUNNING.value


class TestCycleVerdictPublish:
    """Open-ended done-ness writes a third-party verdict to the store; the watchdog
    must publish it (cycle_verdict + ratchet_regression) so the cockpit ROI rail /
    verdict panel update live. Kind-agnostic: no verdict persisted → no emit."""

    def _captured_wd(self):
        wd = _wd()
        events = []
        wd._publish = lambda lid, event, data=None: events.append((event, data))  # type: ignore[assignment]  # noqa: E501
        return wd, events

    def test_publishes_persisted_verdict_and_regression(self):
        c = _running()
        wd, events = self._captured_wd()
        store.write_verdict(
            c.id,
            3,
            {
                "cycle": 3,
                "done": False,
                "marginal_value": 1.2,
                "quality_score": 4.0,
                "regressed": True,
                "done_reason": "slipped",
            },
        )
        wd._publish_cycle_verdict(c.id, 3)
        kinds_emitted = {e for e, _ in events}
        assert "cycle_verdict" in kinds_emitted and "ratchet_regression" in kinds_emitted
        verdict_data = next(d for e, d in events if e == "cycle_verdict")
        assert verdict_data["cycle"] == 3 and verdict_data["marginal_value"] == 1.2

    def test_no_verdict_for_cycle_is_noop(self):
        c = _running()
        wd, events = self._captured_wd()
        wd._publish_cycle_verdict(c.id, 5)  # nothing persisted for cycle 5
        assert events == []

    def test_clean_verdict_emits_no_regression(self):
        c = _running()
        wd, events = self._captured_wd()
        store.write_verdict(
            c.id,
            1,
            {
                "cycle": 1,
                "done": False,
                "marginal_value": 3.0,
                "quality_score": 4.5,
                "regressed": False,
            },
        )
        wd._publish_cycle_verdict(c.id, 1)
        assert [e for e, _ in events] == ["cycle_verdict"]


class TestJudgeErrorOnlyWhenACheckExists:
    """A None done-signal means two different things: a kind that HAS a point-in-time
    check genuinely couldn't assess (degraded → surface judge_error) vs a kind that has
    NO such check for this config (defer-to-budget BY DESIGN → stay silent). The watchdog
    must only flag the former, or it false-alarms 'Done-ness check unavailable'."""

    def _captured_wd(self):
        wd = _wd()
        events = []
        wd._publish = lambda lid, event, data=None: events.append((event, data))  # type: ignore[assignment]  # noqa: E501
        return wd, events

    def test_general_without_verify_command_does_not_flag_judge_error(self):
        # has_done_check is False → None is normal, not degraded.
        c = _running(kind="general", kind_config={}, max_cycles=20)
        wd, events = self._captured_wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())  # seed liveness
        _write_finding(c.id, 1)
        _run(wd._poll_once())  # is_done_signal → None, but no check exists
        assert "judge_error" not in {e for e, _ in events}
        assert store.get(c.id).status == LoopStatus.RUNNING.value

    def test_open_ended_goal_with_no_verdict_still_flags_judge_error(self):
        # Goal open_ended HAS a judge check; a None verdict IS genuine degradation.
        c = _running(kind="goal", kind_config={"goal_type": "open_ended"}, max_cycles=20)
        wd, events = self._captured_wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())
        _write_finding(c.id, 1)
        _run(wd._poll_once())  # judge can't assess in-test → None → degraded
        assert "judge_error" in {e for e, _ in events}


class TestReconcileLinkedTasks:
    """On completion the watchdog force-closes still-open linked tasks (the worker
    leaves them open), EXCEPT a task whose exit criteria are unmet — left open so an
    incomplete checklist stays visible. Live now that goal decompose-on-finalize
    populates linked_task_ids."""

    @pytest.fixture(autouse=True)
    def _wire_tasks(self, monkeypatch, tmp_path):
        monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
        import personalclaw.tasks.native as nat

        monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)

    def test_closes_open_leaves_gated_open(self):
        from personalclaw.tasks import registry

        c = _running()
        plain = _run(registry.create_task(provider_name="native", title="done-able"))
        gated = _run(
            registry.create_task(provider_name="native", title="gated", exit_criteria=["ship it"])
        )
        store.link_tasks(c.id, [plain.id, gated.id])
        _run(_wd()._reconcile_linked_tasks(c.id))
        assert _run(registry.get_task(plain.id, provider_name="native")).status.value == "done"
        # gated task's exit criteria unmet → left open, not force-closed
        assert _run(registry.get_task(gated.id, provider_name="native")).status.value != "done"


class TestDeliverableArtifact:
    """On completion the watchdog surfaces a goal loop's document deliverable
    (REPORT.md / MONITOR_LOG.md) as a file-backed library artifact tagged loop:<id>,
    so the cockpit Outputs panel finds it. Kinds with no document deliverable
    (verifiable/code) register nothing."""

    class _FakeProvider:
        def __init__(self):
            self.created = []
            self.updated = []

        def find_by_source_path(self, source_path):
            return None

        def create(self, **kw):
            self.created.append(kw)
            return type("A", (), {"slug": "s"})()

        def update(self, slug, **kw):
            self.updated.append((slug, kw))

    def test_open_ended_report_registered(self, monkeypatch):
        c = _running(kind_config={"goal_type": "open_ended"})
        (store.loop_dir(c.id) / "REPORT.md").write_text("# Findings\nReal content.")
        prov = self._FakeProvider()
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: prov)
        _wd()._register_deliverable_artifact(c.id)
        assert len(prov.created) == 1
        assert f"loop:{c.id}" in prov.created[0]["tags"]

    def test_deliverable_retrievable_by_loop_tag(self, monkeypatch, tmp_path):
        # End-to-end through the REAL artifact provider: the registered deliverable must
        # be retrievable via list(tag="loop:<id>") — the exact query the cockpit Outputs
        # panel runs. Guards the whole chain (tag format → clean_tags → tag filter), not
        # just that the right kwarg was passed to a fake.
        from personalclaw.artifacts.native import NativeArtifactProvider

        real = NativeArtifactProvider(root=tmp_path / "artifacts")
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: real)
        c = _running(kind_config={"goal_type": "open_ended"})
        (store.loop_dir(c.id) / "REPORT.md").write_text("# Findings\nThe report body.")
        _wd()._register_deliverable_artifact(c.id)
        # list() is metadata-only (omits content, by design) — assert the tag query
        # finds it; then fetch its content via get() like the cockpit does on click.
        found = real.list(tag=f"loop:{c.id}")
        assert len(found) == 1 and f"loop:{c.id}" in found[0].tags
        assert "report body" in (real.get(found[0].slug).content or "").lower()

    def test_design_md_registered(self, monkeypatch):
        # a design loop's DESIGN.md is its document deliverable — must surface too.
        c = _running(kind="design", kind_config={})
        (store.loop_dir(c.id) / "DESIGN.md").write_text("# Design System\nTokens + components.")
        prov = self._FakeProvider()
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: prov)
        _wd()._register_deliverable_artifact(c.id)
        assert len(prov.created) == 1 and f"loop:{c.id}" in prov.created[0]["tags"]

    def test_verifiable_registers_nothing(self, monkeypatch):
        c = _running(kind_config={"goal_type": "verifiable", "verify_command": "true"})
        # even if a stray file exists, verifiable declares no deliverable name
        (store.loop_dir(c.id) / "REPORT.md").write_text("noise")
        prov = self._FakeProvider()
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: prov)
        _wd()._register_deliverable_artifact(c.id)
        assert prov.created == []

    def test_deliverable_resolved_in_bound_workspace(self, monkeypatch, tmp_path):
        # The brief directs the worker to write the deliverable into the BOUND WORKSPACE
        # (fix 2de9af4), NOT the loop dir. The watchdog must resolve workspace-first or
        # it never finds the file → no file-backed artifact (the live repro: SPEC.md in
        # the workspace, the loop dir empty, only the worker's ad-hoc artifact_save left).
        ws = tmp_path / "ws"
        ws.mkdir()
        c = _running(
            workspace_dir=str(ws),
            kind_config={"goal_type": "open_ended", "primary_deliverable": "SPEC.md"},
        )
        (ws / "SPEC.md").write_text("# SPEC\nThe locked contract.")
        # nothing in the loop dir — the OLD code looked only there and would register nothing
        prov = self._FakeProvider()
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: prov)
        _wd()._register_deliverable_artifact(c.id)
        assert len(prov.created) == 1
        assert prov.created[0]["source_path"] == str((ws / "SPEC.md").resolve())
        assert f"loop:{c.id}" in prov.created[0]["tags"]

    def test_missing_file_registers_nothing(self, monkeypatch):
        c = _running(kind_config={"goal_type": "open_ended"})  # no REPORT.md written
        prov = self._FakeProvider()
        monkeypatch.setattr("personalclaw.artifacts.registry.get_provider", lambda name=None: prov)
        _wd()._register_deliverable_artifact(c.id)
        assert prov.created == []


class TestStageAdvanceNotify:
    """A code loop advancing an SDLC stage publishes stage_advance, which must raise
    an in-app notification (parity with the legacy code watchdog) so a user on
    another page sees the progress."""

    def test_stage_advance_raises_notification(self):
        c = _running(kind="code")
        wd = _wd()
        wd._publish(c.id, "stage_advance", {"loop_id": c.id, "completed_stage": "design"})
        titles = [t for (_kind, t, _body, _meta) in wd._state.notes]
        assert "Stage complete" in titles

    def test_non_notify_event_raises_no_notification(self):
        c = _running(kind="code")
        wd = _wd()
        wd._publish(c.id, "task_started", {"loop_id": c.id, "task_id": "t-1"})
        assert wd._state.notes == []


class TestStagnation:
    def test_stagnant_after_window_of_empty_findings(self):
        c = _running(max_cycles=50)
        wd = _wd()
        wd._state._sessions[manager.session_key(c.id)] = _FakeSession(manager.session_key(c.id))
        _run(wd._poll_once())  # seed
        for i in range(1, W._STAGNATION_WINDOW + 1):
            _write_finding(c.id, i, new_findings_count=0)
            _run(wd._poll_once())
        assert store.get(c.id).status == LoopStatus.STAGNANT.value
