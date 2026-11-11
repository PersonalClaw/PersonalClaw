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
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
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


async def _settle() -> None:
    """Let every task this test's loop still holds run to its end: the run budget's cancel of
    its fan-out is one, started with nothing awaiting it, and so is a reap's tail."""
    me = asyncio.current_task()
    rest = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
    if rest:
        await asyncio.wait(rest, timeout=_HANG_SECS)


@pytest_asyncio.fixture(autouse=True)
async def _every_manager_is_stopped(monkeypatch):
    """Stop every manager a test made, even one that failed before its own ``_stop``, then hold
    the test to leaving nothing behind: no helper, delivery or reap of its managers may still be
    pending once they are stopped. A helper left running or waiting here is what reached the next
    test's loop and its spend meter (``_stop``)."""
    made: list[SubagentManager] = []
    build = SubagentManager.__init__

    def _recording(self, *args, **kwargs):
        build(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(SubagentManager, "__init__", _recording)
    yield
    for mgr in made:
        await _stop(mgr)
    await _settle()
    left = [
        f"{type(mgr).__name__}: {name}"
        for mgr in made
        for name, task in [*mgr._tasks.items(), *mgr._delivery_tasks.items()]
        if not task.done()
    ]
    me = asyncio.current_task()
    pending = [repr(t.get_coro()) for t in asyncio.all_tasks() if t is not me and not t.done()]
    assert not left and not pending, f"left behind: {left} {pending}"


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
    """The completing sessions, except each answer waits ``_HANG_SECS`` first: a child stays
    running until something stops it."""
    sessions = _completing_sessions()
    provider = sessions.get_or_create.return_value[0]

    async def _stream(*_a, **_kw):
        await asyncio.sleep(_HANG_SECS)
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
        await asyncio.sleep(_HANG_SECS)
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
        """The chat's last running helper crosses Max Tokens / Run. Its own ending clears the
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
        stopped and ends each one where it waits. A waiting helper's ending must clear that
        stop as a running one's does, or the chat's next turn can start no helper."""
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
            stopped = await mgr.stop_agents([w.id for w in waiting], because="the turn was stopped")
            assert stopped == 2
            assert all(w.done and w.never_ran for w in waiting)
            following = mgr.spawn("the next turn's helper", parent_session_key=chat)
            assert following is not None and not following.done, following.error
            assert following.queued, "it waits behind the other chat's helper"
            assert chat not in mgr._fanout_stops, mgr._fanout_stops.get(chat)
        await _stop(mgr)

    @pytest.mark.asyncio
    async def test_a_stopped_helper_hands_its_slot_to_the_one_waiting(self) -> None:
        """A helper stopped while it runs (Stop, Stop fan-out, the run budget's cancel, the
        reaper) frees its slot, and the helper waiting for that slot starts in it, as it does
        when a helper ends on its own: a spawn that comes later waits behind it."""
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
        """When a child crosses Max Tokens / Run while a sibling still works, the budget's cancel
        ends that sibling with the typed reason, and once the sibling's reap has drained the
        fan-out, the chat can start helpers again."""
        from personalclaw.guardrails.budgets import Budget, reset_meter

        reset_meter()
        sessions = _completing_sessions()
        provider = sessions.get_or_create.return_value[0]
        answers = iter(("complete", "hang"))
        complete = provider.stream.side_effect

        async def _hang(*_a, **_kw):
            await asyncio.sleep(_HANG_SECS)
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="late")

        provider.stream = MagicMock(
            side_effect=lambda *a, **kw: (
                complete(*a, **kw) if next(answers, "hang") == "complete" else _hang()
            )
        )
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
        still ends reaped with its stop's reason, and its slot is given back once, not twice."""
        sessions, began = _sessions_that_say_they_started()
        mgr = SubagentManager(
            sessions=sessions,
            ctx_builder=_mock_ctx_builder(),
            on_done=AsyncMock(),
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
            try:
                await mgr.cancel(running.id, reason="Cancelled by user")
            except OSError:
                pass
            await _settle()
            assert running.reaped and running.error == "Cancelled by user", (
                running.reaped,
                running.error,
            )
            assert mgr._running_count == (0 if waiting.done else 1), (
                mgr._running_count,
                waiting.done,
            )
