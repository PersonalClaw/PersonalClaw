"""Incident kill switch.

One flag — ``~/.personalclaw/incident.json`` (``{active, reason, started_at}``) —
suspends ALL unattended work within one poll interval. There is no unified trigger
store to flip (six independent stores), so incident mode does NOT mutate stores; it
is checked at the execution seams (cron due-collection, hook fire, event-trigger
fire, the idle runtime that fires loop cycles and idle triggers, the loop watchdog,
memory consolidation and background compression, inbox AI, non-interactive subagent
spawn). Each seam gains one ``if incident_active(): skip``, and what becomes of the work it
skips is the seam's own: some is done once the switch is off, and some is not. Seam by seam
(`tests/test_incident_words_match_the_seams.py`):

- A recurring automation whose time comes is refused, and runs at its next time: the tick moves
  it on before the gate refuses it, so Resume does not run it. Counted as a run skipped.
- A one-shot whose time passes is switched off and kept, to run by hand. Counted as a run skipped.
- A lifecycle hook's action, a webhook, and an event automation (an Inbox message, a memory write)
  that fire meanwhile are dropped: each leaves a history row saying so, and none runs later. A
  hook's is counted as a hook dropped, the others' as events dropped.
- A background agent's start is refused, and the work that asked for it is told. Counted as a
  spawn refused.
- Idle automations and loop cycles are held due, and run on the first check after Resume, once: a
  loop whose cycle came due is counted as a loop cycle skipped, since the cycles it would have
  made meanwhile are not made. An idle automation is not counted.
- A running loop or workflow run is held where it is, and goes on after Resume.
- File and page watches skip their polls, and catch up after Resume.
- Inbox sorting, memory consolidation and background compression wait, and run after Resume.

**What it held back is counted** (:func:`note_suppressed`) in the incident's ``suppressed``: a
count and the first :data:`REFS_KEPT` work references for each of :data:`SUPPRESSED_COUNTERS`,
which ``GET /api/incident`` serves while it is on and its line in the history keeps. The work
that catches up is not counted, since none of it is lost.

**Every incident is kept.** Starting it takes a reason (:data:`REASON_MAX` characters at most)
and records who started it: the owner (Settings, the dashboard's own route), the command line, or
an app, named. Resume records who ended it and appends the incident's one line to
``incidents.jsonl`` (:func:`resume`); the owner's notes on it append there too
(:func:`add_note`), and :func:`history` reads them back, newest first. Only this module reads or
writes that file. A line that cannot be written never stops Resume: the owner's decision is
honoured, the failure is logged, and the Doctor's ``incident_history`` row says so until a line
is written again.

**Interactive chat is untouched** — the user talking to their assistant during an
incident is the point. Resume is EXPLICIT (``POST /api/incident/resume {confirm}``
or ``personalclaw incident off``); activation/resume are SEL-audited.

The in-process mirror is refreshed from the file's mtime (the existing mtime-sync
habit), so a flag flipped by the CLI in another process is picked up by the running
gateway without a restart. :func:`watch` is how the gateway tells its open pages that
the switch moved, so no page has to poll it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_INCIDENT_FILENAME = "incident.json"
_HISTORY_FILENAME = "incidents.jsonl"

#: The most characters a reason may have, after trimming, and the most a note may have.
REASON_MAX = 200
NOTE_MAX = 4000
#: How many lines the history keeps once it is trimmed (it is trimmed at twice this).
HISTORY_KEEP = 1000
#: How many incidents one page of :func:`history` holds.
HISTORY_PAGE = 20
#: Who may start an incident, and who may end one.
STARTERS = ("owner", "cli", "app")
ENDERS = ("owner", "cli")
#: The counters an incident's ``suppressed`` holds (:func:`note_suppressed` fills them).
SUPPRESSED_COUNTERS = (
    "runs_skipped",
    "events_dropped",
    "hooks_dropped",
    "loop_cycles_skipped",
    "spawns_refused",
)
REASON_REQUIRED = "Stopping unattended work needs a reason; nothing was changed."
#: How many work references each counter names; its count keeps counting past them.
REFS_KEPT = 50


class IncidentReasonRequired(ValueError):
    """An incident started with no reason, or one longer than :data:`REASON_MAX`."""


def empty_suppressed() -> dict[str, dict[str, Any]]:
    """Each of :data:`SUPPRESSED_COUNTERS` at nothing held back."""
    return {name: {"count": 0, "refs": []} for name in SUPPRESSED_COUNTERS}


#: How often :func:`watch` looks at the flag. One ``stat`` of one small file, so it is cheap to
#: look often, and this is what bounds how late an open page follows ``personalclaw incident``.
WATCH_INTERVAL_SECS = 2.0


@dataclass(frozen=True)
class IncidentState:
    active: bool = False
    reason: str = ""
    started_at: str = ""
    #: Who started it (one of :data:`STARTERS`), and the app's name when an app did.
    activated_by: str = ""
    app: str = ""
    #: What it held back so far, by counter (:func:`empty_suppressed`).
    suppressed: dict[str, Any] = field(default_factory=dict)
    #: Why the last incident's line could not be written, while no line has been since.
    history_unwritten: str = ""


def _incident_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _INCIDENT_FILENAME


# In-process mirror + the mtime it was loaded at, so a flag flipped by another
# process (the CLI) is picked up without a restart.
_mirror: IncidentState | None = None
_mirror_mtime: float = -1.0


def _read_file() -> IncidentState:
    path = _incident_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return IncidentState()
        suppressed = data.get("suppressed")
        return IncidentState(
            active=bool(data.get("active", False)),
            reason=str(data.get("reason", "")),
            started_at=str(data.get("started_at", "")),
            activated_by=str(data.get("activated_by", "")),
            app=str(data.get("app", "")),
            suppressed=suppressed if isinstance(suppressed, dict) else {},
            history_unwritten=str(data.get("history_unwritten", "")),
        )
    except (OSError, ValueError):
        return IncidentState()


def get_incident() -> IncidentState:
    """Current incident state, refreshing the mirror from the file's mtime.

    Fail-safe on error is NOT applied here (incident is opt-IN, not a guard flag):
    an unreadable/missing file means NO incident — the normal, overwhelmingly common
    state. Flipping every seam off on a transient read error would be the wrong
    failure mode for a kill switch (it would halt all automation on a hiccup)."""
    global _mirror, _mirror_mtime
    path = _incident_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # No file → no incident. Reset the mirror so a resumed state is seen.
        _mirror = IncidentState()
        _mirror_mtime = -1.0
        return _mirror
    if _mirror is None or mtime != _mirror_mtime:
        _mirror = _read_file()
        _mirror_mtime = mtime
    return _mirror


def incident_active() -> bool:
    """True when unattended work must be suspended. The one call each seam makes."""
    return get_incident().active


def activate(reason: str, *, by: str, app: str = "") -> IncidentState:
    """Turn incident mode ON (SEL-audited), for *reason*, started *by* one of :data:`STARTERS`
    (*app* naming the app when an app did).

    Raises :class:`IncidentReasonRequired` for a reason that is empty, or longer than
    :data:`REASON_MAX` once trimmed, and changes nothing then. A start while one is already active
    keeps it as it is, its reason and who started it included, and the second reason goes to the
    security log alone: the incident is the first one's until it is resumed.
    """
    text = str(reason or "").strip()
    if not text or len(text) > REASON_MAX:
        raise IncidentReasonRequired(REASON_REQUIRED)
    if by not in STARTERS:
        raise ValueError(f"{by!r} is not who starts an incident: one of {', '.join(STARTERS)}")
    with _locked():
        current = _read_file()
        if current.active:
            state = None
        else:
            state = IncidentState(
                active=True,
                reason=text,
                started_at=datetime.now(timezone.utc).isoformat(),
                activated_by=by,
                app=app if by == "app" else "",
                suppressed=empty_suppressed(),
                history_unwritten=current.history_unwritten,
            )
            _write(state)
    _audit("incident_activated", reason=text)
    if state is None:
        return current
    logger.warning("INCIDENT MODE ACTIVATED: %s", text)
    return state


@dataclass(frozen=True)
class Resumed:
    """What :func:`resume` did: the state now (off), the incident's line, whether the line was
    written (``recorded``), and why not (``why``) when it was not."""

    state: IncidentState
    incident: dict[str, Any] | None
    recorded: bool
    why: str = ""


def resume(*, by: str) -> Resumed:
    """Turn incident mode OFF (SEL-audited), ended *by* one of :data:`ENDERS`, after appending the
    incident's one line to the history. A line that cannot be written does not stop it: the
    owner's decision is honoured, the failure is logged, and the state keeps why
    (``history_unwritten``) for the Doctor until a line is written. What the incident dropped
    meanwhile stays dropped (the module docstring, seam by seam)."""
    if by not in ENDERS:
        raise ValueError(f"{by!r} is not who ends an incident: one of {', '.join(ENDERS)}")
    line: dict[str, Any] | None = None
    recorded, why = True, ""
    # Under the incident's lock, read from the file: a count another process made a moment ago
    # (:func:`note_suppressed`) is in the line, and none lands on the state after it.
    with _locked():
        current = _read_file()
        if current.active:
            line = _incident_line(current, resumed_by=by)
            why = _append(line)
            recorded = not why
        unwritten = why or ("" if line is not None else current.history_unwritten)
        state = IncidentState(active=False, history_unwritten=unwritten)
        _write(state)
    _audit("incident_resumed")
    logger.warning("Incident mode resumed (unattended work re-enabled)")
    return Resumed(state=state, incident=line, recorded=recorded, why=why)


#: Whether :func:`note_suppressed` has logged a failure yet: it logs the first, and only that.
_note_failure_logged = False


def note_suppressed(counter: str, ref: str = "", *, once: bool = False) -> None:
    """Count one piece of work the active incident held back, at *counter* (one of
    :data:`SUPPRESSED_COUNTERS`), named by the work reference *ref* (``workref.work_ref``).

    It lands in the incident the caller saw active, under the incident's lock, and only while
    the file still names that incident's start: a count that comes after Resume, or after the
    next incident began, is not one of its own. Each counter names the first :data:`REFS_KEPT`
    references it is given, each once, and its count keeps counting past them. With *once*, a
    reference it already names is not counted again: a row held due across many checks is one
    thing held back, not one for each check.

    Never raises and never starts an incident: a count that cannot be made is logged, the first
    time only, and the work's own skip goes on as it was.

    The lock is a blocking ``flock``, taken on the caller's thread, the gateway's event loop
    included: one lock and one rewrite of a small file per thing held back. That holds while every
    section under the lock stays as short as it is here; one that waits on anything else would
    stall the loop each time incident mode drops work.
    """
    global _note_failure_logged
    try:
        seen = get_incident()
        if not seen.active:
            return
        if counter not in SUPPRESSED_COUNTERS:
            raise ValueError(f"{counter!r} is not one of {', '.join(SUPPRESSED_COUNTERS)}")
        with _locked():
            current = _read_file()
            if not current.active or current.started_at != seen.started_at:
                return
            suppressed = {**empty_suppressed(), **current.suppressed}
            held = suppressed.get(counter)
            held = held if isinstance(held, dict) else {}
            named = held.get("refs")
            refs = [str(r) for r in named if r] if isinstance(named, list) else []
            if once and ref and ref in refs:
                return
            if ref and ref not in refs and len(refs) < REFS_KEPT:
                refs.append(ref)
            count = held.get("count")
            suppressed[counter] = {
                "count": (count if isinstance(count, int) else 0) + 1,
                "refs": refs,
            }
            _write(replace(current, suppressed=suppressed))
    except Exception:  # noqa: BLE001 - a count never stops the skip it counts
        if not _note_failure_logged:
            _note_failure_logged = True
            logger.warning("could not count what the incident held back", exc_info=True)


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """The incident file's lock (``.incident.lock``, ``record_files.locked``), held across each
    read and write of it: a start, a Resume, a note's flag and a count."""
    from personalclaw import record_files

    with record_files.locked(_incident_path()):
        yield


def history(cursor: str = "") -> tuple[list[dict[str, Any]], str]:
    """One page of the incidents, newest first, :data:`HISTORY_PAGE` at most, each with its newest
    note as ``note`` ('' when it has none), and the cursor of the next page ('' at the end).
    *cursor* is the id of the last incident the page before ended on."""
    incidents, notes = _read_history()
    newest = sorted(incidents.values(), key=lambda row: str(row.get("ended_at", "")), reverse=True)
    start = 0
    if cursor:
        ids = [str(row.get("id", "")) for row in newest]
        start = ids.index(cursor) + 1 if cursor in ids else len(ids)
    page = newest[start : start + HISTORY_PAGE]
    items = [{**row, "note": notes.get(str(row.get("id", "")), {}).get("note", "")} for row in page]
    more = start + HISTORY_PAGE < len(newest)
    return items, (str(page[-1].get("id", "")) if more and page else "")


def add_note(incident_id: str, note: str) -> dict[str, Any] | None:
    """Append the owner's *note* on the incident *incident_id* to the history, and return its line;
    None when the history holds no such incident. Raises ``ValueError`` for a note that is empty
    or longer than :data:`NOTE_MAX` characters once trimmed, and ``OSError`` when the line cannot
    be written. A note is kept as data: nothing hands it to an agent."""
    text = str(note or "").strip()
    if not text or len(text) > NOTE_MAX:
        raise ValueError(f"A note is 1 to {NOTE_MAX} characters.")
    incidents, _notes = _read_history()
    if incident_id not in incidents:
        return None
    from personalclaw.ids import new_id

    line = {
        "v": 1,
        "type": "note",
        "id": new_id("note"),
        "incident": incident_id,
        "note": text,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    why = _append(line)
    if why:
        raise OSError(why)
    with _locked():
        current = _read_file()
        if current.history_unwritten:
            _write(replace(current, history_unwritten=""))
    _audit("incident_reviewed", reason=incident_id)
    return line


def _incident_line(current: IncidentState, *, resumed_by: str) -> dict[str, Any]:
    from personalclaw.ids import new_id

    return {
        "v": 1,
        "type": "incident",
        "id": new_id("inc"),
        "started_at": current.started_at,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "reason": current.reason,
        "activated_by": current.activated_by,
        "app": current.app,
        "resumed_by": resumed_by,
        "suppressed": {**empty_suppressed(), **(current.suppressed or {})},
    }


def _history_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _HISTORY_FILENAME


def _append(line: dict[str, Any]) -> str:
    """Append *line* to the history, then trim it to :data:`HISTORY_KEEP` by time. '' once it is
    written, else why not; a trim that fails leaves the line written and is only logged. The
    caller clears the Doctor's ``history_unwritten`` once a line is written, under the
    incident's lock."""
    from personalclaw import bounded_log

    path = _history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with bounded_log.appending(path):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        why = f"The incident history could not be written: {exc.strerror or exc}."
        logger.warning("%s", why)
        return why
    try:
        bounded_log.trim_jsonl(path, HISTORY_KEEP, at=_line_time)
    except OSError:
        logger.warning("the incident history could not be trimmed", exc_info=True)
    return ""


def _line_time(row: Any) -> Any:
    """A history line's time: an incident's end, or a note's."""
    if not isinstance(row, dict):
        return ""
    return row.get("ended_at") or row.get("at") or ""


def _read_history() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The history's incidents by id, and each incident's newest note, by the incident's id."""
    incidents: dict[str, dict[str, Any]] = {}
    notes: dict[str, dict[str, Any]] = {}
    try:
        text = _history_path().read_text(encoding="utf-8")
    except OSError:
        return incidents, notes
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "incident" and row.get("id"):
            incidents[str(row["id"])] = row
        elif row.get("type") == "note" and row.get("incident"):
            key = str(row["incident"])
            if str(row.get("at", "")) >= str(notes.get(key, {}).get("at", "")):
                notes[key] = row
    return incidents, notes


async def watch(
    on_change: Callable[[IncidentState], None], *, interval: float | None = None
) -> None:
    """Call *on_change* each time the switch is found to have moved, until the gateway stops.

    A flip is a change of ``active`` or of the reason, made here or by the CLI in another
    process (the mtime refresh in :func:`get_incident` sees both). A watcher whose callback
    raises keeps watching: a page that missed one change must still hear the next.
    """
    from personalclaw import shutdown_event

    last = get_incident()
    while not shutdown_event.is_set():
        await asyncio.sleep(WATCH_INTERVAL_SECS if interval is None else interval)
        current = get_incident()
        if (current.active, current.reason) == (last.active, last.reason):
            continue
        last = current
        try:
            on_change(current)
        except Exception:
            logger.debug("incident watch callback failed", exc_info=True)


def _write(state: IncidentState) -> None:
    global _mirror, _mirror_mtime
    path = _incident_path()
    atomic_write(
        path,
        json.dumps(
            {
                "active": state.active,
                "reason": state.reason,
                "started_at": state.started_at,
                "activated_by": state.activated_by,
                "app": state.app,
                "suppressed": state.suppressed,
                "history_unwritten": state.history_unwritten,
            }
        ),
    )
    _mirror = state
    try:
        _mirror_mtime = path.stat().st_mtime
    except OSError:
        _mirror_mtime = -1.0


def _audit(operation: str, *, reason: str = "") -> None:
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="incident",
            operation=f"guardrails.{operation}",
            outcome="ok",
            source="guardrails",
            resources=reason[:200] if reason else "",
        )
    except Exception:
        logger.debug("incident SEL audit failed", exc_info=True)


def reset_incident_mirror() -> None:
    """Drop the in-process mirror — invoked by an autouse test fixture so incident
    state from one test never leaks into the next (the SEL/breaker discipline)."""
    global _mirror, _mirror_mtime
    _mirror = None
    _mirror_mtime = -1.0
