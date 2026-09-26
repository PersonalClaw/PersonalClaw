"""The status strip's trigger count and the Triggers page are one list, and the page follows it.

Measured on day 8: the strip said "6 triggers" over a Triggers page that listed 5. Three things
let the two disagree, and each is pinned here:

* **Two gatherings.** `unified_trigger_count` was a hand-copied duplicate of `api_triggers`'
  gathering. Both now read `_gather`.
* **A blind spot in both.** `automation_create` makes `event` and `manual` rows in the unified
  store, and both gatherings left those kinds out, while the tool told the user "it is active now
  and visible on the Automations page".
* **A page that never re-read.** Only schedules re-polled; a row written after the page opened (a
  loop's auto-nudge, a chat tool in its own process) reached the strip's next poll and never the
  page. The clock loop now reports a store written by anyone else, and the gateway says `crons`.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.triggers import api_triggers, unified_trigger_count
from personalclaw.dashboard.state import DashboardState
from personalclaw.triggers import loop as L
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One home behind every seam the count and the list read (see the metric test's twin)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.triggers.config_dir", lambda: h, raising=False
    )
    import personalclaw.event_triggers as et
    from personalclaw.hooks import set_global_hook_store

    et._engine = None
    try:
        yield h
    finally:
        et._engine = None
        set_global_hook_store(None)


def _state() -> DashboardState:
    return DashboardState(
        sessions=MagicMock(count=0), start_time=time.time(), subagents=None, context_builder=None
    )


def _listed(state: DashboardState) -> list[dict]:
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    app = web.Application()
    app["state"] = state
    req = make_mocked_request("GET", "/api/triggers", app=app)
    req["user"] = "tester"
    return json.loads(asyncio.run(api_triggers(req)).body.decode())["triggers"]


def test_a_chat_made_event_or_manual_automation_is_listed_and_counted(home):
    store = TriggerStore(base_dir=home)
    for kind, spec in (
        ("clock", {"kind": "interval", "every_secs": 3600}),
        ("event", {"source": "session", "pattern": "SessionEnd"}),
        ("manual", {}),
    ):
        made = Tools.create(store, name=f"my {kind}", kind=kind, spec=spec, message="go")
        assert made.ok, made.text
        assert "visible on the Automations page" in made.text

    state = _state()
    listed = {row["raw_id"] for row in _listed(state)}
    assert {
        "event:my-event",
        "manual:my-manual",
    } <= listed, (
        "the tool said the automation is visible on the Automations page; the page did not list it"
    )
    assert unified_trigger_count(state) == len(listed) == 3


def test_a_row_written_by_another_writer_is_announced_by_the_clock_loop(monkeypatch, tmp_path):
    """The loop's own store instance notices a write made through ANY other one: the auto-nudge's,
    a request handler's, the chat tools' in their own process. That is what tells an open page."""
    store = TriggerStore(base_dir=tmp_path)
    told: list[int] = []
    sleeps = {"n": 0}

    async def ok(_payload):
        return {"status": "launched"}

    async def between_ticks(_secs):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise asyncio.CancelledError()
        # A loop starts and writes its auto-nudge, through a store instance of its own.
        TriggerStore(base_dir=tmp_path).upsert(
            Trigger(id="idle:auto-nudge", name="Auto-nudge", kind="idle", enabled=False, spec={})
        )

    monkeypatch.setattr(L.asyncio, "sleep", between_ticks)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(L.run_forever(store, runner=ok, on_store_changed=lambda: told.append(1)))
    assert told == [1], "the first tick saw no store; the second saw the new row"


def test_the_gateway_says_crons_when_the_store_changes(monkeypatch):
    """Wired, not just possible: `_clock_loop` hands the loop a callback that names `crons` alone
    (a store change is not a run, so the run feed has nothing to re-read)."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.triggers import loop as clock_loop

    class _State:
        def __init__(self) -> None:
            self.pushed: list[tuple[str, ...]] = []

        def push_refresh(self, *kinds: str) -> None:
            self.pushed.append(kinds)

    captured: dict = {}

    async def fake_run_forever(store, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(clock_loop, "run_forever", fake_run_forever)
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = _State()
    orch.sessions = None
    asyncio.run(orch._clock_loop())

    captured["on_store_changed"]()
    assert orch.dashboard_state.pushed == [("crons",)]
