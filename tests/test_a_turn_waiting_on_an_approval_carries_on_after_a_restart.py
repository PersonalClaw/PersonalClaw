"""A chat turn waiting on a tool approval carries on after the gateway restarts.

Measured before this was written: a native turn that asked to run a call waited on its own gate, in
memory, with the turn's history and the step's calls kept nowhere else. A restart ended the turn
("The gateway restarted before this reply finished"), the approval went from every surface, and the
owner got the turn back only through Regenerate, which asked the model again.

Now the runtime says where such a turn stands (``NativeAgentRuntime.park_point``): its history as
its model read it, the turn's own message in it, the call it waits on, and the results its step
already has. A runtime started after the restart carries the turn on from there
(``NativeAgentRuntime.resume``), the call answered by the approval kept for it: an Allow runs the
call on its guarded path, a Deny runs nothing, and the model is asked only for what comes after the
call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from test_native_runtime import _defn, _ScriptedModel, _Tool

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)

pytestmark = pytest.mark.asyncio

ASKED = "Change the greeting in notes.txt."


def _calls(*ids: str) -> list[AgentEvent]:
    """One answer of the model: a call of ``echo`` per id, with arguments that name it."""
    return [
        *(
            AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id=i, title="echo", tool_input=json.dumps({"x": i})
            )
            for i in ids
        ),
        AgentEvent(kind=EVENT_COMPLETE),
    ]


def _says(text: str) -> list[AgentEvent]:
    return [AgentEvent(kind=EVENT_TEXT_CHUNK, text=text), AgentEvent(kind=EVENT_COMPLETE)]


async def _runtime(model: _ScriptedModel, tool: _Tool) -> NativeAgentRuntime:
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await rt.start()
    return rt


async def _parked(*ids: str) -> tuple[dict[str, Any], _Tool]:
    """A turn whose model calls ``echo`` once per id, torn down by a restart while it waits on
    its first approval: where it stood, as written to disk and read back."""
    tool = _Tool(requires_approval=True)
    rt = await _runtime(_ScriptedModel([_calls(*ids), _says("never")]), tool)
    point = None
    stream = rt.stream(ASKED)
    async for ev in stream:
        if ev.kind == EVENT_PERMISSION_REQUEST:
            point = rt.park_point(ev.request_id)
            break
    await stream.aclose()  # the restart tears the waiting turn down
    assert point is not None
    assert tool.invoked == []
    return json.loads(json.dumps(point)), tool


async def _carried_on(
    point: dict[str, Any],
    *,
    allowed: bool,
    tool: _Tool | None = None,
    answer_asks: str = "",
) -> tuple[list[AgentEvent], _ScriptedModel, _Tool]:
    """Carry *point*'s turn on in a runtime started after the restart; any call that asks is
    answered with *answer_asks* (``approve``/``reject``)."""
    tool = tool or _Tool(requires_approval=True)
    model = _ScriptedModel([_says("Done: the greeting is changed.")])
    rt = await _runtime(model, tool)
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in rt.resume(point, allowed=allowed):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                if answer_asks == "approve":
                    await rt.approve_tool(ev.request_id)
                else:
                    await rt.reject_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    return seen, model, tool


def _result_of(seen: list[AgentEvent], call: str) -> str:
    return next(
        str(e.tool_output) for e in seen if e.kind == EVENT_TOOL_RESULT and e.tool_call_id == call
    )


def _handed(model: _ScriptedModel, call: str) -> str:
    """What the model after the restart was handed as *call*'s result."""
    (messages,) = model.seen_messages
    return next(str(m["content"]) for m in messages if m.get("tool_call_id") == call)


# ── where a waiting turn stands ──────────────────────────────────────────────────────────────


async def test_a_waiting_turn_says_where_it_stands():
    point, _tool = await _parked("c1")

    assert point["call"] == "c1"
    messages = point["messages"]
    assert messages[point["turn_index"]] == {"role": "user", "content": ASKED}
    (step,) = [m for m in messages if m.get("role") == "assistant"]
    assert [tc["id"] for tc in step["tool_calls"]] == ["c1"]
    assert not any(m.get("role") == "tool" for m in messages)


async def test_a_call_its_model_sent_with_no_id_is_not_carried_on():
    """An answer after the restart could not find the call again."""
    tool = _Tool(requires_approval=True)
    model = _ScriptedModel(
        [
            [
                AgentEvent(kind=EVENT_TOOL_CALL, title="echo", tool_input="{}"),
                AgentEvent(kind=EVENT_COMPLETE),
            ]
        ]
    )
    rt = await _runtime(model, tool)
    stream = rt.stream(ASKED)
    async for ev in stream:
        if ev.kind == EVENT_PERMISSION_REQUEST:
            assert rt.park_point(ev.request_id) is None
            break
    await stream.aclose()


async def test_a_turn_not_waiting_says_nothing():
    rt = await _runtime(_ScriptedModel([_says("hi")]), _Tool())
    assert rt.park_point("c1") is None


# ── carried on after the restart ─────────────────────────────────────────────────────────────


async def test_an_allow_runs_the_call_and_the_model_is_asked_once_for_what_comes_after():
    """🔴 Red before: there was nothing to carry the turn on from, and no way to."""
    point, _ = await _parked("c1")

    seen, model, tool = await _carried_on(point, allowed=True)

    assert tool.invoked == [{"x": "c1"}]
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in seen), "asked again"
    assert model.calls == 1
    assert _handed(model, "c1") == "OUT:c1"
    # It reads the turn as its model did before the restart: the request, then the step.
    (messages,) = model.seen_messages
    assert {"role": "user", "content": ASKED} in messages
    assert "".join(e.text or "" for e in seen if e.kind == EVENT_TEXT_CHUNK) == (
        "Done: the greeting is changed."
    )


async def test_a_deny_runs_nothing_and_the_model_is_told_you_declined():
    point, _ = await _parked("c1")

    seen, model, tool = await _carried_on(point, allowed=False)

    assert tool.invoked == []
    assert model.calls == 1
    handed = _handed(model, "c1").lower()
    assert "declined" in handed and "do not retry" in handed
    assert _result_of(seen, "c1") == _handed(model, "c1")


async def test_a_deny_runs_nothing_though_the_call_would_not_be_asked_now():
    """A Deny is the answer the call was given: a grant that would let it through now (here, a
    tool that no longer asks) does not run it."""
    point, _ = await _parked("c1")

    _seen, _model, tool = await _carried_on(
        point, allowed=False, tool=_Tool(requires_approval=False)
    )

    assert tool.invoked == []


async def test_an_allow_still_takes_the_calls_guarded_path():
    """The host's refusal of the tool now (its grants) still refuses it: the Allow answered the
    ask, not every check the call meets."""
    point, _ = await _parked("c1")
    tool = _Tool(requires_approval=True)
    model = _ScriptedModel([_says("I could not change it.")])
    rt = await _runtime(model, tool)
    rt.set_tool_grants(lambda *_a, **_k: "this run may not use echo")

    seen = [ev async for ev in rt.resume(point, allowed=True)]

    assert tool.invoked == []
    assert "may not use echo" in _result_of(seen, "c1")


async def test_the_steps_other_calls_run_after_it_and_ask_as_any_call_does():
    """Only the call the turn waited on was answered: the next call of its step asks."""
    point, _ = await _parked("c1", "c2")

    seen, model, tool = await _carried_on(point, allowed=True, answer_asks="approve")

    assert tool.invoked == [{"x": "c1"}, {"x": "c2"}]
    asked = [e.request_id for e in seen if e.kind == EVENT_PERMISSION_REQUEST]
    assert asked == ["c2"]
    assert model.calls == 1
    assert _handed(model, "c2") == "OUT:c2"


async def test_a_result_the_step_already_had_is_not_run_again():
    """A call that ran beside the one that asked, before the restart, is not run a second time:
    its result is what the model is handed."""
    point, _ = await _parked("c1", "c2")
    point["carried"] = {"c2": ["OUT:c2, before the restart", {}]}

    seen, model, tool = await _carried_on(point, allowed=True)

    assert tool.invoked == [{"x": "c1"}]
    assert not any(e.kind == EVENT_PERMISSION_REQUEST for e in seen)
    assert _handed(model, "c2") == "OUT:c2, before the restart"


async def test_a_turn_carried_on_from_its_last_step_still_runs_the_call():
    """The step it stopped in had counted: its call runs though it was the turn's last."""
    point, _ = await _parked("c1")
    tool = _Tool(requires_approval=True)
    model = _ScriptedModel([_says("never asked")])
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=[tool], max_turns=1
    )
    await rt.start()

    seen = [ev async for ev in rt.resume(point, allowed=True)]

    assert tool.invoked == [{"x": "c1"}]
    assert model.calls == 0
    assert seen[-1].kind == EVENT_COMPLETE


async def test_an_answer_given_ahead_holds_for_its_call_alone():
    """The Allow carried on is the call's own: when a grant decides the call before it asks, its
    answer does not linger for a later call the model sends under the same id, which asks."""
    point, _ = await _parked("c1")
    tool = _Tool(requires_approval=True)
    model = _ScriptedModel([_says("Done."), _calls("c1"), _says("ok")])
    rt = await _runtime(model, tool)
    rt.set_approval_policy("auto")  # a grant answers the carried-on call before any ask

    [ev async for ev in rt.resume(point, allowed=True)]
    assert tool.invoked == [{"x": "c1"}]

    rt.set_approval_policy("")  # the grant is taken back
    asked: list[str] = []

    async def pump() -> None:
        async for ev in rt.stream("Once more."):
            if ev.kind == EVENT_PERMISSION_REQUEST:
                asked.append(str(ev.request_id))
                await rt.reject_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    assert asked == ["c1"], "a later call under the same id ran on the carried-on answer"
    assert tool.invoked == [{"x": "c1"}]


# ── the chat, through a restart ──────────────────────────────────────────────────────────────


def _edit(call: str = "c1") -> list[AgentEvent]:
    """The model's answer that asks to change the file."""
    return [
        AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id=call,
            title="edit_file",
            tool_input=json.dumps({"x": "notes.txt"}),
        ),
        AgentEvent(kind=EVENT_COMPLETE),
    ]


class _Gateway:
    """A gateway's chat surface over the home, its chats on PersonalClaw's own runtime with one
    tool that asks (``edit_file``), answered by a scripted model."""

    def __init__(self, tmp_path, *answers: list[AgentEvent], runtime=NativeAgentRuntime) -> None:
        from chat_send_world import World

        self.tool = _Tool(name="edit_file", requires_approval=True)
        self.model = _ScriptedModel(list(answers))
        self.world = World(tmp_path, tools=[self.tool], model=self.model, runtime=runtime)
        self.state = self.world.state

    async def waiting(self, key: str, *, kept: bool = True) -> None:
        """Until the chat *key*'s turn waits on its approval, and, when it is to be *kept*, its
        record is written."""
        from personalclaw import parked_turns

        for _ in range(1000):
            asked = any(e.get("session") == key for e in self.state._pending_approvals.values())
            if asked and (not kept or parked_turns.path_of(key).is_file()):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the turn never asked, or was never kept")

    async def restart(self, monkeypatch) -> None:
        """The gateway stops to restart: its turns end, and its chats are saved."""
        from personalclaw import restart_request
        from personalclaw.dashboard.chat_persistence import save_all_sessions_to_history

        monkeypatch.setattr(restart_request, "stopping_for", lambda: restart_request.RESTARTING)
        await self.state.end_running_turns(timeout=5)
        save_all_sessions_to_history(self.state)


async def _parked_chat(tmp_path, monkeypatch) -> str:
    """A chat whose turn asked to change a file and was waiting on the owner's answer when the
    gateway restarted; its key."""
    before = _Gateway(tmp_path, _edit(), _says("never"))
    async with before.world.client() as client:
        key = (await before.world.send(client, ASKED))["session"]
        await before.waiting(key)
        await before.restart(monkeypatch)
    assert before.tool.invoked == []
    assert before.model.calls == 1
    return key


async def _answered_after_restart(tmp_path, key: str, action: str) -> tuple[_Gateway, Any]:
    """The gateway after the restart: its kept turns asked again, the chat *key*'s answered with
    *action*; the gateway and the chat once it has settled."""
    from personalclaw.dashboard.parked_chat_turns import take_back

    after = _Gateway(tmp_path, _says("Done: the greeting is changed."))
    async with after.world.client() as client:
        assert await take_back(after.state) == 1
        await after.waiting(key)
        answer = await client.post(f"/api/chat/sessions/{key}/approve", json={"action": action})
        assert answer.status == 200, await answer.text()
        session = await after.world.settled(key)
    return after, session


async def test_a_turn_waiting_on_an_approval_carries_on_after_a_restart(tmp_path, monkeypatch):
    """🔴 Red before: the restart ended the turn ("The gateway restarted before this reply
    finished"), its approval went from every surface, and nothing was asked again. Now the same
    approval is asked after the restart, and the owner's Allow runs the call, with one model call
    for what comes after it."""
    from personalclaw import parked_turns

    key = await _parked_chat(tmp_path, monkeypatch)
    assert parked_turns.path_of(key).is_file()

    after, session = await _answered_after_restart(tmp_path, key, "approved")

    assert after.tool.invoked == [{"x": "notes.txt"}]
    assert after.model.calls == 1
    assert _handed(after.model, "c1") == "OUT:notes.txt"
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "Done: the greeting is changed."
    assert not parked_turns.path_of(key).exists(), "the record is kept once it was taken"


async def test_the_chat_says_her_answer_carries_the_reply_on(tmp_path, monkeypatch):
    """Where the restart ended it, the chat says the reply waits on her answer, not that it was
    lost."""
    from personalclaw.dashboard.chat_runner import TURN_KEPT_WAITING_NOTICES

    key = await _parked_chat(tmp_path, monkeypatch)
    from personalclaw.dashboard.chat_utils import persisted_history_key
    from personalclaw.history import ConversationLog

    log = ConversationLog(base_dir=tmp_path / "history")
    rows = log.read_messages(persisted_history_key(log, key))
    from personalclaw.restart_request import RESTARTING

    assert rows[-1]["content"] == TURN_KEPT_WAITING_NOTICES[RESTARTING]


async def test_a_deny_after_the_restart_runs_nothing(tmp_path, monkeypatch):
    key = await _parked_chat(tmp_path, monkeypatch)

    after, _session = await _answered_after_restart(tmp_path, key, "rejected")

    assert after.tool.invoked == []
    assert after.model.calls == 1
    assert "declined" in _handed(after.model, "c1").lower()


async def test_a_turn_answered_before_the_restart_is_not_kept(tmp_path):
    """Only a wait the gateway's stop ended is kept: one answered goes on, and leaves nothing."""
    from personalclaw import parked_turns

    gw = _Gateway(tmp_path, _edit(), _says("Done."))
    async with gw.world.client() as client:
        key = (await gw.world.send(client, ASKED))["session"]
        await gw.waiting(key)
        assert parked_turns.path_of(key).is_file()
        answer = await client.post(f"/api/chat/sessions/{key}/approve", json={"action": "approved"})
        assert answer.status == 200
        await gw.world.settled(key)

    assert gw.tool.invoked == [{"x": "notes.txt"}]
    assert not parked_turns.path_of(key).exists()


async def test_a_turn_whose_window_ran_out_while_the_gateway_was_down_is_not_asked(
    tmp_path, monkeypatch
):
    from personalclaw import parked_turns
    from personalclaw.dashboard.parked_chat_turns import take_back

    key = await _parked_chat(tmp_path, monkeypatch)
    path = parked_turns.path_of(key)
    rec = json.loads(path.read_text())
    rec["parked_at"] -= rec["window_secs"] + 1
    path.write_text(json.dumps(rec))

    after = _Gateway(tmp_path, _says("never"))
    async with after.world.client():
        assert await take_back(after.state) == 0
    assert not path.exists()
    assert after.state._pending_approvals == {}
    assert after.tool.invoked == []


async def test_a_record_that_shows_one_call_and_holds_another_is_never_read(tmp_path, monkeypatch):
    """The card asked again is the card of the call that runs: a record whose call was changed
    after it was written is dropped, and nothing is asked or run."""
    from personalclaw import parked_turns
    from personalclaw.dashboard.parked_chat_turns import take_back

    key = await _parked_chat(tmp_path, monkeypatch)
    path = parked_turns.path_of(key)
    rec = json.loads(path.read_text())
    step = next(m for m in rec["point"]["messages"] if m.get("tool_calls"))
    step["tool_calls"][0]["function"]["arguments"] = json.dumps({"x": "/etc/hosts"})
    path.write_text(json.dumps(rec))

    after = _Gateway(tmp_path, _says("never"))
    async with after.world.client():
        assert await take_back(after.state) == 0
    assert not path.exists()
    assert after.tool.invoked == []


async def test_a_chat_that_keeps_no_words_keeps_no_waiting_turn(tmp_path, monkeypatch):
    """An Incognito chat's turn is not written down to wait out a restart."""
    from personalclaw import parked_turns

    gw = _Gateway(tmp_path, _edit(), _says("never"))
    gw.state.get_or_create_session("private-1", memory_mode="incognito")
    async with gw.world.client() as client:
        key = (await gw.world.send(client, ASKED, session="private-1"))["session"]
        assert key == "private-1"
        await gw.waiting(key, kept=False)
        await asyncio.sleep(0.2)  # time enough for a record to have been written
        assert not parked_turns.path_of(key).exists()
        await gw.restart(monkeypatch)
    assert not parked_turns.path_of(key).exists()


async def test_a_record_past_its_cap_is_not_written(tmp_path, monkeypatch):
    """A turn whose record would be larger than the cap ends at the restart as it did before
    records were kept: nothing is written, the chat says its reply was not carried on, and the next
    start asks nothing again."""
    from personalclaw import parked_turns
    from personalclaw.dashboard.chat_runner import TURN_CUT_WHILE_WAITING_NOTICES
    from personalclaw.dashboard.parked_chat_turns import take_back
    from personalclaw.restart_request import RESTARTING

    monkeypatch.setattr(parked_turns, "RECORD_CAP", 10)
    gw = _Gateway(tmp_path, _edit(), _says("never"))
    async with gw.world.client() as client:
        key = (await gw.world.send(client, ASKED))["session"]
        await gw.waiting(key, kept=False)
        await asyncio.sleep(0.2)  # time enough for a record to have been written
        assert not parked_turns.path_of(key).exists()
        await gw.restart(monkeypatch)

    assert not parked_turns.path_of(key).exists()
    assert gw.world.file_rows(key)[-1]["content"] == TURN_CUT_WHILE_WAITING_NOTICES[RESTARTING]
    after = _Gateway(tmp_path, _says("never"))
    async with after.world.client():
        assert await take_back(after.state) == 0
    assert after.state._pending_approvals == {}
    assert after.tool.invoked == []


async def _took_back_after(tmp_path, monkeypatch, change) -> tuple[_Gateway, str]:
    """A chat's turn kept through a restart, and the next start's settle of it after *change* was
    made to the chat it opened; the gateway and the chat's key."""
    from personalclaw import parked_turns
    from personalclaw.dashboard.chat_persistence import resolve_session
    from personalclaw.dashboard.parked_chat_turns import take_back

    key = await _parked_chat(tmp_path, monkeypatch)
    assert parked_turns.path_of(key).is_file()
    after = _Gateway(tmp_path, _says("never"))
    async with after.world.client():
        session = resolve_session(after.state, key)
        undo = change(session)
        try:
            assert await take_back(after.state) == 0
        finally:
            await undo()
    return after, key


@pytest.mark.parametrize("mode", ["incognito", "temporary"])
async def test_a_chat_that_keeps_no_words_since_its_turn_was_kept_is_not_asked_again(
    tmp_path, monkeypatch, mode
):
    """A chat turned Incognito or Temporary while the gateway was down keeps no words, so neither
    does its waiting turn: the record is dropped at the start, and nothing is asked or run."""
    from personalclaw import parked_turns

    def turned(session):
        session.memory_mode = mode

        async def undo() -> None:
            return None

        return undo

    after, key = await _took_back_after(tmp_path, monkeypatch, turned)
    assert not parked_turns.path_of(key).exists()
    assert after.state._pending_approvals == {}
    assert after.tool.invoked == []


async def test_a_chat_already_running_a_turn_at_the_start_does_not_take_its_kept_one(
    tmp_path, monkeypatch
):
    """A chat that is running a turn when the start settles its kept one (a message sent to it
    first) keeps the turn that runs: the kept one is dropped, and nothing is asked or run."""
    from personalclaw import parked_turns

    def running(session):
        session.task = asyncio.ensure_future(asyncio.Event().wait())

        async def undo() -> None:
            session.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.task

        return undo

    after, key = await _took_back_after(tmp_path, monkeypatch, running)
    assert not parked_turns.path_of(key).exists()
    assert after.state._pending_approvals == {}
    assert after.tool.invoked == []


def test_a_restored_snapshots_waiting_turns_are_dropped(tmp_path):
    """A snapshot is a moment in the past: a turn it held waiting is not asked again, nor its call
    run, on the home it is restored to."""
    from personalclaw import parked_turns

    folder = tmp_path / parked_turns.PARKED_DIR
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps({"session": "chat-1"}))

    assert parked_turns.settle_restored(tmp_path) == ["chat-1"]
    assert list(folder.iterdir()) == []


def test_a_record_masks_what_ran_and_keeps_the_waiting_call_as_sent():
    """Every tool output and every call that already ran is kept masked, as the transcript keeps
    it; the call it waits on runs as its model sent it."""
    from personalclaw import parked_turns

    key = "ghp_" + "Zr8" * 12
    point = {
        "messages": [
            {"role": "user", "content": ASKED},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c0",
                        "type": "function",
                        "function": {"name": "echo", "arguments": json.dumps({"k": key})},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c0", "content": f"found {key}"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": json.dumps({"k": key})},
                    }
                ],
            },
        ],
        "turn_index": 0,
        "call": "c1",
        "carried": {},
        "images": [],
    }

    rec = parked_turns.record(
        "chat-1",
        "c1",
        card={"tool": "echo"},
        row={"title": "echo", "meta": {}},
        point=point,
        asked_for_by=None,
        window_secs=60,
    )

    assert rec is not None and parked_turns.holds(rec)
    messages = rec["point"]["messages"]
    assert key not in messages[1]["tool_calls"][0]["function"]["arguments"]
    assert key not in messages[2]["content"]
    assert key in messages[3]["tool_calls"][0]["function"]["arguments"]


class _CannotSay(NativeAgentRuntime):
    """A runtime that cannot say where a waiting turn stands, as an agent CLI's cannot."""

    park_point = None  # type: ignore[assignment]


async def test_a_turn_that_cannot_be_kept_says_so_where_the_restart_ended_it(tmp_path, monkeypatch):
    """A turn waiting on its approval whose runtime cannot go on from the middle of a call (an
    agent CLI's) is not kept, and the chat says the reply can't be carried on from there, not that
    it was cut off like any other."""
    from personalclaw import parked_turns
    from personalclaw.dashboard.chat_runner import TURN_CUT_WHILE_WAITING_NOTICES
    from personalclaw.restart_request import RESTARTING

    gw = _Gateway(tmp_path, _edit(), _says("never"), runtime=_CannotSay)
    async with gw.world.client() as client:
        key = (await gw.world.send(client, ASKED))["session"]
        await gw.waiting(key, kept=False)
        await gw.restart(monkeypatch)

    assert not parked_turns.path_of(key).exists()
    rows = gw.world.file_rows(key)
    assert rows[-1]["content"] == TURN_CUT_WHILE_WAITING_NOTICES[RESTARTING]
