"""JSON⇄:class:`DeckModel` — the ONLY shape a deck crosses the wire in.

The deck half of what ``model_json.py`` does for a text document and ``sheet_json.py`` for
a workbook, and for the same reason: the slide editor edits the *model*, never the bytes,
so the server parses a .pptx into a model, hands the model to the browser, takes a model
back, and re-renders it with the shipped writer. No OOXML is constructed in a browser, and
that is a property of this boundary rather than a convention somebody remembers.

Separate module, not a third pair of functions in one file: the three models share no node
type, so folding them together would produce one file whose every function belonged to a
third of it.

**Serialization is ``asdict``** for ``model_json.py``'s reason — a hand-written mapper is
one forgotten line from dropping a field the writer still emits.

**Deserialization is STRICT**, likewise: the payload arrives from a browser, so an unknown
key is a refusal rather than a silent drop, and a layout the shipped template does not have
is refused HERE rather than becoming a slide that quietly rebuilt itself on save.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from personalclaw.documents.model import DECK_LAYOUTS, Bullet, DeckModel, ShapeBox, Slide

# The scalar guards are ``model_json``'s, reused rather than restated — same posture, and a
# second copy would be a second posture the moment one of them was tightened.
from personalclaw.documents.model_json import _number, _object, _sequence, _text, _whole


def deck_to_dict(model: DeckModel) -> dict[str, Any]:
    """The deck as JSON-ready data."""
    return asdict(model)


def deck_from_dict(payload: Any) -> DeckModel:
    """Build a :class:`DeckModel` from untrusted JSON data.

    Raises ``ValueError`` — with the offending path in the message — for anything that is
    not a deck. The caller turns that into one refusal.
    """
    data = _object(payload, DeckModel, "model")
    return DeckModel(
        title=_text(data.get("title"), "model.title"),
        slides=[
            _slide(item, f"model.slides[{index}]")
            for index, item in enumerate(_sequence(data.get("slides"), "model.slides"))
        ],
        width_in=_number(data.get("width_in"), "model.width_in"),
        height_in=_number(data.get("height_in"), "model.height_in"),
    )


def _slide(payload: Any, where: str) -> Slide:
    data = _object(payload, Slide, where)
    return Slide(
        title=_text(data.get("title"), f"{where}.title"),
        bullets=[
            _bullet(item, f"{where}.bullets[{index}]")
            for index, item in enumerate(_sequence(data.get("bullets"), f"{where}.bullets"))
        ],
        notes=_text(data.get("notes"), f"{where}.notes"),
        artifact_slug=_text(data.get("artifact_slug"), f"{where}.artifact_slug"),
        layout=_layout(data.get("layout"), f"{where}.layout"),
        title_box=_box(data.get("title_box"), f"{where}.title_box"),
        body_box=_box(data.get("body_box"), f"{where}.body_box"),
    )


def _bullet(payload: Any, where: str) -> Bullet:
    data = _object(payload, Bullet, where)
    # `Bullet.__post_init__` clamps the depth to what PowerPoint can express, so a level
    # of 40 lands at 8 rather than being refused — the text is what a user typed, and the
    # depth is the part the format has an opinion about.
    return Bullet(
        text=_text(data.get("text"), f"{where}.text"),
        level=_whole(data.get("level"), f"{where}.level", default=0),
    )


def _box(payload: Any, where: str) -> ShapeBox:
    if payload is None:
        return ShapeBox()
    data = _object(payload, ShapeBox, where)
    return ShapeBox(
        left_in=_number(data.get("left_in"), f"{where}.left_in"),
        top_in=_number(data.get("top_in"), f"{where}.top_in"),
        width_in=_number(data.get("width_in"), f"{where}.width_in"),
        height_in=_number(data.get("height_in"), f"{where}.height_in"),
    )


def _layout(value: Any, where: str) -> str:
    """A layout the shipped template really has, or ``""``.

    Refused rather than dropped: a client that sent ``"Titel Slide"`` would otherwise get
    a deck silently re-laid-out from its content, and would find out by opening the file.
    ``Slide.__post_init__`` enforces the same rule for a server-side caller; this is the
    wire's copy of it, and it names the path in the message.
    """
    name = _text(value, where)
    if name and name not in DECK_LAYOUTS:
        raise ValueError(f"{where} must be one of {list(DECK_LAYOUTS)} or empty, got {name!r}")
    return name
