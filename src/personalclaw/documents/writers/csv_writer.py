"""SheetModel → UTF-8 CSV bytes using the standard-library dialect, and CSV text by the same rule.

**No cell is written so that a spreadsheet would evaluate it.** A CSV says nothing about what
its cells are, so a spreadsheet program opening one decides from each cell's text, and it reads a
cell whose text begins with ``=``, ``+``, ``-`` or ``@`` as a formula (some programs drop a
leading tab or carriage return first). A CSV's cells come from wherever the agent, an app or a
workflow found them: a page, a message, an imported file. So every cell is written by one rule,
applied to the text written for it:

* text that begins with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is written with a
  single quote (``'``) in front of it, which a spreadsheet shows as text and never evaluates. A
  byte-order mark before that character changes nothing, since a spreadsheet drops one that
  opens the file before it reads the first cell;
* except a number, which holds nothing a spreadsheet could compute and is written as it is: an
  optional sign, an optional currency sign, then only digits, commas and periods (at least one
  digit), an optional exponent, and an optional percent or currency sign at the end (``-20``,
  ``-1,234.56``, ``-$45.20``, ``-12.5%``, ``+1.5e3``). A cell the model holds as a number is
  written as that text, so a negative number stays a number. A cell of dashes alone (``-``, a
  table's "none") holds nothing to compute either and is written as it is too;
* every other cell is written as it is, a date among them.

A formula cell is no exception: a CSV cannot say that a cell is a formula, so its text is written
by the same rule and shows as text.

:func:`render_csv` writes a sheet's cells by the rule. :func:`render_csv_text` writes CSV text by
it, for a CSV whose text was written elsewhere: the artifact store keeps every CSV artifact's text
through it, whoever wrote that text.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata

from personalclaw.documents.model import SheetModel
from personalclaw.documents.registry import register_writer

#: The characters a spreadsheet reads as the start of a formula when one begins a cell's text.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

#: A byte-order mark, which a spreadsheet drops from the start of a file before the first cell.
_BOM = "\ufeff"

#: A number's digits: digits, commas and periods, at least one digit, then an optional exponent.
_DIGITS = re.compile(r"(?=[0-9.,]*[0-9])[0-9.,]+(?:[eE][+-]?[0-9]+)?")


def _currency(char: str) -> bool:
    return bool(char) and unicodedata.category(char) == "Sc"


def _is_number(text: str) -> bool:
    """Whether *text* is a number as the module's rule reads one: nothing a spreadsheet computes."""
    body = text[1:] if text[:1] in ("+", "-") else text
    if _currency(body[:1]):
        body = body[1:]
    elif _currency(body[-1:]):
        body = body[:-1]
    if body.endswith("%"):
        body = body[:-1]
    return _DIGITS.fullmatch(body) is not None


def _quoted(text: str) -> str | None:
    """*text* behind a quote, when the module's rule writes it so; otherwise ``None``."""
    probe = text.lstrip(_BOM)
    if not probe.startswith(_FORMULA_LEADS) or _is_number(probe) or not probe.strip("-"):
        return None
    return "'" + text


def _cell(value: object) -> object:
    """The cell as the rule writes it: *value* itself, or its text behind a quote.

    The text tested is the text ``csv.writer`` writes for the value (nothing for ``None``, the
    ``repr`` of a float, the ``str`` of anything else), so a value this returns unchanged is
    written exactly as the standard library writes it.
    """
    text = "" if value is None else repr(value) if isinstance(value, float) else str(value)
    quoted = _quoted(text)
    return value if quoted is None else quoted


def render_csv(model: object) -> bytes:
    """Render one sheet as CSV, each cell by the rule in this module's docstring.

    CSV has no sheet concept, so refusing a multi-sheet workbook is less ambiguous than
    silently concatenating tables or discarding all but the first sheet.
    """
    if not isinstance(model, SheetModel):
        raise TypeError("csv writer expects a SheetModel; prose documents have no rows")
    if len(model.sheets) > 1:
        raise ValueError("csv writer cannot represent multiple sheets; provide one sheet")

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    if model.sheets:
        writer.writerows([_cell(value) for value in row] for row in model.sheets[0].rows)
    return output.getvalue().encode("utf-8")


def render_csv_text(text: str) -> str:
    """CSV *text* with each cell written by the rule in this module's docstring.

    The cells are the ones the ``csv`` module reads, so a field in quotes is one cell whatever
    delimiters, line breaks and doubled quotes it holds. When no cell needs the quote the text is
    returned as it is, byte for byte. When one does, the rows are written again with the module,
    a line break ending each line (and the last only if the text's last line had one); a
    byte-order mark that opens the text stays in front of it, outside the first cell. Raises
    ``csv.Error`` on text the module cannot read (a field longer than its field limit, 131,072
    characters).
    """
    bom = _BOM if text.startswith(_BOM) else ""
    body = text[len(bom) :]
    rows = list(csv.reader(io.StringIO(body, newline="")))
    written = [[_quoted(cell) or cell for cell in row] for row in rows]
    if written == rows:
        return text
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(written)
    again = output.getvalue()
    if not body.endswith(("\n", "\r")):
        again = again[:-1]
    return bom + again


register_writer("csv", render_csv)
