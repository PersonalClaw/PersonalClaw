"""An MCP server's question to you is the chat's own whose call it interrupts.

A server you allowed to ask questions (MCP elicitation) raised its question as an approval with no
session, so its Inbox row was about no chat, and every agent reads those: another chat's, a
scheduled briefing's ``inbox_list``. A Temporary or Incognito chat's call could have its server
repeat what the call sent, and another chat's agent could read it while it waited.

Now a connection that is one chat's own (``McpServerConn.scope``) names that chat, as the call's
own approval does. A connection several chats share can't say whose call it is, so its question's
Inbox row is yours alone (``refs.yours_alone``, ``inbox_reach.YOURS_ALONE``), as is the note it
leaves when nobody answers, and it is still answered from your Inbox.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture

from personalclaw import inbox_reach, mcp_elicitation

TEMP = "dashboard:temp-chat"


@pytest.fixture
def asked(monkeypatch):
    """The approval the question raises, as ``request_approval`` was asked for it."""
    state = SimpleNamespace(request_approval=AsyncMock(return_value=True))
    monkeypatch.setattr(mcp_elicitation, "elicitation_granted", lambda server: True)
    monkeypatch.setattr(mcp_elicitation, "_live_state", lambda: state)
    return state.request_approval


def _question():
    return SimpleNamespace(message="Post this to #general?", mode="form", requestedSchema={})


@pytest.mark.asyncio
async def test_a_chats_own_connection_asks_in_that_chat(asked):
    """🔴 Red before: the question named no chat."""
    await mcp_elicitation._handle_elicitation("poster", _question(), session=TEMP)
    kwargs = asked.await_args.kwargs
    assert kwargs["session"] == TEMP
    assert kwargs["yours_alone"] is False


@pytest.mark.asyncio
async def test_a_shared_connections_question_is_yours_alone(asked):
    """🔴 Red before: about no chat, so every agent read it."""
    await mcp_elicitation._handle_elicitation("poster", _question())
    kwargs = asked.await_args.kwargs
    assert kwargs["session"] == ""
    assert kwargs["yours_alone"] is True


@pytest.mark.asyncio
async def test_the_callback_hands_the_connections_chat_to_the_question(monkeypatch):
    """🔴 Red before: the callback knew only the server's name."""
    seen: list = []

    async def _handle(server, params, *, session=""):
        seen.append((server, session))

    monkeypatch.setattr(mcp_elicitation, "elicitation_granted", lambda server: True)
    monkeypatch.setattr(mcp_elicitation, "_handle_elicitation", _handle)
    callback = mcp_elicitation.elicitation_callback_for("poster", session=TEMP)
    await callback(None, _question())
    assert seen == [("poster", TEMP)]


def test_a_connection_passes_the_chat_it_is_that_chats_own_for(monkeypatch):
    """🔴 Red before: the connection built its callback from the server's name alone."""
    from personalclaw.mcp_client import McpServerConn

    built: list = []
    monkeypatch.setattr(
        mcp_elicitation,
        "elicitation_callback_for",
        lambda server, *, session="": built.append((server, session)),
    )
    McpServerConn("poster", {}, scope=TEMP)._elicitation_callback()
    McpServerConn("shared", {}, scope="")._elicitation_callback()
    assert built == [("poster", TEMP), ("shared", "")]


def test_an_item_yours_alone_is_read_by_you_and_no_chats_agent():
    """🔴 Red before: there was no ref an item could name to be yours alone."""
    item = SimpleNamespace(id="i1", refs={inbox_reach.YOURS_ALONE: "1", "approval": "a1"})
    you = MagicMock(everyone=True)
    agent = MagicMock(everyone=False)
    agent.reads.return_value = True  # it would read any chat's
    assert inbox_reach.reads(you, item) is True
    assert inbox_reach.reads(agent, item) is False


def test_the_approvals_inbox_row_names_its_chat_or_yours_alone(monkeypatch):
    """🔴 Red before: a question with no session raised a row about no chat."""
    from personalclaw import inbox
    from personalclaw.dashboard.approval_state import DashboardApprovalState

    rows: list[dict] = []
    monkeypatch.setattr(inbox, "emit_attention_item", lambda state, **kw: rows.append(kw["refs"]))
    owner = SimpleNamespace(_log=MagicMock())
    entry = {"id": "a1", "source_label": "poster", "tool": "ask", "session": ""}
    DashboardApprovalState._raise_approval_row(owner, {**entry, "yours_alone": True})
    DashboardApprovalState._raise_approval_row(owner, {**entry, "session": TEMP})
    DashboardApprovalState._raise_approval_row(owner, entry)
    assert rows[0][inbox_reach.YOURS_ALONE] == "1" and "session" not in rows[0]
    assert rows[1]["session"] == TEMP and inbox_reach.YOURS_ALONE not in rows[1]
    assert "session" not in rows[2]  # an approval of no chat's, as before
    assert inbox_reach.YOURS_ALONE not in rows[2]


def test_the_note_an_unanswered_question_leaves_is_as_much_yours_alone(monkeypatch):
    """🔴 Red before: the note carries the server's schema and was about no chat."""
    from personalclaw import auto_denials

    notes: list[dict] = []
    monkeypatch.setattr(auto_denials, "_emit", lambda state, **kw: notes.append(kw["refs"]) or "n")
    entry = {"id": "a1", "tool": "ask", "tool_input": "{}", "session": "", "ts": 0.0}
    auto_denials.note_expired(None, {**entry, "yours_alone": True}, who="poster", window_secs=60)
    auto_denials.note_expired(None, entry, who="poster", window_secs=60)
    assert notes[0][inbox_reach.YOURS_ALONE] == "1"
    assert inbox_reach.YOURS_ALONE not in notes[1]


@pytest.mark.asyncio
async def test_a_real_shared_ask_and_its_note_are_yours_alone(world, monkeypatch):  # noqa: F811
    """🔴 Red before: the real approval's row and the note it left named no chat and nothing else."""
    from personalclaw.auto_denials import EXPIRED

    monkeypatch.setattr(type(world.state), "approval_window_secs", lambda _self: 0.05)
    asked = world.state.request_approval("elicit:aa01", "poster", "ask", yours_alone=True)
    assert await asked is False
    in_chat = world.state.request_approval(
        "elicit:aa02", "poster", "ask", session="dashboard:c1", yours_alone=True
    )
    assert await in_chat is False

    def rows(approval_id: str) -> list[dict]:
        return [
            i.refs
            for i in world.inbox.items.values()
            if approval_id in (i.refs.get("approval"), i.refs.get("denied_approval"))
        ]

    alone = rows("elicit:aa01")
    assert len(alone) == 2 and {r.get("auto_denied") for r in alone} == {None, EXPIRED}
    assert all(r.get(inbox_reach.YOURS_ALONE) == "1" for r in alone), alone
    assert all(inbox_reach.YOURS_ALONE not in r for r in rows("elicit:aa02"))
