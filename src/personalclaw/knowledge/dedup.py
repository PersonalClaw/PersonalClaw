"""Cross-source semantic dedup — the pure resolver.

TIER-1 exact dedup (URL-normalize + byte-hash) already lives in ``store.py`` and stays. This
module is TIER-2: the FUZZY resolver that fires only when TIER-1 misses AND a vector exists —
filename/title near-match AND cosine ≥ threshold AND a **date-gate** (a differing
recurring-series date token ⇒ DISTINCT, so a daily/weekly report series never collapses into
one item). On a confirmed dup it names a **format-recall winner** (keep the richer copy,
archive the loser — reversible, never delete).

Everything here is PURE + unit-testable — no DB, no embedder, no I/O. The runner stage +
``KnowledgeStore.find_fuzzy_dup_candidates`` (steps 2+4) call into these functions with rows
they've already fetched; they layer on top without touching this file. Gated off until wired
(the create-time exact tiers are unaffected).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Confirmed-dup thresholds (conservative — false-merge is worse than a missed merge; the
# loser is only ARCHIVED, but a wrongly-merged report series is data loss).
_FUZZY_COSINE_MIN = 0.90  # semantic near-identity on the embedding
_FILENAME_SIM_MIN = 0.85  # title/filename-stem token overlap (Jaccard)

# Recurring-series date tokens in a title/filename — if two otherwise-identical items carry
# DIFFERENT date tokens they are DISTINCT (the date gate). Ordered widest-match first. Use a
# not-preceded-by-digit lookbehind (NOT \b) for the numeric leaders: filenames glue tokens
# with '_' (a regex word-char), so '_2026' has no \b before it — the lookbehind still matches.
_DATE_PATTERNS = [
    re.compile(r"(?<![0-9])(\d{4}-\d{2}-\d{2})(?![0-9])"),  # 2026-07-07
    re.compile(r"(?<![0-9])(\d{4}[-_/]?[qQ][1-4])(?![0-9])"),  # 2026-Q3 / 2026Q3
    re.compile(r"(?<![0-9])(\d{4}[-_/]\d{2})(?![0-9])"),  # 2026-07
    re.compile(r"(?<![0-9])(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})(?![0-9])"),  # 7/7/2026
    re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b", re.I),
    re.compile(r"\bweek\s*\d{1,2}\b", re.I),  # Week 27
]

_STOP_STEM = re.compile(r"[^a-z0-9]+")


@dataclass
class DupVerdict:
    """Result of a TIER-2 comparison. ``is_dup`` only when filename AND cosine AND date-gate
    all agree. ``winner``/``loser`` (item ids) set only when is_dup — the loser is archived."""

    is_dup: bool
    reason: str
    winner_id: str | None = None
    loser_id: str | None = None
    cosine: float = 0.0
    filename_sim: float = 0.0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 for a zero/empty vector or a
    length mismatch (defensive — embeddings from different models must never be compared)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def normalize_filename_stem(name: str) -> str:
    """Lowercase alnum-token stem of a filename/title — drops extension, punctuation, and
    the date token (so 'Q3 Report 2026-07.pdf' and 'q3-report 2026-08' share a stem). Used
    for the filename-similarity leg; the DATE token is compared separately by the gate."""
    if not name:
        return ""
    base = name.rsplit("/", 1)[-1]
    if "." in base:  # strip a trailing extension (not a mid-name dot)
        base = base.rsplit(".", 1)[0]
    base = base.lower()
    for pat in _DATE_PATTERNS:
        base = pat.sub(" ", base)
    toks = [t for t in _STOP_STEM.split(base) if t]
    return " ".join(toks)


def extract_series_date(text: str) -> str | None:
    """Return the first recurring-series date token in ``text`` (title+basename), or None.
    Two items with the SAME stem but DIFFERENT date tokens are a series → NOT a dup."""
    if not text:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0).lower()
    return None


def _stem_similarity(a: str, b: str) -> float:
    """Jaccard token overlap of two normalized stems (1.0 identical, 0.0 disjoint)."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def format_recall_winner(a: dict, b: dict) -> tuple[dict, dict]:
    """Given two confirmed-dup item rows, return (winner, loser) — keep the richer copy.
    Precedence: processing_status done > partial/other; then file > bookmark (item_type);
    then more CONTENT (content_len chars, falling back to word_count); then newer
    created_at. Deterministic + total (ties → a wins).

    ``content_len`` (raw character length of the item's current body) is the primary
    richness signal because it is reliable at dedup time and populated for EVERY type —
    unlike ``word_count``, which the ingest can leave at 0 for a type whose body is moved
    into the extracted-content pool, or which may not be recomputed until after the dedup
    stage runs. word_count is kept as a fallback for a caller that only has it."""

    def richness(it: dict) -> int:
        cl = it.get("content_len")
        if cl is not None:
            try:
                return int(cl or 0)
            except (TypeError, ValueError):
                pass
        return int(it.get("word_count", 0) or 0)

    def rank(it: dict) -> tuple:
        status_done = 1 if str(it.get("processing_status", "")).lower() == "done" else 0
        is_file = (
            1 if str(it.get("item_type", "")).lower() not in ("bookmark", "url", "link") else 0
        )
        created = str(it.get("created_at", "") or "")
        return (status_done, is_file, richness(it), created)

    return (a, b) if rank(a) >= rank(b) else (b, a)


def resolve_duplicate(
    candidate: dict,
    existing: dict,
    *,
    cosine_min: float = _FUZZY_COSINE_MIN,
    filename_sim_min: float = _FILENAME_SIM_MIN,
) -> DupVerdict:
    """Decide whether ``candidate`` duplicates ``existing`` (both item rows carrying at least
    ``title``/``file_path`` + ``embedding`` as a float list). A dup requires ALL of:
    filename/title stem similarity ≥ min, cosine ≥ min, AND the same series-date token
    (differing tokens ⇒ DISTINCT series). On a dup, names the format-recall winner/loser."""
    name_c = candidate.get("title") or candidate.get("file_path") or ""
    name_e = existing.get("title") or existing.get("file_path") or ""
    stem_c, stem_e = normalize_filename_stem(name_c), normalize_filename_stem(name_e)
    fsim = _stem_similarity(stem_c, stem_e)
    cos = cosine_similarity(candidate.get("embedding") or [], existing.get("embedding") or [])

    if fsim < filename_sim_min:
        return DupVerdict(False, "filename too different", cosine=cos, filename_sim=fsim)
    if cos < cosine_min:
        return DupVerdict(False, "cosine below threshold", cosine=cos, filename_sim=fsim)
    # Date gate: a differing recurring-series token ⇒ a series, NOT a dup.
    date_c = extract_series_date(name_c) or extract_series_date(candidate.get("summary", ""))
    date_e = extract_series_date(name_e) or extract_series_date(existing.get("summary", ""))
    if date_c and date_e and date_c != date_e:
        return DupVerdict(False, "series date differs — distinct", cosine=cos, filename_sim=fsim)

    winner, loser = format_recall_winner(candidate, existing)
    return DupVerdict(
        True,
        "fuzzy dup (filename+cosine+date-gate)",
        winner_id=winner.get("id"),
        loser_id=loser.get("id"),
        cosine=cos,
        filename_sim=fsim,
    )
