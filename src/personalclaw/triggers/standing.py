"""How each automation stands, said for a chat, and the line a turn carries about all of them.

Asked in a weekly review whether her morning brief and her digest had happened, the agent answered
"unknown" and said a file automation still waited for her approval, while the brief had run that
morning, the digest that evening, and the file automation ran on its own. It answered from what it
remembered of earlier chats and never read the automations: nothing in the turn said a tool could
(with a few tool servers set up, the deferred-tool catalog showed the automation tools as a bare
count), and `automation_list` gave each automation's kind and health but neither when it last ran
nor how that went.

So :func:`standing` says whether an automation runs now, in the order the Triggers page's status
line decides it, and whether it needs the owner; :func:`last_run` says when its newest run record
ran and how that went; `automation_list` (`tools.list_automations`) says both, and the next run,
for every automation; and :func:`automations_note` is the line each native turn that may call that
tool carries: how many automations there are, how many need attention, and the tool that reads
them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from personalclaw.security import redact_for_display

logger = logging.getLogger(__name__)

#: The tool the turn's line names: the one that reads what the line counts.
LIST_TOOL = "automation_list"

#: How a run went, for every status its record can hold: the keys of
#: `triggers.history.SCHEDULE_STATUS_TO_OUTCOME`, which the status vocabulary rail keeps complete
#: for every writer. In the meaning the Triggers page's history gives each one
#: (`web/src/pages/schedule/scheduleMeta.ts`, `statusMeta`), as a phrase rather than a chip, since
#: an agent repeats it to the owner. The record's own reason follows it (:func:`_reason`).
RUN_SAID: dict[str, str] = {
    "success": "ok",
    "failure": "failed",
    "timeout": "timed out",
    "interrupted": "interrupted by a restart",
    "ran_late": "ran late",
    "degraded": "ran without something it needs",
    "launched": "started work that has not reported back yet",
    "declined": "you declined to let it start",
    "refused": "its limits refused some of what it tried",
    "queued": "queued behind a run already in progress",
    "blocked_injection": "blocked: what it was given matched an injection pattern",
    "needs_input": "held: the day's model budget was spent",
    "waiting": "waiting for you",
    "skipped_budget": "skipped: a spending cap was reached",
    "skipped_gate": "skipped: one of its conditions held it",
    "skipped_missed": "missed, and you dismissed it",
    "skipped_noop": "ran and had nothing to do",
    "skipped_overlap": "skipped: a run was already in progress",
    "skipped_triage": "skipped: its triage step set it aside",
}

#: The statuses whose record keeps its reason in `summary` rather than `error`: a run that stopped
#: for the owner keeps its question there, and a degraded one what it went without.
_SUMMARY_REASONS = frozenset({"waiting", "degraded"})

#: How much of a run's reason the list repeats; its history has the rest.
_REASON_CHARS = 160

#: The health rollups that say an automation's recent runs went wrong (`TriggerHealth`).
_UNHEALTHY = frozenset({"degraded", "failing"})


@dataclass(frozen=True)
class Standing:
    """Whether an automation runs now, as the chat says it (`said`), whether it fires on its own as
    things stand (`runs`), and whether it needs the owner to act (`needs_owner`)."""

    said: str
    runs: bool
    needs_owner: bool


def last_check(trigger: Any, *, base_dir: Any) -> dict[str, Any] | None:
    """A web watch's last check of its page (`web_poll.last_check`), masked for a chat's context
    as the Triggers page masks it; None for any other kind, or a watch not checked yet."""
    if trigger.kind != "web_watch":
        return None
    from personalclaw.triggers.web_poll import last_check as checked

    check = checked(trigger, base_dir=base_dir)
    if check is not None:
        check["said"] = redact_for_display(check["said"])
    return check


def standing(trigger: Any, errors: list[Any], *, base_dir: Any = None) -> Standing:
    """How *trigger* stands, as stored (*errors* are its row's): in the order the Triggers page's
    status line decides it, so the chat and the page say the same thing. *base_dir* is the store's
    home, where a web watch keeps its last check.

    It needs the owner when it does not run until they act — a problem to fix, an import or a grant
    waiting on them, a stop after failures, a quarantine, a watch that cannot fire — or when it is
    on and its recent runs went wrong. A park resumes on its own and a switch the owner turned off
    is their own choice, so neither does (`autopause.needs_attention` keeps the same line).
    """
    from personalclaw.triggers import grants
    from personalclaw.triggers.legacy_import import needs_review
    from personalclaw.triggers.models import TriggerState

    if errors:
        said = f"it has a problem and does not run until it is fixed: {errors[0].message}"
        return Standing(said, runs=False, needs_owner=True)
    if needs_review(trigger):
        said = (
            "it was brought over from an older version and does not run until you switch it on "
            "on the Triggers page"
        )
        return Standing(said, runs=False, needs_owner=True)
    if trigger.enabled and grants.labels(trigger):
        said = (
            "it is on the Triggers page, and it does not run until you allow it there: open "
            "it and choose Allow, and PersonalClaw asks you first"
        )
        return Standing(said, runs=False, needs_owner=True)
    state = str(trigger.state or "")
    if state == TriggerState.AUTOPAUSED.value:
        said = "it was stopped after repeated failures, and runs again once you switch it back on"
        return Standing(said, runs=False, needs_owner=True)
    if state == TriggerState.QUARANTINED.value:
        said = (
            "it is quarantined: something it was given matched an injection pattern, and it does "
            "not run until it is re-authored"
        )
        return Standing(said, runs=False, needs_owner=True)
    if state == TriggerState.PARKED.value:
        said = "it is parked: something it needs is busy, and it resumes on its own"
        return Standing(said, runs=False, needs_owner=False)
    if not trigger.enabled:
        said = "it is switched off until you enable it, and visible on the Triggers page"
        return Standing(said, runs=False, needs_owner=False)
    check = last_check(trigger, base_dir=base_dir)
    if check is not None and not check["can_fire"]:
        said = f"it is on, but it cannot fire as things stand: {check['said']}"
        return Standing(said, runs=False, needs_owner=True)
    return Standing(
        "it is active now and visible on the Triggers page",
        runs=True,
        needs_owner=unhealthy(trigger),
    )


def unhealthy(trigger: Any) -> bool:
    """Whether *trigger*'s health rollup says its recent runs went wrong."""
    return str(getattr(trigger, "health_status", "") or "") in _UNHEALTHY


def when_said(epoch: float, zone: tzinfo) -> str:
    """An instant in *zone*, as the calendar tool writes one: ``Fri 2 Oct 2026, 18:24``. A time no
    calendar holds (a hand-edited record) is said as such rather than failing the list."""
    try:
        moment = datetime.fromtimestamp(epoch, tz=zone)
    except (OverflowError, OSError, ValueError):
        return "at a time that cannot be read"
    return f"{moment:%a} {moment.day} {moment:%b %Y}, {moment:%H:%M}"


def _one_line(text: str) -> str:
    """*text* masked for display, on one line, cut at :data:`_REASON_CHARS` (masked first, so a cut
    never leaves part of a secret the mask no longer recognises)."""
    line = " ".join(redact_for_display(text).split())
    return line if len(line) <= _REASON_CHARS else line[: _REASON_CHARS - 1] + "…"


def _reason(row: Mapping[str, Any], status: str) -> str:
    """Why a run went as it did, from its record: its error, else for a run that stopped for the
    owner or ran degraded its summary. A run that went fine gives none: its output is its own, and
    its history shows it."""
    reason = str(row.get("error") or "")
    if not reason and status in _SUMMARY_REASONS:
        reason = str(row.get("summary") or "")
    return _one_line(reason) if reason.strip() else ""


def _said(at: float, status: str, reason: str, zone: tzinfo) -> str:
    words = RUN_SAID.get(status) or f"recorded as {status!r}"
    return f"{when_said(at, zone)}: {words}" + (f" — {reason}" if reason else "")


def last_run(trigger: Any, *, base_dir: Any, zone: tzinfo) -> dict[str, Any] | None:
    """When *trigger* last ran and how that went, as ``{"at", "status", "said"}``, or None when it
    has not run.

    Its newest run record (`schedule_history.ScheduleRunStore`), the one the Triggers page's history
    opens on. A trigger whose history is gone (aged out, or not brought over) falls back to its own
    outcome stamps (`run_record.stamp_run`), which say when it last ran and whether it went wrong,
    and no more than that.
    """
    from personalclaw.schedule_history import ScheduleRunStore

    rows: list[dict[str, Any]] = []
    if base_dir is not None:
        try:
            rows, _total = ScheduleRunStore(base_dir)._list_for_job_sync(trigger.id, 0, 1)
        except Exception:  # noqa: BLE001 - an unreadable history leaves the stamps to say it
            logger.debug("run history unavailable for %s", trigger.id, exc_info=True)
    if rows:
        row = rows[0]
        status = str(row.get("status") or "")
        try:
            at = float(row.get("started_at") or row.get("finished_at") or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        if at > 0:
            return {
                "at": at,
                "status": status,
                "said": _said(at, status, _reason(row, status), zone),
            }
    return _stamped(trigger, zone)


def _stamped(trigger: Any, zone: tzinfo) -> dict[str, Any] | None:
    """The last run as the trigger's own stamps tell it, or None when they record none."""
    from personalclaw.triggers.service import to_epoch

    stamps = {
        "went wrong": to_epoch(getattr(trigger, "last_failure_at", "")),
        "waiting for you": to_epoch(getattr(trigger, "last_waiting_at", "")),
        "no problem recorded": to_epoch(getattr(trigger, "last_success_at", "")),
    }
    words, at = max(stamps.items(), key=lambda stamp: stamp[1])
    if at <= 0:
        return None
    reason = (
        _one_line(str(getattr(trigger, "last_error_summary", "") or ""))
        if words == "went wrong"
        else ""
    )
    said = f"{when_said(at, zone)}: {words}" + (f" — {reason}" if reason else "")
    return {"at": at, "status": "", "said": said}


def next_run(trigger: Any, *, now: float) -> float:
    """When a schedule fires next, as epoch seconds, or 0 for any other kind or none to come."""
    if trigger.kind != "clock":
        return 0.0
    from personalclaw.triggers.schedule_view import _next_run_ts

    try:
        return float(_next_run_ts(trigger, now=now) or 0.0)
    except Exception:  # noqa: BLE001 - a next fire that cannot be computed is not said
        logger.debug("next run unavailable for %s", trigger.id, exc_info=True)
        return 0.0


@dataclass(frozen=True)
class Rollup:
    """How many automations there are, how many are switched on, and how many need the owner."""

    total: int = 0
    on: int = 0
    needs_owner: int = 0

    def said(self) -> str:
        """``6 automations: 5 on, 1 switched off; 2 need attention``, or ``no automations``."""
        if not self.total:
            return "no automations"
        counts = ((self.on, "on"), (self.total - self.on, "switched off"))
        parts = ", ".join(f"{n} {words}" for n, words in counts if n)
        if not self.needs_owner:
            attention = "none needs attention"
        else:
            verb = "needs" if self.needs_owner == 1 else "need"
            attention = f"{self.needs_owner} {verb} attention"
        noun = "automation" if self.total == 1 else "automations"
        return f"{self.total} {noun}: {parts}; {attention}"


def rollup(stood: list[tuple[Any, Standing]]) -> Rollup:
    """The :class:`Rollup` of ``(trigger, standing)`` pairs."""
    return Rollup(
        total=len(stood),
        on=sum(1 for trigger, _ in stood if trigger.enabled),
        needs_owner=sum(1 for _, how in stood if how.needs_owner),
    )


def _note(base_dir: Any = None) -> str:
    """The turn's line, read now from the local store: never a trigger provider's, which may answer
    from across the network, so a turn never waits on one. "" when the store cannot be read."""
    from personalclaw.triggers.store import TriggerStore

    try:
        store = TriggerStore(base_dir=base_dir)
        counted = rollup(
            [
                (row.trigger, standing(row.trigger, row.errors, base_dir=store.base_dir))
                for row in store.load()
            ]
        )
    except Exception:  # noqa: BLE001 - the line is built for every turn and may cost it nothing
        logger.debug("the automations line could not be read", exc_info=True)
        return ""
    if not counted.total:
        return "[automations] The user has no automations."
    return (
        f"[automations] The user has {counted.said()}. {LIST_TOOL} says how each one stands, when "
        "it last ran and how that went, and when it runs next: read it before you say whether an "
        "automation exists, ran or works, since what you remember about one may be out of date."
    )


async def automations_note(tool_index: Mapping[str, Any], offered: Callable[[str], bool]) -> str:
    """The line a native turn carries about the user's automations (:func:`_note`), when the turn
    may call the tool that reads them: it is in *tool_index* and *offered* shows it. Read off the
    event loop, as the turn's other reads are."""
    if LIST_TOOL not in tool_index or not offered(LIST_TOOL):
        return ""
    return await asyncio.to_thread(_note)
