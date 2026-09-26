"""#773: the dashboard SystemHealth "triggers" rail must agree with the Triggers page.

The rail rendered ``status.cron_jobs`` — the schedule STORE's count — under a "triggers" label,
while ``GET /api/triggers`` lists schedules PLUS lifecycle hooks (and event / store-only kinds). So
a home with 5 schedules and 2 lifecycle hooks showed "5 triggers" on the dashboard and 7 on the
Triggers page, with no explanation for the gap. The fix sources the rail from a unified count that
includes every kind the page lists, computed by ``handlers.triggers.unified_trigger_count`` and
surfaced on ``GET /api/status`` as ``triggers``.

Every assertion is DRIVEN against a real seeded home and the real endpoints, not hand-built state:
the count is measured the same way production measures it, and cross-checked against the very list
the Triggers page renders.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.triggers import api_triggers, unified_trigger_count
from personalclaw.dashboard.state import DashboardState


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One isolated home behind EVERY seam the unified count reads.

    In tests the autouse trigger-store fixture and the config-loader fixture point the trigger
    store and the hook store at DIFFERENT tmp homes; production resolves them all to one
    ``config_dir()``. This pins every seam — the unified store (event triggers included), the
    lifecycle-hook store, and ``status_snapshot``'s own ``trigger_counts`` — at a single home, so
    the count is measured against one seeded world rather than several empty ones.
    """
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.triggers.config_dir", lambda: h, raising=False
    )
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: h, raising=False)
    from personalclaw.hooks import set_global_hook_store

    try:
        yield h
    finally:
        set_global_hook_store(None)


def _seed_schedule(home, trigger_id: str) -> None:
    """A real clock schedule, written to the unified store the way the runtime does."""
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=trigger_id,
            name=trigger_id,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "every_secs": 3600},
        )
    )


def _seed_lifecycle_hook(home, name: str) -> None:
    """A real lifecycle hook — a MemoryWrite hook, the globally-fired kind #773 names."""
    from personalclaw.hooks import ScriptHookStore

    ScriptHookStore(home).create({"name": name, "event": "MemoryWrite", "provider": "notify"})


def _state() -> DashboardState:
    return DashboardState(
        sessions=MagicMock(count=0),
        start_time=time.time(),
        subagents=None,
        context_builder=None,
    )


def _req(state: DashboardState, path: str = "/api/triggers"):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    app = web.Application()
    app["state"] = state
    req = make_mocked_request("GET", path, app=app)
    req["user"] = "tester"
    return req


def test_an_empty_home_counts_zero(home) -> None:
    assert unified_trigger_count(_state()) == 0


def test_the_rail_count_includes_lifecycle_hooks_not_just_schedules(home) -> None:
    """🔴 THE BUG. One schedule and one lifecycle hook: the rail must count BOTH (2).

    The old source (`cron_jobs` = the schedule store's total) counted the schedule alone and would
    report 1 here — silently dropping the lifecycle hook the Triggers page shows.
    """
    _seed_schedule(home, "clock:a")
    _seed_lifecycle_hook(home, "on-memory-write")
    assert unified_trigger_count(_state()) == 2


def test_the_rail_count_equals_the_triggers_page(home) -> None:
    """Coherence: the dashboard's number must equal what GET /api/triggers lists.

    This is the exact divergence #773 reported — 5 on the rail, 7 on the page — reproduced against
    the real endpoint and then proven equal.
    """
    for i in range(5):
        _seed_schedule(home, f"clock:{i}")
    _seed_lifecycle_hook(home, "hook-a")
    _seed_lifecycle_hook(home, "hook-b")

    state = _state()
    resp = asyncio.run(api_triggers(_req(state)))
    listed = json.loads(resp.body.decode())["triggers"]

    assert len(listed) == 7, "seeded 5 schedules + 2 lifecycle hooks"
    assert unified_trigger_count(state) == len(listed)


def test_api_status_surfaces_the_unified_triggers_field(home, monkeypatch) -> None:
    """End to end: /api/status carries `triggers` (unified) and no `cron_jobs`, while the
    schedule-store count still ships as the `cron` block."""
    from personalclaw.dashboard import handlers_system

    # Skip the background update-recheck task so the probe stays a pure read.
    from personalclaw.dashboard.handlers import updates as _updates_mod

    monkeypatch.setattr(_updates_mod, "_last_update_check", time.time())

    _seed_schedule(home, "clock:a")
    _seed_lifecycle_hook(home, "hook-a")

    state = _state()
    state._owner_hash = "test-hash"  # avoid the owner-hash executor round trip
    resp = asyncio.run(handlers_system.api_status(_req(state, path="/api/status")))
    body = json.loads(resp.body.decode())

    assert body["triggers"] == 2, "schedule + lifecycle hook"
    assert "cron_jobs" not in body, "the flat schedule-only mirror is gone (#773)"
    assert body["cron"]["total"] == 1, "the schedule-store block is unchanged and narrower"
