---
id: a-schedule-has-one-reader
type: ai-coding-rule
statement: >
  A clock trigger's fire IS the run of the action it fires. An action provider must not read
  a schedule of its own to decide whether a fire counts, and a feature that keeps a schedule of
  its own (a research report) is mirrored by its trigger, with an edit on either side written
  to both. A fire that runs nothing is recorded as a skip with its reason, never as a success.
appliesTo:
  - src/personalclaw/action_providers/*.py
  - src/personalclaw/knowledge/report_schedules.py
  - src/personalclaw/triggers/tools.py
requiredTests:
  - tests/test_a_reports_schedule_is_one_schedule.py::test_a_fire_at_the_moved_time_runs_the_report
  - tests/test_a_reports_schedule_is_one_schedule.py::test_editing_the_time_on_the_triggers_page_moves_the_report
  - tests/test_a_reports_schedule_is_one_schedule.py::test_a_fire_that_did_not_run_is_recorded_as_skipped_never_as_success
source: >
  A research report's runner re-checked the report's own cron on every fire. Its automation,
  moved on the Triggers page, fired at its new time; the re-check found no slot of the old
  time had passed and skipped, the history recorded the skip as a success, and switching the
  report off and on wrote the old time back over the edit. Two readers of one schedule are two
  schedules, and the second one is invisible until they disagree.
expiry_condition: >
  Retire if a feature's schedule can only be stored in one place by construction (the trigger
  row itself), leaving nothing to mirror.
---

# A schedule has one reader

When something runs on a schedule, the clock decides when, and nothing downstream decides
again. A provider that "double-checks" a cadence on each fire has made a second schedule:
the two agree until someone edits one of them, and then fires are dropped silently.

## What compliance looks like

- An action provider a trigger fires runs when it is called. A provider that cannot run (its
  work is already in flight, it has nothing to do) returns `outcome="skip"` with a `summary`
  sentence that says why, which the history records as the inert `skipped_noop`.
- A feature that owns a schedule mirrors it into ONE trigger row and writes only the fields it
  owns there, keeping the row's own settings and the clock's state
  (`knowledge.report_schedules.to_trigger`).
- Every edit of that row through `triggers.tools` is asked `edit_refusal` before it is saved
  and handed to `adopt` after, so the feature's copy moves with it; a deletion leaves the
  feature unscheduled (`adopt_removal`).
