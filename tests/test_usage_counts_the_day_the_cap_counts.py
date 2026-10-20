"""Every spend surface counts her day: the calendar day in the timezone her schedules run in.

The cap reset at the gateway clock's midnight, and the page counted the UTC day beside it. On an
evening in Toronto (UTC-4) a turn at 20:40 is 00:40 UTC the next day, so at 04:00 the next morning
the page's "Today" held that turn while the cap line under it said nothing had been spent today,
and the 7-day chart put the spend on a day she did not spend it. Then both counted the gateway
clock's day, which is hers only when that clock is in her timezone: on a gateway whose clock is on
UTC (a container) or in another city, her ``config.timezone`` ran her schedules and nothing else.

The clock stands at 2026-09-30 08:00 UTC (04:00 in Toronto) unless a test moves it, the turn is
$10.50 at 2026-09-30 00:40 UTC (20:40 on the 29th there), and every test runs three ways
(:data:`WAYS`): her timezone is this machine's own, or it is her ``config.timezone`` on a gateway
whose clock is on UTC, or on Los Angeles time.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import spend_day
from personalclaw.routing import usage as U

NOW = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
EVENING_TURN = "2026-09-30T00:40:00+00:00"
HER_DAY, HER_TODAY = "2026-09-29", "2026-09-30"


class _Clock(datetime):
    """The clock at :attr:`at` (:data:`NOW` unless a test moves it), read in whatever zone is
    asked for."""

    at = NOW

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.at.astimezone(tz) if tz is not None else cls.at.astimezone().replace(tzinfo=None)


#: How her timezone is known: ``(the gateway clock's zone, her config.timezone)``.
WAYS = {
    "this-machines-zone": ("America/Toronto", ""),
    "configured-on-a-utc-clock": ("UTC", "America/Toronto"),
    "configured-on-a-pacific-clock": ("America/Los_Angeles", "America/Toronto"),
}


@pytest.fixture(params=sorted(WAYS))
def toronto(request, monkeypatch):
    from personalclaw.config.loader import config_dir

    clock_zone, configured = WAYS[request.param]
    if configured:
        (config_dir() / "config.json").write_text(
            json.dumps({"timezone": configured}), encoding="utf-8"
        )
    before = os.environ.get("TZ")
    os.environ["TZ"] = clock_zone
    time.tzset()
    monkeypatch.setattr(spend_day, "datetime", _Clock)
    monkeypatch.setattr(_Clock, "at", NOW)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def _turn(ts: str, dollars: float = 10.5) -> dict:
    return {
        "ts": ts,
        "session_key": "loop:weekly",
        "source": "loop",
        "agent": "",
        "provider": "fakecloud",
        "model": "gpt-4o-mini",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": dollars,
        "priced": True,
    }


def _ledger(rows: list[dict]) -> None:
    from personalclaw.usage_ledger import _path

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _body(response: web.Response) -> dict:
    return json.loads(response.body.decode())


def test_an_evening_turn_is_counted_on_her_day(toronto):
    fold = U.empty_fold()
    assert U.fold_turn_row(fold, _turn(EVENING_TURN), look=lambda p, m: (False, False))
    assert list(fold["days"]) == [HER_DAY]


def test_today_is_the_day_the_cap_is_counting(toronto, tmp_path):
    from personalclaw.guardrails.budgets import SpendMeter

    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(1100, 0.25)
    assert list(json.loads((tmp_path / "spend.json").read_text())) == [HER_TODAY]

    fold = U.empty_fold()
    U.fold_turn_row(fold, _turn(EVENING_TURN), look=lambda p, m: (False, False))
    today = U.query(fold, window="day")
    assert today["dates"] == [HER_TODAY]
    assert today["total"]["calls"] == 0 and today["total"]["dollars_est"] == 0
    week = U.query(fold, window="week")
    assert week["dates"][-2:] == [HER_DAY, HER_TODAY]
    assert [s["dollars_est"] for s in week["series"] if s["dollars_est"]] == [10.5]
    assert [s["date"] for s in week["series"] if s["dollars_est"]] == [HER_DAY]


def test_a_census_day_is_her_day_too(toronto):
    at = datetime.fromisoformat(EVENING_TURN).timestamp()
    census = U.audit_census([{"audit_id": "a", "ts": at, "dollars_est": 0.1, "priced": True}])
    assert census["days"] == {HER_DAY: 1}


@pytest.mark.asyncio
async def test_the_pages_totals_and_tables_read_her_local_days(toronto):
    from personalclaw.dashboard.handlers.usage import api_usage_rollup, api_usage_totals

    _ledger([_turn(EVENING_TURN), _turn("2026-09-30T05:00:00+00:00", 0.5)])

    def ask(handler, query: str):
        return handler(make_mocked_request("GET", f"/api/usage/x?{query}"))

    today = _body(await ask(api_usage_totals, "window=day"))
    assert today["since"] == "2026-09-30T04:00:00+00:00"
    assert (today["totals"]["turns"], today["totals"]["cost_usd"]) == (1, 0.5)
    week = _body(await ask(api_usage_totals, "window=week"))
    assert (week["totals"]["turns"], week["totals"]["cost_usd"]) == (2, 11.0)
    by_day = _body(await ask(api_usage_rollup, "window=week&group_by=day"))
    assert sorted((r["day"], r["cost_usd"]) for r in by_day["rows"]) == [
        (HER_DAY, 10.5),
        (HER_TODAY, 0.5),
    ]


@pytest.mark.asyncio
async def test_a_window_is_one_of_the_folds_and_never_beside_a_since(toronto):
    from personalclaw.dashboard.handlers.usage import api_usage_totals

    bad = await api_usage_totals(make_mocked_request("GET", "/api/usage/totals?window=year"))
    assert bad.status == 400 and _body(bad)["error"]["code"] == "bad_request"
    both = await api_usage_totals(
        make_mocked_request("GET", "/api/usage/totals?window=day&since=2026-09-01T00:00:00Z")
    )
    assert both.status == 400 and _body(both)["error"]["code"] == "bad_request"
    plain = await api_usage_totals(
        make_mocked_request("GET", "/api/usage/totals?since=2026-09-01T00:00:00%2B00:00")
    )
    assert plain.status == 200 and _body(plain)["since"] == "2026-09-01T00:00:00+00:00"


def test_a_fold_kept_in_utc_days_is_replaced_by_her_days_not_counted_beside_them(toronto, tmp_path):
    """The fold persists between reads. One written by a build that keyed days in UTC holds the
    evening turn under the 30th; the refold holds it under the 29th. Both kept, it counts twice."""
    ledger = tmp_path / "turns.jsonl"
    ledger.write_text(json.dumps(_turn(EVENING_TURN)) + "\n", encoding="utf-8")
    utc_cell = {
        "calls": 1,
        "tokens_in": 1000,
        "tokens_out": 100,
        "dollars_est": 10.5,
        "estimated_dollars": 10.5,
        "estimated_calls": 1,
        "unpriced_calls": 0,
        "local_calls": 0,
    }
    prior = U.empty_fold()
    prior["days"] = {HER_TODAY: {"fakecloud:gpt-4o-mini": {"loop": utc_cell}}}
    U.save_usage(tmp_path, prior)

    fold = U.refresh(tmp_path, audit_path=tmp_path / "none.jsonl", ledger_path=ledger)

    assert list(fold["days"]) == [HER_DAY]
    week = U.query(fold, window="week")
    assert (week["total"]["calls"], week["total"]["dollars_est"]) == (1, 10.5)


def test_a_day_the_ledger_no_longer_holds_is_kept_from_the_fold(toronto, tmp_path):
    """The reason the fold persists at all: the ledger is trimmed from its oldest rows."""
    ledger = tmp_path / "turns.jsonl"
    ledger.write_text(json.dumps(_turn(EVENING_TURN)) + "\n", encoding="utf-8")
    aged = {"fakecloud:gpt-4o-mini": {"loop": {"calls": 3, "dollars_est": 1.0}}}
    prior = U.empty_fold()
    prior["days"] = {"2026-09-20": aged}
    U.save_usage(tmp_path, prior)

    fold = U.refresh(tmp_path, audit_path=tmp_path / "none.jsonl", ledger_path=ledger)

    assert sorted(fold["days"]) == ["2026-09-20", HER_DAY]
    assert fold["days"]["2026-09-20"] == aged


@pytest.mark.asyncio
async def test_her_caps_start_afresh_at_her_midnight(toronto):
    """04:00 on the 30th there: the caps start afresh at 00:00 on 1 October there, 04:00 UTC,
    not at the midnight of the gateway's own clock."""
    from personalclaw.dashboard.handlers.usage import api_usage_budget

    midnight = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc).timestamp()
    assert spend_day.next_day_starts() == midnight
    budget = _body(await api_usage_budget(make_mocked_request("GET", "/api/usage/budget")))
    assert budget["resets_at"] == midnight


def test_the_monthly_recap_covers_the_month_before_hers(toronto, monkeypatch):
    """Run by hand at 20:30 on 30 September there, 00:30 UTC on 1 October, the recap is of
    August: her September is still under way."""
    from personalclaw.action_providers import usage_recap_provider as P

    monkeypatch.setattr(_Clock, "at", datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc))
    # The recap's own clock too, so every clock it could read is the pinned one.
    monkeypatch.setattr(P, "datetime", _Clock)
    assert P.previous_month() == "2026-08"


def test_her_day_runs_midnight_to_midnight_on_her_clock_across_the_clock_change(
    toronto, monkeypatch
):
    """Her clocks go back an hour at 02:00 on 1 November, so that day lasts 25 hours: from 04:00
    UTC to 05:00 UTC on the 2nd. A day is midnight to midnight on her wall clock."""
    monkeypatch.setattr(_Clock, "at", datetime(2026, 11, 1, 4, 1, tzinfo=timezone.utc))
    assert spend_day.today() == "2026-11-01"
    assert spend_day.start_of("2026-11-01") == "2026-11-01T04:00:00+00:00"
    assert spend_day.start_of("2026-11-02") == "2026-11-02T05:00:00+00:00"
    assert spend_day.next_day_starts() == datetime(2026, 11, 2, 5, tzinfo=timezone.utc).timestamp()
    assert spend_day.day_of("2026-11-02T04:30:00+00:00") == "2026-11-01"  # 23:30 there
