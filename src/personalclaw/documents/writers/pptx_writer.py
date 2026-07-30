"""DeckModel → .pptx bytes (python-pptx).

Uses the template's real TITLE + BODY placeholders rather than free-floating text boxes.
That matters for more than tidiness: the pptx reader identifies a slide's title via
``slide.shapes.title``, so a deck built from text boxes would round-trip with every title
lost — and so would PowerPoint's own outline view.
"""

from __future__ import annotations

import io

from personalclaw.documents.model import DeckModel, Slide
from personalclaw.documents.registry import register_writer

#: Layout indexes in python-pptx's default template. 0 = title slide (title + subtitle),
#: 1 = title and content (title + body placeholder), 5 = title only.
_LAYOUT_TITLE = 0
_LAYOUT_TITLE_CONTENT = 1
_LAYOUT_TITLE_ONLY = 5


def render_pptx(model: object) -> bytes:
    from pptx import Presentation

    if not isinstance(model, DeckModel):
        raise TypeError("pptx writer expects a DeckModel")
    prs = Presentation()
    # A deck title becomes a real title slide, so the file opens on something meaningful
    # rather than the first content slide.
    if model.title:
        layout = prs.slide_layouts[_LAYOUT_TITLE]
        slide = prs.slides.add_slide(layout)
        if slide.shapes.title is not None:
            slide.shapes.title.text = model.title
    for entry in model.slides:
        _add_slide(prs, entry)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _add_slide(prs, entry: Slide) -> None:
    # A slide with no body uses the title-only layout: an empty body placeholder renders
    # as a visible "Click to add text" prompt in PowerPoint.
    layout_idx = _LAYOUT_TITLE_CONTENT if entry.body else _LAYOUT_TITLE_ONLY
    slide = prs.slides.add_slide(prs.slide_layouts[layout_idx])
    if slide.shapes.title is not None:
        slide.shapes.title.text = entry.title or ""

    if entry.body:
        body = _body_placeholder(slide)
        if body is not None:
            frame = body.text_frame
            # The first paragraph already exists; reuse it, then append the rest, or the
            # deck opens with a blank first bullet.
            frame.text = entry.body[0]
            for line in entry.body[1:]:
                para = frame.add_paragraph()
                para.text = line
                para.level = 0

    # An image block references an artifact; resolving it to bytes is the caller's job
    # (it owns the store). Recording the reference in the notes keeps the fact that an
    # image belonged here rather than dropping it silently.
    notes_text = entry.notes
    if entry.artifact_slug:
        notes_text = (notes_text + f"\n[image: {entry.artifact_slug}]").strip()
    if notes_text:
        # notes_slide is created on first access by python-pptx.
        slide.notes_slide.notes_text_frame.text = notes_text


def _body_placeholder(slide):
    """The slide's body placeholder, or None.

    Compared by placeholder INDEX, not object identity. python-pptx returns a NEW proxy
    object on each `shapes.title` access, so `shape is slide.shapes.title` is False even
    for the title placeholder itself — which made the first body line overwrite the title
    and the real title vanish from the round trip. Measured, not assumed.

    Looked up by exclusion rather than a fixed index because placeholder idx varies by
    layout and template: anything with a text frame whose idx isn't the title's is body.
    """
    title = slide.shapes.title
    title_idx = title.placeholder_format.idx if title is not None else None
    for shape in slide.placeholders:
        if not shape.has_text_frame:
            continue
        if title_idx is not None and shape.placeholder_format.idx == title_idx:
            continue
        return shape
    return None


register_writer("pptx", render_pptx)
