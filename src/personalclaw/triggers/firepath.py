"""The fire path: the ordered composition of every gate a fire passes (§3 — S86).

§3 states the order and says "order matters":

    injection screen → gates (debounce/quiet/cooldown/condition) → budget check pre-claim
    (fail-closed) → overlap claim lock → yield/resource-slot check → fence payload →
    capability filter → resolve def / resume target → create run → engine executes →
    outcome classification → delivery contract → health rollup + failure policy

**Measured before writing — fifteen modules, and nothing composes them.** A grep for live
callers of `claim_fire`, `boot_recovery`, `spool_fire`, `drain_spool`, `freeze_capabilities`,
`evaluate_quiet`, `evaluate_duty`, `needs_attention`, `resolve_missed`, `changed_files` and
`build_delivery` outside their own modules returns **NONE** for every one. There is no
`triggers/service.py`. Sessions S62-S85 each built a control and each recorded "NOT DONE (by
scope): the service" — eight such notes in the plan's execution log — and no queue row ever
owned it.

So the controls are individually correct and collectively unreachable. That is the same defect
class this program keeps finding ("present and inert"), at the scale of a whole subsystem.

**What this module is, and is not.**

It is the ORDER, as a pure function: `evaluate(...)` walks the gates in §3's sequence and
returns the first suppression with its typed outcome and reason, or `ALLOWED`. Every gate call
is real — the module imports the shipped decision functions rather than reimplementing their
logic.

It is NOT the loop, the store, or the executor. Those need `triggers.json` (S83's recorded
blocker) and the WakeupDispatcher, and building them against a store that does not exist is what
EXECUTION-PROTOCOL forbids. What a future service session needs is exactly this: a tested
ordering it can call per fire, instead of re-deriving a 13-step sequence from prose under time
pressure and getting the fail-closed budget check on the wrong side of the claim lock.

**Why the order is load-bearing, at the three places it actually bites:**

* **Screen BEFORE gates.** A payload carrying an injection must be refused on content, not on
  timing — otherwise a quiet-hours window silently "protects" the machine and the same payload
  lands at 08:00.
* **Budget BEFORE the claim lock, fail-closed.** Claiming first means a budget-exhausted
  trigger holds a
  lock it will never use, and single-flight then blocks the NEXT legitimate fire. §3.6 says
  fail-closed: an unreadable budget refuses.
* **Capability filter BEFORE resolving the def.** Resolving first means the run exists (and may have
  written its first ledger row) before anyone checks whether the action was permitted at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from personalclaw.triggers.models import Outcome

logger = logging.getLogger(__name__)

#: The gate names, in §3's order. A LIST rather than prose because the order IS the contract: a test
#: walks it against `evaluate`'s own sequence, so a future edit that reorders the fire path
#: fails a test
#: instead of silently moving the budget check to the wrong side of the claim lock.
GATE_ORDER: tuple[str, ...] = (
    "incident",
    "screen",
    "quiet",
    "duty",
    "budget",
    "claim",
    "yield",
    "capability",
)

#: Which gate produces which typed outcome. Every value is a `FIRE_OUTCOMES` member — a
#: suppression with
#: an outcome outside the vocabulary would be unfilterable in the runs inbox, which is what
#: §1.3's typed
#: outcomes exist to prevent.
GATE_OUTCOMES: dict[str, str] = {
    "incident": Outcome.REFUSED.value,
    "screen": Outcome.BLOCKED_INJECTION.value,
    "quiet": Outcome.SKIPPED_GATE.value,
    "duty": Outcome.SKIPPED_GATE.value,
    "budget": Outcome.SKIPPED_BUDGET.value,
    "claim": Outcome.SKIPPED_OVERLAP.value,
    "yield": Outcome.DEFERRED.value,
    "capability": Outcome.REFUSED.value,
}


@dataclass
class FireContext:
    """Everything the gates need to decide, gathered before any of them runs.

    Gathered up front so the walk is PURE: a gate that fetched its own inputs could observe a
    different world than the gate before it, and "the budget was fine when we checked" is
    exactly the race fail-closed budgeting exists to avoid.
    """

    trigger_id: str
    #: The untrusted text a payload carries — an inbox item body, a webhook field, a file's
    #: name. "" for
    #: a clock trigger, which has no external content.
    payload_text: str = ""
    gates: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] | None = None
    #: The actions this fire wants, `{key: [values]}` — checked against the FROZEN capability set.
    requested: dict[str, list[str]] = field(default_factory=dict)
    moment: datetime | None = None
    #: None means "no budget configured"; a number is the remaining allowance. A budget that
    #: could not be
    #: READ must be passed as `budget_readable=False` so the gate fails closed rather than
    #: treating an
    #: error as unlimited.
    budget_remaining: float | None = None
    budget_readable: bool = True
    #: The existing claim, if any — `scheduling.claim_fire`'s own input shape.
    existing_claim: Any = None
    holder: str = ""
    overlap: str = "skip"
    now: float = 0.0
    #: True when the user is interacting and this fire should yield (§3.5). The service
    #: supplies it; this
    #: module only honours it.
    user_active: bool = False
    yield_to_user: bool = False


@dataclass
class FireDecision:
    """The fire path's verdict: allowed, or the first gate that refused and why."""

    allowed: bool
    outcome: str = Outcome.RAN.value
    gate: str = ""
    reason: str = ""
    #: The claim `claim_fire` granted, when the walk got that far. The caller must release it.
    claim: Any = None
    #: Gates that ran and passed, in order — so a ledger row can say how far a suppressed fire got.
    #: "Suppressed at `budget`" and "suppressed at `screen`" are different incidents.
    passed: list[str] = field(default_factory=list)
    #: Capability violations, when the capability gate refused. Named so the row says WHICH
    #: action was
    #: outside the frozen set rather than just that one was.
    violations: list[tuple[str, str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "outcome": self.outcome,
            "gate": self.gate,
            "reason": self.reason,
            "passed": list(self.passed),
            "violations": [list(v) for v in self.violations],
        }


def _refuse(gate: str, reason: str, passed: list[str], **extra: Any) -> FireDecision:
    return FireDecision(
        allowed=False,
        outcome=GATE_OUTCOMES[gate],
        gate=gate,
        reason=reason,
        passed=list(passed),
        **extra,
    )


async def evaluate(ctx: FireContext) -> FireDecision:
    """Walk §3's gate order. Returns the FIRST refusal, or an allowed decision.

    ASYNC because the duty gate is. Measured while driving this: `calendar.evaluate_duty` is a
    coroutine — §1.4 makes it provider-backed and time-boxed (a third-party calendar app answers
    it), and a sync fire path silently got a coroutine object whose `.allowed` was always
    truthy. Every duty gate would have passed, including one that meant to refuse. The other six
    gates are pure and stay sync; only the walk is awaited.

    First-refusal rather than collect-all, deliberately: the outcome vocabulary has one slot per
    fire, and a row reporting three simultaneous reasons would leave the user guessing which to
    fix. The `passed` list preserves how far the fire got, which is the part that is genuinely
    useful.
    """
    passed: list[str] = []

    # ── 0. THE KILL SWITCH (decision 7's "global manual kill switch") ──
    #
    # 🔴 MEASURED: `personalclaw incident on` did NOT stop a clock trigger. The CLI calls it
    # "Suspend/resume all unattended work" and the legacy `event_triggers` path refuses on it, but
    # the unified engine — the sole path that fires clock triggers since S100 — never read the flag.
    # Driven before writing this: switch thrown, `tick()` still returned `fires: ['clock:nightly']`
    # with `outcome=ran`. So the one control an operator reaches for DURING an incident was the one
    # that kept running unattended work, while reporting itself active.
    #
    # FIRST, ahead of the injection screen, because an incident halts everything unconditionally:
    # a gate ordered after `screen` would make "is the payload clean" a precondition for honouring
    # a kill switch. And here rather than in each loop, because there are three unattended entry
    # points (the clock loop, the file-watch poll, the reaper's re-dispatch) and only the file-watch
    # one checked — a per-loop check is a control that must be re-added correctly at every future
    # call site, which is precisely how this gap opened.
    #
    # `incident_active()` is itself fail-OPEN by deliberate design (see `guardrails/incident.py`:
    # an unreadable flag file must not halt all automation on a filesystem hiccup), so this gate
    # inherits that and does not second-guess it.
    from personalclaw.guardrails.incident import incident_active

    if incident_active():
        return _refuse(
            "incident",
            "incident mode is active: unattended fires are suspended "
            "(resume with `personalclaw incident off`)",
            passed,
        )
    passed.append("incident")

    # ── 1. injection screen, on CONTENT, before anything about timing ──
    if ctx.payload_text:
        from personalclaw.triggers.screen import screen

        verdict = screen(ctx.payload_text)
        if getattr(verdict, "verdict", "") == "blocked":
            groups = ", ".join(getattr(verdict, "groups", ()) or ()) or "injection"
            return _refuse("screen", f"payload blocked by the injection screen ({groups})", passed)
    passed.append("screen")

    moment = ctx.moment or datetime.now()

    # ── 2. quiet windows ──
    from personalclaw.triggers.calendar import evaluate_quiet

    quiet, _issues = evaluate_quiet(ctx.gates, moment)
    if not quiet.allowed:
        return _refuse("quiet", quiet.reason or "inside a quiet window", passed)
    passed.append("quiet")

    # ── 3. the duty gate ──
    from personalclaw.triggers.calendar import evaluate_duty

    duty = await evaluate_duty(ctx.gates, moment)
    if not duty.allowed:
        return _refuse("duty", duty.reason or "the duty gate refused", passed)
    passed.append("duty")

    # ── 4. budget, BEFORE the claim, FAIL-CLOSED (§3.6) ──
    if not ctx.budget_readable:
        # §3.6 is explicit that the budget check is fail-closed. An unreadable budget is not an
        # unlimited one: treating an error as "allowed" is how a runaway trigger gets its allowance
        # from a transient store failure.
        return _refuse("budget", "budget could not be read; failing closed", passed)
    if ctx.budget_remaining is not None and ctx.budget_remaining <= 0:
        return _refuse("budget", "budget exhausted for this window", passed)
    passed.append("budget")

    # ── 5. the overlap claim lock (single-flight) ──
    from personalclaw.triggers.scheduling import claim_fire

    claim, claim_reason = claim_fire(
        ctx.existing_claim,
        trigger_id=ctx.trigger_id,
        holder=ctx.holder or "firepath",
        now=ctx.now,
        overlap=ctx.overlap,
    )
    if claim is None:
        return _refuse("claim", claim_reason or "another fire holds the claim", passed)
    passed.append("claim")

    # ── 6. foreground yield / resource slots (§3.5) ──
    if ctx.yield_to_user and ctx.user_active:
        # The claim is RELEASED by returning it on the decision — a deferred fire that kept the
        # lock would block the retry it is waiting for. Reported as `deferred`, not `skipped_*`:
        # the fire is coming back.
        return _refuse(
            "yield",
            "yielding to foreground user activity",
            passed,
            claim=claim,
        )
    passed.append("yield")

    # ── 7. capability filter, against the FROZEN set, before any def resolves ──
    #
    # 🔴 `ctx.requested` was NEVER POPULATED in production (S116). The only real construction
    # (`service.tick`) omitted it, so this branch was always false and the frozen-capability fence
    # had never run on a real fire — it passed its own unit tests, which supply `requested` by hand.
    # Same shape as S97's `existing_claim`: a gate whose input nobody supplied.
    if ctx.requested:
        from personalclaw.triggers.screen import provider_is_read_only, unfenced_actions

        # DECISION 7's READ-ONLY DEFAULT. "Auto-fired triggers default to read-only action
        # providers; write-capable actions require explicit opt-in." So a request for a read-only
        # provider is permitted with no `capabilities` block at all, and only write-capable actions
        # are held to the frozen set.
        #
        # This is what makes the fence landable: NO writer sets `capabilities` (measured across
        # `tools.py`, `app_crons`, the digest reconciler, the CLI and the API), and the fence denies
        # on an empty set — so enforcing it without the default would refuse every automation in
        # existence. Deny-by-default stays where it matters: an unclassified provider reads as
        # write-capable, so a new action still needs the opt-in.
        needs_fence = {
            key: [v for v in values if not (key == "providers" and provider_is_read_only(v))]
            for key, values in ctx.requested.items()
        }
        needs_fence = {k: v for k, v in needs_fence.items() if v}
        violations = (
            unfenced_actions(ctx.capabilities, requested=needs_fence) if needs_fence else []
        )
        if violations:
            named = ", ".join(f"{k}={v}" for k, v, _ in violations[:3])
            return _refuse(
                "capability",
                f"action outside the frozen capability set: {named}",
                passed,
                claim=claim,
                violations=violations,
            )
    passed.append("capability")

    return FireDecision(
        allowed=True, outcome=Outcome.RAN.value, claim=claim, passed=passed, reason=""
    )


def ledger_row(decision: FireDecision, ctx: FireContext) -> dict[str, Any]:
    """The typed ledger row for one fire attempt — allowed or not.

    §7 criterion 8: "every suppressed fire appears as a typed ledger row with a reason — zero
    silent drops". So this is written for EVERY outcome, which is why it takes the decision
    rather than only being called on the failure path: a helper that existed only for refusals
    would make "we forgot to log the successes" the next defect.
    """
    return {
        "trigger_id": ctx.trigger_id,
        "outcome": decision.outcome,
        "reason": decision.reason,
        "gate": decision.gate,
        "gates_passed": list(decision.passed),
        "violations": [list(v) for v in decision.violations],
    }


def suppressed_at(decision: FireDecision) -> str:
    """Which gate stopped this fire, or "" when it was allowed.

    A named accessor because "suppressed at `budget`" and "suppressed at `screen`" are different
    incidents with different fixes, and a caller reading `decision.gate` directly would have to know
    that an allowed decision leaves it empty.
    """
    return "" if decision.allowed else decision.gate


def gate_order_is_intact(order: tuple[str, ...] = GATE_ORDER) -> list[str]:
    """Structural check: every declared gate has an outcome, and vice versa. Returns the gaps.

    A gate added to the walk without an entry in `GATE_OUTCOMES` would raise `KeyError` mid-fire
    — at which point the fire is lost rather than refused, which is the silent drop §7 criterion
    8 bans. This turns that into a checkable fact.
    """
    gaps: list[str] = []
    for gate in order:
        if gate not in GATE_OUTCOMES:
            gaps.append(f"{gate}: declared in the order with no typed outcome")
    for gate in GATE_OUTCOMES:
        if gate not in order:
            gaps.append(f"{gate}: has an outcome but is not in the order")
    return gaps
