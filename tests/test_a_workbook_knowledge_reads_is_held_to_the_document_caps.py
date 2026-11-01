"""A workbook Knowledge reads is held to the caps every office parser is held to.

The office parsers (``documents/``) refuse a package whose XML is over the archive caps before a
document library opens it, and stop at a cell cap (``documents.limits``). Knowledge's own reader
of an uploaded ``.xlsx`` (``knowledge.readers.FileReader._read_xlsx``) handed the file straight to
``openpyxl`` and read every cell of every sheet, so a small upload that decompresses to far more
than its size cost what the caps exist to refuse.

Now the reader checks the archive caps first, and a package over them ends the item failed with the
limit's sentence, keeping no cell text; and past the cell cap, summed across sheets, reading stops
on a whole row and the text says where.

The caps are lowered for each test, so an ordinary small workbook stands in for an enormous one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.documents import limits
from personalclaw.knowledge.readers import FileReader

openpyxl = pytest.importorskip("openpyxl")

pytestmark = pytest.mark.security


def _workbook(path: Path, sheets: dict[str, list[list[str]]]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def _rows(prefix: str, count: int) -> list[list[str]]:
    return [["name", "value", "note"]] + [
        [f"{prefix}-{n}", str(n), f"{prefix}-note-{n}"] for n in range(1, count)
    ]


def test_a_workbook_over_the_archive_caps_is_not_read(tmp_path, monkeypatch) -> None:
    """🔴 Before: the reader never asked the caps, and read every cell."""
    path = _workbook(tmp_path / "big.xlsx", {"Data": _rows("cell", 200)})
    monkeypatch.setattr(limits, "MAX_XML_PART_BYTES", 2048)

    text, meta = FileReader().read(str(path))

    assert meta["format"] == "error", meta
    assert meta["error"].startswith("The spreadsheet was not read: part "), meta
    assert "decompresses to more than" in meta["error"], meta
    assert "cell-17" not in text, "no cell text is kept of a refused workbook"


def test_the_knowledge_item_ends_failed_and_names_the_limit(tmp_path, monkeypatch) -> None:
    from personalclaw.knowledge.pipeline.nodes.text_nodes import DocumentReadNode
    from personalclaw.knowledge.pipeline.types import NodeContext

    path = _workbook(tmp_path / "big.xlsx", {"Data": _rows("cell", 200)})
    monkeypatch.setattr(limits, "MAX_XML_PART_BYTES", 2048)
    ctx = NodeContext(item_id="k1", item_type="sheet", file_path=str(path))

    out = asyncio.run(DocumentReadNode().run({}, ctx))

    assert out.success is False
    assert "decompresses to more than" in out.error, out.error
    assert "cell-17" not in (out.text or "")


def test_an_ordinary_workbook_reads_as_before(tmp_path) -> None:
    path = _workbook(
        tmp_path / "report.xlsx",
        {"Q1": [["region", "total"], ["north", "10"], ["south", "12"]], "Empty": []},
    )

    text, meta = FileReader().read(str(path))

    assert text == (
        "## Q1\n| region | total |\n| --- | --- |\n| north | 10 |\n| south | 12 |"
    ), text
    read = {k: meta.get(k) for k in ("format", "content_type", "sheet_count", "row_count")}
    assert read == {"format": "xlsx", "content_type": "markdown", "sheet_count": 2, "row_count": 3}


def test_past_the_cell_cap_reading_stops_on_a_whole_row_and_says_so(tmp_path, monkeypatch) -> None:
    """🔴 Before: every cell of every sheet was read, whatever the cap."""
    path = _workbook(
        tmp_path / "wide.xlsx", {"Data": _rows("first", 5), "Later": _rows("second", 3)}
    )
    monkeypatch.setattr(limits, "MAX_CELLS", 10)

    text, meta = FileReader().read(str(path))

    assert meta["format"] == "xlsx", meta
    assert "| first-2 | 2 | first-note-2 |" in text, text
    assert "first-3" not in text, "the row that would pass the cap is not read in part"
    assert "second-1" not in text and "## Later" not in text, "a later sheet is not read"
    assert "Reading stopped at row 4 of sheet 'Data'" in text, text
    assert "more than 10 cells" in text and "raw route" not in text, text
    assert meta["row_count"] == 3, meta
