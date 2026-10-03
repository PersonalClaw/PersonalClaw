"""Smart case: when letter case decides a match for the file tools' patterns.

``grep`` matched with ``query in line`` and ``glob`` matched names exactly, so a model searching for
"dentist" never saw a line that said "Dentist", and the regular expression ``pick up`` never saw
"Pick up". The rule here is the one ripgrep and fd call smart case: a pattern with no capital letter
matches whatever the case, and one with a capital matches it exactly, because a capital is the one
sign that the case was meant. A call's ``ignore_case``, true or false, overrides it either way.

In a regular expression only the letters it matches count. An escape (``\\S``, ``\\W``, ``\\D``,
``\\B``, ``\\A``, ``\\Z``, a character by ``\\N{name}`` or by code), a group's name and the inline
flags are syntax, so ``pick\\Wup`` still matches any case. A capital in a class (``[A-Z]``) is a
capital the pattern matches, so it keeps case.
"""

from __future__ import annotations

import re
from typing import Any

from personalclaw.safety_flags import yes_or_no

#: The regular-expression syntax whose letters name no text the pattern matches. It is taken out
#: before looking for a capital (:func:`ignores_case`).
_REGEX_SYNTAX = re.compile(
    r"\\N\{[^}]*\}"  # a character by name
    r"|\\x[0-9A-Fa-f]{2}|\\u[0-9A-Fa-f]{4}|\\U[0-9A-Fa-f]{8}"  # a character by code
    r"|\\."  # any other escape: a class, an anchor, a group number or a symbol
    r"|\(\?P<[^>]*>|\(\?P=[^)]*\)|\(\?\([^)]*\)"  # a group's name, a reference to one, a condition
    r"|\(\?#[^)]*\)"  # a comment
    r"|\(\?[aiLmsux]*(?:-[imsx]*)?[:)]",  # inline flags
    re.DOTALL,
)


def case_override(raw: Any) -> bool | None:
    """A call's ``ignore_case`` as it was given, or None when it leaves smart case to decide.

    Read as the word it spells (:func:`~personalclaw.safety_flags.yes_or_no`), never as the True
    ``bool("false")`` is. Left out or blank, it leaves the case to smart case. Anything else that
    spells neither yes nor no, a number included, is refused: either guess would match by a case
    the call never asked for.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    said = yes_or_no(raw)
    if said is None:
        raise ValueError("ignore_case must be true or false, or left out for smart case")
    return said


def ignores_case(pattern: str, *, regex: bool = False, override: Any = None) -> bool:
    """Whether *pattern* matches whatever the letter case: *override* (the call's ``ignore_case``)
    when it was given, else smart case, which ignores case unless the pattern has a capital letter
    of its own (with *regex*, a letter of its syntax is not one)."""
    chosen = case_override(override)
    if chosen is not None:
        return chosen
    text = _REGEX_SYNTAX.sub("", pattern) if regex else pattern
    return not any(ch.isupper() for ch in text)


def glob_case_sensitive(pattern: str, override: Any = None) -> bool | None:
    """The ``case_sensitive`` :meth:`pathlib.Path.glob` is given for *pattern*.

    None, the platform's own matching and its quickest, for a pattern with no letter whose case
    could matter (``**/*``, grep's default). Otherwise an explicit answer, so a pattern that keeps
    case keeps it on every platform: left to itself, pathlib can match a pattern's plain names by
    the file system's own rule (any case, on macOS) and its wildcards by exact case.
    """
    ignore = ignores_case(pattern, override=override)  # an unreadable override is refused here too
    if pattern.lower() == pattern.upper():
        return None
    return not ignore


class LineQuery:
    """A grep query as it matches a line, read as text or, when the call's ``regex`` says yes
    (:func:`~personalclaw.safety_flags.yes_or_no`; ``"false"`` is a no), as a regular expression,
    its case decided by :func:`ignores_case`. Raises :class:`re.error` for an expression that does
    not compile, and ``ValueError`` for an ``ignore_case`` that is not true or false."""

    def __init__(self, query: str, *, regex: Any = None, ignore_case: Any = None) -> None:
        as_regex = yes_or_no(regex) is True
        self._fold = ignores_case(query, regex=as_regex, override=ignore_case)
        flags = re.IGNORECASE if self._fold else 0
        self._regex = re.compile(query, flags) if as_regex else None
        self._text = query.casefold() if self._fold else query

    def _folded(self, text: str) -> str:
        return text.casefold() if self._fold else text

    def in_file(self, text: str) -> bool:
        """False when no line of *text* can match, one test that passes over most files at once.
        A regular expression is always tried line by line: its anchors mean a line's ends there, and
        over the whole text a pattern such as ``[^x]*y`` would rescan the rest of the file from
        every character."""
        return self._regex is not None or self._text in self._folded(text)

    def in_line(self, line: str) -> bool:
        if self._regex is not None:
            return self._regex.search(line) is not None
        return self._text in self._folded(line)
