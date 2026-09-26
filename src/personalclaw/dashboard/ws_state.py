"""Dashboard WebSocket state and delivery helpers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

from aiohttp import web

from personalclaw import trace_recorder as _trace

logger = logging.getLogger(__name__)

#: The envelope type a `notify()` note arrives as. Named because `_broadcast` and the rail
#: test that pins its vocabulary must agree on the one string.
NOTE_TYPE_NOTIFICATION = "notification"

#: Every internal `_type` `_broadcast` knows how to translate. A value outside this set is a
#: producer bug: it used to be shipped as a raw `notification` blob, and is now dropped with an
#: ERROR. `tests/test_ws_broadcast_gate.py` asserts every `_type` literal in `src/` is in here.
BROADCAST_NOTE_TYPES = frozenset(
    {
        "sessions",
        "session_title",
        "refresh",
        "update_progress",
        "chat_message",
        NOTE_TYPE_NOTIFICATION,
    }
)


class DashboardWebSocketState:
    """WebSocket delivery state mixed into :class:`DashboardState`."""

    _flush_task: asyncio.Task | None  # type: ignore[type-arg]
    _sessions: dict[str, Any]
    _update_progress: dict[str, str] | None
    _ws_app: dict[web.WebSocketResponse, str]
    _ws_clients: list[web.WebSocketResponse]
    _ws_log_subscribers: set[web.WebSocketResponse]
    _ws_loop: asyncio.AbstractEventLoop | None
    _ws_subagent_subscribers: set[web.WebSocketResponse]
    _stream_seq: int

    def next_stream_seq(self) -> int:
        """Stamp the next streamed text chunk — the ``seq`` every ``chat_chunk`` carries.

        The stamp is the resume point for a client that rebuilds a live answer from a
        session-detail snapshot (a reload mid-answer, the session-create remount, a
        reconnect). The chat runner stamps, appends the chunk to the session's messages and
        broadcasts it in one synchronous step on the loop, and the detail handler reads its
        messages and :attr:`stream_seq` in one synchronous step too — so a snapshot reporting
        ``stream_seq = W`` reflects exactly the chunks stamped ``<= W``. The client drops a
        chunk stamped at or below the watermark it resumed from (already on screen) and
        keeps the rest, which is what makes the resumed answer whole and single.

        Process-wide rather than per session, so a session evicted from memory and
        rehydrated can never restart its numbering under a tab that still has it open. Nor
        does a gateway restart restart it: the count starts from the boot time in
        microseconds (``DashboardState.__init__``), above every stamp an earlier process
        handed out unless that one averaged a chunk per microsecond since its own boot. A
        tab outlives the process and keeps its watermark; a count restarting at zero would
        make it refuse the new process's chunks as already shown until a snapshot re-based
        it — and the reconnect's re-snapshot is a read that can fail."""
        self._stream_seq += 1
        return self._stream_seq

    @property
    def stream_seq(self) -> int:
        """The newest chunk stamp handed out (see :meth:`next_stream_seq`)."""
        return self._stream_seq

    def _broadcast(self, note: dict[str, Any]) -> None:
        """Fan a dashboard state note out to the WebSocket clients.

        The interactive dashboard surface runs on a single multiplexed
        WebSocket (see ``web/src/hooks/useWebSocket.ts``), so the dashboard's
        always-on concerns — status, session list/titles, notifications, and
        refresh hints — ride that one connection. This is the single-transport-
        per-concern doctrine: always-on state on the WS, page-scoped
        feeds (loops/logs/file-watch) on their own per-resource SSE.
        """
        # Dev-only event-trace tap (Self-Verification §2.1): capture the multiplexed WS
        # envelope keyed by its internal note type, before the live-client gate so a
        # headless recording sees every broadcast. No-op unless PERSONALCLAW_TRACE_DIR set.
        if _trace.is_recording():
            _trace.record("ws", str(note.get("_type", "notification")), "note", note)
        # Translate the internal `_type` into the WS envelope, then hand it to the gated
        # producer. This used to end in a raw fan-out helper that wrote to every socket
        # directly — so every always-on frame reached app-scoped sockets whatever their
        # manifest declared, while `broadcast_ws` right below it enforced exactly that.
        # That helper is gone (see `_dispatch_ws`): a producer cannot write without
        # naming an event type, and naming one means passing the gate.
        if not self._ws_clients:
            return
        msg_type = note.get("_type") or NOTE_TYPE_NOTIFICATION
        extra: dict[str, Any] | None = None
        if msg_type == "sessions":
            data: object = note.get("_sessions_list") or json.loads(note["sessions"])
            # Envelope keys preserved verbatim. No consumer for either appears in `web/`
            # today, but dropping a field as a side effect of a permissions fix is not this
            # change's business — if they are dead, they die in their own commit.
            extra = {
                "yolo": note.get("_yolo", False),
                "channelTrusted": note.get("channelTrusted", False),
            }
        elif msg_type == "session_title":
            data = {"key": note["key"], "title": note["title"]}
        elif msg_type == "refresh":
            data = {"kinds": note["kinds"].split(",")}
        elif msg_type == "update_progress":
            data = {"step": note["step"], "detail": note.get("detail", "")}
        elif msg_type == "chat_message":
            chat_data: dict[str, Any] = {
                "session": note["session"],
                "role": note["role"],
                "content": note["content"],
                "ts": note.get("ts", ""),
            }
            # Include cls for messages with metadata (e.g. permission with tool_input)
            if note.get("cls"):
                chat_data["cls"] = note["cls"]
            if note.get("meta"):
                chat_data["meta"] = note["meta"]
            data = chat_data
        elif msg_type == NOTE_TYPE_NOTIFICATION:
            # A note with no `_type` IS a notification — the notify() path. Explicit now,
            # because it used to share a `else:` branch with every unmapped value.
            data = note
        else:
            # 🔴 Dropped, not shipped. The old `else:` sent the raw internal note out as a
            # `notification`, so a typo'd or retired `_type` reached every client as an
            # untyped blob the frontend renders as a toast. An unmapped type is a producer
            # bug; `test_ws_note_types_are_all_mapped` turns it into a failing build rather
            # than a mystery frame, and this branch keeps the note off the wire meanwhile.
            logger.error(
                "dashboard note has an unmapped _type %r — dropped rather than broadcast as a "
                "raw notification; add it to _broadcast's translation",
                msg_type,
            )
            return
        self.broadcast_ws(msg_type, data, extra=extra)

    def _dispatch_ws(
        self, sockets: Iterable[web.WebSocketResponse], event_type: str, msg: str
    ) -> None:
        """Fan a pre-serialized envelope out to ``sockets`` — THE WS write path.

        Every gateway→client frame goes through here or through its awaited
        single-socket twin :meth:`send_ws_event`, and both open with
        :meth:`_ws_may_receive`. That is the entire point of the shape: this used to be
        ``_send_ws_all``, a raw "write to every registered client" helper with no
        permission check in it, and each producer that reached for a raw write — the
        always-on note translator, then the log-subscriber set and the subagent-
        subscriber set — became another place the app-event gate was simply absent
        (issue 2963: an app that declared one private event received the full backend
        log stream and the on-connect session list). There is no ungated primitive left
        to reach for, so a new producer cannot forget the gate; it has to pass an event
        type to get a frame on the wire.

        Safe to call from ANY thread: each send is scheduled by
        :meth:`_schedule_ws_send` (ensure_future on the gateway loop,
        run_coroutine_threadsafe off it), which the ring log handler and the
        subagent/cron threads depend on. Closed or unwritable sockets are reaped in the
        same call."""
        dead: list[web.WebSocketResponse] = []
        for ws in list(sockets):
            if ws.closed:
                dead.append(ws)
                continue
            if not self._ws_may_receive(ws, event_type):
                continue
            try:
                if not self._schedule_ws_send(ws.send_str(msg), ws):
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._remove_ws(ws)

    async def send_ws_event(
        self,
        ws: web.WebSocketResponse,
        event_type: str,
        data: object,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Send ONE event to ONE socket, through the same gate as :meth:`_dispatch_ws`.

        The connect-time replays — the ``sessions`` push, the log ring, the subagent
        snapshot — run on that socket's own handler task and await their writes so a
        thousand-entry replay applies backpressure instead of scheduling a thousand
        tasks. Awaiting is the ONLY thing that differs from the fan-out path; the
        permission check is the same call. Each of those three replays used to call
        ``ws.send_json`` itself, which is how an app-scoped socket received the session
        list and the whole log ring it never declared (issue 2963)."""
        if not self._ws_may_receive(ws, event_type):
            return
        envelope: dict[str, Any] = {"type": event_type, "data": data}
        if extra:
            # Envelope keys only — `type`/`data` stay owned by this method so a caller
            # cannot rename the event out from under the permission check.
            envelope.update({k: v for k, v in extra.items() if k not in ("type", "data")})
        try:
            await ws.send_str(json.dumps(envelope))
        except Exception as exc:
            logger.debug("WS send failed (client gone?): %s", exc)
            self._remove_ws(ws)

    def _ws_may_receive(self, ws: web.WebSocketResponse, event_type: str) -> bool:
        """THE app-event gate: may THIS socket receive ``event_type``?

        An owner/dashboard socket — no app identity in ``_ws_app`` — receives
        everything, exactly as before. An app-scoped socket (sandbox P1) receives an
        event ONLY if the app's manifest declares it in ``permissions.events``: deny by
        default, including when the manifest cannot be read at all. Server-side
        enforcement, because the SDK's client-side filter is advisory and the Store
        shows that declared list as the install-consent surface."""
        app = self._ws_app.get(ws, "")
        if not app:
            return True
        return self._app_may_see_event(app, event_type)

    def _schedule_ws_send(  # type: ignore[no-untyped-def]
        self, coro, ws: "web.WebSocketResponse | None" = None
    ) -> bool:
        """Schedule a WS send coroutine on the right loop from any thread.

        Returns False (so the caller can drop the client) only on an actual
        scheduling error. On the gateway loop → ensure_future; off-loop → submit to
        the captured loop with run_coroutine_threadsafe; no loop anywhere (sync
        startup/tests) → close the coroutine cleanly so it isn't reported unawaited.

        The send is wrapped in :meth:`_send_guarded` on BOTH scheduling branches:
        a fire-and-forget task's exception is never retrieved, so a client that
        disconnects mid-broadcast (navigating away from a streaming response — a
        completely routine event) used to surface as asyncio's unretrieved-task
        ERROR traceback. The guard turns it into a DEBUG line and reaps the client
        immediately instead of on the NEXT broadcast's ``ws.closed`` check."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is not None:
                asyncio.ensure_future(self._send_guarded(coro, ws))
            elif self._ws_loop is not None and not self._ws_loop.is_closed():
                asyncio.run_coroutine_threadsafe(self._send_guarded(coro, ws), self._ws_loop)
            else:
                coro.close()  # no loop available — can't deliver; avoid unawaited warning
            return True
        except Exception:
            try:
                coro.close()
            except Exception:
                pass
            return False

    async def _send_guarded(  # type: ignore[no-untyped-def]
        self, coro, ws: "web.WebSocketResponse | None"
    ) -> None:
        """Await one WS send, absorbing delivery failures.

        Nothing above this can act on a failed send — the correct response to ANY
        send error is to log quietly and drop the client, so a disconnect during an
        active broadcast never escalates past DEBUG."""
        try:
            await coro
        except Exception as exc:
            logger.debug("WS send failed (client gone?): %s", exc)
            if ws is not None:
                self._remove_ws(ws)

    def broadcast_ws(
        self, msg_type: str, data: object, *, extra: dict[str, Any] | None = None
    ) -> None:
        """Send a typed message to all WS clients (not SSE). THE one WS producer.

        Owner/dashboard connections get every event. An app-scoped connection
        (sandbox P1) gets an event ONLY if the app's manifest declares it in
        ``permissions.events`` — server-side enforcement so an untrusted app can't
        observe events it didn't ask for (the SDK's client-side filter is advisory).

        ``extra`` merges additional TOP-LEVEL envelope keys, which exists so the
        dashboard-state translator (`_broadcast`) can route through this filter instead of
        writing to the sockets itself. It had its own raw fan-out call, so every
        always-on frame — sessions, titles, refresh hints, chat messages, notifications —
        reached app-scoped sockets regardless of what the app declared. One gate for
        every producer; a second write path is a second place for it to be missing
        from."""
        if not self._ws_clients:
            return
        envelope: dict[str, Any] = {"type": msg_type, "data": data}
        if extra:
            # Envelope keys only — `type`/`data` stay owned by this method so a caller
            # cannot rename the event out from under the permission check.
            envelope.update({k: v for k, v in extra.items() if k not in ("type", "data")})
        self._dispatch_ws(self._ws_clients, msg_type, json.dumps(envelope))

    def _app_may_see_event(self, app: str, event_type: str) -> bool:
        """Whether an app-scoped WS may receive ``event_type`` per its manifest."""
        try:
            from personalclaw.apps.permissions import checker_for

            checker = checker_for(app)
            return checker is not None and checker.can_use_event(event_type)
        except Exception:
            return False

    def register_ws(self, ws: web.WebSocketResponse, *, app: str = "") -> None:
        """Register a new WebSocket client.

        ``app`` scopes the connection to an installed app (sandbox P1): its events
        are filtered to the app's declared ``permissions.events`` in broadcast_ws.
        Empty (the owner/dashboard) receives the full stream."""
        # Capture the gateway loop so off-loop broadcast_ws callers (MCP tool
        # subprocess callbacks, subagent/cron threads) can schedule sends onto it.
        if self._ws_loop is None:
            try:
                self._ws_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        self._ws_clients.append(ws)
        if app:
            self._ws_app[ws] = app

    def unregister_ws(self, ws: web.WebSocketResponse) -> None:
        """Remove a WebSocket client on disconnect."""
        self._remove_ws(ws)

    def _remove_ws(self, ws: web.WebSocketResponse) -> None:
        """Remove a WS client from all subscriber lists."""
        try:
            self._ws_clients.remove(ws)
        except ValueError:
            pass
        self._ws_app.pop(ws, None)
        self._ws_log_subscribers.discard(ws)
        self._ws_subagent_subscribers.discard(ws)

    def subscribe_logs(self, ws: web.WebSocketResponse) -> bool:
        """Subscribe a WS client to ``log`` events; False if it may not receive them.

        An app-scoped socket that did not declare ``log`` is not added at all. The send
        gate would drop every frame anyway, so this is not the authorization — it keeps
        the server from formatting, and re-refusing, one frame per log record for a
        subscriber that can never be delivered to. The caller uses the answer to skip
        the ring replay for the same reason."""
        if not self._ws_may_receive(ws, "log"):
            return False
        self._ws_log_subscribers.add(ws)
        return True

    def unsubscribe_logs(self, ws: web.WebSocketResponse) -> None:
        """Unsubscribe a WS client from log events."""
        self._ws_log_subscribers.discard(ws)

    def subscribe_subagents(self, ws: web.WebSocketResponse) -> bool:
        """Subscribe a WS client to the subagent chunk stream; False if not permitted.

        ``subagent_chunk`` is the only event this set delivers (the snapshot/done replay
        is sent per-socket), so it is the one type the subscription can be judged on —
        and, as with :meth:`subscribe_logs`, the send gate remains the authority."""
        if not self._ws_may_receive(ws, "subagent_chunk"):
            return False
        self._ws_subagent_subscribers.add(ws)
        return True

    def unsubscribe_subagents(self, ws: web.WebSocketResponse) -> None:
        self._ws_subagent_subscribers.discard(ws)

    def broadcast_ws_subagent_subscribers(self, msg_type: str, data: object) -> None:
        """Send to subagent-subscribed clients only (for heavy chunk data).

        Thread-safe: subagent chunk events originate off the gateway loop, so each send
        is scheduled onto the captured loop when needed."""
        if not self._ws_subagent_subscribers:
            return
        self._dispatch_ws(
            self._ws_subagent_subscribers, msg_type, json.dumps({"type": msg_type, "data": data})
        )

    def broadcast_ws_log_subscribers(self, data: object) -> None:
        """Send one ``log`` frame to the log-subscribed sockets.

        The ring log handler's producer. It used to walk ``_ws_log_subscribers`` and
        write to each socket itself, so nothing on that path ever consulted the app's
        declared events and an app-scoped subscriber got the owner's whole log stream
        (issue 2963). Callable from any thread, like the subagent fan-out."""
        if not self._ws_log_subscribers:
            return
        self._dispatch_ws(
            self._ws_log_subscribers, "log", json.dumps({"type": "log", "data": data})
        )

    async def close_all_ws(self) -> None:
        """Close all WebSocket connections (called on shutdown)."""
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_clients.clear()
        self._ws_log_subscribers.clear()
        self._ws_subagent_subscribers.clear()
