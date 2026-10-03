"""The ``calendar_events`` tool: what is on the user's calendars between two days.

A question about plans (what is on Saturday, when the dentist is, where the game is) needs the
events, each repeat at its own time and an all-day event's last day as the day it ends. Read as raw
``.ics`` text, a weekly event is one entry and a rule the model has to expand in its head, and an
all-day event's end is the day after its last, which a model takes for its last day. This tool
reads the calendars the user shares (:mod:`personalclaw.calendar_files`) and answers with the
events, in the user's time zone, so the model reads them rather than reconstructs them.

It reaches what the file tools reach and nothing more: the calendars in the folders the turn's
``[file places]`` note lists, found again at each call, or one ``.ics`` file it names that
``FileScope.resolve`` admits. It only reads, so it asks nobody.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from personalclaw import calendar_files
from personalclaw.file_scope import ALLOWED_SETTING
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

TOOL = "calendar_events"
PROVIDER = "personalclaw-calendar-tools"

#: The longest stretch one call lists, and the most events it shows.
_MAX_DAYS = 366
_MAX_LISTED = 100
#: Days a call that names no end covers: the start and the six after it.
_DEFAULT_DAYS = 7
#: How much of an event's notes a line carries.
_NOTE_CHARS = 160

_NONE_SHARED = (
    "No calendar (.ics) file is in the folders the user shares with PersonalClaw, so there is no "
    f"calendar to read. To share one, the user adds the folder that holds it in {ALLOWED_SETTING}, "
    "or adds it in Knowledge › Sources as a folder source that takes .ics files in."
)


class CalendarToolProvider(ToolProvider):
    """Reads the calendar files in the folders the user shares."""

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def display_name(self) -> str:
        return "Calendar Tools"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=TOOL,
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Read the user's calendars: the calendar (.ics) files in the folders they "
                    "share, which the turn's [file places] note names. Lists the events between "
                    "two days, each with its day, its start and end in the user's time zone, its "
                    "title and its place, repeating events expanded and an all-day event shown "
                    "through its last day. Use it for any question about plans or the schedule: "
                    "what is on a day, when or where something is. Args: optional start and end "
                    "(YYYY-MM-DD, both included; default today and the six days after), optional "
                    "query (words that must all appear in an event's title, place or notes), "
                    "optional calendar (one calendar's name, or the path of an .ics file the "
                    "file tools reach)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "string",
                            "description": "First day, YYYY-MM-DD, in the user's time zone.",
                        },
                        "end": {
                            "type": "string",
                            "description": "Last day, YYYY-MM-DD, included.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Words an event's title, place or notes must hold.",
                        },
                        "calendar": {
                            "type": "string",
                            "description": "One calendar's name, or an .ics file's path.",
                        },
                    },
                },
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if tool_name != TOOL:
            return ToolResult(success=False, error=f"unknown tool: {tool_name}")
        from personalclaw.agents.native.builtin_tools import current_tool_roots
        from personalclaw.file_scope import FileScope, OutOfScope
        from personalclaw.schedule import get_local_tz

        # The zone the request's [CURRENT DATE] line is written in, so "tomorrow" is one day.
        zone_name, zone = get_local_tz()
        today = datetime.now(zone).date()
        try:
            first, last = _days(arguments, today)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        query = str(arguments.get("query") or "").strip()
        # Where this turn's file tools reach, read now, in the call's own context.
        scope = FileScope(current_tool_roots())
        try:
            refs = _calendars(scope, str(arguments.get("calendar") or "").strip())
        except OutOfScope as exc:
            return ToolResult(
                success=False, error=str(exc), recovery_hints=[exc.hint] if exc.hint else []
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if not refs:
            return ToolResult(success=True, output=_NONE_SHARED)
        output = await asyncio.to_thread(_answer, refs, first, last, query, zone, zone_name)
        return ToolResult(success=True, output=output)


def create_calendar_tools_provider(config: dict[str, Any] | None = None) -> CalendarToolProvider:
    """The bundled Calendar Tools app's provider."""
    return CalendarToolProvider()


def _days(arguments: dict[str, Any], today: date) -> tuple[date, date]:
    """The call's first and last day: today and the six after it unless it names them."""

    def day(name: str, default: date) -> date:
        text = str(arguments.get(name) or "").strip()
        if not text:
            return default
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            raise ValueError(
                f"{name} {text!r} is not a day: write it as YYYY-MM-DD (today is {today})"
            ) from None

    first = day("start", today)
    last = day("end", first + timedelta(days=_DEFAULT_DAYS - 1))
    if last < first:
        raise ValueError(f"end ({last}) is before start ({first})")
    if (last - first).days >= _MAX_DAYS:
        raise ValueError(f"ask for at most {_MAX_DAYS} days at a time ({first} to {last} is more)")
    return first, last


def _calendars(scope: Any, named: str) -> list[calendar_files.CalendarRef]:
    """The calendars a call reads: every one the user shares, the one it names, or an ``.ics``
    file at the path it names, admitted as a file tool would admit it."""
    from personalclaw.home_paths import from_home

    shared = calendar_files.find(scope, fresh=True)
    if not named:
        return shared
    chosen = [ref for ref in shared if ref.name.casefold() == named.casefold()]
    if chosen:
        return chosen
    real = scope.resolve(named)
    match = [ref for ref in shared if ref.path == real]
    if match:
        return match
    name = calendar_files.calendar_name(real)
    if name is None:
        listed = ", ".join(repr(ref.name) for ref in shared) or "none"
        raise ValueError(
            f"{named!r} is neither a calendar the user shares (they are: {listed}) nor an "
            "iCalendar file"
        )
    return [calendar_files.CalendarRef(real, from_home(real), name)]


def _answer(
    refs: list[calendar_files.CalendarRef],
    first: date,
    last: date,
    query: str,
    zone: tzinfo,
    zone_name: str,
) -> str:
    """The events on *refs* from *first* to *last*, as the lines the model reads."""
    words = query.casefold().split()
    events: list[calendar_files.Occurrence] = []
    read: list[calendar_files.CalendarRef] = []
    unread: list[str] = []
    for ref in refs:
        calendar = calendar_files.load(ref, zone)
        if calendar.problem:
            unread.append(f"{ref.shown} ({calendar.problem})")
            continue
        try:
            found = calendar_files.occurrences(calendar, first, last, zone)
        except (ValueError, OverflowError) as exc:
            unread.append(f"{ref.shown} (its events could not be listed: {exc})")
            continue
        read.append(ref)
        for occurrence in found:
            event = occurrence.event
            text = f"{event.summary} {event.location} {event.description}".casefold()
            if all(word in text for word in words):
                events.append(occurrence)
    events.sort(key=lambda o: (calendar_files.sort_key(o.start, zone), o.event.summary))
    names = ", ".join(f"{ref.name} ({ref.shown})" for ref in read) or "no calendar"
    when = _stretch(first, last)
    matching = f" matching {query!r}" if query else ""
    lines: list[str] = []
    if events:
        count = f"{len(events)} event{'s' if len(events) != 1 else ''}{matching}"
        lines.append(f"{names}, {when}, times in {zone_name}: {count}.")
        several = len(read) > 1
        for occurrence in events[:_MAX_LISTED]:
            lines.append(_line(occurrence, several))
        if len(events) > _MAX_LISTED:
            lines.append(
                f"… and {len(events) - _MAX_LISTED} more. Ask for fewer days, or add a query."
            )
    else:
        lines.append(f"No events{matching} {_on(first, last)} in {names}.")
    if unread:
        lines.append("Not read: " + "; ".join(unread) + ".")
    return "\n".join(lines)


def _line(occurrence: calendar_files.Occurrence, several: bool) -> str:
    event = occurrence.event
    parts = [f"- {_when(occurrence)}: {_one_line(event.summary)}"]
    if event.location:
        parts.append(f"at {_one_line(event.location)}")
    if event.rule is not None:
        parts.append(event.rule.describe())
    if several:
        parts.append(occurrence.calendar)
    if event.description:
        note = _one_line(event.description)
        parts.append("notes: " + (note[:_NOTE_CHARS] + "…" if len(note) > _NOTE_CHARS else note))
    if event.unexpanded:
        parts.append(f"{event.unexpanded}, so only its first time is listed")
    if event.zone_note:
        parts.append(event.zone_note)
    return " · ".join(parts)


def _when(occurrence: calendar_files.Occurrence) -> str:
    start, end = occurrence.start, occurrence.end
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        through = end - timedelta(days=1) if end > start else start
        return f"{_stretch(start, through)}, all day"
    if end == start:
        return f"{_day(start.date())}, {start:%H:%M}"
    ends_at_midnight = end.date() == start.date() + timedelta(days=1) and end.time() == time(0)
    if end.date() == start.date() or ends_at_midnight:
        return f"{_day(start.date())}, {start:%H:%M}–{end:%H:%M}"
    return f"{_day(start.date())}, {start:%H:%M} – {_day(end.date())}, {end:%H:%M}"


def _day(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b %Y}"


def _stretch(first: date, last: date) -> str:
    """Two days as one stretch: ``Sat 3 Oct 2026``, ``Thu 22 Oct – Sun 1 Nov 2026``."""
    if first == last:
        return _day(first)
    if first.year == last.year:
        return f"{first:%a} {first.day} {first:%b} – {_day(last)}"
    return f"{_day(first)} – {_day(last)}"


def _on(first: date, last: date) -> str:
    """The days a call covered, in a sentence: ``on Sat 3 Oct 2026``, ``from Sat 3 Oct to Fri 9
    Oct 2026``."""
    if first == last:
        return f"on {_day(first)}"
    if first.year == last.year:
        return f"from {first:%a} {first.day} {first:%b} to {_day(last)}"
    return f"from {_day(first)} to {_day(last)}"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())
