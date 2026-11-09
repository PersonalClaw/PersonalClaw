"""The status answer's summary line gives the gateway's uptime, the one its ``uptime`` field gives.

``GET /api/status`` answered ``"uptime": "35h 51m"`` beside ``"stats_summary": "uptime 5h 37m ·
…"``. The summary's clock was ``Stats``' own: it starts when the singleton is first built, the
first time anything counts or reads a stat, not when the gateway starts, and on macOS
``time.monotonic()`` also stops while the machine sleeps. So the line labelled a counting window
as the uptime.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import handlers_system
from personalclaw.dashboard.handlers import updates as _updates_mod
from personalclaw.dashboard.state import DashboardState
from personalclaw.stats import Stats


def _status(monkeypatch, *, started_ago: float) -> dict:
    """The real ``/api/status`` answer of a gateway that started *started_ago* seconds ago."""
    monkeypatch.setattr(_updates_mod, "_last_update_check", time.time())
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=time.time() - started_ago,
        subagents=MagicMock(count=0),
        context_builder=None,
    )
    state._owner_hash = "test-hash"
    app = web.Application()
    app["state"] = state
    req = make_mocked_request("GET", "/api/status", app=app)
    req["user"] = "tester"
    return json.loads(asyncio.run(handlers_system.api_status(req)).body.decode())


def test_the_summary_gives_the_uptime_the_answer_gives(monkeypatch):
    """🔴 Red before: a gateway up 35 h whose stats were first touched just now summarised
    "uptime 0h 0m" beside its "35h 0m" uptime."""
    Stats().reset()  # the counting window starts now, as when the stats are first touched late
    try:
        answer = _status(monkeypatch, started_ago=35 * 3600 + 30)
    finally:
        Stats().reset()
    assert answer["uptime"] == "35h 0m"
    assert answer["stats_summary"].startswith(f"uptime {answer['uptime']} · "), answer[
        "stats_summary"
    ]


def test_a_summary_with_no_gateway_uptime_names_its_own_window():
    """Asked with no uptime to give, the summary says how long it has counted, and does not
    call that the uptime."""
    Stats().reset()
    try:
        text = Stats().summary()
    finally:
        Stats().reset()
    assert text.startswith("counted for 0h 0m · "), text
    assert "uptime" not in text
