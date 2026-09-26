"""A pending chat approval is ONE registry entry, and every surface reads that entry.

Measured on a running gateway before this existed: a chat sat at ``pending_approval=true`` while
``GET /api/approvals`` answered ``[]``, the Home approvals count read 0, and neither Home's
"To triage" nor the Inbox listed it. The agent was waiting on a user who was somewhere else, and
nothing anywhere else said so. Allow and Deny worked — but only for someone already looking at
that chat.

The cause was two registries. A background approval registered in
``DashboardState._pending_approvals`` (what ``/api/approvals`` serves); a chat approval parked a
future on its session and broadcast a separately composed frame, so every surface that reads the
registry was blind to the most common approval there is. The Inbox copy was a third path: raised
90 s late, and closed through a private ``InboxStore()`` that the live store never saw.

These tests drive the REAL chat runner to a real pending approval and then look at it from every
door — the listing, the Inbox row, the decision from the chat, the decision from elsewhere, and
the end of a turn nobody answered.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from test_dashboard_approval import (  # the file's own run_chat harness
    _complete_event,
    _context_builder,
    _make_session,
    _make_state,
    _set_stream,
)

from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.dashboard.chat import api_chat_session_approve, run_chat
from personalclaw.dashboard.handlers.sessions import api_approval_resolve, api_approvals
from personalclaw.inbox import OPEN_STATUSES, InboxStore, ItemKind, emit_attention_item
from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.sel import SecurityEventLog

CHAT = "chat-a"


def _bash_request(request_id: str = "req-1", command: str = "rm -rf /tmp/scratch") -> LLMEvent:
    """A destructive shell call, which is what the Normal posture asks about."""
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="bash",
        tool_kind="execute",
        request_id=request_id,
        tool_call_id=f"tc-{request_id}",
        tool_input=json.dumps({"command": command}),
    )


def _turn(*requests: LLMEvent) -> list[LLMEvent]:
    """A scripted turn that asks, then answers. The answer matters: a turn with no text reads as
    an empty response, and the runner's silent retry would re-ask the same request a second time.
    """
    return [*requests, LLMEvent(kind=EVENT_TEXT_CHUNK, text="Done."), _complete_event()]


def _app(state):
    app = _api_app(state)
    app.router.add_get("/api/approvals", api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", api_approval_resolve)
    app.router.add_post("/api/chat/sessions/{session}/approve", api_chat_session_approve)
    return app


@pytest.fixture
def world(tmp_path):
    """A real state with a live Inbox store, one chat bound to a named agent, and a frame log."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    frames: list[tuple[str, dict]] = []

    def _record(kind: str, data: dict | None = None) -> None:
        frames.append((kind, data or {}))

    state.broadcast_ws = _record  # type: ignore[method-assign]
    store = InboxStore(tmp_path / "inbox_items.json")
    # `live_store` is type-checked, so a real InboxStore is what makes this the API's store.
    state._inbox_svc = SimpleNamespace(inbox=store)
    session = _make_session(CHAT)
    session.agent = "researcher"
    session.title = "Clean the scratch dir"
    state._sessions[session.key] = session
    return SimpleNamespace(state=state, client=client, session=session, store=store, frames=frames)


async def _until(predicate, what: str) -> None:
    """Poll instead of sleeping a guessed interval, and fail with the cause named (#2563)."""
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never happened within 2s: {what}")


def _approval_rows(store: InboxStore, *, open_only: bool) -> list:
    return [
        i
        for i in store.items.values()
        if i.item_kind == ItemKind.AGENT_REQUEST.value
        and i.refs.get("approval")
        and (i.status in OPEN_STATUSES or not open_only)
    ]


async def _start(w, events) -> asyncio.Task:
    _set_stream(w.client, events)
    task = asyncio.create_task(run_chat(w.state, w.session, "clean up the scratch dir"))
    await _until(
        lambda: "req-1" in w.session._approval_futures, "the chat never asked for approval"
    )
    return task


async def _finish(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.CancelledError:
        pass


# ── the listing ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pending_chat_approval_is_listed_with_enough_context_to_act(world):
    """``GET /api/approvals`` names which chat, which agent, and what it wants to do."""
    task = await _start(world, _turn(_bash_request()))
    try:
        async with TestClient(TestServer(_app(world.state))) as http:
            rows = await (await http.get("/api/approvals")).json()
        assert len(rows) == 1, f"a pending chat approval is missing from /api/approvals: {rows}"
        row = rows[0]
        assert row["session"] == CHAT
        assert row["session_title"] == "Clean the scratch dir"
        assert row["agent"] == "researcher"
        assert row["tool"] == "bash"
        assert "rm -rf /tmp/scratch" in row["tool_input"]
        assert row["request_id"] == "req-1"
        assert row["risk"] == "destructive"
        assert row["is_read_only"] is False
        # One entry, two doors: the live frame the chat card renders IS the listed row, so the
        # card and every other surface cannot describe one call two ways.
        asked = [data for kind, data in world.frames if kind == "approval"]
        assert asked == [row], asked
    finally:
        await _finish(task)


@pytest.mark.asyncio
async def test_the_registry_id_is_unique_across_chats_that_reuse_a_request_id(world, tmp_path):
    """ACP request ids are per-connection JSON-RPC ids, so two chats can both be waiting on "1".

    A global list keyed by the bare id would let one chat's answer land on the other's call.
    """
    other = _make_session("chat-b")
    world.state._sessions[other.key] = other
    _set_stream(world.client, _turn(_bash_request()))
    first = asyncio.create_task(run_chat(world.state, world.session, "one"))
    second = asyncio.create_task(run_chat(world.state, other, "two"))
    try:
        await _until(
            lambda: "req-1" in world.session._approval_futures
            and "req-1" in other._approval_futures,
            "both chats asking",
        )
        rows = list(world.state._pending_approvals.values())
        assert sorted(r["session"] for r in rows) == ["chat-a", "chat-b"], rows
        assert len({r["id"] for r in rows}) == 2, "two pending approvals share one id"

        by_session = {r["session"]: r for r in rows}
        assert world.state.resolve_approval(by_session["chat-b"]["id"], False) is True
        assert not world.session._approval_futures["req-1"].done(), "chat-a was answered for chat-b"
    finally:
        await _finish(first)
        await _finish(second)


# ── the Inbox row is the registry entry, once ──────────────────────────────────────────────────


async def _hold(state, session, request_id: str = "req-1") -> str:
    await state.hold_session_approval(
        session,
        request_id,
        tool="bash",
        tool_input='{"command": "rm -rf /tmp/scratch"}',
        tool_purpose="",
        agent="researcher",
        risk="destructive",
        is_read_only=False,
        grant_agent="",
    )
    return chat_approval_id(session.key, request_id)


@pytest.mark.asyncio
async def test_one_approval_is_one_inbox_row_and_one_notification(world, monkeypatch):
    """A re-held approval must not stack rows or tell the user twice (the dedup key)."""
    notes: list[str] = []
    monkeypatch.setattr(
        world.state, "notify", lambda kind, title, body, meta=None: notes.append(kind)
    )
    first = await _hold(world.state, world.session)
    again = await _hold(world.state, world.session)
    assert first == again
    assert len(_approval_rows(world.store, open_only=True)) == 1
    assert len(notes) == 1, notes

    await _hold(world.state, world.session, "req-2")
    assert len(_approval_rows(world.store, open_only=True)) == 2, "two approvals share one row"


@pytest.mark.asyncio
async def test_an_unreachable_inbox_never_costs_the_user_the_approval(world, monkeypatch):
    """The decision is what the user is waiting on; losing it to a bookkeeping failure is worse
    than losing the row."""

    def _no_disk(*_a, **_k):
        raise OSError("no disk")

    monkeypatch.setattr("personalclaw.inbox.emit_attention_item", _no_disk)
    approval_id = await _hold(world.state, world.session)
    async with TestClient(TestServer(_app(world.state))) as http:
        rows = await (await http.get("/api/approvals")).json()
    assert [r["id"] for r in rows] == [approval_id]


# ── the Inbox, and deciding from the chat ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deciding_in_the_chat_clears_every_surface(world):
    task = await _start(world, _turn(_bash_request()))
    try:
        pending = world.state._pending_approvals
        rows = _approval_rows(world.store, open_only=True)
        assert len(rows) == 1, "the Inbox does not list the pending approval"
        row = rows[0]
        assert row.refs["session"] == CHAT
        assert [row.refs["approval"]] == list(pending), "the Inbox row is not the registry entry"
        assert "bash" in row.message and "researcher" in row.message

        async with TestClient(TestServer(_app(world.state))) as http:
            resp = await http.post(
                f"/api/chat/sessions/{CHAT}/approve",
                json={"action": "approved", "request_id": "req-1"},
            )
            assert resp.status == 200
            await asyncio.wait_for(task, timeout=5)
            assert await (await http.get("/api/approvals")).json() == []

        assert not _approval_rows(world.store, open_only=True), "the Inbox row outlived the answer"
        assert world.store.items[row.id].status == "handled"
        resolved = [d for kind, d in world.frames if kind == "approval_resolved"]
        assert resolved == [
            {
                "id": row.refs["approval"],
                "request_id": "req-1",
                "session": CHAT,
                "approved": True,
                "outcome": "approved",
            }
        ], resolved
        world.client.approve_tool.assert_called_once_with("req-1")
    finally:
        await _finish(task)


# ── deciding from anywhere else goes through the chat's own decision path ───────────────────────


def _decisions(audit: list) -> list[tuple[str, str, str, str]]:
    return [
        (e.caller_identity, e.operation, e.outcome, e.resources)
        for e in audit
        if e.event_type == "api_access" and e.operation.startswith("tool_approval:")
    ]


@pytest.mark.asyncio
async def test_deciding_outside_the_chat_reaches_the_chat_through_the_same_path(world, monkeypatch):
    """Deny from Home/the Inbox: the chat records it, the agent is refused, and it cannot route
    around the refusal with a second call — the same protection an in-chat Deny gives."""
    audit: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: audit.append(event))
    task = await _start(
        world,
        _turn(
            _bash_request("req-1", "rm -rf /tmp/scratch"),
            # The agent's attempt to route around a refusal: same effect, different call.
            _bash_request("req-2", "find /tmp/scratch -delete"),
        ),
    )
    try:
        async with TestClient(TestServer(_app(world.state))) as http:
            rows = await (await http.get("/api/approvals")).json()
            assert [r["request_id"] for r in rows] == ["req-1"], rows
            resp = await http.post(f"/api/approvals/{rows[0]['id']}/reject")
            assert resp.status == 200
            await asyncio.wait_for(task, timeout=5)
            assert await (await http.get("/api/approvals")).json() == []

        # The chat reflects the decision in its permanent record…
        recorded = [
            json.loads(m["cls"]).get("resolved")
            for m in world.session.messages
            if m.get("role") == "permission" and json.loads(m["cls"]).get("request_id") == "req-1"
        ]
        assert recorded == ["rejected"], recorded
        # …the agent was refused, and so was the call that tried to route around it, without the
        # user being asked a second time.
        world.client.approve_tool.assert_not_called()
        world.client.reject_tool.assert_any_call("req-1")
        world.client.reject_tool.assert_any_call("req-2")
        asked = [d["request_id"] for kind, d in world.frames if kind == "approval"]
        assert asked == ["req-1"], asked
        assert not _approval_rows(world.store, open_only=True)
        # The same audit row the chat's own Deny writes.
        assert _decisions(audit) == [
            (f"dashboard:{CHAT}", "tool_approval:rejected", "rejected", "req-1")
        ], _decisions(audit)
    finally:
        await _finish(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["chat", "elsewhere"])
async def test_both_doors_write_one_audit_row(world, monkeypatch, door):
    audit: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: audit.append(event))
    task = await _start(world, _turn(_bash_request()))
    try:
        async with TestClient(TestServer(_app(world.state))) as http:
            if door == "chat":
                await http.post(
                    f"/api/chat/sessions/{CHAT}/approve",
                    json={"action": "approved", "request_id": "req-1"},
                )
            else:
                (row,) = await (await http.get("/api/approvals")).json()
                await http.post(f"/api/approvals/{row['id']}/approve")
            await asyncio.wait_for(task, timeout=5)
        assert _decisions(audit) == [
            (f"dashboard:{CHAT}", "tool_approval:approved", "approved", "req-1")
        ]
        world.client.approve_tool.assert_called_once_with("req-1")
    finally:
        await _finish(task)


# ── an approval nobody answers ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unanswered_approval_leaves_every_surface_when_its_turn_ends(world):
    task = await _start(world, _turn(_bash_request()))
    # Vacuity floor: it was on every surface while it was pending.
    assert world.state._pending_approvals
    assert _approval_rows(world.store, open_only=True)
    (approval_id,) = list(world.state._pending_approvals)

    await _finish(task)  # the user pressed Stop, navigated away, or the gateway is shutting down

    assert world.state._pending_approvals == {}
    assert not _approval_rows(world.store, open_only=True)
    resolved = [d for kind, d in world.frames if kind == "approval_resolved"]
    assert resolved == [
        {
            "id": approval_id,
            "request_id": "req-1",
            "session": CHAT,
            "approved": False,
            "outcome": "cancelled",
        }
    ], resolved


@pytest.mark.asyncio
async def test_a_turn_torn_down_mid_publication_leaves_nothing_listed(world, monkeypatch):
    """The `ApprovalRequest` hook runs a user's script, so publication can be slow — and Stop can
    land inside it. The registry row is written BEFORE the hook, so that cancel must still be the
    waiter's to clean up, or the approval stays on Home and in the Inbox with nobody waiting."""
    import personalclaw.triggers.lifecycle_fire as lifecycle

    in_hook = asyncio.Event()

    async def _slow_hook(payload, **_k):
        # Only the approval's own event; a turn fires other lifecycle events before it asks.
        if payload.get("event") != "ApprovalRequest":
            return
        in_hook.set()
        await asyncio.Event().wait()  # a hook that never returns on its own

    monkeypatch.setattr(lifecycle, "fire", _slow_hook)
    _set_stream(world.client, _turn(_bash_request()))
    task = asyncio.create_task(run_chat(world.state, world.session, "clean up"))
    await asyncio.wait_for(in_hook.wait(), timeout=5)
    # Vacuity floor: it WAS listed on every surface when the cancel landed.
    assert world.state._pending_approvals
    assert _approval_rows(world.store, open_only=True)

    await _finish(task)

    assert world.state._pending_approvals == {}
    assert not _approval_rows(world.store, open_only=True)
    assert "req-1" not in world.session._approval_futures


def test_a_restart_closes_the_rows_whose_approval_did_not_survive_it(world):
    """No approval future survives a restart, so a row asking for one is asking for nothing."""
    stale = emit_attention_item(
        world.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="Approval needed: bash",
        refs={"approval": "chat-a:req-0", "session": CHAT},
        store=world.store,
    )
    world.state._pending_approvals["chat-a:req-7"] = {"id": "chat-a:req-7"}
    live = emit_attention_item(
        world.state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="Approval needed: bash",
        refs={"approval": "chat-a:req-7", "session": CHAT},
        store=world.store,
    )

    assert world.state.close_orphaned_approval_rows() == 1
    assert world.store.items[stale].status == "handled"
    assert world.store.items[live].status == "pending", "a still-pending approval lost its row"


# ── a background approval is the same registry entry ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_background_approval_reaches_the_inbox_and_leaves_it(world):
    task = asyncio.create_task(
        world.state.request_approval(
            "sub-1", "subagent", "Bash", tool_input="rm -rf /tmp/x", session=CHAT
        )
    )
    try:
        await _until(lambda: "sub-1" in world.state._pending_approvals, "registered")
        rows = _approval_rows(world.store, open_only=True)
        assert [r.refs["approval"] for r in rows] == ["sub-1"]
        assert world.state.resolve_approval("sub-1", False) is True
        assert await asyncio.wait_for(task, timeout=5) is False
        assert not _approval_rows(world.store, open_only=True)
    finally:
        await _finish(task)
