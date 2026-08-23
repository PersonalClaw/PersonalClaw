"""Document generation — the write half of the formats knowledge already reads.

`knowledge/readers.py` extracts text from .docx/.pdf/.pptx/.xlsx; this package renders
them. One declarative model per shape (document / sheet / deck), one pure writer per
format, one registry. Adding a format is a writer plus a registration — never a sweep.

The agent never emits OOXML. It supplies markdown (which it is already good at) or a
declarative model, and code renders the file. No vendor file-format vocabulary appears
outside ``writers/``.
"""

from personalclaw.documents.model import (
    Block,
    Cell,
    DeckModel,
    DocumentModel,
    PageSetup,
    ParagraphStyle,
    Run,
    SheetModel,
)
from personalclaw.documents.registry import (
    available_formats,
    get_writer,
    register_writer,
)

__all__ = [
    "Block",
    "Cell",
    "DocumentModel",
    "SheetModel",
    "DeckModel",
    "PageSetup",
    "ParagraphStyle",
    "Run",
    "register_writer",
    "get_writer",
    "available_formats",
]
