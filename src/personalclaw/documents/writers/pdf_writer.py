"""DocumentModel → PDF bytes (reportlab platypus).

Rendered directly from the model rather than via a converter, so PDF is a first-class
writer on every install. The "use a local converter if one happens to be present" option
was rejected by the owner: a documented capability that only works where LibreOffice is
installed is worse than one that doesn't exist, because the agent offers it and then fails.

Flowables are used (not the low-level canvas) so pagination, wrapping and table splitting
are handled by reportlab rather than by arithmetic here.
"""

from __future__ import annotations

import io

from personalclaw.documents.model import Block, DocumentModel
from personalclaw.documents.registry import register_writer

#: Body text large enough to read on screen; headings step down from the title.
_HEADING_SIZES = {1: 18, 2: 15, 3: 13, 4: 12, 5: 11, 6: 11}


def render_pdf(model: object) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        ListFlowable,
        ListItem,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    if not isinstance(model, DocumentModel):
        raise TypeError("pdf writer expects a DocumentModel")

    styles = getSampleStyleSheet()
    mono = ParagraphStyle(
        "PclawCode",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=9,
        leading=11,
        alignment=TA_LEFT,
    )
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        title=model.title or "Document",
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
    )
    story: list = []
    if model.title:
        story.append(Paragraph(_esc(model.title), styles["Title"]))
        story.append(Spacer(1, 10))

    for block in model.blocks:
        _add(
            story,
            block,
            styles,
            mono,
            colors,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            ListFlowable,
            ListItem,
            PageBreak,
        )

    if not story:  # a zero-flowable build raises; an empty doc must still be valid
        story.append(Spacer(1, 1))
    doc.build(story)
    return buf.getvalue()


def _esc(text: str) -> str:
    """Escape for reportlab's mini-HTML paragraph markup.

    Platypus parses `<b>`/`<i>`/`<br/>` inside Paragraph text, so raw `<` or `&` from
    document content would either vanish or raise a parse error mid-build.
    """
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _add(
    story,
    block: Block,
    styles,
    mono,
    colors,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    ListFlowable,
    ListItem,
    PageBreak,
) -> None:
    kind = block.kind
    if kind == "heading":
        style = styles["Heading1"].clone(f"H{block.level}")
        style.fontSize = _HEADING_SIZES.get(block.level, 12)
        style.leading = style.fontSize + 4
        style.spaceBefore = 10
        style.spaceAfter = 4
        story.append(Paragraph(_esc(block.text), style))
    elif kind == "paragraph":
        story.append(Paragraph(_esc(block.text), styles["BodyText"]))
    elif kind in ("bullets", "numbered"):
        items = [ListItem(Paragraph(_esc(i), styles["BodyText"])) for i in block.items]
        if items:
            extra = {}
            if kind == "bullets":
                # A HYPHEN, not a bullet glyph. reportlab's default bullet is
                # ZapfDingbats char 127, whose CID has no unicode mapping — so every
                # bullet extracted as the literal string "(cid:127)", corrupting the text
                # of any generated PDF that was later ingested or searched. Measured: a
                # hyphen extracts as "-", while `bulletFontName=Helvetica` and an explicit
                # "•" both still produce (cid:127). Visually a hyphen list is a fair
                # trade for text that isn't garbage.
                extra["start"] = "-"
            story.append(
                ListFlowable(
                    items,
                    bulletType="bullet" if kind == "bullets" else "1",
                    bulletFontName="Helvetica",
                    leftIndent=18,
                    **extra,
                )
            )
            story.append(Spacer(1, 6))
    elif kind == "table":
        _add_table(story, block.rows, styles, colors, Paragraph, Spacer, Table, TableStyle)
    elif kind == "code":
        # Preformatted would swallow the escaping; a monospace Paragraph with explicit
        # line breaks keeps the text intact AND wraps rather than running off the page.
        body = "<br/>".join(_esc(line) for line in (block.text or "").split("\n"))
        story.append(Paragraph(body, mono))
        story.append(Spacer(1, 6))
    elif kind == "pagebreak":
        story.append(PageBreak())
    elif kind == "image":
        # The reference, not the bytes — resolving an artifact is the caller's job.
        story.append(Paragraph(_esc(f"[image: {block.artifact_slug}]"), styles["Italic"]))


def _add_table(story, rows, styles, colors, Paragraph, Spacer, Table, TableStyle) -> None:
    if not rows:
        return
    width = max(len(r) for r in rows)
    # Every cell is a Paragraph so long values WRAP; bare strings would overflow the
    # column and get clipped off the page edge.
    data = [
        [Paragraph(_esc(str(r[c])) if c < len(r) else "", styles["BodyText"]) for c in range(width)]
        for r in rows
    ]
    table = Table(data, hAlign="LEFT", repeatRows=1)  # header repeats across page breaks
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0B0B0")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 8))


register_writer("pdf", render_pdf)
