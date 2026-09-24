"""Stage 2 — the classifier gate (PROACTIVE-ASSISTANT §1.2).

The gate exists to spend nothing on a quiet window and to keep the per-source rules the user
wrote ("from GitHub notifications only surface review requests; skip dependabot") in front of
the expensive stage. It is a **filter with a real refusal path**: a `drop` disposition removes the
item from the digest entirely — it is not proposed on, and it is not listed. A gate that only
ever re-ordered its input would be decoration, so the drop path is the one this module is
written around, and `tests/test_proactive_triage.py` pins it against a floor taken from the
fixture rather than from the gate's own answer.

Two failure directions, deliberately opposite:

* **No rules, no items, or the gate switched off ⇒ no model call.** `should_call_gate` is the
  precondition guard; a window with nothing in it must not cost a token, which is the whole
  reason a cheap store query runs first.
* **A gate that ran and could not be understood ⇒ fail OPEN.** An unparseable disposition list, a
  disposition for an id the manifest never minted, an item the gate simply did not mention: all
  resolve to `propose`. This is the opposite of the proposal stage's fail-CLOSED (§1.3), and
  the asymmetry is the point — a broken *filter* that silently swallowed the user's inbox is
  worse than one that shows them too much, while a broken *proposer* that invented actions is
  worse than one that proposes nothing. PA-1 recorded the same split for `ProactiveConfig`:
  both switches fail closed, the classifier gate fails open.

The gate never sees an action, a tier or a rule id. It answers one question per item — is
this worth the user's attention — and the routing that follows lives in §1.4's matcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from personalclaw.proactive.manifest import CollectedItem, Manifest


#: What the gate may say. `surface` is the middle rung §1.2 asks for and the reason the gate
#: is not a boolean: an item can be worth SEEING without being worth proposing an action on,
#: and collapsing the two would force every mention into either silence or a proposal.
class GateDisposition(str, Enum):
    DROP = "drop"
    SURFACE = "surface"
    PROPOSE = "propose"


#: What an unreadable or missing disposition resolves to. Named, not inlined, so the fail-open
#: direction is one grep away and a future edit that flips it is visible in a diff.
FAIL_OPEN_DISPOSITION = GateDisposition.PROPOSE


@dataclass(frozen=True)
class GateRule:
    """One user-authored natural-language filter, scoped to a collect lane.

    `source` is a lane name (`inbox`/`channel`/`run`) or `"*"`. Scoping is structural rather
    than left to the prompt: a rule about GitHub notifications must not be able to influence
    how a background run's error row is judged, and a model asked to respect scope in prose
    will sometimes not.
    """

    source: str
    rule: str

    def applies_to(self, item: CollectedItem) -> bool:
        src = (self.source or "*").strip().lower()
        return src in ("*", "") or src == item.source


@dataclass(frozen=True)
class GateOutcome:
    """One item's disposition plus WHY — the rationale a `skipped_triage` row carries."""

    disposition: GateDisposition
    rationale: str = ""
    #: The rule text the gate said it applied, echoed back for the ledger row. Empty when the
    #: gate volunteered none; never invented here.
    rule: str = ""
    #: True when this outcome came from the fail-open default rather than the gate. Recorded
    #: because "the gate passed it" and "the gate could not be read" are different facts, and
    #: a digest built entirely from defaults must be diagnosable.
    defaulted: bool = False


@dataclass(frozen=True)
class GateResult:
    """The gate's effect on the window, as three disjoint sets plus the audit trail."""

    proposable: tuple[CollectedItem, ...] = ()
    surfaced: tuple[CollectedItem, ...] = ()
    dropped: tuple[CollectedItem, ...] = ()
    outcomes: dict[str, GateOutcome] = field(default_factory=dict)

    @property
    def kept(self) -> tuple[CollectedItem, ...]:
        """Everything the digest may mention: proposable + surfaced, in ordinal order."""
        merged = list(self.proposable) + list(self.surfaced)
        return tuple(sorted(merged, key=lambda i: int(i.ordinal or "0")))

    def counts(self) -> dict[str, int]:
        return {
            "proposable": len(self.proposable),
            "surfaced": len(self.surfaced),
            "dropped": len(self.dropped),
        }


def rules_for(rules: list[GateRule] | tuple[GateRule, ...], item: CollectedItem) -> list[GateRule]:
    return [r for r in rules if r.applies_to(item)]


def should_call_gate(
    manifest: Manifest,
    rules: list[GateRule] | tuple[GateRule, ...],
    *,
    enabled: bool,
) -> bool:
    """The precondition guard — may the gate spend a model call at all?

    False on an empty window (the §1.2 short-circuit: one cheap store query decides whether
    the LLM stage runs), false when the user turned the gate off, and false when no rule
    applies to anything collected — a gate with no rule to apply has nothing to decide, and
    asking a model to "filter by no criteria" is a token spent to learn that.
    """
    if not enabled:
        return False
    if manifest.is_empty:
        return False
    return any(rules_for(rules, item) for item in manifest.items)


def _coerce_disposition(raw: object) -> GateDisposition | None:
    if isinstance(raw, GateDisposition):
        return raw
    text = str(raw or "").strip().lower()
    for member in GateDisposition:
        if member.value == text:
            return member
    return None


def parse_gate_output(raw: object, manifest: Manifest) -> dict[str, GateOutcome]:
    """Read the gate's strict-JSON reply into per-ordinal outcomes, failing OPEN.

    Accepts ``{"dispositions": [{"item_id", "disposition", "rationale", "rule"}]}`` — a dict from
    `output_type=dict`, or a JSON string. Anything else yields ``{}``, which `apply_gate`
    then fills with `propose` for every item.

    A disposition for an ordinal the manifest never minted is DISCARDED, not resolved. The
    manifest is the only id authority (§1.1); honouring an id it does not contain would let
    the gate drop an item that does not exist and, worse, would make the id space negotiable
    one stage before the proposal contract depends on it being fixed.
    """
    import json

    payload: object = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("dispositions")
    if not isinstance(entries, list):
        return {}

    allowed = manifest.ordinals()
    outcomes: dict[str, GateOutcome] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ordinal = str(entry.get("item_id", "") or "").strip()
        if ordinal not in allowed or ordinal in outcomes:
            continue
        disposition = _coerce_disposition(entry.get("disposition"))
        if disposition is None:
            # The gate named a real item but not a real disposition: fail open for THIS item
            # rather than discarding the row, so the rationale it did give survives.
            outcomes[ordinal] = GateOutcome(
                disposition=FAIL_OPEN_DISPOSITION,
                rationale=str(entry.get("rationale", "") or "").strip(),
                rule=str(entry.get("rule", "") or "").strip(),
                defaulted=True,
            )
            continue
        outcomes[ordinal] = GateOutcome(
            disposition=disposition,
            rationale=str(entry.get("rationale", "") or "").strip(),
            rule=str(entry.get("rule", "") or "").strip(),
        )
    return outcomes


def apply_gate(manifest: Manifest, outcomes: dict[str, GateOutcome]) -> GateResult:
    """Split the window by disposition. Items with no outcome fail OPEN to `propose`."""
    proposable: list[CollectedItem] = []
    surfaced: list[CollectedItem] = []
    dropped: list[CollectedItem] = []
    resolved: dict[str, GateOutcome] = {}

    for item in manifest.items:
        outcome = outcomes.get(item.ordinal)
        if outcome is None:
            outcome = GateOutcome(
                disposition=FAIL_OPEN_DISPOSITION,
                rationale="no gate disposition for this item",
                defaulted=True,
            )
        resolved[item.ordinal] = outcome
        if outcome.disposition is GateDisposition.DROP:
            dropped.append(item)
        elif outcome.disposition is GateDisposition.SURFACE:
            surfaced.append(item)
        else:
            proposable.append(item)

    return GateResult(
        proposable=tuple(proposable),
        surfaced=tuple(surfaced),
        dropped=tuple(dropped),
        outcomes=resolved,
    )


def open_gate(manifest: Manifest) -> GateResult:
    """The no-gate result: everything proposable, every outcome marked defaulted.

    Used when `should_call_gate` said no. A distinct constructor rather than
    `apply_gate(manifest, {})` at each call site so the "we did not ask" case is one name.
    """
    return apply_gate(manifest, {})


__all__ = [
    "FAIL_OPEN_DISPOSITION",
    "GateOutcome",
    "GateResult",
    "GateRule",
    "GateDisposition",
    "apply_gate",
    "open_gate",
    "parse_gate_output",
    "rules_for",
    "should_call_gate",
]
