"""A "Denied, no answer" note is handled once the call it names is asked again and answered.

#3698 leaves one Inbox note per approval nobody answered in time, and offers "Ask it to try again"
for one asked in a chat. The retry worked, the chat asked again, and the note stayed open after the
answer — still reading as a call that needed you.

When it resolves comes from the Inbox's own contract (`inbox.resolve_attention_items`): a row
raised for a standing request closes, as HANDLED, when the request stops standing. The note's
request is "this call was denied because nobody answered". That stops standing when the same call
is asked again, where it was asked before, and someone answers it, whichever way. Sending the retry
message does not do it, because nothing has been decided yet, and the call may never be asked
again. The note moves through the Inbox's one status transition (`inbox.set_item_status`), which
also tells every open surface and reads its notification in the bell. The answer is written on the
row (`refs.retry`), the way a proposal's result is (`proposals_contract._record`), so the note says
how it ended.

A note stays open for anything that is not that call: a different call of the same tool, the same
call in another chat, or a retry that nobody answers either.

Driven through the real chat runner and the real decision path.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from test_dashboard_approval import _make_session, _set_stream
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_pending_approval_every_surface import CHAT, _bash_request, _finish, _turn, _until

from personalclaw.approval_answer import YOU
from personalclaw.dashboard.chat import run_chat
from personalclaw.inbox import OPEN_STATUSES


@pytest.fixture(autouse=True)
def _measured_context(world):  # noqa: F811 - the imported fixture, by name
    """The harness client is an AsyncMock, whose context read is a coroutine the turn's closing
    line cannot round; a plain "unmeasured" keeps the turn from ending in an error."""
    world.client.context_usage_pct = MagicMock(return_value=None)


def _notes(store) -> list:
    return [i for i in store.items.values() if i.refs.get("auto_denied")]


async def _ask(w, session, request_id: str, command: str, *, window: float, monkeypatch):
    """One chat turn that asks for `command` and waits `window` for an answer."""
    monkeypatch.setattr(w.state, "approval_window_secs", lambda: window)
    _set_stream(w.client, _turn(_bash_request(request_id, command)))
    task = asyncio.create_task(run_chat(w.state, session, "clean up the scratch dir"))
    await _until(lambda: request_id in session._approval_futures, f"{request_id} was never asked")
    return task


async def _expired_note(w, monkeypatch, command: str = "rm -rf /tmp/scratch"):
    task = await _ask(w, w.session, "req-1", command, window=0.05, monkeypatch=monkeypatch)
    await asyncio.wait_for(task, timeout=5)
    (note,) = _notes(w.store)
    assert note.status in OPEN_STATUSES and note.refs["auto_denied"] == "expired"
    return note


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "recorded"), [("approved", "approved"), ("rejected", "rejected")]
)
async def test_asked_again_and_answered_the_note_is_handled_with_the_answer(
    world, monkeypatch, answer, recorded  # noqa: F811
):
    note = await _expired_note(world, monkeypatch)
    task = await _ask(
        world, world.session, "req-2", "rm -rf /tmp/scratch", window=60, monkeypatch=monkeypatch
    )
    try:
        # Asked again, not yet answered: the call is still undecided, so the note still stands.
        assert world.store.items[note.id].status in OPEN_STATUSES
        world.state.decide_session_approval(world.session, "req-2", answer, by=YOU)
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)

    row = world.store.items[note.id]
    assert row.status == "handled"
    assert row.refs["retry"] == recorded
    assert row.refs["retry_by"] == "you"
    # Every open surface was told, so the Inbox and Mission Control move it without a reload.
    moved = [d for kind, d in world.frames if kind == "inbox_item_updated" and d["id"] == note.id]
    assert moved and moved[-1]["status"] == "handled"


@pytest.mark.asyncio
async def test_answered_from_anywhere_else_it_is_handled_the_same_way(
    world, monkeypatch  # noqa: F811
):
    """Home, the phone and Mission Control answer through `resolve_approval`, the registry id."""
    note = await _expired_note(world, monkeypatch)
    task = await _ask(
        world, world.session, "req-2", "rm -rf /tmp/scratch", window=60, monkeypatch=monkeypatch
    )
    try:
        (approval_id,) = list(world.state._pending_approvals)
        assert world.state.resolve_approval(approval_id, True, by=YOU) is True
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)
    assert world.store.items[note.id].status == "handled"
    assert world.store.items[note.id].refs["retry"] == "approved"


@pytest.mark.asyncio
async def test_a_different_call_of_the_same_tool_leaves_the_note_open(
    world, monkeypatch  # noqa: F811
):
    note = await _expired_note(world, monkeypatch)
    task = await _ask(
        world, world.session, "req-2", "ls /tmp/scratch", window=60, monkeypatch=monkeypatch
    )
    try:
        world.state.decide_session_approval(world.session, "req-2", "approved", by=YOU)
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)
    assert world.store.items[note.id].status in OPEN_STATUSES
    assert "retry" not in world.store.items[note.id].refs


@pytest.mark.asyncio
async def test_the_same_call_answered_in_another_chat_leaves_it_open(
    world, monkeypatch  # noqa: F811
):
    note = await _expired_note(world, monkeypatch)
    other = _make_session("chat-b")
    world.state._sessions[other.key] = other
    task = await _ask(
        world, other, "req-2", "rm -rf /tmp/scratch", window=60, monkeypatch=monkeypatch
    )
    try:
        world.state.decide_session_approval(other, "req-2", "approved", by=YOU)
        await asyncio.wait_for(task, timeout=5)
    finally:
        await _finish(task)
    assert world.store.items[note.id].status in OPEN_STATUSES


@pytest.mark.asyncio
async def test_a_retry_nobody_answers_either_leaves_it_open(world, monkeypatch):  # noqa: F811
    """Nothing was decided, so nothing is handled: the second expiry leaves its own note beside."""
    note = await _expired_note(world, monkeypatch)
    task = await _ask(
        world, world.session, "req-2", "rm -rf /tmp/scratch", window=0.05, monkeypatch=monkeypatch
    )
    await asyncio.wait_for(task, timeout=5)
    assert world.store.items[note.id].status in OPEN_STATUSES
    assert len([n for n in _notes(world.store) if n.status in OPEN_STATUSES]) == 2
    assert note.refs.get("session") == CHAT


# ── A retry nobody was asked about: a standing grant ran it ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_retry_the_chat_s_trust_ran_settles_the_note_saying_so(
    world, monkeypatch  # noqa: F811
):
    """🔴 A retry the chat's Trust approved was never asked, so it never reached the registry, and
    the note stayed open over a call that had run. It settles, and names the Trust as who ran it
    (`refs.retry_by`), so the note does not read as your Allow."""
    note = await _expired_note(world, monkeypatch)
    world.session._trust = True
    _set_stream(world.client, _turn(_bash_request("req-2", "rm -rf /tmp/scratch")))
    await asyncio.wait_for(run_chat(world.state, world.session, "try again"), timeout=5)
    row = world.store.items[note.id]
    assert row.status == "handled"
    assert (row.refs["retry"], row.refs["retry_by"]) == ("approved", "trust")


@pytest.mark.asyncio
async def test_a_background_call_a_grant_approved_settles_its_note_too():
    """The relay: a subagent's call YOLO approved reaches the registry's settle, naming YOLO."""
    from test_gateway import _make_orchestrator, _mock_dashboard_state

    orch = _make_orchestrator()
    orch.dashboard_state = _mock_dashboard_state()
    orch.dashboard_state._yolo = True
    ask = _bash_request("subagent:ab12:tc-1")
    relay = orch._interactive_approval("subagent", session_resolver=lambda _rid: CHAT)
    assert await relay(ask, "")
    orch.dashboard_state.settle_granted.assert_called_once_with(
        tool="bash", tool_input=ask.tool_input, session=CHAT, trigger="", by="yolo"
    )


@pytest.mark.asyncio
async def test_a_subagent_s_call_its_grant_ran_is_reported_with_the_grant():
    """A subagent's own runtime approves from its grant without asking the relay; the manager
    reports the call to the gateway (`subagent_tool_granted`), which settles the note."""
    from test_a_tool_call_is_audited_as_it_was_decided import ASKS, _subagents

    from personalclaw.subagent import SubagentInfo

    manager, tools = _subagents(ASKS)
    seen: list[tuple[str, dict]] = []

    async def on_event(kind, _info, extra):
        seen.append((kind, dict(extra)))

    manager._on_event = on_event
    info = SubagentInfo(id="sa-g", task="t", approval_mode="auto", capability_class="mutating")
    await asyncio.wait_for(manager._run_inner(info, "subagent:sa-g"), timeout=20)
    assert tools.ran == [ASKS]
    granted = [extra for kind, extra in seen if kind == "subagent_tool_granted"]
    assert [(g["tool"], g["decided_by"]) for g in granted] == [(ASKS, "approval_mode")]
    # Described as its approval would have been, so the note it settles is the same call's.
    from personalclaw.task_modes import tool_input_to_str

    assert json.loads(tool_input_to_str(granted[0]["tool_input"])) == {"text": "hello"}
