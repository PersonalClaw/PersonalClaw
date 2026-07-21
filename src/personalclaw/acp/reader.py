"""FrameRouter — the single-reader stdout demux for concurrent ACP sessions (P9).

Today an :class:`AcpClient` reads one backend process's stdout *inline* during a turn,
matching frames by ``req_id`` only and serializing turns behind a process-wide lock —
so one process serves one session. The P9 spike confirmed a default-dialect backend
actually INTERLEAVES multiple sessions on one process (distinct ``sessionId``s,
overlapping frames), so the bottleneck is purely PClaw's inline reader.

``FrameRouter`` owns the stdout loop as ONE long-lived coroutine and demuxes every
inbound JSON-RPC frame:

* ``msg.id`` matches a pending request  → resolve that request's Future (responses to
  our ``session/new`` / ``session/prompt`` / config calls).
* else ``params["sessionId"]`` is known → push the frame onto that session's queue
  (``session/update`` chunks, ``session/request_permission``, per-session errors).
* else (no id, no known session)        → hand to an optional broadcast sink
  (id-less notifications, or a frame for a session we haven't registered yet).

This is the pure routing substrate; the connection/session split that consumes it
lands next. The router itself holds no turn lock — concurrency comes from N sessions
each awaiting their own queue while the one reader fans frames out.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from personalclaw.acp.types import JsonRpcMessage

logger = logging.getLogger(__name__)

# Per-session queue bound — backpressure so one slow/abandoned session can't grow an
# unbounded buffer. A consumer that falls this far behind is almost certainly gone;
# the router drops-oldest (the stream is advisory chunks, not a ledger) and logs.
_SESSION_QUEUE_MAX = 2048


class FrameRouter:
    """Owns one ACP process's stdout; demuxes frames to per-session queues + pending
    response futures. Construct with an async line source (``readline() -> bytes``);
    call :meth:`start` to run the reader, :meth:`register_session` before prompting a
    session, and :meth:`await_response` around a request you've written."""

    def __init__(
        self,
        readline: Callable[[], Awaitable[bytes]],
        *,
        on_broadcast: Callable[[JsonRpcMessage], None] | None = None,
        on_server_request: Callable[[JsonRpcMessage], None] | None = None,
    ) -> None:
        self._readline = readline
        self._on_broadcast = on_broadcast
        # A server→client REQUEST (has both id AND method — e.g. session/request_permission
        # or fs/read) is NOT a response; route by session if it carries one, else here.
        self._on_server_request = on_server_request
        self._sessions: dict[str, asyncio.Queue[JsonRpcMessage]] = {}
        self._pending: dict[Any, asyncio.Future[JsonRpcMessage]] = {}
        # Responses whose id arrived BEFORE the caller registered expect() — stashed so
        # expect() can resolve immediately instead of dropping the reply (a real race: the
        # single reader can route a fast response between the request write and expect()).
        self._early_responses: dict[Any, JsonRpcMessage] = {}
        self._reader_task: asyncio.Task | None = None
        self._closed = False
        self._closed_exc: Exception | None = None

    # ── session registry ────────────────────────────────────────────────────
    def register_session(self, session_id: str) -> asyncio.Queue[JsonRpcMessage]:
        """Register a session id → returns its bounded frame queue (idempotent)."""
        q = self._sessions.get(session_id)
        if q is None:
            q = asyncio.Queue(maxsize=_SESSION_QUEUE_MAX)
            self._sessions[session_id] = q
        return q

    def unregister_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    # ── request/response correlation ────────────────────────────────────────
    def expect(self, req_id: Any) -> asyncio.Future[JsonRpcMessage]:
        """Register a pending request id → a Future the reader resolves with its
        response. Call BEFORE writing the request; if the response somehow already
        arrived (fast reply routed before this call), resolve immediately from the
        early-response stash so no reply is ever lost."""
        fut: asyncio.Future[JsonRpcMessage] = asyncio.get_event_loop().create_future()
        early = self._early_responses.pop(req_id, None)
        if early is not None:
            fut.set_result(early)
            return fut
        self._pending[req_id] = fut
        return fut

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.ensure_future(self._run())

    async def close(self, exc: Exception | None = None) -> None:
        """Stop the reader and fail every pending future + wake every session queue so
        no consumer hangs. ``exc`` (e.g. process death) is propagated to pending futures."""
        self._closed = True
        self._closed_exc = exc
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc or ConnectionError("ACP connection closed"))
        self._pending.clear()
        # Wake session consumers with a poison frame so their awaits return.
        for q in self._sessions.values():
            self._offer(q, JsonRpcMessage(method="_router/closed"))

    # ── the single reader loop ────────────────────────────────────────────────
    async def _run(self) -> None:
        try:
            while not self._closed:
                line = await self._readline()
                if not line:
                    await self.close(ConnectionError("ACP stdout EOF"))
                    return
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue  # non-JSON line (stray log) — skip, keep reading
                if not isinstance(raw, dict):
                    continue
                self._route(
                    JsonRpcMessage(
                        id=raw.get("id"),
                        method=raw.get("method"),
                        result=raw.get("result"),
                        error=raw.get("error"),
                        params=raw.get("params"),
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # reader died unexpectedly — fail cleanly, never hang
            logger.warning("ACP FrameRouter reader loop died", exc_info=True)
            await self.close(exc)

    def _route(self, msg: JsonRpcMessage) -> None:
        # 1. A RESPONSE to one of our requests (id present, not a server method call).
        if msg.id is not None and msg.method is None:
            fut = self._pending.pop(msg.id, None)
            if fut is not None:
                if not fut.done():
                    fut.set_result(msg)
            else:
                # Response arrived before expect() registered — stash it (bounded) so
                # the awaiting caller resolves immediately when it does register.
                if len(self._early_responses) < 256:
                    self._early_responses[msg.id] = msg
            return
        sid = None
        if isinstance(msg.params, dict):
            sid = msg.params.get("sessionId")
        # 2. A server→client REQUEST (id + method): permission prompt, fs op, etc.
        #    Route to the owning session if known, else the server-request handler.
        if msg.id is not None and msg.method is not None:
            if sid is not None and sid in self._sessions:
                self._offer(self._sessions[sid], msg)
            elif self._on_server_request is not None:
                self._on_server_request(msg)
            return
        # 3. A session-scoped notification (session/update etc.).
        if sid is not None and sid in self._sessions:
            self._offer(self._sessions[sid], msg)
            return
        # 4. Anything else (id-less broadcast, or a frame for an unregistered session).
        if self._on_broadcast is not None:
            self._on_broadcast(msg)

    @staticmethod
    def _offer(q: asyncio.Queue[JsonRpcMessage], msg: JsonRpcMessage) -> None:
        """Non-blocking enqueue with drop-oldest backpressure (advisory stream, not a
        ledger) — a wedged consumer can't stall the single reader for everyone else."""
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # drop the oldest frame
                q.put_nowait(msg)
                logger.warning("ACP FrameRouter: session queue full — dropped oldest frame")
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
