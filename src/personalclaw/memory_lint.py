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
    flags: list[dict] = field(default_factory=list)  # {check, key, detail}

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


def lint_memory(vs, *, now: datetime | None = None, judge=None, vault=None) -> LintReport:
    """Run the health checks over a vector store. Returns a :class:`LintReport`.

    ``now`` is injectable for testing. ``judge`` (optional, defaults to the
    store's ``contradiction_judge``) drives the contradiction scan; when absent
    that check is skipped (no LLM → no contradiction flags, fail-safe).

    ``vault`` (a :class:`~personalclaw.memory_vault.MemoryVault`) adds the readable-
    vault checks (§5.3). Passed in rather than resolved from config here on purpose:
    this function must never decide by itself to go reading a directory under the
    user's real home, and a caller that has no vault gets no vault flags rather than
    a config read with a surprising side effect.
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
                logger.debug(
                    "lint: contradiction judge failed for %s/%s", key_a, key_b, exc_info=True
                )

    # ── Flag: kept-both contradictions (MEMORY-GRAPH-AND-VAULT §4.1 — MGAV-5) ──
    # The Decide phase refuses to guess: a contradiction it is unsure about, or one where
    # holder precedence forbids the overwrite, is kept as TWO live rows and flagged here.
    # This check is NOT gated on `graph_enabled` (unlike `_lint_graph` below), because the
    # flag is a data-safety notice about semantic rows, not a graph-health statistic — a
    # contradiction the user can never see is the failure mode keeping both exists to
    # prevent, and hiding it behind an unrelated toggle would recreate it.
    _lint_conflicts(vs, report)

    # ── Flag: graph health (MEMORY-GRAPH-AND-VAULT §2.3) ──
    # All deterministic, all flag-only. An orphan usually means the entity set has a
    # gap, so "fixing" it by deleting the record would destroy the evidence.
    _lint_graph(vs, report)

    # ── Flag: readable-vault health (MEMORY-GRAPH-AND-VAULT §5.3) ──
    # Flag-only, like the graph checks. Every one of these describes a page the human
    # owns (an edit that could not be applied, a page they created, a link they broke),
    # and "fixing" any of them means overwriting their file — which is precisely what
    # the two-way design refuses to do.
    _lint_vault(vault, report)

    logger.info("memory lint: auto-fixed %s, %d flags", report.auto_fixed, len(report.flags))
    return report


def _lint_vault(vault, report: LintReport) -> None:
    """Add the vault checks. Silent no-op with no vault (mode ``off``, or a caller
    that never had one)."""
    if vault is None:
        return
    try:
        flags = vault.lint_flags()
    except Exception:  # noqa: BLE001 — lint must never fail on an optional check
        logger.debug("lint: vault checks unavailable", exc_info=True)
        return
    for check, key, detail in flags:
        report.add_flag(check, key, detail)


def _lint_conflicts(vs, report: LintReport) -> None:
    """Flag every standing kept-both contradiction. Never auto-resolves one.

    Flag-only on purpose: the whole point of keeping both rows is that nothing in the
    system knows which is true. An auto-fix here would be the silent pick that §4.1
    exists to refuse.
    """
    try:
        from personalclaw.memory_formation import conflicts
    except Exception:  # noqa: BLE001 — lint must never fail on an optional check
        logger.debug("lint: conflict check unavailable", exc_info=True)
        return
    for row in conflicts(vs):
        detail = f"kept alongside {row['old_key']} — undecided contradiction"
        if row.get("reason"):
            detail += f" ({row['reason']})"
        report.add_flag("keep_both", row["new_key"], detail)


def _lint_graph(vs, report: LintReport) -> None:
    """Add entity-graph checks. Silent no-op when the graph is off or unavailable."""
    if not getattr(vs, "graph_enabled", False):
        return
    try:
        graph = vs.graph
        summary = graph.summary()
    except Exception:  # noqa: BLE001 — lint must never fail on an optional check
        logger.debug("lint: graph checks unavailable", exc_info=True)
        return
    counts = summary
    # With no entities declared, EVERY record is trivially unlinked — reporting
    # that would bury the health tab in noise that says nothing actionable. The
    # proposal queue below is the useful signal in that state.
    if summary["entities"] == 0:
        for proposal in graph.proposals():
            report.add_flag(
                "proposed_entity",
                proposal["name"],
                f"mentioned in {proposal['mention_count']} records but not a known entity — "
                "accept it to start linking, or reject it",
            )
        return
    if counts["semantic_orphans"]:
        report.add_flag(
            "graph_orphans",
            "semantic",
            f"{counts['semantic_orphans']} semantic record(s) link to no entity",
        )
    if counts["episodic_orphans"]:
        report.add_flag(
            "graph_orphans",
            "episodic",
            f"{counts['episodic_orphans']} episodic record(s) link to no entity",
        )
    if counts["phantom_entities"]:
        report.add_flag(
            "phantom_entity",
            "entities",
            f"{counts['phantom_entities']} entit(y/ies) have no inbound links — "
            "candidates for merge or removal",
        )
    for proposal in graph.proposals():
        report.add_flag(
            "proposed_entity",
            proposal["name"],
            f"mentioned in {proposal['mention_count']} records but not a known entity — "
            "accept it to start linking, or reject it",
        )
