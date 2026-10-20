"""The file-watch poll loop is wired into gateway boot.

`file_poll` is tested as a library in `test_triggers_file_poll.py`; these tests pin the GATEWAY
adapter — that a file change routes to the trigger's declared action provider through the same
registry a cron uses, and that the loop is disjoint from `ScheduleService`. A runtime that polled
correctly but was never started, or fired through a second dispatch path, would be the
present-and-inert / drift defects that keep recurring.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw import gateway as G
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _bare_gateway():
    """A GatewayOrchestrator without the full heavy __init__ — enough to call the fire method."""
    return G.GatewayOrchestrator.__new__(G.GatewayOrchestrator)


def _file_trigger(home, provider="notify", config=None):
    store = TriggerStore(base_dir=home)
    store.upsert(
        Trigger(
            id="file:notes",
            name="Notes",
            kind="file",
            enabled=True,
            spec={"paths": ["~/x/**"]},
            workflow={"provider": provider, "config": config or {"title_template": "t"}},
        )
    )
    return store


def test_a_fire_reaches_the_declared_action_provider(home, monkeypatch):
    """🔴 The end-to-end wiring: a file change runs the trigger's workflow action through the SAME
    action-provider registry a cron uses — no second dispatch path to drift."""
    _file_trigger(
        home,
        provider="notify",
        config={"title_template": "changed", "body_template": "{{trigger_id}}"},
    )

    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    captured = {}
    prov = get_action_provider("notify")
    real = prov.execute

    async def spy(action_config, ctx, timeout=30):
        captured["config"] = action_config
        captured["event"] = ctx.event
        captured["trigger_id"] = ctx.payload.get("trigger_id")
        return await real(action_config, ctx, timeout)

    monkeypatch.setattr(prov, "execute", spy)

    gw = _bare_gateway()
    asyncio.run(
        gw._fire_file_trigger(
            {"trigger_id": "file:notes", "changed": ["/x/a.md"], "added": ["/x/a.md"]}
        )
    )
    assert captured["config"]["title_template"] == "changed"
    assert captured["event"] == "file.changed"
    assert captured["trigger_id"] == "file:notes"


def test_a_fire_for_an_unknown_trigger_is_a_noop(home):
    gw = _bare_gateway()
    # No store row — must return quietly, not raise.
    asyncio.run(gw._fire_file_trigger({"trigger_id": "file:ghost"}))


def test_a_fire_with_an_unknown_provider_does_not_raise(home):
    _file_trigger(home, provider="does-not-exist")
    gw = _bare_gateway()
    asyncio.run(gw._fire_file_trigger({"trigger_id": "file:notes"}))


def test_a_provider_that_raises_does_not_crash_the_caller(home, monkeypatch):
    """🔴 A failed fire is logged, never propagated — a throwing action must not kill the poll
    loop and silently retire every other file automation."""
    _file_trigger(home, provider="notify")
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    prov = get_action_provider("notify")

    async def boom(action_config, ctx, timeout=30):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(prov, "execute", boom)
    gw = _bare_gateway()
    # Must NOT raise.
    asyncio.run(gw._fire_file_trigger({"trigger_id": "file:notes"}))


def test_the_poll_loop_is_started_in_init_cron(monkeypatch):
    """🔴 A runtime that polls correctly but is never started is present-and-inert. Assert the boot
    path creates the task — the source, not just behaviour, since the alternative is a loop nobody
    launches."""
    import inspect

    src = inspect.getsource(G.GatewayOrchestrator._init_cron)
    assert "_file_watch_poll_loop" in src
    assert "create_task" in src


def test_the_loop_lives_in_the_no_crons_else_branch(monkeypatch):
    """A file watch is unattended background work like a cron, so --no-crons must disable it too.
    Pinning that it sits inside the else-branch (not before the guard).

    Anchored on the GUARD itself, not on `reconcile_digest_cron`. That proxy meant "the last thing
    in the else-branch", and moving the reconcilers AFTER the boot migration (they wrote a file
    the clock engine never read) broke the assertion without breaking the property. An anchor
    that moves when unrelated code is reordered tests the layout, not the contract.
    """
    import inspect

    src = inspect.getsource(G.GatewayOrchestrator._init_cron)
    guard = src.index("if self._no_crons:")
    assert src.index("_file_watch_task") > guard
    # And every line of the task creation must be indented deeper than the guard, which is what
    # actually makes it conditional rather than merely later in the function.
    for line in src.split("\n"):
        if "_file_watch_task = asyncio.create_task" in line:
            indent = len(line) - len(line.lstrip())
            guard_line = next(ln for ln in src.split("\n") if "if self._no_crons:" in ln)
            assert indent > len(guard_line) - len(guard_line.lstrip())
            break
    else:  # pragma: no cover - the assertion above cannot be reached without the line
        raise AssertionError("the file-watch task creation was not found")


class _LoopThatNeverLeaves:
    """Stands in for the poll loop: once cancelled, it does not leave until the test lets it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancels = 0
        self._released = asyncio.Event()

    def release(self) -> None:
        self._released.set()

    async def __call__(self) -> None:
        self.entered.set()
        while not self._released.is_set():
            try:
                await self._released.wait()
            except asyncio.CancelledError:
                self.cancels += 1  # does not leave on a cancel


@pytest.mark.asyncio
async def test_shutdown_cancels_the_loop_through_the_bounded_wait(monkeypatch):
    """A dangling task across shutdown leaks a filesystem poll into the next process, so the stop
    cancels the loop. It does that through ``cancel_and_wait``, which waits for the loop a bounded
    time: driven with a loop that does not leave after its cancel, the stop still returns."""
    from test_gateway import _make_orchestrator

    from personalclaw import cancellation

    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECS", 0.3)
    handed: list[tuple[list, set]] = []
    real_cancel_and_wait = G.cancel_and_wait

    async def spy(tasks, *, what, grace=None):
        tasks = list(tasks)
        left = await real_cancel_and_wait(tasks, what=what, grace=grace)
        handed.append((tasks, left))
        return left

    monkeypatch.setattr(G, "cancel_and_wait", spy)
    orch = _make_orchestrator()
    orch.heartbeat_svc = None
    orch.inbox_svc = None
    orch.subagent_mgr = None
    orch.sessions = None
    orch.dashboard_state = None
    orch._dashboard_runner = None
    loop = _LoopThatNeverLeaves()
    watch = orch._file_watch_task = asyncio.ensure_future(loop())
    await loop.entered.wait()
    stop = asyncio.ensure_future(orch._shutdown())
    try:
        done, _ = await asyncio.wait({stop}, timeout=5.0)
        assert stop in done, "the stop waited on a file-watch loop that never left"
        stop.result()
        assert loop.cancels == 1, "the stop did not cancel the file-watch loop"
        # Handed to the bounded wait once, which reported it as the task it left running.
        reported = [left for tasks, left in handed if watch in tasks]
        assert reported == [{watch}], f"the loop never reached the bounded wait: {reported}"
    finally:
        loop.release()
        await asyncio.wait({watch, stop}, timeout=5.0)
