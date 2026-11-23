"""A chat that goes leaves none of its words in your Inbox or your notifications.

Measured from the code: deleting a chat removed its transcript and the stores kept for it, and
touched neither the Inbox nor the notification log, which are shared and keyed by row. So every
approval's row stayed with the tool's purpose and its arguments, a helper's report stayed in the
bell word for word, and the chat's questions and notes stayed, though the Delete dialog said the
chat and its history would be removed. The same when a Temporary chat ended.

The owner's call (2026-10-14): each decision the chat asked for stays in the Inbox as a short
record (what was asked, when, and how it ended) with nothing of the chat in it, and opens nothing;
everything else it raised there and in the bell goes.

Driven through the real delete (``chat_forget.delete_chats``) and the real Temporary end, with the
rows raised the way the gateway raises them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from chat_test_helpers import _make_state

from personalclaw import auto_denials, notification_kinds
from personalclaw.dashboard.approval_state import settled_row_text
from personalclaw.dashboard.chat_forget import delete_chats, forget_temporary_chat
from personalclaw.inbox import (
    OPEN_STATUSES,
    InboxState,
    InboxStore,
    ItemKind,
    ItemStatus,
    emit_attention_item,
    set_item_status,
)

pytestmark = pytest.mark.asyncio

PURPOSE = "Clear the build folder before the release"
ARGUMENTS = "rm -rf build/orchard-release"
QUESTION = "Which of the two orchard plans should I keep?"
REPORT = "The pear trees need a pollinator: plant a second variety within fifty feet."
TITLE = "Orchard planning"


@pytest.fixture
def state(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.inbox as inbox_mod
    import personalclaw.session_workspace as ws

    for target in (cfg, ws, inbox_mod):
        monkeypatch.setattr(target, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    s = _make_state(tmp_path)
    store = InboxStore(path=tmp_path / "inbox.json")
    s._inbox_svc = SimpleNamespace(inbox=store, state=InboxState())
    return s


def _raise_rows(state, key: str, label: str) -> dict[str, str]:
    """What a chat filed under *key* raises: an approval answered Allow, a call nobody answered,
    a question, and a helper's report in the bell. Returns their row ids by what they are."""
    entry = {
        "id": f"appr-{label}",
        "source_label": TITLE,
        "session": key,
        "tool": "bash",
        "tool_purpose": PURPOSE,
        "tool_input": ARGUMENTS,
        "risk": "high",
        "ts": 1_763_800_000.0,
    }
    state._raise_approval_row(entry)
    store = state._inbox_svc.inbox
    approval = next(i for i in store.items.values() if i.refs.get("approval") == entry["id"])
    approval.message = settled_row_text(approval.message, "approved")
    set_item_status(state, store, [approval], ItemStatus.HANDLED.value)
    denied = auto_denials.note_expired(
        state, {**entry, "id": f"gone-{label}"}, who=f"The agent in “{TITLE}”", window_secs=600
    )
    asked = emit_attention_item(
        state,
        source="agent",
        kind="needs_input",
        item_kind=ItemKind.NEEDS_INPUT.value,
        title=QUESTION,
        body=f"Asked in “{TITLE}”.",
        refs={"session": key},
    )
    state.notify(
        notification_kinds.SUBAGENT,
        "Helper finished: Read up on pollination",
        REPORT,
        meta={"session": key.removeprefix("dashboard:")},
    )
    return {"approval": approval.id, "denied": denied, "question": asked}


def _rows(state) -> dict:
    return state._inbox_svc.inbox.items


def _notes_naming(state, names: set[str]) -> list[dict]:
    return [
        n
        for n in state._notification_log
        if n.get("session") in names or REPORT in str(n.get("body") or "")
    ]


def _assert_kept_as_a_record(row, *, tool: str = "bash") -> None:
    text = row.message
    for words in (PURPOSE, ARGUMENTS, TITLE, "orchard"):
        assert words not in text, f"{words!r} stayed in the record: {text!r}"
    assert tool in text and "gone" in text, text
    assert row.status not in OPEN_STATUSES
    assert row.refs.get("chat_deleted") == "1"
    assert not {"source_label", "chat", "place_work", "place_step", "place_room"} & set(row.refs)
    assert row.refs.get("session"), "it keeps the chat's key, so no agent reads it"


@pytest.mark.parametrize("key", ["dashboard:chat-a", "chat-a"])
async def test_a_deleted_chats_decisions_stay_as_records_and_the_rest_goes(state, key):
    """🔴 Red before: all of it stayed as it was, the approval with its purpose and arguments and
    the helper's report in the bell word for word."""
    state.get_or_create_session("chat-a").append("user", "plan the orchard", broadcast=False)
    state.get_or_create_session("chat-c").append("user", "plan the garden", broadcast=False)
    mine = _raise_rows(state, key, "a")
    other = _raise_rows(state, "dashboard:chat-c", "c")
    before_other = {i: _rows(state)[i].to_dict() for i in other.values()}
    assert _notes_naming(state, {"chat-a", "dashboard:chat-a"}), "precondition: the bell has them"

    await delete_chats(state, ["chat-a"], by="you")

    rows = _rows(state)
    assert mine["question"] not in rows
    _assert_kept_as_a_record(rows[mine["approval"]])
    assert "approved" in rows[mine["approval"]].message.lower()
    _assert_kept_as_a_record(rows[mine["denied"]])
    assert not [
        n for n in state._notification_log if n.get("session") in {"chat-a", "dashboard:chat-a"}
    ]
    assert not [n for n in state._notification_log if n.get("inbox_item") in mine.values()]
    # Another chat's rows are as they were: purging or rewriting every row cannot pass.
    assert {i: rows[i].to_dict() for i in other.values()} == before_other
    assert [n for n in state._notification_log if n.get("session") == "chat-c"]


async def test_the_records_kept_are_read_by_no_agent(state):
    """No agent reads a record kept from a deleted chat: not another chat's, not a scheduled
    briefing's (``inbox_reach``)."""
    from personalclaw.inbox_reach import reads
    from personalclaw.subagent_reach import Reader

    state.get_or_create_session("chat-a").append("user", "plan the orchard", broadcast=False)
    mine = _raise_rows(state, "dashboard:chat-a", "a")
    await delete_chats(state, ["chat-a"], by="you")

    kept = [_rows(state)[mine["approval"]], _rows(state)[mine["denied"]]]
    for work in ("dashboard:chat-c", "cron:morning-briefing"):
        reader = Reader(state, work=work)
        assert not [row for row in kept if reads(reader, row)], work


async def test_a_temporary_chats_end_does_the_same(state):
    """🔴 Red before: a Temporary chat's end left its rows and notes as they were."""
    chat = state.get_or_create_session("chat-t", memory_mode="temporary")
    chat.append("user", "plan the orchard", broadcast=False)
    mine = _raise_rows(state, "dashboard:chat-t", "t")

    forget_temporary_chat(state, chat, why="its session ended")

    rows = _rows(state)
    assert mine["question"] not in rows
    _assert_kept_as_a_record(rows[mine["approval"]])
    _assert_kept_as_a_record(rows[mine["denied"]])
    assert not [n for n in state._notification_log if REPORT in str(n.get("body") or "")]


async def test_open_tabs_hear_what_moved(state):
    """🔴 Red before: nothing told an open Inbox, which kept showing the rows until a reload."""
    frames: list[tuple[str, dict]] = []
    state.get_or_create_session("chat-a").append("user", "plan the orchard", broadcast=False)
    mine = _raise_rows(state, "dashboard:chat-a", "a")
    state.broadcast_ws = lambda kind, data=None: frames.append((kind, data or {}))

    await delete_chats(state, ["chat-a"], by="you")

    updated = {d.get("id") for kind, d in frames if kind == "inbox_item_updated"}
    assert {mine["approval"], mine["denied"], mine["question"]} <= updated
    removed = [d for kind, d in frames if kind == "notification_removed"]
    assert removed and all(isinstance(d.get("ts"), list) for d in removed)


#: An agent CLI's call as its approval titles it: the call itself (``acp.translate``).
CLI_CALL = "cat ~/Documents/orchard-divorce-settlement.md"


async def test_a_record_names_an_agent_clis_call_by_no_more_than_a_tool_name(state):
    """🔴 Red before: the record kept the approval's title, which on an agent CLI is the call
    itself, the command and the file it read."""
    state.get_or_create_session("chat-a").append("user", "plan the orchard", broadcast=False)
    entry = {
        "id": "appr-cli",
        "source_label": TITLE,
        "session": "dashboard:chat-a",
        "tool": CLI_CALL,
        "tool_input": "",
        "ts": 1_763_800_000.0,
    }
    state._raise_approval_row(entry)
    store = state._inbox_svc.inbox
    approval = next(i for i in store.items.values() if i.refs.get("approval") == "appr-cli")
    approval.message = settled_row_text(approval.message, "approved")
    set_item_status(state, store, [approval], ItemStatus.HANDLED.value)
    denied = auto_denials.note_expired(
        state, {**entry, "id": "gone-cli"}, who="The agent", window_secs=600
    )

    await delete_chats(state, ["chat-a"], by="you")

    for row in (_rows(state)[approval.id], _rows(state)[denied]):
        assert "orchard" not in row.message and "~/" not in row.message, row.message
        assert "a tool call" in row.message, row.message
        assert "orchard" not in str(row.refs), row.refs


async def test_a_helpers_rows_and_notes_go_with_its_chat(state):
    """A question and a note a helper of the chat raised are the chat's (``subagent_reach``), and
    so is a helper's helper's: they go with it, as the chat's own do."""
    from personalclaw.subagent import SubagentInfo, SubagentManager

    state.get_or_create_session("chat-a").append("user", "plan the orchard", broadcast=False)
    state.subagents = SubagentManager(sessions=state.sessions, ctx_builder=None)
    for agent_id, parent in (("h1", "dashboard:chat-a"), ("h2", "subagent:h1")):
        state.subagents._agents[agent_id] = SubagentInfo(
            id=agent_id, task="Read up on pollination", parent_session_key=parent, done=True
        )
    asked = emit_attention_item(
        state,
        source="agent",
        kind="needs_input",
        item_kind=ItemKind.NEEDS_INPUT.value,
        title=QUESTION,
        refs={"session": "subagent:h2"},
    )
    state.notify(
        notification_kinds.SUBAGENT, "Helper finished", REPORT, meta={"session": "subagent:h1"}
    )

    await delete_chats(state, ["chat-a"], by="you")

    assert asked not in _rows(state)
    assert not [n for n in state._notification_log if REPORT in str(n.get("body") or "")]
