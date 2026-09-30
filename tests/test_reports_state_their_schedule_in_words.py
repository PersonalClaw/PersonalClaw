"""The Reports page states a report's schedule in words, with its next run and its time zone.

A report scheduled for Monday 08:00 Toronto read "cron 0 8 * * 1 · anything new · never run" on
the Reports page, while only the Triggers page said "At 8:00 AM EDT, only on Monday … in 5d". The
reports API now serves each report's schedule as the Triggers page words it, read off the very
trigger row its schedule becomes (`report_schedules.shown`), with its zone and its next run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from personalclaw.knowledge import report_schedules as rs
from personalclaw.knowledge import research_reports as rr

A_THURSDAY = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def _defn(**over):
    raw = {
        "id": "rpt-releases",
        "name": "Postgres and Python releases",
        "prompt": "What shipped this week, with links",
        "tz": "America/Toronto",
        "schedule": {"kind": "cron", "cron_expr": "0 8 * * 1"},
        "source": {"tags": [], "window_secs": 0},
        "citation_policy": rr.CITE_SOURCE_ONLY,
        "enabled": True,
    }
    raw.update(over)
    return rr.from_dict(raw)


def test_a_reports_schedule_is_said_in_words_with_its_zone_and_its_next_run():
    shown = rs.shown(_defn(), now=A_THURSDAY)
    assert shown["words"].startswith("At 8:00 AM ED"), shown
    assert "Monday" in shown["words"]
    assert shown["timezone"] == "America/Toronto"
    assert datetime.fromisoformat(shown["next_run_at"]) == datetime(
        2026, 10, 5, 12, 0, tzinfo=timezone.utc
    )


def test_a_paused_report_has_no_next_run():
    shown = rs.shown(_defn(enabled=False), now=A_THURSDAY)
    assert shown["words"] and shown["next_run_at"] == ""


def test_a_report_with_no_schedule_says_none():
    assert rs.shown(_defn(schedule={"kind": "cron", "cron_expr": ""}), now=A_THURSDAY) == {
        "words": "",
        "timezone": "",
        "next_run_at": "",
    }


@pytest.mark.asyncio
async def test_the_reports_list_serves_each_schedule_as_the_page_states_it():
    from personalclaw.dashboard.handlers.research_reports import api_reports_list

    rr.save_report(_defn())
    resp = await api_reports_list(None)  # type: ignore[arg-type]
    [report] = json.loads(resp.body.decode())["reports"]
    assert report["schedule"]["cron_expr"] == "0 8 * * 1", "the stored form is unchanged"
    assert report["schedule_shown"]["timezone"] == "America/Toronto"
    assert "Monday" in report["schedule_shown"]["words"]
    assert report["schedule_shown"]["next_run_at"]
