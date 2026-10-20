"""A helper's report starts a turn in its chat only for work that ran, and that she still wants.

Measured on a native install: she declined the start of a helper her chat's agent asked for. Once
the asking turn ended, the helper's report ("spawn declined, so it never started") was handed to the
chat as a turn of its own, and the agent went to work again by itself: "I'll restart one strictly
read-only review". Nobody had asked for anything. A report starts a turn so the agent can carry on
work it asked for and that ran; a helper that never ran has no work to carry on, and the turn its
ending started told the agent only that its work was not done, which it took up again.

Now a helper that never ran (her Deny of its start, a start nobody allowed in time, a batch none of
whose tasks started) and a helper she stopped herself start no turn. Its chat's agent is told how it
ended with the next turn that runs in the chat, ahead of her message, or at once by a ``wait`` under
way, and the chat's Subagents list shows it. A helper that ran and ended on its own, finished or
failed, still reports as a turn of its own.

Driven through the real dashboard state and chat runner, the gateway's own delivery
(``_init_subagents``) and a real ``SubagentManager`` whose start asks her; the chat's runtime is a
fake that keeps what each turn is handed.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from personalclaw import approval_grants
from personalclaw.approval_grants import ToolDecision
from personalclaw.config.loader import AppConfig
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

CHAT = "chat-review"
KEY = f"dashboard:{CHAT}"
TASK = "Review the last commit and list its three riskiest changes"
#: What she types after she said no.
HER_NEXT = "Then just tell me what changed in the parser."

DECLINED = ToolDecision(False, "rejected", approval_grants.YOU)
NOBODY_IN_TIME = ToolDecision(False, "expired", approval_grants.NOBODY)
ALLOWED = ToolDecision(True, "approved", approval_grants.YOU)

#: How long a delivery may take to come back while the asking turn still runs. It used to wait for
#: that turn to end, so the bound is what a red reads as.
PROMPTLY = 3.0


def _helper_sessions(release: asyncio.Event, *, fails: bool = False) -> MagicMock:
    """The sessions a helper runs on: its start asks (nothing trusts it), and once allowed it says
    what it found when *release* is set, or (*fails*) its runtime cannot start."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_approval_policy = MagicMock(return_value="")
    provider = AsyncMock()
    provider.context_usage_pct = lambda: 0.0

    async def _stream(*_a: Any, **_kw: Any):
        await release.wait()
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="The parser change drops the BOM check.")
        yield LLMEvent(kind=EVENT_COMPLETE)

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    if fails:
        sessions.get_or_create = AsyncMock(side_effect=RuntimeError("the model answered 503"))
    else:
        sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    return sessions


def _helper_ctx() -> MagicMock:
    """A context builder whose hooks start no helper without asking."""
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = False
    return ctx


class _World:
    """One chat on a gateway, whose agent starts a helper that asks her before it starts."""

    def __init__(self, tmp_path: Any, *, fails: bool = False) -> None:
        from chat_test_helpers import _make_state
        from test_what_a_model_is_handed_is_masked import _builder

        from personalclaw.gateway import GatewayOrchestrator
        from personalclaw.subagent import SubagentManager

        self.frames: list[tuple[str, dict]] = []
        self.handed: list[str] = []
        state = _make_state(tmp_path)
        state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        state.push_sessions_update = MagicMock()
        state.context_builder = _builder(tmp_path)
        state.consolidator = None
        state._hook_store = MagicMock()
        state._hook_store.fire_for_ids = AsyncMock(return_value=[])
        client = AsyncMock()
        client.context_usage_pct = MagicMock(return_value=10.0)

        async def _turn(message: str, *_a: Any, **_kw: Any):
            self.handed.append(str(message))
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Noted.")
            yield LLMEvent(kind=EVENT_COMPLETE)

        client.stream = _turn
        client.stream_command = _turn
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
        self.state = state
        self.chat = state.get_or_create_session(CHAT)
        self.chat.title = "Review the parser"

        cfg = AppConfig()
        with patch.object(cfg, "load_credentials", return_value={}):
            orch = GatewayOrchestrator(cfg)
        orch.sessions = MagicMock()
        orch.sessions.get_or_create = AsyncMock(side_effect=AssertionError("a job's turn"))
        orch.sessions.has_session = MagicMock(return_value=False)
        orch.sessions.reset = AsyncMock()
        orch.ctx_builder = MagicMock()
        orch.dashboard_state = state
        with (
            patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
            patch("personalclaw.gateway.SubagentManager") as stand_in,
        ):
            stand_in.return_value = MagicMock(
                running=[], running_agents_for=MagicMock(return_value=[]), get=MagicMock()
            )
            stand_in.return_value.get.return_value = None
            orch._init_subagents()
        self.orch = orch
        #: The gateway's own relay of a helper's events to its chat (`subagent_spawn`, `_done`...).
        relay = stand_in.call_args.kwargs["on_event"]

        self.release = asyncio.Event()
        self._answers: asyncio.Queue[ToolDecision] = asyncio.Queue()
        #: What the helper's start set going: its ask, then its run once allowed.
        self.started: asyncio.Task[Any] | None = None

        async def _ask(event: Any, parent_key: str = "") -> ToolDecision:
            return await self._answers.get()

        self.manager = SubagentManager(
            sessions=_helper_sessions(self.release, fails=fails),
            ctx_builder=_helper_ctx(),
            on_done=orch._announce_subagents,
            on_spawn_approval=_ask,
            on_event=relay,
            delivery_coalesce_secs=0,
        )
        state.subagents = self.manager

    def spawn(self, task: str = TASK, *, parent: str = KEY) -> Any:
        info = self.manager.spawn(task, parent_session_key=parent)
        assert info is not None and not info.done, info
        self.started = self.manager._tasks[info.id]
        return info

    async def answer(self, decision: ToolDecision) -> None:
        """Her answer to the helper's start, and everything it sets going."""
        await self._answers.put(decision)
        await self.settle()

    async def answered_while_its_turn_runs(self, decision: ToolDecision) -> None:
        """Her answer, given while the chat's own turn still runs: what it sets going comes back
        without waiting for that turn."""
        await self._answers.put(decision)
        assert self.started is not None
        await asyncio.wait_for(asyncio.shield(self.started), timeout=PROMPTLY)
        for _ in range(20):
            await asyncio.sleep(0)

    async def until_running(self, info: Any) -> None:
        deadline = time.monotonic() + 10
        while not any(
            kind == "subagent_spawn" and d.get("id") == info.id for kind, d in self.frames
        ):
            assert time.monotonic() < deadline, "the helper never started"
            await asyncio.sleep(0.01)

    async def settle(self) -> None:
        for _ in range(3):
            await asyncio.sleep(0)
            if self.started is not None and not self.started.done():
                await asyncio.wait_for(asyncio.shield(self.started), timeout=10)
            await self.manager.flush_deliveries()
            while running := [t for t in self.state._background_tasks if not t.done()]:
                await asyncio.wait_for(asyncio.gather(*running, return_exceptions=True), timeout=10)
            for _ in range(20):
                await asyncio.sleep(0)

    async def her_turn(self, message: str = HER_NEXT) -> str:
        """She sends *message*; what the chat's agent is told ahead of it."""
        from personalclaw.dashboard.chat import run_chat

        before = len(self.handed)
        await run_chat(self.state, self.chat, message)
        assert len(self.handed) == before + 1, "her message reached the agent once"
        assert self.handed[-1].count(message) == 1, "her message goes as she typed it"
        return self.handed[-1].partition(message)[0]

    def check_in(self) -> web.Request:
        """The check-in a ``wait`` under way in the chat makes, every few seconds."""
        request = MagicMock(spec=web.Request)
        request.headers = {"X-Internal-Secret": "the-gateways-own", "X-Session-Key": KEY}
        request.get = {}.get
        request.app = {"state": self.state}
        return request

    def cards(self) -> list[dict]:
        """The helper endings the chat was told of live, as its Subagents list reads them."""
        return [data for kind, data in self.frames if kind == "subagent_done"]


@pytest.fixture
def world(tmp_path):
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        yield _World(tmp_path)


class _Clock:
    """A clock a wait's sleeps move on, so a wait of minutes takes none."""

    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _wait(check_in: dict, seconds: int) -> tuple[str, list[str]]:
    """What a ``wait`` of *seconds* answers when each of its check-ins is answered *check_in*, and
    the gateway calls it made."""
    from personalclaw.mcp_core import _call_tool

    calls: list[str] = []
    clock = _Clock()

    def _post(path: str, body: dict | None = None, **_kw: Any) -> dict:
        calls.append(path)
        return check_in

    with (
        patch("personalclaw.mcp_core._post", side_effect=_post),
        patch("personalclaw.mcp_core._resolve_session_key", return_value=KEY),
        patch("personalclaw.mcp_core._turn_reach", return_value=("persistent", False)),
        patch.object(time, "sleep", clock.sleep),
        patch.object(time, "monotonic", clock.monotonic),
    ):
        said = _call_tool("wait", {"seconds": seconds, "reason": "the review helper"})
    return said, calls


# ── her Deny of its start ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_helper_whose_start_she_declined_starts_no_turn(world):
    """🔴 Red on integration: her Deny was handed to the chat as a turn of its own, and the agent
    set to work again."""
    world.spawn()
    await world.answer(DECLINED)
    assert world.handed == [], "a turn was started with the report of a helper she declined"
    assert not world.chat.running


@pytest.mark.asyncio
async def test_its_agent_is_told_with_her_next_message_and_only_then(world):
    """🔴 Red on integration: the agent heard of it only in the turn its report started."""
    info = world.spawn()
    await world.answer(DECLINED)
    told = await world.her_turn()
    assert info.id in told and "declined" in told and "never started" in told, told
    assert "do not start it again" in told.lower(), told
    assert info.id not in await world.her_turn("And the tokenizer?"), "it is told once"


@pytest.mark.asyncio
async def test_a_decline_while_the_asking_turn_runs_starts_nothing_once_that_turn_ends(world):
    """The agent's own turn still runs when she declines. 🔴 Red on integration: the report waited
    for that turn to end (no answer within the bound), then started the next turn."""
    world.chat.task = asyncio.get_running_loop().create_future()
    world.spawn()
    await world.answered_while_its_turn_runs(DECLINED)
    world.chat.task.set_result(None)
    world.chat.task = None
    await world.settle()
    assert world.handed == [], "the asking turn's end started a turn with the report"


@pytest.mark.asyncio
async def test_a_wait_under_way_is_handed_it_at_once_and_not_told_it_again(world):
    """The asking turn waits (``wait``), checking in with the gateway every few seconds
    (``POST /api/session-keepalive``). 🔴 Red on integration: the check-in handed it nothing, and
    the wait went on to its end."""
    from personalclaw.dashboard.handlers.sessions import api_session_keepalive

    world.chat.task = asyncio.get_running_loop().create_future()
    info = world.spawn()
    await world.answered_while_its_turn_runs(DECLINED)
    endings = json.loads((await api_session_keepalive(world.check_in())).body).get("endings")
    assert endings and info.id in endings[0] and "declined" in endings[0], endings
    assert json.loads((await api_session_keepalive(world.check_in())).body).get("endings") == []
    world.chat.task.set_result(None)
    world.chat.task = None
    await world.settle()
    assert info.id not in await world.her_turn(), "what the wait was handed is not told twice"


def test_a_wait_ends_with_what_its_check_in_hands_it():
    """🔴 Red on integration: the wait read nothing from its check-in and waited out its time."""
    note = "[Subagent completion event]\nAgent `a1b2c3d4` declined\nTask: Review the last commit"
    said, calls = _wait({"ok": True, "endings": [note]}, 600)
    assert said.startswith("Stopped waiting") and note in said, said
    assert calls == ["/api/session-keepalive"], "it ended at its first check-in"


def test_a_wait_with_nothing_handed_waits_out_its_time_keeping_its_session_alive():
    """The control: a check-in that hands nothing leaves the wait waiting, and checking in keeps
    its session from being taken for stuck."""
    said, calls = _wait({"ok": True, "endings": []}, 60)
    assert said.startswith("Waited 60s"), said
    assert calls and set(calls) == {"/api/session-keepalive"}, calls


@pytest.mark.asyncio
async def test_the_chats_subagents_list_shows_the_helper_she_declined(world):
    """The list showed only helpers that started. 🔴 Red on integration: a helper that never ran
    told the chat of no ending, so the list never had it."""
    info = world.spawn()
    await world.answer(DECLINED)
    (card,) = [c for c in world.cards() if c.get("id") == info.id]
    assert card["session"] == CHAT and card["never_ran"] is True and card["declined"] is True
    assert card["task"] == TASK and "declined" in card["error"], card


# ── a start nobody allowed, a batch that never started ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_helper_nobody_allowed_in_time_starts_no_turn_and_is_told(world):
    """🔴 Red on integration: the start nobody answered started a turn of its own."""
    info = world.spawn()
    await world.answer(NOBODY_IN_TIME)
    assert world.handed == []
    told = await world.her_turn()
    assert info.id in told and "not approved in time" in told, told
    assert "do not start it again" not in told.lower(), "nobody declined it: it may be asked again"


@pytest.mark.asyncio
async def test_a_batch_that_never_started_starts_no_turn(world):
    """A batch's one ask, declined; one nobody answered in time (as a restart finds it). 🔴 Red
    on integration: each started a turn of its own in the chat."""
    from personalclaw.workflows import batch_start

    tasks = [
        batch_start.Task("batch.0", "notes_0", "Read the notes", "", (), ()),
        batch_start.Task("batch.1", "log_1", "Read the log", "", (), ()),
    ]
    for name, declined in (("subagent-batch-1", True), ("subagent-batch-2", False)):
        error = "batch declined, so it never started" if declined else "batch not approved in time"
        world.orch._announce_ended_work(
            batch_start.never_started(KEY, name, tasks, error=error, declined=declined)
        )
        await world.settle()
    assert world.handed == []
    told = await world.her_turn()
    assert "subagent-batch-1" in told and "subagent-batch-2" in told, told


# ── a helper that ran ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_helper_that_finished_still_reports_as_a_turn_of_its_own(world):
    """The control: work she allowed ran, and its report starts its turn, as it always has."""
    info = world.spawn()
    world.release.set()
    await world.answer(ALLOWED)
    assert len(world.handed) == 1, world.handed
    assert info.id in world.handed[0] and "drops the BOM check" in world.handed[0]


@pytest.mark.asyncio
async def test_a_helper_that_ran_and_failed_still_reports(tmp_path):
    """The control: a helper that started and failed reports as a turn, so its agent can say why."""
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        w = _World(tmp_path, fails=True)
        info = w.spawn()
        await w.answer(ALLOWED)
    assert len(w.handed) == 1 and info.id in w.handed[0] and "503" in w.handed[0], w.handed


@pytest.mark.asyncio
async def test_a_helper_she_stopped_starts_no_turn_and_is_told(world):
    """She stops a running helper from the background agents list (``DELETE /api/spawn/{id}``).
    🔴 Red on integration: its "Cancelled by user" started a turn, and the agent took it up."""
    from personalclaw.dashboard.handlers.messaging import api_spawn_delete

    info = world.spawn()
    await world._answers.put(ALLOWED)
    await world.until_running(info)
    request = MagicMock(spec=web.Request)
    request.app = {"state": world.state}
    request.match_info = {"agent_id": info.id}
    await api_spawn_delete(request)
    await world.settle()
    assert world.handed == [], "her stop of the helper started a turn"
    told = await world.her_turn()
    assert info.id in told and "stopped" in told.lower(), told


@pytest.mark.asyncio
async def test_her_stop_fan_out_starts_no_turn(world):
    """Her Stop fan-out in the chat's Subagents list (``POST /api/spawn/cancel-fanout``). 🔴 Red on
    integration: the helpers it stopped reported as a turn."""
    from personalclaw.dashboard.handlers.messaging import api_spawn_cancel_fanout

    info = world.spawn()
    await world._answers.put(ALLOWED)
    await world.until_running(info)
    request = MagicMock(spec=web.Request)
    request.app = {"state": world.state}
    request.json = AsyncMock(return_value={"parent_session": CHAT})
    await api_spawn_cancel_fanout(request)
    await world.settle()
    assert world.handed == []
    assert info.id in await world.her_turn()


@pytest.mark.asyncio
async def test_a_helper_a_run_budget_stopped_reports_why_not_that_she_cancelled_it(world):
    """A product's limit ended work she asked for that ran: its report still starts its turn, and
    says what stopped it. 🔴 Red on integration: it said "Cancelled by user"."""
    info = world.spawn()
    await world._answers.put(ALLOWED)
    await world.until_running(info)
    await world.manager.cancel_fanout(KEY, reason="run budget exceeded (spent $5.10 of $5.00)")
    await world.settle()
    assert len(world.handed) == 1 and info.id in world.handed[0], world.handed
    assert "run budget exceeded" in world.handed[0] and "Cancelled by user" not in world.handed[0]


# ── a scheduled job's helper ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scheduled_jobs_declined_helper_is_handed_to_no_turn(world):
    """A scheduled job's agent asked for the helper. 🔴 Red on integration: her Deny was handed to
    the job's session as a turn. Its session still ends, as after the last helper's report."""
    job = "cron:morning-brief"
    world.spawn(parent=job)
    await world.answer(DECLINED)
    world.orch.sessions.get_or_create.assert_not_called()
    world.orch.sessions.reset.assert_awaited_once_with(job)
