"""Tests for the P12 TIER-2 fuzzy dedup resolver (knowledge/dedup.py) — pure, no DB.

Headline guard: the report-series date gate (same title, near-identical cosine, DIFFERENT
date tokens ⇒ DISTINCT, never collapse a recurring series). Plus filename/cosine gates,
format-recall winner precedence, and the pure stem/date helpers."""

from __future__ import annotations

import math

import pytest

from personalclaw.knowledge.dedup import (
    FILENAME_SIM_MIN,
    FUZZY_COSINE_MIN,
    cosine_similarity,
    extract_series_date,
    filename_similarity,
    format_recall_winner,
    normalize_filename_stem,
    resolve_duplicate,
)


def _item(id, title, emb, **kw):
    return {"id": id, "title": title, "embedding": emb, **kw}


def test_cosine_basics():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0  # empty
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0  # length mismatch → defensive 0


def test_normalize_filename_stem_drops_ext_date_punct():
    assert normalize_filename_stem("Q3 Report 2026-07.pdf") == "q3 report"
    assert normalize_filename_stem("weekly-standup_2026-Q3") == "weekly standup"
    # same series, different month → SAME stem (date stripped)
    assert normalize_filename_stem("Q3 Report 2026-07") == normalize_filename_stem(
        "q3-report 2026-08"
    )


def test_extract_series_date():
    assert extract_series_date("Standup 2026-07-07 notes") == "2026-07-07"
    assert extract_series_date("Report 2026-Q3") == "2026-q3"
    assert extract_series_date("no date here") is None


def test_report_series_NOT_collapsed(TMP=None):
    # THE HEADLINE RISK: identical title + identical embedding, DIFFERENT dates → DISTINCT.
    emb = [0.1, 0.2, 0.3, 0.4]
    a = _item("a", "Daily Standup 2026-07-06", emb)
    b = _item("b", "Daily Standup 2026-07-07", emb)  # same vector, next day
    v = resolve_duplicate(a, b)
    assert v.is_dup is False
    assert "series date differs" in v.reason


def test_true_fuzzy_dup_detected():
    emb = [0.5, 0.5, 0.5, 0.5]
    a = _item("a", "Architecture Overview", emb, word_count=200, processing_status="partial")
    b = _item("b", "Architecture Overview.pdf", emb, word_count=900, processing_status="done")
    v = resolve_duplicate(a, b)
    assert v.is_dup is True
    assert v.winner_id == "b" and v.loser_id == "a"  # richer (done, more words) wins


def test_filename_gate_blocks_unrelated():
    emb = [1.0, 0.0]
    a = _item("a", "Totally Different Topic", emb)
    b = _item("b", "Architecture Overview", emb)  # same vector but disjoint titles
    v = resolve_duplicate(a, b)
    assert v.is_dup is False and "filename" in v.reason


def test_cosine_gate_blocks_semantic_mismatch():
    a = _item("a", "Architecture Overview", [1.0, 0.0])
    b = _item("b", "Architecture Overview", [0.0, 1.0])  # same title, orthogonal vectors
    v = resolve_duplicate(a, b)
    assert v.is_dup is False and "cosine" in v.reason


# ── The two thresholds, AT their boundary ───────────────────────────────
#
# Every gate test above sits far from the line: 1.0 vs 0.0 cosine, disjoint vs identical titles.
# So both comparisons are `<` and NOTHING pinned that — flipping either to `<=`, or nudging a
# constant, changes which pairs a merge UI proposes destroying and no test would have gone red.
# These fix the numbers AND the inclusive/exclusive direction, in both directions, from either side.


def test_the_thresholds_are_the_documented_numbers():
    """A silent nudge to either constant re-scopes a destructive UI. Pin the values themselves."""
    assert (FILENAME_SIM_MIN, FUZZY_COSINE_MIN) == (0.85, 0.90)


def _vec_at_cosine(target: float) -> list[float]:
    """A unit vector whose cosine against [1, 0] is exactly ``target``."""
    return [target, math.sqrt(max(0.0, 1.0 - target * target))]


@pytest.mark.parametrize(
    "cos,is_dup",
    [
        (FUZZY_COSINE_MIN, True),  # EXACTLY at the floor — `<` admits it
        (FUZZY_COSINE_MIN - 0.001, False),  # a hair below — refused
        (FUZZY_COSINE_MIN + 0.001, True),
    ],
)
def test_the_cosine_floor_is_inclusive_at_exactly_the_threshold(cos, is_dup):
    a = _item("a", "Architecture Overview", [1.0, 0.0])
    b = _item("b", "Architecture Overview", _vec_at_cosine(cos))
    v = resolve_duplicate(a, b)
    # Vacuity floor: the construction has to actually land on the boundary, or this parametrize
    # is three tests of the same interior point.
    assert v.cosine == pytest.approx(cos, abs=1e-9), "the fixture missed the boundary it targets"
    assert v.filename_sim == 1.0, "the OTHER leg must be out of the way for this to test cosine"
    assert v.is_dup is is_dup


@pytest.mark.parametrize(
    # The candidate's tokens are a strict PREFIX of the anchor's 20, so the union stays 20 and the
    # Jaccard is `shared / 20` exactly. (First draft added distinct tokens on both sides, which
    # makes the union 2n − shared — 17 of 20 each way is 17/23 = 0.739, not 0.85. The vacuity
    # assertion below is what caught that, which is the reason it is here.)
    "shared,expected_sim,is_dup",
    [
        (17, 0.85, True),  # EXACTLY the floor ⇒ admitted
        (16, 0.80, False),  # below ⇒ refused
        (18, 0.90, True),
    ],
)
def test_the_filename_floor_is_inclusive_at_exactly_the_threshold(shared, expected_sim, is_dup):
    anchor = " ".join(f"t{i}" for i in range(20))
    title = " ".join(f"t{i}" for i in range(shared))
    emb = [1.0, 0.0]  # identical vectors: only the filename leg can decide
    v = resolve_duplicate(_item("a", title, emb), _item("b", anchor, emb))
    assert v.filename_sim == pytest.approx(
        expected_sim, abs=1e-9
    ), "the fixture missed the boundary it targets"
    assert v.cosine == 1.0, "the OTHER leg must be out of the way for this to test the filename"
    assert v.is_dup is is_dup


def test_filename_similarity_is_the_same_metric_the_resolver_gates_on():
    """The store's on-demand surfacing path prefilters a whole library with this function and
    then hands survivors to the resolver. If the two ever computed different numbers, the
    prefilter would drop pairs the resolver would have confirmed — silently, as an empty list."""
    a, b = "Q3 Report 2026-07.pdf", "q3-report 2026-08"
    assert filename_similarity(a, b) == 1.0  # date stripped by both legs
    emb = [1.0, 0.0]
    assert resolve_duplicate(_item("a", a, emb), _item("b", b, emb)).filename_sim == 1.0
    # And it disagrees where the resolver disagrees.
    assert (
        filename_similarity("Totally Different Topic", "Architecture Overview") < FILENAME_SIM_MIN
    )
    assert (
        resolve_duplicate(
            _item("a", "Totally Different Topic", emb), _item("b", "Architecture Overview", emb)
        ).filename_sim
        < FILENAME_SIM_MIN
    )


def test_series_date_gate_via_SUMMARY_not_just_title():
    # The date gate reads title OR summary (a report's date often lives in the body, not the
    # filename). Identical titles + near-1.0 cosine, but DIFFERENT dates in the SUMMARIES ⇒
    # DISTINCT series. Pins the extract_series_date(summary) fallback branch (was uncovered).
    emb = [0.2, 0.2, 0.2, 0.2]
    a = _item("a", "Standup Notes", emb, summary="notes from 2026-07-06 sync")
    b = _item("b", "Standup Notes", emb, summary="notes from 2026-07-07 sync")
    v = resolve_duplicate(a, b)
    assert v.is_dup is False
    assert "series date differs" in v.reason


def test_same_date_token_still_dups():
    # Two near-identical items carrying the SAME date token are NOT a series split — the gate
    # must fire ONLY on DIFFERING tokens (e.g. a re-download of the same day's report → dup).
    emb = [0.5, 0.5, 0.5, 0.5]
    a = _item("a", "Daily Standup 2026-07-07", emb, word_count=100, processing_status="partial")
    b = _item("b", "Daily Standup 2026-07-07.pdf", emb, word_count=800, processing_status="done")
    v = resolve_duplicate(a, b)
    assert v.is_dup is True
    assert v.winner_id == "b"  # richer copy wins; same-date does not block the merge


def test_asymmetric_date_does_not_gate():
    # Only ONE item carries a date token (the other has none anywhere). The gate needs TWO
    # present, differing tokens — an absent token is not a series signal — so these remain
    # dup-eligible. Pins the deliberate `date_c and date_e` design against a future "tightening"
    # that would gate on asymmetry and silently break legitimate dedup.
    emb = [0.4, 0.4, 0.4, 0.4]
    a = _item("a", "Architecture Overview 2026-07", emb, word_count=200, processing_status="done")
    b = _item("b", "Architecture Overview", emb, word_count=200, processing_status="done")
    v = resolve_duplicate(a, b)
    assert v.is_dup is True
    assert "fuzzy dup" in v.reason


def test_format_recall_winner_precedence():
    done_file = {
        "id": "f",
        "processing_status": "done",
        "item_type": "file",
        "word_count": 100,
        "created_at": "2026-01-01",
    }
    partial_bm = {
        "id": "b",
        "processing_status": "partial",
        "item_type": "bookmark",
        "word_count": 999,
        "created_at": "2026-09-01",
    }
    w, loser = format_recall_winner(done_file, partial_bm)
    assert w["id"] == "f"  # done>partial + file>bookmark beats higher word_count
    # tie on status+type → higher word_count wins
    a = {
        "id": "a",
        "processing_status": "done",
        "item_type": "file",
        "word_count": 50,
        "created_at": "2026-01-01",
    }
    b = {
        "id": "b",
        "processing_status": "done",
        "item_type": "file",
        "word_count": 500,
        "created_at": "2026-01-01",
    }
    w2, _ = format_recall_winner(a, b)
    assert w2["id"] == "b"


def test_format_recall_prefers_content_len_over_stale_word_count():
    """Regression (found live in Plan-2 P12 sanity): content_len is the primary richness
    signal, so the copy with more actual body wins even when its word_count column is stale
    (0 / not-yet-recomputed at dedup time). Before the fix, the thin copy with an equal/
    higher word_count was kept and the richer one archived."""
    # Both same status+type; the RICHER item has more content_len but a STALE word_count=0
    # (the exact ingest-ordering situation that inverted the live pick).
    thin = {
        "id": "thin",
        "processing_status": "done",
        "item_type": "note",
        "content_len": 102,
        "word_count": 16,
        "created_at": "2026-07-07T15:51:00",
    }
    rich = {
        "id": "rich",
        "processing_status": "done",
        "item_type": "note",
        "content_len": 296,
        "word_count": 0,
        "created_at": "2026-07-07T15:52:00",
    }
    w, loser = format_recall_winner(thin, rich)
    assert w["id"] == "rich" and loser["id"] == "thin"  # more content wins despite wc=0


def test_format_recall_falls_back_to_word_count_without_content_len():
    """A caller that only supplies word_count (no content_len) still ranks by it —
    the fallback keeps older callers/tests working."""
    a = {"id": "a", "processing_status": "done", "item_type": "file", "word_count": 50}
    b = {"id": "b", "processing_status": "done", "item_type": "file", "word_count": 500}
    w, _ = format_recall_winner(a, b)
    assert w["id"] == "b"
