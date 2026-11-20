"""A workbook's next version, carried from the version it was read from.

The agent writes a workbook's next version from its reading (``artifact_get``): each sheet's rows,
a formula cell as its formula text. Rows are data alone, so :meth:`SheetModel.from_rows` builds
them as a new workbook would be: every value a literal, the header bold and frozen, nothing else.
Written as they are, every formula she had came back as text, and her number formats, fills,
column widths and merged cells were gone.

:func:`carry_from_base` puts back what the rows cannot say, from the base version's own model
(``xlsx_parser.parse_xlsx``). It is pure and writes nothing. Sheets match by name, cells by
address:

* **Formulas.** A cell whose new value is exactly the formula text of the base cell at the same
  address becomes that formula again. Any other value starting with ``=`` stays a literal, as it
  always has: the carry makes no formula she did not have at that cell.
* **Presentation.** Every cell the base has keeps the base cell's number format, bold, italic,
  font colour, fill and alignment, whatever its new value. Only a cell the base does not have
  takes ``from_rows``'s default. Each sheet keeps the base's column widths and frozen header.
* **Merges.** A merge is kept while every cell it covers but its first is empty in the new rows.
  Otherwise writing it would hide those values, so it is dropped, and named.

What it could not carry is in the :class:`Carried` it returns, for the caller to say: the base
formulas whose cell now holds something else or is gone, the merges dropped, the base's sheets
the rows leave out, and what the base's file holds that no model can (its loss items, but a
formula's cached result, which a reader recalculates on open).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from personalclaw.documents.docx_parser import LossItem, LossReport
from personalclaw.documents.model import Sheet, SheetCell, SheetModel

#: The one loss a version written from the model never makes: a formula's cached result, which
#: the file a reader opens recalculates (``xlsx_parser._cell``).
_RECALCULATED = "formula_cached_value"


@dataclass
class Carried:
    """The next version's model, and what of the base it did not carry."""

    model: SheetModel
    #: Each formula carried, as ``Sheet!B5``.
    formulas_kept: list[str] = field(default_factory=list)
    #: Each base formula whose cell now holds something else: ``(Sheet!B5, "=SUM(B3:B4)")``.
    formulas_replaced: list[tuple[str, str]] = field(default_factory=list)
    #: Each base formula whose cell the new rows no longer reach, the same way.
    formulas_removed: list[tuple[str, str]] = field(default_factory=list)
    #: Each merge dropped because a cell inside it now holds a value, as ``Sheet!A1:B1``.
    merges_dropped: list[str] = field(default_factory=list)
    #: The base's sheets the new rows leave out.
    sheets_left_out: list[str] = field(default_factory=list)
    #: What the base's file holds that no version written from the model carries.
    unsupported: list[LossItem] = field(default_factory=list)


def carry_from_base(new: SheetModel, base: SheetModel, losses: LossReport) -> Carried:
    """*new* (``from_rows`` of the rows sent) with what *base*, the version they were read from,
    holds put back. *losses* is the base's parse report."""
    carried = Carried(model=SheetModel(sheets=[]))
    bases = {sheet.name: sheet for sheet in base.sheets}
    for sheet in new.sheets:
        was = bases.get(sheet.name)
        carried.model.sheets.append(sheet if was is None else _carry_sheet(sheet, was, carried))
    sent = {sheet.name for sheet in new.sheets}
    carried.sheets_left_out = [sheet.name for sheet in base.sheets if sheet.name not in sent]
    carried.unsupported = [item for item in losses.items if item.kind != _RECALCULATED]
    return carried


def _carry_sheet(sheet: Sheet, base: Sheet, carried: Carried) -> Sheet:
    cells = [
        [
            _carry_cell(cell, _at(base, r, c), f"{sheet.name}!{_ref(r, c)}", carried)
            for c, cell in enumerate(row)
        ]
        for r, row in enumerate(sheet.cells)
    ]
    for r, row in enumerate(base.cells):
        for c, was in enumerate(row):
            if was.formula and _at(sheet, r, c) is None:
                carried.formulas_removed.append((f"{sheet.name}!{_ref(r, c)}", was.formula))
    merges = []
    for ref in base.merges:
        if _holds_a_value_inside(cells, ref):
            carried.merges_dropped.append(f"{sheet.name}!{ref}")
        else:
            merges.append(ref)
    return Sheet(
        name=sheet.name,
        cells=cells,
        column_widths=list(base.column_widths),
        merges=merges,
        frozen_header=base.frozen_header,
    )


def _carry_cell(cell: SheetCell, was: SheetCell | None, where: str, carried: Carried) -> SheetCell:
    if was is None:
        return cell
    if was.formula and cell.value == was.formula:
        carried.formulas_kept.append(where)
        return _presented(SheetCell(formula=was.formula), was)
    if was.formula:
        carried.formulas_replaced.append((where, was.formula))
    return _presented(cell, was)


def _presented(cell: SheetCell, was: SheetCell) -> SheetCell:
    """*cell* as *was*, the base cell at its address, presented it."""
    return replace(
        cell,
        number_format=was.number_format,
        bold=was.bold,
        italic=was.italic,
        font_color=was.font_color,
        fill=was.fill,
        align=was.align,
    )


def _at(sheet: Sheet, r: int, c: int) -> SheetCell | None:
    if r < len(sheet.cells) and c < len(sheet.cells[r]):
        return sheet.cells[r][c]
    return None


def _holds_a_value_inside(cells: list[list[SheetCell]], ref: str) -> bool:
    """Whether a cell *ref* covers, other than its first, holds a value in *cells*.

    Only the cells the rows hold are looked at, never the whole range: a merge is the file's
    word, and one as large as a sheet (``A1:XFD1048576``) would otherwise be billions of steps
    for a few rows."""
    from openpyxl.utils.cell import range_boundaries

    first_col, first_row, last_col, last_row = range_boundaries(ref)
    for r in range(first_row - 1, min(last_row, len(cells))):
        for c in range(first_col - 1, min(last_col, len(cells[r]))):
            if (r, c) == (first_row - 1, first_col - 1):
                continue
            cell = cells[r][c]
            if cell.formula or cell.value not in (None, ""):
                return True
    return False


def _ref(r: int, c: int) -> str:
    from openpyxl.utils.cell import get_column_letter

    return f"{get_column_letter(c + 1)}{r + 1}"


__all__ = ["Carried", "carry_from_base"]
