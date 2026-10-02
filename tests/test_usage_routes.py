"""The /api/usage read routes.

Read-only rollup + totals over the usage ledger, with the shared
{error:{code,message}} envelope on a bad group_by.

The routes that count days count hers: the calendar day in her timezone (``config.timezone``),
whatever zone the gateway's own clock is in. Every test here runs with her timezone set to
America/Toronto, the process clock on UTC (as a container's is), and the clock pinned, so none
depends on the hour it runs at.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import spend_day
from personalclaw import usage_ledger as ul
from personalclaw.dashboard.handlers.usage import register_usage_routes
from personalclaw.usage_ledger import TurnUsage

ZONE = "America/Toronto"

#: 20:01 on 1 October in Toronto: the UTC date is already the 2nd, her day is still the 1st.
EVENING = datetime(2026, 10, 2, 0, 1, tzinfo=timezone.utc)
EVENING_DAY = "2026-10-01"


class _Clock(datetime):
    """The clock at :attr:`at`, read in whatever zone is asked for."""

    at = EVENING

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.at.astimezone(tz) if tz is not None else cls.at.astimezone().replace(tzinfo=None)


def _config(home, **budgets) -> None:
    """Her config.json: her timezone, and the daily caps when a test sets them."""
    doc: dict = {"timezone": ZONE}
    if budgets:
        doc["guardrails"] = {"budgets": budgets}
    (home / "config.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _config(tmp_path)
    monkeypatch.setattr(spend_day, "datetime", _Clock)
    monkeypatch.setattr(_Clock, "at", EVENING)
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        yield tmp_path
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def _seed():
    for src, cost in (("chat", 1.0), ("chat", 2.0), ("subagent", 0.5)):
        ul.record_turn(
            TurnUsage(
                ts="2026-08-06T12:00:00+00:00",
                session_key="s1",
                source=src,
                agent="",
                provider="anthropic",
                model="claude-opus-4.5",
                input_tokens=100,
                output_tokens=20,
                cost_usd=cost,
                priced=True,
            )
        )


async def _client() -> TestClient:
    app = web.Application()
    register_usage_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_rollup_by_source(_home):
    _seed()
    c = await _client()
    try:
        resp = await c.get("/api/usage/rollup?group_by=source")
        assert resp.status == 200
        body = await resp.json()
        assert body["group_by"] == "source"
        by = {r["source"]: r for r in body["rows"]}
        assert by["chat"]["cost_usd"] == 3.0 and by["chat"]["turns"] == 2
        assert by["subagent"]["cost_usd"] == 0.5
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_rollup_defaults_to_model(_home):
    _seed()
    c = await _client()
    try:
        body = await (await c.get("/api/usage/rollup")).json()
        assert body["group_by"] == "model"
        assert body["rows"][0]["model"] == "claude-opus-4.5"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_bad_group_by_is_400_envelope(_home):
    c = await _client()
    try:
        resp = await c.get("/api/usage/rollup?group_by=nonsense")
        assert resp.status == 400
        body = await resp.json()
        assert body["error"]["code"] == "bad_request"
        assert "group_by" in body["error"]["message"]
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_totals(_home):
    _seed()
    c = await _client()
    try:
        body = await (await c.get("/api/usage/totals")).json()
        assert body["totals"]["cost_usd"] == 3.5
        assert body["totals"]["turns"] == 3
        assert body["totals"]["priced"] is True
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_window_filter_threads_through(_home):
    ul.record_turn(
        TurnUsage(
            ts="2026-08-01T00:00:00+00:00",
            session_key="s",
            source="chat",
            agent="",
            provider="p",
            model="claude-opus-4.5",
            cost_usd=1.0,
            priced=True,
        )
    )
    ul.record_turn(
        TurnUsage(
            ts="2026-08-05T00:00:00+00:00",
            session_key="s",
            source="chat",
            agent="",
            provider="p",
            model="claude-opus-4.5",
            cost_usd=2.0,
            priced=True,
        )
    )
    c = await _client()
    try:
        body = await (await c.get("/api/usage/totals?since=2026-08-03T00:00:00+00:00")).json()
        assert body["totals"]["cost_usd"] == 2.0 and body["totals"]["turns"] == 1
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_empty_ledger_is_ok_not_error(_home):
    c = await _client()
    try:
        r = await c.get("/api/usage/rollup?group_by=source")
        assert r.status == 200 and (await r.json())["rows"] == []
        t = await c.get("/api/usage/totals")
        assert t.status == 200 and (await t.json())["totals"]["turns"] == 0
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_session_filter_threads_through(_home):
    for sk, cost in (("dashboard:a", 1.0), ("dashboard:a", 2.0), ("dashboard:b", 0.5)):
        ul.record_turn(
            TurnUsage(
                ts="2026-08-06T12:00:00+00:00",
                session_key=sk,
                source="chat",
                agent="",
                provider="anthropic",
                model="claude-opus-4.5",
                input_tokens=100,
                output_tokens=20,
                cost_usd=cost,
                priced=True,
            )
        )
    c = await _client()
    try:
        body = await (await c.get("/api/usage/totals?session=dashboard:a")).json()
        assert body["session"] == "dashboard:a"
        assert body["totals"]["cost_usd"] == 3.0 and body["totals"]["turns"] == 2
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_session_totals_join_bare_frontend_key_to_namespaced_ledger_rows(_home):
    """The join that mattered. ``ChatPage``'s cost chip queries
    ``?session=<bare id>`` — the literal ``sessionId``/URL key it holds, never
    ``dashboard:``-prefixed. But every chat turn is written by
    ``chat_runner.run_chat`` under the NAMESPACED key (``_history_key_for`` →
    ``dashboard:<id>``). Before the fix, the route passed the bare param
    straight to ``ul.totals``/``ul.rollup``, which match ``session_key`` by exact
    string equality — so the two halves never joined and the chip silently read a
    confident 0 over real spend (the fix mirrors ``openai_dialect.py``/``cli_run.py``,
    which already wrap with ``dashboard_session_key`` before querying the same ledger).

    This test seeds a MULTI-turn session (3 turns, distinct token/cost values) plus a
    same-name-prefix decoy session that must NOT leak in, then asserts the header total
    (``/api/usage/totals?session=<bare id>``) equals the exact sum of that session's own
    turn rows for cost AND both token axes — not merely that the request succeeds or the
    chip has *some* nonzero number, which would pass vacuously on the wrong join."""
    turns = [  # (input_tokens, output_tokens, cost_usd) — three distinct turns
        (100, 20, 0.10),
        (250, 63, 0.30),
        (400, 91, 0.55),
    ]
    for input_tokens, output_tokens, cost in turns:
        ul.record_turn(
            TurnUsage(
                ts="2026-09-18T12:00:00+00:00",
                session_key="dashboard:abc123",  # the namespaced form chat_runner writes
                source="chat",
                agent="",
                provider="anthropic",
                model="claude-opus-4.5",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                priced=True,
            )
        )
    # A same-prefix decoy session ("abc1234", not "abc123") must not bleed in via a bare
    # startswith — _session_matches already guards this; belt-and-suspenders here.
    ul.record_turn(
        TurnUsage(
            ts="2026-09-18T12:05:00+00:00",
            session_key="dashboard:abc1234",
            source="chat",
            agent="",
            provider="anthropic",
            model="claude-opus-4.5",
            input_tokens=999,
            output_tokens=999,
            cost_usd=99.0,
            priced=True,
        )
    )
    c = await _client()
    try:
        # The frontend's ACTUAL call shape: the bare id, never the namespaced form.
        body = await (await c.get("/api/usage/totals?session=abc123")).json()
        t = body["totals"]
        assert t["turns"] == len(turns)
        assert t["cost_usd"] == pytest.approx(sum(c for _, _, c in turns))
        assert t["input_tokens"] == sum(i for i, _, _ in turns)
        assert t["output_tokens"] == sum(o for _, o, _ in turns)

        # The rollup endpoint shares the exact same join and must agree with totals —
        # grouping by day collapses the 3 turns into one row summing to the same total.
        rollup_body = await (await c.get("/api/usage/rollup?group_by=day&session=abc123")).json()
        assert len(rollup_body["rows"]) == 1
        assert rollup_body["rows"][0]["turns"] == len(turns)
        assert rollup_body["rows"][0]["cost_usd"] == pytest.approx(sum(c for _, _, c in turns))
    finally:
        await c.close()


# ── GET /api/usage — the per-day spend fold ─────────────────────────────────────────────
#
# The sibling routes above read the retained tail of the same ledger. This one reads the DURABLE
# per-day fold over it, grouped into the purpose vocabulary — and reports the guarded-attempt spend
# it deliberately does NOT sum (a loop's inner inference is in both records, with no shared id).


def _seed_attempt(home, *, use_case: str, dollars: float, provider="anthropic", model="claude-x"):
    """Append one guarded-attempt row, made now — the axis the fold censuses instead of summing."""
    rec = {
        "audit_id": "a1",
        "ts": _Clock.at.timestamp(),
        "use_case": use_case,
        "provider": provider,
        "model": model,
        "attempt": 1,
        "tokens_in": 100,
        "tokens_out": 10,
        "dollars_est": dollars,
        "estimated": True,
        "passed": True,
    }
    with (home / "model_calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _seed_turn(source="chat", cost=1.0, model="claude-x", at: datetime | None = None):
    """Record one turn, made *at* that instant (now, by default)."""
    ul.record_turn(
        TurnUsage(
            ts=(at or _Clock.at).isoformat(),
            session_key="s1",
            source=source,
            agent="",
            provider="anthropic",
            model=model,
            input_tokens=400,
            output_tokens=40,
            cost_usd=cost,
            priced=True,
        )
    )


@pytest.mark.asyncio
async def test_usage_fold_route_returns_rows_total_and_estimated_share(_home):
    _seed_turn(source="chat", cost=1.0)
    _seed_turn(source="loop", cost=0.25)
    c = await _client()
    try:
        body = await (await c.get("/api/usage?window=day&group=purpose")).json()
        assert body["window"] == "day" and body["group"] == "purpose"
        keyed = {r["key"]: r for r in body["rows"]}
        assert keyed["interactive"]["dollars_est"] == 1.0
        assert keyed["loop"]["dollars_est"] == 0.25
        assert body["total"]["calls"] == 2
        assert body["total"]["dollars_est"] == 1.25
        assert body["estimated_share"] == 1.0  # a turn carries no reported-cost flag
        assert len(body["series"]) == 1 and body["series"][0]["date"] == EVENING_DAY
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_usage_fold_route_states_the_spend_it_does_not_count(_home):
    """The honesty clause: unattended spend must be visible as an excluded figure, not omitted."""
    _seed_turn(source="chat", cost=1.0)
    _seed_attempt(_home, use_case="reasoning", dollars=0.4)
    _seed_attempt(_home, use_case="loops", dollars=0.6)
    c = await _client()
    try:
        body = await (await c.get("/api/usage?window=day")).json()
        assert body["total"]["dollars_est"] == 1.0  # NOT 1.0 + 0.4 + 0.6
        assert body["uncounted"]["calls"] == 2
        assert body["uncounted"]["total_dollars_est"] == 1.0
        assert body["uncounted"]["by_use_case"] == {"reasoning": 1, "loops": 1}
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_usage_fold_route_names_the_app_that_spent(_home):
    _seed_turn(source="weather-app", cost=0.2)
    c = await _client()
    try:
        body = await (await c.get("/api/usage?window=day&group=purpose")).json()
        assert [r["key"] for r in body["rows"]] == ["app"]
        assert body["app_sources"] == {"weather-app": 1}
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_usage_fold_route_self_heals_a_deleted_fold(_home):
    _seed_turn(source="chat", cost=0.4)
    c = await _client()
    try:
        first = await (await c.get("/api/usage")).json()
        assert first["total"]["dollars_est"] == 0.4
        assert (_home / "usage_stats.json").is_file()
        (_home / "usage_stats.json").unlink()
        second = await (await c.get("/api/usage")).json()
        assert second["total"] == first["total"]
        assert (_home / "usage_stats.json").is_file()
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_bad_window_and_group_are_400_envelopes(_home):
    c = await _client()
    try:
        r = await c.get("/api/usage?window=fortnight")
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "bad_request"
        r2 = await c.get("/api/usage?group=vibes")
        assert r2.status == 400
        assert "group must be one of" in (await r2.json())["error"]["message"]
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_empty_home_is_an_empty_fold_not_an_error(_home):
    c = await _client()
    try:
        r = await c.get("/api/usage?window=month")
        assert r.status == 200
        body = await r.json()
        assert body["rows"] == [] and body["total"]["calls"] == 0
        assert body["estimated_share"] == 0.0
        assert body["uncounted"]["calls"] == 0
    finally:
        await c.close()


# ── /api/usage/budget: the daily cap beside the spend it is held to ──────────────────────


@pytest.mark.asyncio
async def test_the_daily_budget_is_the_meters_spend_beside_the_cap(_home):
    """The cap meters the calls PersonalClaw makes on its own; the ledger holds every chat turn
    too. The line used to set the ledger's figure beside the cap: $3.50, $3 of it chat, against a
    $1 cap read as a cap spent three times over while the meter held $0.25."""
    from personalclaw.guardrails.budgets import get_meter

    _seed()  # $3.50 in the ledger, chat turns included
    get_meter().charge(1200, 0.25)
    _config(_home, max_dollars_per_day=1.0)
    c = await _client()
    try:
        r = await c.get("/api/usage/budget")
        assert r.status == 200
        body = await r.json()
        resets_at = body.pop("resets_at")
        assert body == {
            "spent_dollars": 0.25,
            "spent_tokens": 1200,
            "unpriced_calls": 0,
            "max_dollars_per_day": 1.0,
            "max_tokens_per_day": 0,
            "cap_unreadable": False,
        }
        # The caps start afresh at her next midnight: 00:00 on 2 October in Toronto, 04:00 UTC.
        # The gateway's own clock (UTC) passed its midnight at 20:00 her time, a minute ago.
        assert resets_at == datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc).timestamp()
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_the_daily_budget_says_how_many_calls_its_dollars_leave_out(_home):
    """A metered call nothing priced is charged as one the dollar cap could not count, never as
    free spend: the route says how many there were, so the page can say the total leaves them
    out. Its tokens are counted as any call's are."""
    from personalclaw.guardrails.budgets import get_meter

    get_meter().charge(1200, 0.25)
    get_meter().charge(800, 0.0, unpriced=1)
    get_meter().charge(0, 0.0, unpriced=1)  # an unpriced call that reported no tokens
    _config(_home, max_dollars_per_day=1.0)
    c = await _client()
    try:
        body = await (await c.get("/api/usage/budget")).json()
        assert body["spent_dollars"] == 0.25
        assert body["spent_tokens"] == 2000
        assert body["unpriced_calls"] == 2
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_a_cap_that_cannot_be_read_is_said_to_be_unreadable(_home, monkeypatch):
    """Not an unlimited 0 nobody chose: the caps are null and the answer says why."""
    from personalclaw.guardrails import budgets

    def _unreadable():
        raise budgets.BudgetConfigUnreadable(ValueError("config.json is not JSON"))

    monkeypatch.setattr(budgets, "budget_from_config", _unreadable)
    c = await _client()
    try:
        body = await (await c.get("/api/usage/budget")).json()
        assert body["cap_unreadable"] is True
        assert body["max_dollars_per_day"] is None and body["max_tokens_per_day"] is None
    finally:
        await c.close()


# ── her midnight, not UTC's: a minute either side of each ───────────────────────────────


def _at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


#: (now, her day then, the instant her day started, the instant it ends), in October, when
#: Toronto is UTC-4.
MIDNIGHT_MINUTES = [
    pytest.param(_at(2, 3, 59), "2026-10-01", _at(1, 4, 0), _at(2, 4, 0), id="2359-in-toronto"),
    pytest.param(_at(2, 4, 1), "2026-10-02", _at(2, 4, 0), _at(3, 4, 0), id="0001-in-toronto"),
    pytest.param(_at(1, 23, 59), "2026-10-01", _at(1, 4, 0), _at(2, 4, 0), id="2359-utc"),
    pytest.param(_at(2, 0, 1), "2026-10-01", _at(1, 4, 0), _at(2, 4, 0), id="0001-utc"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("now", "her_day", "day_starts", "day_ends"), MIDNIGHT_MINUTES)
async def test_a_turn_counts_on_her_day_on_every_route(
    _home, monkeypatch, now, her_day, day_starts, day_ends
):
    """A turn made now is in Today on every route, on her day, and the cap counts it on her day
    and starts afresh at her next midnight, whichever side of hers or UTC's the minute is on."""
    from personalclaw.guardrails.budgets import get_meter

    monkeypatch.setattr(_Clock, "at", now)
    _seed_turn(source="loop", cost=0.75)
    get_meter().charge(900, 0.75)
    _config(_home, max_dollars_per_day=5.0)
    c = await _client()
    try:
        fold = await (await c.get("/api/usage?window=day")).json()
        assert fold["dates"] == [her_day]
        assert fold["total"]["calls"] == 1 and fold["total"]["dollars_est"] == 0.75

        totals = await (await c.get("/api/usage/totals?window=day")).json()
        assert totals["since"] == day_starts.isoformat()
        assert totals["totals"]["turns"] == 1

        by_day = await (await c.get("/api/usage/rollup?group_by=day&window=day")).json()
        assert [(r["day"], r["turns"]) for r in by_day["rows"]] == [(her_day, 1)]

        budget = await (await c.get("/api/usage/budget")).json()
        assert (budget["spent_tokens"], budget["spent_dollars"]) == (900, 0.75)
        assert budget["resets_at"] == day_ends.timestamp()
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "now", "today", "week"),
    [
        pytest.param(
            _at(2, 3, 59),
            _at(2, 4, 1),
            1,
            [("2026-10-01", 1), ("2026-10-02", 1)],
            id="her-midnight-divides",
        ),
        pytest.param(
            _at(1, 23, 59), _at(2, 0, 1), 2, [("2026-10-01", 2)], id="utc-midnight-does-not"
        ),
    ],
)
async def test_her_midnight_divides_her_days_and_utc_midnight_does_not(
    _home, monkeypatch, before, now, today, week
):
    """Two turns two minutes apart: one at 23:59, one at 00:01. Across her midnight they are two
    days, in the Usage tiles, the chart and the cap alike; across UTC's (20:00 hers) they are one.
    """
    from personalclaw.guardrails.budgets import get_meter

    for at in (before, now):
        monkeypatch.setattr(_Clock, "at", at)
        _seed_turn(source="loop", cost=0.5)
        get_meter().charge(100, 0.5)
    _config(_home, max_dollars_per_day=5.0)
    c = await _client()
    try:
        fold = await (await c.get("/api/usage?window=day")).json()
        assert fold["total"]["calls"] == today
        totals = await (await c.get("/api/usage/totals?window=day")).json()
        assert totals["totals"]["turns"] == today
        budget = await (await c.get("/api/usage/budget")).json()
        assert budget["spent_tokens"] == 100 * today

        chart = await (await c.get("/api/usage?window=week")).json()
        assert [(s["date"], s["calls"]) for s in chart["series"] if s["calls"]] == week
        by_day = await (await c.get("/api/usage/rollup?group_by=day&window=week")).json()
        assert sorted((r["day"], r["turns"]) for r in by_day["rows"]) == week
    finally:
        await c.close()
