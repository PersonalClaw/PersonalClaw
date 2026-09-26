"""A task's due date tells you it is coming.

`Task.due` was read by exactly one thing — the ready projection's `overdue` rank — and no
notification kind existed for it, so a due date set on a task was a promise nothing kept. This
module is the whole of the feature's policy; the gateway only calls :func:`run_once` on a timer
(`gateway._task_due_loop`), and the delivery itself is the one choke point every notice goes
through (`DashboardState.notify`), with its kind registered as `tasks/due` (`TASK_DUE`), so the
Settings → Notifications matrix has a row for it like every other kind.

**When** (:func:`notice_at`). A due date with a time is announced :data:`LEAD` before it. A
date-only due date — which is what the task form writes — has no time of day: read as the moment
the day starts (which is when the ready projection already calls it overdue), its notice would go
out at midnight the night before, so it goes out at :data:`DATE_ONLY_NOTICE_HOUR` on that day
before instead. A notice missed while the gateway was down still goes out on the first sweep
after, until the due moment is :data:`STALE_AFTER` behind: past that the task is simply overdue —
the board says so — and a first start after an upgrade must not announce every task that went
stale months ago.

**Once per due date.** Each notice sent is recorded as ``{task_id: due}`` in
``<home>/task_due_notices.json`` (its own durability entry; NOT under ``tasks/``, whose files are
the native provider's task records). Nothing is held in memory between sweeps, so a restart neither
repeats a notice nor loses one. Moving the due date re-arms it: the record names the date it was
sent for. A record is dropped only once its date can no longer be announced (its due moment is
:data:`STALE_AFTER` behind) — never because a sweep did not see the task, since a task provider
that fails to list is skipped by `registry.collect_tasks` and one bad sweep would otherwise erase
every record and announce them all again.

**Quiet hours** are honoured by WAITING. The gate (`entity_routes.notification_posture`) drops an
INFO note inside the window — right for a heartbeat, wrong for a reminder, which would then never
arrive — so a notice whose moment falls in quiet hours is left unsent and unrecorded, and the first
sweep after the window ends sends it. Mute and a raised minimum severity mean "not at all" rather
than "not now": the notice is handed to `notify()`, whose gate drops it, and it is recorded, so
unmuting next week does not deliver a week-old reminder.

**Who.** Only the owner's open tasks (`Task.belongs_to`, the same rule the ready count uses), and
only a task whose ``due_reminder`` is on — the per-task opt-out.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from urllib.parse import quote

from personalclaw import notification_kinds
from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.tasks.models import Task, TaskStatus

logger = logging.getLogger(__name__)

#: How long before a task is due its notice goes out.
LEAD = timedelta(days=1)

#: The hour a date-only due date is announced on the day before (local time). See the module note.
DATE_ONLY_NOTICE_HOUR = 9

#: How far past its due moment a task may still be announced. See the module note.
STALE_AFTER = timedelta(days=1)

#: How often the gateway sweeps. A notice is at most this late, which is nothing against a lead
#: time of a day, and a sweep reads every open task once.
SWEEP_SECS = 300

#: How often the gateway's loop looks for the dashboard before its first sweep. The timers start
#: before the dashboard does, and `notify` lives on the dashboard's state.
STARTUP_POLL_SECS = 1.0

#: The ledger's file name under the home — its durability entry is `task_due_notices`.
LEDGER_NAME = "task_due_notices.json"

#: Statuses whose work is over. A reminder for one would be noise.
_FINISHED = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.SKIPPED})

Notify = Callable[..., None]


def _ledger_path():
    # Resolved per call, like `tasks/native._tasks_dir`: a module-level binding would capture
    # whatever home the process had at import.
    return config_loader.config_dir() / LEDGER_NAME


def _owner() -> str:
    from personalclaw.identity import current_username

    return current_username()


# ── when ──────────────────────────────────────────────────────────────────────────


def _is_date_only(raw: str) -> bool:
    return len(raw) == 10 and raw[4] == "-" and raw[7] == "-"


def _parse(due: str) -> datetime | None:
    raw = (due or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def notice_at(due: str) -> float | None:
    """The epoch second a task due at ``due`` is announced, or ``None`` when it is not a date."""
    parsed = _parse(due)
    if parsed is None:
        return None
    if _is_date_only(due.strip()):
        day_before = parsed.date() - LEAD
        return datetime(
            day_before.year, day_before.month, day_before.day, DATE_ONLY_NOTICE_HOUR
        ).timestamp()
    return (parsed - LEAD).timestamp()


def _due_moment(due: str) -> float | None:
    parsed = _parse(due)
    return parsed.timestamp() if parsed is not None else None


def is_due_for_notice(task: Task, *, now: float, owner: str) -> bool:
    """Whether ``task`` should be announced at ``now`` — ignoring whether it already was."""
    if not task.due_reminder or task.status in _FINISHED or not task.belongs_to(owner):
        return False
    at, moment = notice_at(task.due), _due_moment(task.due)
    if at is None or moment is None:
        return False
    return at <= now < moment + STALE_AFTER.total_seconds()


# ── the words ─────────────────────────────────────────────────────────────────────


def _day_words(day: date) -> str:
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"


def notice_text(task: Task, *, now: float) -> tuple[str, str]:
    """The notice's ``(title, body)``: when it is due, said relative to ``now``."""
    parsed = _parse(task.due)
    assert parsed is not None  # guarded by is_due_for_notice
    today = datetime.fromtimestamp(now).date()
    date_only = _is_date_only(task.due.strip())
    local = parsed if date_only else datetime.fromtimestamp(parsed.timestamp())
    due_day = local.date()
    days = (due_day - today).days
    at = "" if date_only else f" at {local.strftime('%H:%M')}"
    if days == 0:
        when = f"Due today{at}"
    elif days == 1:
        when = f"Due tomorrow{at}"
    elif days < 0:
        when = f"Overdue since {_day_words(due_day)}{at}"
    else:
        when = f"Due {_day_words(due_day)}{at}"
    return f"{when}: {task.title}", f"Due {_day_words(due_day)}{at}."


# ── once, across a restart ────────────────────────────────────────────────────────


def _read_ledger() -> dict[str, str]:
    """``{task_id: due}`` for every notice already sent.

    Fails OPEN to empty — an unreadable ledger re-sends the notices still inside their window (at
    most two days of tasks) rather than withholding them all, because a repeated reminder is
    recoverable and a missing one is not. Said out loud, since it is a choice.
    """
    path = _ledger_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("task due notices: unreadable %s; treating as empty", path)
        return {}
    notified = raw.get("notified") if isinstance(raw, dict) else None
    if not isinstance(notified, dict):
        return {}
    return {str(k): str(v) for k, v in notified.items() if isinstance(v, str)}


def _write_ledger(notified: dict[str, str]) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps({"notified": notified}, indent=2, sort_keys=True))


def sweep(tasks: Iterable[Task], *, notify: Notify, now: float) -> list[str]:
    """Announce every task whose notice is due and not yet sent. Returns the ids announced.

    ``notify`` is ``DashboardState.notify`` in production. The ledger is re-read on every sweep and
    written only when it changed; it is trimmed by TIME (:func:`_can_still_be_announced`), not by
    which tasks this sweep was handed, so a finished or deleted task's record still goes, and a
    task a failed provider left out of this sweep keeps its record.
    """
    from personalclaw.providers import entity_routes

    owner = _owner()
    tasks = list(tasks)
    ledger = _read_ledger()
    moment = datetime.fromtimestamp(now)
    announced: list[str] = []
    changed = False
    for task in tasks:
        if ledger.get(task.id) == task.due or not is_due_for_notice(task, now=now, owner=owner):
            continue
        posture = entity_routes.notification_posture(notification_kinds.TASK_DUE, now=moment)
        if posture == entity_routes.POSTURE_DROP and entity_routes.quiet_hours_now(now=moment):
            continue  # not now — the first sweep after the window ends sends it
        title, body = notice_text(task, now=now)
        notify(
            notification_kinds.TASK_DUE,
            title,
            body,
            # `statusUrl` is the note's deep link — the key the notifications page's "Open task"
            # button reads (`notificationLink`), as trigger fires already use it. The id is
            # encoded as the dashboard encodes it: another provider's id may carry `#` or `&`.
            meta={
                "statusUrl": f"#/tasks?open={quote(task.id, safe='')}",
                "task_id": task.id,
                "due": task.due,
            },
        )
        ledger[task.id] = task.due
        announced.append(task.id)
        changed = True
    kept = {tid: due for tid, due in ledger.items() if _can_still_be_announced(due, now=now)}
    if changed or kept != ledger:
        _write_ledger(kept)
    return announced


def _can_still_be_announced(due: str, *, now: float) -> bool:
    """Whether a notice for ``due`` could still go out — the half of :func:`is_due_for_notice`'s
    window a ledger record exists to guard. Past it, the record has no job left."""
    moment = _due_moment(due)
    return moment is not None and now < moment + STALE_AFTER.total_seconds()


async def run_once(notify: Notify, *, now: float) -> list[str]:
    """One sweep over every provider's tasks — the gateway loop's body."""
    from personalclaw.tasks import registry

    tasks, _truncated = await registry.collect_tasks()
    return sweep(tasks, notify=notify, now=now)
