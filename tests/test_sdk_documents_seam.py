"""``personalclaw.sdk.documents`` — the document-generation seam on the app boundary.

An app that fronts document generation (a brief-to-deck app, a report exporter) has to
reach the writers core ships, or it vendors python-pptx/python-docx and becomes a second
renderer that drifts from the parsers Knowledge reads those formats back with. The facade
is what makes the first option available, so this suite pins the three properties an app
front depends on:

* every promoted name resolves, and resolves to the SAME object core uses (a facade that
  re-implements is a second implementation wearing a re-export's clothes);
* ``register_writer`` is deliberately NOT promoted — an app-registered format would be
  visible to every other caller of the shared registry while that app is enabled and gone
  when it is disabled, so ``available_formats()`` would stop being a property of the build;
* the markdown → model → bytes path an app actually walks produces a file the matching
  core parser reads back, which is the only definition of "openable" worth asserting.
"""

from __future__ import annotations

import personalclaw.documents as core_documents
from personalclaw.documents.from_markup import deck_from_markdown as core_deck_from_markdown
from personalclaw.documents.pptx_parser import parse_pptx
from personalclaw.sdk.documents import (
    Block,
    Bullet,
    Cell,
    DeckModel,
    DocumentModel,
    PageSetup,
    ParagraphStyle,
    Run,
    ShapeBox,
    Sheet,
    SheetCell,
    SheetModel,
    Slide,
    available_formats,
    deck_from_dict,
    deck_from_markdown,
    deck_to_dict,
    document_from_dict,
    document_from_html,
    document_from_markdown,
    document_to_dict,
    get_writer,
    sheet_from_dict,
    sheet_to_dict,
)

BRIEF = """# Launch review

## Where we are
- Two writers registered
- One seam, not two

## Ask
- Ship it
"""


def test_every_promoted_name_is_the_core_object_not_a_copy() -> None:
    """A facade that re-implements would pass an isinstance check and still be a fork."""
    for name, promoted in (
        ("DocumentModel", DocumentModel),
        ("DeckModel", DeckModel),
        ("SheetModel", SheetModel),
        ("Block", Block),
        ("Bullet", Bullet),
        ("Cell", Cell),
        ("PageSetup", PageSetup),
        ("ParagraphStyle", ParagraphStyle),
        ("Run", Run),
        ("ShapeBox", ShapeBox),
        ("Sheet", Sheet),
        ("SheetCell", SheetCell),
        ("Slide", Slide),
        ("available_formats", available_formats),
        ("get_writer", get_writer),
        ("document_from_dict", document_from_dict),
        ("document_to_dict", document_to_dict),
        ("deck_from_dict", deck_from_dict),
        ("deck_to_dict", deck_to_dict),
        ("sheet_from_dict", sheet_from_dict),
        ("sheet_to_dict", sheet_to_dict),
    ):
        assert promoted is getattr(core_documents, name), f"{name} is not core's object"
    assert deck_from_markdown is core_deck_from_markdown


def test_the_markup_authoring_path_is_promoted() -> None:
    """Markdown in is the PRIMARY path — an app that had to hand-build models would learn
    the model vocabulary instead of writing the source a model is already good at."""
    for fn in (document_from_markdown, document_from_html, deck_from_markdown):
        assert callable(fn)


def test_register_writer_is_not_on_the_app_boundary() -> None:
    """A format an app could register would make available_formats() app-dependent."""
    import personalclaw.sdk.documents as sdk_documents

    assert "register_writer" not in sdk_documents.__all__
    assert not hasattr(sdk_documents, "register_writer")
    # It is still core's own seam — this is a boundary decision, not a removal.
    assert hasattr(core_documents, "register_writer")


def test_available_formats_reports_the_build_not_a_declaration() -> None:
    """Registration IS the availability check, so every reported format has a writer."""
    formats = available_formats()
    assert formats, "no document writer registered in this build"
    for fmt in formats:
        assert get_writer(fmt) is not None, f"{fmt} is offered but has no writer"


def test_an_unavailable_format_gets_no_writer_rather_than_raising() -> None:
    """An app front turns None into a caller-facing refusal; an exception it would have to
    catch at every call site is how a format check gets skipped."""
    assert get_writer("epub") is None
    assert get_writer("") is None


def test_a_markdown_brief_renders_a_deck_that_reads_back() -> None:
    """The whole path an app walks: markdown → DeckModel → pptx bytes → core's parser."""
    if "pptx" not in available_formats():  # pragma: no cover — build without python-pptx
        return
    model = deck_from_markdown(BRIEF, title="Launch review")
    assert isinstance(model, DeckModel)
    data = get_writer("pptx")(model)  # type: ignore[misc]
    deck, _loss = parse_pptx(data)
    titles = [s.title for s in deck.slides]
    assert "Where we are" in titles and "Ask" in titles
    assert "One seam, not two" in [b.text for s in deck.slides for b in s.bullets]


def test_a_markdown_brief_renders_a_document_model() -> None:
    model = document_from_markdown(BRIEF, title="Launch review")
    assert isinstance(model, DocumentModel)
    assert model.title == "Launch review"
    assert any(b.kind == "heading" and b.text == "Where we are" for b in model.blocks)
