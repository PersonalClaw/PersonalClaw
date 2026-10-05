"""The learning curator's tick: one bounded pass over the learned library.

It runs on the memory consolidation's cadence (``HistoryConsolidator._consolidate_locked``
calls it), and it reports and files proposals only: it grades decisions whose horizon has
elapsed, sweeps work units nobody reads, grades accepted changes, prunes surfacing events past
retention, ages the library and files promotion suggestions. Nothing here rewrites a skill or
retires a work unit itself.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_curator_tick(svc: Any) -> str:
    """One bounded curator tick over the learned library. Returns a summary or "".

    Builds candidates from the usage store rather than from the skills loader: the
    curator judges anything with recorded usage, and coupling it to one entity
    type is what made the previous version un-generalizable.

    Aging DECIDES; the entity's owner applies. So this reports and files review
    proposals, and does not itself rewrite skill frontmatter — that write belongs
    to the skills loader, which is the only thing that knows the file format.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.learning import curator as curator_mod
    from personalclaw.learning.usage import UsageStore

    cfg = AppConfig.load().learning
    if not getattr(cfg, "enabled", True) or not getattr(cfg, "curator_enabled", True):
        return ""

    # Grade any decision whose horizon has elapsed BEFORE the aging pass, so a
    # freshly-measured outcome is in the library the same tick it resolves. Inert unless a
    # vector store is wired (``svc`` degrades to null), and best-effort — a resolver failure
    # never blocks curation.
    outcomes_note = ""
    try:
        from personalclaw.learning import outcome_resolver

        rep = outcome_resolver.resolve(svc)
        if rep.get("resolved") or rep.get("unscored") or rep.get("inconclusive"):
            outcomes_note = (
                f"outcomes resolved={rep['resolved']} unscored={rep['unscored']} "
                f"inconclusive={rep['inconclusive']}"
            )
    except Exception:
        logger.debug("Outcome resolver failed", exc_info=True)

    # With the publish bets just graded, ask which work units nobody reads. Runs AFTER
    # the resolver on purpose — the sweep reads resolutions, so grading first means a cycle that
    # matured this tick is in the window rather than a tick late. Reports and PROPOSES only:
    # nothing here can pause or retire a work unit, because "nobody looked yet" and "nobody will
    # ever look" are different facts and only the user knows which.
    liveness_note = ""
    try:
        from personalclaw.learning import consumer_liveness

        rep = consumer_liveness.sweep()
        if rep.get("dormant") or rep.get("proposed"):
            liveness_note = f"consumer liveness dormant={rep['dormant']} proposed={rep['proposed']}"
    except Exception:
        logger.debug("Consumer-liveness sweep failed", exc_info=True)

    # Grade every accepted change whose post-acceptance horizon has
    # elapsed. Reads the Run Ledger (not semantic memory) so it runs on every box regardless of
    # embedder; inert-by-data when nothing has been accepted, gated on `learning.attribution_*`
    # internally, best-effort — a grading failure never blocks curation. A HARMFUL verdict files
    # a revert PROPOSAL through the shared queue; nothing is ever applied here.
    attribution_note = ""
    try:
        from personalclaw.learning import attribution

        rep = attribution.grade_accepted_changes()
        if rep.get("graded") or rep.get("reverts"):
            attribution_note = f"attribution graded={rep['graded']} reverts={rep['reverts']}"
    except Exception:
        logger.debug("Attribution grading failed", exc_info=True)

    # Surfacing events prune at 90d on the curator tick. Here rather than on its own
    # timer because the surfacing log is exactly the kind of high-volume, low-value,
    # independently-prunable data the curator tick already exists to age — a second cadence
    # would be a daemon to own for one DELETE.
    try:
        from personalclaw.learning.surfacing_events import SurfacingEventStore

        _events = SurfacingEventStore()
        try:
            _pruned = _events.prune()
        finally:
            _events.close()
        if _pruned:
            logger.debug("pruned %d surfacing events past retention", _pruned)
    except Exception:
        logger.debug("Surfacing-event prune failed", exc_info=True)

    store = UsageStore()
    try:
        records = [rec for kind in ("skill", "template") for rec in store.list_kind(kind)]
        candidates = [
            curator_mod.Candidate(
                kind=rec.kind,
                entity=rec.entity,
                last_used_at=rec.last_used_at,
                created_at=rec.first_seen_at,
                stability=min(1.0, rec.used / 10.0),
                pinned=rec.pinned,
                source_type=rec.source_type,
            )
            for rec in records
        ]
        if not candidates:
            return "; ".join(p for p in (outcomes_note, liveness_note, attribution_note) if p)
        active_dates = store.active_days()
        report = curator_mod.run_aging(candidates, active_dates=active_dates, mode="")
        curator_mod.file_review_proposals(report)
        # Heat-earned promotion. The multi-gate (`usage.promotion_ready`) had no
        # caller anywhere in the tree — a gate nothing runs is a gate that never refuses
        # anything, and the bare "surfaced ≥2×" it replaced was still what the ladder
        # effectively used. This is its live cadence: the same verified tick as the aging
        # pass, filing SUGGESTIONS into the shared queue and promoting nothing itself.
        promo_note = ""
        suggestions = curator_mod.promotion_suggestions(records, active_dates=active_dates)
        filed = curator_mod.file_promotion_suggestions(suggestions)
        if filed:
            promo_note = f"promotion suggestions filed={filed}"
        summary = report.summary() if (report.changed or report.review_proposals) else ""
        return "; ".join(
            p for p in (summary, promo_note, outcomes_note, liveness_note, attribution_note) if p
        )
    finally:
        store.close()
