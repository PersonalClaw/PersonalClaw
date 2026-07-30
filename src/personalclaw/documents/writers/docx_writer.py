"""DocumentModel → .docx bytes (python-docx).

The one place OOXML wordprocessing vocabulary is allowed. Every `Block.kind` is handled
explicitly — an unhandled kind would silently drop content, so the fallthrough renders
the block's text as a paragraph rather than nothing.
"""

from __future__ import annotations

import io

from personalclaw.documents.model import Block, DocumentModel
from personalclaw.documents.registry import register_writer


def render_docx(model: object) -> bytes:
    from docx import Document

    if not isinstance(model, DocumentModel):
        raise TypeError("docx writer expects a DocumentModel")
    doc = Document()
    if model.title:
        doc.add_heading(model.title, level=0)
    for block in model.blocks:
        _add_block(doc, block)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_block(doc, block: Block) -> None:
    kind = block.kind
    if kind == "heading":
        doc.add_heading(block.text, level=block.level)
    elif kind == "paragraph":
        doc.add_paragraph(block.text)
    elif kind == "bullets":
        for item in block.items:
            doc.add_paragraph(item, style="List Bullet")
    elif kind == "numbered":
        for item in block.items:
            doc.add_paragraph(item, style="List Number")
    elif kind == "table":
        _add_table(doc, block.rows)
    elif kind == "code":
        # No code style in the default template; a monospace run conveys the intent
        # without depending on a style that may not exist in a user-supplied template.
        para = doc.add_paragraph()
        run = para.add_run(block.text)
        run.font.name = "Courier New"
    elif kind == "pagebreak":
        doc.add_page_break()
    elif kind == "image":
        # Images reference an artifact; resolving one to bytes is the CALLER's job (it
        # owns the store). Rendering a visible placeholder is more honest than dropping
        # the block, which would lose the fact that an image belonged here.
        doc.add_paragraph(f"[image: {block.artifact_slug}]")
    else:  # pragma: no cover — Block.__post_init__ rejects unknown kinds
        doc.add_paragraph(block.text)


def _add_table(doc, rows: list[list[str]]) -> None:
    if not rows:
        return
    # Ragged rows are normalized to the widest row: python-docx needs a fixed column
    # count, and truncating would silently lose cells.
    width = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    for r, row in enumerate(rows):
        for c in range(width):
            table.cell(r, c).text = str(row[c]) if c < len(row) else ""
    # Row 0 is the header by contract — bold it so the output reads as a real table.
    for cell in table.rows[0].cells:
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True


register_writer("docx", render_docx)
