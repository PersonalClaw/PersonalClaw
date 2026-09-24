"""Stage 3 — tiered strict-JSON proposals (PROACTIVE-ASSISTANT §1.3).

ONE model call per digest emits an action-proposal array. This module owns everything that
happens to that array before anything downstream believes a word of it, and every rule here
exists because the alternative is a jailbroken inbox item deciding what the machine does:

* **Exact-ordinal contract.** `item_id` must render to an ordinal the manifest minted (§1.1).
  Unknown ids are refused with `unknown_item_id` — never matched by prefix, never resolved to
  the "nearest" id, never fuzzy-matched against an item's text. A model that invented `"9"` for
  a seven-item window is guessing, and a repaired guess is an action against the wrong message.
  A JSON *number* `3` IS accepted, because the ordinals are decimal strings of 1-based
  positions, so `3` and `"3"` denote the same manifest line; refusing it would fail against a
  provider that emitted a number while buying no safety.
* **Tier is policy-clamped, not prompt-assigned.** The prompt asks for a tier; `clamp_tier`
  then RAISES it to the floor its action class carries. Anything that reaches outside the
  machine can never sit below `medium`; anything that permanently removes an item from the
  user's attention can never sit below `high`. The clamp only ever raises, so a model may
  volunteer more caution but never less — which is what makes "a jailbroken prompt cannot
  self-assign trivial" a property rather than a hope.
* **Fail CLOSED.** Unparseable output degrades to zero proposals and a `refused` reason. No
  retry loop against the schema: a second call with the same fenced content is the same
  content, so the retry buys a second chance at being injected and nothing else. (The
  opposite direction from §1.2's gate, which fails open — see `gate.py`.)
* **A cap that is enforced here, not requested in prose.** `MAX_PROPOSALS` truncates; the
  overflow is refused with `over_cap` rather than dropped, so the digest's counts reconcile.

The schema is also emitted as JSON Schema (`proposal_schema`) with `additionalProperties:
false`, for the typed-structured-output path (`output_type`, AUTONOMY-GUARDRAILS §2.4) on the
providers that enforce it natively. `parse_proposals` re-applies every constraint regardless,
because most providers do not, and a constraint that only holds on some backends is not one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The pre-declared action set. Proposals BIND ARGUMENTS to these; they can never introduce
#: an action (the frozen action-set invariant, AUTOMATION-SUBSTRATE decision 7). `none` is a
#: member so a model can say "nothing to do here" inside the schema instead of omitting the
#: item and leaving the absence ambiguous.
ACTION_TYPES: tuple[str, ...] = (
    "archive",
    "reply_draft",
    "create_task",
    "mute_thread",
    "dismiss",
    "remind",
    "none",
)

#: Tiers, least to most consequential. Index IS the ordering — `clamp_tier` compares indices.
TIERS: tuple[str, ...] = ("trivial", "low", "medium", "high")
_TIER_INDEX = {name: i for i, name in enumerate(TIERS)}

#: Hard cap on proposals per run (§1.3). Eight because a digest is a thing a human reads over
#: coffee; a ninth proposal is a backlog, and a backlog is what the next digest is for.
MAX_PROPOSALS = 8

#: Actions that reach outside the machine — floor `medium`. `reply_draft` is here even though
#: it produces a DRAFT: the draft is the object a §1.6 per-rule graduation turns into a send,
#: so the tier has to already reflect where the pattern can go, not only where it starts.
EXTERNAL_REACH_ACTIONS = frozenset({"reply_draft"})

#: Actions that permanently remove something from the user's attention — floor `high`.
#: `archive` and `mute_thread` are deliberately NOT here: §1.6 makes reversibility the whole
#: reason they are the trivial-capable class. `dismiss` is, because a dismissed item is gone
#: from the surface with nothing to undo it back onto.
DESTRUCTIVE_ACTIONS = frozenset({"dismiss"})

#: Floor for an action_type nobody declared. `high` rather than `trivial`: an unrecognised
#: action is exactly the case a prompt injection produces, and the fail direction has to be
#: "a human looks at it".
UNKNOWN_ACTION_FLOOR = "high"

#: Every field a proposal may carry. Anything else is stripped and reported (`extra_keys`) —
#: the `additionalProperties: false` half of the schema, enforced in Python because most
#: providers do not enforce it on the wire.
PROPOSAL_FIELDS = frozenset(
    {"item_id", "action_type", "action_config", "tier", "pattern_key", "reasoning"}
)

#: Refusal reasons. A closed vocabulary so a ledger reader counts them instead of parsing
#: prose, and so a new reason is a visible addition here.
REFUSE_UNPARSEABLE = "unparseable"
REFUSE_UNKNOWN_ITEM = "unknown_item_id"
REFUSE_UNKNOWN_ACTION = "unknown_action_type"
REFUSE_NO_ACTION = "no_action"
REFUSE_OVER_CAP = "over_cap"
REFUSE_MALFORMED = "malformed_entry"


def tier_floor(action_type: str) -> str:
    """The lowest tier `action_type` may occupy."""
    name = (action_type or "").strip().lower()
    if name in DESTRUCTIVE_ACTIONS:
        return "high"
    if name in EXTERNAL_REACH_ACTIONS:
        return "medium"
    if name not in ACTION_TYPES:
        return UNKNOWN_ACTION_FLOOR
    return TIERS[0]


def clamp_tier(action_type: str, tier: str) -> str:
    """Raise `tier` to its action class's floor. Never lowers; never returns an unknown tier.

    An unrecognised tier string is treated as the floor rather than as `trivial`: a model that
    answered `"low-ish"` has told us nothing, and reading nothing as the cheapest rung is the
    one interpretation that costs something.
    """
    floor = tier_floor(action_type)
    asked = _TIER_INDEX.get((tier or "").strip().lower())
    if asked is None:
        return floor
    return TIERS[max(asked, _TIER_INDEX[floor])]


@dataclass(frozen=True)
class Proposal:
    """One accepted proposal, post-clamp. `tier` is the enforced tier, not the asked one."""

    item_id: str
    action_type: str
    tier: str
    action_config: dict = field(default_factory=dict)
    pattern_key: str = ""
    reasoning: str = ""
    #: The tier the model asked for, when the clamp had to raise it. Empty when it did not.
    #: Kept because "the model tried to call an external send trivial" is a signal worth
    #: seeing in the ledger, and it is unrecoverable once the clamped value overwrites it.
    asked_tier: str = ""

    @property
    def clamped(self) -> bool:
        return bool(self.asked_tier) and self.asked_tier != self.tier


@dataclass(frozen=True)
class RefusedProposal:
    """A proposal that did not survive, with the reason and enough of it to audit."""

    reason: str
    item_id: str = ""
    action_type: str = ""
    detail: str = ""


@dataclass(frozen=True)
class ProposalBatch:
    proposals: tuple[Proposal, ...] = ()
    refused: tuple[RefusedProposal, ...] = ()
    #: True when the call produced nothing usable and the run degrades to a plain digest.
    degraded: bool = False
    #: Field names the model volunteered that the schema does not declare.
    extra_keys: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.proposals)


def proposal_schema(allowed_ordinals: frozenset[str] | set[str] | None = None) -> dict:
    """The strict JSON Schema for the proposal array (`additionalProperties: false`).

    When `allowed_ordinals` is supplied it becomes an `enum` on `item_id`, so a provider that
    enforces schemas natively rejects a hallucinated id on the wire and the run does not pay
    for a refusal it could have avoided. `parse_proposals` enforces the same set either way.
    """
    item_id: dict = {"type": "string", "maxLength": 8}
    if allowed_ordinals:
        item_id["enum"] = sorted(allowed_ordinals, key=lambda s: int(s) if s.isdigit() else 0)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["proposals"],
        "properties": {
            "proposals": {
                "type": "array",
                "maxItems": MAX_PROPOSALS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["item_id", "action_type", "tier"],
                    "properties": {
                        "item_id": item_id,
                        "action_type": {"type": "string", "enum": list(ACTION_TYPES)},
                        "action_config": {"type": "object"},
                        "tier": {"type": "string", "enum": list(TIERS)},
                        "pattern_key": {"type": "string", "maxLength": 200},
                        "reasoning": {"type": "string", "maxLength": 300},
                    },
                },
            }
        },
    }


def parse_proposals(
    raw: object,
    *,
    allowed_ordinals: frozenset[str] | set[str],
) -> ProposalBatch:
    """Turn the model's reply into a batch, enforcing every §1.3 constraint.

    `raw` may be the dict `output_type=dict` returns or a JSON string. Anything that is not a
    dict carrying a `proposals` list is a degraded batch — zero proposals, one `unparseable`
    refusal — and the caller renders a plain digest.
    """
    import json

    payload: object = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except ValueError:
            return ProposalBatch(
                refused=(RefusedProposal(reason=REFUSE_UNPARSEABLE, detail="not JSON"),),
                degraded=True,
            )
    if not isinstance(payload, dict) or not isinstance(payload.get("proposals"), list):
        return ProposalBatch(
            refused=(RefusedProposal(reason=REFUSE_UNPARSEABLE, detail="no 'proposals' array"),),
            degraded=True,
        )

    accepted: list[Proposal] = []
    refused: list[RefusedProposal] = []
    extras: set[str] = set()

    for entry in payload["proposals"]:
        if not isinstance(entry, dict):
            refused.append(RefusedProposal(reason=REFUSE_MALFORMED, detail=type(entry).__name__))
            continue
        extras.update(k for k in entry if k not in PROPOSAL_FIELDS)

        item_id = str(entry.get("item_id", "") or "").strip()
        action_type = str(entry.get("action_type", "") or "").strip().lower()

        if item_id not in allowed_ordinals:
            # The anti-hallucination refusal. Recorded with the id it named so the ledger row
            # says WHAT was invented, which is the only way to tell a confused model from an
            # injected one after the fact.
            refused.append(
                RefusedProposal(
                    reason=REFUSE_UNKNOWN_ITEM, item_id=item_id, action_type=action_type
                )
            )
            continue
        if action_type not in ACTION_TYPES:
            refused.append(
                RefusedProposal(
                    reason=REFUSE_UNKNOWN_ACTION, item_id=item_id, action_type=action_type
                )
            )
            continue
        if action_type == "none":
            refused.append(RefusedProposal(reason=REFUSE_NO_ACTION, item_id=item_id))
            continue
        if len(accepted) >= MAX_PROPOSALS:
            refused.append(
                RefusedProposal(reason=REFUSE_OVER_CAP, item_id=item_id, action_type=action_type)
            )
            continue

        asked = str(entry.get("tier", "") or "").strip().lower()
        tier = clamp_tier(action_type, asked)
        cfg = entry.get("action_config")
        accepted.append(
            Proposal(
                item_id=item_id,
                action_type=action_type,
                tier=tier,
                action_config=dict(cfg) if isinstance(cfg, dict) else {},
                pattern_key=str(entry.get("pattern_key", "") or "").strip()[:200],
                reasoning=str(entry.get("reasoning", "") or "").strip()[:300],
                asked_tier=asked if asked != tier else "",
            )
        )

    return ProposalBatch(
        proposals=tuple(accepted),
        refused=tuple(refused),
        degraded=False,
        extra_keys=tuple(sorted(extras)),
    )


__all__ = [
    "ACTION_TYPES",
    "DESTRUCTIVE_ACTIONS",
    "EXTERNAL_REACH_ACTIONS",
    "MAX_PROPOSALS",
    "PROPOSAL_FIELDS",
    "REFUSE_MALFORMED",
    "REFUSE_NO_ACTION",
    "REFUSE_OVER_CAP",
    "REFUSE_UNKNOWN_ACTION",
    "REFUSE_UNKNOWN_ITEM",
    "REFUSE_UNPARSEABLE",
    "TIERS",
    "UNKNOWN_ACTION_FLOOR",
    "Proposal",
    "ProposalBatch",
    "RefusedProposal",
    "clamp_tier",
    "parse_proposals",
    "proposal_schema",
    "tier_floor",
]
