"""A workbook's next version keeps its formulas and formatting.

The agent changes a workbook artifact by reading it with ``artifact_get``, which shows each formula
cell as its formula text, and writing the next version with ``sheet_create(slug, base, sheets)``.
That version was built from the rows it was sent alone (``SheetModel.from_rows``, made for new
workbooks): every formula came back as text, and the number formats, fills, column widths and
merged cells were gone. A chart or conditional formatting went without a word.

Now the next version starts from the version the agent read. A cell sent back with exactly its own
formula keeps it as a formula, every cell the base has keeps its presentation, and each sheet keeps
its widths and frozen header. A merge is kept unless a cell inside it now holds a value. Anything
else the version cannot carry is refused before anything is written, until ``drop_unsupported``
says she agreed, and the reply names each formula replaced and each merge dropped.
"""

from __future__ import annotations

import io
import json
import re
from unittest.mock import patch

import pytest

from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.documents.model import Sheet, SheetCell, SheetModel
from personalclaw.documents.writers.xlsx_writer import render_xlsx
from personalclaw.documents.xlsx_parser import parse_xlsx

CHAT = "dashboard:chat-1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MONEY = "$#,##0.00"


@pytest.fixture
def store(tmp_path):
    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    with (
        patch("personalclaw.artifacts.registry.get_provider", return_value=provider),
        patch("personalclaw.mcp_artifacts._resolve_session_key", return_value=CHAT),
    ):
        yield provider


def _budget() -> SheetModel:
    """Her budget, as she made it in the sheet editor: a merged title with a fill, a header, two
    months in a currency format (one filled), and a total row that sums them."""
    c = SheetCell
    return SheetModel(
        sheets=[
            Sheet(
                name="Budget",
                cells=[
                    [c(value="Budget", bold=True, fill="DDEEFF"), c(value=None)],
                    [c(value="Month", bold=True), c(value="Amount", bold=True, align="right")],
                    [c(value="Jan"), c(value=1200, number_format=MONEY)],
                    [c(value="Feb"), c(value=900, number_format=MONEY, fill="FFF2CC")],
                    [c(value="Total", bold=True), c(formula="=SUM(B3:B4)", number_format=MONEY)],
                ],
                column_widths=[14.0, 18.0],
                merges=["A1:B1"],
                frozen_header=True,
            )
        ]
    )


def _workbook(store, data: bytes, name: str = "Budget") -> str:
    return store.create_binary(
        name=name, data=data, mime=XLSX, kind="xlsx", source="chat", actor="user"
    ).slug


def _tool(name: str, args: dict) -> str:
    from personalclaw.mcp_artifacts import _call_tool

    return _call_tool(name, args)


def _read(slug: str) -> tuple[dict, str]:
    """The reading the agent writes from: each sheet's rows as JSON, and its base."""
    reading = _tool("artifact_get", {"slug": slug})
    body = re.search(r"<untrusted_content[^>]*>\n(.*?)\n</untrusted_content>", reading, re.S)
    base = re.search(r"\bbase:? '?(v\d+-[0-9a-f]{16})", reading, re.IGNORECASE)
    assert body and base, reading
    return json.loads(body.group(1)), base.group(1)


def _write(slug: str, base: str, sheets: dict, **extra) -> str:
    return _tool(
        "sheet_create", {"slug": slug, "base": base, "sheets": json.dumps(sheets), **extra}
    )


def _live(store, slug: str) -> Sheet:
    data, _ = store.raw_bytes(slug)
    return parse_xlsx(data)[0].sheets[0]


def test_a_workbook_written_back_from_its_reading_is_the_workbook_it_was(store):
    """🔴 Red before: =SUM(B3:B4) came back as the text "=SUM(B3:B4)", and the currency format,
    the fills, the right-aligned header, the column widths and the merged title were gone."""
    slug = _workbook(store, render_xlsx(_budget()))
    sheets, base = _read(slug)

    done = _write(slug, base, sheets)

    assert done.startswith("Updated xlsx: budget (v2"), done
    assert "Kept 1 formula." in done
    assert _live(store, slug) == parse_xlsx(render_xlsx(_budget()))[0].sheets[0]


def test_a_changed_value_keeps_its_cells_format(store):
    slug = _workbook(store, render_xlsx(_budget()))
    sheets, base = _read(slug)
    sheets["Budget"][3][1] = 950

    _write(slug, base, sheets)

    feb = _live(store, slug).cells[3][1]
    assert (feb.value, feb.number_format, feb.fill) == (950, MONEY, "FFF2CC")


def test_a_value_that_is_not_the_cells_own_formula_is_still_text(store):
    """The carry is of her own formula at its own cell: any other value starting with "=" stays a
    literal, as the agent's text always has (``SheetModel.from_rows``)."""
    slug = _workbook(store, render_xlsx(_budget()))
    sheets, base = _read(slug)
    sheets["Budget"][2][0] = "=B3*2"  # a cell that held "Jan", never a formula
    sheets["Budget"][4][0] = "=SUM(B3:B4)"  # her formula, at another cell

    _write(slug, base, sheets)

    live = _live(store, slug)
    assert (live.cells[2][0].formula, live.cells[2][0].value) == ("", "=B3*2")
    assert (live.cells[4][0].formula, live.cells[4][0].value) == ("", "=SUM(B3:B4)")
    assert live.cells[4][1].formula == "=SUM(B3:B4)"


def test_a_formula_moved_down_a_row_is_text_and_the_reply_names_the_one_it_replaced(store):
    """A March row added above the total moves the total down a row, so her formula is no longer
    at its own cell: it is written as text, and the reply names the formula a value replaced."""
    slug = _workbook(store, render_xlsx(_budget()))
    sheets, base = _read(slug)
    rows = sheets["Budget"]
    rows.insert(4, ["Mar", 700])

    done = _write(slug, base, sheets)

    live = _live(store, slug)
    assert (live.cells[5][1].formula, live.cells[5][1].value) == ("", "=SUM(B3:B4)")
    assert live.cells[4][1].value == 700
    assert "Budget!B5's formula =SUM(B3:B4) was replaced by a value." in done


def test_a_merge_with_a_value_inside_it_is_dropped_and_named(store):
    slug = _workbook(store, render_xlsx(_budget()))
    sheets, base = _read(slug)
    sheets["Budget"][0][1] = "2026"

    done = _write(slug, base, sheets)

    assert _live(store, slug).merges == []
    assert "Budget!A1:B1 is no longer merged: a cell inside it now holds a value." in done


def _with_conditional_formatting(data: bytes) -> bytes:
    from openpyxl import load_workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill

    wb = load_workbook(io.BytesIO(data))
    red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    wb["Budget"].conditional_formatting.add(
        "B3:B4", CellIsRule(operator="greaterThan", formula=["1000"], fill=red)
    )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_a_workbook_holding_what_a_version_cannot_carry_is_refused_until_she_agrees(store):
    """🔴 Red before: the conditional formatting was dropped without a word."""
    slug = _workbook(store, _with_conditional_formatting(render_xlsx(_budget())))
    sheets, base = _read(slug)

    refused = _write(slug, base, sheets)

    assert refused.startswith("Error [ERR_WORKBOOK_FEATURES_DROPPED]:"), refused
    assert "1 conditional formatting on this sheet are not carried by the model (Budget)" in (
        refused
    )
    assert "drop_unsupported" in refused and "Nothing was written." in refused
    assert store.get(slug).version == 1

    done = _write(slug, base, sheets, drop_unsupported=True)

    assert done.startswith("Updated xlsx: budget (v2"), done
    assert "This version does not carry: 1 conditional formatting" in done
    assert _live(store, slug).cells[4][1].formula == "=SUM(B3:B4)"


def test_a_new_workbook_is_plain_with_a_bold_frozen_header(store):
    """The control: a call that names no workbook builds a new one from its rows, as before."""
    done = _tool("sheet_create", {"name": "Fresh", "rows": '[["Month", "Amount"], ["Jan", 5]]'})

    assert done.startswith("Created xlsx: fresh (v1"), done
    live = _live(store, "fresh")
    assert live.frozen_header and live.cells[0][0].bold and not live.cells[1][0].bold
    assert (live.column_widths, live.merges) == ([], [])


@pytest.mark.parametrize(("inside", "kept"), [(None, ["A1:XFD1048576"]), ("2026", [])])
def test_a_merge_as_large_as_a_sheet_is_judged_by_the_cells_the_rows_hold(inside, kept):
    """A merge is the file's word, and it can cover a whole sheet. Whether a cell inside it holds
    a value is read from the rows sent, not by walking the range, so a few rows take a few
    steps (the walk was 17 billion)."""
    from personalclaw.documents.docx_parser import LossReport
    from personalclaw.documents.sheet_carry import carry_from_base

    base = SheetModel(
        sheets=[
            Sheet(
                name="Wide",
                cells=[[SheetCell(value="Title"), SheetCell(value=None)]],
                merges=["A1:XFD1048576"],
            )
        ]
    )
    new = SheetModel.from_rows({"Wide": [["Title", inside]]})

    carried = carry_from_base(new, base, LossReport())

    assert carried.model.sheets[0].merges == kept
    assert carried.merges_dropped == ([] if kept else ["Wide!A1:XFD1048576"])


def test_a_workbook_read_only_in_part_is_refused_until_she_agrees(store, monkeypatch):
    """A workbook too big to read whole holds rows its reading never reached, and a version written
    from that reading would drop them all: it is refused, naming where reading stopped, until she
    says yes."""
    monkeypatch.setattr("personalclaw.documents.xlsx_parser.MAX_CELLS", 6)
    slug = _workbook(store, render_xlsx(_budget()))
    sheets, base = _read(slug)
    assert len(sheets["Budget"]) == 3, "the reading stops after the cells it may read"

    refused = _write(slug, base, sheets)

    assert refused.startswith("Error [ERR_WORKBOOK_FEATURES_DROPPED]:"), refused
    assert "Reading stopped at row 4 of sheet 'Budget'" in refused
    assert store.get(slug).version == 1

    done = _write(slug, base, sheets, drop_unsupported="yes")

    assert done.startswith("Updated xlsx: budget (v2"), done
