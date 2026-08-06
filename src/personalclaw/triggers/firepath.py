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
    "spacing",
    "rate",
    "quiet",
    "duty",
    "budget",
    "claim",
    "slot",
    "active",
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
    # §1.3 maps "quiet-hours / debounce / cooldown / condition-false" to ONE outcome, so a
    # debounced fire is filterable beside a quiet-hours one rather than needing its own chip.
    "spacing": Outcome.SKIPPED_GATE.value,
    # §3.6 groups the hourly caps with the storm guards, and §1.3 gives a rate refusal the
    # same `skipped_gate` outcome as the other "should this fire at all" answers.
    "rate": Outcome.SKIPPED_GATE.value,
    "quiet": Outcome.SKIPPED_GATE.value,
    "duty": Outcome.SKIPPED_GATE.value,
    "budget": Outcome.SKIPPED_BUDGET.value,
    "claim": Outcome.SKIPPED_OVERLAP.value,
    # §3.5: "refuses over-capacity starts with a typed RESOURCE_BUSY … (a `deferred`
    # ledger row)". DEFERRED rather than a skip: the slot frees on its own, so this fire
    # is postponed by contention, not dropped by policy.
    "slot": Outcome.DEFERRED.value,
    # §3.5's `skip_if_active` liveness guard (WF2AUT-9). DEFERRED for the same reason as `slot`
    # and `yield`: the target that looks busy (a dirty worktree, a lock file, a just-touched
    # path) settles on its own, so the fire is postponed by state, not dropped by policy — it
    # re-evaluates next tick with no auto-retry storm.
    "active": Outcome.DEFERRED.value,
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
    #: Fires this trigger recorded in the last hour, or None when the ledger could not be read
    #: (S152). Supplied by `service.tick` from `ScheduleRunStore.count_since`. None is NOT zero: an
    #: unreadable ledger must not read as "no fires yet" and hand a runaway trigger a fresh
    #: allowance — the rate gate fails OPEN on None (storm-guard class) but says so in the reason.
    fires_in_window: int | None = None
    #: Seconds since this trigger last FIRED, or None when it has never fired (S151). Supplied by
    #: `service.tick` from `Trigger.last_fired_at` — a THIRD timestamp, because `last_success_at`
    #: and `last_failure_at` describe an OUTCOME and a SUPPRESSED fire is neither, so spacing off
    #: either would count a blocked fire as a fire.
    since_last_fire: float | None = None
    #: `(slot, holder_trigger_id)` when a named resource slot this fire needs is held by
    #: ANOTHER running trigger (§3.5). Supplied by `service.tick` from the claim store.
    busy_slot: tuple[str, str] = ("", "")
    #: The PRE-COMPUTED `skip_if_active` liveness signal (§3.5 / WF2AUT-9). True when the target the
    #: fire would act on looks busy — a dirty worktree, a present lock file, a just-modified path.
    #: Supplied by `service.tick` from `liveness.is_target_active`, gathered up front so the walk
    #: stays PURE: a gate that ran its own `git status` mid-walk would be the self-fetching I/O the
    #: module docstring forbids. `target_active_reason` names WHICH signal fired, for the row.
    target_active: bool = False
    target_active_reason: str = ""


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


def _rate_refusal(ctx: FireContext) -> str:
    """The hourly-cap refusal reason, or "" to allow. Never raises (S152).

    Delegates the DECISION to `missed.within_rate_window` rather than re-deriving it: that function
    already owns the manual-bypass asymmetry and the "no cap configured" case, and a second copy
    of a threshold comparison is how two surfaces start disagreeing about whether a trigger is
    capped.

    The lowest configured cap wins. `rate_cap`, `max_runs_per_hour` and `max_actions_per_hour` are
    three spellings a person may use, and taking the strictest is the only reading that cannot
    surprise: a user who set both 10/hour and 5/hour meant at most 5.

    **FAIL-OPEN when the ledger is unreadable** (`fires_in_window is None`) — §1.4's storm-guard
    class, and the same call `slot` makes about an unreadable claim store. But None is deliberately
    not folded into 0: zero fires would hand a runaway trigger a fresh allowance every time the
    ledger hiccuped, so the distinction is kept even though both currently allow.
    """
    gates = ctx.gates if isinstance(ctx.gates, dict) else {}
    caps: list[int] = []
    for key in ("rate_cap", "max_runs_per_hour", "max_actions_per_hour"):
        try:
            cap = int(gates.get(key) or 0)
        except (TypeError, ValueError):
            continue  # fail-open: a malformed cap is not a reason to suppress
        if cap > 0:
            caps.append(cap)
    if not caps:
        return ""
    if ctx.fires_in_window is None:
        return ""
    from personalclaw.triggers.missed import within_rate_window

    allowed, reason = within_rate_window(
        fires_in_window=int(ctx.fires_in_window), max_per_hour=min(caps)
    )
    return "" if allowed else reason


def _spacing_refusal(ctx: FireContext) -> str:
    """The debounce/cooldown refusal reason, or "" to allow. Never raises (S151).

    Both keys mean "do not fire again too soon", and they are DELIBERATELY kept as two rather than
    collapsed into one, because they answer different questions and a user sets them for different
    reasons:

    * `debounce_secs` — burst suppression. An event source that fires five times for one logical
      change (an editor saving twice, a webhook sender retrying) should produce one run.
    * `cooldown_secs` — a floor on cadence regardless of cause. "Never more than once an hour, even
      if something legitimately happens twice."

    Collapsing them to `max(a, b)` would compute the same number today and lose the author's intent
    the moment either grows its own semantics (a debounce that coalesces rather than drops). The
    reason names WHICH one refused, so the ledger row explains itself.

    **FAIL-OPEN on anything unreadable**, matching §1.4's storm-guard classification: an unparseable
    `debounce_secs` must not silence an automation. A trigger that has never fired
    (`since_last_fire is None`) is always allowed — it has nothing to space against, and treating an
    absent timestamp as "0 seconds ago" would block every trigger's first fire forever, which is the
    failure an S147-style default would have produced.
    """
    since = ctx.since_last_fire
    if since is None:
        return ""
    gates = ctx.gates if isinstance(ctx.gates, dict) else {}
    for key, label in (("debounce_secs", "debounce"), ("cooldown_secs", "cooldown")):
        try:
            window = float(gates.get(key) or 0)
        except (TypeError, ValueError):
            continue  # fail-open: a malformed guard is not a reason to suppress
        if window > 0 and since < window:
            left = int(window - since)
            return f"{label} of {int(window)}s has {left}s left " f"(last fired {int(since)}s ago)"
    return ""


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

    # ── 2. spacing: debounce + cooldown (§7's order is "debounce/quiet/cooldown/condition") ──
    #
    # 🔴 Both keys were declared in `GATE_KEYS` and read by NOTHING until S151 — S150 measured that
    # and put them in `UNMETERED_CAPS` because the meter they needed did not exist. It does now:
    # `Trigger.last_fired_at`, written beside `run_count` at the single fire-grant point.
    #
    # BEFORE the quiet/duty/budget gates, deliberately: spacing is the cheapest check on the path (a
    # float compare, no store read, no provider call), and §7 lists debounce first for that reason.
    # Paying for a duty-gate provider round-trip on a fire a debounce was going to drop anyway is
    # backwards.
    #
    # FAIL-OPEN on a malformed value, matching the storm-guard classification in §1.4: an
    # unparseable `debounce_secs` must not silence an automation. `_spacing_refusal` returns the
    # refusal reason, or "" to allow.
    spacing = _spacing_refusal(ctx)
    if spacing:
        return _refuse("spacing", spacing, passed)
    passed.append("spacing")

    # ── 3. the hourly rate caps (§3.6) ──
    #
    # 🔴 `rate_cap`, `max_runs_per_hour` and `max_actions_per_hour` were validated, carried, and
    # enforced by NOTHING — S133 named them, S150 put them in `UNMETERED_CAPS`, and the reason was
    # always the same: no windowed history query existed. `ScheduleRunStore.count_since` (S152) is
    # that query, and `missed.within_rate_window` has been the pure decision waiting for the number
    # since S65.
    #
    # Beside `spacing` because it answers the same question ("has this fired too much lately") over
    # the same cheap inputs, and before the provider-calling gates for the same cost reason.
    rate = _rate_refusal(ctx)
    if rate:
        return _refuse("rate", rate, passed)
    passed.append("rate")

    # ── 4. quiet windows ──
    from personalclaw.triggers.calendar import evaluate_quiet

    quiet, _issues = evaluate_quiet(ctx.gates, moment)
    if not quiet.allowed:
        return _refuse("quiet", quiet.reason or "inside a quiet window", passed)
    passed.append("quiet")

    # ── 5. the duty gate ──
    from personalclaw.triggers.calendar import evaluate_duty

    duty = await evaluate_duty(ctx.gates, moment)
    if not duty.allowed:
        return _refuse("duty", duty.reason or "the duty gate refused", passed)
    passed.append("duty")

    # ── 6. budget, BEFORE the claim, FAIL-CLOSED (§3.6) ──
    if not ctx.budget_readable:
        # §3.6 is explicit that the budget check is fail-closed. An unreadable budget is not an
        # unlimited one: treating an error as "allowed" is how a runaway trigger gets its allowance
        # from a transient store failure.
        return _refuse("budget", "budget could not be read; failing closed", passed)
    if ctx.budget_remaining is not None and ctx.budget_remaining <= 0:
        return _refuse("budget", "budget exhausted for this window", passed)
    passed.append("budget")

    # ── 7. the overlap claim lock (single-flight) ──
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

    # ── 5b. named resource slots (§3.5 / AUTO-R9 — S135) ──
    #
    # 🔴 `Trigger.resource_slots` was declared, persisted and round-tripped, and read by NOTHING —
    # found by generalising S134's container audit across all 41 dataclasses in `triggers/`. §3.5
    # requires the substrate to "serialize conflicting runs per slot and refuse over-capacity starts
    # with a typed RESOURCE_BUSY + holder identity". So three triggers declaring `["local-llm"]` all
    # ran a local model at once, which is the contention this exists to prevent on a machine shared
    # with the interactive user.
    #
    # AFTER the claim, deliberately: a slot is only contended by a fire that would
    # otherwise proceed,
    # and checking earlier would refuse a fire the overlap gate was about to skip anyway — two
    # reasons for one suppression, with the less useful one reported.
    slot, holder = ctx.busy_slot
    if slot:
        return _refuse(
            "slot",
            f"resource slot {slot!r} is busy (held by {holder}); deferred until it frees",
            passed,
            claim=claim,
        )
    passed.append("slot")

    # ── 5c. skip_if_active liveness guard (§3.5 / WF2AUT-9) ──
    #
    # §3.5 asks for an OPTIONAL guard on a mutating trigger "using cheap liveness heuristics (dirty
    # worktree, lockfiles, recent mtime) at fire time … a busy target defers rather than fires". The
    # signal is PRE-COMPUTED by the caller (`service.tick` → `liveness.is_target_active`) and only
    # honoured here, exactly as `busy_slot`/`user_active` are — a gate that ran its own `git status`
    # mid-walk would break the purity the module docstring depends on.
    #
    # AFTER the slot gate, deliberately: both are "the target isn't ready → DEFERRED", and this is
    # the same class of deferral as a contended slot (the resource is the working STATE, not a
    # named slot). The claim is threaded back on the decision (`claim=claim`) so the caller RELEASES
    # it — a deferred fire that kept the lock would block the very retry it is waiting for, the same
    # reason the `slot` and `yield` gates hand their claim back.
    if ctx.target_active:
        return _refuse(
            "active",
            ctx.target_active_reason or "the target is active; deferred until it settles",
            passed,
            claim=claim,
        )
    passed.append("active")

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
