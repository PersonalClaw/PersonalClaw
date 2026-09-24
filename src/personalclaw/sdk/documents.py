"""SDK: the document-generation seam — markdown/HTML in, a real file out.

Stable re-export of ``personalclaw.documents`` (the declarative document/deck/sheet
models + the format→writer registry) plus ``personalclaw.documents.from_markup``
(markdown/HTML → a model). An app that *fronts* document generation — a brief-to-deck
app, a report exporter — composes a model and asks the SHIPPED writer to render it,
instead of vendoring python-pptx/python-docx and becoming a second backend that drifts
from the one knowledge already reads.

Two properties an app front depends on and must not re-derive:

* ``available_formats()`` reports what is renderable *in this process*. Registration is
  the availability check — a writer whose optional library is missing never registers —
  so an app can offer a format list it will actually be able to honour.
* A writer is PURE (model → bytes, no I/O, no store access), so an app can render and
  then decide where the bytes go without the seam reaching the artifact store behind it.

``from_markup`` is the primary authoring path rather than a convenience: markdown is
what a model writes well, and "the model writes the source, code renders the file" is
why no app needs to learn OOXML. HTML arriving there is treated as untrusted and routed
through core's existing sanitizer and credential redactor.
"""

from personalclaw.documents import (  # noqa: F401
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
    deck_to_dict,
    document_from_dict,
    document_to_dict,
    get_writer,
    sheet_from_dict,
    sheet_to_dict,
)
from personalclaw.documents.from_markup import (  # noqa: F401
    deck_from_markdown,
    document_from_html,
    document_from_markdown,
)

# ``register_writer`` is deliberately NOT promoted. A format an app registers would be
# visible to every other caller of the shared registry while that app is enabled and
# gone when it is disabled, so ``available_formats()`` would stop being a property of
# the build. Apps render through the writers core ships; adding a format is a core change.
__all__ = [
    "document_from_markdown",
    "document_from_html",
    "deck_from_markdown",
    "available_formats",
    "get_writer",
    "DocumentModel",
    "DeckModel",
    "SheetModel",
    "Block",
    "Bullet",
    "Cell",
    "PageSetup",
    "ParagraphStyle",
    "Run",
    "ShapeBox",
    "Sheet",
    "SheetCell",
    "Slide",
    "document_from_dict",
    "document_to_dict",
    "deck_from_dict",
    "deck_to_dict",
    "sheet_from_dict",
    "sheet_to_dict",
]
