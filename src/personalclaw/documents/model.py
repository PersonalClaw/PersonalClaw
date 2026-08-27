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

#: Paragraph/cell alignments. `""` means "the writer's default", never "left" — a writer
#: that has a house style should get to keep it when the author expressed no preference.
ALIGNMENTS = ("", "left", "center", "right", "justify")

#: Page orientations. `""` means "the writer's default" for the same reason.
ORIENTATIONS = ("", "portrait", "landscape")

#: Named page sizes. `""` means "the writer's default" for the same reason as ALIGNMENTS —
#: a template with a house page size keeps it when the author expressed no preference.
PAGE_SIZES = ("", "letter", "a4", "legal", "tabloid")

#: PORTRAIT (width, height) in inches for each named size. Landscape is the same pair
#: swapped, so orientation stays one fact rather than doubling the table.
#:
#: The metric sizes are kept as exact divisions rather than rounded decimals: 914400 EMU
#: per inch divided by 25.4 is exactly 36000 EMU per mm, so `210 / 25.4` inches converts
#: to A4's width in whole EMU with no drift, while a rounded `8.2677` is 11 EMU short and
#: would make an A4 round trip fail an exact comparison.
PAGE_SIZE_IN: dict[str, tuple[float, float]] = {
    "letter": (8.5, 11.0),
    "a4": (210 / 25.4, 297 / 25.4),
    "legal": (8.5, 14.0),
    "tabloid": (11.0, 17.0),
}


@dataclass
class Run:
    """A stretch of text with uniform character formatting.

    Formatting is described, not encoded: `bold` is a property of the document, so it
    survives into any format a writer knows how to emit.
    """

    text: str = ""
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str = ""  # "" means "not a link"


@dataclass
class ParagraphStyle:
    """Optional paragraph-level presentation. Every field's zero value means "unset"."""

    align: str = ""
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    #: 0.0 means "writer default", NEVER "zero spacing" — a document asking for literally
    #: no line spacing would be unreadable, so that reading of 0.0 is never the one wanted.
    line_spacing: float = 0.0
    indent_left_pt: float = 0.0
    indent_right_pt: float = 0.0
    #: NEGATIVE is meaningful here and only here: a hanging indent pulls the first line
    #: LEFT of the body. So this field's "unset" test is `== 0.0`, never `> 0.0` — the
    #: reading every other numeric on this dataclass uses would silently drop it.
    first_line_indent_pt: float = 0.0
    keep_with_next: bool = False

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(f"unknown alignment {self.align!r}; expected one of {ALIGNMENTS}")


@dataclass
class PageSetup:
    """Optional page-level presentation. Zero values mean "unset", as in ParagraphStyle.

    **Margins are per edge, not one number.** A single margin could not express the
    asymmetric top/bottom vs. left/right geometry that Word's own default template ships,
    so every document built from it parsed as lossy — the loss report fired on documents
    this project generated itself. Four fields make that geometry representable.
    """

    size: str = ""
    orientation: str = ""
    margin_top_pt: float = 0.0
    margin_bottom_pt: float = 0.0
    margin_left_pt: float = 0.0
    margin_right_pt: float = 0.0
    #: Plain text only, per §C1's declared scope. A header carrying a table, an image or
    #: several paragraphs is NOT squeezed into this field — the parser reports it.
    header_text: str = ""
    footer_text: str = ""
    #: A page-number field in the footer. Separate from `footer_text` because a page
    #: number is computed per page, which no static string can express.
    page_numbers: bool = False

    def __post_init__(self) -> None:
        if self.orientation not in ORIENTATIONS:
            raise ValueError(
                f"unknown orientation {self.orientation!r}; expected one of {ORIENTATIONS}"
            )
        if self.size not in PAGE_SIZES:
            raise ValueError(f"unknown page size {self.size!r}; expected one of {PAGE_SIZES}")

    def size_in(self) -> tuple[float, float]:
        """The page's (width, height) in inches, orientation applied.

        `(0.0, 0.0)` when no size is named — the writer's template decides, and inventing
        Letter here would silently reformat a document that never asked for one.
        """
        if not self.size:
            return (0.0, 0.0)
        width, height = PAGE_SIZE_IN[self.size]
        return (height, width) if self.orientation == "landscape" else (width, height)


@dataclass
class Cell:
    """One table cell: either formatted runs, or a plain display string, or both."""

    runs: list[Run] = field(default_factory=list)
    text: str = ""
    bold: bool = False
    align: str = ""

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(f"unknown alignment {self.align!r}; expected one of {ALIGNMENTS}")
        # Same non-clobbering precedence as Block: an explicit `text` beside `runs` is an
        # author's deliberate override (a plain-text rendering of rich runs), and silently
        # recomputing it would make that override impossible to express.
        if self.runs and not self.text:
            self.text = "".join(run.text for run in self.runs)


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
    #: Formatted alternatives to `text` / `rows`. APPENDED, so every existing caller —
    #: positional or keyword — keeps working and a writer may ignore them entirely.
    runs: list[Run] = field(default_factory=list)
    cells: list[list[Cell]] = field(default_factory=list)
    style: ParagraphStyle | None = None

    def __post_init__(self) -> None:
        if self.kind not in BLOCK_KINDS:
            raise ValueError(f"unknown block kind {self.kind!r}; expected one of {BLOCK_KINDS}")
        self.level = max(1, min(6, int(self.level or 1)))
        # Derive the plain view from the rich one so a writer that only understands `text`
        # and `rows` still renders every block. Additive and non-clobbering: when BOTH are
        # supplied the EXPLICIT value wins untouched, because recomputing it would silently
        # discard a deliberate override (e.g. a plain-text summary of a link-bearing run).
        if self.runs and not self.text:
            self.text = "".join(run.text for run in self.runs)
        if self.cells and not self.rows:
            self.rows = [[cell.text for cell in row] for row in self.cells]


@dataclass
class DocumentModel:
    """A flowing text document: a title plus ordered blocks."""

    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    #: Appended for the same reason as Block's rich fields. None means "writer default".
    page: PageSetup | None = None


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
