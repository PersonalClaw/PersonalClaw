"""Autopause, parking, and quarantine — generalized (AUTOMATION-SUBSTRATE §3.7 — S68).

The plan is explicit that autopause-after-5 **already exists** for the cron action path
(`GatewayOrchestrator._maybe_autopause`) and that the substrate *generalizes* it rather than
inventing it. So the first thing this session did was measure what the shipped one counts.

**The measured defect.** `_maybe_autopause` is called from four sites and increments the same
counter at every one, with no notion of WHY the fire did not produce work. Driven directly, five
consecutive **denylist blocks** set `enabled = False`: a policy decision the operator configured on
purpose reads as five failures and silently disables the user's trigger. The other three sites are
equally undifferentiated — an unknown-provider config error that can never succeed takes five fires
to stop, exactly as long as a network blip that would have healed on its own.

That is R7's point, and it is why `TRUE_FAILURE_OUTCOMES` (S62) is a single-member set. The rules:

* **Only `FAILED` counts.** A trigger that skipped five times because quiet hours held is working
  as configured; autopausing it punishes the user for saying "not at night".
* **An outage PARKS, it does not autopause.** `auth_unavailable` / `transport_unavailable` are
  conditions of the world, not of the automation. Parking is reversible and self-healing; burning a
  failure budget on an expired token means the automation is still disabled after the user fixes it.
* **Injection screening QUARANTINES and never auto-retries.** Re-running a fire whose payload
  matched an injection pattern is the one case where retrying is itself the harm.
* **Config errors autopause IMMEDIATELY.** A fire that can never succeed — no such action provider
  — has nothing to wait for. Five attempts is four pointless fires and four rows of noise.

`FAILURE_BUDGET = 5` matches the shipped constant so migrated jobs keep the tolerance their authors
observed. Pure decisions over records; the service owns the writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from personalclaw.triggers.models import (
    TRUE_FAILURE_OUTCOMES,
    Outcome,
    TriggerHealth,
    TriggerState,
)

#: Consecutive true failures before a trigger autopauses. Matches the shipped
#: `GatewayOrchestrator._maybe_autopause` threshold so a migrated cron keeps the tolerance its
#: author actually observed — changing it during a migration would be a behaviour change disguised
#: as a port.
FAILURE_BUDGET = 5

#: How long a parked trigger waits before its next attempt, per episode. Deliberately a flat
#: cooldown rather than escalating backoff: §1.3 bans a row per attempt, and an escalating schedule
#: silently turns a 5-minute outage into an hour of not-running (the failure mode that made clawx
#: delete their 3-state breaker).
PARK_COOLDOWN_SECS = 300.0


class ExitType(str, Enum):
    """The typed run-exit taxonomy from §3.7.

    Separate from `Outcome` on purpose. `Outcome` says what happened to the FIRE; this says what the
    RUN exited as, and the mapping between them is the decision this module makes. Collapsing the
    two is how a transport outage ends up counted as a failure.
    """

    OK = "ok"
    #: Resumable — a cursor was persisted, so re-firing continues rather than restarting.
    PARTIAL = "partial"
    #: A credential is missing or expired. The world's problem, not the automation's.
    AUTH_UNAVAILABLE = "auth_unavailable"
    #: The network/provider is unreachable. Also the world's problem.
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    #: Misconfiguration that cannot succeed on retry (no such provider, unparseable spec).
    CONFIG_ERROR = "config_error"
    #: A genuine failure of work that could have succeeded.
    FAILED = "failed"


EXIT_TYPES: tuple[str, ...] = tuple(e.value for e in ExitType)

#: Exits that PARK rather than counting a failure. Both are conditions of the environment: a parked
#: trigger resumes on its own when the condition clears, while a burnt failure budget leaves the
#: automation disabled even after the user fixes the credential.
PARKING_EXITS: frozenset[str] = frozenset(
    {ExitType.AUTH_UNAVAILABLE.value, ExitType.TRANSPORT_UNAVAILABLE.value}
)

#: Exits that autopause IMMEDIATELY, without spending the budget. A fire that cannot succeed has
#: nothing to wait for; five attempts is four pointless fires and four rows of inbox noise.
IMMEDIATE_PAUSE_EXITS: frozenset[str] = frozenset({ExitType.CONFIG_ERROR.value})

#: Why each parking exit parked, in words a user can act on. A bare "parked" tells someone their
#: automation stopped without telling them what to fix.
PARK_REASONS: dict[str, str] = {
    ExitType.AUTH_UNAVAILABLE.value: "a credential this trigger needs is missing or expired",
    ExitType.TRANSPORT_UNAVAILABLE.value: "the service this trigger calls was unreachable",
}


def outcome_for_exit(exit_type: str) -> str:
    """The fire `Outcome` a typed run exit produces.

    The mapping that keeps the two vocabularies honest. A parking exit is `DEFERRED`, not `FAILED` —
    which is what stops it reaching `TRUE_FAILURE_OUTCOMES` and burning the budget. An unrecognized
    exit is `FAILED`: an exit type nobody classified is more likely a real failure than a benign
    one,
    and defaulting to benign would let a whole class of breakage fire forever unnoticed.
    """
    if exit_type in (ExitType.OK.value, ExitType.PARTIAL.value):
        return Outcome.RAN.value
    if exit_type in PARKING_EXITS:
        return Outcome.DEFERRED.value
    return Outcome.FAILED.value


#: Exception type NAMES that mean "the network was unreachable". Matched by name rather than by
#: `isinstance` so this module stays import-light and does not drag `aiohttp`/`httpx`/`botocore`
#: into every caller — and so a provider from an app bundle whose library is not installed here
#: still classifies correctly instead of falling through to FAILED.
_TRANSPORT_EXC_NAMES: frozenset[str] = frozenset(
    {
        "ClientConnectorError",
        "ClientOSError",
        "ConnectError",
        "ConnectionError",
        "ConnectionResetError",
        "EndpointConnectionError",
        "ReadTimeout",
        "ServerDisconnectedError",
        "TimeoutError",
        "TooManyRedirects",
    }
)

#: Substrings that mean "a credential is missing or expired". Lowercased substring matching is a
#: blunt instrument, but the alternative is worse: providers raise plain
#: `RuntimeError`/`ValueError` with the reason only in the message, so a type-only classifier
#: reports every expired token as a true failure and autopauses what the user is about to fix.
_AUTH_HINTS: tuple[str, ...] = (
    "401",
    "403",
    "access denied",
    "credential",
    "expired token",
    "forbidden",
    "invalid api key",
    "not authenticated",
    "not authorized",
    "unauthorized",
)

#: Substrings that mean "this can never succeed as configured".
_CONFIG_HINTS: tuple[str, ...] = (
    "invalid configuration",
    "missing required",
    "no such provider",
    "unknown action provider",
    "unsupported kind",
)


def classify_exception(exc: BaseException | None) -> str:
    """Map a raising provider onto the typed exit taxonomy.

    Order is auth → transport → config → failed, and auth is FIRST on purpose: an expired-credential
    error frequently arrives as an HTTP error whose type is a transport class, and the two get
    different treatment only in how they are explained to the user (both park). Reading it as
    transport would tell someone to check their network when the fix is to re-authenticate.

    An unrecognized exception is `FAILED`. That is the fail-safe direction: an unclassified error is
    more likely real breakage than a benign outage, and defaulting to a parking exit would let a
    genuinely broken automation retry forever without ever autopausing.
    """
    if exc is None:
        return ExitType.FAILED.value
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(hint in text for hint in _AUTH_HINTS):
        return ExitType.AUTH_UNAVAILABLE.value
    if type(exc).__name__ in _TRANSPORT_EXC_NAMES:
        return ExitType.TRANSPORT_UNAVAILABLE.value
    if any(hint in text for hint in _CONFIG_HINTS):
        return ExitType.CONFIG_ERROR.value
    return ExitType.FAILED.value


def counts_toward_autopause(outcome: str) -> bool:
    """Whether this outcome spends a unit of the failure budget.

    Delegates to S62's `TRUE_FAILURE_OUTCOMES` rather than re-listing: a second copy of the set is a
    second thing to forget when an outcome is added, and the failure direction is silent (a new
    outcome quietly stops counting, or quietly starts).
    """
    return outcome in TRUE_FAILURE_OUTCOMES


@dataclass
class PauseDecision:
    """What one fire outcome does to a trigger's lifecycle state.

    Carries the new state, the counter, and a reason. The reason is not decoration: the user is
    being told their automation stopped, and "autopaused" alone does not say what to fix.
    """

    state: str
    consecutive_failures: int
    health: str
    reason: str = ""
    #: When the trigger may next attempt a fire — set only for a park, 0.0 otherwise.
    retry_after: float = 0.0

    @property
    def fires_automatically(self) -> bool:
        """Whether the scheduler may still fire this trigger.

        Only `ACTIVE` fires. Read through this rather than comparing states at each call site,
        because checking `enabled` alone is exactly how an autopaused trigger keeps firing.
        """
        return self.state == TriggerState.ACTIVE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "health_status": self.health,
            "reason": self.reason,
            "retry_after": self.retry_after,
        }


def evaluate(
    *,
    exit_type: str,
    consecutive_failures: int,
    now: float = 0.0,
    budget: int = FAILURE_BUDGET,
    quarantined: bool = False,
) -> PauseDecision:
    """The lifecycle decision for one fire. Pure.

    Order matters, and each branch is ahead of the next for a reason:

    1. **Quarantine wins outright.** An injection-screened fire must never auto-retry, so nothing
       below may put the trigger back in a firing state.
    2. **A clean exit RESETS the counter.** Consecutive means consecutive: four failures then a
       success then one failure is not five. A counter that only ever climbed would eventually pause
       every long-lived trigger that ever had a bad week.
    3. **Parking exits park** without touching the counter, and stay reversible.
    4. **Config errors pause immediately** — retrying cannot help.
    5. **Everything else spends a unit** of the budget and pauses at the threshold.
    """
    if exit_type and exit_type not in EXIT_TYPES:
        # A caller's typo must not silently become `failed` — that is how a config error starts
        # taking 5 fires again, with nothing to notice it. Loud in dev, harmless in prod: the
        # fallthrough below still classifies it as a failure, which is the fail-safe direction.
        import logging

        logging.getLogger(__name__).warning(
            "unknown trigger exit type %r — treating as a true failure; expected one of %s",
            exit_type,
            ", ".join(EXIT_TYPES),
        )

    if quarantined:
        return PauseDecision(
            state=TriggerState.QUARANTINED.value,
            consecutive_failures=consecutive_failures,
            health=TriggerHealth.FAILING.value,
            reason="a fire's payload matched an injection pattern; "
            "quarantined runs never auto-retry",
        )

    if exit_type in (ExitType.OK.value, ExitType.PARTIAL.value):
        # Reset, and clear a PARK too: a successful fire is the proof the outage ended, so leaving a
        # parked state set would keep skipping a trigger that demonstrably works.
        return PauseDecision(
            state=TriggerState.ACTIVE.value,
            consecutive_failures=0,
            health=TriggerHealth.OK.value,
        )

    if exit_type in PARKING_EXITS:
        return PauseDecision(
            state=TriggerState.PARKED.value,
            # Untouched, NOT reset: an outage is neither progress nor failure. Resetting would let a
            # flapping credential clear a real failure streak on every other fire.
            consecutive_failures=consecutive_failures,
            health=TriggerHealth.PARKED.value,
            reason=PARK_REASONS.get(exit_type, "a resource this trigger needs was unavailable"),
            retry_after=now + PARK_COOLDOWN_SECS,
        )

    if exit_type in IMMEDIATE_PAUSE_EXITS:
        return PauseDecision(
            state=TriggerState.AUTOPAUSED.value,
            consecutive_failures=consecutive_failures + 1,
            health=TriggerHealth.FAILING.value,
            reason="this trigger is misconfigured and cannot succeed on retry; paused immediately "
            "rather than after 5 identical failures",
        )

    count = consecutive_failures + 1
    if count >= max(1, budget):
        return PauseDecision(
            state=TriggerState.AUTOPAUSED.value,
            consecutive_failures=count,
            health=TriggerHealth.FAILING.value,
            reason=f"paused after {count} consecutive failures",
        )
    return PauseDecision(
        state=TriggerState.ACTIVE.value,
        consecutive_failures=count,
        health=TriggerHealth.DEGRADED.value,
        reason=f"failure {count} of {max(1, budget)}",
    )


def consecutive_failures_from(runs: list[dict[str, Any]]) -> int:
    """Consecutive TRUE failures in a newest-first run list (§3.7 — S139).

    🔴 DERIVED, not stored, because `LEGACY_FIELD_MAP` says so outright: the legacy
    `consecutive_failures` column maps to *"failure_policy (autopause counter is derived from fire
    records)"*. A second copy on the trigger row would be a truth that can disagree with the ledger
    it summarises — the same reason `last_result` is deliberately dropped there.

    "Consecutive" means consecutive: the walk STOPS at the first clean exit, so four failures then a
    success then one failure counts as one. A counter that only ever climbed would eventually pause
    every long-lived automation that once had a bad week.

    Outcomes that neither fail nor succeed — a skipped or deferred fire — are SKIPPED rather than
    treated as either. A quiet-hours window is not a recovery (it would silently forgive a real
    failure streak) and not a failure (it would pause a healthy trigger for being configured).
    """
    from personalclaw.triggers.models import Outcome

    count = 0
    for run in runs:
        # `status == "failure"` check gets wrong: an outage is stored as a failure too, so
        # `status` is the run store's older vocabulary ("success"/"failure"/"timeout"/"launched").
        # The typed field wins when present, because it distinguishes a true failure from an outage.
        exit_type = str(run.get("trigger") or run.get("outcome") or "")
        status = str(run.get("status") or "")

        if exit_type in {ExitType.OK.value, Outcome.RAN.value, Outcome.RAN_LATE.value}:
            break
        if not exit_type and status in {"success", "ok"}:
            break

        # 🔴 A PARKING exit does NOT spend the budget, and this is the half a naive
        # `status == "failure"` check gets wrong: an outage is stored as `status: "failure"` too, so
        # counting by status would pause a trigger for a network blip — exactly what criterion 3's
        # "auth/transport outages park instead" forbids. Caught by driving six outages followed by
        # four real failures and watching it pause early.
        if exit_type in PARKING_EXITS:
            continue
        if exit_type:
            if counts_toward_autopause(exit_type) or exit_type == ExitType.FAILED.value:
                count += 1
            continue
        # No typed exit (a legacy row): fall back to the status vocabulary.
        if status in {"failure", "timeout", "error"}:
            count += 1
    return count


def unpark_due(*, retry_after: float, now: float) -> bool:
    """Whether a parked trigger's cooldown has elapsed.

    Split from `evaluate` because unparking is driven by the CLOCK, not by a fire outcome — a parked
    trigger produces no fires to evaluate, so nothing would ever bring it back if the transition
    lived in the outcome path. A missing/zero `retry_after` reads as due, so a park written before
    this field existed cannot strand a trigger forever.
    """
    if retry_after <= 0:
        return True
    return now >= retry_after


def resume_state(state: str) -> tuple[str, str]:
    """What a user's explicit Resume does. Returns `(new_state, refusal)`.

    A quarantined trigger is NOT resumable from a button. Quarantine means a payload matched an
    injection pattern, and one click is too cheap a gesture for "run the thing that looked like an
    attack" — that needs an explicit re-authoring of the trigger, which is a different action with a
    different confirmation. Everything else resumes and gets a clean counter, because a user
    pressing Resume has decided the cause is addressed.
    """
    if state == TriggerState.QUARANTINED.value:
        return state, (
            "a quarantined trigger cannot be resumed from here — review the matched payload and "
            "re-author the trigger"
        )
    if state == TriggerState.RETIRED.value:
        return state, "a retired trigger cannot be resumed; duplicate it instead"
    return TriggerState.ACTIVE.value, ""


def needs_attention(state: str) -> bool:
    """Whether this state belongs in the Runs inbox as something the user must act on.

    `PARKED` is deliberately EXCLUDED. A park self-heals, and putting one in the attention list
    means a five-minute outage generates a card the user cannot usefully act on — training them to
    dismiss the surface that is supposed to carry the real ones.
    """
    return state in {TriggerState.AUTOPAUSED.value, TriggerState.QUARANTINED.value}


# ── Runs-inbox surfacing (§3.7's second half) ──

#: Deep-link ref key for the trigger an inbox card is about. `InboxItem.refs` is free-form by design
#: (S51), so a structured payload rides there rather than widening the inbox schema, which is shared
#: with channel messages.
TRIGGER_REF = "trigger"


def inbox_fingerprint(trigger_id: str, state: str) -> str:
    """The dedup key for one attention episode.

    Keyed on `(trigger, state)` and NOT on the fire, deliberately. An autopaused trigger stops
    firing, so per-fire keying would produce exactly one card — but a trigger that autopauses, gets
    resumed, and autopauses again is a SECOND episode the user must see. Re-keying on the state
    transition gives one card per episode: no spam while paused, a new card when it re-enters.
    """
    return f"trigger-attention:{trigger_id}:{state}"


@dataclass
class AttentionCard:
    """What the Runs inbox shows for a trigger that stopped on its own.

    Built as a record rather than written here: the store belongs to the service, and keeping this
    pure means the copy and the dedup rule are testable without an inbox on disk.
    """

    trigger_id: str
    trigger_name: str
    state: str
    title: str
    body: str
    fingerprint: str
    actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "trigger_name": self.trigger_name,
            "state": self.state,
            "title": self.title,
            "body": self.body,
            "fingerprint": self.fingerprint,
            "actions": list(self.actions),
        }


def attention_card(
    *,
    trigger_id: str,
    trigger_name: str,
    decision: PauseDecision,
    last_error: str = "",
) -> AttentionCard | None:
    """The inbox card for a trigger that stopped itself, or None when none is warranted.

    Returns None rather than an empty card for a still-firing or parked trigger, so the caller's
    control flow is "if card: write it" and there is no way to write a card that says nothing.

    The offered ACTIONS differ by state, and that difference is the point: a quarantined trigger
    gets no Resume, because `resume_state` refuses it and offering a button that returns a refusal
    is worse than not offering it. `last_error` rides the body because "paused after 5 consecutive
    failures" without the error is an alert the user has to go digging to act on.
    """
    if not needs_attention(decision.state):
        return None

    if decision.state == TriggerState.QUARANTINED.value:
        title = f"{trigger_name or trigger_id} was quarantined"
        actions: tuple[str, ...] = ("review", "delete")
    else:
        title = f"{trigger_name or trigger_id} paused itself"
        actions = ("resume", "edit", "delete")

    body = decision.reason or f"the trigger entered {decision.state}"
    if last_error:
        body = f"{body}. Last error: {last_error}"
    return AttentionCard(
        trigger_id=trigger_id,
        trigger_name=trigger_name,
        state=decision.state,
        title=title,
        body=body,
        fingerprint=inbox_fingerprint(trigger_id, decision.state),
        actions=actions,
    )


def is_duplicate_card(fingerprint: str, existing: set[str] | frozenset[str]) -> bool:
    """Whether this episode already has a card.

    A set membership test, exposed as a function so the rule lives with the fingerprint that defines
    it — the failure it prevents (one card per fire on a trigger that keeps failing) is invisible at
    a call site that just does `in`.
    """
    return fingerprint in existing
