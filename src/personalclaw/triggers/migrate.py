"""The lossless cron migration (AUTOMATION-SUBSTRATE §2/§6 — S66).

This is the step that cannot be redone cheaply. Once `crons.json` has been converted and the
legacy service retired, a dropped field is a behaviour nobody can recover without the user's
old file — and the failures are quiet: a lost `skip_dates` fires on a holiday, a lost
`strict_schedule` catches up when the author said not to, a lost `timezone` runs at the wrong
hour for half the year.

So the migration is written against S62's `LEGACY_FIELD_MAP` rather than against the
dataclass, and `unconverted_fields()` proves per job that every field it carried was either
translated or explicitly dropped-with-a-reason. "Looks right" is not the bar; the bar is that
nothing left the building unaccounted for.

Two measurements that changed this code:

* `ScheduleService._save` persists 33 of the dataclass's 35 fields — `dry_run` and
  `last_outcome` never reach disk. They are runtime-only, so the migration cannot read them
  and must not claim to; a converter that mapped them would be translating a value that is
  always the default.
* `ScheduleDefinition` has three kinds (`every`, `at`, `cron`) and the trigger clock spec has
  three too, but they do not line up. Legacy `every` has no trigger equivalent, and mapping it
  onto `at` — the tempting shape match, since both carry one number — would turn every
  recurring interval job into a one-shot that fires once and dies.

Pure functions over dicts. Nothing here writes: the caller owns the store, so a migration can
be run as a dry run and diffed before anything is replaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw.triggers.models import LEGACY_FIELD_MAP, TriggerHealth, TriggerState

#: Fields on `ScheduleJob` that never reach `crons.json`. Measured against
#: `ScheduleService._save`'s literal projection: it writes 33 of 35, and these two are runtime-only.
#: Named here so `unconverted_fields` does not report them as dropped — a migration cannot lose a
#: value that was never stored, and claiming otherwise would make the audit lie in the other
#: direction.
NEVER_PERSISTED: frozenset[str] = frozenset({"dry_run", "last_outcome"})

#: `last_status` values the legacy service writes, mapped onto trigger health. `ok`/`error` are what
#: `_record_run` sets; anything else (including an empty string on a never-run job) reads as OK,
#: because "never ran" is not "unhealthy" and showing a fresh job as failing would train the user to
#: ignore the column.
_HEALTH_FROM_STATUS: dict[str, str] = {
    "ok": TriggerHealth.OK.value,
    "success": TriggerHealth.OK.value,
    "error": TriggerHealth.FAILING.value,
    "failed": TriggerHealth.FAILING.value,
    "timeout": TriggerHealth.DEGRADED.value,
}


@dataclass
class Converted:
    """One converted row plus the audit of how it got that way.

    `dropped` and `unaccounted` are separate because they mean different things. A dropped field was
    deliberately not carried and the map says why; an unaccounted one is
    a field nobody thought about,
    which is a bug in the migration rather than a decision about the data.
    """

    trigger: dict[str, Any]
    dropped: list[str] = field(default_factory=list)
    unaccounted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def lossless(self) -> bool:
        return not self.unaccounted


def clock_spec(schedule: dict[str, Any], job: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The trigger `clock` spec for a legacy `ScheduleDefinition`. Returns `(spec, notes)`.

    The three legacy kinds do NOT map one-to-one onto the trigger's three:

    * `cron` → `{kind: cron, expr}` — the clean case.
    * `at` → `{kind: at, at, delete_after_run}` — a one-shot, and `delete_after_run` rides along
      because §1.2 makes it the default for `at` and the legacy row carries the user's choice.
    * `every` → `{kind: cron, ...}` is WRONG and `{kind: at}` is worse. An interval has no cron
      expression and is not a one-shot, so it converts to an explicit
      `interval_secs` spec. Mapping it
      onto `at` (the tempting shape match, since both carry one number) would turn every recurring
      interval job into a one-shot that fires once and dies — the single most destructive possible
      mistranslation in this file.

    `timezone`, `skip_dates` and `strict_schedule` ride the spec verbatim
    for every kind, because they
    are the quietly-losable ones: a dropped `skip_dates` fires on a holiday and nobody knows why.
    """
    notes: list[str] = []
    kind = str((schedule or {}).get("kind", "") or "")
    spec: dict[str, Any] = {}

    if kind == "cron":
        expr = str((schedule or {}).get("cron_expr", "") or "")
        spec = {"kind": "cron", "expr": expr}
        if not expr:
            notes.append(
                "a cron job with no expression cannot be scheduled; needs author attention"
            )
    elif kind == "at":
        spec = {
            "kind": "at",
            "at": (schedule or {}).get("at_ts"),
            # The legacy row's own choice, not the §1.2 default: a one-shot the user marked to keep
            # must not be deleted because the new default says otherwise.
            "delete_after_run": bool(job.get("delete_after_run", True)),
        }
    elif kind == "every":
        secs = (schedule or {}).get("every_secs")
        spec = {"kind": "interval", "interval_secs": secs}
        notes.append(
            "legacy `every` has no trigger clock kind; converted to an explicit "
            "interval rather than `at`, which would turn a recurring job into a one-shot"
        )
    else:
        spec = {"kind": ""}
        notes.append(
            f"unknown legacy schedule kind {kind!r}; the trigger loads disabled for review"
        )

    # The quietly-losable trio, carried for every kind.
    if job.get("timezone"):
        spec["timezone"] = job["timezone"]
    if job.get("skip_dates"):
        spec["skip_dates"] = list(job["skip_dates"])
    if job.get("strict_schedule"):
        spec["strict"] = True
    return spec, notes


def _delivery(job: dict[str, Any]) -> str:
    """The trigger's delivery route for a legacy job.

    `silent` wins over a channel. The legacy flag means "the agent sends via send_message
    itself", so a trigger that also auto-delivered would double-post — the user-visible symptom
    that would make someone distrust the whole migration.
    """
    if job.get("silent"):
        return "none"
    channel = str(job.get("channel", "") or "")
    return f"channel:{channel}" if channel else "none"


def _session(job: dict[str, Any]) -> str:
    """The trigger's session binding.

    `persistent_session` plus a key means the legacy job kept state across fires, which
    is `pinned:`.
    A key WITHOUT the flag is the stateless convention (`cron:{id}:{uuid8}` per fire), so it stays
    `fresh` — pinning it would silently make every fire share one growing session, and the
    drift shows up as an automation that gets slower and stranger over weeks.
    """
    key = str(job.get("session_key", "") or "")
    if job.get("persistent_session") and key:
        return f"pinned:{key}"
    return "fresh"


def _workflow(job: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """What the trigger runs.

    A legacy `agent_sequence` becomes a NOTE, not a field: §2 says a sequence becomes a def, and
    silently flattening a multi-step sequence into a single inline action would run only its first
    step. Converting it needs a def written, which is authoring work, not migration work — so the
    migration preserves the list in the note and leaves the trigger disabled.
    """
    notes: list[str] = []
    sequence = list(job.get("agent_sequence") or [])
    if sequence:
        notes.append(
            f"agent_sequence has {len(sequence)} steps ({', '.join(sequence[:3])}"
            f"{'…' if len(sequence) > 3 else ''}); §2 converts a sequence to a workflow "
            "DEF, which is authoring work — the trigger is left disabled rather than "
            "running only its first step"
        )
        return {}, notes
    action = job.get("action") if isinstance(job.get("action"), dict) else {}
    return ({"inline": dict(action)} if action else {}), notes


def convert_job(job: dict[str, Any]) -> Converted:
    """One `crons.json` row → one Trigger dict, with an audit.

    Never raises. A row this cannot interpret still converts — disabled, with notes — because a
    migration that skipped a row would leave the user with an automation that silently stopped
    existing, and no way to tell it apart from one they deleted themselves.
    """
    notes: list[str] = []
    # Annotated rather than a ternary: the conditional form types as `Any | dict | None`, which mypy
    # correctly refuses at the `clock_spec` call. Same narrowing as `parse_trigger` (S62).
    raw_schedule = job.get("schedule")
    schedule: dict[str, Any] = dict(raw_schedule) if isinstance(raw_schedule, dict) else {}
    spec, spec_notes = clock_spec(schedule, job)
    notes.extend(spec_notes)
    workflow, wf_notes = _workflow(job)
    notes.extend(wf_notes)

    gates: dict[str, Any] = {}
    if job.get("last_posted_hash") or job.get("consecutive_dupes"):
        # The legacy duplicate-suppression state is not carried, but the INTENT is: the job was
        # deduping its output, so the trigger declares idempotency rather than losing the behaviour.
        gates["idempotency"] = True
        notes.append("duplicate-suppression state is delivery-layer; carried as gates.idempotency")

    capabilities: dict[str, Any] = {}
    if job.get("env"):
        capabilities["env"] = dict(job["env"])

    failure_policy: dict[str, Any] = {}
    if job.get("last_failure_hash"):
        failure_policy["dedupe_hash"] = True

    status = str(job.get("last_status", "") or "").strip().lower()
    last_run = float(job.get("last_run_ts") or 0.0)
    healthy = _HEALTH_FROM_STATUS.get(status, TriggerHealth.OK.value)

    trigger: dict[str, Any] = {
        "id": str(job.get("id", "") or ""),
        "name": str(job.get("name", "") or ""),
        "kind": "clock",
        # A row with notes loads DISABLED even if it was enabled, so nothing fires on a schedule the
        # migration could not fully interpret. The user re-enables after reading the note — the
        # opposite default would run a half-understood automation unattended.
        "enabled": bool(job.get("enabled", False)) and not notes,
        "created_by": str(job.get("created_by", "user") or "user"),
        "spec": spec,
        "gates": gates,
        "capabilities": capabilities,
        "workflow": workflow,
        "session": _session(job),
        "delivery": _delivery(job),
        "failure_delivery": "inbox",
        "failure_policy": failure_policy,
        "state": TriggerState.ACTIVE.value,
        "health_status": healthy,
        "last_error_summary": str(job.get("last_error", "") or ""),
        "last_success_at": _iso(last_run) if status in {"ok", "success"} else "",
        "last_failure_at": _iso(float(job.get("last_failure_at") or 0.0)),
    }
    if notes:
        trigger["state"] = TriggerState.PAUSED.value

    dropped = [
        name
        for name, dest in LEGACY_FIELD_MAP["ScheduleJob"].items()
        if dest is None and name in job
    ]
    return Converted(
        trigger=trigger,
        dropped=sorted(dropped),
        unaccounted=unconverted_fields(job),
        notes=notes,
    )


def _iso(epoch: float) -> str:
    """An epoch as an ISO-8601 UTC string, or "" for zero.

    Zero means "never". Rendering it as 1970-01-01 would put a date on screen that reads as a
    real event — the kind of thing a user tries to explain rather than dismiss.
    """
    if epoch <= 0:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def unconverted_fields(job: dict[str, Any]) -> list[str]:
    """Fields present on this job that the map does not account for at all.

    The load-bearing audit. A field here is one the migration would carry into the void — not
    dropped on purpose (the map records those with a reason), but unnoticed. S62 wrote the map
    so this check is possible; running it per row is what makes "lossless" a measurement
    rather than a claim.

    `NEVER_PERSISTED` is excluded: `dry_run` and `last_outcome` are on the dataclass but absent from
    `_save`'s projection, so a `crons.json` row cannot carry them and reporting them would make the
    audit lie in the other direction.
    """
    known = set(LEGACY_FIELD_MAP["ScheduleJob"]) | NEVER_PERSISTED
    return sorted(k for k in (job or {}) if k not in known)


@dataclass
class MigrationReport:
    """The whole migration, as a reviewable artifact.

    Exists so the conversion can be run as a DRY RUN and read before anything is replaced. A
    migration whose only output is a rewritten store is one nobody can check until too late.
    """

    converted: list[Converted] = field(default_factory=list)
    #: Rows the migration refused entirely — a row with no id cannot be addressed later, so it is
    #: reported rather than given a generated one (a synthetic id would make the row un-recognizable
    #: against the user's own file).
    refused: list[dict[str, Any]] = field(default_factory=list)

    @property
    def lossless(self) -> bool:
        return all(c.lossless for c in self.converted) and not self.refused

    @property
    def needs_review(self) -> list[str]:
        return [c.trigger["id"] for c in self.converted if c.notes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "converted": len(self.converted),
            "refused": len(self.refused),
            "lossless": self.lossless,
            "needs_review": self.needs_review,
            "unaccounted": sorted({f for c in self.converted for f in c.unaccounted}),
        }


def migrate_crons(store: dict[str, Any]) -> MigrationReport:
    """Convert a whole `crons.json` payload. Never raises.

    Order is preserved from the file so a reviewer can diff the report against their own store
    line by line. A row with no `id` is refused rather than assigned one: a generated id would
    be un-recognizable against the user's file, and "which of my jobs is this" is the first
    question they would ask.
    """
    report = MigrationReport()
    jobs = store.get("jobs") if isinstance(store, dict) else None
    for raw in jobs or []:
        if not isinstance(raw, dict) or not str(raw.get("id", "") or "").strip():
            report.refused.append(raw if isinstance(raw, dict) else {"row": repr(raw)[:120]})
            continue
        report.converted.append(convert_job(raw))
    return report
