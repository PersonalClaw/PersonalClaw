"""The file-watch poll loop is wired into gateway boot (§3 / crit 2 — S93).

`file_poll` is tested as a library in `test_triggers_file_poll.py`; these tests pin the GATEWAY
adapter — that a file change routes to the trigger's declared action provider through the same
registry a cron uses, and that the loop is disjoint from `ScheduleService`. A runtime that polled
correctly but was never started, or fired through a second dispatch path, would be the
present-and-inert / drift defects this program keeps finding.
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
    Pinning that it sits inside the else-branch (not before the guard)."""
    import inspect

    src = inspect.getsource(G.GatewayOrchestrator._init_cron)
    # The task creation must appear AFTER the `if self._no_crons:` guard's else path — i.e. after
    # the digest-cron reconcile, which is the last thing in the else-branch.
    assert src.index("_file_watch_task") > src.index("reconcile_digest_cron")


def test_shutdown_cancels_the_loop():
    """A dangling task across shutdown leaks a filesystem poll into the next process."""
    import inspect

    src = inspect.getsource(G.GatewayOrchestrator._shutdown)
    assert "_file_watch_task" in src
    assert ".cancel()" in src
