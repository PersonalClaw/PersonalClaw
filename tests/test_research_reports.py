"""Scheduled research reports — the definition round-trip, what a report says it reads, and the
record of its runs.

Each of the three run-record rules named in ``knowledge/research_reports.py``'s docstring gets a
test here: a run says what it found (``nothing_new`` is a finished run with its own sentence, never
a bare "ok"), a failed run advances neither the stamp nor the watermark, and the watermark is the
time the runner resolved its scope. WHEN a report runs is its automation's, and is driven in
``test_a_reports_schedule_is_one_schedule.py``.

Tests that call ``record_run`` anchor their simulated clock on the real one, because
``record_run`` stamps ``time.time()`` by design — a synthetic "now" far from the wall clock would
make the recorded run look either ancient or far in the future.
"""

from __future__ import annotations

import json
import time

import pytest

from personalclaw.knowledge.research_reports import (
    ALLOW_CITING_CONTEXT,
    CITE_SOURCE_ONLY,
    FINDING_KIND,
    MAX_ITERATION_CAP,
    NOTHING_NEW,
    ReportDefinition,
    Scope,
    delete_report,
    from_dict,
    get_report,
    load_reports,
    nothing_new_words,
    record_run,
    save_report,
    sources_shown,
    to_dict,
    wrote_words,
)
from personalclaw.schedule import ScheduleDefinition

HOUR = 3600.0


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """`PERSONALCLAW_HOME` *and* the module-bound `config_dir` — the env var alone
    would still be missed by anything that bound the function at import time, and the
    real `~/.personalclaw` must never be touched by this suite."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.knowledge.research_reports.config_dir", lambda: home)
    return home


def _store_file(home):
    return home / "research_reports.json"


def _defn(**over) -> ReportDefinition:
    kw: dict = {
        "id": "",
        "name": "Weekly deps",
        "prompt": "What changed in my dependency sources?",
        "schedule": ScheduleDefinition(kind="every", every_secs=int(HOUR)),
    }
    kw.update(over)
    return ReportDefinition(**kw)


def _saved_with_last_run(report_id: str, *, created_ts: float, last_run_ts: float, **over):
    """Persist a report that has already run once."""
    defn = save_report(_defn(id=report_id, created_ts=created_ts, **over))
    defn.last_run_ts = last_run_ts
    return save_report(defn)


# ── store ──


def test_absent_store_loads_empty():
    assert load_reports() == []


def test_corrupt_store_loads_empty_and_never_raises(_isolated_home):
    _store_file(_isolated_home).write_text("{not json at all", encoding="utf-8")
    assert load_reports() == []
    # A non-list payload is equally survivable: the gateway reads this on its
    # scheduler path, so an unreadable store degrades to "no reports", never a crash.
    _store_file(_isolated_home).write_text('{"id": "x"}', encoding="utf-8")
    assert load_reports() == []


def test_save_assigns_id_and_created_ts():
    saved = save_report(_defn())
    assert saved.id
    assert saved.created_ts > 0
    assert get_report(saved.id) is not None
    assert [d.id for d in load_reports()] == [saved.id]


def test_save_replaces_by_id_and_delete_reports_whether_it_removed_anything():
    saved = save_report(_defn())
    saved.name = "renamed"
    save_report(saved)
    assert [d.name for d in load_reports()] == ["renamed"]
    assert delete_report(saved.id) is True
    assert delete_report(saved.id) is False
    assert load_reports() == []


def test_save_rejects_unknown_citation_policy():
    with pytest.raises(ValueError, match="invalid citation_policy"):
        save_report(_defn(citation_policy="cite-whatever"))


def test_save_clamps_iteration_cap():
    # An unbounded cap is an unbounded spend on an unattended, recurring surface.
    assert save_report(_defn(iteration_cap=9999)).iteration_cap == MAX_ITERATION_CAP
    assert save_report(_defn(id="z", iteration_cap=0)).iteration_cap == 1


# ── round-trip ──


def test_round_trip_is_json_safe_with_context_none():
    defn = save_report(_defn(citation_policy=CITE_SOURCE_ONLY, last_result="Found nothing."))
    raw = to_dict(defn)
    json.dumps(raw)  # the API layer serves this dict verbatim
    assert raw["context"] is None
    assert from_dict(raw) == defn


def test_round_trip_with_populated_context_scope():
    defn = save_report(
        _defn(
            source=Scope(tags=("deps", "security"), window_secs=0),
            context=Scope(tags=("notes",), window_secs=7 * 86400),
            citation_policy=ALLOW_CITING_CONTEXT,
            schedule=ScheduleDefinition(kind="cron", cron_expr="0 7 * * 1"),
            tz="America/Los_Angeles",
        )
    )
    back = from_dict(to_dict(defn))
    assert back == defn
    assert back.source.tags == ("deps", "security")
    assert back.context is not None and back.context.window_secs == 7 * 86400
    assert back.schedule.cron_expr == "0 7 * * 1"


def test_from_dict_is_tolerant_of_unknown_keys_and_bad_types():
    back = from_dict(
        {
            "id": "r1",
            "name": 17,  # wrong type → default
            "prompt": "p",
            # A string cadence is unusable → None, never 0 (a hot schedule).
            "schedule": {"kind": "every", "every_secs": "60"},
            "source": "nonsense",  # → empty Scope
            "context": {"tags": ["a", 5, ""], "window_secs": -9},
            "citation_policy": "bogus",  # → the safe default
            "iteration_cap": 500,  # → clamped
            "enabled": "yes",  # not a bool → default True
            "last_run_ts": "never",  # → None
            "totally_unknown_key": True,
        }
    )
    assert back.name == ""
    assert back.schedule.every_secs is None
    assert back.source == Scope()
    assert back.context is not None and back.context.tags == ("a",)
    assert back.context.window_secs == 0
    assert back.citation_policy == CITE_SOURCE_ONLY
    assert back.iteration_cap == MAX_ITERATION_CAP
    assert back.enabled is True
    assert back.last_run_ts is None


def test_finding_kind_is_the_shared_constant():
    assert FINDING_KIND == "research-finding"


# ── rule 1: a run says what it found ──


def test_a_run_that_found_nothing_new_is_recorded_as_such_with_its_sentence():
    now = time.time()
    saved = save_report(_defn(id="empty", created_ts=now - 10 * HOUR))
    record_run(saved.id, ok=True, nothing_new=True, result="Found nothing.", watermark_ts=now)
    after = get_report(saved.id)
    assert after is not None
    assert after.last_status == NOTHING_NEW, "a run that found nothing read as a plain ok"
    assert after.last_result == "Found nothing."
    assert after.last_run_ts is not None and after.watermark_ts == now


def test_a_run_that_wrote_a_finding_is_ok_with_its_sentence():
    now = time.time()
    saved = save_report(_defn(id="wrote", created_ts=now - 10 * HOUR))
    record_run(saved.id, ok=True, nothing_new=True, result="Found nothing.", watermark_ts=now)
    record_run(saved.id, ok=True, result="Wrote a finding.", watermark_ts=now)
    after = get_report(saved.id)
    assert after is not None
    assert (after.last_status, after.last_result) == ("ok", "Wrote a finding.")


def test_the_card_says_what_a_report_reads_in_the_terms_of_its_scope():
    assert sources_shown(_defn()) == (
        "Reads what is new in your knowledge each time it runs. It does not search the web."
    )
    assert sources_shown(_defn(source=Scope(tags=("perf", "ops", "ai")))) == (
        "Reads what is new in your knowledge tagged perf, ops or ai each time it runs. "
        "It does not search the web."
    )
    assert sources_shown(
        _defn(source=Scope(tags=("perf",), window_secs=86400), context=Scope(tags=()))
    ) == (
        "Reads your knowledge tagged perf from the last 24 hours each time it runs. "
        "While writing, it may also look at your knowledge. It does not search the web."
    )


def test_a_run_that_found_nothing_names_the_window_it_read():
    tagged = Scope(tags=("perf",))
    assert nothing_new_words(_defn(source=tagged)) == (
        "Found nothing in your knowledge tagged perf to report on yet."
    )
    assert nothing_new_words(_defn(source=tagged, watermark_ts=1.0)) == (
        "Found no new material in your knowledge tagged perf since its previous run."
    )
    assert nothing_new_words(_defn(source=Scope(window_secs=7 * 86400), watermark_ts=1.0)) == (
        "Found no material in your knowledge from the last 7 days."
    )
    assert wrote_words(_defn(), 1) == "Wrote a finding from 1 item in your knowledge."
    assert wrote_words(_defn(source=tagged), 3) == (
        "Wrote a finding from 3 items in your knowledge tagged perf."
    )


# ── rule 2: a failed run records its error WITHOUT advancing last_run_ts ──


def test_failed_run_records_error_and_leaves_last_run_ts_untouched():
    now = time.time()
    saved = _saved_with_last_run("fail", created_ts=now - 10 * HOUR, last_run_ts=now - 5 * HOUR)
    saved.watermark_ts = now - 5 * HOUR
    saved.last_result = "Wrote a finding from 2 items in your knowledge."
    save_report(saved)

    record_run(saved.id, ok=False, error="provider timed out", watermark_ts=now)
    after = get_report(saved.id)
    assert after is not None
    assert after.last_run_ts == now - 5 * HOUR  # untouched → the next run reads it all again
    assert after.last_status == "error"
    assert "provider timed out" in after.last_error
    assert after.watermark_ts == now - 5 * HOUR  # a failed run never advances it either
    # Still the sentence of the run `last_run_ts` dates, which did finish.
    assert after.last_result == "Wrote a finding from 2 items in your knowledge."


def test_successful_run_advances_last_run_ts_and_clears_the_error():
    now = time.time()
    saved = save_report(_defn(id="okrun", created_ts=now - 10 * HOUR))
    record_run(saved.id, ok=False, error="transient")
    record_run(saved.id, ok=True, watermark_ts=now)
    after = get_report(saved.id)
    assert after is not None
    assert after.last_status == "ok"
    assert after.last_error == ""
    assert after.last_run_ts is not None and after.last_run_ts >= now


def test_record_run_for_unknown_id_is_a_noop():
    # A report deleted while its run was in flight must not raise in the runner.
    record_run("does-not-exist", ok=True, watermark_ts=1.0)
    assert load_reports() == []


# ── rule 3: the watermark is scope-resolution time, supplied by the runner ──


def test_watermark_is_the_supplied_scope_resolution_time_not_completion():
    now = time.time()
    saved = save_report(_defn(id="wm", created_ts=now - 10 * HOUR))
    resolved_at = now - 300.0  # when the runner resolved the scope, five minutes back
    record_run(saved.id, ok=True, watermark_ts=resolved_at)
    after = get_report(saved.id)
    assert after is not None
    # Stamping completion instead would push the watermark past everything
    # captured mid-run, skipping those items forever.
    assert after.watermark_ts == resolved_at
    assert after.last_run_ts is not None
    assert after.last_run_ts > resolved_at


def test_watermark_is_left_alone_when_the_runner_supplies_none():
    now = time.time()
    saved = save_report(_defn(id="wmnone", created_ts=now - 10 * HOUR))
    saved.watermark_ts = now - 2 * HOUR
    save_report(saved)
    record_run(saved.id, ok=True)
    after = get_report(saved.id)
    assert after is not None
    assert after.watermark_ts == now - 2 * HOUR
