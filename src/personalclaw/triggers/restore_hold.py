"""What a replace restore does with the automations it writes back, and how they are resumed.

🔴 A replace restore writes a home back from a snapshot whole, the trigger store included, so every
automation came back as the snapshot had it: switched on and armed. Restored onto a second machine,
the move to a new computer, it ran beside the original, which still ran the same automations, and
every brief, digest and watch went out twice. A merge never did this: a row it brings in arrives
switched off (`store.arrived_from_another_home`).

So a replace holds each automation that would run on its own here (:func:`hold`): switched off,
with no armed next fire, and with where the snapshot came from on the row (`Trigger.restore_hold`,
:func:`origin`). The rest of the row stays the snapshot's — what it runs, what the owner allowed
it, its history — because a replace is this home becoming the one the snapshot was taken from, not
another home's automation arriving. The restore says how many and why (:func:`paused_line`); the
Triggers page's Resume all (:func:`resume_all`) and each one's own switch take them on.

Not held: a trigger that runs only when it is run (``manual``), one already switched off, a row
that cannot be parsed (it loads switched off), and a row somebody else wrote, which this harness
never arms and whose switch is not this home's to set (`triggers.ownership`).

A system automation whose switch is a setting is switched from it on every start
(:func:`switch_from_config`), and keeps its hold through each of them: the setting says "on"
because the snapshot did, which is not anyone here resuming it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from personalclaw.triggers.models import RestoreHold

logger = logging.getLogger(__name__)

ANOTHER_HOME = RestoreHold.ANOTHER_HOME.value
THIS_HOME = RestoreHold.THIS_HOME.value
UNKNOWN = RestoreHold.UNKNOWN.value


def origin(snapshot_home: str, home: Path) -> str:
    """Where a snapshot that names *snapshot_home* comes from, seen from *home*: :data:`THIS_HOME`
    when it names this home (`durability.shards.machine_id`), :data:`ANOTHER_HOME` when it names
    another, and :data:`UNKNOWN` when it names none, as a snapshot taken before they did. Writes
    nothing: a home that has no id yet is not the one any snapshot names."""
    if not snapshot_home:
        return UNKNOWN
    from personalclaw.durability.shards import recorded_machine_id

    return THIS_HOME if snapshot_home == recorded_machine_id(home) else ANOTHER_HOME


def _held(row: Any, *, owner: str) -> Any:
    """*row*'s trigger when a restore holds it, else None: one this home would run without anyone
    asking — it parses, it is switched on, it is not one that runs only when it is run, and it is
    the owner's (another person's row is never armed here)."""
    from personalclaw.triggers.models import parse_trigger
    from personalclaw.triggers.ownership import is_owner_authored

    if not isinstance(row, dict):
        return None
    trigger, issues = parse_trigger(row)
    if any(i.severity == "error" for i in issues) or not trigger.enabled:
        return None
    if trigger.kind == "manual" or not is_owner_authored(trigger, owner=owner):
        return None
    return trigger


def _rows(doc: Any) -> list[Any]:
    """The rows of a trigger store's document, either shape it is written in
    (``triggers.store.STORE_SHAPE``); none for no document."""
    from personalclaw.triggers.store import STORE_SHAPE

    return (STORE_SHAPE.records(doc) if doc is not None else None) or []


def hold(home: Path, held_from: str) -> list[str]:
    """Hold every automation the trigger store in *home* would run on its own, as a restore has
    just written it: switched off, its next fire disarmed, *held_from* on the row. Returns the ids
    it held.

    Reads and writes *home*'s own store, not the active home's: the restore names the home it
    wrote. Under the store's lock, re-read under it, by its one writer (`record_files.rewrite`).
    An unreadable store is left as it is: this home lists nothing in it, so nothing in it runs,
    and every write to it is refused until it can be read.
    """
    from personalclaw import record_files
    from personalclaw.triggers.ownership import owner_username
    from personalclaw.triggers.store import STORE_FILENAME, STORE_SHAPE

    owner = owner_username()
    ids: list[str] = []

    def _hold(doc: Any) -> Any:
        for row in _rows(doc):
            trigger = _held(row, owner=owner)
            if trigger is None:
                continue
            row["enabled"] = False
            row["next_fire_at"] = ""
            row["restore_hold"] = held_from
            ids.append(trigger.id)
        return doc if ids else None

    try:
        record_files.rewrite(Path(home) / STORE_FILENAME, _hold, STORE_SHAPE)
    except record_files.Unreadable:
        logger.warning("restore: %s/%s is unreadable; nothing in it is held", home, STORE_FILENAME)
        return []
    return ids


def would_hold(store_file: Path) -> int:
    """How many automations the trigger store at *store_file* (an unpacked snapshot's) holds that a
    replace would hold: what its dry run says, read and nothing written."""
    from personalclaw import record_files
    from personalclaw.triggers.ownership import owner_username

    try:
        doc = record_files.read(Path(store_file))
    except record_files.Unreadable:
        return 0
    owner = owner_username()
    return sum(1 for row in _rows(doc) if _held(row, owner=owner) is not None)


def held(store: Any) -> list[Any]:
    """The automations a restore holds in *store*: what Resume all resumes."""
    return [
        row.trigger for row in store.load() if row.trigger.restore_hold and not row.trigger.enabled
    ]


def resume_all(store: Any) -> tuple[list[Any], list[tuple[Any, str]]]:
    """Resume every automation a restore holds in *store*, each the way its own switch resumes it
    (`tools.set_paused`): switched on, armed from now, the hold ended.

    Returns what it resumed, and what stays held with why: a row that no longer parses, or one
    whose action needs the owner's yes, which its own switch asks for (a replace keeps the yes the
    snapshot had, so such a row could not run where the snapshot was taken either).
    """
    from personalclaw.triggers import tools

    resumed: list[Any] = []
    kept: list[tuple[Any, str]] = []
    for trigger in held(store):
        result = tools.set_paused(store, trigger_id=trigger.id, paused=False)
        if result.ok:
            resumed.append(trigger)
        else:
            kept.append((trigger, result.text.removeprefix("Error: ")))
    return resumed, kept


def switch_from_config(trigger: Any, on: bool) -> None:
    """Set the switch of an automation whose switch is kept elsewhere — a setting, as every start
    writes it, or the report it runs, as the report's save does: *on*, unless a restore holds it,
    which keeps it switched off until someone here resumes it. Off ends the hold: there is nothing
    left to resume."""
    if on and trigger.restore_hold and not trigger.enabled:
        return
    trigger.enabled = on
    if not on:
        trigger.restore_hold = ""


# ── what the restore says ──


def _counted(count: int) -> tuple[str, str]:
    """The pronoun and the noun for *count* automations: ``("it", "1 automation")``."""
    return ("it", "1 automation") if count == 1 else ("them", f"{count} automations")


def paused_line(count: int, held_from: str, *, would: bool = False) -> str:
    """What a restore that held *count* automations says it did, and why; *would*, what its dry run
    says it would do."""
    them, counted = _counted(count)
    runs = "runs on its own" if count == 1 else "run on their own"
    same = "the same automation runs" if count == 1 else "the same automations run"
    why = {
        ANOTHER_HOME: (
            "This snapshot comes from another PersonalClaw home; if that home is still running, "
            f"{same} there too."
        ),
        THIS_HOME: f"This snapshot comes from this home, so nothing else runs {them}.",
    }.get(
        held_from,
        "This snapshot does not say which PersonalClaw home it comes from; if that home is still "
        f"running, {same} there too.",
    )
    return f"{'Would pause' if would else 'Paused'} {counted} that {runs}. {why}"


def where_to_resume(count: int, held_from: str) -> str:
    """Where the owner resumes what the restore held, and when it is safe to."""
    them, _ = _counted(count)
    when = "when you are ready" if held_from == THIS_HOME else "once that home is retired"
    return f"Resume {them} on the Triggers page (Resume all) {when}."


def resume_question(count: int) -> str:
    """The question a restore of this home's own snapshot asks at the terminal."""
    them, _ = _counted(count)
    return f"Resume {them} now? [Y/n] "


def resumed_line(count: int) -> str:
    """What the restore says once it has resumed *count* automations."""
    return f"Resumed {_counted(count)[1]}."
