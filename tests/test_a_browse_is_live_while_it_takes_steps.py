"""A browse is live while it takes steps, and the browse status read says so.

Home's Overview showed its Browse live view band all the time, an Idle pill and a Stop button for a
browser nobody browsed with. It now shows only while a browse runs. Each step is relayed to open
dashboards as a `browse_step` frame; the gateway notes the time of the last one, and
`browse.mirror.is_live()` is true while one came within `mirror.LIVE_SECS` (30 s), so a page opened
mid-browse, which missed the frames, reads it from `GET /api/browse/status` (`live`).
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.browse import mirror


@pytest.fixture(autouse=True)
def _no_browse_yet(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(mirror, "_last_step_at", 0.0)


def _step() -> None:
    mirror.broadcast_browse_step({"run_id": "run-1", "step_n": 1, "url": "https://example.com"})


def test_no_step_yet_is_not_live():
    assert mirror.is_live() is False


def test_a_step_makes_it_live_even_with_no_dashboard_open(monkeypatch):
    monkeypatch.setattr(mirror, "_resolve_state", lambda state: None)

    _step()

    assert mirror.is_live() is True


def test_it_ends_once_no_step_came_for_the_window(monkeypatch):
    _step()
    later = mirror._now() + mirror.LIVE_SECS + 1
    monkeypatch.setattr(mirror, "_now", lambda: later)

    assert mirror.is_live() is False


def test_the_status_read_serves_live():
    from personalclaw.dashboard.handlers.browse_mirror import api_browse_status

    async def read() -> dict:
        # An application per read: each `asyncio.run` is a loop of its own, and an application
        # stays bound to the loop it first started on.
        app = web.Application()
        app.router.add_get("/api/browse/status", api_browse_status)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            return await (await client.get("/api/browse/status")).json()
        finally:
            await client.close()

    assert asyncio.run(read())["live"] is False
    _step()
    assert asyncio.run(read())["live"] is True


def test_a_browser_grant_is_answered_in_attention():
    """A run parked on a grant takes no step, so the browse live view is not where it is answered:
    the waiting list sends you to Home's Attention, where its card is."""
    from personalclaw import waiting

    (item,) = waiting._browse_grants(
        [{"request_id": "g1", "task": "Invoices", "scope": ["billing.example.com"]}], 0.0
    )

    assert item["route"] == "#/mission-control"
