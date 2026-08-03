"""S76 — staging-tier observability: the week-at-a-glance panel (§6).

`StagingStore.health()` already answers "is capture working" over a WINDOW, and `record_flush`
already persists `FLUSH_OK/PRODUCED/ERROR/SKIPPED` plus `proposal_ids`. Measured first: the
aggregation hides the one thing this panel exists to show — **a day with ZERO passes is
indistinguishable from a healthy day** in the windowed view, because an absent day contributes
nothing to the outcome counts or the streak. Silent capture death is precisely the failure the
staging tier was built to make visible.

`test_a_silent_day_is_named` is the regression.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from personalclaw.learning.staging import FlushOutcome, StagingStore

DAY = 86400.0


@pytest.fixture
def store(tmp_path):
    """A real store on disk under tmp_path. Closed after, so the sqlite handle does not leak into
    another test on the same xdist worker."""
    s = StagingStore(tmp_path / "staging.db")
    yield s
    s.close()


def _record(store, monkeypatch, *, outcome, at, proposal_ids=None, cost=0.01, cadence="turn_end"):
    """Record one flush AT a chosen time.

    `record_flush` stamps `time.time()` itself, so the clock is patched per row rather than the
    timestamp being passed — patching lets a test build a week of history without waiting one.
    """
    import personalclaw.learning.staging as module

    monkeypatch.setattr(module.time, "time", lambda: at)
    store.record_flush(cadence=cadence, outcome=outcome, proposal_ids=proposal_ids, cost_usd=cost)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ── the measured gap ──


def test_a_silent_day_is_named(store, monkeypatch):
    """THE regression.

    A day with no passes contributes nothing to `health()`'s counts or streak, so capture dying
    looks exactly like capture being fine. The panel names the day.
    """
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now)
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now - 3 * DAY)

    week = store.week(days=4, now=now)
    silent = set(week["silent_days"])
    assert _day(now - DAY) in silent
    assert _day(now - 2 * DAY) in silent
    assert _day(now) not in silent


def test_health_alone_cannot_see_a_silent_day(store, monkeypatch):
    """The contrast, asserted rather than asserted-about.

    Two windows with the SAME totals — one continuous, one with a two-day hole — are identical to
    `health()` and different in the panel.
    """
    now = time.time()
    for offset in (0, 3):
        _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now - offset * DAY)

    health = store.health(days=4, now=now)
    assert health["passes"] == 2
    assert "silent" not in str(health)
    assert store.week(days=4, now=now)["silent_days"]


def test_every_day_in_the_window_gets_a_bucket(store):
    """Pre-seeded, so a gap renders as a gap rather than vanishing from the list."""
    now = time.time()
    week = store.week(days=7, now=now)
    assert len(week["buckets"]) == 7
    assert all(b["passes"] == 0 for b in week["buckets"])
    assert len(week["silent_days"]) == 7


def test_buckets_come_back_in_date_order(store, monkeypatch):
    """A panel renders left-to-right; an unordered list would need the caller to sort, and a caller
    that forgets renders a scrambled week."""
    now = time.time()
    for offset in range(5):
        _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now - offset * DAY)
    days = [b["day"] for b in store.week(days=5, now=now)["buckets"]]
    assert days == sorted(days)


# ── per-outcome accounting ──


def test_outcomes_are_counted_per_day(store, monkeypatch):
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now)
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now)
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_ERROR, at=now)

    today = next(b for b in store.week(days=2, now=now)["buckets"] if b["day"] == _day(now))
    assert today["passes"] == 3
    assert today["by_outcome"] == {"flush_ok": 2, "flush_error": 1}
    assert today["errors"] == 1


def test_error_days_are_isolated(store, monkeypatch):
    """ "Which day broke" is the question a maintainer actually asks."""
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now)
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_ERROR, at=now - DAY)

    week = store.week(days=3, now=now)
    assert week["error_days"] == [_day(now - DAY)]


def test_every_flush_outcome_is_representable(store, monkeypatch):
    """All four members, so a new outcome cannot silently fail to render."""
    now = time.time()
    for outcome in FlushOutcome:
        _record(store, monkeypatch, outcome=outcome, at=now)
    today = next(b for b in store.week(days=1, now=now)["buckets"] if b["day"] == _day(now))
    assert set(today["by_outcome"]) == {o.value for o in FlushOutcome}


# ── proposal ids: "produced WHAT" ──


def test_proposal_ids_link_a_day_to_its_inbox_rows(store, monkeypatch):
    """Ids turn "a pass produced something" into "produced WHAT" — the panel links straight to
    the Proposal Inbox rows a day generated."""
    now = time.time()
    _record(
        store,
        monkeypatch,
        outcome=FlushOutcome.FLUSH_PRODUCED,
        at=now,
        proposal_ids=["p1", "p2"],
    )
    today = next(b for b in store.week(days=1, now=now)["buckets"] if b["day"] == _day(now))
    assert today["proposal_ids"] == ["p1", "p2"]
    assert today["produced"] == 2


def test_produced_counts_ids_not_passes(store, monkeypatch):
    """One pass that filed three proposals produced three, not one."""
    now = time.time()
    _record(
        store,
        monkeypatch,
        outcome=FlushOutcome.FLUSH_PRODUCED,
        at=now,
        proposal_ids=["a", "b", "c"],
    )
    week = store.week(days=1, now=now)
    assert week["produced_total"] == 3


def test_a_pass_with_no_ids_produces_nothing(store, monkeypatch):
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now)
    week = store.week(days=1, now=now)
    assert week["produced_total"] == 0
    assert week["buckets"][-1]["proposal_ids"] == []


def test_malformed_proposal_ids_do_not_break_the_panel(store, monkeypatch):
    """The column is JSON text; a hand-edited or truncated row must not empty the whole week."""
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now)
    with store._cursor() as cur:  # noqa: SLF001 - deliberately corrupting one row
        cur.execute("UPDATE flush_records SET proposal_ids = ?;", ("{not json",))
    week = store.week(days=1, now=now)
    assert week["buckets"] and week["produced_total"] == 0


# ── staged entries and cost ──


def test_staged_entries_are_bucketed_too(store, monkeypatch):
    """A day that STAGED but never flushed is a different failure from one that never ran: the
    signal arrived and nothing consumed it."""
    import personalclaw.learning.staging as module

    now = time.time()
    monkeypatch.setattr(module.time, "time", lambda: now)
    store.stage(kind="lesson", cadence="turn_end", content="x", session_key="s")

    today = next(b for b in store.week(days=1, now=now)["buckets"] if b["day"] == _day(now))
    assert today["staged"] == 1
    assert today["passes"] == 0  # staged, never flushed


def test_cost_is_summed_per_day_and_overall(store, monkeypatch):
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now, cost=0.02)
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now - DAY, cost=0.03)

    week = store.week(days=2, now=now)
    assert week["cost_usd"] == pytest.approx(0.05)
    today = next(b for b in week["buckets"] if b["day"] == _day(now))
    assert today["cost_usd"] == pytest.approx(0.02)


# ── window handling ──


def test_records_outside_the_window_are_excluded(store, monkeypatch):
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now - 30 * DAY)
    week = store.week(days=3, now=now)
    assert all(b["passes"] == 0 for b in week["buckets"])


def test_a_zero_day_window_is_clamped_to_one(store):
    """A caller passing 0 wants today, not an empty panel."""
    week = store.week(days=0, now=time.time())
    assert week["days"] == 1 and len(week["buckets"]) == 1


def test_the_panel_serializes_for_an_api(store):
    week = store.week(days=7, now=time.time())
    assert set(week) == {
        "days",
        "buckets",
        "silent_days",
        "error_days",
        "produced_total",
        "cost_usd",
    }
    for bucket in week["buckets"]:
        assert set(bucket) == {
            "day",
            "passes",
            "by_outcome",
            "produced",
            "errors",
            "staged",
            "cost_usd",
            "proposal_ids",
        }


def test_days_are_bucketed_by_LOCAL_date(store, monkeypatch):
    """A user reading "Tuesday" means their Tuesday; a UTC-slice panel drifts hours off every
    reader's calendar."""
    now = time.time()
    _record(store, monkeypatch, outcome=FlushOutcome.FLUSH_OK, at=now)
    week = store.week(days=1, now=now)
    assert week["buckets"][0]["day"] == datetime.fromtimestamp(now).strftime("%Y-%m-%d")
