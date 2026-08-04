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
}

#: A hook's `last_status` → `FIRE_OUTCOMES`. Hooks report a shell exit code, so the vocabulary is
#: narrower: it either ran or it did not.
HOOK_STATUS_TO_OUTCOME: dict[str, str] = {
    "ok": Outcome.RAN.value,
    "success": Outcome.RAN.value,
    "error": Outcome.FAILED.value,
    "failure": Outcome.FAILED.value,
    "timeout": Outcome.FAILED.value,
    "blocked": Outcome.REFUSED.value,
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
    rollup treats as nothing to look at.
    """
    run = run or {}
    status = str(run.get("status", "") or "")
    outcome = SCHEDULE_STATUS_TO_OUTCOME.get(status, Outcome.FAILED.value)
    reason = str(run.get("error") or "") or str(run.get("summary") or "")
    if status == "timeout":
        # The vocabulary folds timeout into `failed`, so the reason has to carry what was lost.
        reason = f"timed out: {reason}" if reason else "timed out"
    elif status == "launched":
        reason = reason or "action launched a background turn; outcome not yet known"
    job_id = str(run.get("job_id", "") or "")
    return FireRecord(
        id=str(run.get("run_id", "") or ""),
        trigger_id=trigger_id or (f"schedule:{job_id}" if job_id else ""),
        outcome=outcome,
        reason=_redact(reason)[:200],
        # `FULL` — a schedule run has a real run record behind it (trace, error, duration), which
        # is what `FULL` means: "earned a run directory and a journal". A `DEFERRED` (launched)
        # fire has not produced that yet, so it stays `LEDGER` until its background turn reports.
        weight=(
            RunWeight.LEDGER.value if outcome == Outcome.DEFERRED.value else RunWeight.FULL.value
        ),
        started_at=_iso(run.get("started_at")),
        finished_at=_iso(run.get("finished_at")),
        duration_secs=round(float(run.get("duration_ms") or 0) / 1000.0, 3),
        run_id=str(run.get("run_id", "") or ""),
    )


def hook_to_record(hook: Any) -> FireRecord | None:
    """Project a hook's LAST run onto the common row, or None if it never ran.

    One row, not a history: `ScriptHook` keeps `last_run`/`last_status`/`run_count` and no
    per-run store, so the last run is genuinely all there is. Returning None for `run_count ==
    0` matters — a synthetic row for a hook that never fired would read as "it ran and recorded
    nothing", which is the same lie the event-kind `supported: false` response was written to
    avoid.

    `counters` carries `run_count` so the feed can show "this has run 12 times, here is the most
    recent" without inventing eleven rows it does not have.
    """
    run_count = int(getattr(hook, "run_count", 0) or 0)
    last_run = getattr(hook, "last_run", None)
    if run_count <= 0 and not last_run:
        return None
    status = str(getattr(hook, "last_status", "") or "")
    outcome = HOOK_STATUS_TO_OUTCOME.get(
        status.lower(), Outcome.RAN.value if last_run else Outcome.FAILED.value
    )
    hook_id = str(getattr(hook, "id", "") or "")
    return FireRecord(
        id=f"lifecycle:{hook_id}:last",
        trigger_id=f"lifecycle:{hook_id}",
        outcome=outcome,
        reason=(
            ""
            if outcome == Outcome.RAN.value
            # `status` comes from the hook store, so it is redacted like any other stored text.
            else _redact(f"hook last reported {status or 'an unknown status'}")
        ),
        # `FULL`: a hook genuinely executed a script, with an exit code and a duration behind it.
        weight=RunWeight.FULL.value,
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
