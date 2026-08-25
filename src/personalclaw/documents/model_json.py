"""JSON⇄:class:`DocumentModel` — the ONLY shape a document crosses the wire in.

The editing surface (DOCUMENT-FIDELITY-EDITOR §C4) edits the *model* and never the
bytes: the server parses a .docx into a model, hands the model to the browser, takes a
model back, and re-renders it with the shipped writer. That contract needs exactly two
functions, and this module is deliberately the whole boundary — so "the browser never
constructs OOXML" is a property of the type system here, not a convention somebody
remembers.

**Serialization is ``asdict``, not a hand-written mapper.** A field-for-field mapper is
one forgotten line away from silently dropping a field the writer still emits, which is
the fidelity failure this package exists to prevent; ``asdict`` cannot fall behind the
dataclass.

**Deserialization is STRICT, and that is the security posture.** The model arrives from
a browser, so every unknown key is a refusal rather than a silent drop: a client that
misspells ``italic`` must learn that its formatting was not saved, not discover it in
Word later. Allowed keys are derived from the dataclasses with
:func:`dataclasses.fields`, so a new model field is accepted the moment it exists and
never needs a second registration here. Value validation (block kinds, alignments,
orientations) is left to the dataclasses' own ``__post_init__``, which already raises
``ValueError`` — one validator, not two that can disagree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields
from typing import Any

from personalclaw.documents.model import (
    Block,
    Cell,
    DocumentModel,
    PageSetup,
    ParagraphStyle,
    Run,
)


def document_to_dict(model: DocumentModel) -> dict[str, Any]:
    """The model as JSON-ready data (``page`` is ``None`` when unset)."""
    return asdict(model)


def document_from_dict(payload: Any) -> DocumentModel:
    """Build a :class:`DocumentModel` from untrusted JSON data.

    Raises ``ValueError`` — with the offending path in the message — for anything that
    is not a document: a non-object, an unknown field, a wrongly typed collection, or a
    value the model's own validators reject. The caller turns that into one refusal.
    """
    data = _object(payload, DocumentModel, "model")
    return DocumentModel(
        title=_text(data.get("title"), "model.title"),
        blocks=[
            _block(item, f"model.blocks[{index}]")
            for index, item in enumerate(_sequence(data.get("blocks"), "model.blocks"))
        ],
        page=_page(data.get("page"), "model.page"),
    )


# ── per-node builders ────────────────────────────────────────────────────────


def _block(payload: Any, where: str) -> Block:
    data = _object(payload, Block, where)
    if "kind" not in data:
        raise ValueError(f"{where} is missing the required field 'kind'")
    return Block(
        kind=_text(data.get("kind"), f"{where}.kind"),
        text=_text(data.get("text"), f"{where}.text"),
        level=_whole(data.get("level"), f"{where}.level", default=1),
        items=[
            _text(item, f"{where}.items[{index}]")
            for index, item in enumerate(_sequence(data.get("items"), f"{where}.items"))
        ],
        rows=[
            [
                _text(cell, f"{where}.rows[{r}][{c}]")
                for c, cell in enumerate(_sequence(row, f"{where}.rows[{r}]"))
            ]
            for r, row in enumerate(_sequence(data.get("rows"), f"{where}.rows"))
        ],
        artifact_slug=_text(data.get("artifact_slug"), f"{where}.artifact_slug"),
        runs=[
            _run(item, f"{where}.runs[{index}]")
            for index, item in enumerate(_sequence(data.get("runs"), f"{where}.runs"))
        ],
        cells=[
            [
                _cell(cell, f"{where}.cells[{r}][{c}]")
                for c, cell in enumerate(_sequence(row, f"{where}.cells[{r}]"))
            ]
            for r, row in enumerate(_sequence(data.get("cells"), f"{where}.cells"))
        ],
        style=_style(data.get("style"), f"{where}.style"),
    )


def _run(payload: Any, where: str) -> Run:
    data = _object(payload, Run, where)
    return Run(
        text=_text(data.get("text"), f"{where}.text"),
        bold=_flag(data.get("bold"), f"{where}.bold"),
        italic=_flag(data.get("italic"), f"{where}.italic"),
        code=_flag(data.get("code"), f"{where}.code"),
        link=_text(data.get("link"), f"{where}.link"),
    )


def _cell(payload: Any, where: str) -> Cell:
    data = _object(payload, Cell, where)
    return Cell(
        runs=[
            _run(item, f"{where}.runs[{index}]")
            for index, item in enumerate(_sequence(data.get("runs"), f"{where}.runs"))
        ],
        text=_text(data.get("text"), f"{where}.text"),
        bold=_flag(data.get("bold"), f"{where}.bold"),
        align=_text(data.get("align"), f"{where}.align"),
    )


def _style(payload: Any, where: str) -> ParagraphStyle | None:
    if payload is None:
        return None
    data = _object(payload, ParagraphStyle, where)
    return ParagraphStyle(
        align=_text(data.get("align"), f"{where}.align"),
        space_before_pt=_number(data.get("space_before_pt"), f"{where}.space_before_pt"),
        space_after_pt=_number(data.get("space_after_pt"), f"{where}.space_after_pt"),
        line_spacing=_number(data.get("line_spacing"), f"{where}.line_spacing"),
    )


def _page(payload: Any, where: str) -> PageSetup | None:
    if payload is None:
        return None
    data = _object(payload, PageSetup, where)
    return PageSetup(
        orientation=_text(data.get("orientation"), f"{where}.orientation"),
        margin_in=_number(data.get("margin_in"), f"{where}.margin_in"),
    )


# ── scalar/shape guards (every one names its path) ───────────────────────────


def _object(payload: Any, cls: type, where: str) -> Mapping[str, Any]:
    """*payload* as a mapping whose keys are all fields of *cls* — else ``ValueError``."""
    if not isinstance(payload, Mapping):
        raise ValueError(f"{where} must be an object, got {type(payload).__name__}")
    allowed = {f.name for f in fields(cls)}
    unknown = sorted(str(key) for key in payload if key not in allowed)
    if unknown:
        raise ValueError(
            f"{where} has unknown field(s) {', '.join(repr(u) for u in unknown)}; "
            f"expected any of {sorted(allowed)}"
        )
    return payload


def _sequence(value: Any, where: str) -> Sequence[Any]:
    """A JSON list, or ``()`` when absent. A string is NOT a sequence of items here —
    ``{"items": "one"}`` would otherwise become five single-character bullets."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{where} must be a list, got {type(value).__name__}")
    return value


def _text(value: Any, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string, got {type(value).__name__}")
    return value


def _flag(value: Any, where: str) -> bool:
    """A real JSON boolean. ``"false"`` and ``0`` are refused rather than coerced: a
    truthiness cast is how a string ``"false"`` becomes bold text."""
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"{where} must be true or false, got {type(value).__name__}")
    return value


def _whole(value: Any, where: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be an integer, got {type(value).__name__}")
    return value


def _number(value: Any, where: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number, got {type(value).__name__}")
    return float(value)
