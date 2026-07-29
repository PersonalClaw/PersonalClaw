"""The declarative document models — vendor-neutral by construction.

No OOXML vocabulary lives here: these describe *what* a document contains, and a writer
decides how to express it in its format. That split is what lets a second format reuse
the same model, and what keeps the agent from having to know anything about file formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Block kinds a text document can hold. A writer MUST handle every one of these —
#: silently skipping an unknown kind would drop the user's content without saying so.
BLOCK_KINDS = (
    "heading",
    "paragraph",
    "bullets",
    "numbered",
    "table",
    "image",
    "pagebreak",
    "code",
)


@dataclass
class Block:
    """One flow element of a text document."""

    kind: str
    text: str = ""  # heading / paragraph / code body
    level: int = 1  # heading level, 1-6
    items: list[str] = field(default_factory=list)  # bullets / numbered
    rows: list[list[str]] = field(default_factory=list)  # table; row 0 = header
    #: An image block REFERENCES an existing artifact rather than carrying bytes. Keeps
    #: image data out of the prompt and reuses the artifact store's versioning.
    artifact_slug: str = ""

    def __post_init__(self) -> None:
        if self.kind not in BLOCK_KINDS:
            raise ValueError(f"unknown block kind {self.kind!r}; expected one of {BLOCK_KINDS}")
        self.level = max(1, min(6, int(self.level or 1)))


@dataclass
class DocumentModel:
    """A flowing text document: a title plus ordered blocks."""

    title: str = ""
    blocks: list[Block] = field(default_factory=list)


@dataclass
class SheetModel:
    """Named sheets, each a list of rows.

    Cell values keep their Python type (str / int / float / bool / None) so numbers stay
    numbers in the output — a spreadsheet full of text-formatted numbers can't be summed,
    which defeats the point of producing one.
    """

    sheets: dict[str, list[list[object]]] = field(default_factory=dict)


@dataclass
class Slide:
    title: str = ""
    body: list[str] = field(default_factory=list)
    notes: str = ""
    artifact_slug: str = ""


@dataclass
class DeckModel:
    title: str = ""
    slides: list[Slide] = field(default_factory=list)
