"""One run-history feed across all three trigger kinds (AUTO §7 criterion 4 — S84).

Criterion 4: "A hook, an event trigger, and a cron all show run history in the same feed with the
same record shape and typed outcomes."

**Measured before writing — three incompatible shapes and one unused vocabulary.**

* `schedule` writes a `ScheduleRun` — `run_id`, `job_id`, `trigger`, `started_at`, `finished_at`,
  `duration_ms`, `status`, `summary`, `trace`, `error` — with `status` from
  `{success, failure, timeout, launched}`.
* `lifecycle` (hooks) keeps NO run store. `ScriptHook` carries `last_run`/`last_status`/`run_count`
  plus a transient `ScriptHookResult` (`exit_code`, `duration_ms`, `stderr`) that is never saved.
* `event` keeps a COUNTER: `fire_count` + `last_fired_at`, no per-fire rows at all.

And `FireRecord` — the typed row S62 designed for exactly this, with `FIRE_OUTCOMES` — is
**exported and
never constructed**: `grep "FireRecord("` outside its own module returns nothing. So the shared
shape the criterion asks for already existed on paper and nothing produced it.

**This module projects, it does not migrate.** Each kind keeps its own store; the
projections map what each one HAS onto the common row, and `unified_feed` merges them
newest-first. A migration that rewrote three stores into one is the unified-store program
S83 recorded as unbuilt. A projection is what makes the feed honest meanwhile, because the
alternative is the schedule-only feed the criterion calls wrong.

**Honesty over uniformity, in two places.**

1. **A counter is not a run.** An `event` trigger's `fire_count` becomes ONE synthetic row carrying
   the count, marked `incomplete=True` and `weight=ledger`, never N fabricated rows with invented
   timestamps. `FireRecord.incomplete` exists for this: "a count that was cut short … a reader is
   never misled by a number that stopped early".
2. **`launched` is not `ran`.** The schedule store's honest T7 status — the action started a
   background turn, outcome unknown — maps to `deferred`, not `ran`. Calling it `ran` would report
   success for work nobody has seen, which is the distinction T7 exists to keep.
3. **A status these tables lack is not a run** (WV-15). Both projections used to fall back to
   "it ran" — `hook_to_record` literally returned `RAN if last_run` — so every status written after
   the tables were authored read as work that happened. Measured: NINE values across FOUR writers.
   `hooks.py` writes `skipped_incident` (the incident kill switch held the action BEFORE dispatch —
   nothing executed) and `launched`; `gateway._record_blocked_fire` writes `blocked_injection`; and
   `service._record_suppression_row` writes all six `INERT_OUTCOMES` members into
   `ScheduleRun.status`. The tables now name every one of them, the fallbacks are LOUD (a warning
   naming the status, and never `ran`), and `tests/test_triggers_status_vocabulary.py` reads the
   writers' own ASTs so the next status cannot ship unmapped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from personalclaw.triggers.models import (
    FIRE_OUTCOMES,
    INERT_OUTCOMES,
    FireRecord,
    Outcome,
    RunWeight,
)

logger = logging.getLogger(__name__)

#: `ScheduleRun.status` → `FIRE_OUTCOMES`. The schedule store predates the typed vocabulary, so
#: this is the translation, kept as data because a mapping is reviewable and a chain of `if`s is
#: not.  `timeout` → `failed` rather than its own outcome: `FIRE_OUTCOMES` has no timeout member,
#: and adding one would change a vocabulary five other modules already switch on. The REASON
#: string carries the distinction, which is where a user reads it anyway.
SCHEDULE_STATUS_TO_OUTCOME: dict[str, str] = {
    "success": Outcome.RAN.value,
    "failure": Outcome.FAILED.value,
    "timeout": Outcome.FAILED.value,
    # See the module docstring: started ≠ succeeded.
    "launched": Outcome.DEFERRED.value,
    # `on_overlap: queue` held the start behind a run already in flight (WV-14). DEFERRED's
    # "parked / resource-busy" half, and `LEDGER` weight follows for the same reason
    # `launched` gets it: no run directory or journal exists for it yet.
    "queued": Outcome.DEFERRED.value,
    # 🔴 A SCREENED payload (WV-15). `gateway._record_blocked_fire` writes `status:
    # "blocked_injection"` and this table had no key for it, so a defended injection attempt fell to
    # the `FAILED` fallback below and appeared in the user's history as a genuine failure — the one
    # outcome `TRUE_FAILURE_OUTCOMES` contains. `BLOCKED_INJECTION` is a real member and says the
    # rest of what a reader needs: never auto-retried, and the row names the matched pattern class.
    "blocked_injection": Outcome.BLOCKED_INJECTION.value,
    # 🔴 A SUPPRESSED fire (WV-15). `service._record_suppression_row` persists the typed outcome in
    # BOTH `trigger` and `status` so criterion 8's "zero silent drops" is real — and every one of
    # them missed this table and projected as `failed`. A quiet-hours skip rendered as a red failure
    # in the runs feed, which is the exact confusion `isInertOutcome` was written to prevent one
    # layer later, and it also kept the row OUT of `partition_inert`'s suppressed half, so it buried
    # the fires that mattered instead of folding away.
    #
    # Identity by construction rather than six hand-copied lines: the writer's own guard is
    # `outcome not in INERT_OUTCOMES: return`, so that set IS the vocabulary, and a seventh inert
    # member must not need a second edit here to stay honest.
    **{value: value for value in sorted(INERT_OUTCOMES)},
}

#: Outcomes whose row is bookkeeping, not an openable run — `LEDGER` weight (`FULL` means "earned a
#: run directory and a journal"). A `DEFERRED` fire has not produced one YET, and a suppressed or
#: screened fire never will: neither reached a runner. Kept as one set because both projections
#: below owe the same answer, and they disagreed before WV-15 (the hook projection hardcoded
#: `FULL`, so a hook the incident switch stopped claimed an exit code it never had).
LEDGER_WEIGHT_OUTCOMES: frozenset[str] = frozenset(
    {Outcome.DEFERRED.value, Outcome.BLOCKED_INJECTION.value} | set(INERT_OUTCOMES)
)

#: A hook's `last_status` → `FIRE_OUTCOMES`. Hooks report a shell exit code, so the vocabulary is
#: narrower: it either ran or it did not.
HOOK_STATUS_TO_OUTCOME: dict[str, str] = {
    "ok": Outcome.RAN.value,
    "success": Outcome.RAN.value,
    "error": Outcome.FAILED.value,
    "failure": Outcome.FAILED.value,
    "timeout": Outcome.FAILED.value,
    "blocked": Outcome.REFUSED.value,
    # A hook whose action queued a run rather than starting one (WV-14). Without this the
    # fire would fall to the `RAN if last_run` default and read as "it ran and did something
    # durable" — a queued start has run nothing.
    "queued": Outcome.DEFERRED.value,
    # 🔴 FIRE-AND-FORGET (WV-15). `hooks.py:653` writes `launched` for a run-prompt/run-workflow/
    # invoke-agent action, and this table — alone among the three — had no key for it, so the one
    # status written to say "started ≠ succeeded" landed on the `RAN if last_run` default and said
    # "succeeded". `DEFERRED`, matching `SCHEDULE_STATUS_TO_OUTCOME` and `executor
    # .STATUS_TO_OUTCOME`: the background turn records its own outcome in its own run.
    "launched": Outcome.DEFERRED.value,
    # 🔴 THE INCIDENT KILL SWITCH (WV-15). `hooks.py:590` writes this when `incident_active()` holds
    # the action BEFORE dispatch — the provider is never called, so nothing ran, nothing was spent
    # and nothing changed. That is `SKIPPED_GATE`'s family (quiet-hours / cooldown /
    # condition-false) and its `INERT_OUTCOMES` membership is the point: the row collapses to a
    # ledger row and folds out of the default runs inbox instead of claiming a run.
    #
    # NOT `REFUSED`, which `blocked` already carries: a denylist refusal is a verdict on THIS
    # action ("you may not do that"), while an incident suspends every automated action for a while
    # and lifts on its own. NOT `DEFERRED` either — deferred work still starts, and this fire is
    # dropped, never retried.
    "skipped_incident": Outcome.SKIPPED_GATE.value,
    # 🔴 THE RUNG LADDER (AUTONOMY-GUARDRAILS §5.2). `hooks.py` writes this when the action's
    # declared rung resolves below `auto_with_undo` — the provider is never called, and a
    # durable inbox row now carries the decision. `SKIPPED_GATE` for exactly the reasons
    # `skipped_incident` above gives: nothing ran and nothing was spent, it is not a verdict
    # on this action (`blocked`/REFUSED is), and it is not deferred work that still starts.
    # Mapped in the SAME change that writes it — an unmapped status falls to the `RAN if
    # last_run` default and would report a held action as one that succeeded.
    "held_for_rung": Outcome.SKIPPED_GATE.value,
}


def _redact(text: str) -> str:
    """Strip credentials from a reason string. Criterion 11's rule, applied at THIS boundary too.

    🔴 Found by driving criterion 11 against every surface: `reason` carries a schedule run's raw
    `error`/`summary`, and a run that failed while printing a token would put that token in the
    feed. The live endpoint happens to pre-redact via `_redact_run`, so the shipped path was
    safe — but these projections are public functions, and a second caller passing raw store
    rows would leak. Defending at the boundary rather than trusting the caller is the same rule
    `journal.redact` follows.

    Delegates to the platform redactors; a private pattern copy would drift exactly when it
    mattered.
    """
    try:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        out, _ = redact_exfiltration_urls(text or "")
        out, _ = redact_credentials(out)
        return out
    except Exception:  # noqa: BLE001 - redaction must never empty the feed
        logger.debug("history redaction unavailable", exc_info=True)
        return text or ""


def _iso(ts: Any) -> str:
    """An epoch (or ISO string) as ISO-8601 UTC, or "".

    `FireRecord`'s timestamps are ISO strings while both legacy stores keep floats, so the
    projection has to convert. A bad value yields "" rather than raising: one corrupt row must not
    empty the whole feed, which is the failure that would make a user think nothing ever ran.
    """
    if not ts:
        return ""
    if isinstance(ts, str):
        return ts
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def schedule_run_to_record(run: dict[str, Any], *, trigger_id: str = "") -> FireRecord:
    """Project one `ScheduleRun` dict onto the common row.

    `duration_secs` is derived from `duration_ms` rather than from the timestamps: the store already
    computed it, and re-deriving would disagree by rounding on short runs.

    An unknown status becomes `failed`, matching `FireRecord.from_dict`'s own rule — a row this
    build cannot classify must not be counted as a success, because a success is what the health
    rollup treats as nothing to look at. It also LOGS (WV-15): four statuses reached this fallback
    for months and a silent fallback is why nobody noticed. A warning naming the status is how the
    next one gets found before a user reads it as a failure.
    """
    run = run or {}
    status = str(run.get("status", "") or "")
    outcome = SCHEDULE_STATUS_TO_OUTCOME.get(status, Outcome.FAILED.value)
    if status and status not in SCHEDULE_STATUS_TO_OUTCOME:
        logger.warning(
            "run status %r is not in SCHEDULE_STATUS_TO_OUTCOME; recorded as %s. Map it in "
            "triggers/history.py — a status this build cannot classify is shown as a failure.",
            status,
            outcome,
        )
    reason = str(run.get("error") or "") or str(run.get("summary") or "")
    if status == "timeout":
        # The vocabulary folds timeout into `failed`, so the reason has to carry what was lost.
        reason = f"timed out: {reason}" if reason else "timed out"
    elif status == "launched":
        reason = reason or "action launched a background turn; outcome not yet known"
    elif status == "queued":
        reason = reason or "queued behind a run already in flight; it starts when that one ends"
    elif outcome == Outcome.BLOCKED_INJECTION.value:
        # `_record_blocked_fire` already writes the matched pattern class into `error`; this only
        # covers a row that lost it, because "blocked" with no reason reads as a bug in us.
        reason = reason or "payload blocked by the injection screen; never retried"
    elif outcome in INERT_OUTCOMES:
        # The suppression reason IS the row's `error` (that is where `_record_suppression_row` puts
        # it). A blank one still must not render as an unexplained skip — §7 criterion 8's whole
        # point is that a user can ask "why did my automation not run" and get an answer.
        reason = reason or f"suppressed: {status.replace('_', ' ')}"
    job_id = str(run.get("job_id", "") or "")
    return FireRecord(
        id=str(run.get("run_id", "") or ""),
        trigger_id=trigger_id or (f"schedule:{job_id}" if job_id else ""),
        outcome=outcome,
        reason=_redact(reason)[:200],
        # `FULL` — a schedule run has a real run record behind it (trace, error, duration), which
        # is what `FULL` means: "earned a run directory and a journal". A `DEFERRED` (launched)
        # fire has not produced that yet, so it stays `LEDGER` until its background turn reports —
        # as do the suppressed and screened rows WV-15 mapped, which never reached a runner at all.
        weight=(
            RunWeight.LEDGER.value if outcome in LEDGER_WEIGHT_OUTCOMES else RunWeight.FULL.value
        ),
        started_at=_iso(run.get("started_at")),
        finished_at=_iso(run.get("finished_at")),
        duration_secs=round(float(run.get("duration_ms") or 0) / 1000.0, 3),
        run_id=str(run.get("run_id", "") or ""),
    )


def _hook_reason(status: str, outcome: str) -> str:
    """What a hook's row SAYS, given its raw status and mapped outcome.

    Deliberately the same sentences `schedule_run_to_record` writes for `launched`/`queued`: one
    feed showing two different explanations of one thing reads as two different things. A `RAN` row
    carries no reason, because "it ran" is the whole story and a decorative reason costs a line of a
    user's attention for nothing.
    """
    if outcome == Outcome.RAN.value:
        return ""
    if status == "launched":
        return "action launched a background turn; outcome not yet known"
    if status == "queued":
        return "queued behind a run already in flight; it starts when that one ends"
    if status == "skipped_incident":
        # Names the CAUSE and the exit, not just the state: an incident pause the user cannot see
        # the end of is indistinguishable from a hook that broke.
        return "suppressed: incident mode is active; automated actions resume when it clears"
    return f"hook last reported {status or 'an unknown status'}"


def hook_to_record(hook: Any) -> FireRecord | None:
    """Project a hook's LAST run onto the common row, or None if it never ran.

    One row, not a history: `ScriptHook` keeps `last_run`/`last_status`/`run_count` and no
    per-run store, so the last run is genuinely all there is. Returning None for `run_count ==
    0` matters — a synthetic row for a hook that never fired would read as "it ran and recorded
    nothing", which is the same lie the event-kind `supported: false` response was written to
    avoid.

    `counters` carries `run_count` so the feed can show "this has run 12 times, here is the most
    recent" without inventing eleven rows it does not have.

    🔴 THE FALLBACK IS THE DEFECT (WV-15). This read `RAN if last_run` for any status the table
    lacked, and two of the eight `hooks.py` writes were exactly that: `skipped_incident` (the
    incident switch — the provider was never called) and `launched` (a background turn nobody has
    seen) both landed on "it ran and did something durable". Three cases, and each needs its own
    answer:

    * A status the table names → its mapped outcome.
    * NO status at all (`""`) with a `last_run` → `RAN`. This is a store row written before the
      field existed; "it ran" is the fact we actually have and the verdict is the part that is
      missing, so calling it a failure would invent one. It cannot hide a new status — a hook that
      executes always writes one of the literals.
    * A status the table does NOT name → `FAILED` plus a WARNING naming it. Matches
      `schedule_run_to_record`'s rule (unclassifiable must not read as a success) and is loud,
      because the whole reason these two sat unmapped is that the fallback said nothing.
    """
    run_count = int(getattr(hook, "run_count", 0) or 0)
    last_run = getattr(hook, "last_run", None)
    if run_count <= 0 and not last_run:
        return None
    status = str(getattr(hook, "last_status", "") or "")
    key = status.lower()
    if key in HOOK_STATUS_TO_OUTCOME:
        outcome = HOOK_STATUS_TO_OUTCOME[key]
    elif not key:
        outcome = Outcome.RAN.value if last_run else Outcome.FAILED.value
    else:
        outcome = Outcome.FAILED.value
        logger.warning(
            "hook status %r is not in HOOK_STATUS_TO_OUTCOME; recorded as %s. Map it in "
            "triggers/history.py — a status this build cannot classify is shown as a failure.",
            status,
            outcome,
        )
    hook_id = str(getattr(hook, "id", "") or "")
    return FireRecord(
        id=f"lifecycle:{hook_id}:last",
        trigger_id=f"lifecycle:{hook_id}",
        outcome=outcome,
        reason=_redact(_hook_reason(status, outcome)),
        # `FULL`: a hook genuinely executed a script, with an exit code and a duration behind it.
        # `LEDGER` when it did not (WV-15): an incident-suppressed hook never reached its provider
        # and a `launched` one has no verdict yet, so neither has the exit code `FULL` promises.
        weight=(
            RunWeight.LEDGER.value if outcome in LEDGER_WEIGHT_OUTCOMES else RunWeight.FULL.value
        ),
        started_at=_iso(last_run),
        finished_at=_iso(last_run),
        run_id="",
        counters={"run_count": run_count},
        # A hook keeps only its most recent run, so any count above 1 means earlier rows are gone.
        incomplete=run_count > 1,
    )


def event_trigger_to_record(trigger: Any) -> FireRecord | None:
    """Project an event trigger's fire COUNTER onto one synthetic row, or None if it never fired.

    Deliberately ONE row for N fires. The store keeps `fire_count` + `last_fired_at` and nothing
    else, so N rows would mean N invented timestamps — a fabricated history is worse than an
    honest summary,
    and `incomplete=True` plus the count in `counters` says exactly what is known.

    `weight=ledger` rather than `full`: this row is a bookkeeping summary, not a run the user can
    open. A reader or health rollup treating it as a run would double-count every fire behind it.
    """
    count = int(getattr(trigger, "fire_count", 0) or 0)
    last = getattr(trigger, "last_fired_at", None)
    if count <= 0 and not last:
        return None
    tid = str(getattr(trigger, "id", "") or "")
    return FireRecord(
        id=f"event:{tid}:summary",
        trigger_id=f"event:{tid}",
        outcome=Outcome.RAN.value,
        reason=f"{count} fire(s) recorded; this store keeps a counter, not per-fire rows",
        weight=RunWeight.LEDGER.value,
        started_at=_iso(last),
        finished_at=_iso(last),
        counters={"fire_count": count},
        incomplete=True,
    )


def unified_feed(
    *,
    schedule_runs: list[dict[str, Any]] | None = None,
    hooks: list[Any] | None = None,
    event_triggers: list[Any] | None = None,
    limit: int = 50,
) -> list[FireRecord]:
    """Every kind's history as one list, newest first.

    Sorted on `started_at` DESCENDING with a stable secondary key on `id`, so two rows from the same
    second do not reshuffle between requests — a feed that reorders on refresh makes a reader lose
    their place, the same reason `inbox.order_rows` breaks ties on id.

    Rows with no timestamp sort LAST rather than first. An unparseable or absent time is not news; a
    feed that led with it would bury the run that just happened.
    """
    records: list[FireRecord] = []
    for run in schedule_runs or []:
        try:
            records.append(schedule_run_to_record(run))
        except Exception:  # noqa: BLE001 - one bad row must not empty the feed
            logger.debug("could not project a schedule run", exc_info=True)
    for hook in hooks or []:
        try:
            record = hook_to_record(hook)
        except Exception:  # noqa: BLE001
            logger.debug("could not project a hook", exc_info=True)
            continue
        if record is not None:
            records.append(record)
    for trigger in event_triggers or []:
        try:
            record = event_trigger_to_record(trigger)
        except Exception:  # noqa: BLE001
            logger.debug("could not project an event trigger", exc_info=True)
            continue
        if record is not None:
            records.append(record)

    records.sort(key=lambda r: (r.started_at == "", r.started_at, r.id), reverse=False)
    # `started_at == ""` sorts False(0) before True(1), so timestamped rows lead; within them the
    # sort is ascending, so reverse the timestamped head to get newest-first while keeping the
    # empty tail last.
    timed = [r for r in records if r.started_at]
    untimed = [r for r in records if not r.started_at]
    timed.sort(key=lambda r: (r.started_at, r.id), reverse=True)
    untimed.sort(key=lambda r: r.id)
    return (timed + untimed)[: max(1, limit)]


def feed_response(records: list[FireRecord], *, total: int | None = None) -> dict[str, Any]:
    """The wire shape for the unified feed.

    `supported` is gone from this response ON PURPOSE. The per-kind `supported: false` (S67) was the
    honest answer while only schedules had rows; now every kind projects, so a flag saying
    otherwise would be stale. What replaces it is per-row honesty: `incomplete` and `weight` say
    which rows are summaries rather than openable runs.
    """
    rows = [r.to_dict() for r in records]
    did, suppressed = partition_inert(records)
    return {
        "runs": rows,
        "total": total if total is not None else len(rows),
        "kinds": sorted({r.trigger_id.split(":", 1)[0] for r in records if r.trigger_id}),
        # Named so a caller can render "3 of these are summaries" rather than implying 3 more runs.
        "summaries": sum(1 for r in records if r.incomplete),
        # §1.3's archive split (S132). `runs` still carries EVERY row — a surface that lost the
        # suppressed ones could not answer "why did my automation not run", and §7 criterion 8 bans
        # silent drops. These two ids lists let a default view show work and fold the rest away.
        "did_ids": [r.id for r in did],
        "suppressed_ids": [r.id for r in suppressed],
        "suppressed": len(suppressed),
    }


def is_inert(record: FireRecord) -> bool:
    """Whether this row is a suppression rather than work the machine DID (§1.3 — S132).

    🔴 `INERT_OUTCOMES` was declared in `models.py` and read by NOTHING. §1.3 says inert outcomes
    "collapse to ledger rows and archive out of the default inbox view — the runs inbox is for what
    the machine DID", and measured: the unified feed returned every row undifferentiated, so a
    minutely trigger suppressed by quiet hours buried the one fire that mattered under 1439 skips.
    """
    return record.outcome in INERT_OUTCOMES


def partition_inert(records: list[FireRecord]) -> tuple[list[FireRecord], list[FireRecord]]:
    """`(did_something, suppressed)` — §1.3's archive split.

    A PARTITION rather than a filter, deliberately: the suppressed rows are the answer to "why did
    my automation not run", so dropping them would replace one bad default with a worse one. §7
    criterion 8 bans silent drops, and a row filtered out of the only surface that shows it is a
    silent drop with extra steps. The caller renders them behind an "archived" affordance.
    """
    did: list[FireRecord] = []
    suppressed: list[FireRecord] = []
    for record in records:
        (suppressed if is_inert(record) else did).append(record)
    return did, suppressed


def outcome_counts(records: list[FireRecord]) -> dict[str, int]:
    """Per-outcome tally for the health rollup, over the typed vocabulary only.

    Every key comes from `FIRE_OUTCOMES`, so a caller can render a fixed set of chips instead of
    discovering outcome names at runtime — and an outcome this build does not know cannot appear,
    because `FireRecord.from_dict` already folds unknown ones into `failed`.
    """
    counts = {name: 0 for name in FIRE_OUTCOMES}
    for record in records:
        if record.outcome in counts:
            counts[record.outcome] += 1
    return {k: v for k, v in counts.items() if v}
