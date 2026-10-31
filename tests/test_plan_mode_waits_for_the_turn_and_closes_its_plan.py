"""Plan mode waits for the turn it was switched on in, and leaving it closes its plan.

From an owner's chat (#5722, #5723). Plan mode switched on from the composer while a turn ran
parked the run: its queue was dropped and the turn was asked to stop, with nothing on the control
saying so. The stopped turn wrote no stop card, so the transcript showed an answer that was never
given, and the page offered follow-up chips as if it had answered. Ten seconds later the mode went
back to agent, which left the plan open and drafting. The next turn ran in agent mode, its report
of finished work became the plan to review, and approving it sent that report back as a run.

Now, while a turn runs:

* "Plan after this turn" (what the control does unless told otherwise) stops nothing: the turn
  runs to its end, the mode changes then, and the next turn plans;
* "Stop and plan" stops the turn as a Stop does, with the same card, live and after a reload;
* no stopped turn is offered follow-ups.

And setting any mode other than plan cancels a plan not yet drafted, parked run included, and only
a turn that ran in plan mode drafts a plan.

Everything runs through the real chat runner and the real routes; only the runtime is a fake.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_plan_mode_shared import _app

from personalclaw import cli_run
from personalclaw.dashboard import chat_plan
from personalclaw.dashboard.chat_followups import maybe_offer_check_work
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.planning.session import StepStatus

#: In a turn's script: the turn holds here until it is let go on, or until a stop is asked.
WAIT = object()

ANSWER = ["Half of the work ", WAIT, "and the rest. Done."]
PLAN = "# The plan\n- rename the module\n- update its imports"
REPORT = "I renamed the module and updated its imports."
CHIP = "Shall I run the tests?"


class _Runtime:
    """The chat's runtime. Each turn streams the next script: its text, then the end of its turn
    (once the scripts run out, a turn writes nothing). At ``WAIT`` it holds until let go on
    (``go_on``), when it carries on, or until a stop is asked, when it ends cancelled, as a runtime
    that heard the stop ends its turn: having said *says_on_stop* first, as a model call already in
    flight when the stop came finishes saying what it was saying."""

    def __init__(self, *turns: list, says_on_stop: str = "") -> None:
        self.turns = list(turns)
        self.says_on_stop = says_on_stop
        #: Called as the stop is asked, before it lands.
        self.on_stop = lambda: None
        self.waiting = asyncio.Event()
        self._go_on = asyncio.Event()
        self.stops = 0

    def stream(self, *_a, **_kw):
        return self._stream(self.turns.pop(0) if self.turns else [])

    async def _stream(self, script: list):
        for item in script:
            if item is WAIT:
                self.waiting.set()
                await self._go_on.wait()
                if self.stops:
                    if self.says_on_stop:
                        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=self.says_on_stop)
                    yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="cancelled")
                    return
                continue
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=item)
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    def go_on(self) -> None:
        self._go_on.set()

    async def stop_turn(self, _key, *, force=False, **_kw) -> str:
        self.on_stop()
        self.stops += 1
        self._go_on.set()
        return "soft"


def _state(tmp_path, runtime: _Runtime) -> DashboardState:
    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=runtime.stream)
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_channel_link = MagicMock(return_value=("", ""))  # no channel thread
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.stop_turn = runtime.stop_turn
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    from personalclaw.hooks import ToolHookResult

    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state._broadcast = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    return state


def _chat(state: DashboardState) -> _ChatSession:
    session = state.get_or_create_session("c1", memory_mode="persistent")
    session._titled = True  # the auto-title call is a model call of its own; not this contract
    return session


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """The home is the test's, and a turn that ends well is offered a chip (the chip model is a
    fake, so whether one is offered is the gateway's call alone)."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_handlers.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_followups.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_followups._followups_enabled", return_value=True),
        patch("personalclaw.dashboard.chat_followups._check_work_offer_enabled", return_value=True),
        patch(
            "personalclaw.dashboard.chat_followups._generate_followups",
            new=AsyncMock(return_value=[CHIP]),
        ),
    ):
        yield


def _start(state: DashboardState, session: _ChatSession, message: str) -> bool:
    return session.enqueue_or_run_prompt(message, run_chat, state)


async def _settled(session: _ChatSession) -> None:
    """Every turn the chat has to run has run, and what its last one offered has been offered."""
    while session.task is not None and not session.task.done():
        await asyncio.wait_for(asyncio.shield(session.task), timeout=10)
    if session._followups_task is not None:
        await asyncio.wait_for(session._followups_task, timeout=10)


async def _turn(state: DashboardState, session: _ChatSession, message: str) -> None:
    assert _start(state, session, message)
    await _settled(session)


def _chips(state: DashboardState) -> list[list[str]]:
    return [
        c.args[1].get("items")
        for c in state.broadcast_ws.call_args_list
        if c.args and c.args[0] == "chat_followups" and c.args[1].get("items")
    ]


def _check_work_offers(state: DashboardState) -> int:
    return sum(
        1 for c in state.broadcast_ws.call_args_list if c.args[:1] == ("chat_check_work_offer",)
    )


def _stop_cards(rows: list[dict]) -> list[dict]:
    """The stop cards in *rows*, as the page reads each one (its ``cls``)."""
    cards = []
    for row in rows:
        try:
            meta = json.loads(row.get("cls") or "")
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("kind") == "stop_event":
            cards.append(meta)
    return cards


def _live_stop_cards(state: DashboardState) -> list[dict]:
    """The stop cards the open page was sent, in order: one as the stop starts, and the same card
    again once it says how the stop ended."""
    frames = [c.args[0] for c in state._broadcast.call_args_list if c.args]
    return _stop_cards([f for f in frames if f.get("_type") == "chat_message"])


async def _activate(client, **body) -> dict:
    r = await client.post("/api/chat/sessions/c1/plan/activate", json=body)
    assert r.status == 200, await r.text()
    return await r.json()


async def _set_mode(client, mode: str) -> None:
    r = await client.post("/api/chat/task-mode", json={"mode": mode, "session": "c1"})
    assert r.status == 200, await r.text()


async def _plan_gate(client) -> dict:
    r = await client.get("/api/chat/sessions/c1/plan-session")
    assert r.status == 200, await r.text()
    return await r.json()


def _step(session: _ChatSession):
    sess, _binding = chat_plan.read(session.key)
    assert sess is not None, "the chat has no plan"
    return sess.steps[-1]


# ── plan mode switched on while a turn runs (#5722) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_switched_on_mid_turn_lets_the_turn_finish_and_plans_the_next(tmp_path):
    runtime = _Runtime(ANSWER, [PLAN])
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)

        body = await _activate(client)
        assert runtime.stops == 0, "nothing is stopped unless she picks Stop and plan"
        assert chat._task_mode == "agent", "the mode changes when the turn ends"
        assert (body["after_turn"], body["parked"], body["task_mode"]) == (True, False, "agent")

        runtime.go_on()
        await _settled(chat)
        answers = [m["content"] for m in chat.messages if m.get("role") == "assistant"]
        assert answers == ["Half of the work and the rest. Done."], "the turn ran to its end"
        assert chat._task_mode == "plan"
        assert _step(chat).status == StepStatus.RUNNING.value, "the turn that ran on is no plan"

        await _turn(state, chat, "plan it")
        gate = await _plan_gate(client)
    assert gate["awaiting_step_id"] == "chat-plan-1"
    assert _step(chat).artifact["markdown"] == PLAN, "the next turn is the planning turn"


@pytest.mark.asyncio
async def test_stop_given_as_text_is_refused_and_stops_nothing(tmp_path):
    """``stop`` is a JSON boolean. The text ``"false"`` is not a no: read by truthiness it would
    stop the turn, so it is refused, and the turn and the mode are left as they were."""
    runtime = _Runtime(ANSWER, [PLAN])
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)

        r = await client.post("/api/chat/sessions/c1/plan/activate", json={"stop": "false"})
        assert r.status == 400, await r.text()
        assert (await r.json())["error"]["code"] == "field_not_a_boolean"
        assert runtime.stops == 0 and chat._task_mode == "agent"

        runtime.go_on()
        await _settled(chat)
    assert chat._task_mode == "agent", "a refused switch leaves the mode as it was"


@pytest.mark.asyncio
async def test_a_message_queued_behind_the_turn_is_the_planning_turn(tmp_path):
    runtime = _Runtime(ANSWER, [PLAN])
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        assert not _start(state, chat, "and plan the imports"), "queued behind the turn"
        await _activate(client)
        runtime.go_on()
        await _settled(chat)
        gate = await _plan_gate(client)
    assert runtime.stops == 0
    assert gate["awaiting_step_id"] == "chat-plan-1"
    assert _step(chat).artifact["markdown"] == PLAN


@pytest.mark.asyncio
async def test_the_turn_that_ran_on_is_never_the_plan(tmp_path):
    """The planning turn writes nothing: the plan stays drafting. The answer of the turn that ran
    to its end, after plan mode was switched on, is not taken in its place: it writes all of it
    after the switch."""
    runtime = _Runtime([WAIT, "The whole answer, written after the switch."])
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        await _activate(client)
        runtime.go_on()
        await _settled(chat)
        await _turn(state, chat, "plan it")
        gate = await _plan_gate(client)
    assert runtime.stops == 0, "the turn ran to its end"
    assert gate["awaiting_step_id"] == ""
    assert _step(chat).status == StepStatus.RUNNING.value


@pytest.mark.asyncio
async def test_stop_and_plan_shows_the_stop_card_live_and_after_a_reload(tmp_path):
    runtime = _Runtime([WAIT], says_on_stop=REPORT)
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        body = await _activate(client, stop=True)
        await _settled(chat)
    assert runtime.stops == 1

    live = _live_stop_cards(state)
    assert [c["state"] for c in live] == ["stopping", "stopped"], live
    assert len({c["id"] for c in live}) == 1, "one card, told how its stop ended"

    # After a reload: the chat as a fresh gateway reads it back from its history.
    reloaded = _state(tmp_path, _Runtime())
    async with TestClient(TestServer(_app(reloaded))) as client:
        r = await client.get("/api/chat/sessions/c1")
        assert r.status == 200, await r.text()
        rows = (await r.json())["messages"]
    assert [c["state"] for c in _stop_cards(rows)] == ["stopped"], rows

    assert (body["parked"], body["after_turn"], body["task_mode"]) == (True, False, "plan"), body
    # And what the stopped agent turn wrote, after plan mode was on, is not the plan.
    said = [m["content"] for m in chat.messages if m.get("role") == "assistant"]
    assert any(REPORT in c for c in said), chat.messages
    assert _step(chat).status == StepStatus.RUNNING.value


@pytest.mark.asyncio
async def test_stop_and_plan_ends_a_headless_runs_trust_before_its_turn_is_asked_to_stop(
    tmp_path,
):
    """A headless run's chat keeps no Trust past a stop, as Stop ends it: it is gone by the time
    the runtime is asked to stop, so no call is approved on it while the turn winds down, and
    approving the plan resumes the run with each of its calls asking as usual."""
    runtime = _Runtime([WAIT], [PLAN], [REPORT])
    state = _state(tmp_path, runtime)
    key = cli_run.session_key_for("nightly-report")
    chat = state.get_or_create_session(key, memory_mode="persistent")
    chat._titled = True
    chat._trust = True
    assert state.standing_grant(chat) != "", "the run's Trust answers its calls"
    asked: list[bool] = []
    runtime.on_stop = lambda: asked.append(chat._trust)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "write the report")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        r = await client.post(f"/api/chat/sessions/{key}/plan/activate", json={"stop": True})
        assert r.status == 200, await r.text()
        assert asked == [False], "the turn was asked to stop while the run's Trust still stood"
        await _settled(chat)
        assert (chat._trust, state.standing_grant(chat)) == (False, "")

        await _turn(state, chat, "plan the report")
        r = await client.post(
            f"/api/chat/sessions/{key}/plan/approve", json={"step_id": "chat-plan-1"}
        )
        assert r.status == 200, await r.text()
        assert (await r.json())["resumed"] is True
        await _settled(chat)
    assert runtime.turns == [], "the approved plan ran"
    assert (chat._trust, state.standing_grant(chat)) == (False, "")


@pytest.mark.asyncio
async def test_no_follow_ups_are_offered_after_stop_and_plan(tmp_path):
    runtime = _Runtime([WAIT])
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        await _activate(client, stop=True)
        await _settled(chat)
    assert runtime.stops == 1
    assert _chips(state) == []


@pytest.mark.asyncio
async def test_no_follow_ups_are_offered_after_a_stop(tmp_path):
    runtime = _Runtime(["Half an answer ", WAIT])
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        r = await client.post("/api/chat/sessions/c1/stop")
        assert r.status == 200, await r.text()
        await _settled(chat)
    assert runtime.stops == 1
    assert _chips(state) == []


@pytest.mark.asyncio
async def test_a_turn_that_ends_with_its_answer_is_offered_follow_ups(tmp_path):
    """Vacuity floor for the two above: the same chat and chip model offer a chip once a turn
    ends with its answer."""
    state = _state(tmp_path, _Runtime([REPORT]))
    chat = _chat(state)
    await _turn(state, chat, "rename the module")
    assert _chips(state) == [[CHIP]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("script", "offered"), [([WAIT], 0), ([REPORT], 1)], ids=["stopped", "answered"]
)
async def test_only_a_turn_that_ends_with_its_answer_is_offered_a_check_of_its_work(
    tmp_path, script, offered
):
    """The "Check this work" chip follows up a turn too: a stopped one is offered none, whatever
    its last words claim. The chip is offered for a turn of three or more steps whose answer says
    it is done, read here straight after the turn."""
    runtime = _Runtime(script)
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        if script == [WAIT]:
            await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
            assert (await client.post("/api/chat/sessions/c1/stop")).status == 200
        await _settled(chat)
    chat.append("assistant", "Done, I created three files.", "msg msg-a")
    state.broadcast_ws.reset_mock()
    maybe_offer_check_work(state, chat, 5)
    assert _check_work_offers(state) == offered


# ── leaving plan mode before its plan is drafted (#5723) ────────────────────────────────────


@pytest.mark.asyncio
async def test_leaving_plan_mode_before_a_draft_cancels_the_plan(tmp_path):
    state = _state(tmp_path, _Runtime([REPORT]))
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        await _activate(client)
        assert chat._task_mode == "plan"
        await _set_mode(client, "agent")
        assert chat_plan.read(chat.key)[0] is None, "the plan is cancelled, as Cancel cancels it"

        await _turn(state, chat, "rename the module")
        gate = await _plan_gate(client)
    assert (gate["session"], gate["awaiting_step_id"]) == (None, ""), "nothing to approve"
    assert chat._task_mode == "agent"


@pytest.mark.asyncio
async def test_leaving_plan_mode_lets_the_run_it_parked_go(tmp_path):
    runtime = _Runtime([WAIT], [REPORT])
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        await _activate(client, stop=True)
        await _settled(chat)
        assert chat_plan.read(chat.key)[1].get("parked") is True
        await _set_mode(client, "agent")
        await _turn(state, chat, "carry on")
        r = await client.post("/api/chat/sessions/c1/plan/approve", json={"step_id": "chat-plan-1"})
    assert r.status == 404, "no plan is left to approve, and no parked run to send"
    assert chat_plan.read(chat.key) == (None, {})


@pytest.mark.asyncio
async def test_a_mode_picked_while_the_plan_waits_for_the_turn_cancels_it(tmp_path):
    runtime = _Runtime(ANSWER)
    state = _state(tmp_path, runtime)
    chat = _chat(state)
    async with TestClient(TestServer(_app(state))) as client:
        assert _start(state, chat, "rename the module")
        await asyncio.wait_for(runtime.waiting.wait(), timeout=10)
        await _activate(client)
        await _set_mode(client, "ask")
        runtime.go_on()
        await _settled(chat)
    assert chat_plan.read(chat.key)[0] is None
    assert chat._task_mode == "ask", "the turn's end starts no plan the mode change cancelled"


@pytest.mark.asyncio
async def test_an_agent_turn_never_drafts_a_plan_even_with_one_open_in_the_home(tmp_path):
    """A plan left open in the home (a sidecar from before this fix, or one a mode change on
    another gateway never closed): an agent-mode turn's answer is not handed to it."""
    state = _state(tmp_path, _Runtime([REPORT]))
    chat = _chat(state)
    chat_plan.activate(chat, running=False)  # its step drafting, the chat in agent mode
    assert chat._task_mode == "agent"

    await _turn(state, chat, "rename the module")

    step = _step(chat)
    assert (step.status, step.artifact) == (StepStatus.RUNNING.value, {}), step
    assert chat_plan.awaiting_review(chat.key) == ""


def test_the_plan_hooks_are_plain_and_the_turn_keeps_its_wrappers() -> None:
    """The turn's two plan hooks are plain functions beside ``run_chat``, not under its decorators.
    Placed between them, the first was wrapped as a turn (a coroutine nobody awaits, so the plan
    never started), and ``run_chat`` lost its phases and its memory session."""
    import inspect

    from personalclaw.dashboard import chat_runner

    for hook in (chat_runner._start_plan_after_turn, chat_runner._submit_plan_draft):
        assert not inspect.iscoroutinefunction(hook), hook
        assert not hasattr(hook, "__wrapped__"), hook
    layers, fn = [], chat_runner.run_chat
    while fn is not None:
        layers.append(fn.__code__.co_name)
        fn = getattr(fn, "__wrapped__", None)
    assert layers == ["_as_its_session", "_said", "run_chat"]
