"""A document read back as the markup its tool writes it from: the other half of the round trip.

``from_markup`` turns the markdown an agent writes into a model, and ``document_create``,
``deck_create`` and ``sheet_create`` render the model into a file. This module goes the other
way, so the agent can read a document before it writes the next version, in the words it would
write it in: a .docx as the markdown ``document_create`` takes, a .pptx as the outline
``deck_create`` takes, a .xlsx as the JSON ``sheets`` ``sheet_create`` takes, and a PDF as the
text of its pages.

A reading is not a fidelity claim. The markup holds what the tools write (headings, paragraphs,
lists, tables, code and page breaks; slide titles, bullets and notes; sheets of cells), so each
reading says what it leaves out (:attr:`DocumentText.leaves_out`): a next version written from it
comes out in the tool's own style.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from personalclaw.documents.model import Block, DeckModel, DocumentModel, Run, SheetModel

__all__ = [
    "DocumentText",
    "document_text",
    "markdown_of_document",
    "outline_of_deck",
    "sheets_of_workbook",
]

#: The kinds :func:`document_text` reads.
READ_KINDS = ("docx", "pptx", "xlsx", "pdf")


@dataclass(frozen=True)
class DocumentText:
    """A document's text (``text``), what that text is (``form``) and what it leaves out."""

    text: str
    form: str
    leaves_out: tuple[str, ...] = ()


def _flat(text: str) -> str:
    """*text* on one line: the markup reads a line break inside a paragraph, a list item or a
    cell as the end of it."""
    return " ".join((text or "").split("\n"))


def _marked(text: str, mark: str) -> str:
    """*text* between *mark*s, the marks inside any space at its edges (``** x**`` opens no
    emphasis in the markup)."""
    core = text.strip()
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{mark}{core}{mark}{trail}"


def _inline(runs: list[Run], plain: str) -> str:
    """Runs as the inline markdown ``from_markup.parse_inline`` reads back into them: bold,
    italic, code and links. *plain* when there are no runs."""
    if not runs:
        return _flat(plain)
    out: list[str] = []
    for run in runs:
        text = _flat(run.text)
        if not text:
            continue
        if run.code:
            text = f"`{text}`"
        elif run.bold and run.italic:
            text = _marked(text, "***")
        elif run.bold:
            text = _marked(text, "**")
        elif run.italic:
            text = _marked(text, "*")
        if run.link:
            text = f"[{text}]({run.link})"
        out.append(text)
    return "".join(out)


def _table(block: Block) -> list[str]:
    """A table block as markdown table rows, a separator under the first (the header). A cell is
    its plain text: the markup's table holds no formatting (``document_from_markdown`` keeps a
    cell's text only), and the writer makes the header row bold itself."""
    rows = [[_flat(text) for text in row] for row in block.rows]
    lines: list[str] = []
    for index, row in enumerate(rows):
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
        if index == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    return lines


def _block(block: Block) -> str:
    if block.kind == "heading":
        return f"{'#' * block.level} {_inline(block.runs, block.text)}"
    if block.kind == "paragraph":
        return _inline(block.runs, block.text)
    if block.kind == "bullets":
        return "\n".join(f"- {_flat(item)}" for item in block.items)
    if block.kind == "numbered":
        return "\n".join(f"{n}. {_flat(item)}" for n, item in enumerate(block.items, 1))
    if block.kind == "table":
        return "\n".join(_table(block))
    if block.kind == "code":
        return f"```\n{block.text}\n```"
    if block.kind == "pagebreak":
        return "---"
    # An image block is the placeholder paragraph the docx writer writes for it, which reads
    # back as the same block.
    return f"[image: {block.artifact_slug}]"


def markdown_of_document(model: DocumentModel) -> str:
    """*model* as the markdown ``document_create`` takes (``from_markup.document_from_markdown``
    reads it back): the title as the opening ``#``, then each block."""
    parts = [f"# {_flat(model.title)}"] if model.title else []
    parts += [_block(block) for block in model.blocks]
    return "\n\n".join(part for part in parts if part)


def outline_of_deck(model: DeckModel) -> str:
    """*model* as the outline ``deck_create`` takes (``from_markup.deck_from_markdown`` reads it
    back): the deck title as ``#``, a ``##`` per slide, its bullets indented two spaces per level,
    and a ``<!-- notes: … -->`` line per line of its speaker notes."""
    lines = [f"# {_flat(model.title)}", ""] if model.title else []
    for slide in model.slides:
        lines.append(f"## {_flat(slide.title)}")
        lines += [f"{'  ' * bullet.level}- {_flat(bullet.text)}" for bullet in slide.bullets]
        for note in (slide.notes or "").split("\n"):
            if note.strip():
                lines.append(f"<!-- notes: {note.strip().replace('-->', '-- >')} -->")
        lines.append("")
    return "\n".join(lines).strip()


def sheets_of_workbook(model: SheetModel) -> str:
    """*model* as the JSON ``sheets`` ``sheet_create`` takes: each sheet's name and its rows,
    one row to a line, a cell as its value, a formula cell as its formula (``Sheet.rows``)."""
    if not model.sheets:
        return "{}"
    parts = []
    for sheet in model.sheets:
        rows = [json.dumps(row, ensure_ascii=False, default=str) for row in sheet.rows]
        body = ",\n".join(rows)
        parts.append(f"{json.dumps(sheet.name, ensure_ascii=False)}: [\n{body}\n]")
    return "{\n" + ",\n".join(parts) + "\n}"


def _losses(summary: str, lossless: bool) -> tuple[str, ...]:
    if lossless:
        return ()
    return (f"The file also holds what this reading cannot show ({summary}).",)


def _pdf_text(data: bytes) -> str:
    """The text of a PDF's pages, read by the reader ``read_file`` uses (``doc_parser``)."""
    import tempfile
    from pathlib import Path

    from personalclaw.doc_parser import extract_text

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "document.pdf"
        scratch.write_bytes(data)
        return extract_text(str(scratch))


def document_text(kind: str, data: bytes) -> DocumentText:
    """The text of a document of *kind* (one of :data:`READ_KINDS`) whose bytes are *data*.

    Raises what its parser raises on a file it cannot read (``DocumentTooLarge`` over the archive
    caps, a ``zipfile`` or XML error on a damaged one), and ``ValueError`` for another kind.
    """
    if kind == "docx":
        from personalclaw.documents.docx_parser import parse_docx

        document, loss = parse_docx(data)
        return DocumentText(
            text=markdown_of_document(document),
            form="the document as markdown, the input document_create takes",
            leaves_out=(
                "Its fonts, colours, spacing, alignment and page setup are not in this text, "
                "so a version made from it with document_create takes that tool's style.",
                *_losses(loss.summary(), loss.lossless),
            ),
        )
    if kind == "pptx":
        from personalclaw.documents.pptx_parser import parse_pptx

        deck, loss = parse_pptx(data)
        return DocumentText(
            text=outline_of_deck(deck),
            form="the deck as an outline, the input deck_create takes",
            leaves_out=(
                "Its layouts, shape positions and styling are not in this outline, so a "
                "version made from it with deck_create takes that tool's layout.",
                *_losses(loss.summary(), loss.lossless),
            ),
        )
    if kind == "xlsx":
        from personalclaw.documents.xlsx_parser import parse_xlsx

        workbook, loss = parse_xlsx(data)
        return DocumentText(
            text=sheets_of_workbook(workbook),
            form=(
                "the workbook as JSON, the sheets input sheet_create takes: each sheet's rows, "
                "a formula cell as its formula"
            ),
            leaves_out=(
                "Its number formats, cell styles, column widths and merged cells are not in "
                "this text.",
                *_losses(loss.summary(), loss.lossless),
            ),
        )
    if kind == "pdf":
        return DocumentText(
            text=_pdf_text(data),
            form="the text of the PDF's pages",
            leaves_out=("Its layout, images and styling are not in this text.",),
        )
    raise ValueError(f"no reading for a {kind!r} document")
