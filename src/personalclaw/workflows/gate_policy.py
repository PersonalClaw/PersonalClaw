"""Gate policy — who may answer, and what auto-approves.

Slice 5a made a gate durable. This decides whether a human is asked at all, and whose
answer counts.

**Auto-approve is scoped by RISK, never blanket.** A scheduled run that stops for every
approval is not unattended, but one that waves through a `DESTRUCTIVE` action is a
liability. So trigger-origin runs auto-approve SAFE and CAUTION gates and still block
DESTRUCTIVE ones. The risk gradient is `tool_providers.RiskLevel`, reused deliberately —
a second private vocabulary would drift from the one the approval UI already renders.

**A remote-channel gate is default-DENY and owner-bound.** An approval arriving over a
shared channel is an instruction from whoever typed it, and a workflow gate can authorize
real action. Only the run's requester can answer; a non-owner reply is ignored rather than
argued with, and a gate nobody answers denies rather than passing. Default-ALLOW here
would make a shared Slack channel a privilege-escalation path.

**"Always allow" is run-scoped and cleared on rewind.** Remembering a decision across a
rewind would silently auto-approve the very step the user rewound to reconsider.

**An event gate's semantics live in the RESUME path, not here (#375).** A transient-hold
contract for `gate{kind: event}` — `evaluate_event_gate`, `HoldState`, `HoldVerdict`,
`DEFAULT_EVENT_HOLD_LIMIT`, and `controller._event_holds` — was written, documented and
unit-tested with **zero production callers**, and its state dict was allocated and never
touched. It has been deleted rather than wired, because the delivery model it assumed does
not exist and nothing asked for it: it presumed an event fires INTO the engine, which then
decides whether a prerequisite is met and re-holds up to `hold_limit`. What actually
shipped is the opposite direction — an event gate parks in WAITING (`engine.dispatch_gate`,
the shared approval tail) and a trigger *targets the parked run*:
`set_onetime_task(resume_run_id="self")` → `triggers.wakeup.resume_target_of` →
`triggers.loop._apply_resume` → `service.resume_run` → `controller.resume`. The wake-up is
single-use and claimed by `human_input.consume_continuation`'s rename, so nothing here
needs a `preserve_event` flag. `bundled/goal-pursuit-monitor`'s `park` gate is the live
consumer, and no template has ever declared `hold_limit`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.tool_providers.base import RiskLevel
from personalclaw.workflows.models import OriginKind

logger = logging.getLogger(__name__)

#: Risk levels a trigger-origin run may auto-approve. DESTRUCTIVE is deliberately absent:
#: an unattended run that waves through arbitrary shell exec or an outward side effect is
#: the failure this whole slice exists to prevent.
AUTO_APPROVABLE_RISKS = frozenset({RiskLevel.SAFE, RiskLevel.CAUTION})

#: Origins that run with nobody watching, so they get the auto-approve policy.
UNATTENDED_ORIGINS = frozenset(
    {OriginKind.SCHEDULE, OriginKind.EVENT, OriginKind.HOOK, OriginKind.IDLE}
)


class Decision(str, Enum):
    """What the policy decided about a gate."""

    ASK = "ask"  # surface to a human
    AUTO_APPROVED = "auto_approved"
    AUTO_DENIED = "auto_denied"  # remote gate timed out, or a non-owner tried
    REMEMBERED = "remembered"  # a run-scoped "always allow" applies


@dataclass
class PolicyVerdict:
    decision: Decision
    reason: str = ""
    risk: str = ""

    @property
    def asks_human(self) -> bool:
        return self.decision == Decision.ASK

    @property
    def approved(self) -> bool:
        return self.decision in (Decision.AUTO_APPROVED, Decision.REMEMBERED)

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.value, "reason": self.reason, "risk": self.risk}


def gate_risk(node_config: dict[str, Any]) -> RiskLevel:
    """The gate's declared risk, defaulting to DESTRUCTIVE.

    Deny-by-default toward higher risk: an undeclared gate is treated as the most
    dangerous, so forgetting to classify one makes it ASK rather than silently
    auto-approve. The opposite default would turn every unclassified gate in an
    unattended run into an unreviewed action.
    """
    raw = str((node_config or {}).get("risk", "") or "").strip().lower()
    try:
        return RiskLevel(raw)
    except ValueError:
        return RiskLevel.DESTRUCTIVE


def is_unattended(origin_kind: OriginKind, *, mode: str = "background") -> bool:
    """Is nobody watching this run?

    Chat/manual/API origins have a requester who can answer, even in background mode.
    Only genuinely trigger-fired work is unattended.
    """
    return origin_kind in UNATTENDED_ORIGINS and str(mode) != "blocking"


@dataclass
class AllowMemory:
    """Run-scoped "always allow", keyed by (operation, target).

    Run-scoped and cleared on rewind on purpose: remembering across a rewind would
    auto-approve the very step the user rewound in order to reconsider it.
    """

    _allowed: set[tuple[str, str]] = field(default_factory=set)

    @staticmethod
    def key(node_config: dict[str, Any], node_id: str) -> tuple[str, str]:
        cfg = node_config or {}
        operation = str(cfg.get("operation", "") or cfg.get("kind", "") or "approval")
        target = str(cfg.get("target", "") or node_id)
        return (operation, target)

    def remember(self, node_config: dict[str, Any], node_id: str) -> None:
        self._allowed.add(self.key(node_config, node_id))

    def allows(self, node_config: dict[str, Any], node_id: str) -> bool:
        return self.key(node_config, node_id) in self._allowed

    def clear(self) -> None:
        self._allowed.clear()

    def __len__(self) -> int:
        return len(self._allowed)


def decide(
    node_config: dict[str, Any],
    node_id: str,
    *,
    origin_kind: OriginKind = OriginKind.MANUAL,
    mode: str = "background",
    memory: AllowMemory | None = None,
) -> PolicyVerdict:
    """Should this gate ask a human?

    Order matters. A remembered allow wins first (the user already decided). Then the
    unattended auto-approve, risk-scoped. Everything else asks — the safe fallback, because
    asking costs a delay while wrongly proceeding costs an action.
    """
    risk = gate_risk(node_config)
    if memory is not None and memory.allows(node_config, node_id):
        return PolicyVerdict(
            decision=Decision.REMEMBERED,
            reason="the user chose 'always allow' for this operation in this run",
            risk=risk.value,
        )
    if is_unattended(origin_kind, mode=mode):
        if risk in AUTO_APPROVABLE_RISKS:
            return PolicyVerdict(
                decision=Decision.AUTO_APPROVED,
                reason=f"unattended run auto-approves {risk.value} gates",
                risk=risk.value,
            )
        return PolicyVerdict(
            decision=Decision.ASK,
            reason=(
                f"{risk.value} gates always ask, even unattended — an unreviewed "
                "destructive action is worse than a stalled run"
            ),
            risk=risk.value,
        )
    return PolicyVerdict(decision=Decision.ASK, reason="attended run", risk=risk.value)


# ── remote-channel gates ─────────────────────────────────────────────────────


def owner_of(run: Any) -> str:
    """Who may answer this run's gates.

    The requester recorded at start — a workflow gate can authorize real action, so an
    approval arriving over a shared channel must be attributable, not merely present.
    """
    origin = getattr(run, "origin", None)
    return str(getattr(origin, "session_key", "") or "")


def may_answer(run: Any, *, responder: str, channel: str = "") -> tuple[bool, str]:
    """Is `responder` allowed to answer this run's gate?

    A local surface (widget/CLI/HTTP with no channel) is already authenticated by the
    gateway, so it passes. A REMOTE channel reply must come from the run's owner: without
    owner binding, a shared channel becomes a privilege-escalation path where anyone who
    can type can approve someone else's deployment.

    A non-owner is IGNORED, not argued with — replying "you may not approve this" to a
    shared channel leaks the existence and content of the gate to everyone in it.
    """
    if not channel:
        return True, ""
    owner = owner_of(run)
    if not owner:
        # No recorded requester and a remote reply: refuse. An unattributable approval on
        # a shared channel is exactly what owner binding exists to stop.
        return False, "this run has no recorded owner, so remote approval is refused"
    if responder and responder == owner:
        return True, ""
    return False, "only the run's requester may approve from a shared channel"


def remote_timeout_decision(node_config: dict[str, Any]) -> PolicyVerdict:
    """What an unanswered REMOTE gate becomes: DENY (WF2-R7).

    Default-DENY is the whole point. A remote gate that passed on timeout would let a
    deployment ship because nobody was reading a channel — silence is not consent.
    """
    return PolicyVerdict(
        decision=Decision.AUTO_DENIED,
        reason="remote gate expired with no owner reply; silence is not consent",
        risk=gate_risk(node_config).value,
    )


# ── action-node clarification ────────────────────────────────────────────────


def clarification_from_output(output: Any) -> dict[str, Any] | None:
    """Extract a clarification REQUEST from an action provider's output (WF2-R7).

    Any action node may ask for input without a template author pre-placing a gate: a
    provider that cannot proceed says so in its own output, and the run pauses into
    needs_input rather than failing. Pre-placing a gate for every question a provider
    *might* ask is not possible — the provider knows, the author does not.

    The recognized shape is a `needs_input` key carrying the ask. Anything else is a
    normal output.
    """
    if not isinstance(output, dict):
        return None
    raw = output.get("needs_input") or output.get("clarification")
    if not raw:
        return None
    if isinstance(raw, str):
        return {"kind": "text", "prompt": raw}
    if isinstance(raw, dict):
        ask = dict(raw)
        ask.setdefault("kind", "text")
        ask.setdefault("prompt", "The action needs more information.")
        return ask
    return None
