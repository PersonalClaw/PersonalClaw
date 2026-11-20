"""The plain numbers in CSV text, as a workbook holds them.

CSV text is all text: ``knowledge.readers.delimited_rows``, the one reading of delimited text
(Knowledge's ``.csv`` and ``.tsv`` files read through it too), returns every cell as a string, and
must. A workbook made from it needs its numbers as numbers, or a column sums to 0 and sorts
"100" before "20". :func:`typed_rows` types the cells a workbook writes back as the same text, and
leaves every other cell as it was.

**A plain number is one that loses nothing as a number.** ``12``, ``-3.5`` and ``0.25`` show the
same in a cell as in the text. Each of these would not, so they stay text: ``007`` (a code),
``1,234`` (and in some places it reads as 1.234), ``$45.20``, ``12.5%``, ``+5``, ``1e3`` (it shows
as 1000), ``1.50`` (it shows as 1.5), ``-0``, `` 12 ``, a date, and a number of more than 15
significant digits, which a workbook cannot hold exactly. A cell that wants a number format is
the work of the cell grammar the formula task (#4161) brings.
"""

from __future__ import annotations

import re

#: A canonical decimal: an optional minus, no leading zero, and a fraction with no trailing zero.
_PLAIN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")

#: The significant digits a workbook's number holds exactly (IEEE double: 15).
_HELD_DIGITS = 15


def plain_number(text: str) -> int | float | None:
    """*text* as the number a workbook shows as the same text, or ``None`` when it is not one."""
    if not _PLAIN.fullmatch(text) or text == "-0":
        return None
    if len(text.lstrip("-").replace(".", "").lstrip("0")) > _HELD_DIGITS:
        return None
    if "." not in text:
        return int(text)
    number = float(text)
    return number if repr(number) == text else None


def typed_rows(rows: list[list[str]]) -> list[list[object]]:
    """*rows* (a CSV's cells, row 0 its header) with each plain number below the header as a
    number. The header stays text: a year heading such as ``2024`` is a label."""
    typed: list[list[object]] = [list(rows[0])] if rows else []
    for row in rows[1:]:
        typed.append([_typed(cell) for cell in row])
    return typed


def _typed(cell: str) -> object:
    number = plain_number(cell)
    return cell if number is None else number


__all__ = ["plain_number", "typed_rows"]
