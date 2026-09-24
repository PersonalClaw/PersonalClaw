"""The ad-hoc→template gate's CALL SITE: decide, record the refusal, file a draft.

``learning.detectors`` holds the decision (a deterministic chain plus a structural score) and is
documented as pure — "nothing here calls a model, writes memory, or files a proposal". That purity
is worth keeping, and it is also exactly why the chain shipped inert: ``detectors.gate()`` had
**zero production callers**, so no real run was ever evaluated, no refusal was ever recorded, and
the ``Skip`` enum's whole premise — that "a count per reason says which gate is doing the work and
which is dead weight" — had no data behind it. A gate nothing calls cannot decline anything.

This module is the missing half, and only that half:

* **decide** by delegating to ``detectors.gate`` (no second copy of the thresholds);
* **record** every negative decision as a ``FLUSH_SKIPPED`` row carrying the TYPED ``Skip`` value,
  reusing the same ledger ``gate.record_denial`` writes to rather than minting a parallel one — two
  skip ledgers would make "why did nothing get captured this week" a two-query question with two
  possible answers;
* **file** an accepted candidate as a PENDING ``template`` proposal.

Filing is never installing. ``proposals.enqueue`` returns a row a human accepts or rejects, so even
the ``AUTO_FILE`` branch — which deliberately spends zero model calls — cannot put a template into
the library on its own. That is what makes a free auto-file safe: the cost it skips is the model's,
not the reviewer's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from personalclaw.learning import detectors
from personalclaw.learning.detectors import Action, Candidate, GateDecision

logger = logging.getLogger(__name__)

#: Prefix on every ledger ``detail`` this module writes. The flush ledger is shared with the capture
#: gate's denials, so the reason alone would be ambiguous — ``budget_burn`` and ``not_worthwhile``
#: both mean "declined" but belong to different gates with different fixes.
LEDGER_PREFIX = "template_gate"


@dataclass
class GateOutcome:
    """What the call site did — the decision, whether it was recorded, and what it filed.

    The three are reported separately on purpose. A decision that was reached but not recorded is
    the defect this module exists to fix, so collapsing them into "did it work" would hide the
    regression it is meant to prevent.
    """

    decision: GateDecision
    recorded: bool = False
    proposal_id: str = ""

    @property
    def filed(self) -> bool:
        return bool(self.proposal_id)

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = dict(self.decision.to_dict())
        out["recorded"] = self.recorded
        out["proposal_id"] = self.proposal_id
        return out


def record_skip(decision: GateDecision, *, detail: str = "") -> bool:
    """Persist one negative gate decision, keyed by its TYPED skip reason.

    Returns True iff a row was written. A non-skip decision writes nothing and returns False — the
    ledger's ``FLUSH_SKIPPED`` outcome means "declined", and recording an accept under it would
    corrupt the very counts the reasons exist to produce.

    Best-effort, like ``gate.record_denial``: recording is observability, and a staging-store
    failure must not lose a template proposal that the chain already approved.
    """
    reason = decision.skip_reason
    if decision.action != Action.SKIP.value or not reason:
        return False
    try:
        from personalclaw.learning.gate import Cadence
        from personalclaw.learning.staging import FlushOutcome, get_store

        get_store().record_flush(
            cadence=Cadence.PER_TURN.value,
            outcome=FlushOutcome.FLUSH_SKIPPED,
            detail=f"{LEDGER_PREFIX}: {reason}{': ' + detail if detail else ''}",
        )
        return True
    except Exception:
        logger.debug("template gate: recording skip %s failed", reason, exc_info=True)
        return False


def skip_counts(*, days: int = 30) -> dict[str, int]:
    """Counts per typed skip reason over a window — the "negative space" §3.2 tunes against.

    Reads the same ``flush_records`` rows :func:`record_skip` writes and keys them by the ``Skip``
    value parsed back out of ``detail``. Only reasons this module actually wrote are counted, so a
    capture-gate denial sharing the ledger cannot inflate a template-gate reason.

    Returns ``{}`` rather than raising when the store is unavailable: a statistics read is never
    worth failing a caller over.
    """
    try:
        from personalclaw.learning.staging import FlushOutcome, get_store

        store = get_store()
        import time

        since = time.time() - max(1, days) * 86400
        with store._cursor() as cur:  # noqa: SLF001 — same-package read of the store's connection
            rows = cur.execute(
                "SELECT detail FROM flush_records WHERE outcome = ? AND created_ts >= ?;",
                (FlushOutcome.FLUSH_SKIPPED.value, since),
            ).fetchall()
    except Exception:
        logger.debug("template gate: skip_counts unavailable", exc_info=True)
        return {}

    known = {s.value for s in detectors.Skip}
    counts: dict[str, int] = {}
    for row in rows:
        detail = str(row[0] or "")
        if not detail.startswith(LEDGER_PREFIX + ":"):
            continue
        reason = detail[len(LEDGER_PREFIX) + 1 :].strip().split(":", 1)[0].strip()
        if reason in known:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _file_template(
    candidate: Candidate,
    decision: GateDecision,
    *,
    session_key: str,
    title: str,
    body: str,
) -> str:
    """File the accepted candidate as a PENDING template proposal. Returns its id or ``""``."""
    from personalclaw.learning.proposals import Kind, enqueue

    try:
        _verdict, prop = enqueue(
            kind=Kind.TEMPLATE.value,
            title=title,
            body=body,
            provenance="inferred",
            session_key=session_key,
            run_id=candidate.run_id,
            source_excerpt=candidate.text[:2000],
            confidence=round(decision.score.total, 4),
            tags=["ad_hoc_to_template", decision.action],
        )
    except Exception:
        logger.debug("template gate: enqueue failed for %s", candidate.run_id, exc_info=True)
        return ""
    # A SKIP verdict means decision memory already forbids this content. Not an error: the human
    # said no once, and re-filing it would be nagging.
    return prop.id if prop is not None else ""


def evaluate(
    candidate: Candidate,
    *,
    session_key: str = "",
    title: str = "",
    body: str = "",
    file_proposal: bool = True,
) -> GateOutcome:
    """Run the chain on a real candidate, record any refusal, and file an accepted one.

    ``CONSULT`` files nothing here. §3.2 pays for a model only in the ambiguous middle band, and
    this call site has no model to pay with — reporting the band honestly is better than promoting
    the candidate on a score the design said was inconclusive, and better than recording it as a
    skip it was not.
    """
    decision = detectors.gate(candidate)
    outcome = GateOutcome(decision=decision)
    outcome.recorded = record_skip(decision, detail=candidate.run_id)
    if decision.action == Action.AUTO_FILE.value and file_proposal:
        outcome.proposal_id = _file_template(
            candidate,
            decision,
            session_key=session_key,
            title=title or f"Template from run {candidate.run_id or 'ad-hoc'}",
            body=body or candidate.text,
        )
    return outcome
