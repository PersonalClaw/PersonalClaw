"""A wait for the work held in the background ends once that work has.

🔴 The defect. The gateway holds what it starts in the background in a set its tasks leave by a
done callback (``task.add_done_callback(held.discard)``), and every wait for that work looped
until the set emptied::

    while held:
        await asyncio.gather(*held, return_exceptions=True)

A task that has just finished stays in the set until its callback runs, on the loop's next pass,
and asyncio completes a gather of finished tasks at once, without giving the loop a pass. So when
the last task ended in the same pass the wait resumed in, the loop never ran again and the wait
spun forever. The webhook automations' tests met it on loaded CI runners (each fire's task ends
in the pass its HTTP answer arrives in) and timed out at 120 s; they pass on an idle machine.

Every wait now goes through ``cancellation.settle``, which waits for the tasks rather than for the
set to empty, and a rail below keeps a loop on a set's membership out of the tree. Each wait here
runs on a loop of its own, on a thread: a wait that spins never yields to its loop, so nothing on
that loop could stop it, and a spinning wait must fail this test rather than hang it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine

import pytest
from a_wait_for_background_work_ends_when_the_work_has_shared import _waits_for_a_set_to_empty

from personalclaw.cancellation import settle

#: Long enough for an idle wait on a loaded host; a spinning one never ends.
_BOUND_SECS = 20.0


def _ends(work: Callable[[], Coroutine[Any, Any, None]]) -> bool:
    """Whether *work*, run to its end on a loop of its own, ended within the bound."""
    outcome: dict[str, BaseException] = {}

    def run() -> None:
        try:
            asyncio.run(work())
        except BaseException as exc:  # noqa: BLE001 - handed back to the test below
            outcome["error"] = exc

    waiter = threading.Thread(target=run, name="held-work-wait", daemon=True)
    waiter.start()
    waiter.join(_BOUND_SECS)
    if "error" in outcome:
        raise outcome["error"]
    return not waiter.is_alive()


def _held(held: set[asyncio.Future[Any]], work: Awaitable[Any]) -> asyncio.Future[Any]:
    """Start *work* and hold it as the gateway holds what it starts in the background."""
    task = asyncio.ensure_future(work)
    held.add(task)
    task.add_done_callback(held.discard)
    return task


async def _nothing() -> None:
    return None


def test_a_wait_ends_when_the_last_task_has_only_just_finished() -> None:
    async def work() -> None:
        held: set[asyncio.Future[Any]] = set()
        task = _held(held, _nothing())
        await asyncio.sleep(0)  # it runs to its end in this pass; its callback waits for the next
        assert task.done() and task in held, "premise: finished, still held"
        await settle(held)

    assert _ends(work), "the wait spun on a finished task its set still held"


def test_a_wait_waits_for_work_started_while_it_waits() -> None:
    order: list[str] = []

    async def work() -> None:
        held: set[asyncio.Future[Any]] = set()

        async def second() -> None:
            await asyncio.sleep(0.05)
            order.append("second")

        async def first() -> None:
            await asyncio.sleep(0)
            _held(held, second())
            order.append("first")

        _held(held, first())
        await settle(held)
        assert order == ["first", "second"], order

    assert _ends(work)


def test_a_wait_does_not_raise_a_tasks_own_error() -> None:
    async def work() -> None:
        held: set[asyncio.Future[Any]] = set()

        async def fails() -> None:
            raise RuntimeError("the task's own")

        task = _held(held, fails())
        await settle(held)
        assert isinstance(task.exception(), RuntimeError)

    assert _ends(work)


def test_the_event_router_settles_when_its_last_delivery_has_only_just_finished(
    tmp_path: Path,
) -> None:
    from personalclaw.event_triggers import BusEvent
    from personalclaw.triggers.event_fire import EventRouter

    async def dispatch(*_args: Any, **_kwargs: Any) -> str:
        return ""

    async def work() -> None:
        router = EventRouter(dispatch=dispatch, loop=asyncio.get_running_loop(), base_dir=tmp_path)
        router(BusEvent(source="memory", event_type="create", key="k", value="v", now=time.time()))
        await asyncio.sleep(0)  # the delivery (no automation to match) ends in this pass
        assert router._pending and all(t.done() for t in router._pending), "premise"
        await router.settle()

    assert _ends(work), "the router's settle spun on a finished delivery it still held"


def test_the_capture_drain_ends_when_its_last_recording_has_only_just_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personalclaw.inbound import capture_proxy as proxy

    async def record(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(proxy, "_recorder", lambda: record)
    monkeypatch.setattr(proxy, "_pending", set())

    async def work() -> None:
        proxy._schedule_record(turn="t")
        await asyncio.sleep(0)  # the recording ends in this pass
        assert proxy._pending and all(t.done() for t in proxy._pending), "premise"
        await proxy.drain_recordings()

    assert _ends(work), "the capture drain spun on a finished recording it still held"


# ── the rail: no wait loops on a held set's membership ──


def test_the_rail_sees_a_wait_for_a_set_to_empty_and_passes_one_for_its_tasks() -> None:
    """The rail's positive control: the shapes the tree held are found, the replacement is not."""
    held_shapes = [
        "async def f(held):\n    while held:\n        await asyncio.gather(*held)\n",
        "async def f(s):\n    while s._pending:\n"
        "        await asyncio.gather(*list(s._pending), return_exceptions=True)\n",
        "async def f(s):\n    while s.state._background_tasks:\n"
        "        await asyncio.wait_for(asyncio.gather(*set(s.state._background_tasks)), 10)\n",
    ]
    for shape in held_shapes:
        assert _waits_for_a_set_to_empty(shape) == [2], shape
    replacement = (
        "async def f(held):\n    while pending := [t for t in held if not t.done()]:\n"
        "        await asyncio.gather(*pending, return_exceptions=True)\n"
    )
    assert _waits_for_a_set_to_empty(replacement) == []
