"""Fan-out subagent-path defect fixes.

Each test proves one of these properties:

- Injection wall: N near-simultaneous completions deliver in ONE batch turn
  without loss AND a delivery failure never resets the parent (context preserved).
- Queue correctness: a queued headless spawn keeps its full parameter set and
  is cancellable by its returned (real, non-colliding) id.
- Agent validation: an unknown agent name → typed error, no silent downgrade.
- Fan-out control: a run-scoped lane, one-click kill-fan-out, and a
  consecutive-child-failure breaker that trips at 5.
- Run-scoped budget: re-checked mid-flight, stops a fan-out with a typed reason
  and per-child cost is surfaced on the completion event.
- A fan-out's stop: it refuses what is on its way into the fan-out and ends once nothing of it
  runs or waits, however its last helper ends (on its own, stopped, refused in a drain, or refused
  at its start), and a stopped helper hands its slot to the one waiting for it.
- A workflow run's key: its breaker and run budget count every step of the run, one after another
  as well as side by side, and end with the run.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.started_work import TURN_STOPPED
from personalclaw.subagent import _CIRCUIT_BREAKER_THRESHOLD, SubagentInfo, SubagentManager

# Bounded stand-in hang: long enough to outlast the cap under test, short enough that a
# cap which fails to bind costs one assertion failure rather than the 120s
# pytest-timeout that took a whole CI shard down (#2996, #3143).
_HANG_SECS = 2.0


def _mock_ctx_builder() -> MagicMock:
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    return ctx


async def _stop(mgr: SubagentManager) -> None:
    """Stop what *mgr* started before its test's loop closes. A child still running at the close
    is cancelled there, and its teardown starts a queued one in a task the close never cancels;
    once collected, that task runs *mgr*'s teardown in whichever test's loop is running then, and
    ends its fan-out's run on the one spend meter the process has: a fan-out with the same key in
    that test loses its run's total. A delivery still waiting out its window is sent here too,
    rather than left pending when the loop closes."""
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await mgr.cancel_all()
        await mgr.flush_deliveries()


async def _settle(*running: SubagentManager, strict: bool = True) -> list[asyncio.Task]:
    """Let every task this test's loop holds run to its end, the run budget's late cancel and a
    reap's tail among them (each started with nothing awaiting it), except the helpers still
    running in *running*. Tasks may start others, so it looks again until none is left. Past
    ``_HANG_SECS`` something is stuck: *strict* fails the test there; otherwise what is still
    pending is returned."""
    loop = asyncio.get_running_loop()
    me = asyncio.current_task()
    deadline = loop.time() + _HANG_SECS
    while True:
        spared = {t for mgr in running for t in mgr._tasks.values()}
        rest = [t for t in asyncio.all_tasks() if t is not me and not t.done() and t not in spared]
        if not rest:
            return []
        if loop.time() >= deadline:
            if strict:
                raise AssertionError(f"still pending after {_HANG_SECS}s: {rest}")
            return rest
        await asyncio.wait(rest, timeout=deadline - loop.time())


def _owner(task: asyncio.Task) -> object:
    """The object whose method *task* runs, when it runs one."""
    frame = getattr(task.get_coro(), "cr_frame", None)
    return frame.f_locals.get("self") if frame is not None else None


@pytest_asyncio.fixture(autouse=True)
async def _every_manager_is_stopped(monkeypatch):
    """Stop every manager a test made, even one whose test failed or never reached its own
    ``_stop``, then hold the test to leaving nothing pending in its loop. A helper left running or
    waiting is what reached the next test's loop and its spend meter (``_stop``). A task still
    pending is named as this test's (one its managers held, or whose method is theirs) or as
    another's: one left by an earlier test can be finalized into this test's loop."""
    made: list[SubagentManager] = []
    build = SubagentManager.__init__

    def _recording(self, *args, **kwargs):
        build(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(SubagentManager, "__init__", _recording)
    yield
    held = {
        t
        for mgr in made
        for t in (
            *mgr._tasks.values(),
            *mgr._delivery_tasks.values(),
            *mgr._reaps_finishing,
            *mgr._events_in_flight,
        )
    }
    for mgr in made:
        await _stop(mgr)
    pending = await _settle(strict=False)
    ours = [t for t in pending if t in held or any(_owner(t) is mgr for mgr in made)]
    others = [t for t in pending if t not in ours]
    assert not pending, f"left behind by this test: {ours}; left by another test: {others}"


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


# ── injection wall ───────────────────────────────────────────────────


class TestInjectionWall:
    @pytest.mark.asyncio
    async def test_eight_completions_deliver_in_one_batch_without_loss(self) -> None:
        """8 near-simultaneous completions for ONE parent deliver as a single batch
        turn — every result present, no loss."""
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
        # coalesced into far fewer turns than 8 (the whole point)
        assert len(delivered) < 8

    @pytest.mark.asyncio
    async def test_delivery_failure_preserves_parent_session(self) -> None:
        """REGRESSION: a delivery timeout does NOT reset the parent session —
        the orchestrator's context is preserved; the failure is surfaced instead."""
        events: list[str] = []

        async def hanging_on_done(batch: list[SubagentInfo]) -> None:
            await asyncio.sleep(_HANG_SECS)

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
            on_done_timeout=0.05,
        )
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
        ):
            info = mgr.spawn("task", parent_session_key="dashboard:orch")
            await mgr._tasks[info.id]  # type: ignore[union-attr]
            await mgr.flush_deliveries()

        # The parent key was NEVER reset (context preserved).
        reset_keys = [c.args[0] for c in sessions.reset.await_args_list if c.args]
        assert "dashboard:orch" not in reset_keys
        assert "subagent_injection_failed" in events


# ── queue correctness ────────────────────────────────────────────────


class TestQueueCorrectness:
    @pytest.mark.asyncio
    async def test_queued_spawn_keeps_full_parameter_set(self) -> None:
        """A queued headless spawn retains approval_mode/model/silent/dry_run and
        parent_run — no silent parameter drop."""
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
        await _stop(mgr)

    @pytest.mark.asyncio
    async def test_queued_spawn_cancellable_by_id_and_ids_never_collide(self) -> None:
        """A queued spawn is cancellable by its returned id, and queued ids never
        collide across drains."""
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
        await _stop(mgr)


# ── agent validation ─────────────────────────────────────────────────


class TestAgentValidation:
    @pytest.mark.asyncio
    async def test_unknown_agent_typed_error_no_downgrade(self) -> None:
        """Spawning an unknown agent fails with a typed error naming valid agents —
        no silent downgrade to the default."""
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


# ── fan-out control ──────────────────────────────────────────────────


class TestFanoutControl:
    @pytest.mark.asyncio
    async def test_run_scoped_lane_does_not_starve_other_runs(self) -> None:
        """A run-scoped lane caps ONE run's concurrency so a wide run cannot take
        every slot away from another run."""
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
        await _stop(mgr)

    @pytest.mark.asyncio
    async def test_kill_fanout_cancels_all_children_of_one_run(self) -> None:
        """One call kills EVERY child (running + queued) of one fan-out, leaving
        other runs untouched."""
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
            # B is untouched — cancel_fanout scopes to ONE run (B, queued behind A's two
            # running children, was never cancelled).
            assert b is not None and b.cancelled is False
        await _stop(mgr)

    @pytest.mark.asyncio
    async def test_record_failure_breaker_trips_at_five(self) -> None:
        """Five consecutive child failures in one fan-out trip its breaker, which
        then stops further spawns — the session-level breaker could never
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
        """A success clears the consecutive-failure streak — only CONSECUTIVE
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
        """A user-cancelled child does not count toward the breaker."""
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


# ── run-scoped budget ────────────────────────────────────────────────


class TestRunBudget:
    @pytest.mark.asyncio
    async def test_run_budget_stops_fanout_midflight_with_typed_reason(self) -> None:
        """A fan-out that would exceed the run budget stops mid-flight with a typed
        reason, composing with the SpendMeter run scope."""
        from personalclaw.guardrails.budgets import Budget, reset_meter

        reset_meter()
        # Three children of 1500 tokens each under a 3000-token run ceiling. The first leaves room;
        # the second brings the run to 3000, its ceiling, so the third, waiting behind them, is
        # never started. Only a run scope that adds up its children stops this fan-out.
        sessions = _completing_sessions(cost=0.0)
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,  # serialize so the later children wait in the queue
        )
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch(
                "personalclaw.guardrails.budgets.run_budget_from_config",
                return_value=Budget(max_tokens=3000),
            ),
        ):
            kids = [
                # A fan-out key of its own: no manager another test left behind reaches its total.
                mgr.spawn(
                    f"c{i}", parent_session_key="dashboard:x", parent_run="workflow:run-budget"
                )
                for i in (1, 2, 3)
            ]
            assert all(k is not None for k in kids)
            first, second, third = kids
            assert second.queued and third.queued, "the later children wait behind the first"
            for _ in kids:  # each child that starts, in turn; none starts more than once
                if not mgr._tasks:
                    break
                await asyncio.gather(*list(mgr._tasks.values()), return_exceptions=True)
            assert not mgr._tasks, "a child was still running after every child had its turn"
            await mgr.flush_deliveries()
            # the first two ran to their ends, 1500 tokens each
            for kid in (first, second):
                assert kid.done and not kid.never_ran and not kid.error, (kid.task, kid.error)
            # the third, still queued, is refused on drain with the run scope's typed reason
            assert third.done and third.never_ran, (third.done, third.never_ran, third.error)
            assert third.error.startswith("spawn refused: run budget exceeded"), third.error
            assert "run token budget exceeded (3000/3000)" in third.error, third.error
        reset_meter()

    @pytest.mark.asyncio
    async def test_per_child_cost_on_completion_event(self) -> None:
        """Per-child cost + tokens are surfaced on the subagent_done event,
        consuming the figures captured at EVENT_COMPLETE — not a new ledger."""
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


# ── a fan-out's stop ends with its fan-out ───────────────────────────


def _hanging_sessions() -> MagicMock:
    """The completing sessions, except no answer ever comes: a child stays running until
    something stops it."""
    sessions = _completing_sessions()
    provider = sessions.get_or_create.return_value[0]

    async def _stream(*_a, **_kw):
        await asyncio.Event().wait()  # never set: it runs until it is stopped
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="late")

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    return sessions


BREAKER = "spawn refused: fan-out breaker tripped"


def _sessions_that_say_they_started() -> tuple[MagicMock, asyncio.Event]:
    """The hanging sessions, and an event set once a helper's model call has begun: a helper
    stopped after that point is one stopped while it runs."""
    sessions = _hanging_sessions()
    provider = sessions.get_or_create.return_value[0]
    began = asyncio.Event()

    async def _stream(*_a, **_kw):
        began.set()
        await asyncio.Event().wait()  # never set: it runs until it is stopped
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="late")

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    return sessions, began


def _trip(mgr: SubagentManager, chat: str) -> None:
    """Five failed helpers of *chat* trip its breaker, as
    ``test_record_failure_breaker_trips_at_five`` does. The stop it sets cancels nothing, so
    whatever of *chat* still waits ends in a drain."""
    for i in range(_CIRCUIT_BREAKER_THRESHOLD):
        failed = SubagentInfo(id=f"f{i}", task="t", parent_session_key=chat)
        failed.error = "boom"
        mgr._note_child_outcome(failed)
    assert chat in mgr._fanout_stops


class TestTheStopEndsWithItsFanout:
    """A fan-out's stop refuses what is still on its way into it, and ends once nothing of it is
    running or waiting. One that outlives its fan-out refuses every later helper of the same chat
    (a chat's helpers share the chat's session key) until the gateway restarts."""

    @pytest.mark.asyncio
    async def test_a_chat_whose_last_helper_crossed_its_run_budget_can_start_another(
        self,
    ) -> None:
        """The chat's last running helper crosses Max tokens / run. Its own ending clears the
        drained fan-out's stop, and the budget's cancel, which runs after that, finds nothing to
        stop: it must not leave the stop behind."""
        from personalclaw.guardrails.budgets import Budget, reset_meter

        reset_meter()
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        chat = "dashboard:a-chat-past-its-budget"
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch(
                "personalclaw.guardrails.budgets.run_budget_from_config",
                return_value=Budget(max_tokens=1500),
            ),
        ):
            last = mgr.spawn("the only helper", parent_session_key=chat)
            assert last is not None and not last.queued
            await mgr._tasks[last.id]
            await mgr.flush_deliveries()
            await _settle()
            assert last.done and not last.error, last.error
            following = mgr.spawn("the next helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
        await _stop(mgr)
        reset_meter()

    @pytest.mark.asyncio
    async def test_stop_fanout_on_a_chat_with_nothing_running_leaves_no_stop(self) -> None:
        """Stop fan-out (``POST /api/spawn/cancel-fanout``) on a chat whose helpers have all
        ended stops nothing, and must not leave its "Cancelled by user" behind."""
        from types import SimpleNamespace

        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        from personalclaw.dashboard.handlers.messaging import api_spawn_cancel_fanout

        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        chat = "dashboard:a-chat-at-rest"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            earlier = mgr.spawn("an earlier helper", parent_session_key=chat)
            assert earlier is not None
            await mgr._tasks[earlier.id]
            await mgr.flush_deliveries()
            app = web.Application()
            app["state"] = SimpleNamespace(subagents=mgr)
            request = make_mocked_request("POST", "/api/spawn/cancel-fanout", app=app)

            async def _body() -> dict:
                return {"parent_session": chat}

            request.json = _body  # type: ignore[method-assign]
            answer = await api_spawn_cancel_fanout(request)
            assert answer.status == 200 and b'"cancelled": 0' in answer.body
            following = mgr.spawn("the next helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
        await _stop(mgr)

    @pytest.mark.asyncio
    async def test_a_stopped_turn_whose_helpers_were_all_waiting_leaves_no_stop(self) -> None:
        """A stopped turn whose helpers were all still waiting for a slot marks their fan-out
        stopped and ends each one where it waits. Once the last of them has ended, nothing of
        the fan-out is left, so its stop must be gone, or the chat's next turn can start no
        helper."""
        mgr = SubagentManager(
            sessions=_hanging_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        chat = "dashboard:a-chat-whose-helpers-wait"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            busy = mgr.spawn("another chat's helper", parent_session_key="dashboard:a-busy-chat")
            assert busy is not None and not busy.queued, "it takes the one slot"
            waiting = [mgr.spawn(f"helper {i}", parent_session_key=chat) for i in (1, 2)]
            assert all(w is not None and w.queued for w in waiting)
            stopped = await mgr.stop_agents([w.id for w in waiting], because=TURN_STOPPED)
            assert stopped == 2
            assert all(w.done and w.never_ran for w in waiting)
            following = mgr.spawn("the next turn's helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error
            assert following.queued, "it waits behind the other chat's helper"
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
        await _stop(mgr)

    @pytest.mark.asyncio
    async def test_a_stopped_helper_hands_its_slot_to_the_one_waiting(self) -> None:
        """A helper stopped before its run has begun, here by its Stop, frees its slot, and the
        helper waiting for that slot starts in it, as it does when a helper ends on its own: a
        spawn that comes later waits behind it. One stopped inside its model call is
        ``test_a_helper_stopped_inside_its_model_call_hands_its_slot_on``'s."""
        mgr = SubagentManager(
            sessions=_hanging_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            running = mgr.spawn("the running helper", parent_session_key="dashboard:one-chat")
            waiting = mgr.spawn("the waiting helper", parent_session_key="dashboard:other-chat")
            assert running is not None and not running.queued
            assert waiting is not None and waiting.queued
            assert await mgr.cancel(running.id) is True
            assert running.done and running.reaped
            assert not waiting.queued and waiting.id in mgr._tasks, "it starts in the freed slot"
            later = mgr.spawn("a later helper", parent_session_key="dashboard:a-third-chat")
            assert later is not None and later.queued, "it waits behind the one that waited"
        await _stop(mgr)

    @pytest.mark.asyncio
    async def test_the_run_budget_stops_a_sibling_still_working_and_then_lets_go(self) -> None:
        """When a child crosses Max tokens / run while a sibling still works, the budget's cancel
        ends that sibling with the typed reason, and once the sibling's reap has drained the
        fan-out, the chat can start helpers again. Which helper finishes is chosen by its session
        key: each builds its first prompt on a worker thread, so the order in which they reach
        their model is a race."""
        from personalclaw.guardrails.budgets import Budget, reset_meter

        reset_meter()
        sessions = _completing_sessions()
        finishing = sessions.get_or_create.return_value
        hanging = _hanging_sessions().get_or_create.return_value
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=2,
        )
        chat = "dashboard:a-chat-with-two-helpers"
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch(
                "personalclaw.guardrails.budgets.run_budget_from_config",
                return_value=Budget(max_tokens=1500),
            ),
        ):
            first = mgr.spawn("the one that finishes", parent_session_key=chat)
            sibling = mgr.spawn("the one still working", parent_session_key=chat)
            assert first is not None and sibling is not None
            assert not first.queued and not sibling.queued
            sessions.get_or_create = AsyncMock(
                side_effect=lambda key, **_kw: hanging if key.endswith(sibling.id) else finishing
            )
            await mgr._tasks[first.id]
            await mgr.flush_deliveries()
            await _settle()
            assert first.done and not first.error, first.error
            assert sibling.done and sibling.reaped, (sibling.done, sibling.error)
            assert sibling.error.startswith("run budget exceeded ("), sibling.error
            following = mgr.spawn("the next helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error
        await _stop(mgr)
        reset_meter()

    @pytest.mark.parametrize("other_ends", ["on_its_own", "stopped"])
    @pytest.mark.asyncio
    async def test_a_waiting_helper_refused_in_another_chats_drain_ends_its_chats_stop(
        self, other_ends: str
    ) -> None:
        """Chat Y's last helper waits behind chat X's, and Y's breaker trips. X's helper ends, on
        its own or stopped, and the drain that follows refuses Y's helper with the breaker's
        reason. Nothing of Y is left, so Y's next helper must not be refused: by then the chat's
        Stop fan-out is gone too, and only a restart would free it."""
        sessions = _completing_sessions() if other_ends == "on_its_own" else _hanging_sessions()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        y = "dashboard:chat-y"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            busy = mgr.spawn("chat x's helper", parent_session_key="dashboard:chat-x")
            last = mgr.spawn("chat y's last helper", parent_session_key=y)
            assert busy is not None and not busy.queued
            assert last is not None and last.queued
            _trip(mgr, y)
            if other_ends == "on_its_own":
                await mgr._tasks[busy.id]
            else:
                assert await mgr.cancel(busy.id) is True
            await mgr.flush_deliveries()
            assert last.done and last.never_ran and last.error.startswith(BREAKER), last.error
            following = mgr.spawn("chat y's next helper", parent_session_key=y)
            assert following is not None and not following.done, following.error
            assert y not in mgr._fanout_stops, mgr._fanout_stops.get(y)

    @pytest.mark.parametrize("state", ["breaker", "run_total"])
    @pytest.mark.asyncio
    async def test_a_start_refused_as_its_fanouts_last_helper_ends_the_fanouts_state(
        self, state: str
    ) -> None:
        """A chat's last helper is still asking to start when its fan-out's state is set: its
        breaker trips, or, with no stop at all, a finished sibling's tokens sit on the chat's run
        total. The start is denied. Nothing of the chat runs or waits, so its next helper starts
        clean: not refused, and not part-spent."""
        from personalclaw.approval_grants import YOU, ToolDecision
        from personalclaw.guardrails.budgets import Budget, get_meter, reset_meter

        reset_meter()
        answer = asyncio.Event()

        async def owner(_event, _key=""):
            await answer.wait()
            return ToolDecision(False, "rejected", YOU)

        sessions = _completing_sessions()
        sessions.get_approval_policy = MagicMock(return_value="")
        ctx = _mock_ctx_builder()
        ctx.hooks.auto_approve_subagent_spawn = False
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=ctx,
            on_done=AsyncMock(),
            is_yolo=lambda: False,
            on_spawn_approval=owner,
        )
        chat = "dashboard:a-chat-whose-start-is-denied"
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch(
                "personalclaw.guardrails.budgets.run_budget_from_config",
                return_value=Budget(max_tokens=100_000),
            ),
        ):
            asking = mgr.spawn("the helper asking to start", parent_session_key=chat)
            assert asking is not None and not asking.done and not asking.queued
            start = mgr._tasks[asking.id]
            if state == "breaker":
                _trip(mgr, chat)
            else:
                get_meter().charge_run(chat, 1500, 0.0)
                assert get_meter().run_totals(chat).tokens == 1500
            answer.set()
            await start
            assert asking.done and asking.never_ran and asking.declined, asking.error
            assert mgr._lane_count(chat) == 0
            if state == "breaker":
                following = mgr.spawn("the next helper", parent_session_key=chat)
                assert following is not None and not following.done, following.error
                assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
            else:
                assert get_meter().run_totals(chat).tokens == 0, "the next fan-out starts at 0"
        reset_meter()

    @pytest.mark.asyncio
    async def test_a_next_start_that_fails_in_the_reap_leaves_the_reap_whole(self) -> None:
        """A running helper is stopped, and the start of the helper waiting for its slot raises in
        the reap (the audit log refusing its row, as a full disk makes it). The stopped helper
        still ends reaped with its stop's reason, and the Stop says it stopped it; its slot is
        given back once, not twice, and the rest of its reap runs: its Activity row hears that it
        ended."""
        sessions, began = _sessions_that_say_they_started()
        ended: list[str] = []

        async def on_event(etype: str, info: SubagentInfo, _extra: dict) -> None:
            if etype == "subagent_done":
                ended.append(info.id)

        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            on_event=on_event,
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel") as sel:
            running = mgr.spawn("the running helper", parent_session_key="dashboard:one-chat")
            waiting = mgr.spawn("the waiting helper", parent_session_key="dashboard:other-chat")
            assert running is not None and waiting is not None and waiting.queued
            await asyncio.wait_for(began.wait(), 5)

            def full(**kw):
                if kw.get("outcome") == "auto_approved_spawn":
                    raise OSError(28, "No space left on device")

            sel.return_value.log_tool_invocation.side_effect = full
            assert await mgr.cancel(running.id, reason="Cancelled by user") is True
            await _settle(mgr)
            assert running.reaped and running.error == "Cancelled by user", (
                running.reaped,
                running.error,
            )
            assert mgr._running_count == (0 if waiting.done else 1), (
                mgr._running_count,
                waiting.done,
            )
            assert running.id in ended, "the reap's tail ran to its end"

    @pytest.mark.asyncio
    async def test_a_next_start_that_fails_after_a_denied_start_leaves_that_ending_whole(
        self,
    ) -> None:
        """A chat's last helper asks to start under its tripped breaker, and another chat's
        helper, started by its own grant, waits for the slot. The start is denied, and the waiting
        helper's start raises in the drain that follows (the audit log refusing its row). The
        refusal still ends its chat's stop and says the denied helper ended, so the chat's next
        helper is not refused."""
        from personalclaw.approval_grants import YOU, ToolDecision

        answer = asyncio.Event()

        async def owner(_event, _key=""):
            await answer.wait()
            return ToolDecision(False, "rejected", YOU)

        ended: list[str] = []

        async def on_event(etype: str, info: SubagentInfo, _extra: dict) -> None:
            if etype == "subagent_done":
                ended.append(info.id)

        sessions = _hanging_sessions()
        sessions.get_approval_policy = MagicMock(return_value="")
        ctx = _mock_ctx_builder()
        ctx.hooks.auto_approve_subagent_spawn = False
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=ctx,
            on_done=AsyncMock(),
            on_event=on_event,
            is_yolo=lambda: False,
            on_spawn_approval=owner,
            max_concurrent=1,
        )
        chat = "dashboard:a-chat-whose-start-is-denied"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel") as sel:
            asking = mgr.spawn("the helper asking to start", parent_session_key=chat)
            granted = mgr.spawn(
                "another chat's helper", parent_session_key="dashboard:b", approval_mode="auto"
            )
            assert asking is not None and not asking.queued
            assert granted is not None and granted.queued
            start = mgr._tasks[asking.id]
            _trip(mgr, chat)

            def full(**kw):
                if kw.get("metadata", {}).get("subagent_id") == granted.id:
                    raise OSError(28, "No space left on device")

            sel.return_value.log_tool_invocation.side_effect = full
            answer.set()
            await asyncio.wait({start})
            assert start.exception() is None, start.exception()
            assert asking.done and asking.declined, asking.error
            assert not granted.queued and not granted.never_ran, "the waiting helper started"
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
            assert asking.id in ended, "the denied helper's Activity row heard it ended"
            following = mgr.spawn("the chat's next helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error

    @pytest.mark.asyncio
    async def test_a_next_start_that_fails_after_a_helper_ends_on_its_own_leaves_that_ending_whole(
        self,
    ) -> None:
        """A chat's last helper ends on its own under its tripped breaker, and the start of
        another chat's helper waiting for the slot raises in the drain its ending makes (the
        audit log refusing its row). The ending still runs to its end: the chat's stop ends, the
        helper's session is reset and its report reaches its chat."""
        delivered: list[str] = []

        async def on_done(batch: list[SubagentInfo]) -> None:
            delivered.extend(info.id for info in batch)

        sessions = _completing_sessions()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=on_done,
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        chat = "dashboard:a-chat-whose-last-helper-ends"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel") as sel:
            last = mgr.spawn("the chat's last helper", parent_session_key=chat)
            waiting = mgr.spawn("another chat's helper", parent_session_key="dashboard:b")
            assert last is not None and not last.queued and waiting is not None and waiting.queued
            ending = mgr._tasks[last.id]
            _trip(mgr, chat)

            def full(**kw):
                if kw.get("metadata", {}).get("subagent_id") == waiting.id:
                    raise OSError(28, "No space left on device")

            sel.return_value.log_tool_invocation.side_effect = full
            await asyncio.wait({ending})
            assert ending.exception() is None, ending.exception()
            await mgr.flush_deliveries()
            assert last.done and not last.error, last.error
            assert not waiting.queued and not waiting.never_ran, "the waiting helper started"
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
            assert any(
                c.args and c.args[0].endswith(last.id) for c in sessions.reset.await_args_list
            ), "its session was reset"
            assert last.id in delivered, "its report reached its chat"
            following = mgr.spawn("the chat's next helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error

    @pytest.mark.asyncio
    async def test_the_run_budgets_late_cancel_spares_a_helper_of_the_next_fanout(self) -> None:
        """The run budget's cancel runs after the child that crossed the ceiling has ended. When
        that child was its fan-out's last, the fan-out has drained and its stop has ended, and a
        helper the same chat starts before the cancel runs belongs to a new fan-out: the late
        cancel must spare it, not end it with the old fan-out's reason."""
        from personalclaw.guardrails.budgets import Budget, reset_meter

        reset_meter()
        mgr = SubagentManager(
            sessions=_hanging_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        chat = "dashboard:a-chat-between-two-fanouts"
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch(
                "personalclaw.guardrails.budgets.run_budget_from_config",
                return_value=Budget(max_tokens=1500),
            ),
        ):
            # The last child of the first fan-out ends: its run's total crosses the ceiling, which
            # schedules the late cancel, and its ending then drains the fan-out, as `_run` does.
            crossed = SubagentInfo(id="crossed", task="t", parent_session_key=chat)
            crossed.input_tokens, crossed.output_tokens = 1000, 500
            mgr._charge_child_and_check_budget(crossed)
            assert chat in mgr._fanout_stops
            mgr._maybe_clear_fanout(chat)
            # Before the late cancel has run, the chat starts its next helper.
            fresh = mgr.spawn("a helper of the next fan-out", parent_session_key=chat)
            assert fresh is not None and not fresh.done, fresh.error
            await _settle(mgr)
            assert not fresh.done and not fresh.cancelled, fresh.error
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
        reset_meter()

    @pytest.mark.asyncio
    async def test_stop_fanout_on_a_chat_whose_helpers_all_wait_leaves_no_stop(self) -> None:
        """Stop fan-out on a chat whose helpers are all still waiting for a slot (the button
        counts waiting helpers) ends each where it waits, and once the last has ended the chat's
        next helper is not refused."""
        mgr = SubagentManager(
            sessions=_hanging_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        chat = "dashboard:a-chat-whose-helpers-wait"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            busy = mgr.spawn("another chat's helper", parent_session_key="dashboard:a-busy-chat")
            assert busy is not None and not busy.queued, "it takes the one slot"
            waiting = [mgr.spawn(f"helper {i}", parent_session_key=chat) for i in (1, 2)]
            assert all(w is not None and w.queued for w in waiting)
            assert await mgr.cancel_fanout(chat, reason="Cancelled by user", by_you=True) == 2
            assert all(w.done and w.never_ran for w in waiting)
            following = mgr.spawn("the next helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error
            assert following.queued, "it waits behind the other chat's helper"
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)

    @pytest.mark.asyncio
    async def test_stopping_a_stopped_fanouts_last_waiting_helper_ends_its_stop(self) -> None:
        """A helper card's Stop (``cancel(id, by_you=True)``) on a stopped chat's last waiting
        helper ends the chat's stop: nothing else of the chat is left to end it."""
        mgr = SubagentManager(
            sessions=_hanging_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        y = "dashboard:chat-y"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            busy = mgr.spawn("chat x's helper", parent_session_key="dashboard:chat-x")
            last = mgr.spawn("chat y's last helper", parent_session_key=y)
            assert busy is not None and last is not None and last.queued
            _trip(mgr, y)
            assert await mgr.cancel(last.id, by_you=True) is True
            following = mgr.spawn("chat y's next helper", parent_session_key=y)
            assert following is not None and not following.done, following.error
            assert following.queued, "it waits behind chat x's helper"
            assert y not in mgr._fanout_stops, mgr._fanout_stops.get(y)

    @pytest.mark.asyncio
    async def test_a_stop_holds_while_a_helper_of_its_fanout_still_runs(self) -> None:
        """Chat Y's helper r runs, q waits, and Y's breaker trips. Stopping q leaves r running,
        so Y's stop stands and its next spawn is refused. Stopping r drains Y and ends it."""
        sessions, began = _sessions_that_say_they_started()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        y = "dashboard:chat-y"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            r = mgr.spawn("y's running helper", parent_session_key=y)
            q = mgr.spawn("y's waiting helper", parent_session_key=y)
            assert r is not None and not r.queued and q is not None and q.queued
            await asyncio.wait_for(began.wait(), 5)
            _trip(mgr, y)
            assert await mgr.cancel(q.id) is True
            refused = mgr.spawn("refused while r runs", parent_session_key=y)
            assert refused is not None and refused.done and refused.error.startswith(BREAKER)
            assert await mgr.cancel(r.id) is True
            following = mgr.spawn("y's next helper", parent_session_key=y)
            assert following is not None and not following.done, following.error

    @pytest.mark.asyncio
    async def test_a_stop_holds_while_a_helper_of_its_fanout_still_waits(self) -> None:
        """Chat Y's q1 and q2 wait behind chat X's helper, and Y's breaker trips. Stopping q2
        leaves q1 waiting, so Y's stop stands and Y's next spawn is refused."""
        mgr = SubagentManager(
            sessions=_hanging_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        y = "dashboard:chat-y"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            busy = mgr.spawn("chat x's helper", parent_session_key="dashboard:chat-x")
            q1 = mgr.spawn("y's first waiting helper", parent_session_key=y)
            q2 = mgr.spawn("y's second waiting helper", parent_session_key=y)
            assert busy is not None and q1 is not None and q2 is not None
            assert q1.queued and q2.queued
            _trip(mgr, y)
            assert await mgr.cancel(q2.id) is True
            refused = mgr.spawn("refused while q1 waits", parent_session_key=y)
            assert refused is not None and refused.done and refused.error.startswith(BREAKER)

    @pytest.mark.parametrize("stopped_by", ["cancel", "reaper"])
    @pytest.mark.asyncio
    async def test_a_helper_stopped_inside_its_model_call_hands_its_slot_on(
        self, stopped_by: str
    ) -> None:
        """A helper stopped while it runs, inside its model call, by its Stop or by the reaper's
        deadline kill, frees its slot to the helper waiting for one."""
        sessions, began = _sessions_that_say_they_started()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            running = mgr.spawn("the running helper", parent_session_key="dashboard:one-chat")
            waiting = mgr.spawn("the waiting helper", parent_session_key="dashboard:other-chat")
            assert running is not None and waiting is not None and waiting.queued
            await asyncio.wait_for(began.wait(), 5)
            if stopped_by == "cancel":
                assert await mgr.cancel(running.id) is True
            else:
                await mgr._force_reap(running.id, running, 0.0)
            assert running.reaped and not running.never_ran
            assert not waiting.queued and waiting.id in mgr._tasks, "it starts in the freed slot"

    @pytest.mark.asyncio
    async def test_a_stopped_helper_whose_fanout_is_stopped_refuses_the_one_waiting(
        self,
    ) -> None:
        """Chat Y's helper r runs and q waits under Y's tripped breaker. Stopping r: the drain
        refuses q with the stop's reason, so q never starts, and then Y's stop ends."""
        sessions, began = _sessions_that_say_they_started()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
            max_concurrent=1,
        )
        y = "dashboard:chat-y"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            r = mgr.spawn("y's running helper", parent_session_key=y)
            q = mgr.spawn("y's waiting helper", parent_session_key=y)
            assert r is not None and q is not None and q.queued
            await asyncio.wait_for(began.wait(), 5)
            _trip(mgr, y)
            assert await mgr.cancel(r.id) is True
            assert q.done and q.never_ran and q.error.startswith(BREAKER), q.error
            assert q.id not in mgr._tasks
            following = mgr.spawn("y's next helper", parent_session_key=y)
            assert following is not None and not following.done, following.error

    @pytest.mark.asyncio
    async def test_stop_fanout_on_one_chat_leaves_another_chats_stop(self) -> None:
        """Stop fan-out on a chat with nothing running ends that chat's stop only: chat X, whose
        helper still runs under its tripped breaker, stays stopped."""
        mgr = SubagentManager(
            sessions=_hanging_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        x = "dashboard:chat-x"
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            busy = mgr.spawn("chat x's helper", parent_session_key=x)
            assert busy is not None and not busy.queued
            _trip(mgr, x)
            assert await mgr.cancel_fanout("dashboard:chat-y", reason="Cancelled by user") == 0
            assert x in mgr._fanout_stops
            refused = mgr.spawn("x's next helper", parent_session_key=x)
            assert refused is not None and refused.done and refused.error.startswith(BREAKER)


# ── a workflow run's key lasts the run ───────────────────────────────────────────────────────


def _failing_sessions() -> MagicMock:
    """The completing sessions, except the model call fails: each helper ends with an error."""
    sessions = _completing_sessions()
    provider = sessions.get_or_create.return_value[0]

    async def _stream(*_a, **_kw):
        raise RuntimeError("the model call failed")
        yield  # an async generator, as the provider's stream is

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    return sessions


STEPS_CHAT = "dashboard:chat-that-started-the-run"


async def _one_step(mgr: SubagentManager, run_key: str, n: int) -> SubagentInfo:
    """Agent step *n* of the workflow run whose fan-out key is *run_key*, run to its end before
    anything else starts: a run's steps one after another, none of them side by side. Started from
    a chat, as Run workflow in one does, so the step reads that chat's memory setting."""
    step = mgr.spawn(f"step {n}", parent_session_key=STEPS_CHAT, parent_run=run_key)
    assert step is not None and not step.done, step.error if step else None
    await asyncio.gather(*list(mgr._tasks.values()), return_exceptions=True)
    await mgr.flush_deliveries()
    await _settle()
    return step


class TestARunsKeyLastsTheRun:
    """A workflow run's agent steps share one fan-out key, ``workflow:<run_id>``
    (``workflows/engine.py``). Its breaker and its Max tokens / run count the whole run, steps run
    one after another as well as side by side, and end with the run (``started_work.end_run``)."""

    @pytest.mark.asyncio
    async def test_a_step_after_the_one_that_crossed_the_ceiling_is_refused(self) -> None:
        from personalclaw.guardrails.budgets import Budget, reset_meter

        reset_meter()
        run_key = "workflow:run-two-steps"
        mgr = SubagentManager(
            sessions=_completing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch(
                "personalclaw.guardrails.budgets.run_budget_from_config",
                return_value=Budget(max_tokens=2000),
            ),
        ):
            first = await _one_step(mgr, run_key, 1)  # 1500 of the run's 2000
            assert first.done and not first.error, first.error
            second = await _one_step(mgr, run_key, 2)  # 3000: it crosses the ceiling as it ends
            assert second.done and not second.error, second.error
            third = mgr.spawn("step 3", parent_session_key=STEPS_CHAT, parent_run=run_key)
            assert third is not None and third.done and third.never_ran, third
            assert third.error.startswith("spawn refused: run budget exceeded"), third.error
            assert "3000/2000" in third.error, third.error
        reset_meter()

    @pytest.mark.asyncio
    async def test_failed_steps_one_after_another_trip_the_runs_breaker(self) -> None:
        run_key = "workflow:run-of-failing-steps"
        mgr = SubagentManager(
            sessions=_failing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            for n in range(_CIRCUIT_BREAKER_THRESHOLD):
                step = await _one_step(mgr, run_key, n)
                assert step.done and step.error and not step.cancelled, step.error
            refused = mgr.spawn("the next step", parent_session_key=STEPS_CHAT, parent_run=run_key)
            assert refused is not None and refused.done and refused.never_ran
            assert refused.error.startswith(BREAKER), refused.error
            # A chat's helpers end their fan-out's streak when they drain, as before.
            chat = "dashboard:chat-of-failing-helpers"
            for n in range(_CIRCUIT_BREAKER_THRESHOLD):
                helper = mgr.spawn(f"helper {n}", parent_session_key=chat)
                assert helper is not None and not helper.done, helper.error
                await asyncio.gather(*list(mgr._tasks.values()), return_exceptions=True)
                await mgr.flush_deliveries()
                await _settle()
            assert chat not in mgr._fanout_stops and chat not in mgr._fanout_failures

    @pytest.mark.asyncio
    async def test_the_runs_end_ends_what_its_steps_shared(self) -> None:
        """``started_work.end_run``, which a run's terminal write and its deletion call, ends the
        breaker streak, stop and run total of the run's key, even when ending what the run started
        fails, so a run reusing the id (a resumed one) starts clean."""
        from types import SimpleNamespace

        from personalclaw import started_work
        from personalclaw.guardrails.budgets import get_meter, reset_meter

        reset_meter()
        run_key = "workflow:run-that-ends"
        mgr = SubagentManager(
            sessions=_failing_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            for n in range(_CIRCUIT_BREAKER_THRESHOLD):
                await _one_step(mgr, run_key, n)
            get_meter().charge_run(run_key, 1200, 0.0)
            assert run_key in mgr._fanout_stops, "the positive control: the run's breaker tripped"
            run = SimpleNamespace(id="run-that-ends", title="", workflow_name="digest")
            with patch.object(started_work, "end_started", AsyncMock(side_effect=OSError("disk"))):
                assert await started_work.end_run(run, ending="failed", subagents=mgr) == 0
            assert run_key not in mgr._fanout_stops and run_key not in mgr._fanout_failures
            assert get_meter().run_totals(run_key).tokens == 0
            assert run_key not in mgr._run_fanouts and run_key not in mgr._run_fanouts_ended
            again = mgr.spawn(
                "a resumed run's step", parent_session_key=STEPS_CHAT, parent_run=run_key
            )
            assert again is not None and not again.done, again.error
            await asyncio.gather(*list(mgr._tasks.values()), return_exceptions=True)
            await mgr.flush_deliveries()
        reset_meter()

    @pytest.mark.asyncio
    async def test_a_run_that_ends_while_its_step_runs_ends_its_key_with_that_step(self) -> None:
        run_key = "workflow:run-ends-mid-step"
        sessions, began = _sessions_that_say_they_started()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
            is_yolo=lambda: True,
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            step = mgr.spawn("step", parent_session_key=STEPS_CHAT, parent_run=run_key)
            assert step is not None and not step.queued
            await asyncio.wait_for(began.wait(), 5)
            mgr._fanout_failures[run_key] = 3  # a streak the run's earlier steps left
            mgr.end_run_fanout(run_key)
            assert mgr._fanout_failures.get(run_key) == 3, "kept while a step of the run still runs"
            assert await mgr.cancel(step.id) is True
            await _settle()
            assert run_key not in mgr._fanout_failures
            assert run_key not in mgr._run_fanouts and run_key not in mgr._run_fanouts_ended
