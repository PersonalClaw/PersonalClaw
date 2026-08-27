"""Which shape on a slide is the title, and which is the body.

One module because BOTH halves of the deck round trip have to answer it the same way. The
writer puts the outline in the body placeholder; the parser reads the outline back out of
it. If those two disagreed by one placeholder index, a deck would save its bullets into a
shape the next load did not look at — the body would vanish and nothing would report it.

python-pptx returns a NEW proxy object on each ``shapes.title`` access, so
``shape is slide.shapes.title`` is False even for the title placeholder itself. That cost
a real defect once (the first body line overwrote the title and the real title vanished
from the round trip), which is why everything here compares placeholder INDEX.
"""

from __future__ import annotations

from typing import Any


def title_index(slide: Any) -> int | None:
    """The placeholder index of *slide*'s title, or ``None`` when it has no title."""
    title = slide.shapes.title
    if title is None:
        return None
    return int(title.placeholder_format.idx)


def body_placeholder(slide: Any) -> Any | None:
    """*slide*'s body placeholder, or ``None``.

    Looked up by exclusion rather than a fixed index because placeholder idx varies by
    layout and template: the FIRST placeholder with a text frame whose idx is not the
    title's is the body. "First" is what makes it a single answer — a two-content layout
    has two, and carrying both would mean a model with two bodies and a writer that has
    to guess which one a user's edit belonged to. The second is reported as a loss.
    """
    index = title_index(slide)
    for shape in slide.placeholders:
        if not shape.has_text_frame:
            continue
        if index is not None and shape.placeholder_format.idx == index:
            continue
        return shape
    return None
