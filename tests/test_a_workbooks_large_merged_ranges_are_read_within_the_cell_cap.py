"""A workbook's merged ranges are held to the cell cap before the workbook is read.

``parse_xlsx`` checks the package's size first (``assert_archive_within_limits``) and stops at
``MAX_CELLS`` cells, but a merged range is a few bytes of XML (``<mergeCell ref="A1:J100000"/>``)
and the full read makes an object for every cell a merge covers before a single cell is walked.
So the merges are counted from the sheet XML first, and a workbook whose merges cover more cells
than the cap is refused as too large, the way an oversized package is, before it is read.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest

from personalclaw.documents.limits import MAX_CELLS, DocumentTooLarge
from personalclaw.documents.model import Sheet, SheetCell, SheetModel
from personalclaw.documents.writers.xlsx_writer import render_xlsx
from personalclaw.documents.xlsx_parser import parse_xlsx


def _with_merges(*refs: str, utf16: bool = False, named: str = "") -> bytes:
    """A small workbook whose first sheet's XML declares *refs* as merged ranges, the way a file
    from anywhere can: written straight into the part, as no editor would. *utf16* writes that part
    in UTF-16; *named* puts it under another name, with the package's relationships and content
    types pointing at it, as openpyxl follows them."""
    plain = render_xlsx(SheetModel(sheets=[Sheet(name="Data", cells=[[SheetCell(value="Title")]])]))
    merges = "".join(f'<mergeCell ref="{ref}"/>' for ref in refs)
    sheet = "xl/worksheets/sheet1.xml"
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(plain)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            body, name = src.read(info), info.filename
            if name == sheet:
                text = body.decode("utf-8")
                assert "</sheetData>" in text and "<mergeCells" not in text
                text = text.replace(
                    "</sheetData>",
                    f'</sheetData><mergeCells count="{len(refs)}">{merges}</mergeCells>',
                    1,
                )
                if utf16:
                    text = re.sub(r"^<\?xml[^>]*\?>", "", text)
                    text = '<?xml version="1.0" encoding="UTF-16"?>' + text
                body = text.encode("utf-16" if utf16 else "utf-8")
                name = named or name
            elif named and name in ("xl/_rels/workbook.xml.rels", "[Content_Types].xml"):
                body = body.replace(b"/xl/worksheets/sheet1.xml", f"/{named}".encode()).replace(
                    b"worksheets/sheet1.xml", named.removeprefix("xl/").encode()
                )
            dst.writestr(name, body)
    return out.getvalue()


def test_a_merge_over_the_cell_cap_is_refused_before_the_workbook_is_read():
    """🔴 Red before: the read made an object for each of the merge's million cells, and went on."""
    with pytest.raises(DocumentTooLarge, match="merged"):
        parse_xlsx(_with_merges("A1:J100000"))


def test_merges_that_add_up_past_the_cap_are_refused():
    """🔴 Red before: each merge is under the cap, and together they are not."""
    half = MAX_CELLS // 2 + 1  # two of these are one more than the cap allows
    rows = half // 2
    with pytest.raises(DocumentTooLarge, match="merged"):
        parse_xlsx(_with_merges(f"A1:B{rows + 1}", f"C1:D{rows + 1}"))


def test_a_merge_as_large_as_a_sheet_is_refused_at_once():
    """The largest a sheet can declare (17 billion cells). Without the check the read does not end;
    so this one runs only against it."""
    with pytest.raises(DocumentTooLarge, match="merged"):
        parse_xlsx(_with_merges("A1:XFD1048576"))


@pytest.mark.parametrize(
    "ref",
    ["A:A", "1:1048576", "A1&#58;J100000", "a1:j100000"],
    ids=["a whole column", "every row", "its colon encoded", "lower case"],
)
def test_a_merge_written_any_way_openpyxl_reads_is_counted(ref):
    """The count reads a ref the way the read does, so no spelling of one gets past it."""
    with pytest.raises(DocumentTooLarge, match="merged"):
        parse_xlsx(_with_merges(ref))


@pytest.mark.parametrize(
    "how",
    [{"utf16": True}, {"named": "xl/worksheets/sheet1.dat"}],
    ids=["a sheet in UTF-16", "a sheet not named .xml"],
)
def test_a_merge_in_any_sheet_openpyxl_reads_is_counted(how):
    """openpyxl reads a sheet in any encoding its parser takes, and finds it by the package's
    relationships, whatever it is called: so the count reads every part."""
    with pytest.raises(DocumentTooLarge, match="merged"):
        parse_xlsx(_with_merges("A1:J100000", **how))


@pytest.mark.parametrize(
    "how",
    [{"utf16": True}, {"named": "xl/worksheets/sheet1.dat"}],
    ids=["a sheet in UTF-16", "a sheet not named .xml"],
)
def test_such_a_sheet_within_the_cap_is_still_read(how):
    """The control for each: openpyxl really reads that sheet, merge and all."""
    model, _ = parse_xlsx(_with_merges("A1:B1", **how))

    assert model.sheets[0].merges == ["A1:B1"]


def test_a_merge_within_the_cap_is_read_as_before():
    model, _ = parse_xlsx(_with_merges("A1:B1"))

    assert model.sheets[0].merges == ["A1:B1"]
    assert model.sheets[0].cells[0][0].value == "Title"
