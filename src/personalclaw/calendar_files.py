"""The calendars in the folders the user shares: found, read, and turned into the events on a day.

Asked where a child's Saturday game was, the agent searched the notes folder, found the time in a
weekly note and said it did not know the place, while the calendar file in a folder the user had
shared with it held the event with its place. Nothing told the agent a calendar was there, and
nothing read one: the morning brief that did read it opened the raw ``.ics`` text, where a weekly
event is one entry and its rule, and where an all-day event's end is the day AFTER its last.

So a calendar is a file the file tools already reach (``file_scope``): an ``.ics`` file in an
allowed working directory, or in a knowledge source that takes ``.ics`` files in. :func:`find` names
them, which the turn's ``[file places]`` note passes on, and :func:`occurrences` lists what is on
them between two days, each repeat at its own time in its own zone, shown in the user's.

What a calendar file says is read as RFC 5545 says it: lines unfolded, text unescaped, a start in
its ``TZID`` zone, in UTC, or floating (the calendar's ``X-WR-TIMEZONE``, else the user's zone). A
repeating event's first start always counts, its ``RRULE`` (daily, weekly, monthly or yearly, with
its interval, count, end and BY-parts) adds the rest, ``RDATE`` adds more, ``EXDATE`` takes some
away, and an entry with a ``RECURRENCE-ID`` replaces the one repeat it names, or cancels it. A rule
this reader does not expand (an hourly one, a week number) is said, never guessed at: the event's
first start is listed with the reason.

Nothing here writes, and a file is read only when it is still where the scope allows; a file that
is too large, unreadable or not a calendar is named with the reason rather than skipped quietly.
"""

from __future__ import annotations

import calendar as _monthdays
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Iterator, Union

from personalclaw import timezones
from personalclaw.home_paths import from_home

logger = logging.getLogger(__name__)

#: What a calendar the agent is told about is, in the words its note and the tool use.
KIND = "an .ics calendar file"

#: The file a calendar is.
SUFFIX = ".ics"

#: The largest calendar file read. Years of a busy calendar export to a few megabytes.
MAX_BYTES = 8 * 1024 * 1024

#: How far below a shared folder a calendar is looked for, and how many entries one folder's
#: search may visit: the search runs before a model request, so it is bounded however large the
#: folder is, and each folder has its own bound so a large code folder cannot hide the next one.
_DEPTH = 3
_FOLDER_BUDGET = 2000

#: How long a search stands before the next request searches again. The tool searches afresh.
_FIND_TTL_SECS = 30.0

#: The repeats one event may produce before its expansion stops, whatever its rule says. A rule
#: is walked from its first start only when it has a count (else from just before the window),
#: and daily repeats from 1900 to now are under 50,000, so only a malformed file reaches this.
_MAX_REPEATS = 100_000

Moment = Union[date, datetime]

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_FREQUENCIES = {"DAILY": "daily", "WEEKLY": "weekly", "MONTHLY": "monthly", "YEARLY": "yearly"}
_UNITS = {"DAILY": "days", "WEEKLY": "weeks", "MONTHLY": "months", "YEARLY": "years"}
#: Rule parts this reader does not expand. A rule using one is listed by its first start alone.
_UNEXPANDED = ("BYYEARDAY", "BYWEEKNO", "BYHOUR", "BYMINUTE", "BYSECOND")

_LINE_BREAK = re.compile(r"\r\n|\n|\r")
_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$")
_DURATION = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_BYDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
_ESCAPES = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}


@dataclass(frozen=True)
class CalendarRef:
    """A calendar file the user shares: its real path, the path as the user reads it (from ``~``
    in the home), and its name (the calendar's own ``X-WR-CALNAME``, else the file's name)."""

    path: str
    shown: str
    name: str


@dataclass(frozen=True)
class Rule:
    """An ``RRULE`` this reader expands. ``byday`` holds ``(ordinal, weekday)``, 0 for none."""

    freq: str
    interval: int = 1
    count: int | None = None
    until: Moment | None = None
    byday: tuple[tuple[int, int], ...] = ()
    bymonthday: tuple[int, ...] = ()
    bymonth: tuple[int, ...] = ()
    bysetpos: tuple[int, ...] = ()
    wkst: int = 0

    def describe(self) -> str:
        """How often it repeats, in words: ``repeats weekly``, ``repeats every 2 weeks``."""
        if self.interval == 1:
            return f"repeats {_FREQUENCIES[self.freq]}"
        return f"repeats every {self.interval} {_UNITS[self.freq]}"


@dataclass(frozen=True)
class Event:
    """One ``VEVENT``: what it says, when it first starts and ends, and how it repeats."""

    uid: str
    summary: str
    location: str
    description: str
    start: Moment
    end: Moment
    rule: Rule | None = None
    #: Why a rule the event carries is not expanded, ``""`` when it is (or it has none).
    unexpanded: str = ""
    rdates: tuple[Moment, ...] = ()
    exdates: frozenset = frozenset()
    #: The repeat this entry replaces (its ``RECURRENCE-ID``), ``None`` for a master or single one.
    replaces: Any = None
    cancelled: bool = False
    #: Said beside the event when its zone could not be read, naming the zone its times are read in.
    zone_note: str = ""


@dataclass(frozen=True)
class Calendar:
    """A calendar file as it was read: its events, or why it could not be read."""

    ref: CalendarRef
    events: tuple[Event, ...] = ()
    problem: str = ""


@dataclass(frozen=True)
class Occurrence:
    """One time an event happens: its start and end (dates for an all-day one, the end a day
    after its last), shown in the user's zone."""

    start: Moment
    end: Moment
    event: Event
    calendar: str


# ── finding the calendars ─────────────────────────────────────────────────────────────────────

_found: dict[tuple, tuple[float, tuple[CalendarRef, ...]]] = {}
_names: dict[str, tuple[tuple[int, int], str | None]] = {}
_loaded: dict[str, tuple[tuple[int, int, str], Calendar]] = {}


def find(scope: Any, *, fresh: bool = False) -> list[CalendarRef]:
    """The calendar files in the folders the user shares, as *scope* (a ``FileScope``) reaches
    them: its allowed working directories and knowledge sources, never the session's own folder,
    and only what a read there may open (``FileScope.admits``). Each folder is searched to a few
    levels and a bounded number of entries, hidden and dependency folders left out.

    A search stands for a short while, so a turn's several requests search once; *fresh* searches
    again, as a call to the tool does."""
    from personalclaw.file_scope import ALLOWED, SOURCE

    shared = [p for p in scope.places if p.kind in (ALLOWED, SOURCE)]
    key = tuple((p.root, p.kind, repr(sorted((p.spec or {}).items()))) for p in shared)
    now = time.monotonic()
    standing = _found.get(key)
    if standing is not None and not fresh and standing[0] > now:
        return list(standing[1])
    refs: list[CalendarRef] = []
    seen: set[str] = set()
    for place in shared:
        for path in _walk(place.root):
            real = scope.admits(path)
            if real is None or real in seen:
                continue
            seen.add(real)
            name = calendar_name(real)
            if name is not None:
                refs.append(CalendarRef(real, from_home(real), name))
    _found[key] = (now + _FIND_TTL_SECS, tuple(refs))
    while len(_found) > 32:
        _found.pop(next(iter(_found)))
    return refs


def _walk(root: str) -> Iterator[str]:
    """The ``.ics`` files under *root*, nearest first, to :data:`_DEPTH` folders down."""
    from personalclaw.knowledge_providers.dir_source import SKIP_DIRS

    budget = _FOLDER_BUDGET
    level = [root]
    for _depth in range(_DEPTH + 1):
        below: list[str] = []
        for folder in level:
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        budget -= 1
                        if budget < 0:
                            return
                        if entry.name.startswith("."):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name not in SKIP_DIRS:
                                    below.append(entry.path)
                            elif entry.name.lower().endswith(SUFFIX) and entry.is_file():
                                yield entry.path
                        except OSError:
                            continue
            except OSError:
                continue
        level = below


def _signature(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def calendar_name(real: str) -> str | None:
    """The calendar's name from its header, or ``None`` when the file is not a calendar."""
    signature = _signature(real)
    if signature is None:
        return None
    known = _names.get(real)
    if known is not None and known[0] == signature:
        return known[1]
    name: str | None = None
    try:
        with open(real, "rb") as fh:
            head = fh.read(64 * 1024).decode("utf-8-sig", errors="replace")
    except OSError:
        head = ""
    lines = _unfold(head)
    if any(line.strip().upper() == "BEGIN:VCALENDAR" for line in lines[:8]):
        name = ""
        for line in lines:
            parsed = _content_line(line)
            if parsed is None:
                continue
            if parsed[0] == "BEGIN" and parsed[2].strip().upper() == "VEVENT":
                break
            if parsed[0] == "X-WR-CALNAME":
                name = " ".join(_text(parsed[2]).split())
                break
        name = name or os.path.splitext(os.path.basename(real))[0]
    _names[real] = (signature, name)
    while len(_names) > 256:
        _names.pop(next(iter(_names)))
    return name


# ── reading one ───────────────────────────────────────────────────────────────────────────────


def load(ref: CalendarRef, zone: tzinfo) -> Calendar:
    """*ref*'s events, read now (a file unchanged since its last read is not parsed again), or the
    reason it could not be read. A floating time is read in the calendar's own zone, else *zone*,
    the user's."""
    found = _signature(ref.path)
    if found is None:
        return Calendar(ref, problem="it could not be opened")
    if found[1] > MAX_BYTES:
        return Calendar(ref, problem=f"it is over {MAX_BYTES // (1024 * 1024)} MB")
    # A floating time is read in the user's zone, so a parse holds for the zone it was read in.
    signature = (*found, str(zone))
    cached = _loaded.get(ref.path)
    if cached is not None and cached[0] == signature and cached[1].ref == ref:
        return cached[1]
    try:
        with open(ref.path, "rb") as fh:
            text = fh.read(MAX_BYTES + 1).decode("utf-8-sig", errors="replace")
    except OSError as exc:
        return Calendar(ref, problem=f"it could not be read ({exc.strerror or exc})")
    calendar = parse(text, ref, zone)
    _loaded[ref.path] = (signature, calendar)
    while len(_loaded) > 32:
        _loaded.pop(next(iter(_loaded)))
    return calendar


def parse(text: str, ref: CalendarRef, zone: tzinfo) -> Calendar:
    """The events *text*, an iCalendar file's content, describes."""
    lines = _unfold(text)
    if not any(line.strip().upper() == "BEGIN:VCALENDAR" for line in lines[:8]):
        return Calendar(ref, problem="it is not an iCalendar file")
    stack: list[str] = []
    header: dict[str, str] = {}
    raw_events: list[dict[str, list[tuple[dict[str, str], str]]]] = []
    current: dict[str, list[tuple[dict[str, str], str]]] | None = None
    for line in lines:
        parsed = _content_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN":
            stack.append(value.strip().upper())
            if stack == ["VCALENDAR", "VEVENT"]:
                current = {}
            continue
        if name == "END":
            component = value.strip().upper()
            if stack and stack[-1] == component:
                stack.pop()
            if component == "VEVENT" and current is not None and stack == ["VCALENDAR"]:
                raw_events.append(current)
                current = None
            continue
        if stack == ["VCALENDAR"]:
            header.setdefault(name, value)
        elif current is not None and stack == ["VCALENDAR", "VEVENT"]:
            current.setdefault(name, []).append((params, value))
    zones: dict[str, tzinfo | None] = {}
    own = _zone(header.get("X-WR-TIMEZONE", ""), zones)
    floating = own or zone
    events = []
    for raw in raw_events:
        try:
            event = _event(raw, floating, zones)
        except (ValueError, OverflowError):
            logger.debug("calendar %s: an event it could not read was left out", ref.shown)
            continue
        if event is not None:
            events.append(event)
    return Calendar(ref, tuple(events))


def _unfold(text: str) -> list[str]:
    """Content lines with their folds joined (a line that starts with a space or a tab goes on)."""
    lines: list[str] = []
    for raw in _LINE_BREAK.split(text):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _content_line(line: str) -> tuple[str, dict[str, str], str] | None:
    """``NAME;PARAM=VALUE:value`` as ``(NAME, {PARAM: VALUE}, value)``: a quoted parameter may hold
    a colon or a semicolon."""
    quoted = False
    cuts: list[int] = []
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == ";":
            cuts.append(i)
        elif not quoted and ch == ":":
            head, value = line[:i], line[i + 1 :]
            break
    else:
        return None
    pieces = [head[a:b] for a, b in zip([0, *(c + 1 for c in cuts)], [*cuts, len(head)])]
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, _, val = piece.partition("=")
        params[key.strip().upper()] = val.strip().strip('"')
    return pieces[0].strip().upper(), params, value


def _text(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), value)


def _zone(tzid: str, zones: dict[str, tzinfo | None]) -> tzinfo | None:
    """The zone a ``TZID`` names, or ``None``. A name with a prefix before its IANA key (as some
    applications write it, ``/vendor/2005/America/Toronto``) is read by the key it ends with."""
    text = (tzid or "").strip().strip('"')
    if not text:
        return None
    if text not in zones:
        parts = [p for p in text.split("/") if p]
        found: tzinfo | None = None
        for i in range(len(parts)):
            try:
                found = timezones.zone_or_raise("/".join(parts[i:]))
                break
            except (timezones.UnknownTimeZone, timezones.TimeZoneDatabaseUnavailable):
                continue
        zones[text] = found
    return zones[text]


def _moment(
    params: dict[str, str], value: str, floating: tzinfo, zones: dict[str, tzinfo | None]
) -> tuple[Moment, str]:
    """A date or date-time value, and a note when its ``TZID`` could not be read (its time is
    then read as floating). Raises ``ValueError`` for a value that is neither."""
    m = _STAMP.match(value.strip())
    if m is None:
        raise ValueError(f"not a date or date-time: {value!r}")
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if m.group(4) is None or params.get("VALUE", "").upper() == "DATE":
        return date(year, month, day), ""
    hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
    note = ""
    if m.group(7):
        tz: tzinfo = timezone.utc
    elif params.get("TZID"):
        named = _zone(params["TZID"], zones)
        if named is None:
            note = (
                f"its time zone {params['TZID']!r} is not one this reader knows, so its times "
                f"are read as {floating}"
            )
        tz = named or floating
    else:
        tz = floating
    return datetime(year, month, day, hour, minute, second, tzinfo=tz), note


def _moments(
    entries: list[tuple[dict[str, str], str]], floating: tzinfo, zones: dict[str, tzinfo | None]
) -> list[Moment]:
    """Every value of a list-valued property (``EXDATE``, ``RDATE``), a period left out."""
    out: list[Moment] = []
    for params, value in entries:
        if params.get("VALUE", "").upper() == "PERIOD":
            continue
        for piece in value.split(","):
            if piece.strip() and "/" not in piece:
                try:
                    out.append(_moment(params, piece, floating, zones)[0])
                except ValueError:
                    continue
    return out


def _duration(value: str) -> timedelta:
    m = _DURATION.match(value.strip().upper())
    if m is None:
        raise ValueError(f"not a duration: {value!r}")
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in m.groups()[1:])
    span = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    return -span if m.group(1) == "-" else span


def _key(moment: Moment) -> Moment:
    """What two values of one instant share: a date as it is, a date-time as its UTC instant."""
    if isinstance(moment, datetime):
        return moment.astimezone(timezone.utc)
    return moment


def _event(
    props: dict[str, list[tuple[dict[str, str], str]]],
    floating: tzinfo,
    zones: dict[str, tzinfo | None],
) -> Event | None:
    if not props.get("DTSTART"):
        return None
    start, note = _moment(*props["DTSTART"][0], floating, zones)
    end: Moment | None = None
    if props.get("DTEND"):
        end = _moment(*props["DTEND"][0], floating, zones)[0]
    elif props.get("DURATION"):
        span = _duration(props["DURATION"][0][1])
        end = start + (timedelta(days=span.days or 1) if not isinstance(start, datetime) else span)
    if isinstance(start, datetime) and end is not None and not isinstance(end, datetime):
        end = datetime(end.year, end.month, end.day, tzinfo=start.tzinfo)
    elif not isinstance(start, datetime) and isinstance(end, datetime):
        end = end.date()
    if end is None or end < start:
        end = start if isinstance(start, datetime) else start + timedelta(days=1)
    if not isinstance(start, datetime) and end == start:
        end = start + timedelta(days=1)

    def text(name: str) -> str:
        entries = props.get(name) or []
        return _text(entries[0][1]).strip() if entries else ""

    rule: Rule | None = None
    unexpanded = ""
    if props.get("RRULE"):
        rule, unexpanded = _rule(props["RRULE"][0][1], start, floating, zones)
    replaces = None
    if props.get("RECURRENCE-ID"):
        params, value = props["RECURRENCE-ID"][0]
        replaces = _key(_moment(params, value, floating, zones)[0])
    return Event(
        uid=text("UID"),
        summary=text("SUMMARY") or "(no title)",
        location=text("LOCATION"),
        description=text("DESCRIPTION"),
        start=start,
        end=end,
        rule=rule,
        unexpanded=unexpanded,
        rdates=tuple(_moments(props.get("RDATE") or [], floating, zones)),
        exdates=frozenset(_key(m) for m in _moments(props.get("EXDATE") or [], floating, zones)),
        replaces=replaces,
        cancelled=text("STATUS").upper() == "CANCELLED",
        zone_note=note,
    )


def _rule(
    value: str, start: Moment, floating: tzinfo, zones: dict[str, tzinfo | None]
) -> tuple[Rule | None, str]:
    """The ``RRULE`` *value* as a :class:`Rule`, or ``None`` and why it is not expanded."""
    parts: dict[str, str] = {}
    for piece in value.split(";"):
        key, _, val = piece.partition("=")
        if key.strip():
            parts[key.strip().upper()] = val.strip()
    freq = parts.get("FREQ", "").upper()
    if freq not in _FREQUENCIES:
        return None, f"it repeats by a rule this reader does not expand ({value.strip()})"
    beyond = [p for p in _UNEXPANDED if p in parts]
    if beyond:
        return None, f"it repeats by a rule this reader does not expand ({value.strip()})"
    try:
        interval = max(1, int(parts.get("INTERVAL") or 1))
        count = int(parts["COUNT"]) if parts.get("COUNT") else None
        until = None
        if parts.get("UNTIL"):
            until_zone = start.tzinfo if isinstance(start, datetime) and start.tzinfo else floating
            until = _moment({}, parts["UNTIL"], until_zone or floating, zones)[0]
        byday = []
        for piece in filter(None, parts.get("BYDAY", "").upper().split(",")):
            m = _BYDAY.match(piece.strip())
            if m is None:
                raise ValueError(piece)
            byday.append((int(m.group(1) or 0), _WEEKDAYS[m.group(2)]))

        def numbers(name: str) -> tuple[int, ...]:
            return tuple(int(n) for n in parts.get(name, "").split(",") if n.strip())

        bymonth = numbers("BYMONTH")
        if any(not 1 <= m <= 12 for m in bymonth):
            raise ValueError("BYMONTH")
        rule = Rule(
            freq=freq,
            interval=interval,
            count=count,
            until=until,
            byday=tuple(byday),
            bymonthday=numbers("BYMONTHDAY"),
            bymonth=bymonth,
            bysetpos=numbers("BYSETPOS"),
            wkst=_WEEKDAYS.get(parts.get("WKST", "MO").upper(), 0),
        )
    except (ValueError, KeyError):
        return None, f"its repeat rule could not be read ({value.strip()})"
    return rule, ""


# ── what is on the calendar between two days ─────────────────────────────────────────────────


def occurrences(
    calendar: Calendar, first_day: date, last_day: date, zone: tzinfo
) -> list[Occurrence]:
    """Every time an event on *calendar* happens between *first_day* and *last_day* (both in the
    user's zone, *zone*, and both included): an event that started earlier and is still going
    counts. Timed ones are shown in *zone*; an all-day one keeps its dates."""
    window_start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=zone)
    after = last_day + timedelta(days=1)
    window_end = datetime(after.year, after.month, after.day, tzinfo=zone)
    replaced: dict[str, set] = {}
    for event in calendar.events:
        if event.replaces is not None:
            replaced.setdefault(event.uid, set()).add(event.replaces)
    out: list[Occurrence] = []
    for event in calendar.events:
        if event.cancelled:
            continue
        skip = replaced.get(event.uid, set()) if event.replaces is None else set()
        span = _span(event)
        for start in _instances(event, window_start, window_end, span):
            if _key(start) in event.exdates or _key(start) in skip:
                continue
            # A date excluded from a timed event takes out the repeat on that day.
            if isinstance(start, datetime) and start.date() in event.exdates:
                continue
            if isinstance(start, datetime):
                end = start + span
                if not (start < window_end and (end > window_start or start >= window_start)):
                    continue
                out.append(
                    Occurrence(
                        start.astimezone(zone), end.astimezone(zone), event, calendar.ref.name
                    )
                )
            elif start <= last_day and start + span > first_day:
                out.append(Occurrence(start, start + span, event, calendar.ref.name))
    out.sort(key=lambda o: (sort_key(o.start, zone), o.event.summary))
    return out


def _span(event: Event) -> timedelta:
    if isinstance(event.start, datetime):
        return event.end - event.start
    return timedelta(days=max(1, (event.end - event.start).days))  # type: ignore[operator]


def sort_key(moment: Moment, zone: tzinfo) -> datetime:
    if isinstance(moment, datetime):
        return moment
    return datetime(moment.year, moment.month, moment.day, tzinfo=zone)


def _instances(
    event: Event, window_start: datetime, window_end: datetime, span: timedelta
) -> Iterator[Moment]:
    """The event's starts that can reach the window, earliest first: its first start, the repeats
    its rule makes, and its extra dates."""
    first = event.start
    if isinstance(first, datetime):
        own = first.tzinfo or timezone.utc
        reach_from = (window_start.astimezone(own) - span).date() - timedelta(days=1)
        last = window_end.astimezone(own).date() + timedelta(days=1)
    else:
        reach_from = window_start.date() - timedelta(days=span.days + 1)
        last = window_end.date() + timedelta(days=1)
    seen: set = set()
    for start in [first, *_rule_starts(event, reach_from, last), *event.rdates]:
        day = start.date() if isinstance(start, datetime) else start
        # A start before *reach_from* ends before the window opens, however long it runs.
        if day < reach_from or day > last:
            continue
        key = _key(start)
        if key in seen:
            continue
        seen.add(key)
        yield start


def _rule_starts(event: Event, reach_from: date, last: date) -> Iterator[Moment]:
    """The starts the event's rule adds after its first one (which always counts), in order, from
    *reach_from* to *last* (days in the event's own zone), within the rule's count and end.

    A rule with a count is walked from its first start, since every repeat counts; the repeats
    before *reach_from* are counted by their day alone, so only the ones that can be shown are
    made into times in the event's zone."""
    first = event.start
    rule = event.rule
    if rule is None or (rule.count is not None and rule.count <= 1):
        return
    first_day = first.date() if isinstance(first, datetime) else first
    until = rule.until
    if isinstance(until, datetime):
        own = first.tzinfo if isinstance(first, datetime) else None
        until_day: date | None = until.astimezone(own).date() if own else until.date()
    else:
        until_day = until
    made = 1
    # Repeats before the window are skipped only when nothing counts them.
    skip_to = reach_from if rule.count is None else None
    for n, day in enumerate(_days(rule, first_day, last, skip_to)):
        if n >= _MAX_REPEATS:
            logger.warning(
                "calendar: the repeats of %r stop after %d; its later ones are not listed",
                event.summary,
                n,
            )
            return
        if until_day is not None and day > until_day:
            return
        start: Moment | None = None
        if day >= reach_from or day == first_day or day == until_day:
            start = _at(day, first)
            if start <= first:
                continue
            if not _within_until(start, until):
                return
        if start is not None and day >= reach_from:
            yield start
        made += 1
        if rule.count is not None and made >= rule.count:
            return


def _at(day: date, first: Moment) -> Moment:
    """A repeat's start on *day*: at the first start's wall-clock time in its own zone."""
    if not isinstance(first, datetime):
        return day
    return datetime(
        day.year, day.month, day.day, first.hour, first.minute, first.second, tzinfo=first.tzinfo
    )


def _within_until(start: Moment, until: Moment | None) -> bool:
    if until is None:
        return True
    if isinstance(start, datetime) and isinstance(until, datetime):
        return start <= until
    if isinstance(start, datetime):
        return start.date() <= until
    if isinstance(until, datetime):
        return start <= until.date()
    return start <= until


def _days(rule: Rule, first: date, last: date, skip_to: date | None) -> Iterator[date]:
    """The days *rule* makes from *first* on, in order, up to *last*. With *skip_to*, periods
    wholly before it are not walked."""
    if rule.freq == "DAILY":
        day = first
        if skip_to is not None and skip_to > first:
            day += timedelta(days=((skip_to - first).days // rule.interval) * rule.interval)
        while day <= last:
            if _day_kept(day, rule):
                yield day
            day += timedelta(days=rule.interval)
    elif rule.freq == "WEEKLY":
        week = first - timedelta(days=(first.weekday() - rule.wkst) % 7)
        if skip_to is not None and skip_to > week:
            week += timedelta(weeks=((skip_to - week).days // 7 // rule.interval) * rule.interval)
        weekdays = sorted({wd for _, wd in rule.byday} or {first.weekday()})
        while week <= last:
            days = sorted(week + timedelta(days=(wd - rule.wkst) % 7) for wd in weekdays)
            if rule.bymonth:
                days = [d for d in days if d.month in rule.bymonth]
            for day in _positions(days, rule.bysetpos):
                if first <= day <= last:
                    yield day
            week += timedelta(weeks=rule.interval)
    elif rule.freq == "MONTHLY":
        index = first.year * 12 + first.month - 1
        if skip_to is not None:
            target = skip_to.year * 12 + skip_to.month - 1
            if target > index:
                index += ((target - index) // rule.interval) * rule.interval
        while date(index // 12, index % 12 + 1, 1) <= last:
            year, month = index // 12, index % 12 + 1
            if not rule.bymonth or month in rule.bymonth:
                for day in _positions(_month_days(year, month, rule, first), rule.bysetpos):
                    if first <= day <= last:
                        yield day
            index += rule.interval
    else:
        year = first.year
        if skip_to is not None and skip_to.year > year:
            year += ((skip_to.year - year) // rule.interval) * rule.interval
        while date(year, 1, 1) <= last:
            for day in _positions(_year_days(year, rule, first), rule.bysetpos):
                if first <= day <= last:
                    yield day
            year += rule.interval


def _day_kept(day: date, rule: Rule) -> bool:
    """Whether a daily rule's BY-parts keep *day* (they limit a daily rule; they do not add)."""
    if rule.bymonth and day.month not in rule.bymonth:
        return False
    if rule.bymonthday and day.day not in _month_numbers(day.year, day.month, rule.bymonthday):
        return False
    return not rule.byday or day.weekday() in {wd for _, wd in rule.byday}


def _month_numbers(year: int, month: int, monthdays: tuple[int, ...]) -> set[int]:
    """``BYMONTHDAY`` values as days of this month (``-1`` is its last)."""
    length = _monthdays.monthrange(year, month)[1]
    days = {n if n > 0 else length + n + 1 for n in monthdays}
    return {d for d in days if 1 <= d <= length}


def _weekdays_in(first: date, length: int, ordinal: int, weekday: int) -> list[date]:
    """The *weekday*s in the *length* days from *first*: all of them for ordinal 0, else the
    ordinal-th (from the end when negative)."""
    offset = (weekday - first.weekday()) % 7
    days = [first + timedelta(days=d) for d in range(offset, length, 7)]
    if ordinal == 0:
        return days
    if 1 <= abs(ordinal) <= len(days):
        return [days[ordinal - 1 if ordinal > 0 else ordinal]]
    return []


def _month_days(year: int, month: int, rule: Rule, first: date) -> list[date]:
    length = _monthdays.monthrange(year, month)[1]
    if rule.bymonthday:
        days = [date(year, month, d) for d in sorted(_month_numbers(year, month, rule.bymonthday))]
        if rule.byday:
            days = [d for d in days if d.weekday() in {wd for _, wd in rule.byday}]
        return days
    if rule.byday:
        found = set()
        for ordinal, weekday in rule.byday:
            found.update(_weekdays_in(date(year, month, 1), length, ordinal, weekday))
        return sorted(found)
    return [date(year, month, first.day)] if first.day <= length else []


def _year_days(year: int, rule: Rule, first: date) -> list[date]:
    if rule.bymonth:
        days: list[date] = []
        for month in sorted(set(rule.bymonth)):
            if rule.byday or rule.bymonthday:
                days.extend(_month_days(year, month, rule, first))
            elif first.day <= _monthdays.monthrange(year, month)[1]:
                days.append(date(year, month, first.day))
        return sorted(days)
    if rule.bymonthday:
        return [d for month in range(1, 13) for d in _month_days(year, month, rule, first)]
    if rule.byday:
        length = 366 if _monthdays.isleap(year) else 365
        found = set()
        for ordinal, weekday in rule.byday:
            found.update(_weekdays_in(date(year, 1, 1), length, ordinal, weekday))
        return sorted(found)
    if first.month == 2 and first.day == 29 and not _monthdays.isleap(year):
        return []
    return [date(year, first.month, first.day)]


def _positions(days: list[date], bysetpos: tuple[int, ...]) -> list[date]:
    """``BYSETPOS``: the positions it names among one period's days, else all of them."""
    if not bysetpos:
        return days
    picked = {days[p - 1 if p > 0 else p] for p in bysetpos if 1 <= abs(p) <= len(days)}
    return sorted(picked)
