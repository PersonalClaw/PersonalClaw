"""One WebSocket producer, one permission gate — and an unmapped note never reaches a client.

`broadcast_ws` enforces per-app `permissions.events`: an app-scoped socket receives an event only
if the app's manifest declared it. `_broadcast` — the always-on dashboard translator (sessions,
titles, refresh hints, chat messages, notifications) — ended in `self._send_ws_all(...)`, which
writes to every socket directly. So the gate sat immediately below a second producer that skipped
it, and app-scoped sockets received every always-on frame regardless of what the app declared.

Its `else:` branch was the second half of the defect: any unrecognized `_type` was shipped as
`{"type": "notification", "data": <the raw internal note>}`, so a typo'd or retired type reached
every client as an untyped blob the frontend renders as a toast.

Both are asserted here **at the socket**, by reading what a fake app-scoped WS actually received —
not by inspecting the producer. And the vacuity case is the one that matters: a DECLARED event must
still arrive, or "filter everything" would pass every test above it.
"""

from __future__ import annotations

import json

import pytest


class _FakeWS:
    """Enough WebSocketResponse surface for the broadcast path, plus a sent-frame log."""

    def __init__(self):
        self.closed = False
        self.sent: list[str] = []

    def send_str(self, msg: str):
        self.sent.append(msg)

        async def _noop():
            return None

        return _noop()

    def types(self) -> list[str]:
        return [json.loads(m).get("type") for m in self.sent]


@pytest.fixture
def state(monkeypatch, tmp_path):
    """A DashboardState with the send path made synchronous and the real home untouched."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.dashboard.state import DashboardState

    st = DashboardState.__new__(DashboardState)
    st._ws_clients = []
    st._ws_app = {}
    st._notification_log = []
    # `_schedule_ws_send` normally hops onto the captured loop; here the coroutine is consumed
    # inline so the assertion reads frames that were really handed to the socket.
    monkeypatch.setattr(
        DashboardState,
        "_schedule_ws_send",
        lambda self, coro: (coro.close(), True)[1],
        raising=False,
    )
    monkeypatch.setattr(DashboardState, "_remove_ws", lambda self, ws: None, raising=False)
    return st


def _app_socket(state, app: str, declared_events: list[str], monkeypatch):
    """Register an app-scoped socket whose manifest declares exactly `declared_events`."""
    ws = _FakeWS()
    state._ws_clients.append(ws)
    state._ws_app[ws] = app

    class _Checker:
        def can_use_event(self, event_type: str) -> bool:
            return event_type in declared_events

    monkeypatch.setattr(
        "personalclaw.apps.permissions.checker_for", lambda _name: _Checker(), raising=False
    )
    return ws


def test_an_always_on_frame_is_now_gated_for_an_app_socket(state, monkeypatch):
    """The defect: `_broadcast`'s frames used to reach app sockets ungated."""
    ws = _app_socket(state, "an-app", ["chat_message"], monkeypatch)

    state._broadcast({"_type": "sessions", "sessions": json.dumps([{"key": "s1"}])})

    assert ws.types() == [], f"an undeclared always-on frame reached an app socket: {ws.types()}"


def test_a_DECLARED_event_still_arrives(state, monkeypatch):
    """Vacuity. A gate that drops everything would satisfy every other test here."""
    ws = _app_socket(state, "an-app", ["chat_message"], monkeypatch)

    state._broadcast(
        {"_type": "chat_message", "session": "s1", "role": "assistant", "content": "hi"}
    )

    assert ws.types() == ["chat_message"], f"a declared event was filtered out: {ws.types()}"
    body = json.loads(ws.sent[0])
    assert body["data"]["content"] == "hi", "the frame arrived but lost its payload"


def test_the_owner_dashboard_socket_still_gets_everything(state, monkeypatch):
    """An unscoped (owner) connection is not an app and must not be filtered."""
    owner = _FakeWS()
    state._ws_clients.append(owner)
    _app_socket(state, "an-app", [], monkeypatch)  # forces the slow, per-socket path

    state._broadcast({"_type": "refresh", "kinds": "crons,cron_history"})

    assert owner.types() == ["refresh"], f"the owner socket lost a frame: {owner.types()}"


def test_an_unmapped_note_type_is_dropped_rather_than_shipped(state, monkeypatch, caplog):
    """The `else:` half: a raw internal note must never reach a client as a notification."""
    import logging

    owner = _FakeWS()
    state._ws_clients.append(owner)

    with caplog.at_level(logging.ERROR, logger="personalclaw.dashboard.state"):
        state._broadcast({"_type": "definitely_not_a_type", "secret_internal_field": "leak-me"})

    assert owner.sent == [], f"an unmapped note was broadcast anyway: {owner.sent}"
    assert any(
        "unmapped _type" in r.message for r in caplog.records
    ), f"the drop was silent: {[r.message for r in caplog.records]}"


def test_a_notification_note_still_rides_the_wire(state):
    """A note with NO `_type` is the notify() path — explicit now, not a default branch."""
    owner = _FakeWS()
    state._ws_clients.append(owner)

    state._broadcast({"title": "Build finished", "body": "all green"})

    assert owner.types() == ["notification"]
    assert json.loads(owner.sent[0])["data"]["title"] == "Build finished"


def test_the_sessions_envelope_keys_survive_the_refactor(state):
    """`yolo`/`channelTrusted` are top-level envelope keys; a permissions fix must not eat them."""
    owner = _FakeWS()
    state._ws_clients.append(owner)

    state._broadcast(
        {"_type": "sessions", "sessions": json.dumps([]), "_yolo": True, "channelTrusted": True}
    )

    body = json.loads(owner.sent[0])
    assert body["yolo"] is True and body["channelTrusted"] is True, body


def test_extra_cannot_relabel_the_frame_a_client_receives(state, monkeypatch):
    """`extra` merges envelope keys only — `type` stays owned by the producer.

    Written the way it is because the obvious version was VACUOUS: the permission check reads the
    `msg_type` ARGUMENT, so an undeclared event is dropped whatever `extra` says, and the test
    passed with the guard removed. The real property is what the client is TOLD a delivered frame
    is: a socket must never receive a `sessions` payload labelled `chat_message`, or every
    consumer that branches on `type` is reading a lie.
    """
    ws = _app_socket(state, "an-app", ["sessions"], monkeypatch)

    state.broadcast_ws("sessions", [{"key": "s1"}], extra={"type": "chat_message", "yolo": True})

    assert ws.types() == ["sessions"], f"`extra` relabelled a delivered frame: {ws.types()}"
    assert json.loads(ws.sent[0])["yolo"] is True, "a legitimate envelope key was dropped"


def test_every_note_type_in_the_tree_is_mapped():
    """The rail that makes an unmapped type a failing BUILD rather than a mystery frame.

    Scans `src/` for `"_type": "<value>"` literals and requires each to be translatable. Without
    it, the drop above is only discovered by someone noticing a missing toast in production.
    """
    import re
    from pathlib import Path

    from personalclaw.dashboard.state import BROADCAST_NOTE_TYPES

    src = Path(__file__).resolve().parent.parent / "src"
    found: dict[str, str] = {}
    for path in src.rglob("*.py"):
        for m in re.finditer(r'"_type":\s*"([a-z_]+)"', path.read_text(encoding="utf-8")):
            found.setdefault(m.group(1), str(path.relative_to(src)))
    assert found, "the scan found no `_type` literals at all — it would pass vacuously"
    unmapped = {k: v for k, v in found.items() if k not in BROADCAST_NOTE_TYPES}
    # `metadata` and `archive` are session-JSONL record types, not dashboard notes; they never
    # reach `_broadcast`. Named here so the exemption is a decision on the record.
    unmapped = {k: v for k, v in unmapped.items() if k not in ("metadata", "archive")}
    assert not unmapped, f"note types no producer can translate: {unmapped}"
