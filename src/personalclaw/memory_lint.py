"""Periodic memory-health lint — scan semantic + episodic memory, auto-fix the
safe issues, flag the rest as recommendations.

A low-frequency complement to the at-write hygiene (the contradiction judge,
supersession, dedup): over time, orphaned episodic rows, never-recalled stale
facts, near-duplicates the dedup missed, and latent contradictions accumulate.
This sweep surfaces them.

Auto-fixed (safe, reversible via the WAL): nothing destructive without a clear
signal — only **purge already-superseded rows past a long retention** (they're
soft-deleted with a pointer; keeping them forever is the only "fix" and it's
bounded). Everything judgmental — stale facts, near-dups, contradictions — is
**flagged**, not auto-changed, so the user (or a future policy) decides.

Returns a structured :class:`LintReport`; the caller renders or logs it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# A semantic fact not recalled in this long is flagged stale (not deleted).
_STALE_DAYS = 90
# Superseded rows older than this are purged (the only auto-fix; bounded cleanup).
_SUPERSEDED_RETENTION_DAYS = 90
# Keyword-overlap ratio above which two non-lesson facts are flagged near-dup.
_NEAR_DUP_RATIO = 0.7


@dataclass
class LintReport:
    """Outcome of a memory-health sweep."""

    auto_fixed: dict[str, int] = field(default_factory=dict)  # check → count fixed
    flags: list[dict] = field(default_factory=list)           # {check, key, detail}

    def add_flag(self, check: str, key: str, detail: str) -> None:
        self.flags.append({"check": check, "key": key, "detail": detail})

    def to_dict(self) -> dict:
        return {"auto_fixed": self.auto_fixed, "flags": self.flags, "flag_count": len(self.flags)}


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _keywords(text: str) -> set[str]:
    import re
    stop = {"the", "a", "an", "to", "in", "for", "and", "or", "not", "is", "of", "on"}
    return {w for w in re.split(r"\W+", text.lower()) if len(w) > 2 and w not in stop}


def lint_memory(vs, *, now: datetime | None = None, judge=None) -> LintReport:
    """Run the health checks over a vector store. Returns a :class:`LintReport`.

    ``now`` is injectable for testing. ``judge`` (optional, defaults to the
    store's ``contradiction_judge``) drives the contradiction scan; when absent
    that check is skipped (no LLM → no contradiction flags, fail-safe).
    """
    now = now or datetime.now(tz=timezone.utc)
    judge = judge if judge is not None else getattr(vs, "contradiction_judge", None)
    report = LintReport()

    # ── Auto-fix: purge long-superseded rows (bounded cleanup) ──
    purged = 0
    cutoff = (now - timedelta(days=_SUPERSEDED_RETENTION_DAYS)).isoformat()
    try:
        rows = vs.db.execute(
            "SELECT key FROM semantic_memory WHERE superseded_by IS NOT NULL "
            "AND invalidated_at IS NOT NULL AND invalidated_at < ?",
            (cutoff,),
        ).fetchall()
        for r in rows:
            vs.db.execute("DELETE FROM semantic_memory WHERE key = ?", (r["key"],))
            purged += 1
        if purged:
            vs.db.commit()
    except Exception:
        logger.debug("lint: superseded-purge failed", exc_info=True)
    report.auto_fixed["superseded_purged"] = purged

    # Active (non-deleted, non-lesson) facts drive the remaining checks.
    facts = [
        dict(r)
        for r in vs.db.execute(
            "SELECT key, value_json, recall_count, updated_at FROM semantic_memory "
            "WHERE is_deleted = 0 AND key NOT LIKE 'lesson.%'"
        ).fetchall()
    ]

    # ── Flag: stale (never recalled + old) ──
    for f in facts:
        if (f.get("recall_count") or 0) == 0:
            upd = _parse_iso(f.get("updated_at"))
            if upd and (now - upd).days >= _STALE_DAYS:
                report.add_flag("stale", f["key"], f"not recalled in {(now - upd).days}d")

    # ── Flag: sparse value ──
    for f in facts:
        try:
            val = json.loads(f["value_json"])
        except (json.JSONDecodeError, TypeError):
            val = f["value_json"]
        if isinstance(val, str) and len(val.strip()) < 2:
            report.add_flag("sparse", f["key"], "near-empty value")

    # ── Flag: near-duplicate pairs (keyword overlap the dedup missed) ──
    seen_pairs: set[tuple[str, str]] = set()
    for i, a in enumerate(facts):
        a_words = _keywords(str(a["value_json"]))
        if not a_words:
            continue
        for b in facts[i + 1 :]:
            b_words = _keywords(str(b["value_json"]))
            if not b_words:
                continue
            ratio = len(a_words & b_words) / min(len(a_words), len(b_words))
            if ratio >= _NEAR_DUP_RATIO:
                pair = tuple(sorted((a["key"], b["key"])))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    report.add_flag("near_dup", a["key"], f"~{ratio:.0%} overlap with {b['key']}")

    # ── Flag: contradictions (sweep-time LLM judge, complement to at-write) ──
    if judge is not None:
        for key_a, key_b in seen_pairs:  # same-topic neighbors are the cheap candidate set
            va = next((str(f["value_json"]) for f in facts if f["key"] == key_a), "")
            vb = next((str(f["value_json"]) for f in facts if f["key"] == key_b), "")
            if not va or not vb:
                continue
            try:
                if judge(va, vb):
                    report.add_flag("contradiction", key_a, f"contradicts {key_b}")
            except Exception:
                logger.debug("lint: contradiction judge failed for %s/%s", key_a, key_b, exc_info=True)

    logger.info(
        "memory lint: auto-fixed %s, %d flags", report.auto_fixed, len(report.flags)
    )
    return report
