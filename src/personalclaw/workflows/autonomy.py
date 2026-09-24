"""Autonomy and risk — approving a plan is not a boolean.

"Run it" and "run it unattended" are different grants, and the difference is not the user's
patience: it is what the plan can do while nobody is watching. So approval offers a MODE, and the
modes a plan may offer are governed by what its own nodes touch.

**One risk-signal registry, by reference.** Destructive ops, external writes, credentials and
payments, schedule creation. Cited from one place so the planner, the templates and the review
surface cannot drift into three different opinions about what "risky" means. It reuses the engine's
existing `RiskLevel` gradient rather than inventing a private vocabulary — a second one would drift
from the gradient the approval UI already renders.

**Floors cannot be silently lowered.** A template declares an `autonomy_floor`; neither the planner
nor the user can quietly go below it. A user CAN override, but the override costs exactly one
informed-consent question — never a silent honor, and never a silent upgrade either. Both silences
are failures: honoring "run unattended" on a plan that deletes production is the obvious one, and
quietly refusing it is the one that makes a user distrust the whole control.

**Everything compiles to `require_hitl`.** Node-level HITL/AFK typing, the risk registry and the
chosen mode all reduce to one flag the engine already knows how to honour. The plan's own risk note
is explicit: autonomy machinery that grew its own enforcement path would contradict the engine's
trust plumbing, so the compilation target is single by design.

**Two interrupts, and only two.** An unattended run stops for an irreversible action and for an
uninferable credential or payment detail. Everything else proceeds with a JOURNALED assumption —
because a run that stops for every ambiguity is not unattended, and one that proceeds without
recording what it assumed cannot be audited afterwards.

**The interrupt taxonomy ADVISES; it does not enforce.** `should_interrupt` answers "would
unattended stop here?" for the plan-review surface. Enforcement is `gate_policy.decide`, on the
engine's own `RiskLevel` × `OriginKind` vocabulary. That split is deliberate — see
`compile_require_hitl`: autonomy machinery that grew a second enforcement path would contradict the
engine's trust plumbing, so this module explains the trade and the engine makes it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.tool_providers.base import RiskLevel

logger = logging.getLogger(__name__)


class Mode(str, Enum):
    """What the user granted at approval time.

    Ordered weakest-to-strongest so a floor comparison is an index comparison — and so "lower" has
    exactly one meaning across the module rather than being re-argued per call site.
    """

    FRAME_ONLY = "frame_only"  # produce the plan, run nothing
    FIRST_STAGE = "first_stage"  # run one stage, then come back
    PER_STAGE = "per_stage"  # approve each stage as it comes
    UNATTENDED = "unattended"  # run to completion, stopping only at the two interrupts


#: Strength order. `index()` on this is the comparison — an enum with a `<` operator would invite
#: comparing modes to strings, which is how a floor check silently passes.
MODE_ORDER: tuple[Mode, ...] = (Mode.FRAME_ONLY, Mode.FIRST_STAGE, Mode.PER_STAGE, Mode.UNATTENDED)


class Attention(str, Enum):
    """Whether a node needs a person present.

    Typed at PLAN time, not run time: "does this need me?" is a property of the work, and deciding
    it while the run is already going means deciding it under time pressure.
    """

    AFK = "afk"  # safe to run with nobody watching
    HITL = "hitl"  # stops even in an unattended run


# ── the risk-signal registry ──


@dataclass(frozen=True)
class RiskSignal:
    """One canonical reason a plan is risky."""

    name: str
    level: RiskLevel
    #: Why this matters, in the words a consent question will use. Not decoration: an informed
    #: consent question built from a signal name is not informed.
    consequence: str
    #: Providers whose mere presence trips this signal.
    providers: tuple[str, ...] = ()
    #: Regexes over prompt/command text.
    patterns: tuple[str, ...] = ()
    #: The strongest mode a plan hitting this signal may be OFFERED.
    caps_autonomy_at: Mode = Mode.PER_STAGE


#: THE registry. One file, cited by reference — the plan is explicit that planner and templates must
#: not each carry their own opinion of what risky means.
RISK_SIGNALS: tuple[RiskSignal, ...] = (
    RiskSignal(
        name="destructive_op",
        level=RiskLevel.DESTRUCTIVE,
        consequence="this can delete or overwrite data that cannot be recovered",
        providers=("bash", "run-script"),
        patterns=(
            r"\brm\s+-rf\b",
            r"\bdrop\s+(table|database)\b",
            r"\bdelete\s+from\b",
            # `\btruncate\b` alone matched the BINDING PIPE `| truncate(1500)` — measured, it
            # flagged three templates as destructive for shortening a string in a prompt. The SQL
            # form always names a target.
            r"\btruncate\s+(table\s+)?[a-z_]",
            r"\bgit\s+push\s+--force\b",
            r"\bgit\s+reset\s+--hard\b",
        ),
        caps_autonomy_at=Mode.PER_STAGE,
    ),
    RiskSignal(
        name="external_write",
        level=RiskLevel.CAUTION,
        consequence="this sends something outward that cannot be unsent",
        providers=("send-message", "notify", "webhook", "call-app-route"),
        patterns=(r"\bpublish\b", r"\bpost to\b", r"\bemail\b", r"\bsend (a )?message\b"),
        caps_autonomy_at=Mode.PER_STAGE,
    ),
    RiskSignal(
        name="credentials_or_payment",
        level=RiskLevel.DESTRUCTIVE,
        consequence=(
            "this touches credentials or money, where a mistake is costly as well as wrong"
        ),
        providers=(),
        # ACTION-shaped only. Measured: bare `\bcredential\b` fired on `audit-sweep`'s finder,
        # whose prompt asks the model to look FOR credential-handling problems — reading about a
        # risk is not taking one, and capping an auditing template for doing its job is a scanner
        # arguing with the library it is meant to protect.
        patterns=(
            r"\b(rotate|revoke|issue|reset|store|write|update)\b[^.]{0,30}"
            r"\b(credential|api[_ -]?key|secret|token|password)\b",
            r"\b(charge|refund|invoice|bill|pay)\b[^.]{0,20}\b(card|account|customer|amount)\b",
            r"\bmake a payment\b",
            r"\bprocess (the )?payment\b",
        ),
        caps_autonomy_at=Mode.PER_STAGE,
    ),
    RiskSignal(
        name="schedule_creation",
        level=RiskLevel.CAUTION,
        consequence="this creates something that will keep running after this run ends",
        providers=("run-workflow",),
        patterns=(r"\bschedule\b", r"\bcron\b", r"\brecurring\b", r"\bevery (day|hour|week)\b"),
        caps_autonomy_at=Mode.PER_STAGE,
    ),
    RiskSignal(
        name="production_target",
        level=RiskLevel.DESTRUCTIVE,
        consequence="this acts on production, where the blast radius is real users",
        providers=(),
        # An action verb near the target, for the same reason as the credential patterns: a prompt
        # that MENTIONS production (a report about it, a question about it) is not one that acts on
        # it, and flagging the mention would cap every plan that discusses the system.
        patterns=(
            r"\b(deploy|delete|drop|migrate|restart|rotate|wipe)\b[^.]{0,40}"
            r"\b(production|prod\b(?!uct)|live (site|system|db|database))\b",
            # `to production` / `in production` with a write verb: the preposition is what
            # distinguishes acting on it from describing it. `write a report about our
            # production architecture` matched the generic-verb form and was flagged.
            r"\b(write|push|update|patch|apply)\b[^.]{0,20}\b(to|in|on)\s+"
            r"(production|prod\b(?!uct))\b",
            r"\b(production|prod\b(?!uct))\b[^.]{0,30}"
            r"\b(deploy|delete|drop|migrate|restart|wipe)\b",
        ),
        caps_autonomy_at=Mode.PER_STAGE,
    ),
)

SIGNALS_BY_NAME = {s.name: s for s in RISK_SIGNALS}


@dataclass
class RiskHit:
    """One signal, found on one node."""

    signal: str
    level: str
    node_id: str
    evidence: str
    consequence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "level": self.level,
            "node_id": self.node_id,
            "evidence": self.evidence,
            "consequence": self.consequence,
        }


def scan_risk(spec: dict[str, Any]) -> list[RiskHit]:
    """Every risk signal this plan trips, with the node and the evidence.

    Node-attributed rather than plan-level: "this plan is risky" tells a reviewer to read all
    twelve stages, while "stage 4 touches payments" tells them where to look. A finding nobody can
    locate is a finding nobody acts on.
    """
    from personalclaw.workflows.models import Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return []
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        return []

    hits: list[RiskHit] = []
    for path, node in walk(root):
        node_id = node.id or path
        cfg = node.config or {}
        provider = str(cfg.get("provider", "") or "")
        # The searchable surface of one node. `with` args are included because a `bash` node's
        # danger is entirely in its command, not in the word "bash".
        text_parts = [
            str(cfg.get("prompt", "") or ""),
            str(cfg.get("label", "") or ""),
            str(cfg.get("expr", "") or ""),
        ]
        with_args = cfg.get("with")
        if isinstance(with_args, dict):
            text_parts.extend(str(v) for v in with_args.values())
        haystack = " ".join(text_parts).lower()

        for signal in RISK_SIGNALS:
            # Capability AND content, not either-or. The provider check used to `continue` past the
            # patterns, so a `run-script` node whose argument said `drop table users` was reported
            # only as "uses the run-script provider" — the same verdict on far worse evidence. A
            # reviewer told the capability has to go read the node; one told the matched text
            # already knows.
            if provider and provider in signal.providers:
                hits.append(
                    RiskHit(
                        signal=signal.name,
                        level=signal.level.value,
                        node_id=node_id,
                        evidence=f"uses the `{provider}` provider",
                        consequence=signal.consequence,
                    )
                )
            for pattern in signal.patterns:
                match = re.search(pattern, haystack)
                if match:
                    hits.append(
                        RiskHit(
                            signal=signal.name,
                            level=signal.level.value,
                            node_id=node_id,
                            evidence=f"matched {match.group(0)!r}",
                            consequence=signal.consequence,
                        )
                    )
                    break
    return hits


# ── HITL/AFK typing ──


def type_attention(spec: dict[str, Any], hits: list[RiskHit] | None = None) -> dict[str, Attention]:
    """Type every work node HITL or AFK.

    Derived from the risk scan rather than declared, because a node's attention need follows from
    what it does. A DESTRUCTIVE-level hit makes a node HITL; everything else is AFK unless the
    author said otherwise.

    An author's explicit `require_hitl: true` is honoured and never downgraded — the author knows
    something the scanner does not, and a scanner that overrode them would make the declaration
    useless.
    """
    from personalclaw.workflows.models import Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return {}
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        return {}

    hits = hits if hits is not None else scan_risk(spec)
    destructive_nodes = {h.node_id for h in hits if h.level == RiskLevel.DESTRUCTIVE.value}

    out: dict[str, Attention] = {}
    for path, node in walk(root):
        if node.is_container:
            continue
        node_id = node.id or path
        cfg = node.config or {}
        if cfg.get("require_hitl") is True:
            out[node_id] = Attention.HITL
            continue
        if node_id in destructive_nodes:
            out[node_id] = Attention.HITL
            continue
        if node.kind.value == "gate" and str(cfg.get("kind", "")) == "approval":
            out[node_id] = Attention.HITL
            continue
        out[node_id] = Attention.AFK
    return out


def compile_require_hitl(spec: dict[str, Any], mode: Mode) -> dict[str, bool]:
    """The ONE uniform engine target: node id → `require_hitl`.

    Everything above — the registry, the attention typing, the chosen mode — reduces here. The
    plan's own risk note says autonomy machinery that grew a second enforcement path would
    contradict the engine's trust plumbing, so there is exactly one output and the engine already
    knows how to honour it.

    `frame_only` compiles to nothing rather than to all-True: it means run NOTHING, and expressing
    that as "stop at every node" would start a run the user declined.
    """
    if mode == Mode.FRAME_ONLY:
        return {}

    attention = type_attention(spec)
    if mode == Mode.UNATTENDED:
        # Unattended still stops at HITL nodes. That is the whole point of typing them: an
        # unattended grant is "do not ask me about the routine parts", not "do anything".
        return {node_id: kind == Attention.HITL for node_id, kind in attention.items()}

    if mode == Mode.PER_STAGE:
        return {node_id: True for node_id in attention}

    if mode == Mode.FIRST_STAGE:
        # Everything after the first work node needs a person, which is what "run one stage then
        # come back" means when compiled to a per-node flag.
        ordered = list(attention)
        return {node_id: index > 0 for index, node_id in enumerate(ordered)}

    return {node_id: True for node_id in attention}


# ── floors and offers ──


@dataclass
class AutonomyOffer:
    """What the approval gate may offer, and why it is capped.

    `consent_question` is populated only when the user asked for more than the plan allows. Exactly
    one question, and only when it changes what runs: a control that asks about everything trains
    the user to click through, which is the failure a consent question exists to prevent.
    """

    offered: list[Mode] = field(default_factory=list)
    ceiling: Mode = Mode.UNATTENDED
    floor: Mode = Mode.FRAME_ONLY
    capped_by: list[str] = field(default_factory=list)
    consent_question: str = ""
    #: The mode that will actually be used absent further input.
    recommended: Mode = Mode.PER_STAGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "offered": [m.value for m in self.offered],
            "ceiling": self.ceiling.value,
            "floor": self.floor.value,
            "capped_by": list(self.capped_by),
            "consent_question": self.consent_question,
            "recommended": self.recommended.value,
        }


def offer_autonomy(
    spec: dict[str, Any],
    *,
    template_floor: Mode | None = None,
    requested: Mode | None = None,
    hits: list[RiskHit] | None = None,
    earned: Mode | None = None,
) -> AutonomyOffer:
    """Which autonomy modes this plan may be run at.

    The ceiling comes from the risk scan; the floor from the template. A request above the
    ceiling produces exactly ONE informed-consent question naming the CONSEQUENCE rather
    than the signal — "this can delete data that cannot be recovered" is a decision a user
    can make, and "destructive_op hit" is not.

    Silent honor and silent refusal are both failures. Honoring "unattended" on a plan that
    deletes production is the obvious one; quietly downgrading it is the one that makes the
    user distrust the control and stop reading it.
    """
    hits = hits if hits is not None else scan_risk(spec)

    ceiling = Mode.UNATTENDED
    capped_by: list[str] = []
    for hit in hits:
        signal = SIGNALS_BY_NAME.get(hit.signal)
        if signal is None:
            continue
        if MODE_ORDER.index(signal.caps_autonomy_at) < MODE_ORDER.index(ceiling):
            ceiling = signal.caps_autonomy_at
        label = f"{hit.signal} at `{hit.node_id}`"
        if label not in capped_by:
            capped_by.append(label)

    floor = template_floor or Mode.FRAME_ONLY
    if MODE_ORDER.index(floor) > MODE_ORDER.index(ceiling):
        # A template floor ABOVE the risk ceiling is a real conflict: the template insists on at
        # least per-stage while the risk scan caps it lower, or vice versa. The floor wins, because
        # it is the author's considered minimum and the scan is a heuristic — but the conflict is
        # recorded rather than resolved silently.
        capped_by.append(f"template floor `{floor.value}` exceeds the risk ceiling")
        ceiling = floor

    offer = AutonomyOffer(
        offered=[m for m in MODE_ORDER if MODE_ORDER.index(m) <= MODE_ORDER.index(ceiling)],
        ceiling=ceiling,
        floor=floor,
        capped_by=capped_by,
    )
    offer.offered = [m for m in offer.offered if MODE_ORDER.index(m) >= MODE_ORDER.index(floor)]
    if not offer.offered:
        offer.offered = [floor]

    offer.recommended = _recommend(spec, offer, hits, earned=earned)

    if requested is not None and MODE_ORDER.index(requested) > MODE_ORDER.index(ceiling):
        worst = max(hits, key=lambda h: h.level == RiskLevel.DESTRUCTIVE.value, default=None)
        because = worst.consequence if worst is not None else "this plan is capped below that mode"
        offer.consent_question = (
            f"You asked to run this {requested.value.replace('_', ' ')}, but {because} "
            f"(at `{worst.node_id if worst else '?'}`). Run it {requested.value.replace('_', ' ')} "
            f"anyway, or use {ceiling.value.replace('_', ' ')}?"
        )

    return offer


def _recommend(
    spec: dict[str, Any], offer: AutonomyOffer, hits: list[RiskHit], *, earned: Mode | None
) -> Mode:
    """The default mode, driven by cost-of-error.

    A plan whose steps carry machine-checkable verification defaults toward unattended: if it goes
    wrong, something catches it. A high-stakes plan with no verification defaults down — that is
    the combination where a mistake is both likely to happen and unlikely to be noticed.

    Earned trust raises the default but never above the ceiling. A template that has run cleanly ten
    times has earned a cheaper default, not permission to touch production unattended.
    """
    from personalclaw.workflows.contracts import derive_contracts

    ceiling_index = MODE_ORDER.index(offer.ceiling)
    floor_index = MODE_ORDER.index(offer.floor)

    contracts = derive_contracts(spec)
    verified = [
        c
        for c in contracts
        if getattr(c, "verification", "") in ("gate", "loop-condition", "artifact")
    ]
    destructive = any(h.level == RiskLevel.DESTRUCTIVE.value for h in hits)

    if destructive:
        base = Mode.PER_STAGE
    elif contracts and len(verified) >= max(1, len(contracts) // 2):
        base = Mode.UNATTENDED
    else:
        base = Mode.PER_STAGE

    if earned is not None and MODE_ORDER.index(earned) > MODE_ORDER.index(base) and not destructive:
        base = earned

    index = min(ceiling_index, max(floor_index, MODE_ORDER.index(base)))
    return MODE_ORDER[index]


# ── the confirmation matrix ──


class ConfirmationType(str, Enum):
    """What a confirmation is ABOUT. Distinct from risk: a destructive read does not exist, and a
    safe outward write still leaves the machine."""

    READ = "read"
    WRITE = "write"
    OUTWARD = "outward"  # leaves this machine
    SPEND = "spend"
    DESTRUCTIVE = "destructive"


@dataclass
class ConfirmationRequest:
    """One typed, resolve-by-id confirmation.

    Carries its own id so a resolution can arrive asynchronously — over a channel, from the inbox,
    minutes later — and still be matched to the thing it answers. An untyped "yes" arriving with no
    id is an answer to whatever asked most recently, which is how the wrong action gets approved.
    """

    request_id: str
    node_id: str
    confirmation_type: ConfirmationType
    risk: RiskLevel
    question: str
    auto_approved: bool = False
    reason: str = ""
    #: The `RISK_SIGNALS` names that fired on this node, carried by NAME rather than collapsed into
    #: `risk`. `_classify_node` flattens every DESTRUCTIVE-level signal to the same
    #: `(DESTRUCTIVE, DESTRUCTIVE)` pair, which loses the distinction the interrupt taxonomy needs:
    #: "this deletes data" and "this needs a credential I cannot guess" both stop an unattended run,
    #: but for different reasons and with different remedies. Reading the signal name off the
    #: registry keeps that distinction without text-scanning the question.
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "node_id": self.node_id,
            "type": self.confirmation_type.value,
            "risk": self.risk.value,
            "question": self.question,
            "auto_approved": self.auto_approved,
            "reason": self.reason,
            "signals": list(self.signals),
        }


def confirmation_policy(
    confirmation_type: ConfirmationType, risk: RiskLevel, mode: Mode
) -> tuple[bool, str]:
    """`(ConfirmationType × RiskLevel × mode)` → `(auto_approve, reason)`.

    One table, evaluated engine-side. The plan is explicit that a per-executor evaluation would let
    two executors disagree about the same action — and the one that disagreed permissively would be
    the one that mattered.
    """
    if risk == RiskLevel.DESTRUCTIVE or confirmation_type == ConfirmationType.DESTRUCTIVE:
        # No mode auto-approves destruction. `unattended` means "do not ask me about the routine
        # parts", and there is no reading of it that includes this.
        return False, "destructive actions are never auto-approved, in any mode"

    if mode == Mode.UNATTENDED:
        return True, "unattended mode auto-approves everything short of destruction"

    if mode == Mode.PER_STAGE:
        if confirmation_type == ConfirmationType.READ:
            # A read has nothing to approve. Asking about it is the noise that makes a user stop
            # reading the questions that matter.
            return True, "read-only, nothing to approve"
        return False, f"per-stage mode asks about {confirmation_type.value} actions"

    if mode == Mode.FIRST_STAGE:
        return False, "first-stage mode returns to the user after one stage"

    return False, "frame-only mode runs nothing"


def build_confirmations(spec: dict[str, Any], mode: Mode) -> list[ConfirmationRequest]:
    """The confirmations this plan will raise at the chosen mode.

    Computed at PLAN time so the review can show them: "this will stop you twice" is a fact a user
    should have before approving, not a discovery they make while waiting.
    """
    from personalclaw.workflows.models import Node, walk

    root_raw = spec.get("root")
    if not isinstance(root_raw, dict):
        return []
    try:
        root = Node.from_dict(root_raw)
    except Exception:
        return []

    hits_by_node: dict[str, list[RiskHit]] = {}
    for hit in scan_risk(spec):
        hits_by_node.setdefault(hit.node_id, []).append(hit)

    out: list[ConfirmationRequest] = []
    for path, node in walk(root):
        if node.is_container:
            continue
        node_id = node.id or path
        node_hits = hits_by_node.get(node_id, [])
        confirmation_type, risk = _classify_node(node, node_hits)
        auto, reason = confirmation_policy(confirmation_type, risk, mode)
        if auto:
            continue
        out.append(
            ConfirmationRequest(
                request_id=f"cr-{node_id}",
                node_id=node_id,
                confirmation_type=confirmation_type,
                risk=risk,
                question=_question_for(node_id, confirmation_type, node_hits),
                auto_approved=False,
                reason=reason,
                signals=tuple(sorted({h.signal for h in node_hits})),
            )
        )
    return out


def _classify_node(node: Any, hits: list[RiskHit]) -> tuple[ConfirmationType, RiskLevel]:
    """What kind of confirmation this node needs, and at what risk."""
    if any(h.level == RiskLevel.DESTRUCTIVE.value for h in hits):
        return ConfirmationType.DESTRUCTIVE, RiskLevel.DESTRUCTIVE
    signals = {h.signal for h in hits}
    if "external_write" in signals:
        return ConfirmationType.OUTWARD, RiskLevel.CAUTION
    if "schedule_creation" in signals:
        return ConfirmationType.WRITE, RiskLevel.CAUTION

    kind = node.kind.value
    cfg = node.config or {}
    if kind == "action":
        provider = str(cfg.get("provider", "") or "")
        # A knowledge write is a write; a knowledge read is not. Treating every action as a write
        # would make a retrieve-heavy plan stop constantly for nothing.
        if provider.endswith(("-retrieve", "-health", "-gaps")):
            return ConfirmationType.READ, RiskLevel.SAFE
        return ConfirmationType.WRITE, RiskLevel.CAUTION
    if kind in ("transform",):
        return ConfirmationType.READ, RiskLevel.SAFE
    if kind in ("stage", "infer"):
        return ConfirmationType.SPEND, RiskLevel.SAFE
    return ConfirmationType.READ, RiskLevel.SAFE


def _question_for(node_id: str, confirmation_type: ConfirmationType, hits: list[RiskHit]) -> str:
    if hits:
        return f"`{node_id}`: {hits[0].consequence}. Proceed?"
    return f"`{node_id}` performs a {confirmation_type.value} action. Proceed?"


# ── the two interrupts ──


class Interrupt(str, Enum):
    """The only two things that stop an unattended run.

    Closed on purpose. "Anything surprising" is not a taxonomy — it is a licence to stop whenever,
    which makes unattended mode a slower per-stage mode. Everything outside this set proceeds with
    a journaled assumption.

    Closed AND produced: every member is named in `should_interrupt`, and a ratchet fails the build
    if a new one is added without a branch. A documented interrupt nothing can produce is worse than
    no interrupt at all — it reads, to anyone auditing the guardrail, like a stop that exists.
    (WF2UNI-13 removed a third member, `CONFLICTING`, for exactly that reason: "requirements that
    contradict each other" named no signal a `ConfirmationRequest` carries. The one real
    contradiction this module detects — a template `autonomy_floor` above the risk ceiling, in
    `offer_autonomy` — is resolved at PLAN time by letting the floor win, so it never reaches a run
    to stop it.)
    """

    IRREVERSIBLE = "irreversible"
    UNINFERABLE = "uninferable"  # a credential or payment detail nobody can guess


@dataclass
class Assumption:
    """What an unattended run decided for itself, and why.

    Journaled rather than silent. An unattended run that proceeded on an assumption nobody recorded
    cannot be audited afterwards — and "why did it do that?" is the first question asked about every
    autonomous run that surprised someone.
    """

    node_id: str
    question: str
    assumed: str
    because: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "question": self.question,
            "assumed": self.assumed,
            "because": self.because,
        }


def should_interrupt(
    *, mode: Mode, confirmation: ConfirmationRequest
) -> tuple[bool, Interrupt | None, str]:
    """Does this confirmation stop an unattended run?

    Returns `(interrupt, which, reason)`. In any mode below unattended everything stops anyway, so
    the taxonomy only governs the unattended case — which is exactly where a wrong answer is
    expensive in both directions.

    ADVISORY. This is the plan-review counterfactual ("what would unattended skip?"), not the run's
    gate: `gate_policy.decide` enforces. Keeping the two apart is why this function may be read as a
    frank description of the trade rather than as a control that has to be conservative.

    Exhaustive over `ConfirmationType` with a RAISING tail, and every `Interrupt` member is named
    here. Both halves are the ratchet (WF2UNI-13): before it, two of the three documented interrupts
    were produced nowhere, so the taxonomy described stops that could not happen.
    """
    if mode != Mode.UNATTENDED:
        return True, None, f"{mode.value} mode stops here regardless"

    if SIGNALS_BY_NAME["credentials_or_payment"].name in confirmation.signals:
        # Checked BEFORE risk, and it changes only the LABEL: `credentials_or_payment` is a
        # DESTRUCTIVE-level signal, so this request also trips the IRREVERSIBLE branch below and
        # stops either way. The distinction is worth carrying anyway — "this cannot be undone" tells
        # the user to review, "nobody can guess this value" tells them to supply it. Naming the
        # second as irreversible sends them looking for a blast radius that is not the problem.
        return True, Interrupt.UNINFERABLE, confirmation.question

    if confirmation.risk == RiskLevel.DESTRUCTIVE:
        return True, Interrupt.IRREVERSIBLE, confirmation.question

    kind = confirmation.confirmation_type
    if kind == ConfirmationType.DESTRUCTIVE:
        # Reachable only for a request whose type says destructive while its risk does not.
        # Trusting the weaker of two disagreeing fields is how a destructive action gets waved
        # through, so the stop is asserted from either one.
        return True, Interrupt.IRREVERSIBLE, confirmation.question

    if kind == ConfirmationType.OUTWARD:
        # Outward writes are CAUTION, not destructive — but they cannot be unsent, and an
        # unattended run posting to a channel is the surprise people remember. Interrupting here is
        # the deliberate asymmetry: a delayed message costs patience, a wrong one costs trust.
        return True, Interrupt.IRREVERSIBLE, confirmation.question

    if kind == ConfirmationType.SPEND:
        return False, None, "spend proceeds under the run's budget"

    if kind == ConfirmationType.READ:
        return False, None, "a read needs no decision"

    if kind == ConfirmationType.WRITE:
        return False, None, "proceeding with a journaled assumption"

    raise AssertionError(
        f"no branch for ConfirmationType.{getattr(kind, 'name', kind)} — a new confirmation type "
        "must declare whether an unattended run stops for it rather than inheriting the last "
        "branch written, which is how a stop silently becomes a proceed"
    )


def unattended_interrupts(confirmations: list[ConfirmationRequest]) -> list[dict[str, Any]]:
    """Per confirmation, whether UNATTENDED would still stop for it.

    The counterfactual the offer surface was missing. `offer_autonomy` routinely recommends
    `per_stage` while still OFFERING `unattended` (every risk signal caps at `per_stage`), so the
    preview's `confirmations` list describes the stops for the RECOMMENDED mode only. A user
    weighing the upgrade is choosing precisely which of those stops to give up, and until this ran
    the preview never said which — making "run it unattended" a blind trade rather than the informed
    consent the rest of this module insists on.

    Advisory, and additive by construction: it reports on the confirmations the plan already raises
    and changes no verdict. Enforcement stays with the engine's gate policy.
    """
    out: list[dict[str, Any]] = []
    for request in confirmations:
        stop, which, reason = should_interrupt(mode=Mode.UNATTENDED, confirmation=request)
        out.append(
            {
                "request_id": request.request_id,
                "node_id": request.node_id,
                "interrupts": stop,
                # "" rather than None: an absent interrupt is "unattended proceeds here", and a
                # renderer that has to distinguish null from missing gets that wrong.
                "interrupt": which.value if which is not None else "",
                "reason": reason,
            }
        )
    return out


# ── earned trust ──


@dataclass
class TrustRecord:
    """A template's run history, as far as autonomy is concerned."""

    template: str
    clean_runs: int = 0
    failed_runs: int = 0
    #: The mode the user chose last time. Offered as the default next time — a user who picks
    #: per-stage three times running should not be asked to pick it a fourth.
    last_choice: Mode | None = None

    #: Clean runs before a template earns a stronger default. Small enough to be reachable, large
    #: enough that one lucky run does not grant it.
    PROMOTION_THRESHOLD = 3

    @property
    def earned(self) -> Mode | None:
        """The mode this template's history has earned, or None.

        A single failure resets it. Not decays — RESETS: a template that broke once is a template
        whose next run deserves eyes, and averaging that away is how earned trust becomes a
        rubber stamp.
        """
        if self.failed_runs:
            return None
        if self.clean_runs >= self.PROMOTION_THRESHOLD:
            return Mode.UNATTENDED
        if self.clean_runs >= 1:
            return Mode.PER_STAGE
        return None

    def to_dict(self) -> dict[str, Any]:
        earned = self.earned
        return {
            "template": self.template,
            "clean_runs": self.clean_runs,
            "failed_runs": self.failed_runs,
            "last_choice": self.last_choice.value if self.last_choice else None,
            "earned": earned.value if earned else None,
            "promotion_at": self.PROMOTION_THRESHOLD,
        }


def report_only_first(record: TrustRecord | None) -> bool:
    """Should a template's FIRST run be report-only?

    Yes, always. A template nobody has ever run is a template nobody has seen the output of, and
    "report only" is how a user learns what it does without the run doing it. The cost is one extra
    approval on first use; the alternative is discovering the behaviour by having it happen.
    """
    return record is None or (record.clean_runs == 0 and record.failed_runs == 0)


def commitment(
    *,
    mode: Mode,
    executor: str = "",
    environment: str = "local",
) -> dict[str, Any]:
    """The one combined commitment control: mode, executor, environment — stamped together.

    Three choices at one gate rather than three gates. They are stamped as ONE record because they
    only make sense together: unattended-in-a-sandbox and unattended-on-the-real-filesystem are
    different grants, and a user who approved the first has not approved the second.
    """
    return {
        "mode": mode.value,
        "executor": executor or "default",
        "environment": environment,
        "stamped": True,
    }
