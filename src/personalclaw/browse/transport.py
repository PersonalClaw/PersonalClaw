"""The real CDP wire behind :class:`personalclaw.browse.cdp.CdpTransport`.

``browse/cdp.py`` defines the transport as a Protocol so the gate can be asserted on the
messages it writes rather than on a return value. That Protocol had no production
implementor: every proof of "a denied host produces zero ``Page.navigate``" ran against a
recording fake, and a fake cannot show that Chrome's own ``Page.frameNavigated`` reaches
:meth:`~personalclaw.browse.cdp.GatedCdpSession.handle_event` at all. This module is that
implementor — one WebSocket to one already-open page target, and the flat sessions the browser
attaches on it for that page's frames and workers, nothing more.

**Deliberately NOT here: launching a browser.** No Chrome discovery, no ``--user-data-dir``,
no process supervision. Two reasons, both from the design rather than from convenience:
*persistent per-site profiles* belong to the login handoff (``browse/handoff.py``), and it is
open whether the gateway shares the interactive chrome-devtools MCP's browser instead of starting
its own. Choosing either here would pre-empt that decision, so the caller supplies a page
target's ``webSocketDebuggerUrl`` and owns the process. Tests launch their own.

THE ONE STRUCTURAL CONSTRAINT, and the reason this is not fifty lines: **the read loop must
never await the event listener.** The listener is ``GatedCdpSession.handle_event``, and its
job on a denied redirect is to *send* — ``Page.stopLoading`` then ``Page.navigate`` — on this
same socket. Awaiting the listener inline from the frame reader would mean the only task that
can resolve that send's response future is parked inside the send. The teardown would hang
until its timeout, and the guard's enforcement would silently become a no-op on a live
browser while every fake-transport test stayed green. So there are two tasks: a reader that
only routes, and a dispatcher that only calls the listener. Events stay ordered because the
dispatcher is single and takes them off one queue.

The listener also blanks frames, and starts the tasks that watch or let load the frames and
workers the page starts; those tasks send on this socket too, each on the session the browser
attached for its target. A caller that must not act before the events read so far are judged awaits
:meth:`WebSocketCdpTransport.drain_events` — never from the listener itself, which is the only
task that can make that wait end.

Failure posture matches the session's: a dead socket, a CDP error reply and a lost response
all raise out of :meth:`WebSocketCdpTransport.send`, and ``GatedCdpSession`` turns a raising
send into a quarantine. A hung request is capped by ``request_timeout`` rather than waiting
forever, because "the browser never answered" must not read as "the navigation was fine".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

#: How long one CDP command waits for its reply before it is treated as a transport failure.
DEFAULT_REQUEST_TIMEOUT = 30.0

#: How long the WebSocket handshake to the debugger endpoint may take.
DEFAULT_OPEN_TIMEOUT = 10.0

#: What a command on a frame's or a worker's session fails with when the browser detaches that
#: session before it answers: the reply will never come, so the command fails at once rather than
#: at its timeout.
SESSION_DETACHED = "detached the frame's session"

#: The browser's event for a session it detached.
_DETACHED_FROM_TARGET = "Target.detachedFromTarget"


class CdpTransportError(RuntimeError):
    """The wire failed, or the browser answered a command with an error."""


class WebSocketCdpTransport:
    """A :class:`~personalclaw.browse.cdp.CdpTransport` over one page target's WebSocket.

    Connect to a **page** target's ``webSocketDebuggerUrl``, not the browser-level one:
    ``Page.*`` commands on the browser endpoint need a ``sessionId`` on every message, and
    carrying one here would put a routing field between the gate and the wire it is asserted
    on. The page's own commands never carry a ``sessionId``; only a target the browser attached
    on this socket (a frame or a worker of this page) is addressed by one.
    """

    def __init__(
        self,
        connection: Any,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._conn = connection
        self._request_timeout = request_timeout
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._listener: Callable[..., Awaitable[None]] | None = None
        self._events: asyncio.Queue[tuple[str, dict[str, Any], str | None]] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None
        # Which session each pending command was sent on, so a detach fails only that session's.
        self._session_of: dict[int, str] = {}
        # Events read off the socket, and events the listener has finished with: a drain waits
        # for the second to reach the first as it stood when the drain began.
        self._read_events = 0
        self._handled_events = 0
        # When the wire last moved (anything read or written, or an event handled), and the
        # signal that wakes a waiting drain whenever it does.
        self._last_progress = time.monotonic()
        self._progress = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────────────────────────

    @classmethod
    async def connect(
        cls,
        ws_url: str,
        *,
        open_timeout: float = DEFAULT_OPEN_TIMEOUT,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> WebSocketCdpTransport:
        """Open the socket and start reading. ``max_size=None`` because a serialized DOM
        or a screenshot arrives as one frame and the library's 1 MB default would truncate
        the page the agent is about to read.

        The import is function-local so ``browse`` stays importable (and the rest of its
        tests runnable) in an environment without ``websockets``.
        """
        import websockets

        connection = await websockets.connect(  # type: ignore[attr-defined]
            ws_url, max_size=None, open_timeout=open_timeout
        )
        transport = cls(connection, request_timeout=request_timeout)
        transport.start_reading()
        return transport

    def start_reading(self) -> None:
        """Spawn the reader and the dispatcher. Idempotent.

        Two tasks, not one — see the module docstring. Collapsing them deadlocks the
        teardown that enforces a denied redirect.
        """
        if self._reader is None:
            self._reader = asyncio.get_running_loop().create_task(self._read_loop())
        if self._dispatcher is None:
            self._dispatcher = asyncio.get_running_loop().create_task(self._dispatch_loop())

    async def close(self) -> None:
        """Stop both tasks, close the socket, and fail anything still waiting."""
        self._fail(CdpTransportError("the CDP transport was closed"))
        for task in (self._reader, self._dispatcher):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._reader = None
        self._dispatcher = None
        self._moved()
        with contextlib.suppress(Exception):
            await self._conn.close()

    async def __aenter__(self) -> WebSocketCdpTransport:
        self.start_reading()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ── the CdpTransport Protocol ────────────────────────────────────────────

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send one command and wait for ITS reply, matched by ``id``.

        Matching on ``id`` rather than "the next message that arrives" is what makes this
        safe to call from the dispatcher while events keep flowing: a ``Page.frameNavigated``
        arriving mid-teardown goes to the queue, not into this reply.

        ``session_id`` addresses a target the browser attached on this socket; left out, the
        command is the page's own and the message carries no ``sessionId`` at all.
        """
        if self._failure is not None:
            raise CdpTransportError(f"the CDP transport is unusable: {self._failure}")

        self._next_id += 1
        message_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
            self._session_of[message_id] = session_id
        try:
            try:
                await self._conn.send(json.dumps(message))
            except Exception as exc:
                self._pending.pop(message_id, None)
                raise CdpTransportError(f"CDP {method} could not be sent: {exc}") from exc
            self._moved()

            try:
                return await asyncio.wait_for(future, timeout=self._request_timeout)
            except asyncio.TimeoutError as exc:
                self._pending.pop(message_id, None)
                raise CdpTransportError(
                    f"CDP {method} got no reply in {self._request_timeout:g}s; treating the "
                    "browser's state as unknown rather than as a success"
                ) from exc
        finally:
            self._session_of.pop(message_id, None)

    def set_event_listener(self, listener: Callable[..., Awaitable[None]] | None) -> None:
        """Register the coroutine the dispatcher hands every CDP event to, as
        ``(method, params, session_id)``: ``session_id`` is the session the event came on, or
        ``None`` for the page's own."""
        self._listener = listener

    async def drain_events(self) -> None:
        """Return once every event read so far has been handed to the listener and handled.

        The target is fixed when the call begins, so a page that keeps reporting events cannot
        hold a drain forever. The bound is on progress, not on the total: it fails only when the
        wire has been silent (nothing read, nothing written) and no event was handled for twice
        the command timeout while events are still waiting. A listener step that is healthy
        either produces traffic or ends within one command's timeout.

        Raises :class:`RuntimeError` when called from the listener (that wait could never end),
        and :class:`CdpTransportError` when the dispatcher is gone or the bound ran out.
        """
        dispatcher = self._dispatcher
        if dispatcher is not None and asyncio.current_task() is dispatcher:
            raise RuntimeError("drain_events was called from the event listener")
        target = self._read_events
        bound = 2 * self._request_timeout
        while self._handled_events < target:
            if self._dispatcher is None or self._dispatcher.done():
                raise CdpTransportError("the CDP transport is closed")
            remaining = self._last_progress + bound - time.monotonic()
            if remaining <= 0:
                raise CdpTransportError(
                    f"no CDP event was judged and nothing moved on the wire for {bound:g}s"
                )
            signal = self._progress
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(signal.wait(), remaining)

    def _moved(self) -> None:
        """Note that the wire moved, and wake every waiting drain."""
        self._last_progress = time.monotonic()
        signal, self._progress = self._progress, asyncio.Event()
        signal.set()

    # ── the two loops ────────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        """Route frames. Replies resolve futures; events go on the queue. Never awaits the
        listener — that is the dispatcher's job, and the whole reason it exists."""
        try:
            async for raw in self._conn:
                self._moved()
                try:
                    message = json.loads(raw)
                except Exception:
                    logger.warning("browse: undecodable CDP frame ignored")
                    continue
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if message_id is not None:
                    future = self._pending.pop(message_id, None)
                    if future is None or future.done():
                        continue
                    error = message.get("error")
                    if error:
                        future.set_exception(
                            CdpTransportError(f"the browser rejected the command: {error}")
                        )
                    else:
                        future.set_result(message.get("result") or {})
                    continue
                method = message.get("method")
                if not method:
                    continue
                params = message.get("params") or {}
                sid = message.get("sessionId")
                sid = sid if isinstance(sid, str) and sid else None
                if method == _DETACHED_FROM_TARGET and isinstance(params, dict):
                    self._fail_session(params.get("sessionId"))
                self._read_events += 1
                self._events.put_nowait((str(method), params, sid))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(CdpTransportError(f"the CDP socket failed: {exc}"))
        else:
            self._fail(CdpTransportError("the CDP socket closed"))

    async def _dispatch_loop(self) -> None:
        """Hand events to the listener, one at a time, so ordering survives.

        A listener that raises is logged and the next event is still delivered: one bad
        event must not stop the guard from judging the following navigations.
        """
        while True:
            method, params, sid = await self._events.get()
            try:
                listener = self._listener
                if listener is not None:
                    await listener(method, params, sid)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("browse: CDP event listener raised on %s", method, exc_info=True)
            finally:
                self._handled_events += 1
                self._moved()

    def _fail_session(self, session_id: Any) -> None:
        """Fail every command still waiting on a session the browser detached: it never answers."""
        if not isinstance(session_id, str) or not session_id:
            return
        for message_id, sid in list(self._session_of.items()):
            if sid != session_id:
                continue
            future = self._pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_exception(
                    CdpTransportError(
                        f"the browser {SESSION_DETACHED} {session_id} before it answered"
                    )
                )

    def _fail(self, exc: BaseException) -> None:
        """Record the failure once and fail every waiting command with it."""
        if self._failure is None:
            self._failure = exc
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
