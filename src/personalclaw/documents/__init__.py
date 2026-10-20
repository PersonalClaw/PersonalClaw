"""Document generation — the write half of the formats knowledge already reads.

`knowledge/readers.py` extracts text from .docx/.pdf/.pptx/.xlsx; this package renders
them. One declarative model per shape (document / sheet / deck), one pure writer per
format, one registry. Adding a format is a writer plus a registration — never a sweep.

The agent never emits OOXML. It supplies markdown (which it is already good at) or a
declarative model, and code renders the file. No vendor file-format vocabulary appears
outside ``writers/``.

**A generated spreadsheet evaluates only the formulas its model declares.** A cell is a formula
when its :class:`SheetCell` says so (``formula``), never because its text looks like one, since
a sheet's text comes from wherever the agent or an app found it. The xlsx writer stores a
literal that begins with ``=`` as text. A CSV cannot say what a cell is, and a spreadsheet reads
a cell whose text begins with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return as a formula,
so the csv writer writes every such cell with a single quote in front of it, which shows it as
text, a declared formula included. Text that is a number (``-20``, ``-1,234.56``, ``-$45.20``,
``-12.5%``, ``+1.5e3``) is written as it is, so a negative number stays a number. The artifact
store keeps every CSV artifact's text by the same rule, whoever wrote it. The rule's exact
wording is in ``writers/csv_writer.py``.
"""

from personalclaw.documents.deck_json import deck_from_dict, deck_to_dict
from personalclaw.documents.model import (
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
)
from personalclaw.documents.model_json import document_from_dict, document_to_dict
from personalclaw.documents.registry import (
    available_formats,
    get_writer,
    register_writer,
)
from personalclaw.documents.sheet_json import sheet_from_dict, sheet_to_dict

__all__ = [
    "Block",
    "Cell",
    "DocumentModel",
    "Sheet",
    "SheetCell",
    "SheetModel",
    "DeckModel",
    "Bullet",
    "ShapeBox",
    "Slide",
    "PageSetup",
    "ParagraphStyle",
    "Run",
    "document_from_dict",
    "document_to_dict",
    "sheet_from_dict",
    "sheet_to_dict",
    "deck_from_dict",
    "deck_to_dict",
    "register_writer",
    "get_writer",
    "available_formats",
]
