"""PersonalClaw — personal AI agent with pluggable LLM providers.

Every ``python -m personalclaw.<module>`` child runs this file first, the sandbox's spawn shim
(``personalclaw._spawn_exec_shim``) on each command an agent runs included, so it imports
nothing at module level: asyncio and the installed-version read cost a child about 50 ms
before the shim's few lines run. Each is imported where it is used.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

# Version single-sourcing: pyproject.toml is the single source of
# truth. An installed package reports its version via importlib.metadata; a raw
# source-tree run (no dist-info, e.g. `python -m personalclaw` from a checkout
# without `pip install -e .`) falls back to the literal below. The consistency
# test (tests/test_version_consistency.py) asserts the three agree at release time.
_FALLBACK_VERSION = "0.3.3"


@functools.cache
def _version() -> str:
    """The installed version, read once, on the first ``__version__`` a caller asks for."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("personalclaw")
    except PackageNotFoundError:  # running from an uninstalled source tree
        return _FALLBACK_VERSION


if TYPE_CHECKING:
    __version__: str
else:
    # Only at run time: mypy never sees this ``__getattr__``, so a misspelled
    # ``personalclaw.<name>`` stays an error there and ``__version__`` stays a ``str``.
    def __getattr__(name: str) -> str:
        if name == "__version__":
            return _version()
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __dir__() -> list[str]:
        return sorted({*globals(), "__version__"})


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
        import asyncio

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
