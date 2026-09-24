"""Routing gap detection — what turns measured evidence into a PROPOSAL (MRT-5 §6.3).

:mod:`personalclaw.routing.proposals` is a queue *primitive*: it is handed a ``current`` order, a
``proposed`` order and the evidence, and it enqueues. Nothing computed the gap, so nothing ever
called it — a queue with no executor, whose ``n >= min_samples`` clause had no ``n`` anywhere on the
proposal path. This module is that executor, and it is deliberately the ONLY caller of
:func:`proposals.propose` in the tree.

**Where it runs, and why there** — the trigger point §6.3 never names. Three candidates were on the
table: at route time inside ``route_refs``, on the stats-fold write, or a scheduled sweep. The fold
write wins on four counts:

* A proposal is a function of the EVIDENCE, not of a request. The fold write is the one moment new
  evidence arrives, so it is the only moment a gap can newly appear. A timer would re-derive the
  same answer on an interval that no plan section supplies, with a job owner that does not exist.
* The route path stays exactly as it is: no queue read, no proposal write, and no second walk of the
  run ledger on the path a model call waits on. Detecting at route time would add all three — and
  would additionally have to run the learned stage on the lever-3 path that currently short-circuits
  before it, i.e. new work on every call for every cell that already has a recorded order.
* ``proposals._notify``'s own docstring already assumed this call site ("reached from a fold, not a
  request", which is why it reaches the dashboard state through the process-wide accessor rather
  than a request object). The queue was written for this trigger.
* A proposal has to reach a user who never opens the Routing tab. That disqualifies the cheapest
  variant of all — sweeping when the proposals list is read — because its notification would only
  ever fire while the user was already looking at the surface it points to.

The fold write is documented best-effort observability that "must not break a model call", so this
runs inside its own ``try`` with its own log line: a detector failure stays attributable to the
detector, is never mistaken for a fold failure, and never reaches the call.

**What counts as a gap.** Exactly this: *the learned finding is not what routing will actually do.*
``current`` is what :func:`policy.route_refs` returns today — whichever lever wins, be it a recorded
order, the heuristic floor, or the learned stage itself — and ``proposed`` is the learned stage
applied on top of that. The two differ only when the fold's opinion is not already in effect, which
is precisely when there is something for a human to decide. Under ``learned`` mode with no recorded
order the stage is already live, the two agree, and nothing is proposed: the machine does not ask
permission for what it is already permitted to do. Under ``heuristic`` mode, or against a recorded
order that the evidence has since outgrown, a proposal is the ONLY route to the table — which is
what propose-don't-write means in practice.

**Why every floor here is borrowed rather than restated.** ``n >= min_samples`` is
``learned._opinion``'s floor and the score is ``stats._score``: this module calls both instead of
re-deriving either, so there is never a second answer to "does this ref have an opinion" or "what is
it worth". The quality floor is the same ``hysteresis`` band the ordering stage bands by — and a
within-band difference is deliberately NOT a gap, because cost is the only thing allowed to reorder
near-equals (§5.2). A cost preference is not a quality finding and must not nag a user about their
table.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing only
    from personalclaw.routing.proposals import RoutingProposal

logger = logging.getLogger(__name__)

#: The bounded forensic window read for evidence — the same tail the telemetry route reads, so a
#: proposal's p50 numbers and the Routing tab's p50 numbers cannot disagree.
_AUDIT_TAIL = 2000


def _cell_opinions(rows: dict[str, Any], *, min_samples: int) -> dict[str, float]:
    """``{ref: learned score}`` for the refs the fold has an opinion about, others omitted.

    Delegates to ``learned._opinion`` — the confidence floor and the 0.60/0.40 scoring both live
    there (and in ``stats._score`` behind it). Restating either here would mint a second floor that
    could disagree with the one the ordering stage actually applies.
    """
    from personalclaw.routing import learned as _learned

    out: dict[str, float] = {}
    for ref, row in rows.items():
        score = _learned._opinion(row, min_samples=min_samples)
        if score is not None:
            out[str(ref)] = score
    return out


def _sample_ids(
    audit_rows: list[dict[str, Any]], use_case: str, query_class: str, refs: tuple[str, ...]
) -> list[str]:
    """Audit ids for this cell's most recent attempts on ``refs``, newest first.

    These are the correlation handle §6.4 asks for: a reviewer pastes one into the audit reader and
    sees the actual call the proposal was built from. Newest first because a reviewer checking a
    sample wants the recent ones; the cap is applied by ``proposals._clean_evidence``.
    """
    ids: list[str] = []
    for row in reversed(audit_rows):
        if row.get("use_case") != use_case or row.get("query_class") != query_class:
            continue
        if f"{row.get('provider', '')}:{row.get('model', '')}" not in refs:
            continue
        audit_id = str(row.get("audit_id", "") or "")
        if audit_id:
            ids.append(audit_id)
    return ids


def _evidence(
    stats: dict[str, Any],
    use_case: str,
    query_class: str,
    *,
    opinions: dict[str, float],
    knobs: dict[str, Any],
    current: list[str],
    proposed: list[str],
) -> dict[str, Any]:
    """The §6.3 evidence payload — everything needed to review the proposal without re-running it.

    Every value is a number, a dict of numbers, or an id list. No prose:
    ``proposals._clean_evidence`` FENCES any other string, which would mangle a ref into an
    untrusted-text block, and the two refs that matter are already on the record as ``current[0]``
    and ``proposed[0]``.

    Latency and cost come from ``telemetry.telemetry_rows`` — the existing read model for this exact
    bucket — so the numbers on a proposal are the same numbers the Routing tab shows. The deltas are
    ``promoted − demoted``, so **negative is better** on both axes.
    """
    audit_rows: list[dict[str, Any]] = []
    try:
        from personalclaw.guardrails.audit import read_recent

        audit_rows = read_recent(limit=_AUDIT_TAIL)
    except Exception:  # noqa: BLE001 — thinner evidence is fine; a missing tail is not a failure
        logger.debug("routing proposal evidence: audit tail unreadable", exc_info=True)

    from personalclaw.routing.telemetry import telemetry_rows

    view = {
        str(row["ref"]): row for row in telemetry_rows(stats, audit_rows, use_case, query_class)
    }
    promoted, demoted = proposed[0], current[0]

    def _p50(ref: str) -> float:
        return float(view.get(ref, {}).get("p50_ms", 0.0) or 0.0)

    def _cost(ref: str) -> float:
        return float(view.get(ref, {}).get("avg_cost_usd", 0.0) or 0.0)

    return {
        "n": {ref: int(view.get(ref, {}).get("n", 0)) for ref in sorted(opinions)},
        "scores": {ref: round(score, 4) for ref, score in sorted(opinions.items())},
        "min_samples": int(knobs["min_samples"]),
        "hysteresis": float(knobs["hysteresis"]),
        "cloud_quality_margin": float(knobs["cloud_quality_margin"]),
        "p50_delta_ms": round(_p50(promoted) - _p50(demoted), 1),
        "cost_delta_usd": round(_cost(promoted) - _cost(demoted), 6),
        "sample_audit_ids": _sample_ids(audit_rows, use_case, query_class, (promoted, demoted)),
    }


def detect_gap(
    stats: dict[str, Any],
    use_case: str,
    query_class: str,
    *,
    home: Path,
) -> "RoutingProposal | None":
    """Enqueue a proposal if this cell's evidence has outgrown what routing does. Else ``None``.

    ``stats`` is the just-folded ``routing_stats.json`` (already in memory at the call site, so this
    costs no extra read of it). **Writes no policy table**: the only write on this path is the
    proposal queue, and ``propose`` is the one function that performs it.

    Returns ``None`` — cheaply and quietly, never raising — for every reason there is nothing to
    ask: fewer than two refs, fewer than two above the confidence floor, every opinion inside the
    hysteresis band (a cost preference is not a quality gap), a decision already pending for this
    cell, routing off for the use case, a pin in force (which short-circuits ordering, so a recorded
    order under it would be dead), or the finding already being in effect.
    """
    from personalclaw.routing import learned as _learned
    from personalclaw.routing import policy, proposals

    rows = _learned._rows_for(stats, use_case, query_class)
    if len(rows) < 2:
        return None

    knobs = policy._routing_knobs()
    opinions = _cell_opinions(rows, min_samples=int(knobs["min_samples"]))
    if len(opinions) < 2:
        return None  # the n >= min_samples floor — fewer than two opinions is nothing to compare
    if max(opinions.values()) - min(opinions.values()) <= float(knobs["hysteresis"]):
        return None  # all near-equal: cost may reorder these, but that is not a quality gap

    # Cheap gates before any ordering work. The pending check is what stops a cell that is already
    # awaiting a decision from re-deriving the same finding on every subsequent model call.
    if any(
        p.use_case == use_case and p.query_class == query_class
        for p in proposals.pending(home=home)
    ):
        return None
    if not policy.routing_active(use_case, home=home):
        return None
    if policy.pin_for(use_case, home=home):
        return None

    refs = sorted(rows)
    current = policy.route_refs(use_case, query_class, refs, home=home)
    proposed = policy._learned_order(
        current, use_case, query_class, policy._local_provider_keys(), home=home
    )
    if proposed == current:
        return None  # the learned finding is already what routing does — nothing to decide

    return proposals.propose(
        use_case=use_case,
        query_class=query_class,
        current=current,
        proposed=proposed,
        evidence=_evidence(
            stats,
            use_case,
            query_class,
            opinions=opinions,
            knobs=knobs,
            current=current,
            proposed=proposed,
        ),
        home=home,
    )
