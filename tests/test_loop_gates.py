"""Shared loop done-ness gates (Slice 2c.i) — the supervisor's verify-command
runner. Tristate (pass/fail/can't-run) so a missing tool isn't misread as a real
failure; security-screened; bounded + never raises."""

from __future__ import annotations

import pytest

from personalclaw.loop.gates import run_verify_command


@pytest.mark.asyncio
async def test_exit_zero_is_pass():
    assert await run_verify_command("true", None) is True


@pytest.mark.asyncio
async def test_real_nonzero_is_fail():
    assert await run_verify_command("false", None) is False


@pytest.mark.asyncio
async def test_missing_binary_is_none_not_fail():
    # exit 127 = tool not installed → can't tell, NOT a real failure (a verifiable
    # gate must not silently spin forever reading this as "didn't pass yet").
    assert await run_verify_command("this-binary-does-not-exist-pclaw", None) is None


@pytest.mark.asyncio
async def test_empty_command_is_none():
    assert await run_verify_command("", None) is None
    assert await run_verify_command("   ", None) is None


@pytest.mark.asyncio
async def test_destructive_command_is_refused():
    # The safety screen refuses a destructive command → None (never "passes").
    assert await run_verify_command("rm -rf /", None) is None


def test_verdict_rendered_distinguishes_real_verdict_from_cant_judge():
    # verdict_rendered tells a genuine PASS/FAIL apart from an empty/errored judge
    # output (provider unavailable / stream timeout → judge_verdict returns "").
    # A flaky judge must NOT be read as FAIL when deterministic gates already passed.
    from personalclaw.loop.gates import verdict_is_pass, verdict_rendered

    assert verdict_rendered("PASS") and verdict_is_pass("PASS")
    assert verdict_rendered("FAIL: criterion 2 unmet") and not verdict_is_pass(
        "FAIL: criterion 2 unmet"
    )
    # empty (judge errored/timed out) or pure prose → NO verdict rendered
    assert not verdict_rendered("")
    assert not verdict_rendered(None)
    assert not verdict_rendered("the model could not be reached")


class TestOpenEndedJudgePath:
    """Goal open-ended done-ness: a SEPARATE judge subagent scores done-ness +
    marginal value; the granularity dial decides returns-exhaustion. Judge advises,
    supervisor decides. The judge module is mocked (no live model)."""

    @pytest.fixture(autouse=True)
    def _tmp(self, monkeypatch, tmp_path):
        monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)

    def _loop(self, **cfg):
        from personalclaw.loop import store
        from personalclaw.loop.loop import Loop

        # judge_calibrated=True = the steady state after the P4 canary has proven the judge
        # once at loop start; these tests exercise the per-cycle verdict path, not the canary,
        # so they represent an already-calibrated loop (the canary path has its own tests in
        # test_loop_instrument.py).
        base = {"goal_type": "open_ended", "granularity": "balanced", "judge_calibrated": True}
        base.update(cfg)
        return store.create(
            Loop(
                id="",
                name="g",
                kind="goal",
                task="research X",
                success_criteria="answer Q",
                kind_config=base,
            )
        )

    @pytest.mark.asyncio
    async def test_judge_done_completes_and_persists_verdict(self, monkeypatch):
        from unittest.mock import AsyncMock

        from personalclaw.loop import kinds, store
        from personalclaw.loop.judge import CycleVerdict

        kinds.ensure_loaded()
        loop = self._loop()
        monkeypatch.setattr(
            "personalclaw.loop.judge.assess_cycle",
            AsyncMock(
                return_value=CycleVerdict(
                    done=True, done_reason="met", marginal_value=3.0, quality_score=4.0
                )
            ),
        )
        assert await kinds.get("goal").is_done_signal(loop, [{"cycle": 1, "summary": "x"}]) is True
        assert store.get_verdicts(loop.id)[0]["done"] is True
        assert store.get_marginal_scores(loop.id) == [3.0]

    @pytest.mark.asyncio
    async def test_judge_failure_defers_with_none(self, monkeypatch):
        from unittest.mock import AsyncMock

        from personalclaw.loop import kinds

        kinds.ensure_loaded()
        loop = self._loop()
        monkeypatch.setattr("personalclaw.loop.judge.assess_cycle", AsyncMock(return_value=None))
        # None (defer) — NOT a clean False — so the watchdog can flag degradation.
        assert await kinds.get("goal").is_done_signal(loop, [{"cycle": 1}]) is None

    @pytest.mark.asyncio
    async def test_monitor_never_completes(self):
        from personalclaw.loop import kinds

        kinds.ensure_loaded()
        loop = self._loop(goal_type="monitor")
        assert await kinds.get("goal").is_done_signal(loop, [{"cycle": 1}]) is False


@pytest.mark.asyncio
async def test_verifiable_goal_kind_runs_the_command():
    from personalclaw.loop import kinds
    from personalclaw.loop.loop import Loop

    kinds.ensure_loaded()
    s = kinds.get("goal")
    ok = Loop(
        id="abcd1234",
        name="g",
        kind="goal",
        task="t",
        kind_config={"goal_type": "verifiable", "verify_command": "true"},
    )
    assert await s.is_done_signal(ok, []) is True
    bad = Loop(
        id="abcd1234",
        name="g",
        kind="goal",
        task="t",
        kind_config={"goal_type": "verifiable", "verify_command": "false"},
    )
    assert await s.is_done_signal(bad, []) is False
    # open_ended defers (judge not yet wired here)
    oe = Loop(
        id="abcd1234", name="g", kind="goal", task="t", kind_config={"goal_type": "open_ended"}
    )
    assert await s.is_done_signal(oe, []) is None


class TestJudgeIndependence:
    """Slice C (O-E2): the open-ended judge independently observes ground truth — runs
    the goal's verify command + reads named deliverable files — instead of scoring only
    the worker's reported finding."""

    @pytest.mark.asyncio
    async def test_observe_ground_truth_runs_command_and_reads_file(self, tmp_path):
        from personalclaw.loop.judge import _observe_ground_truth

        (tmp_path / "REPORT.md").write_text("# Findings\nUNIQUE_OBSERVED_TOKEN in the report.\n")
        # command runs (true → PASSED), and the named file is read
        block = await _observe_ground_truth("true", str(tmp_path), ["REPORT.md"])
        assert "supervisor observed DIRECTLY" in block
        assert "PASSED (exit 0)" in block
        assert "UNIQUE_OBSERVED_TOKEN" in block

    @pytest.mark.asyncio
    async def test_observe_ground_truth_failed_command(self, tmp_path):
        from personalclaw.loop.judge import _observe_ground_truth

        block = await _observe_ground_truth("false", str(tmp_path), [])
        assert "FAILED (non-zero exit)" in block

    @pytest.mark.asyncio
    async def test_judge_unavailable_is_observable_not_silent(self, caplog):
        """A degraded judge (its 'reasoning'/'chat' provider can't start) returns None
        (defer, never a false complete) AND logs WARNING — the done-ness brain failing
        must be diagnosable, not an invisible forever-defer. Regression: it logged at
        debug, so an unresolvable judge provider silently no-op'd judge independence."""
        import logging

        from personalclaw.loop import judge as judge_mod

        def _boom_factory(_key):
            raise RuntimeError("no reasoning provider configured")

        with caplog.at_level(logging.WARNING, logger="personalclaw.loop.judge"):
            verdict = await judge_mod.assess_cycle(
                "goal", "dod", {"cycle": 1, "summary": "x"}, [], provider_factory=_boom_factory
            )
        assert verdict is None  # defer — never a false complete
        assert any("degraded" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_observe_ground_truth_empty_when_no_anchor(self, tmp_path):
        from personalclaw.loop.judge import _observe_ground_truth

        # no command + no readable deliverable → nothing observed (stays transcript-only)
        assert await _observe_ground_truth("", str(tmp_path), ["missing.md"]) == ""

    @pytest.mark.asyncio
    async def test_observe_ground_truth_searches_fallback_dirs(self, tmp_path):
        """V6 fix: the deliverable may live in the loop dir (unbound loop), not the workspace.
        _observe_ground_truth must search fallback_dirs too, else the skeptic wrongly reports
        'no proof the file exists' and overturns a legitimate completion."""
        from personalclaw.loop.judge import _observe_ground_truth

        ws = tmp_path / "ws"
        ws.mkdir()  # workspace: exists but no deliverable
        loopdir = tmp_path / "loop"
        loopdir.mkdir()
        (loopdir / "REPORT.md").write_text("real deliverable body", encoding="utf-8")
        # workspace-only → not found (the pre-fix behavior)
        assert await _observe_ground_truth("", str(ws), ["REPORT.md"]) == ""
        # with the loop dir as a fallback → found
        block = await _observe_ground_truth("", str(ws), ["REPORT.md"], [str(loopdir)])
        assert "real deliverable body" in block

    @pytest.mark.asyncio
    async def test_assess_cycle_injects_ground_truth_into_prompt(self, tmp_path, monkeypatch):
        """assess_cycle with a verify_command + deliverable feeds the observed ground truth
        into the judge prompt — captured via a fake provider."""
        from personalclaw.loop import judge as judge_mod

        (tmp_path / "OUT.md").write_text("PROOF_MARKER_C ok\n")

        seen = {}

        class _FakeProvider:
            async def start(self):
                pass

            async def shutdown(self):
                pass

            async def reject_tool(self, _rid):
                pass

            async def stream(self, prompt):
                seen["prompt"] = prompt
                from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

                class _E:
                    def __init__(self, kind, text=""):
                        self.kind = kind
                        self.text = text
                        self.request_id = None

                yield _E(
                    EVENT_TEXT_CHUNK,
                    '{"done": false, "done_reason": "n", "marginal_value": 2, "quality_score": 3, "regressed": false}',  # noqa: E501
                )
                yield _E(EVENT_COMPLETE)

        class _FakeJudge:
            def __init__(self, *_a):
                self._provider = _FakeProvider()

            async def start(self):
                await self._provider.start()

            async def shutdown(self):
                await self._provider.shutdown()

        monkeypatch.setattr("personalclaw.eval.judge.LLMJudge", _FakeJudge)
        verdict = await judge_mod.assess_cycle(
            "build a thing",
            "done when OUT.md exists",
            {"cycle": 1, "summary": "worker says done"},
            [],
            verify_command="true",
            workspace=str(tmp_path),
            deliverables=["OUT.md"],
        )
        assert verdict is not None and verdict.done is False
        # the judge prompt carried the supervisor-observed ground truth, not just the summary
        assert "PROOF_MARKER_C" in seen["prompt"]
        assert "GROUND TRUTH the supervisor observed" in seen["prompt"]
        assert "PASSED (exit 0)" in seen["prompt"]

    @pytest.mark.asyncio
    async def test_open_ended_caller_threads_anchors(self, monkeypatch, tmp_path):
        """The goal open-ended path passes verify_command + workspace + deliverables from
        kind_config into assess_cycle (so the judge can observe ground truth)."""
        from unittest.mock import AsyncMock

        from personalclaw.loop import kinds, store
        from personalclaw.loop.judge import CycleVerdict
        from personalclaw.loop.loop import Loop

        monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
        kinds.ensure_loaded()
        loop = store.create(
            Loop(
                id="",
                name="g",
                kind="goal",
                task="t",
                success_criteria="c",
                workspace_dir=str(tmp_path),
                kind_config={
                    "goal_type": "open_ended",
                    "verify_command": "true",
                    "deliverables": ["OUT.md"],
                    "judge_calibrated": True,
                },
            )
        )
        spy = AsyncMock(return_value=CycleVerdict(done=True, done_reason="ok"))
        monkeypatch.setattr("personalclaw.loop.judge.assess_cycle", spy)
        await kinds.get("goal").is_done_signal(loop, [{"cycle": 1, "summary": "x"}])
        _, kwargs = spy.call_args
        assert kwargs["verify_command"] == "true"
        assert kwargs["workspace"] == str(tmp_path)
        assert kwargs["deliverables"] == ["OUT.md"]

    @pytest.mark.asyncio
    async def test_open_ended_falls_back_to_project_context_dir(self, monkeypatch, tmp_path):
        """When the goal loop has NO workspace_dir (the common open-ended case), the judge
        must still get the effective dir — the project's context dir — so it can find the
        deliverable. Regression for live goal 0fef190e (workspace_dir='' + a deliverable →
        the ground-truth read silently no-op'd)."""
        from unittest.mock import AsyncMock

        from personalclaw.loop import kinds, store
        from personalclaw.loop.judge import CycleVerdict
        from personalclaw.loop.loop import Loop

        monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        monkeypatch.setattr("personalclaw.projects.context_dir", lambda pid: str(ctx))
        kinds.ensure_loaded()
        loop = store.create(
            Loop(
                id="",
                name="g",
                kind="goal",
                task="t",
                success_criteria="c",
                workspace_dir="",
                project_id="proj-1",
                kind_config={
                    "goal_type": "open_ended",
                    "deliverables": ["throughput_report.md"],
                    "judge_calibrated": True,
                },
            )
        )
        spy = AsyncMock(return_value=CycleVerdict(done=False, done_reason=""))
        monkeypatch.setattr("personalclaw.loop.judge.assess_cycle", spy)
        await kinds.get("goal").is_done_signal(loop, [{"cycle": 1, "summary": "x"}])
        _, kwargs = spy.call_args
        # the judge got the project context dir, NOT None (which would skip the read)
        assert kwargs["workspace"] == str(ctx)

    def test_effective_dir_falls_back_to_workspace_root(self, monkeypatch, tmp_path):
        """A loop with NO workspace_dir AND no project still resolves the effective dir to
        the default session workspace_root — where such a loop's worker actually writes
        (observed live: goal 0fef190e wrote its deliverable to the workspace root, but
        effective_dir returned '' until this fallback was added → judge saw nothing)."""
        from personalclaw.loop.loop import Loop, effective_dir

        root = tmp_path / "wsroot"
        root.mkdir()
        monkeypatch.setattr("personalclaw.config.loader.workspace_root", lambda: root)
        loop = Loop(id="z", name="g", kind="goal", task="t", workspace_dir="", project_id="")
        assert effective_dir(loop) == str(root)

    def test_effective_dir_greenfield_code_uses_loop_dir(self, monkeypatch, tmp_path):
        """A greenfield CODE loop (no bound workspace, no project) operates FROM its own
        loop dir — the brief says "(none — operate from the project files dir)" and the
        worker writes its deliverable there, not to the shared workspace root. The
        supervisor's ground-truth gate must resolve to that same dir, or `_resolve_deliverable`
        never finds the deliverable → "stage held" forever on genuinely-complete work
        (observed live: greenfield loop 07d5a0d0, 15/15 tests green, stage held)."""
        from personalclaw.loop.loop import Loop, effective_dir

        root = tmp_path / "wsroot"
        root.mkdir()
        loop_files = tmp_path / "loopdir"
        loop_files.mkdir()
        monkeypatch.setattr("personalclaw.config.loader.workspace_root", lambda: root)
        monkeypatch.setattr("personalclaw.loop.store.loop_dir", lambda lid: loop_files)
        code = Loop(id="c", name="g", kind="code", task="t", workspace_dir="", project_id="")
        assert effective_dir(code) == str(loop_files)
        # A goal loop with the same empty binding must NOT be redirected to its loop dir —
        # it keeps only engine files there and writes its deliverable to the workspace root.
        goal = Loop(id="c", name="g", kind="goal", task="t", workspace_dir="", project_id="")
        assert effective_dir(goal) == str(root)
        # A code loop WITH a bound workspace still wins on tier 1 (the bound codebase).
        bound = tmp_path / "codebase"
        bound.mkdir()
        brown = Loop(
            id="c", name="g", kind="code", task="t", workspace_dir=str(bound), project_id=""
        )
        assert effective_dir(brown) == str(bound)
