"""MODEL-ROUTING-TELEMETRY MRT-3 — the usage/spend fold (``routing/usage.py``).

The load-bearing test here is :func:`test_fold_matches_hand_computed_fixture`: 50 recorded lines
whose expected fold is written out BY HAND (not recomputed by the code under test), so an arithmetic
or bucketing change has to break an assertion rather than agree with itself.

The second load-bearing one is :func:`test_a_guarded_attempt_is_censused_never_summed`. The atom as
written folded ``model_calls.jsonl``; that record structurally excludes interactive chat, and a
union with the turn ledger double-counts a loop (its inner inference is recorded in both, and the
two rows share no id to dedupe on). So the fold sums the ledger and CENSUSES the audit, and that
test is what stops a future change from quietly summing them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from personalclaw.routing import usage as U
from personalclaw.routing.rates import ModelRate

DAY1 = "2026-08-01"
DAY2 = "2026-08-02"

# ── the 50-line fixture (ledger turns) ──────────────────────────────────────────────────

#: (count, date, source, provider, model, in, out, cost, priced) — 20+12+10+5+3 = 50.
FIXTURE_GROUPS = [
    (20, DAY1, "chat", "anthropic", "claude-x", 400, 40, 0.05, True),
    (12, DAY1, "loop", "ollama-models", "qwen3:8b", 200, 20, 0.0, True),
    (10, DAY2, "subagent", "anthropic", "claude-x", 50, 5, 0.002, True),
    # priced=False ⇒ an honest 0.0 that must read as a FLOOR, never as "$0 spent".
    (5, DAY2, "cron", "openai", "gpt-x", 10, 1, 0.0, False),
    # An unrecognized source is an APP NAME (chat_runner sets `source = session._app or "chat"`),
    # so it buckets to `app` and the name is censused — not treated as corrupt data.
    (3, DAY2, "my-app", "openai", "gpt-x", 7, 2, 0.03, True),
]


def _fixture_turns() -> list[dict]:
    rows: list[dict] = []
    for n, date, source, provider, model, t_in, t_out, cost, priced in FIXTURE_GROUPS:
        for _ in range(n):
            rows.append(
                {
                    "ts": f"{date}T12:00:00+00:00",
                    "session_key": "s1",
                    "source": source,
                    "agent": "",
                    "provider": provider,
                    "model": model,
                    "input_tokens": t_in,
                    "output_tokens": t_out,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cost_usd": cost,
                    "priced": priced,
                    "duration_ms": 1200,
                }
            )
    return rows


#: 12 guarded attempts — CENSUSED, never folded. Four are `loops`, the axis that also produces a
#: ledger turn; that is the overlap which makes a union unsound.
AUDIT_GROUPS = [(8, "reasoning", 0.25), (4, "loops", 0.5)]


def _fixture_attempts() -> list[dict]:
    import datetime as _dt

    ts = (
        _dt.datetime.strptime(DAY2, "%Y-%m-%d")
        .replace(hour=12, tzinfo=_dt.timezone.utc)
        .timestamp()
    )
    rows: list[dict] = []
    for n, use_case, dollars in AUDIT_GROUPS:
        for i in range(n):
            rows.append(
                {
                    "audit_id": f"{use_case}-{i}",
                    "ts": ts,
                    "use_case": use_case,
                    "provider": "anthropic",
                    "model": "claude-x",
                    "attempt": 1,
                    "tokens_in": 100,
                    "tokens_out": 10,
                    "dollars_est": dollars,
                    "estimated": True,
                    "passed": True,
                }
            )
    return rows


#: A deterministic stand-in for the rate table so the fixture's expected cells are genuinely
#: hand-computed rather than "whatever the shipped rate defaults happen to say today".
_STUB_RATES = {
    ("anthropic", "claude-x"): ModelRate(3.0, 15.0, source="overlay"),
    ("ollama-models", "qwen3:8b"): ModelRate(0.0, 0.0, source="local"),
}


@pytest.fixture
def stub_rates(monkeypatch):
    monkeypatch.setattr(U, "rate_for", lambda p, m, home=None: _STUB_RATES.get((p, m)))


def _cell(calls, t_in, t_out, dollars, *, unpriced=0, local=0):
    """One expected fold cell. A turn carries no "estimated" flag, so the fold treats every dollar
    as an estimate — hence estimated_dollars == dollars_est and estimated_calls == calls."""
    return {
        "calls": calls,
        "tokens_in": t_in,
        "tokens_out": t_out,
        "dollars_est": dollars,
        "estimated_dollars": dollars,
        "estimated_calls": calls,
        "unpriced_calls": unpriced,
        "local_calls": local,
    }


#: HAND-COMPUTED from FIXTURE_GROUPS, group by group:
#:   day1 anthropic/interactive = 20 turns, 20*400=8000 in, 20*40=800 out, 20*0.05=$1.00
#:   day1 ollama/loop           = 12 turns, 12*200=2400 in, 12*20=240 out, $0.00, all 12 local
#:   day2 anthropic/background  = 10 turns, 10*50=500 in,  10*5=50 out,   10*0.002=$0.02
#:   day2 openai/background     = 5 turns,  5*10=50 in,    5*1=5 out,     $0.00, all 5 UNPRICED
#:   day2 openai/app            = 3 turns,  3*7=21 in,     3*2=6 out,     3*0.03=$0.09
#: openai:gpt-x carries TWO purpose cells on the same day — a ref is not a purpose.
EXPECTED_DAYS = {
    DAY1: {
        "anthropic:claude-x": {"interactive": _cell(20, 8000, 800, 1.0)},
        "ollama-models:qwen3:8b": {"loop": _cell(12, 2400, 240, 0.0, local=12)},
    },
    DAY2: {
        "anthropic:claude-x": {"background": _cell(10, 500, 50, 0.02)},
        "openai:gpt-x": {
            "background": _cell(5, 50, 5, 0.0, unpriced=5),
            "app": _cell(3, 21, 6, 0.09),
        },
    },
}

#: HAND-COMPUTED census: 12 rows, 8*0.25 + 4*0.5 = $4.00, all on DAY2.
EXPECTED_UNCOUNTED = {
    "calls": 12,
    "dollars_est": 4.0,
    "by_use_case": {"reasoning": 8, "loops": 4},
    "days": {DAY2: 12},
}


def _write(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    p = tmp_path / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def _rebuild(tmp_path: Path, *, turns=None, attempts=None):
    return U.rebuild(
        tmp_path,
        ledger_path=_write(tmp_path, "turns.jsonl", _fixture_turns() if turns is None else turns),
        audit_path=_write(tmp_path, "model_calls.jsonl", [] if attempts is None else attempts),
    )


# ── the fixture assertions ──────────────────────────────────────────────────────────────


def test_fold_matches_hand_computed_fixture(tmp_path, stub_rates):
    assert len(_fixture_turns()) == 50  # the clause's 50 recorded lines
    fold = _rebuild(tmp_path)

    assert fold["days"] == EXPECTED_DAYS
    # An app-initiated turn's source is censused by name, so "which app spent" is answerable.
    assert fold["app_sources"] == {"my-app": 3}
    assert fold["unmapped"] == {}
    assert fold["sources"] == {"usage_ledger": 50}


def test_query_totals_match_the_hand_computed_fixture(tmp_path, stub_rates):
    fold = _rebuild(tmp_path)
    got = U.query(fold, window="week", group="purpose", today=DAY2)
    total = got["total"]
    # Hand-computed: 50 turns; 8000+2400+500+50+21 = 10971 in; 800+240+50+5+6 = 1101 out;
    # $1.00 + $0.00 + $0.02 + $0.00 + $0.09 = $1.11.
    assert (total["calls"], total["tokens_in"], total["tokens_out"]) == (50, 10971, 1101)
    assert total["dollars_est"] == 1.11
    assert total["tokens"] == 12072
    # Every dollar is an estimate; 5 unpriced turns make the total a FLOOR — separate disclosures.
    assert got["estimated_share"] == 1.0
    assert total["unpriced_calls"] == 5
    assert total["priced"] is False
    assert total["local_calls"] == 12

    assert [(r["key"], r["calls"], r["dollars_est"]) for r in got["rows"]] == [
        ("interactive", 20, 1.0),
        ("app", 3, 0.09),
        ("background", 15, 0.02),  # 10 subagent + 5 cron
        ("loop", 12, 0.0),
    ]
    by_model = U.query(fold, window="week", group="model", today=DAY2)
    assert [(r["key"], r["dollars_est"]) for r in by_model["rows"]] == [
        ("anthropic:claude-x", 1.02),  # $1.00 (day1 chat) + $0.02 (day2 subagent)
        ("openai:gpt-x", 0.09),
        ("ollama-models:qwen3:8b", 0.0),
    ]
    by_provider = U.query(fold, window="week", group="provider", today=DAY2)
    assert [r["key"] for r in by_provider["rows"]] == ["anthropic", "openai", "ollama-models"]


def test_window_narrows_to_the_reference_day(tmp_path, stub_rates):
    fold = _rebuild(tmp_path)
    day = U.query(fold, window="day", group="model", today=DAY2)
    # DAY2 only: 10 + 5 + 3 = 18 turns at $0.02 + $0.00 + $0.09 = $0.11.
    assert (day["total"]["calls"], day["total"]["dollars_est"]) == (18, 0.11)
    assert day["dates"] == [DAY2]

    week = U.query(fold, window="week", group="model", today=DAY2)
    assert len(week["series"]) == 7
    assert week["series"][-1] == {"date": DAY2, "calls": 18, "dollars_est": 0.11, "tokens": 632}
    assert week["series"][0]["calls"] == 0  # a day with no traffic is a zero, not a gap


# ── the census: what the fold refuses to sum ────────────────────────────────────────────


def test_a_guarded_attempt_is_censused_never_summed(tmp_path, stub_rates):
    """The design decision this atom turns on. A guarded ``complete()`` attempt must appear in the
    census and NOWHERE in the money totals: its `loops` rows overlap the ledger's `source="loop"`
    turns for the same inference, and no shared id exists to dedupe them."""
    fold = _rebuild(tmp_path, attempts=_fixture_attempts())

    assert fold["uncounted"] == EXPECTED_UNCOUNTED
    # The money is IDENTICAL with and without the audit file — proof it is not being summed in.
    without = _rebuild(tmp_path, attempts=[])
    assert fold["days"] == without["days"] == EXPECTED_DAYS

    got = U.query(fold, window="day", group="purpose", today=DAY2)
    assert got["total"]["dollars_est"] == 0.11  # not 0.11 + 4.00
    # ...but the gap is STATED, with the overlapping axis named.
    assert got["uncounted"]["calls"] == 12
    assert got["uncounted"]["total_dollars_est"] == 4.0
    assert got["uncounted"]["by_use_case"] == {"reasoning": 8, "loops": 4}


def test_the_census_is_empty_when_no_attempts_were_recorded(tmp_path, stub_rates):
    fold = _rebuild(tmp_path, attempts=[])
    assert fold["uncounted"] == {"calls": 0, "dollars_est": 0.0, "by_use_case": {}, "days": {}}
    assert U.query(fold, window="day", today=DAY2)["uncounted"]["calls"] == 0


# ── reproducible after delete ───────────────────────────────────────────────────────────


def test_deleting_the_fold_rebuilds_the_same_values(tmp_path, stub_rates):
    ledger = _write(tmp_path, "turns.jsonl", _fixture_turns())
    audit = _write(tmp_path, "model_calls.jsonl", _fixture_attempts())
    before = U.refresh(tmp_path, audit_path=audit, ledger_path=ledger)
    persisted = (tmp_path / "usage_stats.json").read_text(encoding="utf-8")
    assert before["days"] == EXPECTED_DAYS

    (tmp_path / "usage_stats.json").unlink()
    assert U.load_usage(tmp_path)["days"] == {}  # gone, not cached in memory

    after = U.refresh(tmp_path, audit_path=audit, ledger_path=ledger)
    assert after["days"] == before["days"] == EXPECTED_DAYS
    assert after["uncounted"] == EXPECTED_UNCOUNTED
    # Byte-identical on disk too — the fold is a deterministic function of its inputs.
    assert (tmp_path / "usage_stats.json").read_text(encoding="utf-8") == persisted


def test_refresh_keeps_a_day_that_aged_out_of_the_capped_jsonl(tmp_path, stub_rates):
    """The whole reason a durable fold earns its place beside the ledger's own day rollup: the
    JSONL trims, so a refold alone would silently lose history."""
    ledger = _write(tmp_path, "turns.jsonl", _fixture_turns())
    audit = _write(tmp_path, "model_calls.jsonl", [])
    U.refresh(tmp_path, audit_path=audit, ledger_path=ledger)

    # Simulate the trim: DAY1's rows roll off the tail.
    _write(tmp_path, "turns.jsonl", [r for r in _fixture_turns() if not r["ts"].startswith(DAY1)])
    kept = U.refresh(tmp_path, audit_path=audit, ledger_path=ledger)

    assert kept["days"][DAY1] == EXPECTED_DAYS[DAY1]  # archived, not recomputed away
    assert kept["days"][DAY2] == EXPECTED_DAYS[DAY2]


# ── purpose mapping + unattributable rows ───────────────────────────────────────────────


def test_an_unknown_source_is_an_app_and_is_named(tmp_path):
    assert U.purpose_for_source("chat") == ("interactive", "")
    assert U.purpose_for_source("loop") == ("loop", "")
    assert U.purpose_for_source("weather-app") == ("app", "weather-app")
    # A blank source still lands in a real bucket and is still named, so it cannot vanish.
    assert U.purpose_for_source("") == ("app", "(unnamed)")


def test_a_row_with_no_usable_day_is_counted_not_silently_discarded(tmp_path):
    fold = U.empty_fold()
    # False = "did not land in a day cell" — but it is still ACCOUNTED FOR in `unmapped`, which is
    # the difference between a visible gap and a silently shrinking total.
    landed = U.fold_turn_row(fold, {"provider": "x", "model": "y"}, look=lambda p, m: (True, False))
    assert landed is False
    assert fold["days"] == {}
    assert fold["unmapped"] == {"row:no_date": 1}

    no_ref = U.empty_fold()
    U.fold_turn_row(no_ref, {"ts": f"{DAY1}T00:00:00+00:00"}, look=lambda p, m: (True, False))
    assert no_ref["unmapped"] == {"row:no_ref": 1}


def test_reachable_purposes_reports_only_what_a_writer_can_produce():
    """`eval` alone has no turn-ledger writer; `loop` has one and is reachable.

    This assertion has now been wrong in BOTH directions. It first claimed `loop` was reachable
    for no stated reason; it was then "corrected" to exclude `loop` on a census of LITERAL
    `source=` arguments, which cannot see the loop's spelling because the loop's spelling is a
    runtime value: `loop/manager.py` names the worker session `app="loop"` and `chat_runner`
    passes `session._app or "chat"` as the source, so `PURPOSE_BY_SOURCE["loop"]` is hit on the
    first lookup. `eval` really has none — no call site passes the literal and no session is
    created with `app="eval"`.

    `test_usage_reachable_purposes.py` censuses the real call sites AND the `app=` literals, so a
    writer appearing (or disappearing) goes red there with the reason rather than here with a bare
    tuple mismatch."""
    assert U.reachable_purposes() == ("interactive", "background", "loop", "app")
    assert set(U.PURPOSES) - set(U.reachable_purposes()) == {"eval"}
    assert U.UNWRITTEN_PURPOSES == {"eval"}


def test_every_mapping_target_is_in_the_fixed_vocabulary():
    for raw, purpose in U.PURPOSE_BY_SOURCE.items():
        assert purpose in U.PURPOSES, f"{raw} maps outside the vocabulary: {purpose}"
    assert U.APP_PURPOSE in U.PURPOSES


def test_purpose_mapping_covers_every_documented_ledger_source():
    """`TurnUsage.source`'s documented vocabulary is the contract. A new literal added there must
    be mapped deliberately, not fall through to `app` and read as an app's spend."""
    from personalclaw import usage_ledger

    text = Path(usage_ledger.__file__).read_text(encoding="utf-8")
    m = re.search(r"source: str  # (.+)", text)
    assert m, "TurnUsage.source's documented vocabulary moved"
    sources = [s.strip() for s in m.group(1).split("|")]
    assert len(sources) >= 5, "vacuity guard: the documented source list must be non-trivial"
    assert set(sources) <= set(
        U.PURPOSE_BY_SOURCE
    ), f"sources that would be mistaken for app names: {set(sources) - set(U.PURPOSE_BY_SOURCE)}"


def test_the_guarded_use_cases_are_all_accounted_for_in_the_census():
    """`provider_bridge` decides which use_cases reach the attempt audit. They are censused, not
    folded, so this rail only has to prove the set is still the one the docstring names."""
    src = Path(__import__("personalclaw.providers.provider_bridge", fromlist=["x"]).__file__)
    text = src.read_text(encoding="utf-8")
    m = re.search(r'if use_case in \(([^)]*)\):\n\s+kwargs\["_guard_use_case"\]', text)
    assert m, "the guarded use_case tuple moved — re-read the census rationale before trusting it"
    guarded = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert guarded, "vacuity guard: the rail must actually match some use_cases"
    assert guarded == {"reasoning", "background", "loops", "orchestration"}, guarded
    # `loops` being in that set is exactly why a union would double-count.
    assert "loops" in guarded


# ── unpriced vs local vs estimated ──────────────────────────────────────────────────────


def test_priced_false_wins_over_the_rate_table(tmp_path, stub_rates):
    """openai:gpt-x has no stub rate, yet the app rows carry `priced: True` — the ledger's own
    disclosure is authoritative, so those turns are NOT reported as unpriced."""
    fold = _rebuild(tmp_path)
    day2 = fold["days"][DAY2]["openai:gpt-x"]
    assert day2["app"]["unpriced_calls"] == 0  # priced: True
    assert day2["background"]["unpriced_calls"] == 5  # priced: False


def test_local_and_unpriced_come_from_the_real_rate_table(tmp_path):
    """The stubbed fixture proves the arithmetic; this proves the fold actually asks rates.py."""
    fold = U.empty_fold()
    look = U._rate_lookup(tmp_path)
    base = {"ts": f"{DAY1}T00:00:00+00:00", "source": "chat", "input_tokens": 1, "output_tokens": 1}
    U.fold_turn_row(fold, {**base, "provider": "ollama", "model": "qwen3:8b"}, look=look)
    U.fold_turn_row(fold, {**base, "provider": "nonesuch", "model": "no-such-model-xyz"}, look=look)
    cells = fold["days"][DAY1]
    assert cells["ollama:qwen3:8b"]["interactive"]["local_calls"] == 1
    assert cells["ollama:qwen3:8b"]["interactive"]["unpriced_calls"] == 0
    assert cells["nonesuch:no-such-model-xyz"]["interactive"]["unpriced_calls"] == 1


def test_an_empty_or_missing_jsonl_is_an_empty_fold_not_an_error(tmp_path):
    fold = U.rebuild(
        tmp_path, audit_path=tmp_path / "nope.jsonl", ledger_path=tmp_path / "no.jsonl"
    )
    assert fold["days"] == {}
    assert fold["sources"] == {"usage_ledger": 0}
    assert U.query(fold, window="month")["estimated_share"] == 0.0  # no dollars ⇒ no share to claim


def test_a_corrupt_line_is_skipped_and_the_rest_still_folds(tmp_path, stub_rates):
    good = json.dumps(_fixture_turns()[0])
    p = tmp_path / "turns.jsonl"
    p.write_text(f"{good}\nnot json at all\n{good}\n", encoding="utf-8")
    fold = U.rebuild(tmp_path, ledger_path=p, audit_path=tmp_path / "none.jsonl")
    assert fold["sources"]["usage_ledger"] == 2


# ── the recap ───────────────────────────────────────────────────────────────────────────


def test_usage_recap_renders_verbatim_predictable(tmp_path, stub_rates):
    fold = _rebuild(tmp_path, attempts=_fixture_attempts())
    # Hand-computed from the same fixture: $1.11 over 50 turns, 12 local (24%), interactive $1.00
    # ahead of app $0.09, biggest line item anthropic:claude-x at $1.02, 5 unpriced, 12 uncounted.
    assert U.usage_recap("2026-08", fold=fold) == (
        "August 2026: ~$1.11 across 50 turns."
        " 24% of those turns ran locally at $0."
        " By purpose: interactive ~$1.00, app ~$0.0900, background ~$0.0200, loop ~$0.0000."
        " Biggest line item: anthropic:claude-x (~$1.02)."
        " Every dollar here is an estimate, not a provider-reported charge."
        " 5 turns ran on a model with no price row and counted as $0, so the total is a floor."
        " Separately, 12 unattended model calls were recorded this month but are not included"
        " above — they cannot be merged with turns without double-counting loops."
    )
    # Deterministic: same fold in, same sentence out (no LLM, no clock).
    assert U.usage_recap("2026-08", fold=fold) == U.usage_recap("2026-08", fold=fold)


def test_usage_recap_on_an_empty_month_says_so_instead_of_zero_dollars(tmp_path):
    assert (
        U.usage_recap("2026-09", fold=U.empty_fold()) == "September 2026: no model turns recorded."
    )


def test_usage_recap_reads_the_persisted_fold_from_a_home(tmp_path, stub_rates):
    _rebuild(tmp_path)
    assert U.usage_recap("2026-08", home=tmp_path).startswith(
        "August 2026: ~$1.11 across 50 turns."
    )
