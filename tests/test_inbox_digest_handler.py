"""api_inbox_digest query-param validation.

Regression for bug #23: ``hours = float(request.query.get("hours"))`` sat BEFORE
the try block, so a non-numeric ``?hours=abc`` raised an unhandled ValueError →
raw 500 "Server got itself in trouble" instead of a clean 400. (The other
int/float(request.query…) casts across the dashboard handlers are already inside
try/except — this was the lone outlier.)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from personalclaw.dashboard.handlers_inbox import api_inbox_digest


def _req(query: dict) -> MagicMock:
    r = MagicMock()
    app = web.Application()
    app["state"] = MagicMock(_inbox_svc=None)
    r.app = app
    r.query = query
    return r


async def _json(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_digest_missing_channel_id_is_400():
    resp = await api_inbox_digest(_req({}))
    assert resp.status == 400
    assert "channel_id" in (await _json(resp))["error"]


@pytest.mark.asyncio
async def test_digest_non_numeric_hours_is_400_not_500():
    """The core of bug #23 — a bad hours param must be a clean 400."""
    resp = await api_inbox_digest(_req({"channel_id": "C123", "hours": "abc"}))
    assert resp.status == 400
    assert "hours" in (await _json(resp))["error"]


@pytest.mark.asyncio
async def test_digest_non_positive_hours_is_400():
    resp = await api_inbox_digest(_req({"channel_id": "C123", "hours": "0"}))
    assert resp.status == 400
    resp2 = await api_inbox_digest(_req({"channel_id": "C123", "hours": "-5"}))
    assert resp2.status == 400


@pytest.mark.asyncio
async def test_digest_valid_hours_passes_param_gate():
    """A valid channel_id + hours gets past the param gate to the service check
    (which returns 400 'inbox not running' here since _inbox_svc is None) — proving
    the numeric parse succeeded rather than 500'ing."""
    resp = await api_inbox_digest(_req({"channel_id": "C123", "hours": "4"}))
    assert resp.status == 400
    assert "inbox not running" in (await _json(resp))["error"]
