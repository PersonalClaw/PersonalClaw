"""The one automation substrate: triggers fire workflows (AUTOMATION-SUBSTRATE).

Session 62 is the ENTITY layer only — the record, its per-kind specs, and the fire/run records with
typed outcomes. The scheduler (`TriggerService`) is session 63, dispatch is 64, and the
lossless cron
migration is 66. Nothing here schedules or fires anything, deliberately: the shape has to be settled
before three legacy stores are folded into it, because the migration is the step that cannot be
redone cheaply.

Three stores this absorbs, measured before the dataclass was written (S62):

* `crons.json` — `ScheduleJob`, 33 fields including `skip_dates`, IANA `timezone`,
  `strict_schedule`, `delete_after_run`, `agent_sequence`, `consecutive_failures`.
* `event_triggers.json` — `EventTrigger`, 11 fields: `pattern`, `key_glob`, `content_re`,
  `max_fires`, `fire_count`, `debounce_secs`, `last_fired_at`.
* the hook/autonudge configs, whose semantics arrive as the `event` and `idle` kinds.

Every field those carry has a home in `Trigger` or its `spec`/`gates`, and
`test_triggers_entity.py` asserts that per field rather than by inspection — a migration that
silently dropped `skip_dates` would keep firing on a holiday, and the user would not know why.
"""

from personalclaw.triggers.models import (
    FIRE_OUTCOMES,
    KINDS,
    FireRecord,
    Outcome,
    RunWeight,
    Trigger,
    TriggerHealth,
    TriggerState,
    parse_trigger,
    validate_spec,
)

__all__ = [
    "FIRE_OUTCOMES",
    "KINDS",
    "FireRecord",
    "Outcome",
    "RunWeight",
    "Trigger",
    "TriggerHealth",
    "TriggerState",
    "parse_trigger",
    "validate_spec",
]
