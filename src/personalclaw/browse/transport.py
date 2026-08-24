"""The real CDP wire behind :class:`personalclaw.browse.cdp.CdpTransport` (BA-2).

``browse/cdp.py`` defines the transport as a Protocol so the gate can be asserted on the
messages it writes rather than on a return value. That Protocol had no production
implementor: every proof of "a denied host produces zero ``Page.navigate``" ran against a
recording fake, and a fake cannot show that Chrome's own ``Page.frameNavigated`` reaches
:meth:`~personalclaw.browse.cdp.GatedCdpSession.handle_event` at all. This module is that
implementor — one WebSocket to one already-open page target, nothing more.

**Deliberately NOT here: launching a browser.** No Chrome discovery, no ``--user-data-dir``,
no process supervision. Two reasons, both from the plan rather than from convenience:
BROWSE-AUTOMATION §5.1 gives *persistent per-site profiles* to BA-4, and §4.1 leaves open
whether the gateway shares the interactive chrome-devtools MCP's browser instead of starting
its own. Choosing either here would pre-empt an owner decision, so the caller supplies a page
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
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

#: How long one CDP command waits for its reply before it is treated as a transport failure.
DEFAULT_REQUEST_TIMEOUT = 30.0

#: How long the WebSocket handshake to the debugger endpoint may take.
DEFAULT_OPEN_TIMEOUT = 10.0


class CdpTransportError(RuntimeError):
    """The wire failed, or the browser answered a command with an error."""


class WebSocketCdpTransport:
    """A :class:`~personalclaw.browse.cdp.CdpTransport` over one page target's WebSocket.

    Connect to a **page** target's ``webSocketDebuggerUrl``, not the browser-level one:
    ``Page.*`` commands on the browser endpoint need a ``sessionId`` on every message, and
    carrying one here would put a routing field between the gate and the wire it is asserted
    on. One socket, one page, no session multiplexing.
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
        self._listener: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
        self._events: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._dispatcher: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None

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
        with contextlib.suppress(Exception):
            await self._conn.close()

    async def __aenter__(self) -> WebSocketCdpTransport:
        self.start_reading()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ── the CdpTransport Protocol ────────────────────────────────────────────

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one command and wait for ITS reply, matched by ``id``.

        Matching on ``id`` rather than "the next message that arrives" is what makes this
        safe to call from the dispatcher while events keep flowing: a ``Page.frameNavigated``
        arriving mid-teardown goes to the queue, not into this reply.
        """
        if self._failure is not None:
            raise CdpTransportError(f"the CDP transport is unusable: {self._failure}")

        self._next_id += 1
        message_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        payload = json.dumps({"id": message_id, "method": method, "params": params or {}})
        try:
            await self._conn.send(payload)
        except Exception as exc:
            self._pending.pop(message_id, None)
            raise CdpTransportError(f"CDP {method} could not be sent: {exc}") from exc

        try:
            return await asyncio.wait_for(future, timeout=self._request_timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise CdpTransportError(
                f"CDP {method} got no reply in {self._request_timeout:g}s; treating the "
                "browser's state as unknown rather than as a success"
            ) from exc

    def set_event_listener(
        self, listener: Callable[[str, dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        """Register the coroutine the dispatcher hands every CDP event to."""
        self._listener = listener

    # ── the two loops ────────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        """Route frames. Replies resolve futures; events go on the queue. Never awaits the
        listener — that is the dispatcher's job, and the whole reason it exists."""
        try:
            async for raw in self._conn:
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
                if method:
                    self._events.put_nowait((str(method), message.get("params") or {}))
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
            method, params = await self._events.get()
            listener = self._listener
            if listener is None:
                continue
            try:
                await listener(method, params)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("browse: CDP event listener raised on %s", method, exc_info=True)

    def _fail(self, exc: BaseException) -> None:
        """Record the failure once and fail every waiting command with it."""
        if self._failure is None:
            self._failure = exc
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
