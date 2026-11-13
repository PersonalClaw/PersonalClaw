"""The hold that the code-loop tests over one git step share.

Used by ``tests/test_a_code_loop_has_one_writer.py`` and
``tests/test_a_loop_that_ends_keeps_the_work_nobody_merged.py``. A code loop's scheduler pass
runs its git steps in a worker thread, so the gateway's event loop keeps answering while git
works. A test holds one step until it lets it go and does something on the event loop meanwhile:
answers a request, ends the loop, starts a turn. No clock decides when.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

#: How long a held step waits to be let go, and the test for it to begin: longer than any
#: timeout these tests run under (the suite's 30 s, the git loops' 60 s), so a slow machine
#: fails a row only on its own timeout. Either wait running out still fails it by name.
_CAP = 120.0


@pytest.fixture()
def hold():
    """The held step's two signals: it began, and the test lets it go. Both are set at the end
    in any case, so a row that failed leaves no thread waiting out :data:`_CAP`."""
    started, go = threading.Event(), threading.Event()
    yield started, go
    started.set()
    go.set()


def _on_a_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _held(
    monkeypatch: Any,
    name: str,
    started: threading.Event,
    go: threading.Event,
    *,
    answer_first: bool = False,
    when: Callable[..., bool] | None = None,
) -> SimpleNamespace:
    """Patch ``worktree.<name>`` so its first call is held until *go* is set. With *when*, only
    the calls it accepts count: the first of those is held, and the rest go straight through.

    That call records whether it runs on a thread with a running event loop and sets *started*.
    Off the event loop it then waits for *go* before the real call, or after it with
    *answer_first*; on the event loop it is not held, since nothing could run there to let it
    go. Later calls go straight through. Returns its record: ``on_loop``, ``calls``, and
    ``returned``, which is True once the first call has returned to its caller."""
    from personalclaw.loop import worktree

    real = getattr(worktree, name)
    seen = SimpleNamespace(on_loop=None, calls=0, returned=False)

    def held(*args: Any, **kwargs: Any) -> Any:
        if when is not None and not when(*args, **kwargs):
            return real(*args, **kwargs)
        seen.calls += 1
        if seen.calls > 1:
            return real(*args, **kwargs)
        seen.on_loop = _on_a_running_loop()
        if seen.on_loop:
            started.set()
            out = real(*args, **kwargs)
        elif answer_first:
            out = real(*args, **kwargs)
            started.set()
            _let_go(go, name)
        else:
            started.set()
            _let_go(go, name)
            out = real(*args, **kwargs)
        seen.returned = True
        return out

    monkeypatch.setattr(worktree, name, held)
    return seen


def _lock_waits(monkeypatch: Any, reached: threading.Event | None = None) -> threading.Event:
    """Wrap ``manager.git_lock`` so that *reached* is set when an acquire finds the lock taken:
    whoever asked then waits on the git that holds it. Returns *reached*."""
    from personalclaw.loop import manager

    if reached is None:
        reached = threading.Event()
    lock_of = getattr(manager, "git_lock", None)  # None in a tree with no git lock yet

    class _Watched:
        def __init__(self, lock):
            self.lock = lock

        async def __aenter__(self):
            if self.lock.locked():
                reached.set()
            await self.lock.acquire()

        async def __aexit__(self, *exc):
            self.lock.release()

    monkeypatch.setattr(
        manager, "git_lock", lambda loop_id: _Watched(lock_of(loop_id)), raising=False
    )
    return reached


def _let_go(go: threading.Event, name: str) -> None:
    if not go.wait(_CAP):
        raise AssertionError(f"the test never let {name} go")


async def _when(
    started: threading.Event, go: threading.Event, then: Callable[[], Awaitable[Any]]
) -> Any:
    """On the event loop: once the held step began, run *then*, let the step go, and return
    what *then* returned."""
    if not await asyncio.to_thread(started.wait, _CAP):
        go.set()
        raise AssertionError("the held step never began")
    try:
        return await then()
    finally:
        go.set()
