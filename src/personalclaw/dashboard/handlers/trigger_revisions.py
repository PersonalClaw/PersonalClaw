"""What a trigger row's ``revision`` covers, and the refusal a whole-form save from a stale copy
gets (`personalclaw/stale_write.py`).

The trigger edit forms (the Schedule form, the lifecycle editor) send every field from the copy
they read, so a save made after the agent's `automation_update`, another tab, or the CLI changed
the trigger used to put the page's old values back without a word. Each row a read hands out now
carries the revision of the part of it such a save replaces, and a save names it in ``If-Match``.

Checked BEFORE the consent question in `triggers.api_trigger_detail`: asking the owner to approve
a loosening of a document the write is about to be refused for would be asking about the wrong
copy. From the check to the store write nothing awaits — the consent check and the update helpers
are plain functions for exactly that reason — so no other writer in the gateway's event loop can
land between the comparison and the save.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiohttp import web

from personalclaw.stale_write import revision_of, stale_write_refusal

#: What a schedule row carries beside the automation a whole-form save replaces: its run state,
#: and readings derived from the automation rather than parts of it. The scheduler rewrites the
#: first group on every fire (the next fire, the run count, the health rollup) and autopause flips
#: `enabled`/`state`; the edit form sends none of them, so a revision that covered them would
#: refuse every save made across a fire, for a change the user never made and the save could never
#: have undone. The second group is the cadence SENTENCE (an adaptive clock's embeds the live
#: health state maintenance flips — the form edits `cron_expr`/`every_secs`, never the sentence),
#: the parse issues, the session flag, the attribution verdict, which follows the owner's
#: configured username, why the results cannot reach the channel named — which follows the
#: channel's own setup, not anything the form sends — the two grant verdicts (`needs_review`,
#: `needs_grant`), which follow the capability block the owner's switch writes and the form never
#: sends: an Allow in another tab must not make an open editor's save stale — and a restore's hold
#: (`restore_hold`), which the restore and the switch write.
#:
#: The EXCLUSION is the list, not the inclusion — the reason `arm.NON_CADENCE_SPEC_KEYS` gives: a
#: field the row gains later is covered by default, so a new editable field cannot ride a
#: whole-form save unguarded.
SCHEDULE_RUN_STATE: frozenset[str] = frozenset(
    {
        "enabled",
        "state",
        "last_status",
        "last_run_status",
        "last_run_ts",
        "last_result",
        "last_error",
        "has_result",
        "next_run_ts",
        "next_fire_at",
        "is_running",
        "running_since",
        "run_count",
        "schedule",
        "broken",
        "warnings",
        "has_session",
        "author",
        "read_only",
        "channel_problem",
        "needs_review",
        "needs_grant",
        "restore_hold",
    }
)

#: The same split for a lifecycle row: the fire path stamps `last_run`/`last_status`/`run_count` on
#: every fire, the toggle route owns `enabled`, `used_by`/`enforcement`/`blocking` are read off the
#: agents that reference the hook and the event it is on, and `needs_grant` follows the grant the
#: owner's switch writes — none of it is sent by the edit form.
LIFECYCLE_RUN_STATE: frozenset[str] = frozenset(
    {
        "enabled",
        "last_run",
        "last_status",
        "run_count",
        "used_by",
        "blocking",
        "enforcement",
        "needs_grant",
    }
)

#: The fields that make a schedule PUT a whole-form save: the skip dates, replaced as one list, and
#: the action, every setting the form shows sent as it was read and put over the stored ones
#: (`triggers.action_edit`). The edit form sends both on every save (`ScheduleForm.draftToPayload`),
#: so every form save names its base; a PUT of one scalar — a rename, a new cron — changes only what
#: it names and needs none. A lifecycle save is whole when it carries the action, for the same
#: reason: its form sends every setting it shows.
SCHEDULE_WHOLE_FIELDS: tuple[str, ...] = ("skip_dates", "action")


def edited_part(row: dict[str, Any], run_state: frozenset[str]) -> dict[str, Any]:
    """*row* without its run state — the document a whole-form save replaces, which is what the
    row's ``revision`` describes (and without that ``revision`` itself, once it is attached)."""
    return {k: v for k, v in row.items() if k not in run_state and k != "revision"}


def with_revision(row: dict[str, Any], *, schedule: bool) -> dict[str, Any]:
    """*row* as a read hands it out: with the revision of its edited part, taken from this very
    row — redacted as the reader sees it — so it describes the value beside it and no other."""
    run_state = SCHEDULE_RUN_STATE if schedule else LIFECYCLE_RUN_STATE
    return {**row, "revision": revision_of(edited_part(row, run_state))}


def refusal(
    request: web.Request,
    body: dict[str, Any],
    *,
    schedule: bool,
    current: Callable[[], dict[str, Any] | None],
) -> web.Response | None:
    """The refusal for a whole-form save built from a stale copy of the trigger, else ``None``.

    *current* builds the row the way the list read builds the one it hands out, from the trigger
    as stored now, so the comparison is against exactly the value a fresh read would report. It
    runs only for a whole-form save. A trigger that does not exist (``None``) is left to the write
    path's 404.
    """
    whole = any(key in body for key in SCHEDULE_WHOLE_FIELDS) if schedule else "action" in body
    if not whole:
        return None
    row = current()
    if row is None:
        return None
    run_state = SCHEDULE_RUN_STATE if schedule else LIFECYCLE_RUN_STATE
    what = f"the automation {row['name']!r}" if schedule else f"the trigger {row['name']!r}"
    return stale_write_refusal(request, edited_part(row, run_state), what=what)
