"""Tests for the P12 TIER-2 fuzzy dedup resolver (knowledge/dedup.py) — pure, no DB.

Headline guard: the report-series date gate (same title, near-identical cosine, DIFFERENT
date tokens ⇒ DISTINCT, never collapse a recurring series). Plus filename/cosine gates,
format-recall winner precedence, and the pure stem/date helpers."""

from __future__ import annotations

from personalclaw.knowledge.dedup import (
    cosine_similarity,
    extract_series_date,
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
