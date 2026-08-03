"""The Proposal Inbox: one surface for six kinds, and the accept gate (§6.1 / §7 — S75).

§7's criterion 1 has two halves: one inbox shows all six proposal kinds with provenance, evidence
manifests, and risk-tier metadata — **and the model cannot accept its own proposals under any trust
mode**. The second half is the load-bearing one, and this module is where it becomes a control
rather than a coincidence.

**Measured before writing.** `learning/proposals.accept()` takes `(pid, installer=...)` and NOTHING
in it knows WHO is accepting: no actor, no caller, no trust check. The invariant held only because
no agent tool happened to call it — an ABSENCE, not a control. Add one MCP tool next month and it is
gone, silently, with no test failing. `require_human` is the gate; `test_an_agent_can_never_accept`
is the regression.

The rest of the module is the inbox's own discipline:

* **Six kinds, one queue.** `proposals.Kind` already has exactly six, so this reuses them rather
than
  minting a parallel vocabulary — a second kind list is how a surface silently stops showing one.
* **Risk tier is METADATA, never a lane.** §3.1 is explicit: any "auto" tier is guardrail-violating.
  Tiers order and filter the queue; they never decide.
* **`manifest_valid=false` surfaces, never rejects.** §3.1's validation is lenient-but-recording: a
  proposal with a broken manifest is still reviewable, flagged, because dropping it would hide a
  refiner bug behind an empty inbox.
* **Provenance is required to render a row.** A proposal whose source cannot be shown is one a
  reviewer cannot weigh, and an unweighable row trains people to bulk-accept.

Pure decisions and view models. The store stays in `learning.proposals`; nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


#: The actor vocabulary, REUSED from S56's verified-done matrix rather than redefined. That module
#: already carries the doctrine this gate needs — "the AGENT is a worker whose self-report is
#: exactly
#: what needs checking" — and two actor enums on one machine would eventually disagree about who an
#: `agent` is.
def _actor_enum():
    from personalclaw.workflows.verified_done import Actor

    return Actor


#: Actors permitted to ACCEPT a proposal. Deliberately just the user.
#:
#: The ENGINE is excluded as well as the agent, and that is not an oversight: §7 says the
#: human-installs
#: invariant is absolute, so "the engine observed the work" — sufficient authority to record a task
#: outcome in S56 — is NOT sufficient authority to install autonomously-authored behaviour. An
#: engine
#: that could accept would make every gate upstream of it decorative.
ACCEPT_ACTORS: frozenset[str] = frozenset({"user"})

#: Actors permitted to REJECT. The agent may not, for a subtler reason than accepting: an agent that
#: could reject could clear its own bad proposals out of the queue before a human ever read them,
#: and
#: the rejection exemplars §2.2 learns from would silently stop accumulating.
REJECT_ACTORS: frozenset[str] = frozenset({"user"})

#: Actors permitted to FILE a proposal. All three — filing is the safe verb, and §2.6/§3.1/§3.2 all
#: depend on non-human proposers.
FILE_ACTORS: frozenset[str] = frozenset({"user", "agent", "engine"})


class Denial(str, Enum):
    """Why a review action was refused. Typed because the refusal is audited.

    `SELF_ACCEPT` is the one §7 names. Kept distinct from a generic permission denial so the SEL row
    says what was actually attempted — "an agent tried to accept its own proposal" is an incident,
    while "an unknown actor tried to accept" is a bug.
    """

    SELF_ACCEPT = "self_accept"
    NOT_A_REVIEWER = "not_a_reviewer"
    UNKNOWN_ACTOR = "unknown_actor"
    ALREADY_RESOLVED = "already_resolved"


@dataclass
class Gate:
    """Whether a review action may proceed."""

    allowed: bool
    denial: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "denial": self.denial, "reason": self.reason}


def require_human(
    *,
    action: str,
    actor: str,
    status: str = "pending",
) -> Gate:
    """THE gate. Fails CLOSED for anything that is not a human reviewer.

    §7: "the model cannot accept its own proposals under ANY trust mode" — so this deliberately
    takes no trust parameter. A gate that could be relaxed by a mode is a gate whose invariant is a
    default, and the plan is explicit that this one is absolute. Tool-set scoping is the structural
    (§3.1: the refiner agent gets only `propose_*` tools); this is the enforcement half, so the
    invariant survives someone adding a tool without reading the plan.

    An UNKNOWN actor is denied rather than assumed human. The failure directions are not symmetric:
    denying a human costs one click through the UI, while admitting an unrecognized caller is the
    hole this exists to close.
    """
    known = {a.value for a in _actor_enum()}
    if actor not in known:
        return Gate(
            allowed=False,
            denial=Denial.UNKNOWN_ACTOR.value,
            reason=f"unrecognized actor {actor!r}; expected one of {', '.join(sorted(known))} — an "
            "unknown caller is denied rather than assumed human",
        )
    if status not in ("pending", "draft"):
        return Gate(
            allowed=False,
            denial=Denial.ALREADY_RESOLVED.value,
            reason=f"the proposal is already {status}; re-deciding it would overwrite a recorded "
            "decision",
        )
    permitted = ACCEPT_ACTORS if action == "accept" else REJECT_ACTORS
    if actor in permitted:
        return Gate(allowed=True)
    if actor == "agent":
        return Gate(
            allowed=False,
            denial=Denial.SELF_ACCEPT.value,
            reason=f"an agent may propose but never {action} — a worker whose self-report installs "
            "itself is not reviewed at all",
        )
    return Gate(
        allowed=False,
        denial=Denial.NOT_A_REVIEWER.value,
        reason=f"{actor!r} may not {action} proposals; only a human reviewer installs behaviour",
    )


def can_file(actor: str) -> bool:
    """Whether this actor may FILE a proposal. All three may — filing is the safe verb.

    Separated from `require_human` so the asymmetry is explicit in the code rather than implied: the
    whole design depends on non-human proposers, and only the DECISION is human-only.
    """
    return actor in FILE_ACTORS


# ── the inbox view model ──


#: Risk tiers, in display order. Imported from the refiner rather than restated — §3.1 assigns them
#: deterministically by edit type, and a second ordering here would let the inbox sort by a scale
#: the
#: refiner does not use.
def _tier_order() -> list[str]:
    from personalclaw.learning.refiner import RiskTier

    return [RiskTier.LOW.value, RiskTier.REVIEW.value, RiskTier.MANUAL_ONLY.value]


#: Tiers a bulk-accept control may include. `manual_only` is excluded BY NAME: §3.1 stamps it on
#: destructive edits, and "bulk" plus "destructive" is the combination that turns an ergonomic
#: affordance into an accident. Note this bounds a UI CONTROL, not the gate — every accept in a bulk
#: action still passes `require_human` individually.
BULK_ACCEPTABLE_TIERS: frozenset[str] = frozenset({"low", "review"})


@dataclass
class Row:
    """One inbox row: everything a reviewer needs to decide without opening anything else.

    §6.1 names the fields, and each is here because its absence produces a specific bad review:
    without provenance the reviewer cannot weigh the source, without the evidence manifest they
    cannot check the claim, without `manifest_valid` they cannot tell a flagged proposal from a
    and without the reinforcement count they cannot tell one observation from twenty.
    """

    id: str
    kind: str
    title: str
    provenance: str = ""
    source_cadence: str = ""
    source_excerpt: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    reinforcements: int = 0
    confidence: float = 0.0
    manifest_valid: bool = True
    manifest_issues: list[str] = field(default_factory=list)
    risk_tier: str = "review"
    status: str = "pending"

    @property
    def renderable(self) -> bool:
        """Whether this row can be shown honestly.

        Requires a kind, a title, and PROVENANCE. A proposal whose source cannot be shown is one a
        reviewer cannot weigh — and a queue of unweighable rows trains people to bulk-accept, which
        defeats the human-installs invariant while appearing to honour it.
        """
        return bool(self.kind and self.title and self.provenance)

    @property
    def bulk_acceptable(self) -> bool:
        """Whether a bulk control may include this row.

        Four conditions, each excluding a different mistake: a `manual_only` tier (destructive), an
        invalid manifest (the claim is unverified), a missing evidence ref (nothing to check), and —
        measured while probing — an UNRENDERABLE row.

        That last one was the defect: a proposal with no title or provenance came back
        `bulk_acceptable=True` while `renderable=False`, so a row the UI cannot honestly show was
        eligible for a control that accepts without opening it. Bulk-accepting something a reviewer
        could not have read is the human-installs invariant in name only.
        """
        return (
            self.renderable
            and self.risk_tier in BULK_ACCEPTABLE_TIERS
            and self.manifest_valid
            and bool(self.evidence_refs)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "provenance": self.provenance,
            "source_cadence": self.source_cadence,
            "source_excerpt": self.source_excerpt,
            "evidence_refs": list(self.evidence_refs),
            "reinforcements": self.reinforcements,
            "confidence": round(self.confidence, 4),
            "manifest_valid": self.manifest_valid,
            "manifest_issues": list(self.manifest_issues),
            "risk_tier": self.risk_tier,
            "status": self.status,
            "renderable": self.renderable,
            "bulk_acceptable": self.bulk_acceptable,
        }


def row_from_proposal(prop: Any, *, risk_tier: str = "") -> Row:
    """Project a stored `Proposal` onto an inbox row.

    Reads through `getattr` with defaults rather than assuming the dataclass shape, because
    `Proposal` gains fields as the flywheel grows and a projection that raises on an older record
    would empty the inbox exactly when someone needs to review a backlog.

    `risk_tier` is passed in rather than computed: only a `template_diff` carries typed ops to
    derive it from, so guessing one for a lesson would stamp a number that means nothing.
    """
    tier = risk_tier or "review"
    return Row(
        id=str(getattr(prop, "id", "") or ""),
        kind=str(getattr(prop, "kind", "") or ""),
        title=str(getattr(prop, "title", "") or ""),
        provenance=str(getattr(prop, "provenance", "") or ""),
        source_cadence=str(getattr(prop, "source_cadence", "") or ""),
        source_excerpt=str(getattr(prop, "source_excerpt", "") or ""),
        evidence_refs=[str(r) for r in (getattr(prop, "evidence_refs", None) or [])],
        reinforcements=int(getattr(prop, "reinforcements", 0) or 0),
        confidence=float(getattr(prop, "confidence", 0.0) or 0.0),
        manifest_valid=bool(getattr(prop, "manifest_valid", True)),
        manifest_issues=[str(i) for i in (getattr(prop, "manifest_issues", None) or [])],
        risk_tier=tier,
        status=str(getattr(prop, "status", "pending") or "pending"),
    )


def order_rows(rows: list[Row]) -> list[Row]:
    """The inbox order: riskiest-to-review first, then strongest evidence.

    `manual_only` sorts FIRST rather than last. It is the tier that most needs a human's attention,
    and burying destructive proposals under a page of parameter tweaks is how one gets accepted by
    momentum. Within a tier, more reinforcements first — twenty observations outrank one.

    Ties break on id so the order is stable: a queue that reshuffles between renders makes a
    reviewer lose their place, and re-reading a dismissed row is how a decision gets reversed by
    accident.
    """
    order = _tier_order()

    def key(row: Row) -> tuple[int, int, float, str]:
        try:
            # Negated so a LATER tier in display order (riskier) sorts first.
            tier_rank = -order.index(row.risk_tier)
        except ValueError:
            # An unknown tier outranks every known one. Measured: `1` collided with the rank a
            # known tier could produce, so an unscored proposal sorted BELOW `manual_only` instead
            # of above it — and an unrecognized tier is the one case where nobody has judged the
            # risk at all, which is strictly more urgent than a judged destructive edit.
            tier_rank = -len(order) - 1
        return (tier_rank, -row.reinforcements, -row.confidence, row.id)

    return sorted(rows, key=key)


def filter_rows(
    rows: list[Row],
    *,
    kind: str = "",
    tier: str = "",
    flagged_only: bool = False,
) -> list[Row]:
    """Filter the queue. §6.1's "filter by kind/source".

    `flagged_only` surfaces the `manifest_valid=false` rows specifically. That is the view a
    maintainer wants when the refiner starts producing broken manifests — §3.1 records those rather
    than rejecting them, and a flag nobody can filter to is a flag nobody sees.
    """
    out = list(rows or [])
    if kind:
        out = [r for r in out if r.kind == kind]
    if tier:
        out = [r for r in out if r.risk_tier == tier]
    if flagged_only:
        out = [r for r in out if not r.manifest_valid]
    return out


@dataclass
class InboxView:
    """The whole inbox, ordered and counted.

    Counts per kind and per tier are here because §6.1's surface offers filtering: a filter chip
    with no count is a chip a user has to click to discover is empty.
    """

    rows: list[Row] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Row count. A property rather than only a `to_dict` key: a caller checking the queue size
        should not have to serialize the whole view to learn it."""
        return len(self.rows)

    @property
    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.kind] = counts.get(row.kind, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def by_tier(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.risk_tier] = counts.get(row.risk_tier, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def flagged(self) -> int:
        return sum(1 for row in self.rows if not row.manifest_valid)

    @property
    def unrenderable(self) -> list[str]:
        """Ids of rows that cannot be shown honestly.

        Reported rather than silently dropped: a proposal missing its provenance is a PROPOSER bug,
        and an inbox that quietly hides them makes that bug invisible.
        """
        return [row.id for row in self.rows if not row.renderable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "total": len(self.rows),
            "by_kind": self.by_kind,
            "by_tier": self.by_tier,
            "flagged": self.flagged,
            "unrenderable": self.unrenderable,
            "bulk_acceptable": sum(1 for r in self.rows if r.bulk_acceptable),
        }


def build_view(
    proposals: list[Any],
    *,
    tiers: dict[str, str] | None = None,
    kind: str = "",
    tier: str = "",
    flagged_only: bool = False,
) -> InboxView:
    """Assemble the inbox from stored proposals.

    `tiers` maps proposal id → risk tier, supplied by the caller that has the typed ops. Passing it
    in keeps this module from importing the refiner's op vocabulary for a value only one kind has.
    """
    rows = [
        row_from_proposal(prop, risk_tier=(tiers or {}).get(str(getattr(prop, "id", "")), ""))
        for prop in proposals or []
    ]
    rows = filter_rows(rows, kind=kind, tier=tier, flagged_only=flagged_only)
    return InboxView(rows=order_rows(rows))


def audit_denial(*, action: str, actor: str, pid: str, gate: Gate) -> dict[str, Any]:
    """The SEL row for a refused review action.

    §3 requires a SEL audit of accepts; a refused accept is at least as worth recording. A blocked
    self-accept in particular is the signal that something is calling the wrong path — and it would
    be invisible if only successes were logged.
    """
    return {
        "operation": f"learning_proposal_{action}",
        "outcome": "blocked",
        "actor": actor,
        "proposal": pid,
        "denial": gate.denial,
        "reason": gate.reason,
    }
