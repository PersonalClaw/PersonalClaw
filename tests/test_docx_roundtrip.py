"""DFE-3: parse → write → parse, across every `BLOCK_KIND`, and the proof it is not vacuous.

The fixture is one rich `DocumentModel` exercising **every** member of `BLOCK_KINDS` plus
inline run spans, paragraph styles, table cells and page setup. It is rendered by this
repo's writer, parsed, re-rendered and re-parsed, and the suite makes three separate
claims about that circuit:

1. `test_the_round_trip_recovers_the_authored_model` — the parse of the writer's output
   equals the model that was authored. This is the claim with teeth.
2. `test_parse_write_parse_is_stable` — the second lap equals the first. Idempotence
   catches a *classification* bug that claim 1 cannot: a parser that reads a construct
   into a shape it then re-renders differently would loop forever without converging.
3. `test_a_deliberate_writer_regression_reds_the_round_trip` — with one writer property
   suppressed, claim 1 must FAIL.

Claim 3 exists because claims 1 and 2 are not interchangeable, and the difference is the
whole reason the acceptance bar demands a regression test. **Idempotence alone is blind to
a uniform writer regression**: if the writer stopped emitting bold, the first parse would
report no bold, the re-render would emit no bold, and the second parse would agree — a
perfectly stable round trip over a document that lost its formatting. Only comparing
against the AUTHORED model can see that, which is why claim 3 asserts an `AssertionError`
out of claim 1's helper and not out of claim 2's.

The comparison runs over a canonical projection rather than raw dataclass equality,
because the model deliberately offers two ways to say the same thing (`text` beside
`runs`, `rows` beside `cells`) and the writer renders them identically. The projection
collapses exactly those documented equivalences and NOTHING else — every property the
writer can express stays in the comparison, which is what makes the regressions red.
"""

from __future__ import annotations

import pytest

from personalclaw.documents.docx_parser import parse_docx
from personalclaw.documents.model import (
    BLOCK_KINDS,
    Block,
    Cell,
    DocumentModel,
    PageSetup,
    ParagraphStyle,
    Run,
)
from personalclaw.documents.writers import docx_writer
from personalclaw.documents.writers.docx_writer import render_docx

_URL = "https://example.invalid/spec"
_OTHER_URL = "https://example.invalid/other"


def _rich_model() -> DocumentModel:
    """Every `BLOCK_KIND`, plus inline spans, styles, cells and a page setup.

    Margins are declared explicitly: python-docx's default template is 1.00in top/bottom
    and 1.25in left/right, which `PageSetup.margin_in` cannot hold, so a fixture that left
    the page unset would carry an unavoidable loss item and could not assert losslessness.
    """
    return DocumentModel(
        title="Fidelity Fixture",
        page=PageSetup(orientation="landscape", margin_in=0.9),
        blocks=[
            Block(kind="heading", text="Chapter One", level=1),
            Block(kind="heading", text="A Deeper Section", level=4),
            # Inline spans: adjacent runs with DIFFERENT formatting, a code span and a
            # link in the middle of a sentence rather than at either end, so a writer or
            # parser that appends out of order shows up as reordered text.
            Block(
                kind="paragraph",
                runs=[
                    Run(text="Plain then "),
                    Run(text="bold", bold=True),
                    Run(text=" then "),
                    Run(text="italic", italic=True),
                    Run(text=" then "),
                    Run(text="mono()", code=True),
                    Run(text=" then "),
                    Run(text="a link", link=_URL),
                    Run(text=" and a tail."),
                ],
                style=ParagraphStyle(
                    align="justify",
                    space_before_pt=8.0,
                    space_after_pt=4.0,
                    line_spacing=1.25,
                ),
            ),
            Block(
                kind="paragraph",
                text="A centered plain paragraph.",
                style=ParagraphStyle(align="center"),
            ),
            Block(kind="bullets", items=["first bullet", "second bullet", "third"]),
            Block(kind="numbered", items=["step one", "step two"]),
            Block(
                kind="table",
                cells=[
                    [Cell(text="Header A"), Cell(text="Header B")],
                    [
                        Cell(runs=[Run(text="cell "), Run(text="bold", bold=True)]),
                        Cell(text="right", align="right"),
                    ],
                    [
                        Cell(runs=[Run(text="linked", link=_OTHER_URL)]),
                        Cell(runs=[Run(text="mono", code=True)]),
                    ],
                ],
            ),
            Block(kind="table", rows=[["plain h1", "plain h2"], ["r1c1", "r1c2"]]),
            Block(kind="code", text="def f(x):\n    return x"),
            Block(kind="pagebreak"),
            Block(kind="image", artifact_slug="architecture-diagram"),
            Block(kind="paragraph", text="Closing paragraph."),
        ],
    )


# --------------------------------------------------------------------------------------
# the canonical projection
# --------------------------------------------------------------------------------------


def _spans(runs: list[Run], text: str, *, bold: bool = False) -> tuple:
    """Inline spans as comparable tuples, collapsing the model's two ways to say one thing.

    `Block.text` beside `Block.runs` is a documented equivalence — the writer renders a
    plain `text` as a single unformatted run — so both sides project to spans. Adjacent
    equals are merged and empties dropped because a `w:r` boundary is not something the
    format preserves: Word itself splits one phrase across several.
    """
    source = list(runs) if runs else ([Run(text=text)] if text else [])
    out: list[tuple] = []
    for run in source:
        if not run.text:
            continue
        key = (run.bold or bold, run.italic, run.code, run.link)
        if out and out[-1][1:] == key:
            out[-1] = (out[-1][0] + run.text,) + key
            continue
        out.append((run.text,) + key)
    return tuple(out)


def _cells(block: Block) -> tuple:
    rows = block.cells or [[Cell(text=value) for value in row] for row in block.rows]
    return tuple(
        tuple(
            # Row 0 is the header BY CONTRACT: the writer bolds every header cell, so both
            # sides are projected as bold there. Bolding a header is the writer's
            # convention, not a property the model can turn off, and comparing it as
            # authored would call the writer's own contract a round-trip failure.
            (_spans(cell.runs, cell.text, bold=number == 0), cell.align)
            for cell in row
        )
        for number, row in enumerate(rows)
    )


def _style(style: ParagraphStyle | None) -> tuple:
    if style is None:
        return ("", 0.0, 0.0, 0.0)
    return (
        style.align,
        float(style.space_before_pt),
        float(style.space_after_pt),
        float(style.line_spacing),
    )


def _canonical(model: DocumentModel) -> tuple:
    page = model.page or PageSetup()
    return (
        model.title,
        (page.orientation or "portrait", round(page.margin_in, 4)),
        tuple(
            (
                block.kind,
                block.level,
                tuple(block.items),
                block.artifact_slug,
                _spans(block.runs, block.text),
                _cells(block),
                _style(block.style),
            )
            for block in model.blocks
        ),
    )


def _assert_recovers(authored: DocumentModel) -> None:
    """The claim with teeth: parsing the writer's output returns what was authored."""
    parsed, _ = parse_docx(render_docx(authored))
    assert _canonical(parsed) == _canonical(authored)


# --------------------------------------------------------------------------------------
# the claims
# --------------------------------------------------------------------------------------


def test_the_fixture_covers_every_block_kind():
    """The rail behind "across every BLOCK_KIND".

    A new kind added to the model without a line in the fixture reds here, so the
    round-trip claim can never quietly stop covering the whole vocabulary.
    """
    assert {block.kind for block in _rich_model().blocks} == set(BLOCK_KINDS)


def test_the_round_trip_recovers_the_authored_model():
    _assert_recovers(_rich_model())


def test_parse_write_parse_is_stable():
    first, _ = parse_docx(render_docx(_rich_model()))
    second, _ = parse_docx(render_docx(first))

    # Raw dataclass equality here, not the projection: the SECOND lap starts from a model
    # the parser itself produced, so both sides already use the parser's own conventions
    # and any difference is a real instability.
    assert first == second


def test_a_document_this_repo_wrote_parses_with_no_losses():
    """The writer's whole output vocabulary is inside the model — nothing is dropped."""
    _, report = parse_docx(render_docx(_rich_model()))

    assert report.lossless, report.summary()


def test_inline_span_boundaries_survive_the_round_trip():
    parsed, _ = parse_docx(render_docx(_rich_model()))

    (spans,) = [
        _spans(block.runs, block.text)
        for block in parsed.blocks
        if block.kind == "paragraph" and block.runs
    ]
    assert spans == (
        ("Plain then ", False, False, False, ""),
        ("bold", True, False, False, ""),
        (" then ", False, False, False, ""),
        ("italic", False, True, False, ""),
        (" then ", False, False, False, ""),
        ("mono()", False, False, True, ""),
        (" then ", False, False, False, ""),
        ("a link", False, False, False, _URL),
        (" and a tail.", False, False, False, ""),
    )


def test_page_setup_survives_the_round_trip():
    parsed, _ = parse_docx(render_docx(_rich_model()))

    assert parsed.page == PageSetup(orientation="landscape", margin_in=0.9)


def test_table_cells_survive_the_round_trip():
    parsed, _ = parse_docx(render_docx(_rich_model()))

    rich, plain = [block for block in parsed.blocks if block.kind == "table"]
    assert rich.rows == [
        ["Header A", "Header B"],
        ["cell bold", "right"],
        ["linked", "mono"],
    ]
    assert rich.cells[1][1].align == "right"
    assert rich.cells[2][0].runs[0].link == _OTHER_URL
    assert rich.cells[2][1].runs[0].code is True
    assert plain.rows == [["plain h1", "plain h2"], ["r1c1", "r1c2"]]


# --------------------------------------------------------------------------------------
# claim 3 — the regressions that must red
# --------------------------------------------------------------------------------------


def _drop_bold(monkeypatch) -> None:
    original = docx_writer._add_run

    def patched(para, run, *, monospace: bool = False, bold: bool = False):
        stripped = Run(text=run.text, italic=run.italic, code=run.code, link=run.link)
        original(para, stripped, monospace=monospace, bold=False)

    monkeypatch.setattr(docx_writer, "_add_run", patched)


def _drop_code_font(monkeypatch) -> None:
    # The writer's code convention IS the font name, so renaming it is exactly the
    # regression "code runs stop being distinguishable".
    monkeypatch.setattr(docx_writer, "_MONOSPACE", "Arial")


def _drop_link(monkeypatch) -> None:
    monkeypatch.setattr(
        docx_writer,
        "_add_hyperlink",
        lambda para, run, **kwargs: para.add_run(run.text),
    )


def _drop_paragraph_style(monkeypatch) -> None:
    monkeypatch.setattr(docx_writer, "_apply_style", lambda para, style: None)


def _drop_alignment(monkeypatch) -> None:
    monkeypatch.setattr(docx_writer, "_apply_align", lambda para, align: None)


def _drop_page_setup(monkeypatch) -> None:
    monkeypatch.setattr(docx_writer, "_apply_page", lambda doc, page: None)


def _drop_page_breaks(monkeypatch) -> None:
    original = docx_writer._add_block

    def patched(doc, block):
        if block.kind == "pagebreak":
            return
        original(doc, block)

    monkeypatch.setattr(docx_writer, "_add_block", patched)


@pytest.mark.parametrize(
    "regression",
    [
        _drop_bold,
        _drop_code_font,
        _drop_link,
        _drop_paragraph_style,
        _drop_alignment,
        _drop_page_setup,
        _drop_page_breaks,
    ],
    ids=lambda fn: fn.__name__.lstrip("_"),
)
def test_a_deliberate_writer_regression_reds_the_round_trip(monkeypatch, regression):
    """Each regression drops ONE property the writer is supposed to emit.

    The writer is not edited: every regression is a monkeypatch over the module attribute
    the writer looks up at call time, applied AFTER the clean assertion has already passed
    in this same test. That ordering is what makes the falsification complete — the clean
    pass rules out the `raises` succeeding for an unrelated reason.
    """
    authored = _rich_model()
    _assert_recovers(authored)  # the control: green before the regression

    regression(monkeypatch)

    with pytest.raises(AssertionError):
        _assert_recovers(authored)
