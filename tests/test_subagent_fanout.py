"""WF2WOR-8 — fan-out subagent-path defect fixes (C1.1–C1.5).

Each test proves one acceptance criterion from the plan's amendment task table:

- C1.1 injection wall: N near-simultaneous completions deliver in ONE batch turn
  without loss AND a delivery failure never resets the parent (context preserved).
- C1.2 queue correctness: a queued headless spawn keeps its full parameter set and
  is cancellable by its returned (real, non-colliding) id.
- C1.3 agent validation: an unknown agent name → typed error, no silent downgrade.
- C1.4 fan-out control: a run-scoped lane, one-click kill-fan-out, and a
  consecutive-child-failure breaker that trips at 5.
- C1.5 run-scoped budget: re-checked mid-flight, stops a fan-out with a typed reason
  and per-child cost is surfaced on the completion event.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.subagent import _CIRCUIT_BREAKER_THRESHOLD, SubagentInfo, SubagentManager


def _mock_ctx_builder() -> MagicMock:
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    return ctx


def _completing_sessions(*, text: str = "result", cost: float = 0.0) -> MagicMock:
    """A SessionManager mock whose provider yields a text chunk + COMPLETE, so a
    spawned subagent runs to a real completion."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    provider = AsyncMock()
    provider.start = AsyncMock()
    provider.shutdown = AsyncMock()
    provider.context_usage_pct = lambda: 0.0
    provider.session_id = "uuid"

    async def _stream(*_a, **_kw):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        ev = LLMEvent(kind=EVENT_COMPLETE)
        ev.input_tokens = 1000  # type: ignore[attr-defined]
        ev.output_tokens = 500  # type: ignore[attr-defined]
        ev.cost_usd = cost  # type: ignore[attr-defined]
        yield ev

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.get_approval_policy = MagicMock(return_value="auto")
    sessions.has_session = MagicMock(return_value=False)
    return sessions


# ── C1.1 ─────────────────────────────────────────────────────────────


class TestInjectionWall:
    @pytest.mark.asyncio
    async def test_eight_completions_deliver_in_one_batch_without_loss(self) -> None:
        """8 near-simultaneous completions for ONE parent deliver as a single batch
        turn (C1.1) — every result present, no loss."""
        delivered: list[list[SubagentInfo]] = []

        async def on_done(batch: list[SubagentInfo]) -> None:
            delivered.append(list(batch))

        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=on_done,
            is_yolo=lambda: True,
            max_concurrent=8,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            infos = [mgr.spawn(f"task {i}", parent_session_key="dashboard:orch") for i in range(8)]
            await asyncio.gather(*(mgr._tasks[i.id] for i in infos))  # type: ignore[union-attr]
            await mgr.flush_deliveries()

        all_ids = {i.id for batch in delivered for i in batch}  # type: ignore[union-attr]
        assert all_ids == {i.id for i in infos}  # every result delivered, none lost
        # coalesced into far fewer turns than 8 (the whole point of C1.1)
        assert len(delivered) < 8

    @pytest.mark.asyncio
    async def test_delivery_failure_preserves_parent_session(self) -> None:
        """C1.1 REGRESSION: a delivery timeout does NOT reset the parent session —
        the orchestrator's context is preserved; the failure is surfaced instead."""
        events: list[str] = []

        async def hanging_on_done(batch: list[SubagentInfo]) -> None:
            await asyncio.sleep(999)

        async def on_event(etype: str, info: object, extra: dict) -> None:
            if etype == "subagent_injection_failed":
                events.append(etype)

        sessions = _completing_sessions()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=hanging_on_done,
            on_event=on_event,
            is_yolo=lambda: True,
        )
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch("personalclaw.subagent._ON_DONE_TIMEOUT", 0.05),
        ):
            info = mgr.spawn("task", parent_session_key="dashboard:orch")
            await mgr._tasks[info.id]  # type: ignore[union-attr]
            await mgr.flush_deliveries()

        # The parent key was NEVER reset (context preserved).
        reset_keys = [c.args[0] for c in sessions.reset.await_args_list if c.args]
        assert "dashboard:orch" not in reset_keys
        assert "subagent_injection_failed" in events


# ── C1.2 ─────────────────────────────────────────────────────────────


class TestQueueCorrectness:
    @pytest.mark.asyncio
    async def test_queued_spawn_keeps_full_parameter_set(self) -> None:
        """A queued headless spawn retains approval_mode/model/silent/dry_run and
        parent_run (C1.2) — no silent parameter drop."""
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            mgr.spawn("first", parent_session_key="dashboard:orch")  # fills the slot
            queued = mgr.spawn(
                "second",
                parent_session_key="dashboard:orch",
                approval_mode="auto",
                model="claude-opus",
                silent=True,
                dry_run=True,
                parent_run="workflow:run-1",
            )
        assert queued is not None and queued.queued is True
        assert queued.approval_mode == "auto"
        assert queued.model == "claude-opus"
        assert queued.silent is True
        assert queued.dry_run is True
        assert queued.parent_run == "workflow:run-1"

    @pytest.mark.asyncio
    async def test_queued_spawn_cancellable_by_id_and_ids_never_collide(self) -> None:
        """A queued spawn is cancellable by its returned id, and queued ids never
        collide across drains (C1.2)."""
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            a = mgr.spawn("run", parent_session_key="dashboard:orch")
            q1 = mgr.spawn("q1", parent_session_key="dashboard:orch")
            q2 = mgr.spawn("q2", parent_session_key="dashboard:orch")
            assert q1 is not None and q2 is not None
            # ids are real (no q<N> placeholder) and unique
            assert len({a.id, q1.id, q2.id}) == 3  # type: ignore[union-attr]
            assert not q1.id.startswith("q") and not q2.id.startswith("q")
            # cancel the FIRST queued spawn by its id
            ok = await mgr.cancel(q1.id)
            assert ok is True
            assert q1.done is True and q1.cancelled is True
            # it is gone from the queue; the other queued spawn remains addressable
            assert all(qi.id != q1.id for qi in mgr._queue)
            assert mgr.get(q2.id) is q2


# ── C1.3 ─────────────────────────────────────────────────────────────


class TestAgentValidation:
    @pytest.mark.asyncio
    async def test_unknown_agent_typed_error_no_downgrade(self) -> None:
        """Spawning an unknown agent fails with a typed error naming valid agents —
        no silent downgrade to the default (C1.3)."""
        cfg = MagicMock()
        cfg.agents = {"personalclaw": MagicMock(), "code-reviewer": MagicMock()}
        cfg.agent.approval_mode = "auto"
        # `spawn` runs the memory gate BEFORE agent validation and reads
        # `spawn_min_memory_gb` into `check_memory_available(min_gb=...)`. A bare MagicMock there
        # raised `TypeError: '>=' not supported between float and MagicMock` on any host where
        # /proc/meminfo is readable (Linux CI) while passing on macOS, where the missing file
        # short-circuits before the comparison — a platform split that hid the failure locally.
        # Give it a real number so the memory gate is a no-op and validation is reached.
        cfg.agent.spawn_min_memory_gb = 0.0
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch("personalclaw.config.loader.AppConfig.load", return_value=cfg),
        ):
            info = mgr.spawn("task", parent_session_key="dashboard:orch", agent="does-not-exist")
        assert info is not None
        assert info.done is True
        assert info.error
        assert "does-not-exist" in info.error
        assert "code-reviewer" in info.error  # names the valid agents
        assert info.agent == ""  # not downgraded to a running default
        assert info.id not in mgr._tasks  # never started


# ── C1.4 ─────────────────────────────────────────────────────────────


class TestFanoutControl:
    @pytest.mark.asyncio
    async def test_run_scoped_lane_does_not_starve_other_runs(self) -> None:
        """A run-scoped lane caps ONE run's concurrency so a wide run cannot take
        every slot away from another run (C1.4)."""
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=4,
            run_lane_cap=2,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            # Run A tries for 4 slots but its lane caps at 2.
            a = [
                mgr.spawn(f"a{i}", parent_session_key="dashboard:x", parent_run="workflow:A")
                for i in range(4)
            ]
            running_a = [i for i in a if not i.queued]  # type: ignore[union-attr]
            queued_a = [i for i in a if i.queued]  # type: ignore[union-attr]
            assert len(running_a) == 2  # lane-capped
            assert len(queued_a) == 2
            # Run B still gets its own lane slots — not starved by A.
            b = mgr.spawn("b0", parent_session_key="dashboard:y", parent_run="workflow:B")
            assert b is not None and b.queued is False

    @pytest.mark.asyncio
    async def test_kill_fanout_cancels_all_children_of_one_run(self) -> None:
        """One call kills EVERY child (running + queued) of one fan-out, leaving
        other runs untouched (C1.4)."""
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=2,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            a = [
                mgr.spawn(f"a{i}", parent_session_key="dashboard:x", parent_run="workflow:A")
                for i in range(3)
            ]
            b = mgr.spawn("b0", parent_session_key="dashboard:y", parent_run="workflow:B")
            n = await mgr.cancel_fanout("workflow:A")
            assert n == 3
            assert all(i.done and i.cancelled for i in a)  # type: ignore[union-attr]
            # B is untouched — cancel_fanout scopes to ONE run (B was never cancelled;
            # it may have run to completion on its own).
            assert b is not None and b.cancelled is False

    @pytest.mark.asyncio
    async def test_record_failure_breaker_trips_at_five(self) -> None:
        """Five consecutive child failures in one fan-out trip its breaker, which
        then stops further spawns (C1.4) — the session-level breaker could never
        trip for sub-agents because the per-child session is already released."""
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        fkey = "workflow:A"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            for i in range(_CIRCUIT_BREAKER_THRESHOLD):
                info = SubagentInfo(
                    id=f"c{i}", task="t", parent_session_key="dashboard:x", parent_run=fkey
                )
                info.error = "boom"
                mgr._note_child_outcome(info)
            assert mgr._fanout_failures.get(fkey, 0) >= _CIRCUIT_BREAKER_THRESHOLD
            assert fkey in mgr._fanout_stops
            # A new spawn for the tripped fan-out is refused.
            refused = mgr.spawn("late", parent_session_key="dashboard:x", parent_run=fkey)
            assert refused is not None and refused.done and "breaker" in refused.error

    @pytest.mark.asyncio
    async def test_success_resets_failure_streak(self) -> None:
        """A success clears the consecutive-failure streak (C1.4) — only CONSECUTIVE
        failures trip the breaker."""
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
        )
        fkey = "workflow:A"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            for i in range(4):
                bad = SubagentInfo(id=f"b{i}", task="t", parent_run=fkey)
                bad.error = "boom"
                mgr._note_child_outcome(bad)
            good = SubagentInfo(id="ok", task="t", parent_run=fkey)  # no error
            mgr._note_child_outcome(good)
            assert mgr._fanout_failures.get(fkey, 0) == 0
            assert fkey not in mgr._fanout_stops

    @pytest.mark.asyncio
    async def test_cancelled_child_is_not_a_failure(self) -> None:
        """A user-cancelled child does not count toward the breaker (C1.4)."""
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
        )
        fkey = "workflow:A"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            for i in range(_CIRCUIT_BREAKER_THRESHOLD + 2):
                c = SubagentInfo(id=f"c{i}", task="t", parent_run=fkey)
                c.error = "cancelled"
                c.cancelled = True
                mgr._note_child_outcome(c)
            assert fkey not in mgr._fanout_stops


# ── C1.5 ─────────────────────────────────────────────────────────────


class TestRunBudget:
    @pytest.mark.asyncio
    async def test_run_budget_stops_fanout_midflight_with_typed_reason(self) -> None:
        """A fan-out that would exceed the run budget stops mid-flight with a typed
        reason (C1.5), composing with the SpendMeter run scope."""
        from personalclaw.guardrails.budgets import Budget, reset_meter

        reset_meter()
        # Two children of 1500 tokens each; a 2000-token run ceiling bites on #2.
        sessions = _completing_sessions(cost=0.0)
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,  # serialize so the mid-flight re-check bites the 2nd
        )
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch(
                "personalclaw.guardrails.budgets.run_budget_from_config",
                return_value=Budget(max_tokens=2000),
            ),
        ):
            first = mgr.spawn("c1", parent_session_key="dashboard:x", parent_run="workflow:A")
            second = mgr.spawn("c2", parent_session_key="dashboard:x", parent_run="workflow:A")
            await mgr._tasks[first.id]  # type: ignore[union-attr]
            await mgr.flush_deliveries()
            # first child charged 1500 tokens → over the 2000 ceiling after it
            assert "workflow:A" in mgr._fanout_stops
            assert "budget" in mgr._fanout_stops["workflow:A"]
            # the queued second child is refused with the typed reason on drain
            assert second is not None
        reset_meter()

    @pytest.mark.asyncio
    async def test_per_child_cost_on_completion_event(self) -> None:
        """Per-child cost + tokens are surfaced on the subagent_done event (C1.5),
        consuming the T1.3 figures captured at EVENT_COMPLETE — not a new ledger."""
        seen: dict = {}

        async def on_event(etype: str, info: object, extra: dict) -> None:
            if etype == "subagent_done":
                seen.update(extra)

        mgr = SubagentManager(
            sessions=_completing_sessions(cost=0.42),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            on_event=on_event,
            is_yolo=lambda: True,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = mgr.spawn("task", parent_session_key="dashboard:orch")
            await mgr._tasks[info.id]  # type: ignore[union-attr]
            await mgr.flush_deliveries()
        assert seen.get("cost_usd") == pytest.approx(0.42)
        assert seen.get("tokens") == 1500  # 1000 in + 500 out
