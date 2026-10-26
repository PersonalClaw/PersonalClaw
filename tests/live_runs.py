"""Each workflow run a test starts is stopped before the test's event loop closes.

The gateway stops each of its runs before its loop closes (``WorkflowWatchdog.stop``). A test that
ends with a run still live hands it to the loop's close instead, which cancels whatever the run's
step is doing. On Python 3.12, a step that is starting a command then never ends: the close cancels
the start together with asyncio's own task connecting the child's pipes, and the start waits for
pipes that never connected (gh-119710, fixed in 3.13). Breadth's macOS 3.12 leg waited on the step's
900 s ceiling that way (run 40266675190).

``started(controller)`` notes a run a test started and hands it back. ``stop_started()`` stops each
one noted, on the loop still running: an async test's fixture calls it, and ``run(coro)`` is
``asyncio.run`` that calls it before its loop closes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

_started: list[Any] = []


def started(controller: T) -> T:
    """Note *controller*, a run the test started, so it is stopped before the loop closes."""
    _started.append(controller)
    return controller


async def stop_started() -> None:
    """Stop each run noted, as the gateway stops each of its runs. One whose loop is not this one
    went with its own loop, and is only forgotten."""
    loop = asyncio.get_running_loop()
    while _started:
        controller = _started.pop()
        task = controller._task
        if task is None or task.get_loop() is loop:
            await controller.stop()


def run(coro: Coroutine[Any, Any, T]) -> T:
    """``asyncio.run(coro)``, stopping each run noted while it ran before its loop closes."""

    async def stopping() -> T:
        try:
            return await coro
        finally:
            await stop_started()

    return asyncio.run(stopping())
