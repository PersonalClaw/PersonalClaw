"""The agent's `automation_history` reads an automation's recorded runs, the rows its History lists.

The tool called `tools.history()` with no run records, and `history()` is a pure projection of the
sources its caller hands it, so every answer was "<id> has no recorded runs yet", with zero rows,
for an automation the Triggers page showed runs for. An agent asked "has my automation run?" had
to say the tool and the page disagree. The tool now hands `history()` the automation's run rows
from the same run store the page's History reads (`ScheduleRunStore`), newest first, up to `n`.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader
from personalclaw import mcp_automation
from personalclaw.dashboard.handlers import triggers as H
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.store import TriggerStore

_NOTIFY = {"inline": {"provider": "notify", "config": {"title_template": "Stand up"}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(H, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(H, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


def _make(home) -> str:
    result = Tools.create(
        TriggerStore(base_dir=home),
        name="Stand up",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *", "timezone": "UTC"},
        workflow=_NOTIFY,
        created_by="user",
        enabled=True,
        owner_consented=True,
    )
    assert result.ok, result.text
    return str(result.data["trigger"]["id"])


def _ran(home, trigger_id: str, *, status: str, ago: float, error: str = "") -> None:
    started = time.time() - ago
    ScheduleRunStore(home).append_sync(
        ScheduleRun(
            job_id=trigger_id,
            job_name="Stand up",
            started_at=started,
            finished_at=started + 1,
            status=status,
            error=error,
        )
    )


def _said(tool_out: str) -> str:
    """The tool's sentence, without the `<automation-data>` block it carries for the agent."""
    return tool_out.split("<automation-data>", 1)[0].strip()


def _page_history(trigger_id: str) -> dict:
    app = web.Application()
    req = make_mocked_request(
        "GET", "/api/triggers/x/history", match_info={"id": f"schedule:{trigger_id}"}, app=app
    )
    resp = asyncio.run(H.api_trigger_history(req))
    assert resp.status == 200, resp.text
    import json

    return json.loads(resp.text)


def test_the_tool_lists_the_runs_the_page_lists(home):
    trigger_id = _make(home)
    _ran(home, trigger_id, status="success", ago=7200)
    _ran(home, trigger_id, status="failure", ago=60, error="the notify app was not installed")

    out = _said(mcp_automation._call_tool_inner("automation_history", {"id": trigger_id}))

    assert "no recorded runs" not in out, out
    assert "last 2 run(s)" in out, out
    page = _page_history(trigger_id)
    assert page["total"] == 2
    # Newest first, with its outcome and its reason, as the page shows it.
    lines = out.splitlines()[1:]
    assert "failed" in lines[0] and "the notify app was not installed" in lines[0], out
    assert lines[1].endswith(" ran"), out


def test_n_bounds_the_rows_and_the_newest_come_first(home):
    trigger_id = _make(home)
    for i in range(5):
        _ran(home, trigger_id, status="success", ago=3600 * (5 - i))

    out = _said(mcp_automation._call_tool_inner("automation_history", {"id": trigger_id, "n": 2}))

    assert "last 2 run(s)" in out, out
    first, second = (line.split(" ", 1)[0] for line in out.splitlines()[1:3])
    assert first > second, "newest first"


def test_an_automation_that_never_ran_says_so(home):
    """The control: with no run rows, the honest answer stays."""
    trigger_id = _make(home)

    out = _said(mcp_automation._call_tool_inner("automation_history", {"id": trigger_id}))

    assert out == f"{trigger_id} has no recorded runs yet.", out
