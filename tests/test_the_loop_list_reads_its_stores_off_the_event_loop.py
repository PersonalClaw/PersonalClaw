"""``GET /api/loops`` reads its stores in a worker thread, never on the event loop.

The shell asks for the loop list from every page (the Loop tile's badge, every 30 s), and so do
Home, the Loops page and Mission Control. It read the loops table, one ledger file per loop, and
the workflow run store, which it opened and closed again, all on the event loop. The browser
harness's gateway stopped answering with its loop's thread inside that open while a worker thread
closed the same database (``test_a_pysqlite3_close_never_runs_beside_an_open``). While the loop is
inside a store, nothing else it serves answers, however the store's driver behaves.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.workflows import loop_view


def test_the_loop_list_reads_both_its_homes_in_a_worker_thread(monkeypatch: pytest.MonkeyPatch):
    """🔴 Red before: both were read on the event loop's thread."""
    read_on: dict[str, int] = {}

    def loops_table(project_id: str = "", kind: str = "") -> list[dict[str, Any]]:
        read_on["loops table"] = threading.get_ident()
        return [{"id": "older", "created_at": 1.0}]

    def run_store(*, project_id: str = "", kind: str = "") -> list[dict[str, Any]]:
        read_on["run store"] = threading.get_ident()
        return [{"id": "newer", "created_at": 2.0}]

    monkeypatch.setattr(H.store, "list_redacted", loops_table)
    monkeypatch.setattr(loop_view, "list_loop_views", run_store)
    request = make_mocked_request("GET", "/api/loops?kind=goal", app=web.Application())
    request["user"] = "owner"

    response = asyncio.run(H.api_loop_list(request))

    loop_thread = threading.get_ident()  # asyncio.run ran the handler on this thread
    assert set(read_on) == {"loops table", "run store"}
    assert loop_thread not in read_on.values(), "a store was read on the event loop"
    assert response.status == 200
    assert [r["id"] for r in json.loads(response.body.decode())["loops"]] == ["newer", "older"]
