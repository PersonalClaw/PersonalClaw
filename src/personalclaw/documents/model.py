"""The declarative document models — vendor-neutral by construction.

No OOXML vocabulary lives here: these describe *what* a document contains, and a writer
decides how to express it in its format. That split is what lets a second format reuse
the same model, and what keeps the agent from having to know anything about file formats.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
class SheetCell:
    """One spreadsheet cell: a typed value, an optional formula, optional presentation.

    **`value` and `formula` are separate fields, and that separation IS the fidelity.**
    A spreadsheet cell holds either a literal or a computed expression, and the two are
    different things in the file format — so a model that carried only a value would have
    to guess from the text which one it had. Guessing gets it wrong in both directions:
    ``"=SUM(A1)"`` typed as a value silently becomes a formula, and a literal label a user
    typed as ``"=WORK IN PROGRESS"`` silently becomes a broken one (Excel shows
    ``#NAME?``). Declaring the intent means neither can happen.

    `formula` keeps its leading ``=`` because that is how a person writes it, how the
    file stores it, and how every spreadsheet UI shows it — stripping it here would mean
    re-adding it in the writer, the parser, and the editor, three places to disagree.
    """

    #: The literal, keeping its Python type (str / int / float / bool / None) so numbers
    #: stay numbers — a spreadsheet full of text-formatted numbers can't be summed.
    #: When `formula` is set this is the CACHED result, or None when none was recorded.
    value: object = None
    #: ``""`` means "not a formula". Anything else must start with ``=``.
    formula: str = ""
    #: An Excel number-format code (``"#,##0.00"``, ``"0.0%"``, ``"yyyy-mm-dd"``).
    #: ``""`` means "the writer's default", never ``"General"`` — see ALIGNMENTS.
    number_format: str = ""
    bold: bool = False
    italic: bool = False
    #: ``RRGGBB`` hex, no ``#``. ``""`` means unset.
    font_color: str = ""
    #: ``RRGGBB`` hex solid fill, no ``#``. ``""`` means unset.
    fill: str = ""
    align: str = ""

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(f"unknown alignment {self.align!r}; expected one of {ALIGNMENTS}")
        if self.formula and not self.formula.startswith("="):
            raise ValueError(
                f"formula {self.formula!r} must start with '='; "
                "a literal that merely looks like one belongs in `value`"
            )

    @property
    def display(self) -> object:
        """What a reader sees: the formula if there is one, else the literal.

        The formula wins because that is what a spreadsheet's formula bar shows and what
        a plain-text export of the sheet should say — the cached value is a snapshot that
        may already be stale.
        """
        return self.formula or self.value


@dataclass
class Sheet:
    """One named sheet: a rectangle of cells plus the geometry Excel stores beside them."""

    name: str = ""
    #: Row-major, row 0 first. Rows need not be the same length; a writer pads nothing.
    cells: list[list[SheetCell]] = field(default_factory=list)
    #: Per-column width in Excel's own character units, index-aligned with `cells`
    #: columns. ``0.0`` means "the writer's default" for that column, so a sheet that
    #: sets only column C still needs the two zeros in front of it.
    column_widths: list[float] = field(default_factory=list)
    #: Merged regions as ``"A1:B2"`` refs. Kept as refs rather than index pairs because
    #: that is what both the file format and a person use, and converting in the model
    #: would put A1-notation arithmetic in three places instead of one.
    merges: list[str] = field(default_factory=list)
    #: Freeze the first row so a header stays visible while scrolling.
    frozen_header: bool = False

    @property
    def rows(self) -> list[list[object]]:
        """The plain display view — what a writer that knows nothing of formats emits.

        A property, not a stored mirror field: a stored copy of the cells is a second
        representation of the same data, and the first edit that updated one and not the
        other would silently write stale content. Derived means it cannot go stale.
        """
        return [[cell.display for cell in row] for row in self.cells]


@dataclass
class SheetModel:
    """A workbook: ordered named sheets.

    Ordered (a list, not a dict keyed by name) because sheet order is part of the
    document, and because a `Sheet` already carries its own name — a dict would make the
    name two things that can disagree.
    """

    sheets: list[Sheet] = field(default_factory=list)

    @classmethod
    def from_rows(cls, rows_by_name: Mapping[str, Sequence[Sequence[object]]]) -> SheetModel:
        """Build a plain, unformatted workbook from ``{name: rows}``.

        The convenience form for a caller that has data and no opinion about
        presentation (the agent's ``create_artifact`` path). A raw value that looks like
        a formula stays a LITERAL here: promoting it would be exactly the guess
        :class:`SheetCell` exists to avoid, and a caller that wants a formula says so by
        building the cell.

        Row 0 is the header by this constructor's contract, so it is marked bold and
        frozen HERE rather than in the writer. The model is the whole story: a writer
        that invented a bold row would be presentation the model could not see, and the
        editor would show a header that looks plain and saves back plain.
        """
        return cls(
            sheets=[
                Sheet(
                    name=str(name),
                    cells=[
                        [SheetCell(value=value, bold=index == 0) for value in row]
                        for index, row in enumerate(rows)
                    ],
                    frozen_header=bool(rows),
                )
                for name, rows in rows_by_name.items()
            ]
        )


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
