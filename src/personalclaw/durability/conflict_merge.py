"""The propose-only conflict merge pass (DURABILITY-AND-SYNC §4.2 item 2 + §7, DAS-7).

A background LLM pass over the review queue: for each unresolved conflict it drafts a
merged version plus a rationale and writes them ONTO THE RECORD as a proposal. It never
touches the live store — the soul rule is *propose, don't write* (§7: no auto-applied LLM
merges), so the local version stays authoritative until a human accepts on the review
surface (DAS-10).

Two invariants, both tested:

* **Never auto-apply.** This module imports nothing that can write an entry's store — no
  ``writeback``, no ``reconcile``, no ``apply_rows``. A source-level rail in
  ``tests/test_durability_conflicts.py`` asserts that the dangerous direction is
  unreachable rather than merely unused, so a later edit can't quietly wire it up.
* **Fail-open on the model.** No model configured, an open circuit breaker, a timeout, a
  budget refusal, a schema miss, an unparseable answer — every one of them leaves the
  conflict record exactly where it was: ``needs-review``, with no proposal and a recorded
  ``proposal_error``. A missing model must never lose a conflict and must never silently
  resolve one. Nothing here raises; the report carries the failure count.

``use_case="background"`` is deliberate — the reasoning axis, never the chat/native-runtime
axis (a one-shot merge draft wants a plain model provider, not an agent runtime).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from personalclaw.durability.conflicts import (
    STATUS_NEEDS_REVIEW,
    ConflictQueue,
    ConflictRecord,
)
from personalclaw.llm_helpers import one_shot_completion

logger = logging.getLogger(__name__)

#: Default cap per pass — a conflict draft is a model call each, and the queue is drained
#: over successive sync cycles rather than in one unbounded burst.
DEFAULT_LIMIT = 5

_PROMPT = """You are proposing a merge for a synchronization conflict. Two machines edited \
the same record after they last agreed on it, so neither version is authoritative.

Record family: {entry_id}
Entity id: {entity_id}

Version A (this machine's local version, currently authoritative):
{local}

Version B (the version pulled from the other machine):
{remote}

Draft a merged version that preserves every intentional edit from both versions. Keep the \
same JSON shape and the same "id". Never invent field values that appear in neither version. \
If a single field genuinely cannot be reconciled, keep version A's value and say so in the \
rationale.

Reply with ONLY a JSON object of exactly this shape:
{{"merged": {{...the merged record...}}, "rationale": "one short paragraph explaining the \
choices and naming anything a human must decide"}}"""


@dataclass
class DraftReport:
    """What one propose-only pass did. ``failed`` is a fail-open count, not an error."""

    considered: int = 0
    drafted: int = 0
    failed: int = 0
    skipped: str = ""

    @property
    def detail(self) -> str:
        if self.skipped:
            return f"skipped: {self.skipped}"
        return f"{self.drafted} drafted, {self.failed} without a proposal (of {self.considered})"


def pending(queue: ConflictQueue, *, limit: int = DEFAULT_LIMIT) -> list[ConflictRecord]:
    """Up to ``limit`` needs-review records that have no proposal and no prior draft attempt.

    A record whose draft already failed is NOT retried here: a per-pass retry storm against a
    down model would burn the budget on the same conflict every cycle, and the record is
    already correctly surfaced (needs-review, no proposal). Clearing ``proposal_error`` is a
    review-surface action.
    """
    out = [
        r
        for r in queue.items(status=STATUS_NEEDS_REVIEW)
        if r.proposal is None and not r.proposal_error
    ]
    return out[: max(0, int(limit))]


def _parse(text: str) -> tuple[dict | None, str]:
    """``(merged, rationale)`` from the model's answer; ``(None, "")`` on anything unusable.

    Tolerates a fenced code block, since that is the single most common shape drift; anything
    else unparseable is a fail-open miss, not an exception.
    """
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        if body.rstrip().endswith("```"):
            body = body.rstrip()[: -len("```")]
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None, ""
    if not isinstance(obj, dict):
        return None, ""
    merged = obj.get("merged")
    if not isinstance(merged, dict) or not merged:
        return None, ""
    rationale = obj.get("rationale")
    return merged, str(rationale or "")


async def draft_proposals(home: Path, *, limit: int = DEFAULT_LIMIT, now: str = "") -> DraftReport:
    """Draft a proposed merge + rationale for each pending conflict under ``home``.

    Writes only onto the conflict record (``proposal``/``rationale``/``proposed_at``, or
    ``proposal_error`` when the model could not answer). The record's status stays
    ``needs-review`` in every branch — a proposal is a suggestion for a human, and nothing in
    this module can apply one.
    """
    queue = ConflictQueue(home)
    todo = pending(queue, limit=limit)
    report = DraftReport(considered=len(todo))
    if not todo:
        report.skipped = "no pending conflicts"
        return report
    for rec in todo:
        prompt = _PROMPT.format(
            entry_id=rec.entry_id,
            entity_id=rec.entity_id,
            local=json.dumps(rec.local_row, indent=2, sort_keys=True, ensure_ascii=False),
            remote=json.dumps(rec.remote_row, indent=2, sort_keys=True, ensure_ascii=False),
        )
        try:
            text = await one_shot_completion(prompt, use_case="background")
        except Exception as exc:  # noqa: BLE001 — fail-open: keep the conflict, lose the draft
            logger.warning(
                "conflict merge: no proposal for %s/%s (%s)", rec.entry_id, rec.entity_id, exc
            )
            rec.proposal_error = f"{type(exc).__name__}: {exc}"[:300]
            queue.update(rec)
            report.failed += 1
            continue
        merged, rationale = _parse(text)
        if merged is None:
            logger.warning(
                "conflict merge: unusable draft for %s/%s — left as needs-review with no proposal",
                rec.entry_id,
                rec.entity_id,
            )
            rec.proposal_error = "model answer was not a {merged, rationale} object"
            queue.update(rec)
            report.failed += 1
            continue
        rec.proposal = merged
        rec.rationale = rationale
        rec.proposed_at = now
        rec.proposal_error = ""
        # status stays needs-review — a proposal is never an application (§7).
        queue.update(rec)
        report.drafted += 1
    return report
