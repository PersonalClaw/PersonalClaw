"""SheetModel → .xlsx bytes (openpyxl).

Cell TYPES are preserved deliberately: a spreadsheet whose numbers arrived as text can't
be summed or charted, which defeats the purpose of generating one.
"""

from __future__ import annotations

import io

from personalclaw.documents.model import SheetModel
from personalclaw.documents.registry import register_writer

#: Excel's own limit on a sheet name, plus the characters it forbids.
_MAX_SHEET_NAME = 31
_ILLEGAL_SHEET_CHARS = set(r"[]:*?/\\")


def _safe_sheet_name(name: str, used: set[str]) -> str:
    """Excel refuses some names outright; a rejected name would fail the whole write."""
    cleaned = "".join("-" if ch in _ILLEGAL_SHEET_CHARS else ch for ch in (name or "").strip())
    cleaned = (cleaned or "Sheet")[:_MAX_SHEET_NAME]
    base, n = cleaned, 2
    while cleaned.casefold() in used:  # Excel treats sheet names case-insensitively
        suffix = f"-{n}"
        cleaned = f"{base[: _MAX_SHEET_NAME - len(suffix)]}{suffix}"
        n += 1
    used.add(cleaned.casefold())
    return cleaned


def render_xlsx(model: object) -> bytes:
    from openpyxl import Workbook

    if not isinstance(model, SheetModel):
        raise TypeError("xlsx writer expects a SheetModel")
    wb = Workbook()
    wb.remove(wb.active)  # drop the default sheet; every sheet here is explicit
    used: set[str] = set()
    for name, rows in (model.sheets or {"Sheet1": []}).items():
        ws = wb.create_sheet(_safe_sheet_name(name, used))
        for row in rows:
            ws.append([_cell(v) for v in row])
        if rows:
            for cell in ws[1]:  # row 0 is the header by contract
                cell.font = cell.font.copy(bold=True)
            ws.freeze_panes = "A2"  # header stays visible while scrolling
    if not wb.sheetnames:  # a model with an empty dict still has to produce a valid file
        wb.create_sheet("Sheet1")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(value: object) -> object:
    """Pass through the types openpyxl writes natively; stringify anything else.

    bool is checked before int on purpose — in Python `bool` IS an `int`, and writing
    True as 1 would lose the distinction the model went out of its way to preserve.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


register_writer("xlsx", render_xlsx)
