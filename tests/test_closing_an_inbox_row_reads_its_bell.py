"""Closing an Inbox row reads its notification in the bell, on every path that closes one.

Measured on day 8: a note captured into the Inbox and then handled there left its notification
unread in the bell. It was not about notes. `emit_attention_item` stamps each notification with
the row it is about (`inbox_item`), and nothing ever read that link back, so every row closed
anywhere (a note handled, "Dismiss all", an approval answered in its chat, a workflow gate passed,
a loop resumed, a proposal accepted) left the bell counting work that was done.

The same moves were silent too. The resolver closed rows without a frame, and a row raised by
`emit_attention_item` arrived without one, while the Inbox page, Home and Mission Control read the
Inbox on its frames. So a gate passed or a new request raised was missing from all three until
something else made them re-read.

The fix is one status transition, `inbox.set_item_status`, that every writer goes through. These
drive it through each door a row closes by: the HTTP routes the Inbox page calls, the resolver the
approval, gate, loop and room paths share, and a proposal's own decision.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app

from personalclaw.dashboard import handlers_inbox as h
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbox import (
    InboxState,
    InboxStore,
    ItemKind,
    ItemStatus,
    emit_attention_item,
    resolve_attention_items,
)


@pytest.fixture
def world(tmp_path):
    """A real dashboard state (its real notification log) over a real, live Inbox store."""
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    frames: list[tuple[str, dict]] = []

    def _record(kind: str, data: dict | None = None) -> None:
        frames.append((kind, data or {}))

    def _fire(note: dict) -> None:
        _record(note.get("_type") or "notification", note)

    state.broadcast_ws = _record  # type: ignore[method-assign]
    # The full fire (toast, desktop banner) goes out through `_broadcast` as a `notification`.
    state._broadcast = _fire  # type: ignore[method-assign]
    store = InboxStore(tmp_path / "inbox.json")
    # `live_store` is type-checked, so a real InboxStore is what makes this the API's store.
    state._inbox_svc = SimpleNamespace(inbox=store, state=InboxState(tmp_path / "inbox_state.json"))
    return SimpleNamespace(state=state, store=store, frames=frames)


def _app(state):
    app = _api_app(state)
    app.router.add_post("/api/inbox/notes", h.api_inbox_note_create)
    app.router.add_post("/api/inbox/seen", h.api_inbox_seen)
    app.router.add_post("/api/inbox/dismiss-all", h.api_inbox_dismiss_all)
    app.router.add_put("/api/inbox/{id}", h.api_inbox_update)
    return app


def _raise(world, title: str, **refs: str) -> str:
    """One attention row and its one notification, raised the way the approval path raises it."""
    return emit_attention_item(
        world.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title=title,
        refs=refs,
        store=world.store,
    )


def _bell(world, item_id: str) -> dict:
    """The notification the bell shows for the row, and there must be exactly one."""
    notes = [n for n in world.state._notification_log if n.get("inbox_item") == item_id]
    assert len(notes) == 1, f"expected one notification for {item_id}, found {len(notes)}"
    return notes[0]


def _frames(world, kind: str) -> list[dict]:
    return [data for k, data in world.frames if k == kind]


def _acked_ts(world) -> list[str]:
    """Every notification the frames said became read."""
    out: list[str] = []
    for data in _frames(world, "notification_ack"):
        ts = data.get("ts")
        out.extend(ts if isinstance(ts, list) else [ts])
    return out


# ── the doors the Inbox page uses ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handling_a_note_in_the_inbox_reads_its_bell_notification(world):
    """The measured defect, driven through the two routes the Inbox page calls."""
    async with TestClient(TestServer(_app(world.state))) as http:
        made = await http.post("/api/inbox/notes", json={"text": "Call the plumber"})
        assert made.status == 201
        note_id = (await made.json())["id"]
        assert _bell(world, note_id).get("acked") is not True, "a new note starts unread"

        handled = await http.put(f"/api/inbox/{note_id}", json={"status": "handled"})
        assert handled.status == 200

    note = _bell(world, note_id)
    assert note.get("acked") is True, "the note was handled and its bell notification is unread"
    assert note["ts"] in _acked_ts(world), "the bell was never told to re-read"


@pytest.mark.asyncio
async def test_dismiss_all_reads_every_bell_notification_in_one_write(world):
    ids = [_raise(world, f"Approval needed: tool {n}", approval=f"a{n}") for n in range(3)]
    async with TestClient(TestServer(_app(world.state))) as http:
        swept = await http.post("/api/inbox/dismiss-all")
        assert (await swept.json())["dismissed"] == 3

    assert all(_bell(world, i).get("acked") is True for i in ids)
    acks = _frames(world, "notification_ack")
    assert len(acks) == 1, "one sweep is one bell update, not one per row"
    assert sorted(acks[0]["ts"]) == sorted(_bell(world, i)["ts"] for i in ids)
    # …and every open surface heard about every row it lost.
    updated = {f["id"] for f in _frames(world, "inbox_item_updated")}
    assert updated == set(ids)


@pytest.mark.asyncio
async def test_reading_a_row_leaves_its_bell_notification_alone(world):
    """Control, on both trees: SEEN is "I looked", not "I dealt with it". The row keeps its own
    unread dot for that, and the bell is not a second copy of it."""
    item_id = _raise(world, "Approval needed: bash", approval="a1")
    async with TestClient(TestServer(_app(world.state))) as http:
        seen = await http.post("/api/inbox/seen", json={"ids": [item_id]})
        assert (await seen.json())["seen"] == 1

    assert world.store.items[item_id].status == ItemStatus.SEEN.value
    assert _bell(world, item_id).get("acked") is not True
    assert [f["id"] for f in _frames(world, "inbox_item_updated")] == [item_id]


# ── the resolver every "the request stopped standing" path shares ───────────────────────────


def test_a_resolved_row_reads_its_bell_notification(world):
    """Approvals answered anywhere, workflow gates passed, loops resumed and rooms unpaused all
    close their rows through `resolve_attention_items`."""
    item_id = _raise(world, "Approval needed: bash", approval="a1")
    assert resolve_attention_items(world.state, {"approval": "a1"}) == 1
    assert _bell(world, item_id).get("acked") is True


def test_a_resolved_row_is_announced_to_every_open_surface(world):
    item_id = _raise(world, "Ship the release?", workflow="run-1", workflow_node="gate")
    world.frames.clear()
    assert resolve_attention_items(world.state, {"workflow": "run-1"}) == 1
    updated = _frames(world, "inbox_item_updated")
    assert [(f["id"], f["status"]) for f in updated] == [(item_id, "handled")]


def test_closing_one_row_reads_only_its_own_notification(world):
    mine = _raise(world, "Approval needed: bash", approval="a1")
    other = _raise(world, "Approval needed: curl", approval="a2")
    resolve_attention_items(world.state, {"approval": "a1"})
    assert _bell(world, mine).get("acked") is True
    assert _bell(world, other).get("acked") is not True


# ── a new row is announced where it lands ──────────────────────────────────────────────────


def test_raising_a_row_is_announced_to_every_open_surface(world):
    item_id = _raise(world, "Ship the release?", workflow="run-1", workflow_node="gate")
    assert [f["id"] for f in _frames(world, "inbox_new_item")] == [item_id]


def test_a_captured_note_is_announced_once(world):
    """The note route announced its row itself; the seam does now, and one frame is enough."""
    item_id = emit_attention_item(
        world.state,
        source="user",
        kind="note",
        item_kind=ItemKind.USER_NOTE.value,
        title="Call the plumber",
        store=world.store,
    )
    assert [f["id"] for f in _frames(world, "inbox_new_item")] == [item_id]


def test_a_captured_note_reaches_the_bell_without_a_toast(world):
    """A note's rule is `badge`: counted in the bell, never a toast. It was persisted with no
    frame at all, so the bell only counted it on its next poll."""
    emit_attention_item(
        world.state,
        source="user",
        kind="note",
        item_kind=ItemKind.USER_NOTE.value,
        title="Call the plumber",
        store=world.store,
    )
    kinds = [kind for kind, _data in world.frames]
    assert "notification_logged" in kinds, "the bell was not told a note was counted"
    assert "notification" not in kinds, "a badge note must not toast"


# ── a proposal's own decision ──────────────────────────────────────────────────────────────


def test_accepting_a_skill_proposal_reads_its_bell_notification(world, monkeypatch):
    """A decision made on the Skills page closes the row the Inbox mirrors it with."""
    from personalclaw.inbox_providers import native_source
    from personalclaw.skills import proposals as skill_proposals

    monkeypatch.setattr(native_source, "_dashboard_state", world.state)
    item_id = emit_attention_item(
        world.state,
        source="skills",
        kind="proposal",
        item_kind=ItemKind.PROPOSAL.value,
        title="New skill: editorial-document",
        refs={"skill_proposal": "p-1"},
        store=world.store,
    )
    skill_proposals._resolve_inbox_item("p-1", ItemStatus.HANDLED.value)
    assert world.store.items[item_id].status == ItemStatus.HANDLED.value
    assert _bell(world, item_id).get("acked") is True


# ── the one door ───────────────────────────────────────────────────────────────────────────


def test_a_status_cannot_be_written_around_the_transition(world):
    """`update(status=…)` closed the row and told nobody. It is refused, so a status can only
    move through `set_item_status`."""
    item_id = _raise(world, "Approval needed: bash", approval="a1")
    with pytest.raises(TypeError):
        world.store.update(item_id, status=ItemStatus.HANDLED.value)
    assert world.store.items[item_id].status == ItemStatus.PENDING.value
