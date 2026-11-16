"""A missed-run card settles once its automation has run on schedule since.

A digest missed its slots while PersonalClaw was stopped, and its card waited: "Missed 2 scheduled
runs … Run now runs it once." The digest then ran on schedule every day, and its row said "ok 5h
ago", yet the card stayed for weeks, offering to run the missed slot again, which for a digest
repeats today's run. Nothing retired it.

Now a good scheduled run after the card's newest missed slot settles it: the review no longer lists
it, the list no longer orders by it, and the trigger's history records the missed run let go, with
why, as a dismissal does. A run of yours, a failed run, or a run from before the slot settles
nothing, and a card about a run a restart cut off waits for you whatever runs after it.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers import review, run_source
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

TID = "clock:morning-digest"
DAY = 86_400.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    TriggerStore(base_dir=tmp_path).upsert(
        Trigger(
            id=TID,
            name="Morning digest",
            kind="clock",
            enabled=True,
            created_by="user",
            spec={"kind": "cron", "expr": "0 7 * * *"},
            capabilities={"providers": ["run-command"]},
            workflow={"inline": {"provider": "run-command", "config": {"command": "true"}}},
        )
    )
    return tmp_path


def _card(latest: float, kind: str = review.MISSED) -> review.ReviewCard:
    return review.ReviewCard(trigger_id=TID, kind=kind, count=2, latest=latest, oldest=latest - DAY)


def _ran(home, at: float, *, status: str = "success", source: str = run_source.SCHEDULE) -> None:
    ScheduleRunStore(home).append_sync(
        ScheduleRun(
            job_id=TID,
            job_name="Morning digest",
            status=status,
            source=source,
            started_at=at,
            finished_at=at + 5,
        )
    )


def _review_get() -> dict[str, Any]:
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    req = make_mocked_request("GET", "/api/triggers/review", app=app)
    req["user"] = "owner"
    resp = asyncio.run(T.api_trigger_review(req))
    return json.loads(resp.body.decode())


def _rows(home) -> list[dict[str, Any]]:
    rows, _ = asyncio.run(ScheduleRunStore(home).list_for_job(TID, 0, 20))
    return rows


@pytest.mark.parametrize("status", ["success", "skipped_noop", "degraded"])
def test_a_good_scheduled_run_after_the_missed_slot_settles_the_card(status):
    now = time.time()
    rows = [{"status": status, "source": run_source.SCHEDULE, "started_at": now - 5 * 3600}]
    assert review.settled_by(_card(now - 25 * DAY), rows) == rows[0]


@pytest.mark.parametrize(
    "row",
    [
        {"status": "success", "source": run_source.YOU, "started_at": 2e9},
        {"status": "failure", "source": run_source.SCHEDULE, "started_at": 2e9},
        {"status": "launched", "source": run_source.SCHEDULE, "started_at": 2e9},
        {"status": "success", "source": run_source.SCHEDULE, "started_at": 1e9 - 1},
    ],
    ids=["a run of yours", "a failed run", "a run whose work is not done", "a run before the slot"],
)
def test_what_settles_nothing(row):
    assert review.settled_by(_card(1e9), [row]) is None


def test_a_card_that_names_no_slot_settles_nothing():
    """A card file with no `latest` reads it as 0.0: no run can be after a slot it does not name."""
    rows = [{"status": "success", "source": run_source.SCHEDULE, "started_at": 2e9}]
    assert review.settled_by(_card(0.0), rows) is None


def test_a_card_about_an_interrupted_run_waits_whatever_runs_after_it():
    rows = [{"status": "success", "source": run_source.SCHEDULE, "started_at": 2e9}]
    assert review.settled_by(_card(1e9, kind=review.INTERRUPTED), rows) is None


def test_the_review_lets_a_settled_card_go_and_records_why(home):
    now = time.time()
    review.record([_card(now - 25 * DAY)], base_dir=home)
    _ran(home, now - 5 * 3600)
    # Before the review is read: the list's own read leaves a settled card out.
    assert T._waiting_decisions() == {}, "the list orders by no settled card"
    listed = _review_get()["cards"]
    assert listed == [], "a card whose automation has run on schedule since is no decision"
    assert review.pending(base_dir=home) == []
    assert T._waiting_decisions() == {}
    let_go = [r for r in _rows(home) if r.get("status") == "skipped_missed"]
    assert len(let_go) == 1
    assert "run on schedule since" in str(let_go[0].get("error") or let_go[0].get("summary") or "")


def test_the_review_keeps_a_card_whose_missed_work_is_still_missing(home):
    now = time.time()
    review.record([_card(now - 2 * DAY)], base_dir=home)
    _ran(home, now - 3 * DAY)  # before the slot
    _ran(home, now - 3600, status="failure")
    _ran(home, now - 1800, source=run_source.YOU)
    listed = _review_get()["cards"]
    assert [c["trigger_id"] for c in listed] == [TID]
    assert len(review.pending(base_dir=home)) == 1
