"""The ledger side of triage — a skip that leaves a row behind.

"A test-only commit yields a ledger-only skip with a one-line rationale" is an assertion about
what gets *written*, not about what is absent. A companion that ran and correctly skipped, and
a companion that never fired at all, both produce zero Inbox items and zero Tasks; the only
thing that separates them is this record. So the skip path writes first and returns second, and
:func:`record_triage` refuses to write a record with no rationale rather than emitting a row
that answers "why did nothing run?" with an empty string.

The record goes through the platform ledger primitive (:mod:`personalclaw.ledger`, PP-4) using
existing kinds — `step_skipped` for a skip, `decision` for an impactful commit that proceeds. No
new ledger kind is minted for this: a consumer that already folds `step_skipped` gets the
companion's skips for free, and one that does not would not learn a bespoke kind either.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from personalclaw.ledger import DECISION, STEP_SKIPPED
from personalclaw.selfqa.triage import CommitTriage

logger = logging.getLogger(__name__)

#: The node id these records are attributed to, matching the `self-qa` template's triage node.
TRIAGE_NODE = "triage"


class _Ledger(Protocol):
    """The one method needed. Typed structurally so any `LedgerWriter` subclass fits."""

    def write(self, kind: str, **fields: Any) -> dict[str, Any]: ...


def record_triage(ledger: _Ledger, triage: CommitTriage, *, epoch: int = 0) -> dict[str, Any]:
    """Write one ledger record for `triage` and return it.

    A skipped commit writes `step_skipped`; an impactful one writes `decision` (it is a decision
    to spend a scenario, and the run's later step events cover the work itself). Both carry
    `sha`, `impact` and `rationale`, so the run inbox can render *why* without a second lookup.

    Raises `ValueError` on an empty rationale. That is the whole point of the record: a row
    saying "skipped" with no reason is the silence this function exists to prevent, and letting
    it through would make the surface look healthy while being useless.
    """
    rationale = (triage.rationale or "").strip()
    if not rationale:
        raise ValueError(f"selfqa: refusing to record a rationale-less verdict for {triage.sha}")

    kind = STEP_SKIPPED if triage.skipped else DECISION
    record = ledger.write(
        kind,
        node_id=TRIAGE_NODE,
        instance_path=TRIAGE_NODE,
        epoch=epoch,
        actor="selfqa",
        sha=triage.sha,
        impact=triage.impact,
        rationale=rationale,
        subject=triage.subject,
    )
    logger.debug("selfqa triage recorded: %s %s (%s)", triage.sha[:8], triage.impact, rationale)
    return record
