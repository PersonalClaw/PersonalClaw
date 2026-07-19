"""Server-Sent Events substrate — the reusable transport for server→client
state and event streams.

PersonalClaw's realtime doctrine is
**single-transport-per-concern**: per-resource SSE for state/event streams,
WebSocket only for the chat token stream + terminal. This module is the SSE half:
a small hub that fans typed events out to every connected client of a stream.

A :class:`SseHub` owns a set of per-client queues. A producer calls
:meth:`SseHub.publish` with a named event + payload; every subscribed client
receives it. The HTTP handler side is :func:`stream_response`, which drains a
client's queue to the wire as ``event: <name>\\ndata: <payload>\\n\\n`` frames
and emits periodic keepalives.

Hubs are addressable so the same substrate serves every scope:

- a single **global** hub (the dashboard status + notification stream),
- **per-type** hubs (e.g. one for sessions, one for notifications), and
- **per-resource-id** hubs (e.g. ``loop:<id>``) created on demand and
  evicted when their last subscriber disconnects.

:class:`SseRegistry` manages the per-id hubs and their lifecycle.

Wire-format contract: ``publish(event, data)`` writes ``data`` verbatim when it
is already a ``str`` (the producer controls serialization — e.g. a bare
comma-joined ``refresh`` payload or a pre-serialized JSON sessions list), and
``json.dumps(data)`` otherwise. This lets a producer emit an exact byte-for-byte
frame when it needs full control of the wire payload.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from personalclaw import shutdown_event

logger = logging.getLogger(__name__)

# Per-client queue depth. A slow client that fills its queue drops further
# events rather than back-pressuring the producer (state changes are coalesced
# by a fresh full-snapshot push, so a dropped intermediate event is recovered by
# the next one).
_QUEUE_MAXSIZE = 100

# Keepalive cadence (seconds) when a client's queue is idle. A comment line
# (``: keepalive``) keeps the connection and any intermediary proxy from idling
# the stream out.
_KEEPALIVE_SECS = 15


class _Event:
    """One SSE frame: an event name and an already-resolved data string."""

    __slots__ = ("name", "data")

    def __init__(self, name: str, data: str) -> None:
        self.name = name
        self.data = data


class SseHub:
    """Fans named SSE events out to every subscribed client of one stream.

    A hub is transport-only: it holds no domain state, just the set of live
    client queues. Producers call :meth:`publish`; clients are served by
    :func:`stream_response`, which subscribes on connect and unsubscribes on
    disconnect.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[_Event]] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    def subscribe(self) -> asyncio.Queue[_Event]:
        """Register a client and return its dedicated event queue."""
        q: asyncio.Queue[_Event] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[_Event]) -> None:
        """Drop a client's queue (idempotent)."""
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def publish(self, event: str, data: Any) -> None:
        """Fan a named event out to all subscribed clients.

        ``data`` is written verbatim if it is already a ``str`` (the producer
        owns serialization); otherwise it is JSON-encoded.
        """
        payload = data if isinstance(data, str) else json.dumps(data)
        frame = _Event(event, payload)
        for q in self._queues:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                # Slow consumer — drop. The next full-state push recovers it.
                pass


class SseRegistry:
    """Owns per-resource-id :class:`SseHub` instances and their lifecycle.

    A per-id hub is created on first subscribe and evicted when its last
    subscriber disconnects, so an idle resource holds no memory. Use this for
    resource-scoped streams (e.g. ``loop:<id>``); for a fixed global or
    per-type stream, hold a single :class:`SseHub` directly instead.
    """

    def __init__(self) -> None:
        self._hubs: dict[str, SseHub] = {}

    def hub(self, key: str) -> SseHub:
        """Return the hub for ``key``, creating it if absent."""
        hub = self._hubs.get(key)
        if hub is None:
            hub = SseHub()
            self._hubs[key] = hub
        return hub

    def peek(self, key: str) -> SseHub | None:
        """Return the hub for ``key`` only if it already exists (no creation).

        Producers use this to publish without resurrecting a hub for a resource
        nobody is watching.
        """
        return self._hubs.get(key)

    def publish(self, key: str, event: str, data: Any) -> None:
        """Publish to ``key``'s hub iff it has live subscribers (else no-op)."""
        hub = self._hubs.get(key)
        if hub is not None:
            hub.publish(event, data)

    def _evict_if_empty(self, key: str, hub: SseHub) -> None:
        if hub.subscriber_count == 0 and self._hubs.get(key) is hub:
            del self._hubs[key]


async def stream_response(
    request: web.Request,
    hub: SseHub,
    *,
    on_connect: list[tuple[str, Any]] | None = None,
    periodic: "Periodic | None" = None,
    registry_evict: tuple[SseRegistry, str] | None = None,
    close_after_connect: bool = False,
) -> web.StreamResponse:
    """Serve one SSE client from ``hub`` until the connection or gateway closes.

    - ``on_connect`` — frames to replay immediately after the headers (e.g. a
      buffered snapshot), each a ``(event, data)`` pair with the same verbatim/
      JSON rule as :meth:`SseHub.publish`.
    - ``periodic`` — an optional generator of ``(event, data)`` frames pushed on
      a fixed cadence (e.g. the dashboard status heartbeat), interleaved with
      queued events.
    - ``registry_evict`` — ``(registry, key)`` to evict an empty per-id hub when
      this client disconnects.
    - ``close_after_connect`` — write the ``on_connect`` snapshot then close the
      stream, without subscribing to the hub. For a resource already in a terminal
      state (no further events will ever arrive) this avoids holding a connection
      open indefinitely.
    """
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    try:
        await resp.prepare(request)
    except (ConnectionResetError, ClientConnectionResetError):
        return resp

    async def _write(event: str, data: Any) -> bool:
        payload = data if isinstance(data, str) else json.dumps(data)
        try:
            await resp.write(f"event: {event}\ndata: {payload}\n\n".encode())
            return True
        except (ConnectionResetError, ClientConnectionResetError):
            return False

    for event, data in on_connect or []:
        if not await _write(event, data):
            return resp

    if close_after_connect:
        with contextlib.suppress(Exception):
            await resp.write_eof()
        # Evict the hub we may have just created (we never subscribed) so a terminal
        # item's stream doesn't leave an empty hub lingering in the registry.
        if registry_evict is not None:
            reg, key = registry_evict
            reg._evict_if_empty(key, hub)
        return resp

    q = hub.subscribe()
    next_periodic = 0.0
    try:
        while not shutdown_event.is_set():
            # Drain everything queued so far.
            drained = False
            while not q.empty():
                try:
                    frame = q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not await _write(frame.name, frame.data):
                    return resp
                drained = True

            # Periodic push (e.g. status heartbeat), rate-limited by its cadence.
            if periodic is not None:
                loop_now = asyncio.get_running_loop().time()
                if loop_now >= next_periodic:
                    ev, data = periodic.frame()
                    if not await _write(ev, data):
                        return resp
                    next_periodic = loop_now + periodic.interval

            # Wait for the next event or the keepalive/periodic deadline.
            timeout = _KEEPALIVE_SECS
            if periodic is not None:
                timeout = min(
                    timeout,
                    max(0.05, next_periodic - asyncio.get_running_loop().time()),
                )
            try:
                frame = await asyncio.wait_for(q.get(), timeout=timeout)
                if not await _write(frame.name, frame.data):
                    return resp
            except asyncio.TimeoutError:
                if periodic is None and not drained:
                    try:
                        await resp.write(b": keepalive\n\n")
                    except (ConnectionResetError, ClientConnectionResetError):
                        return resp
    except (ConnectionResetError, ClientConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.unsubscribe(q)
        if registry_evict is not None:
            reg, key = registry_evict
            reg._evict_if_empty(key, hub)
    return resp


class Periodic:
    """A fixed-cadence frame source for :func:`stream_response`.

    ``frame_fn`` is called each tick and returns the ``(event, data)`` to push;
    ``interval`` is the minimum seconds between pushes.
    """

    __slots__ = ("frame", "interval")

    def __init__(self, frame_fn, interval: float) -> None:
        self.frame = frame_fn
        self.interval = interval
