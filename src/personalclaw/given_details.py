"""What a message written for her to send may state: only the details its source gives.

PersonalClaw writes some messages for her to send as her own: the follow-up chips under a chat
reply (:mod:`personalclaw.dashboard.chat_followups`), the suggestions a new chat offers
(:mod:`personalclaw.suggestions`) and a rewrite of her prompt
(:mod:`personalclaw.dashboard.handlers.optimizer`), which ``/optimize`` sends without showing it
to her first. A model writing one can put a detail in it that nobody gave. Measured: a reply said
that a swim class's time was not in her notes and asked her for it, and a follow-up chip under it
read "Lina's swim class is Saturday at 10:00 AM", sent with one click: a time nobody gave would
have gone into her notes as hers. Each of those prompts tells its model not to. A model can ignore
its rules, so what it writes is checked too.

**A detail** is what a message can state that she would have to know is true: a clock time, a
number, a name, or a path, file, address or link. :func:`ungiven` lists each one a message states
that its source (the text the model wrote it from) does not give, and :func:`keep_given` leaves out
each message that states one. A time is given only by a time that can be the same one (``10:00
AM`` by ``10am`` or ``10:00``, never by a bare ``10``), a number by the same number, a name by the
same word in any case or number (``Saturday`` by ``saturdays``), and a path, file or address by
the same text anywhere in the source, so a file named alone is given by the path that holds it.

**What is read as a name** is a capitalised word inside a sentence. The first word of a sentence
could be any word, so it is read as a name only as a possessive (``Lina's``); and a word written
all in capitals (``API``, ``README``) is a term more often than a person, place or day. A name
written in lower case, or a claim with no detail in it, is not seen here; the prompts' rules cover
those.

The check is a reading of words, not one more model call. It costs nothing and cannot fail to
answer, and follow-ups are asked for after every reply, where a second call each time would double
what a convenience costs. What it exists for is a specific value she would have to verify, and
that is what a reading of words can find.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Marks around a token that are not part of a path, file or address in it.
_LEAD = "\"'`“”‘’([{<*"
_TRAIL = "\"'`“”‘’)]}>*.,;:!?…"

#: A file name: a stem holding a letter and an extension that starts with one (``notes.md``,
#: ``config.py``, ``example.org``), so never ``e.g.``, ``a.m.`` or ``3.5``.
_FILE = re.compile(r"[\w-]*[^\W\d][\w-]*(?:\.[\w-]+)*\.[^\W\d_][^\W_]{1,6}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

#: A clock time: ``10am``, ``10:30 p.m.``, ``22:15``, ``10 o'clock``, and the named ones.
_TIME = re.compile(
    r"(?<![\w:.])(?P<h>\d{1,2})(?::(?P<m>[0-5]\d))?\s*(?P<half>[ap])(?:\.\s?m(?:\.|\b)|\s?m\b)"
    r"|(?<![\w:.])(?P<h24>\d{1,2}):(?P<m24>[0-5]\d)(?![\d:])"
    r"|(?<![\w:.])(?P<hour>\d{1,2})\s*o['’]?clock\b"
    r"|\b(?P<named>noon|midday|midnight)\b",
    re.IGNORECASE,
)

#: A number as written: ``3``, ``1,200``, ``3.5``, ``2026-10-09``, ``24/7``.
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,:/-]\d+)*")
_DIGITS = re.compile(r"\d+")
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")

#: A number that only counts a list's items: at the start of a line, before ``.``, ``)`` or
#: ``:`` (``1. Find the file``, ``Step 2: …``).
_LIST_MARK = re.compile(r"(?:^|\n)[ \t]*(?:step[ \t]+)?\Z", re.IGNORECASE)

#: A word, with the apostrophes and hyphens inside it (``Lina's``, ``Jean-Luc``).
_WORD = re.compile(r"[^\W\d_](?:[\w'’-]*[^\W_])?")

#: What a sentence's first word can be when it is not a name, written as a possessive or a
#: contraction (``It's``, ``Let's``, ``Today's``).
_OPENERS = frozenset(
    "it that what let here there who he she where how when why one everyone someone anyone "
    "nobody everybody somebody today tomorrow yesterday tonight".split()
)

#: Titles whose closing period does not end a sentence (``Dr. Patel``).
_TITLES = frozenset("mr mrs ms mx dr prof st".split())

#: The short names of days and months, and the full names they stand for. A source's short name
#: gives its full name only when capitalised: a lower-case ``sat`` or ``sun`` is another word.
_CALENDAR = {
    "mon": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
    "jan": "january",
    "feb": "february",
    "mar": "march",
    "apr": "april",
    "jun": "june",
    "jul": "july",
    "aug": "august",
    "sep": "september",
    "sept": "september",
    "oct": "october",
    "nov": "november",
    "dec": "december",
}


@dataclass(frozen=True)
class _Given:
    """What a source gives, read once for every message checked against it."""

    text: str
    times: frozenset[int]
    numbers: frozenset[str]
    words: frozenset[str]


def ungiven(text: str, given: str) -> list[str]:
    """Each detail *text* states that *given* (what it was written from) does not give, as *text*
    writes it, in the order it writes them; ``[]`` when it states none."""
    return _ungiven(text, _read(given))


def keep_given(items: list[str], given: str, *, what: str) -> list[str]:
    """*items*, in order, without each one that states a detail *given* does not give.

    What is left out is said in the log, at warning (the gateway log's level): a model wrote what
    its rules forbid, and the owner may want another model for it. The line says how many, under
    *what*; the details themselves, words written for her, are said at debug."""
    source = _read(given)
    kept: list[str] = []
    unsaid: list[str] = []
    for item in items:
        details = _ungiven(item, source)
        if details:
            unsaid.extend(details)
        else:
            kept.append(item)
    if unsaid:
        logger.warning(
            "%s: left out %d of %d the model wrote: each states a detail its source does not give",
            what,
            len(items) - len(kept),
            len(items),
        )
        logger.debug("%s: the details nothing gives: %s", what, ", ".join(unsaid))
    return kept


def _read(given: str) -> _Given:
    """What *given* gives: its times, its numbers (each as written and each run of digits in it),
    and its words."""
    text = given or ""
    times: set[int] = set()
    for match in _TIME.finditer(text):
        times |= _minutes(match)
    numbers = {_number(m.group(0)) for m in _NUMBER.finditer(text)}
    numbers |= {_number(m.group(0)) for m in _DIGITS.finditer(text)}
    words: set[str] = set()
    for word in _WORD.findall(text):
        key = _key(word)
        words.add(key)
        if word[0].isupper() and key in _CALENDAR:
            words.add(_CALENDAR[key])
    return _Given(text.lower(), frozenset(times), frozenset(numbers), frozenset(words))


def _ungiven(text: str, given: _Given) -> list[str]:
    """:func:`ungiven` over a source already read. Each kind of detail is read in turn, and what
    one reading took is blanked out of the text the next reads: a path's digits are not numbers,
    and a time's are not either."""
    found: list[tuple[int, str]] = []
    rest = text or ""

    def blank(start: int, end: int) -> None:
        nonlocal rest
        rest = rest[:start] + " " * (end - start) + rest[end:]

    for match in re.finditer(r"\S+", rest):
        start, core = _core(match)
        if core and _is_address(core):
            if core.lower() not in given.text:
                found.append((start, core))
            blank(start, start + len(core))
    for match in _TIME.finditer(rest):
        if not _minutes(match) & given.times:
            found.append((match.start(), match.group(0).strip()))
        blank(match.start(), match.end())
    for match in _NUMBER.finditer(rest):
        if _counts_a_list(rest, match):
            continue
        if _number(match.group(0)) not in given.numbers:
            found.append((match.start(), match.group(0)))
        blank(match.start(), match.end())
    for match in _WORD.finditer(rest):
        word = match.group(0)
        if _is_name(rest, match) and not _names_given(word, given):
            found.append((match.start(), word))
    return [detail for _, detail in sorted(found)]


def _core(match: re.Match[str]) -> tuple[int, str]:
    """Where the token *match* starts once the marks around it are taken off, and its text."""
    raw = match.group(0)
    lead = len(raw) - len(raw.lstrip(_LEAD))
    return match.start() + lead, raw[lead:].rstrip(_TRAIL)


def _is_address(token: str) -> bool:
    """Whether *token* is a link, an address, a handle, a path or a file name."""
    if "://" in token or token.lower().startswith("www."):
        return True
    if token.startswith("@") and len(token) > 1:
        return True
    if _EMAIL.fullmatch(token):
        return True
    if token.startswith(("~/", "/", "./", "../")) and len(token) > 1:
        return True
    parts = token.split("/")
    if len(parts) > 1 and all(parts) and (len(parts) > 2 or _FILE.fullmatch(parts[-1])):
        return True
    return bool(_FILE.fullmatch(token))


def _minutes(match: re.Match[str]) -> frozenset[int]:
    """The minutes after midnight the time *match* can be: one with its half of the day named,
    and both halves for a 12-hour time without one (``10:00`` is ten in the morning or at
    night)."""
    named = match.group("named")
    if named:
        return frozenset({0 if named.lower() == "midnight" else 12 * 60})
    if match.group("h") is not None:
        hour, minute = int(match.group("h")), int(match.group("m") or 0)
        if 1 <= hour <= 12:
            afternoon = 12 if match.group("half").lower() == "p" else 0
            return frozenset({(hour % 12 + afternoon) * 60 + minute})
        return frozenset({hour % 24 * 60 + minute})
    hour = int(match.group("h24") or match.group("hour"))
    minute = int(match.group("m24") or 0)
    if 1 <= hour <= 12:
        return frozenset({hour % 12 * 60 + minute, (hour % 12 + 12) * 60 + minute})
    return frozenset({hour % 24 * 60 + minute})


def _number(text: str) -> str:
    """*text*, a number, as it compares: thousands unseparated and no leading zeros."""
    if _THOUSANDS.fullmatch(text):
        text = text.replace(",", "")
    return str(int(text)) if text.isdigit() else text


def _counts_a_list(text: str, match: re.Match[str]) -> bool:
    """Whether the number *match* only counts a list's items (``1. Find the file``)."""
    after = text[match.end() : match.end() + 2]
    return (
        len(after) == 2
        and after[0] in ".):"
        and after[1].isspace()
        and _LIST_MARK.search(text[: match.start()]) is not None
    )


def _is_name(text: str, match: re.Match[str]) -> bool:
    """Whether the word *match* is read as a name: capitalised, not all in capitals and not
    ``I``, inside a sentence, or a possessive opening one."""
    word = match.group(0)
    if not word[0].isupper() or word == "I" or word.startswith(("I'", "I’")):
        return False
    if len(word) > 1 and word.isupper():
        return False
    if not _opens_a_sentence(text, match.start()):
        return True
    stem = re.sub(r"['’]s$", "", word)
    return stem != word and len(stem) > 1 and stem.lower() not in _OPENERS


def _opens_a_sentence(text: str, at: int) -> bool:
    """Whether the word at *at* in *text* is the first of a sentence, a line or a list item."""
    before = text[:at].rstrip(" \t\"'`“”‘’([{<*•-–—")
    if not before or before.endswith(("\n", "\r", "!", "?", "…", ":")):
        return True
    if not before.endswith("."):
        return False
    last = re.search(r"([^\W\d_]+)\.$", before)
    return not (last and last.group(1).lower() in _TITLES)


def _names_given(word: str, given: _Given) -> bool:
    """Whether *given* gives the name *word*: the same word, or the day or month it shortens."""
    key = _key(word)
    return key in given.words or _CALENDAR.get(key, key) in given.words


def _key(word: str) -> str:
    """*word* as a name compares: lower case, with a possessive or a plural taken off."""
    word = word.lower().replace("’", "'")
    if word.endswith("'s"):
        word = word[:-2]
    elif word.endswith("'"):
        word = word[:-1]
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word
