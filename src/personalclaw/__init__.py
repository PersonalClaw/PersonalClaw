"""PersonalClaw — personal AI agent with pluggable LLM providers."""

import asyncio

__version__ = "0.1.0"


class _ShutdownEvent:
    """Process-wide shutdown signal that rebinds to the running event loop.

    A plain module-level ``asyncio.Event()`` binds to whichever loop first
    touches it; awaiting it later from a different loop (e.g. the fresh loop
    ``asyncio.run()`` creates for the gateway, or a loop after an in-process
    restart) raises ``RuntimeError: got Future attached to a different loop``
    and crashes the gateway in a restart spiral.

    This proxy lazily (re)creates the underlying :class:`asyncio.Event` on the
    *current* running loop, so each ``asyncio.run()`` gets a fresh, correctly
    bound Event. A ``set()`` issued with no loop running is remembered and
    applied to the next Event built. Background loops use
    ``await shutdown_event.wait()`` (with a timeout) instead of plain
    ``asyncio.sleep()`` so they wake instantly on Ctrl-C.
    """

    def __init__(self) -> None:
        self._event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_set: bool = False

    def _get(self) -> asyncio.Event:
        """Return the Event bound to the running loop, rebuilding on loop change.

        Raises ``RuntimeError`` when called without a running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "shutdown_event cannot be accessed without a running event loop"
            ) from exc
        if self._event is None or self._loop is not loop:
            self._event = asyncio.Event()
            self._loop = loop
            if self._pending_set:
                self._event.set()
        return self._event

    async def wait(self) -> bool:
        return await self._get().wait()

    def set(self) -> None:
        """Set the event. Safe to call with no running loop (remembered)."""
        self._pending_set = True
        try:
            self._get().set()
        except RuntimeError:
            # No running loop yet — the pending flag applies it on next build.
            pass

    def clear(self) -> None:
        self._pending_set = False
        if self._event is not None:
            self._event.clear()

    def is_set(self) -> bool:
        if self._event is not None:
            return self._event.is_set()
        return self._pending_set


# Process-wide shutdown signal. Any background loop should use
# ``await shutdown_event.wait()`` (with a timeout) instead of plain
# ``asyncio.sleep()`` so it wakes instantly on Ctrl-C.
shutdown_event = _ShutdownEvent()
