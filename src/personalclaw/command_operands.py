"""Reading a command line for its operands: which words are a program's operands, and the values
the options that matter are given.

The programs :mod:`personalclaw.command_effects` reads past the read-only forms are never a read,
so their options are not checked against read-only forms: a command line is read only for which
words are its operands and what the options that matter are given, which is what says where the
program reaches.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from personalclaw.shell_syntax import Word


@dataclass
class _Scan:
    """A command line read for its operands and the values of the options that matter.

    ``unknown``: it holds an option this reading does not know, so which words are operands is
    not known for certain (that option may take the next word as its value)."""

    operands: list[Word]
    values: dict[str, list[Word]]
    seen: set[str]
    unknown: bool = False


def _tail(word: Word, start: int) -> Word:
    """The part of *word* from index *start*, as a word of its own."""
    at = word.glob_at - start if word.glob_at >= start else -1
    if start and word.leading:
        return Word(word.text[start:], at, True)  # the variable it began with is cut off
    return Word(word.text[start:], at, word.opaque, word.leading)


def _scan(
    args: Sequence[Word],
    *,
    flags: str = "",
    valued: str = "",
    long_flags: frozenset[str] = frozenset(),
    long_valued: frozenset[str] = frozenset(),
    unknown_short_is_flag: bool = False,
    stop_at_operand: bool = False,
) -> _Scan:
    """Read *args*: every ``-`` word before ``--`` is an option, wherever it is (or, with
    *stop_at_operand*, until the first operand: a program another one starts begins there)."""
    operands: list[Word] = []
    values: dict[str, list[Word]] = {}
    seen: set[str] = set()
    unknown = False
    i = 0
    while i < len(args):
        word = args[i]
        text = word.text
        i += 1
        if text == "--":
            operands.extend(args[i:])
            break
        if text == "-" or not text.startswith("-"):
            operands.append(word)
            if stop_at_operand:
                operands.extend(args[i:])
                break
            continue
        if text.startswith("--"):
            name, eq, _value = text.partition("=")
            seen.add(name)
            if eq:
                values.setdefault(name, []).append(_tail(word, len(name) + 1))
                if name not in long_valued and name not in long_flags:
                    unknown = True
            elif name in long_valued:
                if i < len(args):
                    values.setdefault(name, []).append(args[i])
                    i += 1
                else:
                    unknown = True
            elif name not in long_flags:
                unknown = True
            continue
        for j, letter in enumerate(text[1:], start=1):
            option = f"-{letter}"
            seen.add(option)
            if letter in valued:
                if j + 1 < len(text):
                    values.setdefault(option, []).append(_tail(word, j + 1))
                elif i < len(args):
                    values.setdefault(option, []).append(args[i])
                    i += 1
                else:
                    unknown = True
                break
            if letter not in flags and not unknown_short_is_flag:
                unknown = True
    return _Scan(operands, values, seen, unknown)


def _value_words(scan: _Scan, options: Iterable[str]) -> list[Word]:
    return [word for option in options for word in scan.values.get(option, [])]
