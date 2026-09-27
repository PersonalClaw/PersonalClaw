"""A one-time ``when`` read to the instant it names — deterministically, in the owner's zone.

"remind me at 5 pm", "in 20 minutes", "tomorrow at 9am", "on 2026-10-01 at 14:00" each name ONE
instant, and reading them takes a clock and a zone, not a model. So they are read here, with no
model and no network: an explicit phrase is answered the same way every time, and a scheduling
tool works on a box whose model is down.

**Only a phrase that leaves the time to judgement goes to the model** ("tomorrow morning", "later
today", "this weekend", "at 5pm PT"): the reader returns ``None`` for anything it cannot account
for word by word, and the caller asks the model, handing it the clock and the zone
(`nl_to_cron`'s one-time answer). A guess here would be a reminder at a time nobody said.

**A repeating phrase is never one time** (:func:`is_recurring`). "every weekday at 9" names a
cadence; reading its "at 9" as tonight would make the user's schedule fire once and stop.

**The zone is the owner's**, resolved by `personalclaw.timezones` — the configured zone, then the
machine's — unless the phrase names one ("at 5pm UTC", "at 9am Europe/London", an ISO offset).
Abbreviations such as "PT" are not zones (`timezones.UnknownTimeZone` says why), so a phrase
carrying one is left to the model rather than read in the wrong zone.

**An hour with no am/pm** is read the way people mean it: with no day named, the next time that
hour comes round ("at 9" said at 2 pm is tonight's); with a day named, 7-11 is morning, 12 is noon
and 1-6 is afternoon ("tomorrow at 3" is 3 pm). A part of the day ("tonight", "this evening",
"tomorrow morning") settles it outright.
"""

from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo

__all__ = ["OneTime", "is_recurring", "read_one_time"]


@dataclass(frozen=True)
class OneTime:
    """The instant a one-time phrase names, and the zone it was read in."""

    #: Epoch seconds — the `at` a one-time clock spec carries (`triggers.arm`).
    at: float
    #: The zone the phrase was read in: an IANA name, ``UTC``, or an ISO offset like ``-04:00``.
    zone: str
    tz: tzinfo

    def local(self) -> datetime:
        return datetime.fromtimestamp(self.at, self.tz)

    def describe(self, *, now: float | None = None) -> str:
        """The instant as a person says it: ``today, 5:00 PM PDT`` / ``Mon Sep 28, 9:00 AM PDT``.

        What the chat echoes back on creation, so a misread is caught while it is fresh.
        """
        local = self.local()
        today = datetime.fromtimestamp(_time.time() if now is None else float(now), self.tz)
        clock = f"{local:%I:%M %p}".lstrip("0")
        abbrev = local.tzname() or self.zone
        if local.date() == today.date():
            return f"today, {clock} {abbrev}"
        year = f" {local.year}" if local.year != today.year else ""
        return f"{local:%a %b} {local.day}{year}, {clock} {abbrev}"


# ── recurring phrases ─────────────────────────────────────────────────────────

#: Words that make a phrase a CADENCE. Checked as whole words: "every" and "each" name a cadence
#: outright, and a plural day or part of the day ("mondays", "weekdays", "mornings") repeats.
_RECURRING_WORDS = re.compile(
    r"\b(?:every|each|daily|weekly|monthly|hourly|yearly|annually|nightly|biweekly|"
    r"fortnightly|weekdays|weekends|mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|"
    r"sundays|mornings|afternoons|evenings|nights|twice|thrice|repeatedly|recurring|"
    r"(?:once|twice|\d+\s+times)\s+(?:a|an|per)\s+\w+|per\s+(?:day|week|hour|month))\b"
)

#: A raw 5-field cron expression — a cadence by construction.
_CRON_SHAPE = re.compile(r"^\s*(?:[\d*/,\-?LW#]+\s+){4}[\d*/,\-?LW#a-zA-Z]+\s*$")


def is_recurring(text: str) -> bool:
    """Whether *text* names a repeating schedule rather than one time."""
    raw = str(text or "")
    return bool(_CRON_SHAPE.match(raw)) or bool(_RECURRING_WORDS.search(raw.lower()))


# ── the vocabulary ───────────────────────────────────────────────────────────

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH = r"(?P<{g}>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tues": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thurs": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
_WEEKDAY = r"(?P<weekday>" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")"

#: Seconds per unit of a delay. Months and years are left to the model: their length depends on
#: the date they start from.
_UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "wk": 604800,
    "wks": 604800,
    "week": 604800,
    "weeks": 604800,
}
_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "fifty": 50,
    "sixty": 60,
    "ninety": 90,
}
_UNIT = "|".join(sorted(_UNITS, key=len, reverse=True))
#: One ``<amount> <unit>`` of a delay. A digit amount may touch its unit ("1h30m"); a word amount
#: needs a space, or "and" would read as "an" + "d" — a day nobody asked for.
_DELAY_PART = re.compile(
    r"(?:(?<![\d.])(?P<num>\d+(?:\.\d+)?)\s*|(?<![a-z])(?P<word>"
    + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    + rf")\s+)(?P<unit>{_UNIT})(?![a-z])"
)

#: Parts of the day that settle an hour's am/pm. "tonight" and "night" are evening hours; "at
#: night" after midnight is not a thing anyone says to a reminder.
_PM_PARTS = ("afternoon", "evening", "night", "tonight")
_AM_PARTS = ("morning",)

_CLOCK = re.compile(
    r"(?<![\d:])(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?"
    r"(?:\s*o'?clock)?(?![\d:])"
)

#: Words that may surround the parts of a phrase without changing what it says.
_FILLER = re.compile(r"\b(?:at|on|the|of|in|by|for|around|about|@)\b|[,@]")

#: Leading words a caller sometimes leaves on a time ("remind me in 5 minutes").
_LEAD = re.compile(r"^(?:please\s+)?(?:remind me|ping me|nudge me|wake me(?: up)?|check)\s+")

_ZONE_TAIL = re.compile(
    r"^(?P<rest>.*?)[\s,]+(?P<zone>utc|gmt|z|[a-z]+(?:/[a-z0-9_+\-]+)+)$", re.IGNORECASE
)


# ── reading ──────────────────────────────────────────────────────────────────


def read_one_time(text: str, *, now: float | None = None, zone: str = "") -> OneTime | None:
    """The instant an explicit one-time phrase names, or ``None`` when it does not name one.

    ``zone`` is the zone to read a bare wall-clock time in; empty means the owner's
    (`timezones.resolve_zone_name`). ``now`` is epoch seconds, defaulting to the clock.

    ``None`` for a repeating phrase, for one that leaves the time to judgement, and for anything
    with a word this reader cannot account for. An explicit time that has already passed is
    returned as said — the caller says it has passed rather than scheduling something else.
    """
    raw = " ".join(str(text or "").split())
    if not raw or is_recurring(raw):
        return None
    now_ts = _time.time() if now is None else float(now)
    try:
        zone_name, tz = _owner_zone(zone)
    except (ValueError, RuntimeError):  # a typo'd zone, or no tz database to check it against
        return None
    split = _split_zone(raw)
    if split is None:
        return None
    body, named = split
    if named is not None:
        zone_name, tz = named
    phrase = _LEAD.sub("", body.lower()).strip().rstrip(".!?")
    if not phrase:
        return None

    iso = _iso_instant(phrase, tz)
    if iso is not None:
        found, iso_zone = iso
        return OneTime(at=found.timestamp(), zone=iso_zone or zone_name, tz=found.tzinfo or tz)

    delay = _delay_secs(phrase)
    if delay is not None:
        return OneTime(at=now_ts + delay, zone=zone_name, tz=tz)

    named_time = _day_and_clock(phrase, datetime.fromtimestamp(now_ts, tz))
    if named_time is None:
        return None
    return OneTime(at=named_time.timestamp(), zone=zone_name, tz=tz)


def _owner_zone(zone: str) -> tuple[str, tzinfo]:
    """The zone a bare time is read in: *zone*, else the owner's. Raises for a typo'd *zone*."""
    from personalclaw.timezones import resolve_zone, resolve_zone_name

    name, _source = resolve_zone_name(zone)
    return name, resolve_zone(zone)


def _split_zone(raw: str) -> tuple[str, tuple[str, tzinfo] | None] | None:
    """``(raw without its trailing zone, that zone)``; the zone is None when the phrase names none.

    None overall when the phrase ends in something zone-shaped that is not a zone: it is then not
    this reader's to answer, and reading it in the owner's zone instead would move the reminder.
    """
    from personalclaw.timezones import UnknownTimeZone, zone_or_raise

    match = _ZONE_TAIL.match(raw)
    if not match:
        return raw, None
    token = match.group("zone")
    if token.lower() in ("utc", "gmt", "z"):
        return match.group("rest"), ("UTC", timezone.utc)
    for candidate in (token, "/".join(_title(part) for part in token.split("/"))):
        try:
            return match.group("rest"), (candidate, zone_or_raise(candidate))
        except (UnknownTimeZone, RuntimeError):
            continue
    return None


def _title(part: str) -> str:
    """One IANA path segment in its canonical case: ``new_york`` → ``New_York``."""
    return "_".join(word[:1].upper() + word[1:] for word in part.split("_"))


def _iso_instant(phrase: str, tz: tzinfo) -> tuple[datetime, str] | None:
    """An ISO date AND time (``2026-10-01T14:00``, ``2026-10-01 14:00:00Z``), or None.

    A bare date is not one: it names a day and leaves the time to judgement.
    """
    text = phrase.upper() if phrase.endswith("z") else phrase
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[t ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:z|[+\-][\d:]+)?", phrase
    ):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("t", "T", 1))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz), ""
    offset = parsed.utcoffset() or timedelta(0)
    if offset == timedelta(0) and phrase.endswith("z"):
        return parsed, "UTC"
    total = int(offset.total_seconds() // 60)
    sign = "-" if total < 0 else "+"
    return parsed, f"{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"


def _delay_secs(phrase: str) -> float | None:
    """Seconds from now for "in 20 minutes" / "20 minutes from now" / "in an hour and a half"."""
    match = re.fullmatch(r"(?:in|after)\s+(?P<span>.+)", phrase) or re.fullmatch(
        r"(?P<span>.+?)\s+from now", phrase
    )
    if not match:
        return None
    span = match.group("span").strip()
    if span in ("half an hour", "half hour", "a half hour"):
        return 1800.0
    half = 0.0
    if span.endswith(" and a half"):
        span = span[: -len(" and a half")]
        half = 0.5
    total = 0.0
    last_unit = 0
    consumed = 0
    for part in _DELAY_PART.finditer(span):
        between = span[consumed : part.start()]
        if between.strip(" ,") not in ("", "and"):
            return None
        value = float(part["num"]) if part["num"] else float(_NUMBER_WORDS[part["word"]])
        last_unit = _UNITS[part.group("unit")]
        total += value * last_unit
        consumed = part.end()
    if not last_unit or span[consumed:].strip(" ,"):
        return None
    total += half * last_unit
    return total if total > 0 else None


def _day_and_clock(phrase: str, now: datetime) -> datetime | None:
    """A named day (or none) and a clock time, as an instant in *now*'s zone. None unless every
    word of *phrase* is accounted for and a clock time is among them."""
    rest = phrase
    day: date | None = None
    explicit_year = False
    relative = ""  # today | tomorrow | weekday | next-weekday
    weekday = -1
    part = ""

    iso_day = re.search(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b", rest)
    named_day = re.search(
        r"\b(?:" + _MONTH.format(g="mon1") + r"\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?"
        r"(?:,?\s+(?P<y1>\d{4}))?"
        r"|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
        + _MONTH.format(g="mon2")
        + r"(?:,?\s+(?P<y2>\d{4}))?)\b",
        rest,
    )
    if iso_day:
        try:
            day = date(int(iso_day["y"]), int(iso_day["m"]), int(iso_day["d"]))
        except ValueError:
            return None
        explicit_year = True
        rest = _cut(rest, iso_day)
    elif named_day:
        month = _MONTHS[named_day["mon1"] or named_day["mon2"]]
        dom = int(named_day["d1"] or named_day["d2"])
        year_text = named_day["y1"] or named_day["y2"]
        try:
            day = date(int(year_text) if year_text else now.year, month, dom)
        except ValueError:
            return None
        explicit_year = bool(year_text)
        rest = _cut(rest, named_day)
    else:
        words = re.search(
            r"\b(?:(?P<rel>today|tomorrow|tonight)"
            r"(?:\s+(?P<relpart>morning|afternoon|evening|night))?"
            r"|this\s+(?P<thispart>morning|afternoon|evening)"
            r"|(?P<next>next\s+|this\s+)?" + _WEEKDAY + r")\b",
            rest,
        )
        if words:
            if words["rel"]:
                relative = "tomorrow" if words["rel"] == "tomorrow" else "today"
                part = "night" if words["rel"] == "tonight" else (words["relpart"] or "")
            elif words["thispart"]:
                relative, part = "today", words["thispart"]
            else:
                relative = "next-weekday" if (words["next"] or "").startswith("next") else "weekday"
                weekday = _WEEKDAYS[words["weekday"]]
            rest = _cut(rest, words)

    clock: tuple[int, int, str] | None = None
    for word, hint in (("noon", (12, 0)), ("midday", (12, 0)), ("midnight", (0, 0))):
        found = re.search(rf"\b{word}\b", rest)
        if found:
            clock = (hint[0], hint[1], "fixed")
            rest = _cut(rest, found)
            break
    else:
        found_clock = [m for m in _CLOCK.finditer(rest) if m.group("hour")]
        if len(found_clock) != 1:
            return None
        match = found_clock[0]
        hour, minute = int(match["hour"]), int(match["minute"] or 0)
        ampm = (match["ampm"] or "").replace(".", "")
        if minute > 59:
            return None
        if ampm:
            if not 1 <= hour <= 12:
                return None
            hour = (hour % 12) + (12 if ampm == "pm" else 0)
            clock = (hour, minute, "fixed")
        elif hour > 23:
            return None
        elif hour == 0 or hour > 12 or len(match["hour"]) == 2 and match["hour"].startswith("0"):
            # 0-hour, 13-23 and a zero-padded hour ("09:00") are 24-hour spellings.
            clock = (hour, minute, "fixed")
        else:
            clock = (hour, minute, "ambiguous")
        rest = _cut(rest, match)

    trailing_part = re.search(
        r"\b(?:in\s+the\s+)?(?P<p>morning|afternoon|evening|night|tonight)\b", rest
    )
    if trailing_part:
        part = part or ("night" if trailing_part["p"] == "tonight" else trailing_part["p"])
        if trailing_part["p"] == "tonight" and not relative:
            relative = "today"
        rest = _cut(rest, trailing_part)

    if _FILLER.sub(" ", rest).strip():
        return None
    if clock is None:
        return None
    hour, minute, kind = clock
    if kind == "ambiguous" and part:
        if part in _PM_PARTS and hour < 12:
            hour += 12
        kind = "fixed"

    tz = now.tzinfo
    if day is not None:
        candidate = _at(day, _pick_hour(hour, kind), minute, tz)
        if not explicit_year and candidate <= now:
            try:
                next_year = date(day.year + 1, day.month, day.day)
            except ValueError:  # 29 February, next year
                return None
            candidate = _at(next_year, candidate.hour, minute, tz)
        return candidate
    if relative in ("today", "tomorrow"):
        base = now.date() + timedelta(days=1 if relative == "tomorrow" else 0)
        if kind == "ambiguous" and relative == "today":
            return _next_reading(now, hour, minute, base_days=(0,))
        return _at(base, _pick_hour(hour, kind), minute, tz)
    if relative in ("weekday", "next-weekday"):
        ahead = (weekday - now.weekday()) % 7
        if relative == "next-weekday" and ahead == 0:
            ahead = 7
        candidate = _at(now.date() + timedelta(days=ahead), _pick_hour(hour, kind), minute, tz)
        if candidate <= now:
            candidate = _at(now.date() + timedelta(days=ahead + 7), candidate.hour, minute, tz)
        return candidate
    if kind == "ambiguous":
        return _next_reading(now, hour, minute, base_days=(0, 1))
    candidate = _at(now.date(), hour, minute, tz)
    return candidate if candidate > now else _at(now.date() + timedelta(days=1), hour, minute, tz)


def _cut(text: str, match: re.Match[str]) -> str:
    return f"{text[: match.start()]} {text[match.end():]}"


def _at(day: date, hour: int, minute: int, tz: tzinfo | None) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def _pick_hour(hour: int, kind: str) -> int:
    """An am/pm-less hour on a NAMED day: 7-11 morning, 12 noon, 1-6 afternoon."""
    if kind != "ambiguous":
        return hour
    return hour + 12 if 1 <= hour <= 6 else hour


def _next_reading(now: datetime, hour: int, minute: int, *, base_days: tuple[int, ...]) -> datetime:
    """The first instant after *now* that an am/pm-less *hour* can mean, over *base_days*."""
    readings = (hour, hour + 12) if hour < 12 else (12, 0)
    candidates = [
        _at(now.date() + timedelta(days=offset), reading % 24, minute, now.tzinfo)
        for offset in base_days
        for reading in readings
    ]
    future = sorted(c for c in candidates if c > now)
    return future[0] if future else candidates[0]
