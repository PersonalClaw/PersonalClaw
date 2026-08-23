"""The run/style/cell half of the document model — derivation, precedence, neutrality.

`tests/test_documents.py` proves the pre-existing model still behaves; this file proves the
additive fields do what they claim, that they never overwrite an author's explicit value,
and that the module stayed free of format-specific vocabulary.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from personalclaw.documents import Block, Cell, DocumentModel, PageSetup, ParagraphStyle, Run
from personalclaw.documents import model as model_module
from personalclaw.documents.model import ALIGNMENTS, BLOCK_KINDS, ORIENTATIONS

# --- the two done-when clauses, directly ------------------------------------------------


def test_runs_only_block_answers_text() -> None:
    block = Block(
        kind="paragraph",
        runs=[Run(text="plain "), Run(text="bold", bold=True), Run(text=" tail")],
    )
    assert block.text == "plain bold tail"
    # The rich view is preserved, not consumed — a writer that understands runs still sees
    # the formatting the derivation flattened away.
    assert [run.bold for run in block.runs] == [False, True, False]


def test_cells_only_table_answers_rows() -> None:
    block = Block(
        kind="table",
        cells=[
            [Cell(runs=[Run(text="Region")], bold=True), Cell(text="Total", bold=True)],
            [Cell(text="EU"), Cell(runs=[Run(text="1"), Run(text="2")])],
        ],
    )
    assert block.rows == [["Region", "Total"], ["EU", "12"]]


# --- precedence: an explicit value is an override, and overrides are load-bearing --------


def test_explicit_text_beside_runs_wins() -> None:
    # Recomputing this would make a deliberate override impossible to express: the author
    # is choosing a plain-text rendering of rich runs that spells out the link target.
    block = Block(
        kind="paragraph",
        text="see the docs <https://example.invalid>",
        runs=[Run(text="see the "), Run(text="docs", link="https://example.invalid")],
    )
    assert block.text == "see the docs <https://example.invalid>"
    assert block.runs[1].link == "https://example.invalid"
    # The override MUST differ from what derivation would produce, or this test cannot tell
    # "explicit value kept" from "explicit value recomputed to the same string".
    assert "".join(run.text for run in block.runs) != block.text


def test_explicit_rows_beside_cells_wins() -> None:
    block = Block(
        kind="table",
        rows=[["header"], ["explicit"]],
        cells=[[Cell(text="derived")]],
    )
    assert block.rows == [["header"], ["explicit"]]
    assert block.cells[0][0].text == "derived"
    # Same discriminator: the explicit grid differs from the derived one.
    assert [[cell.text for cell in row] for row in block.cells] != block.rows


def test_cell_derives_its_own_text_but_never_clobbers_it() -> None:
    assert Cell(runs=[Run(text="a"), Run(text="b")]).text == "ab"
    cell = Cell(text="kept", runs=[Run(text="ignored")])
    assert cell.text == "kept"
    assert "".join(run.text for run in cell.runs) != cell.text


def test_empty_runs_list_leaves_text_exactly_as_passed() -> None:
    # Including the empty string: an absent rich view must not make `text` "unset-ish".
    assert Block(kind="paragraph", runs=[]).text == ""
    assert Block(kind="paragraph", text="", runs=[]).text == ""
    assert Block(kind="paragraph", text="held", runs=[]).text == "held"
    assert Cell(runs=[]).text == ""
    assert Cell(text="held", runs=[]).text == "held"
    # ...and an absent `cells` leaves `rows` alone in both directions.
    assert Block(kind="table", cells=[]).rows == []
    assert Block(kind="table", rows=[["held"]], cells=[]).rows == [["held"]]


# --- defaults: a style flag must never arrive enabled -----------------------------------


@pytest.mark.parametrize("cls", [Run, Cell, ParagraphStyle, PageSetup])
def test_style_dataclass_defaults_are_all_falsy(cls: type) -> None:
    for spec in dataclasses.fields(cls):
        if spec.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = spec.default_factory()  # type: ignore[misc]
        else:
            default = spec.default
        assert default is not dataclasses.MISSING, f"{cls.__name__}.{spec.name} has no default"
        assert not default, f"{cls.__name__}.{spec.name} defaults to {default!r}, not a falsy value"


def test_document_model_page_defaults_to_none_and_block_style_too() -> None:
    assert DocumentModel().page is None
    assert Block(kind="paragraph").style is None


def test_appended_fields_keep_positional_construction_working() -> None:
    # The additive fields sit AFTER the originals, so a positional caller is unaffected.
    block = Block("heading", "Title", 2)
    assert (block.kind, block.text, block.level) == ("heading", "Title", 2)
    assert (block.runs, block.cells, block.style) == ([], [], None)


# --- 0.0 means "writer default", and stays distinguishable from a real small value -------


def test_zero_floats_are_distinguishable_from_an_explicit_small_value() -> None:
    unset = ParagraphStyle()
    tight = ParagraphStyle(space_before_pt=0.5, space_after_pt=0.5, line_spacing=0.9)
    assert (unset.space_before_pt, unset.space_after_pt, unset.line_spacing) == (0.0, 0.0, 0.0)
    assert unset.line_spacing != tight.line_spacing
    assert unset.space_before_pt != tight.space_before_pt
    # A writer asking "did the author set this?" must get False for unset and True for a
    # deliberately tight value — a default substituted at construction time would erase
    # that distinction and make "tight" unrequestable.
    assert not unset.line_spacing
    assert tight.line_spacing
    assert PageSetup().margin_in == 0.0
    assert PageSetup(margin_in=0.25).margin_in == 0.25
    assert PageSetup(margin_in=0.25).margin_in != PageSetup().margin_in


# --- enum-ish validation: unknown values RAISE, matching Block's `kind` ------------------
#
# Decision: raise, do NOT normalise to "". An unknown `align`/`orientation` is a typo
# ("centre", "landscpae"), and normalising it produces a file that looks plausible while
# silently ignoring the author's layout intent — the user would have to eyeball the output
# to notice. Raising names the bad value at construction time, next to the code that wrote
# it. It also matches `Block.__post_init__`'s existing treatment of an unknown `kind` in
# this same module, so one module has one failure mode rather than two.


@pytest.mark.parametrize("align", ALIGNMENTS)
def test_every_declared_alignment_is_accepted(align: str) -> None:
    assert ParagraphStyle(align=align).align == align
    assert Cell(align=align).align == align


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_every_declared_orientation_is_accepted(orientation: str) -> None:
    assert PageSetup(orientation=orientation).orientation == orientation


def test_unknown_alignment_raises_rather_than_normalising() -> None:
    with pytest.raises(ValueError, match="unknown alignment"):
        ParagraphStyle(align="centre")
    with pytest.raises(ValueError, match="unknown alignment"):
        Cell(align="middle")
    # The rejection path is the point: nothing is quietly rewritten to "".
    with pytest.raises(ValueError, match="unknown alignment"):
        Block(kind="paragraph", style=ParagraphStyle(align="LEFT"))


def test_unknown_orientation_raises_rather_than_normalising() -> None:
    with pytest.raises(ValueError, match="unknown orientation"):
        PageSetup(orientation="landscpae")
    with pytest.raises(ValueError, match="unknown orientation"):
        DocumentModel(page=PageSetup(orientation="sideways"))


# --- vacuity floor: the derivation must work on unhappy input too -----------------------


def test_block_kinds_is_non_empty() -> None:
    assert BLOCK_KINDS
    assert "paragraph" in BLOCK_KINDS


def test_all_empty_runs_derive_empty_string_rather_than_raising() -> None:
    # A derivation that only works on happy input is not a derivation.
    assert Block(kind="paragraph", runs=[Run(), Run()]).text == ""
    assert Block(kind="paragraph", runs=[Run(text="")]).text == ""
    assert Cell(runs=[Run(), Run()]).text == ""
    # An empty cell grid row still yields a row, not a dropped one.
    assert Block(kind="table", cells=[[], [Cell()]]).rows == [[], [""]]


# --- the vendor-neutrality guardrail, as a rail ------------------------------------------

#: Vocabulary that belongs to a writer, never to the model.
FORMAT_VOCABULARY = ("docx", "pptx", "xlsx", "w:", "rpr", "ooxml")


def _code_below_module_docstring(source: str) -> str:
    """Everything after the module docstring.

    The module docstring is where the guardrail is *declared*, so it legitimately names the
    vocabulary it forbids; scanning it would make the rail unfalsifiable-by-construction.
    Everything below it — code, comments and class docstrings alike — must stay neutral.
    """
    first = ast.parse(source).body[0]
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)):
        return source
    return "\n".join(source.splitlines()[first.end_lineno or 0 :])


def _format_vocabulary_hits(source: str) -> list[str]:
    haystack = _code_below_module_docstring(source).lower()
    return [token for token in FORMAT_VOCABULARY if token in haystack]


def test_model_module_names_no_file_format_vocabulary() -> None:
    source = Path(model_module.__file__).read_text(encoding="utf-8")
    # Vacuity guard 1: we are scanning real code, not an empty string left by the strip.
    body = _code_below_module_docstring(source)
    assert "BLOCK_KINDS" in body
    assert "class Run" in body
    assert len(body) > 0.5 * len(source)

    assert _format_vocabulary_hits(source) == []


def test_the_vocabulary_rail_can_actually_fail() -> None:
    # Vacuity guard 2: a rail that matches nothing looks clean. Prove it fires.
    offender = '"""Module docstring."""\n\nWRITER = "the .docx writer"\n'
    assert _format_vocabulary_hits(offender) == ["docx"]

    # Vacuity guard 3: the real module's docstring DOES contain forbidden vocabulary (it
    # states the guardrail), so the scan is only clean because the strip is deliberate —
    # not because the tokens are impossible to find in this file.
    source = Path(model_module.__file__).read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    assert any(token in docstring.lower() for token in FORMAT_VOCABULARY)
