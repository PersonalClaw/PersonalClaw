"""JSON⇄:class:`SheetModel` — the ONLY shape a spreadsheet crosses the wire in.

The sheet half of what ``model_json.py`` does for a text document, and for the same
reasons: the grid editor edits the *model*, never the bytes, so the server parses an
.xlsx into a model, hands the model to the browser, takes a model back, and re-renders it
with the shipped writer. No OOXML is constructed in a browser, and that is a property of
this boundary rather than a convention somebody remembers.

Separate module, not a second pair of functions inside ``model_json.py``: the two models
share no node type, so folding them together would produce one file whose every function
belonged to only half of it.

**Serialization is ``asdict``** for ``model_json.py``'s reason — a hand-written mapper is
one forgotten line from dropping a field the writer still emits.
:attr:`~personalclaw.documents.model.Sheet.rows` is a derived property and therefore
absent from ``asdict`` by construction, which is the point: the wire carries cells, and
the plain view is computed wherever it is needed rather than shipped as a second copy
that can go stale.

**Deserialization is STRICT**, likewise: the payload arrives from a browser, so an
unknown key is a refusal rather than a silent drop. A client that misspells
``number_format`` must learn its formatting was not saved, not find out in Excel.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from personalclaw.documents.model import Sheet, SheetCell, SheetModel

# The scalar guards are ``model_json``'s, reused rather than restated. They encode the
# strictness POSTURE (unknown key = refusal, allowed keys derived from the dataclass with
# ``fields()``), and a second copy here would be a second posture the moment one of them
# was tightened. Underscore-named because they are internal to the documents package,
# which this module is part of — not because they are private to that one file.
from personalclaw.documents.model_json import _flag, _number, _object, _sequence, _text


def sheet_to_dict(model: SheetModel) -> dict[str, Any]:
    """The workbook as JSON-ready data."""
    return asdict(model)


def sheet_from_dict(payload: Any) -> SheetModel:
    """Build a :class:`SheetModel` from untrusted JSON data.

    Raises ``ValueError`` — with the offending path in the message — for anything that is
    not a workbook. The caller turns that into one refusal.
    """
    data = _object(payload, SheetModel, "model")
    return SheetModel(
        sheets=[
            _sheet(item, f"model.sheets[{index}]")
            for index, item in enumerate(_sequence(data.get("sheets"), "model.sheets"))
        ]
    )


def _sheet(payload: Any, where: str) -> Sheet:
    data = _object(payload, Sheet, where)
    return Sheet(
        name=_text(data.get("name"), f"{where}.name"),
        cells=[
            [
                _cell(cell, f"{where}.cells[{row_index}][{col_index}]")
                for col_index, cell in enumerate(_sequence(row, f"{where}.cells[{row_index}]"))
            ]
            for row_index, row in enumerate(_sequence(data.get("cells"), f"{where}.cells"))
        ],
        column_widths=[
            _number(width, f"{where}.column_widths[{index}]")
            for index, width in enumerate(
                _sequence(data.get("column_widths"), f"{where}.column_widths")
            )
        ],
        merges=[
            _text(ref, f"{where}.merges[{index}]")
            for index, ref in enumerate(_sequence(data.get("merges"), f"{where}.merges"))
        ],
        frozen_header=_flag(data.get("frozen_header"), f"{where}.frozen_header"),
    )


def _cell(payload: Any, where: str) -> SheetCell:
    data = _object(payload, SheetCell, where)
    return SheetCell(
        value=_value(data.get("value"), f"{where}.value"),
        formula=_text(data.get("formula"), f"{where}.formula"),
        number_format=_text(data.get("number_format"), f"{where}.number_format"),
        bold=_flag(data.get("bold"), f"{where}.bold"),
        italic=_flag(data.get("italic"), f"{where}.italic"),
        font_color=_text(data.get("font_color"), f"{where}.font_color"),
        fill=_text(data.get("fill"), f"{where}.fill"),
        align=_text(data.get("align"), f"{where}.align"),
    )


def _value(value: Any, where: str) -> object:
    """A cell literal: exactly the JSON scalars, and nothing composite.

    The one place this codec is looser than ``model_json.py``'s helpers, because a cell's
    value is genuinely polymorphic — that polymorphism is the whole reason numbers stay
    summable. Still closed, though: a list or an object here would reach the writer as
    ``str(...)`` of a Python repr, which is not what any user typed.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError(f"{where} must be a string, number, boolean or null")
