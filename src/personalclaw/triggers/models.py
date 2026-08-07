"""The Trigger entity, its per-kind specs, and the fire records (AUTOMATION-SUBSTRATE §1 — S62).

One record replaces three stores. The whole value is that a person edits ONE form and reads ONE
history, so the shape has to hold everything `crons.json`, `event_triggers.json` and the hook/
autonudge configs carry — measured field by field before this was written, and asserted per field
by the tests rather than by inspection. A migration that silently dropped `skip_dates` would keep
firing on a holiday and the user would never learn why.

Two properties the plan calls out that shape everything here:

* **Never-throw structural validation (R15).** A trigger authored by an agent with a near-miss field
  name must become a WARNING chip, not a silently-dead row. `parse_trigger` therefore returns
  `(trigger, issues)` and never raises — the trigger still loads, disabled if it must be, and the
  issue says which key it did not recognize and what the closest known one is.
* **Silent drops are banned (R2).** Every suppressed or degenerate fire is a ledger row with a typed
  outcome and a one-line reason. `Outcome` is that vocabulary, and it is closed: a fire that ends in
  none of these is a fire nobody can account for.

Pure records and decisions. No scheduling, no I/O, no firing — those are sessions 63/64.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── the closed vocabularies ──

#: Trigger kinds. `pulse`/`observe` are the plan's Phase 2 and are NOT accepted yet: a kind the
#: service cannot dispatch would let a user author a trigger that never fires, which is the failure
#: the never-throw validation exists to make impossible.
KINDS: tuple[str, ...] = (
    "clock",
    "event",
    "run_completed",
    "idle",
    "file",
    "webhook",
    "view",
    "web_watch",
    "manual",
)


class TriggerState(str, Enum):
    """Where a trigger is in its lifecycle.

    `autopaused` is separate from `paused` because the two answer different questions: a paused
    trigger is a user decision, an autopaused one is the system reporting five
    true failures. Showing
    both as "paused" would make the user look for a switch they never flipped.

    `parked` is not a failure — it is "the resource this needs is busy", which resolves on its own.
    `quarantined` is the injection-screen outcome and must never auto-retry.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    AUTOPAUSED = "autopaused"
    PARKED = "parked"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class TriggerHealth(str, Enum):
    """The rollup a list renders without scanning run history.

    Persisted on the row deliberately (R7): computing it per render means reading every run of every
    trigger to draw one page of status dots.
    """

    OK = "ok"
    DEGRADED = "degraded"
    PARKED = "parked"
    FAILING = "failing"


class RunWeight(str, Enum):
    """Two weights, because trigger history and run state are different concerns.

    A single-action fire that mutated nothing does not earn a run directory and a journal; a
    multi-node LLM workflow does. Giving everything the heavy shape is what produces 1440 run dirs a
    day from a minutely trigger.
    """

    LEDGER = "ledger"
    FULL = "full"


class Outcome(str, Enum):
    """Typed fire outcomes. The vocabulary that makes "silent drops are banned" checkable.

    Every member is a REASON a fire did not produce work, or the one way it did. A surface switches
    on these; prose would make the runs inbox unfilterable, and S54 already paid for prose-matched
    reasons (a message containing "secret" was matched as if it were one).
    """

    #: It ran and did something durable.
    RAN = "ran"
    #: It ran, later than scheduled — `scheduled_for` is recorded alongside `started_at`.
    RAN_LATE = "ran_late"
    #: The overlap claim lock was held by a run already in flight.
    SKIPPED_OVERLAP = "skipped_overlap"
    #: A cost/action cap was breached BEFORE the claim, so nothing was spent.
    SKIPPED_BUDGET = "skipped_budget"
    #: quiet-hours / debounce / cooldown / condition-false.
    SKIPPED_GATE = "skipped_gate"
    #: It ran and mutated nothing durable — collapses to a ledger row and auto-archives.
    SKIPPED_NOOP = "skipped_noop"
    #: A triage stage said ignore. Carries the rationale: an unexplained skip is indistinguishable
    #: from a bug.
    SKIPPED_TRIAGE = "skipped_triage"
    #: The user dismissed a missed-fire card.
    SKIPPED_MISSED = "skipped_missed"
    #: Parked / yielded / resource-busy. ONE row per episode, not per attempt — escalating backoff
    #: would otherwise write a row a second.
    DEFERRED = "deferred"
    #: A policy refusal. Distinct from failed and from skipped, with a mandatory human-readable
    #: reason posted back to the triggering surface.
    REFUSED = "refused"
    #: A pre-LLM injection-screen match. NEVER auto-retried, and it names the matched pattern.
    BLOCKED_INJECTION = "blocked_injection"
    #: A genuine failure — the only outcome that counts toward autopause.
    FAILED = "failed"


FIRE_OUTCOMES: tuple[str, ...] = tuple(o.value for o in Outcome)

#: Outcomes that count toward the autopause-after-5 rule (R7). Deliberately just `FAILED`: a trigger
#: that skipped five times because its quiet-hours gate held is working exactly as configured, and
#: autopausing it would punish the user for saying "not at night".
TRUE_FAILURE_OUTCOMES: frozenset[str] = frozenset({Outcome.FAILED.value})

#: Outcomes that mean "nothing was spent and nothing changed". These collapse to ledger rows and
#: archive out of the default inbox view — the runs inbox is for what the machine DID.
INERT_OUTCOMES: frozenset[str] = frozenset(
    {
        Outcome.SKIPPED_OVERLAP.value,
        Outcome.SKIPPED_BUDGET.value,
        Outcome.SKIPPED_GATE.value,
        Outcome.SKIPPED_NOOP.value,
        Outcome.SKIPPED_TRIAGE.value,
        Outcome.SKIPPED_MISSED.value,
    }
)


# ── structural issues ──


@dataclass
class Issue:
    """One structural problem with an authored trigger.

    `closest` is the point of the record: an agent that wrote `debounce_seconds` for
    `debounce_secs` should be told which key it meant, not that its trigger is invalid. A validation
    error with no suggestion is how a near-miss becomes a dead row nobody diagnoses.
    """

    path: str
    message: str
    severity: str = "warning"
    closest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
            "closest": self.closest,
        }


def _closest(name: str, known: tuple[str, ...] | frozenset[str]) -> str:
    """The nearest known key, or "" when nothing is close enough.

    A cutoff rather than always returning the best match: suggesting `timezone` for `xyzzy` is worse
    than suggesting nothing, because the reader trusts the suggestion and goes looking for a
    relationship that is not there.
    """
    matches = difflib.get_close_matches(name, list(known), n=1, cutoff=0.7)
    return matches[0] if matches else ""


# ── per-kind specs ──

#: The keys each kind's `spec` may carry. Closed per kind, because an unrecognized key in a spec is
#: the single most likely authoring mistake and the one with the quietest failure
#: — the trigger loads,
#: the service ignores the key, and the automation behaves in a way its author cannot explain.
SPEC_KEYS: dict[str, frozenset[str]] = {
    # Everything `schedule.py` carries today, verbatim. `jitter_secs` and `strict` are named here
    # rather than left to the service so the migration has somewhere to put them (§1.2).
    "clock": frozenset(
        {
            "kind",
            "expr",
            "at",
            # The `interval` clock kind's payload (S87). Paired with `CLOCK_KINDS`' fourth member:
            # without this key a migrated `every` cron parses its own spec as an unknown field and
            # warns on the very number that defines when it fires.
            "interval_secs",
            "timezone",
            "jitter_secs",
            "strict",
            "skip_dates",
            "delete_after_run",
        }
    ),
    "event": frozenset({"source", "pattern", "blocking", "agent_scope"}),
    "run_completed": frozenset({"source_trigger", "source_def"}),
    "idle": frozenset({"scope", "idle_secs", "first_idle_secs"}),
    "file": frozenset({"paths", "dedup"}),
    "webhook": frozenset({"token_ref"}),
    "view": frozenset({"surface_binding", "ttl_secs"}),
    "web_watch": frozenset(
        {
            "url",
            "poll_interval",
            "extraction",
            "novelty_key",
            # The opt-in headless-Chromium escalation tier (WF2AUT-7). `escalate_headless` turns it
            # on (default OFF, so an existing watch is byte-unchanged); `max_headless_requests`
            # bounds the expensive render tier with its own daily budget.
            "escalate_headless",
            "max_headless_requests",
        }
    ),
    "manual": frozenset(),
}

#: The `clock` spec's tagged-union discriminator values (§1.2).
#:
#: **DEVIATION from §1.2's literal three-member union: `interval` is the fourth (S87).** Found by
#: driving the migration into the store: `migrate.convert_job` emits `{kind: "interval",
#: interval_secs}` for a legacy `every` cron — deliberately, and its docstring explains why at
#: length ("`{kind: cron}` is WRONG and `{kind: at}` is worse … would turn every recurring interval
#: job into a one-shot that fires once and dies — the single most destructive possible
#: mistranslation in this file"). But `CLOCK_KINDS` never gained the member, so every migrated
#: interval cron parsed with `unknown clock kind 'interval'`, landed `enabled=False`, and would have
#: been silently retired by the migration that was supposed to preserve it.
#:
#: Measured against the OWNER's real store: 4 jobs, of which 1 uses `every`. The plan's §1.2 union
#: and its §6 lossless-migration promise cannot both hold with three kinds, and the promise is the
#: one with data behind it. So the union widens rather than the migration lying.
CLOCK_KINDS: frozenset[str] = frozenset({"cron", "at", "sequence", "interval"})

#: Minimum interval for an LLM-invoking clock trigger, in seconds (R1). A floor rather than a hard
#: rule: the plan makes it overridable, because a 5-minute local-model poll is a legitimate choice —
#: it just should not be the accident you get from typing `* * * * *`.
MIN_CLOCK_INTERVAL_SECS = 900


def _agent_scope_issues(spec: dict[str, Any] | None) -> list[Issue]:
    """Structural issues in an `event` trigger's `agent_scope` (§1.4 decision 2 — S131).

    🔴 MEASURED: `agent_scope` was declared in `SPEC_KEYS["event"]`, persisted, round-tripped —
    and validated by nothing. Every one of these stored with `ok: True` and zero issues:

        agent_scope="not-a-list"        # a bare string
        agent_scope=[]                  # an empty list
        agent_scope=[123]               # non-string entries
        agent_scope=["nonexistent"]     # an agent that does not exist

    Decision 2's recon note is explicit that the substrate "PRESERVES agent scoping as an optional
    `spec.agent_scope` and does not silently introduce a global chat firing path". A field that
    accepts any shape and is read by nothing does not preserve scoping — it *promises* it. That is
    worse than its absence, because an author who sets it believes their trigger is scoped.

    Structure only, matching `validate_spec`'s own contract: whether the named agent EXISTS is a
    semantic question the config layer answers, and rejecting an agent id at author time would
    refuse a trigger that becomes valid the moment the agent is installed. What is checked is the
    shape a reader must be able to rely on.

    An EMPTY list is an error rather than a warning, deliberately. In the legacy path an empty id
    list means `fire_for_ids` fires NOTHING (its resolver returns `[]` on failure precisely so a
    broken lookup cannot fall back to global firing). So `agent_scope: []` is an automation that can
    never fire — silently, forever — which is exactly the inert row the never-throw validation
    exists to make visible.
    """
    raw = (spec or {}).get("agent_scope")
    if raw is None:
        return []
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        return [
            Issue(
                path="spec.agent_scope",
                message="agent_scope must be a list of agent ids; a "
                f"{type(raw).__name__} is refused rather than coerced, because a scope that "
                "silently read as one agent would fence the wrong thing",
                severity="error",
            )
        ]
    if not raw:
        return [
            Issue(
                path="spec.agent_scope",
                message="agent_scope is empty, so this trigger can never fire for any agent — "
                "remove the key to leave it unscoped, or name the agents it belongs to",
                severity="error",
            )
        ]
    bad = [entry for entry in raw if not isinstance(entry, str) or not entry.strip()]
    if bad:
        return [
            Issue(
                path="spec.agent_scope",
                message=f"agent_scope entries must be non-empty agent ids; got {bad!r}",
                severity="error",
            )
        ]
    return []


def validate_spec(kind: str, spec: dict[str, Any]) -> list[Issue]:
    """Structural issues in one kind's spec. NEVER raises.

    Reports unknown keys with a suggestion and missing required ones as errors.
    Deliberately does NOT
    validate a cron expression or a URL: those need the libraries the service owns, and a parse
    failure at author time would reject a trigger the service could have run. Structure here,
    semantics there.
    """
    issues: list[Issue] = []
    known = SPEC_KEYS.get(kind)
    if known is None:
        return [
            Issue(
                path="kind",
                message=f"unknown trigger kind {kind!r}; expected one of {list(KINDS)}",
                severity="error",
                closest=_closest(kind, KINDS),
            )
        ]
    for key in sorted(spec or {}):
        if key not in known:
            issues.append(
                Issue(
                    path=f"spec.{key}",
                    message=f"{kind} triggers do not use {key!r}",
                    closest=_closest(key, known),
                )
            )

    if kind == "clock":
        clock_kind = str((spec or {}).get("kind", "") or "")
        if not clock_kind:
            issues.append(
                Issue(
                    path="spec.kind",
                    message=f"a clock trigger needs one of {sorted(CLOCK_KINDS)}",
                    severity="error",
                )
            )
        elif clock_kind not in CLOCK_KINDS:
            issues.append(
                Issue(
                    path="spec.kind",
                    message=f"unknown clock kind {clock_kind!r}",
                    severity="error",
                    closest=_closest(clock_kind, CLOCK_KINDS),
                )
            )
        elif clock_kind == "cron" and not str(spec.get("expr", "") or "").strip():
            issues.append(
                Issue(
                    path="spec.expr", message="a cron clock needs an expression", severity="error"
                )
            )
        elif clock_kind in {"at", "sequence"} and not spec.get("at"):
            issues.append(
                Issue(path="spec.at", message=f"an {clock_kind} clock needs `at`", severity="error")
            )
        if clock_kind == "interval":
            # 🔴 THE R1 FLOOR, finally enforced (S109). `MIN_CLOCK_INTERVAL_SECS` was declared and
            # read by NOTHING — measured: `create(spec={"kind":"interval","interval_secs":5})`
            # persisted a 5-second LLM poll with `ok: True` and zero issues. The only live floor was
            # the retired `schedule_add` schema's `min_val=60`, so retiring the alias would have
            # removed the last check standing between a typo and an every-5-seconds model call.
            #
            # A WARNING, not an error, because R1 makes the floor overridable ("a 5-minute
            # local-model poll is a legitimate choice — it just should not be the accident you get
            # from typing `* * * * *`"). An error would refuse a trigger the plan says to allow; a
            # silent pass is what let this go unnoticed. So: it fires, and it is visibly flagged.
            try:
                secs = int((spec or {}).get("interval_secs") or 0)
            except (TypeError, ValueError):
                secs = 0
            if 0 < secs < MIN_CLOCK_INTERVAL_SECS:
                issues.append(
                    Issue(
                        path="spec.interval_secs",
                        message=(
                            f"{secs}s is below the {MIN_CLOCK_INTERVAL_SECS}s floor for an "
                            f"LLM-invoking trigger; it will still run, but confirm this is "
                            f"intended"
                        ),
                    )
                )
    elif kind == "event" and not str((spec or {}).get("source", "") or "").strip():
        issues.append(
            Issue(path="spec.source", message="an event trigger needs a source", severity="error")
        )
    if kind == "event":
        issues.extend(_agent_scope_issues(spec))
    elif kind == "webhook" and not str((spec or {}).get("token_ref", "") or "").strip():
        # A webhook with no token is an unauthenticated fire endpoint. Refused at author time rather
        # than defaulted, because a generated default would be a secret nobody chose.
        issues.append(
            Issue(
                path="spec.token_ref",
                message="a webhook trigger needs a token_ref; an unauthenticated fire endpoint is "
                "refused rather than defaulted",
                severity="error",
            )
        )
    elif kind == "web_watch" and not str((spec or {}).get("url", "") or "").strip():
        issues.append(
            Issue(path="spec.url", message="a web_watch trigger needs a url", severity="error")
        )
    return issues


# ── gates ──

#: The gate vocabulary (§1.1). Closed for the same reason the spec keys are: a gate the service does
#: not read is a safety control the user believes they set.
GATE_KEYS: frozenset[str] = frozenset(
    {
        "debounce_secs",
        "rate_cap",
        "max_fires",
        "skip_dates",
        "quiet_hours",
        "cost_cap",
        "max_cost_usd_per_run",
        "max_actions_per_hour",
        "cooldown_secs",
        "idempotency",
        "threshold",
        "condition",
        "max_runs_per_hour",
        # AUTO-A2 (S70): the pluggable is-the-user-on-duty predicate. Shaped
        # `{provider, config}` like an action, and classified FAIL-OPEN below —
        # a broken calendar app must not silence every automation.
        "duty_gate",
    }
)

#: Gates that FAIL OPEN when their check cannot complete, per R3's amendment.
#: Budget and storm guards
#: time-box and fail open — a budget probe that hangs must not silently stop every automation on the
#: machine. Security fences are absent from this set on purpose: capabilities, the injection screen
#: and fencing fail CLOSED, because the cost of skipping them is unbounded while the cost of a
#: skipped budget check is one extra run.
#: 🔴 TWO VOCABULARIES, and this set has to answer for BOTH (S130).
#:
#: MEASURED: `set(firepath.GATE_ORDER) & FAIL_OPEN_GATES` was **empty**. The names here were the
#: per-trigger CAP KEYS a person edits (`cost_cap`, `rate_cap`, `duty_gate` — the `GATE_KEYS`
#: vocabulary), while the fire path walks GATE names (`screen`, `quiet`, `duty`, `budget`, `claim`,
#: `yield`, `capability`, `incident`). So every gate the engine actually runs read "closed",
#: including
#: `duty` — which §1.4 and `calendar.evaluate_duty` both require to fail OPEN, and which correctly
#: DOES fail open in practice. The classifier disagreed with the code it was written to
#: describe, and
#: nothing outside tests read it, so nothing caught the drift.
#:
#: Both spellings are listed deliberately rather than renaming one side: a person's trigger config
#: says `duty_gate` and the fire path's gate is `duty`, and both are correct in their own surface. A
#: test asserts every `GATE_ORDER` entry resolves to the direction its gate actually implements.
FAIL_OPEN_GATES: frozenset[str] = frozenset(
    {
        # ── per-trigger cap keys (`GATE_KEYS` vocabulary — what a person edits) ──
        "cost_cap",
        "max_cost_usd_per_run",
        "max_actions_per_hour",
        "max_runs_per_hour",
        "rate_cap",
        "condition",
        # AUTO-A2 (S70): the duty gate calls OUT to a provider (a calendar app), so §1.4 classifies
        # it fail-open explicitly — uninstalling the app that supplied it must not silently stop
        # every automation that referenced it. `evaluate_duty` is time-boxed for the same reason.
        "duty_gate",
        # ── fire-path gate names (`firepath.GATE_ORDER` vocabulary — what the engine walks) ──
        # `duty` is the same control as `duty_gate` above, under the name the walk uses.
        "duty",
        # `slot` (§3.5 — S135) belongs with the storm guards, not the fences: an unreadable
        # claim store means "I cannot tell who holds the gpu", and refusing every slotted
        # trigger over a filesystem hiccup would silence real automations. Contention costs
        # a slow run; a stuck-closed slot gate costs the automation. It inherits
        # `read_claim`'s own unreadable-reads-as-idle contract.
        "slot",
        # `active` (§3.5 / WF2AUT-9) is the `skip_if_active` liveness guard, and it belongs with the
        # storm guards for the same reason `slot` does: a broken `git status` or an unreadable path
        # means "I cannot tell if the target is busy", and deferring every guarded fire over that
        # would silence a real automation forever — a stuck-closed liveness gate looks exactly
        # like a dead trigger, while a stuck-open one costs at most one run against a target that
        # turned out to be busy, which the resource-slot and claim gates still bound. The probe in
        # `triggers.liveness.is_target_active` is written fail-open to match.
        "active",
        # `incident` is the kill switch (S117). It inherits `incident_active()`'s own deliberate
        # fail-open contract: an unreadable flag file must not halt every automation on a filesystem
        # hiccup. The asymmetry against the fences below is the point — a stuck-closed kill switch
        # silently stops work the user depends on and looks exactly like a broken scheduler.
        "incident",
        # `spacing` (S151) is debounce + cooldown, and it belongs with the storm guards for the same
        # reason `rate_cap` does: a malformed `debounce_secs` must not SILENCE an automation. The
        # asymmetry is the point — a stuck-closed spacing gate looks exactly like a dead trigger,
        # while a stuck-open one costs at most one duplicate run that the claim lock still bounds.
        "spacing",
        # `rate` (S152) is the hourly-cap gate. Fail-open for the same reason as `slot`: an
        # unreadable ledger means "I cannot tell how often this fired", and suppressing every
        # capped trigger over a filesystem hiccup would silence real automations. The cap's
        # purpose — stop a RUNAWAY — still holds, because a runaway writes many good rows.
        "rate",
        # …and the cap keys under the spelling a person edits.
        "debounce_secs",
        "cooldown_secs",
    }
)

#: Gates whose direction is asserted, not assumed. `budget` is deliberately CLOSED here even though
#: §1.4's prose groups "budget/storm-guard" as fail-open, because §3.6 is more specific and the code
#: follows it: "the budget check is fail-closed — an unreadable budget is not an unlimited one".
#: The per-trigger CAP keys above stay open; the fire path's pre-claim budget READ is closed. Those
#: are different questions about the same word, which is exactly why this is written down.
FAIL_CLOSED_GATES: frozenset[str] = frozenset(
    {"screen", "quiet", "budget", "claim", "yield", "capability", "idempotency"}
)


def gate_failure_mode(gate: str) -> str:
    """`open` or `closed` for one gate, when its own check cannot complete.

    Accepts EITHER vocabulary — a per-trigger cap key (`duty_gate`, `cost_cap`) or a fire-path gate
    name (`duty`, `budget`) — because callers legitimately hold one or the other and a classifier
    that silently answered "closed" for the other namespace is what S130 found.

    Named as a function rather than left implicit so a caller cannot get it wrong by omission: the
    default for an unknown gate is CLOSED. A new gate that nobody classified should refuse the fire,
    not wave it through — the safe direction for a control whose semantics are unknown.
    """
    return "open" if gate in FAIL_OPEN_GATES else "closed"


def validate_gates(gates: dict[str, Any]) -> list[Issue]:
    """Structural issues in the gates block. Never raises."""
    issues: list[Issue] = []
    for key in sorted(gates or {}):
        if key not in GATE_KEYS:
            issues.append(
                Issue(
                    path=f"gates.{key}",
                    message=f"unknown gate {key!r}; it would be stored and never enforced",
                    closest=_closest(key, GATE_KEYS),
                )
            )
    return issues


# ── the entity ──


@dataclass
class Trigger:
    """One automation. The record that replaces `crons.json`, `event_triggers.json` and the hook
    and autonudge configs.

    Field notes worth keeping (the rest are self-describing):

    * `id` is DETERMINISTIC where a feature mints it (`system:heartbeat:fts`), so re-registration on
      every boot is idempotent rather than accumulating a row per restart (R1).
    * `capabilities` is frozen AT SAVE (R3). Resolving it at fire time would let a trigger authored
      when a provider was harmless inherit whatever that provider can do later.
    * `next_fire_at` is persisted BEFORE execution (R1) — a crash mid-run must
    not lose the schedule.
    * The runtime rollups (`last_success_at`, `health_status`, …) live on the row so a list renders
      status dots without reading every run (R7).
    """

    id: str
    name: str
    kind: str
    enabled: bool = True
    created_by: str = "user"
    spec: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    overlap: str = "skip"
    session: str = "fresh"
    model_tier: str = "background"
    delivery: str = "none"
    #: A SEPARATE route for failures (R12). Failures reach the inbox even when `delivery` is none:
    #: an automation the user asked to stay quiet still has to be able to say it broke.
    failure_delivery: str = "inbox"
    retry: dict[str, Any] = field(default_factory=dict)
    failure_policy: dict[str, Any] = field(default_factory=dict)
    yield_to_user: bool = False
    resource_slots: list[str] = field(default_factory=list)
    #: A fire-time LIVENESS guard (§3.5 / WF2AUT-9). Declarative, and empty by default so a trigger
    #: that does not opt in is NEVER deferred — non-breaking for every trigger authored before this
    #: field existed. Where `resource_slots` serializes against a NAMED slot another running trigger
    #: holds, this defers when the working STATE the fire would act on looks busy, using cheap
    #: heuristics the service evaluates up front (`triggers.liveness.is_target_active`):
    #:
    #:   * ``paths: [glob, …]``   — defer if any matching path was modified within ``recent_secs``
    #:                              (default 300s) → "the target was just modified".
    #:   * ``recent_secs: <int>`` — the freshness window `paths` compares against.
    #:   * ``lockfiles: [path, …]`` — defer if any exists → "a lock file is present".
    #:   * ``dirty_git: <dir>``   — defer if that worktree has uncommitted changes → "dirty".
    #:
    #: A busy target yields a `deferred` ledger row, never a drop and never a hard error — the fire
    #: is postponed and re-evaluates next tick, exactly like the resource-slot and yield gates. The
    #: probe is fail-OPEN (a broken `git status` or an unreadable path reads as NOT busy) so a check
    #: that can never pass cannot strand a trigger forever.
    skip_if_active: dict[str, Any] = field(default_factory=dict)
    catch_up: bool = False
    expires_at: str = ""
    # ── runtime rollups, written by the service and never by a form ──
    next_fire_at: str = ""
    last_run_id: str = ""
    run_count: int = 0
    last_success_at: str = ""
    last_failure_at: str = ""
    #: When this trigger last FIRED — set on a granted fire, beside `run_count` (S151).
    #:
    #: 🔴 A THIRD timestamp, deliberately, and the reason is the whole point of the field. Spacing a
    #: fire needs "when did this last fire", and neither existing timestamp answers it:
    #: `last_success_at` and `last_failure_at` both describe an OUTCOME, and a fire that was
    #: SUPPRESSED (quiet hours, budget, overlap) is neither — so debouncing off either one would
    #: count a blocked fire as a fire and let a debounced trigger straight through. The legacy
    #: `event_triggers.EventTrigger` carries exactly this field, which is why debounce works there
    #: and not here (S150 measured that gap and named it).
    #:
    #: ISO, like every other timestamp on this entity. Absent on every row written before S151,
    #: which reads as "never fired" — the right answer for spacing: a trigger with no recorded
    #: fire has nothing to space against, so its first fire is allowed.
    last_fired_at: str = ""
    health_status: str = TriggerHealth.OK.value
    last_error_summary: str = ""
    state: str = TriggerState.ACTIVE.value
    #: When a PARKED trigger becomes eligible to try again (epoch seconds, 0 = immediately).
    #:
    #: 🔴 `autopause.evaluate` has always RETURNED `retry_after=now + PARK_COOLDOWN_SECS` on a
    #: parking exit, and `unpark_due` has always implemented the clock decision — and this
    #: entity had nowhere to keep the number, so `_record_fire_outcome` dropped it and it had no
    #: caller. Measured (S159): one transport outage parked a working trigger and it fired **0 times
    #: over the next 5 slots and stayed `parked` indefinitely** — a 30-second network blip
    #: permanently disabling an automation.
    #:
    #: Epoch rather than ISO, deliberately breaking this entity's timestamp convention: `unpark_due`
    #: compares it against `now` as a float, and a conversion at each comparison site is a
    #: chance to mix units. The ISO fields are the ones a HUMAN reads; only a scheduler reads
    #: this one.
    #:
    #: Absent/0 reads as DUE — `unpark_due`'s own documented contract ("a park written before this
    #: field existed cannot strand a trigger forever"), which is also the fail-open direction: a
    #: missing cooldown must not become an infinite one.
    park_retry_after: float = 0.0
    #: Hash of the LAST failure whose alert went out, and when — what `dedupe_hash` needs (S161).
    #:
    #: 🔴 `failure_policy.dedupe_hash` is written by the migration from the legacy
    #: `last_failure_hash` and was read by nothing: the unified fire path kept the legacy
    #: reminder-window constant and hash helper, and dropped the check that used them.
    #: Measured: the SAME error on 6 consecutive fires produced 6 notifications.
    #:
    #: Hashed from the ERROR TEXT, not from `last_error_summary` — that field holds
    #: `PauseDecision.reason` ("failure 1 of 5", "failure 2 of 5"), which changes on every failure
    #: even when the cause is identical, so hashing it could never dedupe once.
    #:
    #: `last_alert_at` is epoch, matching `park_retry_after` and for the same reason: the window
    #: comparison is float arithmetic, and a conversion at the comparison site is a chance to mix
    #: units. Absent/0 never suppresses, so the first alert of anything always goes out.
    last_alert_hash: str = ""
    last_alert_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "enabled": self.enabled,
            "created_by": self.created_by,
            "spec": dict(self.spec),
            "gates": dict(self.gates),
            "capabilities": dict(self.capabilities),
            "workflow": dict(self.workflow),
            "overlap": self.overlap,
            "session": self.session,
            "model_tier": self.model_tier,
            "delivery": self.delivery,
            "failure_delivery": self.failure_delivery,
            "retry": dict(self.retry),
            "failure_policy": dict(self.failure_policy),
            "yield_to_user": self.yield_to_user,
            "resource_slots": list(self.resource_slots),
            "skip_if_active": dict(self.skip_if_active),
            "catch_up": self.catch_up,
            "expires_at": self.expires_at,
            "next_fire_at": self.next_fire_at,
            "last_run_id": self.last_run_id,
            "run_count": self.run_count,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_fired_at": self.last_fired_at,
            "park_retry_after": self.park_retry_after,
            "last_alert_hash": self.last_alert_hash,
            "last_alert_at": self.last_alert_at,
            "health_status": self.health_status,
            "last_error_summary": self.last_error_summary,
            "state": self.state,
        }

    @property
    def fires_automatically(self) -> bool:
        """Whether the service should ever fire this on its own.

        A `manual` trigger never does, and neither does one in a non-active state. Asked as one
        question so a scheduler cannot forget half of it — checking `enabled` without `state` is how
        an autopaused trigger keeps firing.
        """
        return self.enabled and self.kind != "manual" and self.state == TriggerState.ACTIVE.value


#: Every key `Trigger` recognizes, for the unknown-field warning. Derived from the dataclass rather
#: than hand-listed — a hand-maintained copy drifts the first time a field is added, and then the
#: parser warns about a field it actually supports.
def _known_fields() -> frozenset[str]:
    import dataclasses as _dc

    return frozenset(f.name for f in _dc.fields(Trigger))


def _token_ref_issues(spec: Any) -> list[Issue]:
    """WARN when a `webhook` trigger's `token_ref` holds the token itself (decision 12 — S119).

    🔴 MEASURED. Decision 12 says webhook bearer tokens are "SHA-256-hashed at rest" and R14 says
    "never verbatim in triggers.json". Driven against the real store:

        spec: {"token_ref": "sk-LITERAL-SECRET-abc123"}
          → the token appears VERBATIM in triggers.json, `ok: True`, zero warnings

    S115's `_inline_credential_issues` would have caught that string — but it scans the WORKFLOW
    only, and a webhook's token lives in `spec`. So the one field on the one kind whose entire
    purpose is authentication was the field with no credential lint, and `triggers.json` is
    snapshotted (S113), echoed into run records and rendered in the UI.

    The name is the tell: `token_ref` is a REFERENCE. A value that is not a `{{secret:KEY}}`
    reference is the token itself, which is what this flags.

    A WARNING, not an error, for the reason S115 recorded: refusing would break every webhook a
    user has already authored, which is exactly the population that most needs to keep working
    while they migrate. `parse_trigger` already REFUSES a webhook with no `token_ref` at all — an
    unauthenticated fire endpoint is a different and worse thing than a badly-stored token.
    """
    if not isinstance(spec, dict):
        return []
    raw = spec.get("token_ref")
    if not isinstance(raw, str) or not raw.strip():
        # Absent is handled by the kind's own required-field check, which errors rather than warns.
        return []
    from personalclaw.triggers.secrets import SECRET_REF_RE

    if SECRET_REF_RE.fullmatch(raw.strip()):
        return []
    return [
        Issue(
            path="spec.token_ref",
            message=(
                "token_ref holds the token itself rather than a reference — store it with "
                "`personalclaw auth` and reference it as {{secret:KEY}}, which is resolved at "
                "dispatch and never written to triggers.json (which is snapshotted and rendered "
                "in the UI)"
            ),
        )
    ]


def _inline_credential_issues(workflow: Any) -> list[Issue]:
    """WARN when a trigger's action carries a credential LITERALLY (§7 item 6 / R14 — S115).

    🔴 Measured: the workflow lint flags `curl -H 'Authorization: Bearer sk-ant-api03-…'` as an
    inline secret, and a TRIGGER stored the same string with `ok: True` and zero issues. The two
    surfaces disagreed about the same mistake, so the guidance the workflow validator gives
    ("reference credentials as {{secret:KEY}}") was unenforced for the automation half.

    Reuses `workflows.secrets.find_inline_secrets` rather than re-deriving the credential shapes: a
    second regex set would drift, and this one already skips the sanctioned `{{secret:...}}` form so
    the fix for a finding never trips the finding again.

    A WARNING, not an error. The trigger still fires — refusing would break every automation a user
    already has with a token pasted in, which is exactly the population that most needs to keep
    working while they migrate. The row is visibly flagged in the store, the doctor and the UI.
    """
    if not isinstance(workflow, dict):
        return []
    try:
        from personalclaw.workflows.secrets import find_inline_secrets
    except Exception:  # noqa: BLE001 - a lint that cannot import must not fail a parse
        return []
    try:
        findings = find_inline_secrets(workflow)
    except Exception:  # noqa: BLE001 - same; this module is pure validation and never logs
        return []
    return [
        Issue(
            path=f"workflow.{f.key}" if f.key else "workflow",
            message=(
                f"{f.key or 'the action'} looks like an inline credential ({f.hint}) — "
                f"reference it as {{{{secret:KEY}}}} instead, which is resolved at dispatch and "
                f"never stored"
            ),
        )
        for f in findings
    ]


def parse_trigger(raw: dict[str, Any]) -> tuple[Trigger, list[Issue]]:
    """Parse one authored trigger. Returns `(trigger, issues)` and NEVER raises.

    The never-throw contract (R15) is the whole point: an agent-authored near-miss must become a
    WARNING chip on a loaded row, not an exception that drops the trigger out of the store. So a bad
    kind still yields a Trigger — with `enabled=False`, because a trigger the
    service cannot dispatch
    must not sit in the active set pretending it will fire.

    An unknown top-level key is reported with its closest match and otherwise ignored: keeping it
    would make `to_dict` echo a field nothing reads, which is how a typo survives a round trip and
    looks supported.
    """
    issues: list[Issue] = []
    data = raw if isinstance(raw, dict) else {}
    if not isinstance(raw, dict):
        issues.append(Issue(path="", message="a trigger must be an object", severity="error"))

    known = _known_fields()
    for key in sorted(data):
        if key not in known:
            issues.append(
                Issue(
                    path=key,
                    message=f"unknown trigger field {key!r}",
                    closest=_closest(key, known),
                )
            )

    kind = str(data.get("kind", "") or "").strip().lower()
    # Narrowed with an explicit annotation rather than a conditional expression: the ternary form
    # types as `Any | dict | None`, which mypy correctly refuses at the `validate_*` call below.
    raw_spec = data.get("spec")
    raw_gates = data.get("gates")
    spec: dict[str, Any] = dict(raw_spec) if isinstance(raw_spec, dict) else {}
    gates: dict[str, Any] = dict(raw_gates) if isinstance(raw_gates, dict) else {}
    issues.extend(validate_spec(kind, spec))
    issues.extend(validate_gates(gates))
    issues.extend(_inline_credential_issues(data.get("workflow")))
    issues.extend(_token_ref_issues(data.get("spec")))

    if not str(data.get("id", "") or "").strip():
        issues.append(Issue(path="id", message="a trigger needs an id", severity="error"))
    if not str(data.get("name", "") or "").strip():
        issues.append(Issue(path="name", message="a trigger needs a name", severity="error"))

    overlap = str(data.get("overlap", "skip") or "skip")
    if overlap not in {"skip", "queue", "parallel"}:
        issues.append(
            Issue(
                path="overlap",
                message=f"unknown overlap policy {overlap!r}",
                closest=_closest(overlap, ("skip", "queue", "parallel")),
            )
        )
        overlap = "skip"

    state = str(data.get("state", TriggerState.ACTIVE.value) or TriggerState.ACTIVE.value)
    if state not in {s.value for s in TriggerState}:
        issues.append(
            Issue(
                path="state",
                message=f"unknown state {state!r}",
                closest=_closest(state, tuple(s.value for s in TriggerState)),
            )
        )
        state = TriggerState.ACTIVE.value

    fatal = any(i.severity == "error" for i in issues)
    trigger = Trigger(
        id=str(data.get("id", "") or ""),
        name=str(data.get("name", "") or ""),
        kind=kind,
        # A structurally broken trigger loads DISABLED. It stays visible and
        # editable — which is what
        # makes the warning chip actionable — but the service will not try to dispatch something it
        # cannot interpret.
        enabled=bool(data.get("enabled", True)) and not fatal,
        created_by=str(data.get("created_by", "user") or "user"),
        spec=dict(spec),
        gates=dict(gates),
        capabilities=(
            dict(data["capabilities"]) if isinstance(data.get("capabilities"), dict) else {}
        ),
        workflow=dict(data["workflow"]) if isinstance(data.get("workflow"), dict) else {},
        overlap=overlap,
        session=str(data.get("session", "fresh") or "fresh"),
        model_tier=str(data.get("model_tier", "background") or "background"),
        delivery=str(data.get("delivery", "none") or "none"),
        failure_delivery=str(data.get("failure_delivery", "inbox") or "inbox"),
        retry=dict(data["retry"]) if isinstance(data.get("retry"), dict) else {},
        failure_policy=(
            dict(data["failure_policy"]) if isinstance(data.get("failure_policy"), dict) else {}
        ),
        yield_to_user=data.get("yield_to_user") is True,
        resource_slots=[str(s) for s in (data.get("resource_slots") or [])],
        skip_if_active=(
            dict(data["skip_if_active"]) if isinstance(data.get("skip_if_active"), dict) else {}
        ),
        catch_up=data.get("catch_up") is True,
        expires_at=str(data.get("expires_at", "") or ""),
        next_fire_at=str(data.get("next_fire_at", "") or ""),
        last_run_id=str(data.get("last_run_id", "") or ""),
        run_count=_int(data.get("run_count"), 0),
        last_success_at=str(data.get("last_success_at", "") or ""),
        last_failure_at=str(data.get("last_failure_at", "") or ""),
        last_fired_at=str(data.get("last_fired_at", "") or ""),
        park_retry_after=_float(data.get("park_retry_after"), 0.0),
        last_alert_hash=str(data.get("last_alert_hash", "") or ""),
        last_alert_at=_float(data.get("last_alert_at"), 0.0),
        health_status=str(data.get("health_status", TriggerHealth.OK.value) or "ok"),
        last_error_summary=str(data.get("last_error_summary", "") or ""),
        state=state,
    )
    return trigger, issues


def _int(value: Any, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ── fire / run records ──


@dataclass
class FireRecord:
    """One fire attempt. The row that makes "silent drops are banned" real.

    Written for EVERY fire, including the ones that did nothing — that is the
    point. A suppressed fire
    with no row is indistinguishable from a scheduler that never woke up, and the second is a bug
    while the first is the configuration working.

    `scheduled_for` sits alongside `started_at` so `ran_late` is a measurable fact rather than an
    impression: a run that started 40 minutes after its slot is a different story from one that
    started on time and took 40 minutes.

    `incomplete` marks a count that was cut short ("at least N"), so a reader is never misled by a
    number that stopped early. `acted_on`/`dismissed` are pre-allocated for LEARNING-FLYWHEEL's
    outcome feedback — reserved now because adding them later would mean a migration over history.
    """

    id: str
    trigger_id: str
    outcome: str
    #: The one-line reason. MANDATORY for anything other than a clean run: an
    #: outcome without a reason
    #: tells the user their automation did not happen and nothing else.
    reason: str = ""
    weight: str = RunWeight.LEDGER.value
    scheduled_for: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_secs: float = 0.0
    run_id: str = ""
    mutated: bool = False
    counters: dict[str, Any] = field(default_factory=dict)
    incomplete: bool = False
    #: Reserved for LEARNING-FLYWHEEL (§1.3). Present from the start so the flywheel does not need a
    #: migration over existing history to start reading feedback.
    acted_on: bool = False
    dismissed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trigger_id": self.trigger_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "weight": self.weight,
            "scheduled_for": self.scheduled_for,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_secs": self.duration_secs,
            "run_id": self.run_id,
            "mutated": self.mutated,
            "counters": dict(self.counters),
            "incomplete": self.incomplete,
            "acted_on": self.acted_on,
            "dismissed": self.dismissed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FireRecord:
        """Tolerant read. An unknown outcome becomes `failed`.

        `failed` rather than `ran`: a row this build cannot classify must not
        be counted as a success,
        because a success is what the health rollup and the "what did my machine do" view treat as
        nothing to look at.
        """
        d = d or {}
        outcome = str(d.get("outcome", "") or "")
        if outcome not in FIRE_OUTCOMES:
            outcome = Outcome.FAILED.value
        weight = str(d.get("weight", RunWeight.LEDGER.value) or RunWeight.LEDGER.value)
        return cls(
            id=str(d.get("id", "") or ""),
            trigger_id=str(d.get("trigger_id", "") or ""),
            outcome=outcome,
            reason=str(d.get("reason", "") or ""),
            weight=weight if weight in {w.value for w in RunWeight} else RunWeight.LEDGER.value,
            scheduled_for=str(d.get("scheduled_for", "") or ""),
            started_at=str(d.get("started_at", "") or ""),
            finished_at=str(d.get("finished_at", "") or ""),
            duration_secs=_float(d.get("duration_secs"), 0.0),
            run_id=str(d.get("run_id", "") or ""),
            mutated=d.get("mutated") is True,
            counters=dict(d["counters"]) if isinstance(d.get("counters"), dict) else {},
            incomplete=d.get("incomplete") is True,
            acted_on=d.get("acted_on") is True,
            dismissed=d.get("dismissed") is True,
        )

    @property
    def productive(self) -> bool:
        """Whether this row belongs in the default runs-inbox view.

        Derived from `mutated`, not from the outcome alone: a run can end `ran` and still
        have touched
        nothing, and §1.3's materiality predicate is explicit that the classification criterion is
        "did it mutate durable state". A view built on the outcome would show a page of runs that
        changed nothing.
        """
        return self.mutated and self.outcome in {Outcome.RAN.value, Outcome.RAN_LATE.value}

    @property
    def counts_toward_autopause(self) -> bool:
        """Whether this row moves the trigger toward autopause (R7).

        Only a TRUE failure. Five skipped fires because quiet hours held is the
        configuration working;
        autopausing for that would punish the user for saying "not at night".
        """
        return self.outcome in TRUE_FAILURE_OUTCOMES


def _float(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def classify_weight(*, node_count: int, has_llm: bool, resumable: bool) -> str:
    """Which record weight a fire earns (§1.3).

    FULL for anything with ≥2 nodes, any LLM stage, or anything resumable — those need a
    directory and a journal to be diagnosable. Everything else is a ledger row, which is what keeps
    a minutely trigger from producing 1440 run directories a day.
    """
    if node_count >= 2 or has_llm or resumable:
        return RunWeight.FULL.value
    return RunWeight.LEDGER.value


def require_reason(outcome: str) -> bool:
    """Whether this outcome must carry a human-readable reason.

    Everything except a clean run. `refused` is called out in the plan as mandatory-reason, and the
    same logic applies to every suppression: the user is being told their automation did not happen,
    and "skipped_gate" alone does not say which gate.
    """
    return outcome not in {Outcome.RAN.value, Outcome.RAN_LATE.value}


def fire_issues(record: FireRecord) -> list[Issue]:
    """What is wrong with a fire record. Used by the service's own self-check, not by a form.

    Exists because the "no silent drops" rule is only real if something checks it: a suppression
    written without a reason satisfies the type and defeats the purpose.
    """
    issues: list[Issue] = []
    if record.outcome not in FIRE_OUTCOMES:
        issues.append(
            Issue(path="outcome", message=f"unknown outcome {record.outcome!r}", severity="error")
        )
    if require_reason(record.outcome) and not record.reason.strip():
        issues.append(
            Issue(
                path="reason",
                message=f"{record.outcome} must say why in one line; a suppression with no reason "
                "reads as the automation being broken",
                severity="error",
            )
        )
    if record.outcome == Outcome.RAN_LATE.value and not record.scheduled_for:
        issues.append(
            Issue(
                path="scheduled_for",
                message="ran_late is only meaningful next to the slot it missed",
                severity="error",
            )
        )
    return issues


# ── the migration map (what session 66 needs to be lossless) ──

#: Where every legacy field lands in the unified shape. Written HERE, in session 62, rather than
#: left to the migration session. Measured: `ScheduleJob` has 33 fields, `EventTrigger` 11, and 31
#: of
#: them have no same-named home on `Trigger`. A migration written against the dataclass alone would
#: silently drop `skip_dates` (the trigger keeps firing on a holiday),
#: `strict_schedule` (a missed slot
#: catches up when the author said not to), `content_re` (an event trigger fires on everything), and
#: `token_ref`-class secrets.
#:
#: The value is the destination path. `None` means DELIBERATELY DROPPED, and the comment says why —
#: an unexplained omission is indistinguishable from an oversight when someone reads this in six
#: months.
LEGACY_FIELD_MAP: dict[str, dict[str, str | None]] = {
    "ScheduleJob": {
        "id": "id",
        "name": "name",
        "enabled": "enabled",
        "created_by": "created_by",
        "created_ts": None,  # superseded by the fire-record history; the row keeps no birth time
        # The schedule itself becomes the clock spec's tagged union.
        "schedule": "spec (clock: kind/expr/at)",
        "timezone": "spec.timezone",
        "skip_dates": "spec.skip_dates",
        "strict_schedule": "spec.strict",
        "delete_after_run": "spec.delete_after_run",
        # What it runs.
        "action": "workflow.inline",
        "agent_sequence": "workflow.ref (a sequence becomes a def, not a list on the trigger)",
        "env": "capabilities.env",
        "timeout_secs": "gates.cooldown_secs is NOT this — timeout is per-run, so it rides the run",
        "dry_run": "spec (manual dry-run is a fire MODE, not trigger state)",
        # Delivery.
        "channel": "delivery",
        "thread_ts": "delivery (channel target carries the thread)",
        "silent": "delivery == none",
        "session_key": "session",
        "persistent_session": "session (pinned:<key>)",
        "context_enabled": "spec.context_enabled is NOT a gate — it shapes the run's prompt",
        # Rollups.
        "last_run_ts": "last_success_at / last_failure_at",
        "last_status": "health_status",
        "last_error": "last_error_summary",
        "last_outcome": "the fire record's typed outcome",
        "last_result": None,  # the run's output lives in the run/ledger, never on the trigger row
        "consecutive_failures": "failure_policy (autopause counter is derived from fire records)",
        "last_failure_hash": "failure_policy.dedupe_hash",
        "last_failure_at": "last_failure_at",
        "last_posted_hash": "gates.idempotency",
        "last_posted_at": None,  # duplicate-suppression state belongs with the delivery layer
        "consecutive_dupes": None,  # ditto — a delivery concern, not a trigger field
        "acked_items": None,  # inbox acknowledgement state; the inbox owns it (Inbox-Unification)
    },
    "EventTrigger": {
        "id": "id",
        "enabled": "enabled",
        "pattern": "spec.source",
        "key_glob": "spec.pattern.glob",
        "content_re": "spec.pattern.regex",
        "action_provider": "workflow.inline.provider",
        "action_config": "workflow.inline",
        "max_fires": "gates.max_fires",
        "debounce_secs": "gates.debounce_secs",
        "fire_count": "run_count",
        "last_fired_at": "last_success_at",
    },
}


def unmapped_legacy_fields(legacy: str, field_names: list[str]) -> list[str]:
    """Legacy fields with no entry in the map at all — the ones a migration would drop silently.

    Distinguishes "mapped to None on purpose" from "nobody thought about it". Session 66 runs this
    against the real dataclasses so a field added to `ScheduleJob` after this map was written cannot
    slip through the migration unnoticed.
    """
    known = LEGACY_FIELD_MAP.get(legacy, {})
    return [name for name in field_names if name not in known]
