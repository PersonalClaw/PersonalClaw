"""A spreadsheet the agent makes from CSV text holds its plain numbers as numbers.

``sheet_create``'s ``csv`` input is read by Knowledge's reader (``delimited_rows``), which returns
every cell as text, and the workbook was built from that text as it was: every amount landed as a
number stored as text, summed to 0 and sorted "100" before "20". The same data as JSON ``rows``
kept its numbers, and the tool tells the agent to keep numbers as numbers.

Now a cell below the header whose text is a plain number, one that a workbook writes back as the
same text, is written as a number. Everything that would change its look as a bare number stays
text: a leading zero, a separator, a currency or percent sign, a plus sign, an exponent, a
trailing zero, spaces, and more digits than a workbook holds exactly. The header row stays text,
and Knowledge's own reading of a .csv file is unchanged.
"""

from __future__ import annotations

import io

import pytest


def _quiet(*_args, **_kwargs) -> None:
    pass


def _cells_from(tmp_path, monkeypatch, text: str) -> list[list[object]]:
    """The cells of the spreadsheet ``sheet_create`` makes from *text*, as the file holds them:
    a number as a number, text as text."""
    from openpyxl import load_workbook

    from personalclaw.artifacts.native import NativeArtifactProvider
    from personalclaw.mcp_artifacts import _document_create

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    prov = NativeArtifactProvider(root=tmp_path / "artifacts")
    reply = _document_create(prov, "sheet_create", {"name": "Costs", "csv": text}, None, _quiet)
    assert "Error" not in reply, reply
    [made] = prov.list()
    body = prov.raw_bytes(made.slug)
    assert body is not None
    sheet = load_workbook(io.BytesIO(body[0])).active
    return [[cell.value for cell in row] for row in sheet.iter_rows()]


def test_a_plain_number_in_csv_text_is_a_number_the_sheet_can_sum(tmp_path, monkeypatch):
    """🔴 Red before: "12" and "3.5" were written as text, so the column summed to 0."""
    cells = _cells_from(tmp_path, monkeypatch, "item,amount\nA,12\nB,3.5\nC,-4\nD,0.25\n")

    assert cells == [["item", "amount"], ["A", 12], ["B", 3.5], ["C", -4], ["D", 0.25]]
    assert all(type(row[1]) in (int, float) for row in cells[1:])


@pytest.mark.parametrize(
    "text",
    [
        "007",  # a leading zero: a code, not 7
        "1,234",  # a separator
        "$45.20",
        "12.5%",
        "+5",
        "1e3",  # an exponent: it would show as 1000
        "1.50",  # a trailing zero: it would show as 1.5
        "-0",
        " 12 ",
        "2026-10-06",
        "12345678901234567",  # more digits than a workbook holds exactly
        "nan",
        "inf",
        "",
    ],
)
def test_text_that_would_change_its_look_as_a_number_stays_text(tmp_path, monkeypatch, text):
    cells = _cells_from(tmp_path, monkeypatch, f'item,code\nA,"{text}"\n')

    assert cells[1][1] == (text or None), cells


def test_the_header_row_stays_text(tmp_path, monkeypatch):
    """Row 0 is the header ``from_rows`` makes bold and frozen: a year heading is a label."""
    cells = _cells_from(tmp_path, monkeypatch, "item,2024,2025\nA,10,12\n")

    assert cells[0] == ["item", "2024", "2025"]
    assert cells[1] == ["A", 10, 12]


def test_knowledges_reading_of_csv_text_is_unchanged():
    """Knowledge reads .csv files through the same reader, and keeps every cell as its text."""
    from personalclaw.knowledge.readers import delimited_rows

    assert delimited_rows("item,amount\nA,12\nB,3.5\n") == [
        ["item", "amount"],
        ["A", "12"],
        ["B", "3.5"],
    ]
