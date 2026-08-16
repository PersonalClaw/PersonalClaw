"""Fetch-and-slice — the deterministic document-shaping primitive (WATCHED-SOURCES §5).

Four jobs, one module, **zero model calls anywhere in this file**:

1. **Source sniffing** — an arXiv id, a DOI, a raw-PDF URL or a plain URL, normalized to
   one :class:`SourceRef` a fetcher can act on.
2. **Cascaded section detection** — three deterministic strategies over a PDF's layout
   (:func:`~personalclaw.knowledge.readers.read_pdf_structure`), the first two unioned.
3. **Purpose-cut slices** — ``brief`` / ``body`` / ``meta`` / ``full``, each sized for the
   ROLE that will read it, persisted as ``extracted_contents`` rows on the ONE item.
4. **Deterministic reference extraction** — a four-tier citation cascade over the
   bibliography, identifiers first.

**Determinism is the product, not a nice property.** The same bytes must always yield the
same sections, because everything downstream (which text an enrichment node sees, which
references get stored) is derived from them; a detector that reorders on a different
Python run silently changes what a model was asked about. So: no model calls, no reliance
on dict/set iteration order, every candidate list explicitly sorted by a total key, and
:func:`detect_sections` is a pure function of its argument. The property is asserted by
running detection twice over one input and comparing, which is the only assertion that
can catch an ordering regression.

**Slices are rows, not chunks.** Each slice is one ``extracted_contents`` row on the SAME
item (``node_type: "slice:brief"``). The one-item-one-document model is untouched — the
repo removed chunk-items deliberately and this primitive does not reintroduce them.

**The full text is for deterministic passes only.** ``SliceResult.full_text`` feeds
reference extraction and the kept-pages floor. Nothing here hands it to a model, and the
persisted rows are the shaped slices precisely so an enrichment node receives the slice
its role needs — token control by input shaping, not by asking a prompt to be brief.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from personalclaw.knowledge.readers import PdfLine, PdfStructure, read_pdf_structure

# ══ THRESHOLDS ═══════════════════════════════════════════════════════════════════
#
# Every tunable number in this module lives HERE and nowhere else. §5 makes that a
# requirement rather than a style preference, from the paperloom doc-vs-code drift
# lesson: a threshold written inline is a threshold that gets copied, and two copies
# of "1.1" drift the day someone tunes one of them. If you need a number below,
# reference a name from this block — a literal in the code is the defect this exists
# to prevent.

#: A line is a heading candidate when its largest glyph exceeds the body size by this
#: factor. 1.1 rather than something larger because many papers set section headings
#: only slightly above body size (bold does the visual work), and a stricter ratio
#: finds no headings at all in exactly those papers.
HEADING_SIZE_RATIO = 1.1

#: A heading is short. Above this, a large-font run is a pull-quote or a title block
#: spanning lines, not a section heading.
HEADING_MAX_CHARS = 120

#: The kept-pages floor: the first N and last M PRE-BIBLIOGRAPHY pages are always
#: retained in ``body`` even when section detection found nothing in them. A paper's
#: front matter and its closing argument are the two places a missed heading costs the
#: most, so they are guaranteed rather than left to the cascade.
KEEP_FIRST_PAGES = 3
KEEP_LAST_PAGES = 2

#: ``meta`` is the first pages — title block, authors, abstract region.
META_PAGES = 2

#: ``brief`` is clamped into this fraction band of the pre-bibliography text. Below the
#: floor it is topped up from the leading text (a two-line brief is not a brief); above
#: the ceiling it is truncated (a "brief" that is most of the paper defeats its purpose).
BRIEF_MIN_FRACTION = 0.10
BRIEF_MAX_FRACTION = 0.25

#: Two bibliography entries whose titles match at or above this ratio are the same work
#: cited twice in different formats. 0.85 tolerates punctuation/casing/line-wrap damage
#: without merging two genuinely different papers with similar titles.
TITLE_MATCH_RATIO = 0.85

#: The fuzzy-title window slides in steps of this many characters. 1 would be exact and
#: quadratic on a long entry; a small stride keeps the scan bounded while still landing
#: inside a title that starts after an author list of unknown length.
TITLE_WINDOW_STEP_CHARS = 4

#: A title candidate needs at least this many words. Fewer and it is a venue
#: abbreviation or an author initial run, not a title.
TITLE_MIN_WORDS = 4

#: The author+year tier looks for a 4-digit year within this many characters of the
#: entry's start — the proximity that distinguishes "Smith, J. (2019)" from a page
#: range or an arbitrary number deep in the entry.
AUTHOR_YEAR_PROXIMITY_CHARS = 120

#: Shorter than this and a bibliography fragment is line noise, not an entry.
MIN_REFERENCE_CHARS = 20

#: A cap on bibliography entries parsed from one document. A malformed split on a
#: pathological file must cost bounded work, not a pathological loop.
MAX_REFERENCES = 500


# ══ VOCABULARIES ═════════════════════════════════════════════════════════════════
# Closed sets of strings, deliberately NOT enums: these values are written into a
# sqlite column and a JSON metadata blob, where the string IS the value.

SOURCE_ARXIV = "arxiv"
SOURCE_DOI = "doi"
SOURCE_PDF = "pdf"
SOURCE_URL = "url"

ROLE_ABSTRACT = "abstract"
ROLE_INTRODUCTION = "introduction"
ROLE_METHOD = "method"
ROLE_RESULTS = "results"
ROLE_DISCUSSION = "discussion"
ROLE_CONCLUSION = "conclusion"
ROLE_REFERENCES = "references"
ROLE_OTHER = "other"

#: Which cascade tier found a section. Ordered by evidence strength — see
#: :func:`detect_sections`.
STRATEGY_OUTLINE = "outline"
STRATEGY_FONT = "font"
STRATEGY_HEADER = "header"
_STRATEGY_RANK = {STRATEGY_OUTLINE: 0, STRATEGY_FONT: 1, STRATEGY_HEADER: 2}

SLICE_BRIEF = "brief"
SLICE_BODY = "body"
SLICE_META = "meta"
SLICE_FULL = "full"

#: The persisted slice roles, in the order their rows are written. ``full`` is absent on
#: purpose: it is byte-identical to the item's own ``content`` column, so a row would
#: double every paper's storage to say something already stored. It is still COMPUTED
#: (reference extraction and the kept-pages floor both read it) — see :class:`SliceResult`.
PERSISTED_SLICES = (SLICE_BRIEF, SLICE_BODY, SLICE_META)

#: ``brief`` answers "what is this paper claiming"; ``body`` answers "how did they do it
#: and what happened". Roles absent from both (``other``, and ``references``, which is
#: stripped) reach neither.
BRIEF_ROLES = (ROLE_ABSTRACT, ROLE_INTRODUCTION, ROLE_CONCLUSION)
BODY_ROLES = (ROLE_METHOD, ROLE_RESULTS, ROLE_DISCUSSION)

#: Reference-cascade tiers, strongest first.
TIER_ARXIV = "arxiv"
TIER_DOI = "doi"
TIER_TITLE = "title"
TIER_AUTHOR_YEAR = "author_year"

#: The ``extracted_contents.node_type`` prefix every slice row carries. Public because
#: any consumer that concatenates an item's whole pool must EXCLUDE slice rows — they are
#: derived views of text already in the pool, so including them double-counts the
#: document (see ``is_slice_row``).
SLICE_NODE_PREFIX = "slice:"


def slice_node_type(role: str) -> str:
    """The ``extracted_contents.node_type`` for a slice of *role*."""
    return f"{SLICE_NODE_PREFIX}{role}"


def is_slice_row(node_type: str) -> bool:
    """True when a pool row is a derived slice rather than a node's own extraction."""
    return str(node_type or "").startswith(SLICE_NODE_PREFIX)


# ══ SOURCE SNIFFING ══════════════════════════════════════════════════════════════

#: The arXiv id SHAPE (post-2007 scheme), version-insensitive — ``2103.00020v3`` and
#: ``2103.00020`` are the same paper, so the version is parsed and discarded.
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")

#: A DOI. The suffix is deliberately greedy-minus-delimiters: DOIs legitimately contain
#: slashes, dots and parentheses, so only whitespace and quoting characters end one.
_DOI = re.compile(r"\b(10\.\d{4,}(?:\.\d+)*/[^\s\"'<>,;]+)")

#: An arXiv id is only trusted with CONTEXT — an explicit ``arXiv:`` marker, an arxiv.org
#: host, or the whole string being nothing but an id. Unanchored, ``\d{4}\.\d{4,5}`` also
#: matches a page range, a version string and a table cell, so an uncontextualized match
#: invents citations out of numbers.
_ARXIV_MARKER = re.compile(r"arxiv", re.IGNORECASE)
_BARE_ARXIV_ID = re.compile(r"^\s*(\d{4}\.\d{4,5})(?:v\d+)?\s*$")

_PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class SourceRef:
    """A normalized document reference: what it is, and the URL that yields its bytes."""

    kind: str
    url: str
    identifier: str = ""
    raw: str = ""


def sniff_source(raw: str) -> SourceRef | None:
    """Classify *raw* as an arXiv id, a DOI, a PDF URL or a plain URL.

    Returns None when *raw* is not a fetchable document reference at all (empty, a
    non-http scheme, a bare word). Never raises: the callers are an ingest node and a
    poll loop, and a malformed user paste must be a "no" rather than a traceback.

    Order matters. arXiv is tried first because an arXiv landing page URL *also* parses
    as a plain URL and would then be scraped as HTML instead of fetched as a PDF; DOI
    next for the same reason. The generic URL tiers are last, as fallbacks always are.
    """
    text = (raw or "").strip()
    if not text:
        return None

    bare = _BARE_ARXIV_ID.match(text)
    if bare:
        ident = bare.group(1)
        return SourceRef(kind=SOURCE_ARXIV, url=_arxiv_pdf_url(ident), identifier=ident, raw=text)

    parsed = _safe_parse(text)
    host = (parsed.netloc or "").lower().removeprefix("www.")

    if _ARXIV_MARKER.search(text) or host.endswith("arxiv.org"):
        found = _ARXIV_ID.search(text)
        if found:
            ident = found.group(1)
            return SourceRef(
                kind=SOURCE_ARXIV, url=_arxiv_pdf_url(ident), identifier=ident, raw=text
            )

    doi = _DOI.search(text)
    if doi:
        ident = doi.group(1).rstrip(".")
        return SourceRef(
            kind=SOURCE_DOI, url=f"https://doi.org/{ident}", identifier=ident, raw=text
        )

    if parsed.scheme.lower() not in ("http", "https") or not host:
        return None
    if (parsed.path or "").lower().endswith(".pdf"):
        return SourceRef(kind=SOURCE_PDF, url=text, raw=text)
    return SourceRef(kind=SOURCE_URL, url=text, raw=text)


def _arxiv_pdf_url(identifier: str) -> str:
    """arXiv's PDF endpoint for a version-less id — arXiv resolves it to the latest."""
    return f"https://arxiv.org/pdf/{identifier}"


def _safe_parse(text: str):
    try:
        return urlparse(text)
    except ValueError:
        return urlparse("")


def is_pdf_bytes(body: bytes) -> bool:
    """The raw-PDF sniff: PDF bytes are self-identifying, so the CONTENT decides the
    cached original's extension rather than a URL's path or a server's content-type
    (arXiv serves PDFs from extension-less URLs; publishers mislabel content-types)."""
    return bytes(body or b"")[: len(_PDF_MAGIC)] == _PDF_MAGIC


# ══ SHA256 SOURCE CACHE ══════════════════════════════════════════════════════════


class SourceFetchError(RuntimeError):
    """A source could not be fetched (transport error, or a non-2xx response)."""


@dataclass(frozen=True)
class FetchedSource:
    """A source's bytes on disk. ``from_cache`` is True when NO network was touched."""

    path: Path
    sha256: str
    from_cache: bool
    ref: SourceRef


def source_cache_dir() -> Path:
    """Where cached originals live: a ``sources/`` subdirectory of the EXISTING knowledge
    files dir. §5 says "no new cache root" — the durability inventory already claims
    ``workspace/knowledge/files`` as a tree, so a subdirectory inherits its backup and
    export treatment instead of needing its own entry."""
    from personalclaw.knowledge import knowledge_files_dir

    directory = Path(knowledge_files_dir()) / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pointer_path(ref: SourceRef) -> Path:
    """The ref→content pointer for *ref*.

    Content-addressing alone cannot serve a re-ingest: computing a content hash requires
    the content, which is the very thing we are trying not to fetch. So the cache is two
    levels — originals keyed by CONTENT hash (§5's requirement, and it dedupes two refs
    that resolve to identical bytes onto one file), plus a tiny pointer keyed by the
    normalized REFERENCE that records which digest that reference resolved to. The
    pointer is what makes the second ingest cost zero network.
    """
    return source_cache_dir() / f"ref-{_digest(ref.url.encode('utf-8'))}.json"


def _original_path(sha256: str, suffix: str) -> Path:
    return source_cache_dir() / f"sha256-{sha256}{suffix}"


def cached_source(ref: SourceRef) -> FetchedSource | None:
    """The cached original for *ref*, or None. Pure — opens no socket, ever."""
    pointer = _pointer_path(ref)
    try:
        record = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sha256 = str(record.get("sha256") or "")
    suffix = str(record.get("suffix") or "")
    if not sha256:
        return None
    path = _original_path(sha256, suffix)
    if not path.is_file():
        # The pointer outlived its original (a manual cleanup of the files dir). Treat it
        # as a miss rather than handing back a path that does not exist.
        return None
    return FetchedSource(path=path, sha256=sha256, from_cache=True, ref=ref)


async def fetch_source(
    ref: SourceRef, *, fetch_fn: Callable[[str], Any] | None = None
) -> FetchedSource:
    """*ref*'s bytes on disk, from the sha256 cache when possible.

    On a cache hit the fetch seam is not consulted at all — not called-and-ignored,
    NOT REACHED — which is the only form of "zero network on re-ingest" that survives a
    caller passing a fetcher with side effects.

    ``fetch_fn`` is the single injectable byte seam; it defaults to the guarded
    ``net.fetch`` under the ``SOURCE`` egress profile, so a source fetch is host-
    classified, redirect-re-checked, byte-capped and egress-audited like every other
    outbound request. Nothing in this module opens a socket by any other route.
    """
    hit = cached_source(ref)
    if hit is not None:
        return hit
    body = await (fetch_fn or _default_fetch)(ref.url)
    body = bytes(body or b"")
    if not body:
        raise SourceFetchError(f"empty response for {ref.url}")
    sha256 = _digest(body)
    suffix = ".pdf" if is_pdf_bytes(body) else ".bin"
    path = _original_path(sha256, suffix)
    if not path.is_file():
        path.write_bytes(body)
    _pointer_path(ref).write_text(
        json.dumps({"sha256": sha256, "suffix": suffix, "url": ref.url, "kind": ref.kind}),
        encoding="utf-8",
    )
    return FetchedSource(path=path, sha256=sha256, from_cache=False, ref=ref)


async def _default_fetch(url: str) -> bytes:
    from personalclaw.net.client import fetch as net_fetch
    from personalclaw.net.policy import SOURCE, egress_policy_for

    response = await net_fetch(url, policy=egress_policy_for(SOURCE))
    if int(getattr(response, "status", 0)) >= 400:
        raise SourceFetchError(f"HTTP {response.status} for {url}")
    return bytes(getattr(response, "body", b"") or b"")


# ══ SECTION DETECTION ════════════════════════════════════════════════════════════

#: Role cues, in resolution order. A heading takes the role of the FIRST pattern it
#: matches, so the order encodes the tie-breaks:
#:  * ``references`` is resolved early so a bibliography can never be captured by a
#:    looser later pattern — it is the one section whose text must be STRIPPED, and
#:    mis-roling it leaks a page of citations into ``body``.
#:  * ``results`` precedes ``discussion`` so an "Analysis" heading (which either could
#:    claim) lands in the half that reports what happened.
_ROLE_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (ROLE_ABSTRACT, re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?abstract\b", re.IGNORECASE)),
    (
        ROLE_REFERENCES,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:references|bibliography|works\s+cited)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_INTRODUCTION,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:introduction|background|motivation)\b", re.IGNORECASE
        ),
    ),
    (
        ROLE_METHOD,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:methods?|methodology|approach|architecture|"
            r"implementation|experimental\s+setup|our\s+model)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_RESULTS,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:results|evaluation|experiments?|findings|analysis)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_DISCUSSION,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:discussion|limitations|related\s+work|future\s+work|"
            r"threats\s+to\s+validity)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_CONCLUSION,
        re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:conclusions?|concluding\s+remarks|summary)\b",
            re.IGNORECASE,
        ),
    ),
)


def role_for(title: str) -> str:
    """The canonical paper role a heading names, or ``other``."""
    text = " ".join(str(title or "").split())
    for role, pattern in _ROLE_CUES:
        if pattern.match(text):
            return role
    return ROLE_OTHER


@dataclass(frozen=True)
class Section:
    """One detected section: its heading, its role, and its char span in the full text."""

    title: str
    role: str
    start: int
    end: int
    page: int
    strategy: str


@dataclass(frozen=True)
class DocumentText:
    """The flattened document: one text, plus each page's span within it."""

    text: str
    page_spans: tuple[tuple[int, int], ...]

    @property
    def page_count(self) -> int:
        return len(self.page_spans)


def structure_from_text(text: str) -> PdfStructure:
    """A layout-free structure for a document with no PDF to measure.

    Every synthesized line carries size 0, which makes the font tier contribute nothing
    (0 is never > 0 × the ratio) rather than contribute garbage — the cascade then falls
    through to the header-regex tier exactly as §5 intends. One page, because a text
    document has no pagination to honour.
    """
    body = str(text or "")
    lines = tuple(
        PdfLine(page=0, text=stripped, size=0.0, char_count=len(stripped))
        for stripped in (raw.strip() for raw in body.splitlines())
        if stripped
    )
    return PdfStructure(pages=(body,), lines=lines, outline=())


def flatten(structure: PdfStructure) -> DocumentText:
    """Join a structure's pages into one text, recording each page's span.

    The spans are what let the kept-pages floor and the ``meta`` cut speak in PAGES while
    every other operation speaks in char offsets.
    """
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    position = 0
    for page in structure.pages:
        page_text = str(page or "")
        spans.append((position, position + len(page_text)))
        parts.append(page_text)
        position += len(page_text) + 1  # the "\n" joiner below
    return DocumentText(text="\n".join(parts), page_spans=tuple(spans))


def body_font_size(lines: tuple[PdfLine, ...]) -> float:
    """The document's body glyph size: the CHAR-WEIGHTED mode of line sizes.

    Char-weighted rather than line-counted because a paper with many short headings and
    few long body lines would otherwise elect a heading size as "body" and then detect no
    headings at all. Ties break to the SMALLER size — body text is the smaller of any two
    equally-common sizes, and an explicit tie-break is what keeps two runs agreeing.
    """
    weights: dict[float, int] = {}
    for line in lines:
        if line.size > 0:
            weights[line.size] = weights.get(line.size, 0) + max(1, line.char_count)
    if not weights:
        return 0.0
    return sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def detect_sections(structure: PdfStructure) -> tuple[Section, ...]:
    """Detect *structure*'s sections by the §5 cascade. Pure and deterministic.

    Three strategies, the first two UNIONED:

    1. **Outline** — the PDF's own bookmark titles that name a canonical section. The
       document telling us its own structure is the strongest evidence there is.
    2. **Font size** — a short line whose largest glyph exceeds the body size by
       ``HEADING_SIZE_RATIO``. Typographic evidence: weaker than a declaration, but the
       author still put it there on purpose.
    3. **Header regex** — a short line that literally names a canonical section. This
       tier runs ONLY when the union of 1 and 2 is empty, and it deliberately proposes
       nothing it cannot name: a fallback that guessed at structure would be worse than
       one section spanning the whole document, because a wrong span silently truncates
       what an enrichment node reads.

    The union is resolved by char offset with the strategy as tie-break, so a heading
    found by both the outline and the font tier is ONE section attributed to the outline.
    """
    document = flatten(structure)
    candidates = _outline_candidates(structure, document) + _font_candidates(structure, document)
    if not candidates:
        candidates = _header_candidates(structure, document)
    # Total order, then first-wins dedupe by offset: (offset, strategy strength, title).
    candidates.sort(key=lambda c: (c[0], _STRATEGY_RANK.get(c[2], 99), c[1]))
    unique: list[tuple[int, str, str, int]] = []
    for candidate in candidates:
        if unique and candidate[0] == unique[-1][0]:
            continue
        unique.append(candidate)
    sections: list[Section] = []
    for index, (offset, title, strategy, page) in enumerate(unique):
        end = unique[index + 1][0] if index + 1 < len(unique) else len(document.text)
        sections.append(
            Section(
                title=title,
                role=role_for(title),
                start=offset,
                end=end,
                page=page,
                strategy=strategy,
            )
        )
    return tuple(sections)


def _outline_candidates(
    structure: PdfStructure, document: DocumentText
) -> list[tuple[int, str, str, int]]:
    """Bookmark titles that name a canonical section, located in the text.

    Only role-bearing titles: a PDF outline routinely contains figure and table
    bookmarks, and those are not sections. Located by a forward-only scan so repeated
    titles resolve in document order rather than all collapsing onto the first match.
    """
    out: list[tuple[int, str, str, int]] = []
    cursor = 0
    for title in structure.outline:
        if role_for(title) == ROLE_OTHER:
            continue
        offset = document.text.find(title, cursor)
        if offset < 0:
            continue  # a bookmark whose title is not in the extracted text — skip, silently
        out.append((offset, title, STRATEGY_OUTLINE, _page_of(document, offset)))
        cursor = offset + len(title)
    return out


def _font_candidates(
    structure: PdfStructure, document: DocumentText
) -> list[tuple[int, str, str, int]]:
    body = body_font_size(structure.lines)
    if body <= 0:
        return []
    out: list[tuple[int, str, str, int]] = []
    cursor = 0
    for line in structure.lines:
        if line.size <= body * HEADING_SIZE_RATIO or len(line.text) > HEADING_MAX_CHARS:
            continue
        offset = document.text.find(line.text, cursor)
        if offset < 0:
            # The extracted page text can differ from the glyph run (ligatures, soft
            # hyphens). Retry from the page start before giving up — the line IS on that
            # page even when the running cursor has already passed its position.
            page_start = document.page_spans[line.page][0] if line.page < document.page_count else 0
            offset = document.text.find(line.text, page_start)
        if offset < 0:
            continue
        out.append((offset, line.text, STRATEGY_FONT, line.page))
        cursor = offset + len(line.text)
    return out


def _header_candidates(
    structure: PdfStructure, document: DocumentText
) -> list[tuple[int, str, str, int]]:
    """Short lines that literally name a canonical section — the no-layout fallback."""
    out: list[tuple[int, str, str, int]] = []
    cursor = 0
    for line in structure.lines:
        if len(line.text) > HEADING_MAX_CHARS or role_for(line.text) == ROLE_OTHER:
            continue
        offset = document.text.find(line.text, cursor)
        if offset < 0:
            continue
        out.append((offset, line.text, STRATEGY_HEADER, line.page))
        cursor = offset + len(line.text)
    return out


def _page_of(document: DocumentText, offset: int) -> int:
    for index, (start, end) in enumerate(document.page_spans):
        if start <= offset <= end:
            return index
    return max(0, document.page_count - 1)


# ══ PURPOSE-CUT SLICES ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Slice:
    """One role-sized cut of the document."""

    role: str
    text: str
    fraction: float
    section_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reference:
    """One extracted citation, keyed by the strongest tier that could identify it."""

    key: str
    tier: str
    raw: str
    title: str = ""
    year: str = ""


@dataclass(frozen=True)
class SliceResult:
    """Everything the primitive derived from one document, deterministically.

    ``full_text`` is the ``full`` slice of §5 — computed and read here (reference
    extraction and the kept-pages floor both need it) but never persisted as a row: it is
    byte-identical to the item's ``content``. It never reaches a model.
    """

    sections: tuple[Section, ...] = ()
    slices: tuple[Slice, ...] = ()
    references: tuple[Reference, ...] = ()
    full_text: str = ""
    page_count: int = 0
    bibliography_start: int = 0
    body_font_size: float = 0.0
    unkeyed_references: int = 0
    strategies: tuple[str, ...] = ()

    def slice_for(self, role: str) -> Slice | None:
        """The cut for *role*, or None.

        ``full`` is synthesized here rather than stored in ``slices``: §5's four-role
        model is complete, but the full text is already the item's ``content``, so it is
        RETRIEVABLE without ever becoming a duplicate row (``slice_rows`` iterates
        ``PERSISTED_SLICES``, which excludes it).
        """
        if role == SLICE_FULL:
            return Slice(role=SLICE_FULL, text=self.full_text, fraction=1.0)
        for item in self.slices:
            if item.role == role:
                return item
        return None


def slice_document(*, file_path: str = "", text: str = "") -> SliceResult:
    """Shape one document: detect its sections, cut its slices, extract its references.

    *file_path* is preferred (a PDF's layout is what the cascade's strong tiers read);
    *text* is the fallback for anything that is not a readable PDF. Deterministic and
    model-free end to end.
    """
    structure: PdfStructure | None = None
    if file_path and Path(file_path).suffix.lower() == ".pdf":
        structure = read_pdf_structure(file_path)
    if structure is None or not any((page or "").strip() for page in structure.pages):
        structure = structure_from_text(text)
    return slice_structure(structure)


def slice_structure(structure: PdfStructure) -> SliceResult:
    """The pure half of :func:`slice_document` — no filesystem, no network."""
    document = flatten(structure)
    sections = detect_sections(structure)
    bibliography_start = _bibliography_start(sections, document)
    pre_bib_len = max(0, bibliography_start)
    slices: list[Slice] = []

    # Slices require detected PAPER structure — at least one section the cascade could
    # NAME. Without that gate ``meta`` ("the first pages") would fire on every plain .txt
    # in the library, and on a one-page document it is byte-identical to the content, so
    # every note would gain a row that says nothing and doubles what a pool-concatenating
    # consumer reads. A run of large-font lines with no recognizable role is a flyer, not
    # a paper.
    if not any(section.role != ROLE_OTHER for section in sections):
        return SliceResult(
            sections=sections,
            full_text=document.text,
            page_count=document.page_count,
            bibliography_start=bibliography_start,
            body_font_size=body_font_size(structure.lines),
            strategies=tuple(sorted({s.strategy for s in sections})),
        )

    meta_text = _text_for(document, _page_ranges(document, range(META_PAGES), bibliography_start))
    body_text = _text_for(
        document,
        _merge_ranges(
            [(s.start, min(s.end, bibliography_start)) for s in sections if s.role in BODY_ROLES]
            + list(_kept_page_ranges(document, bibliography_start))
        ),
    )
    brief_text = _clamped_brief(document, sections, bibliography_start)

    for role, body in ((SLICE_BRIEF, brief_text), (SLICE_BODY, body_text), (SLICE_META, meta_text)):
        if not body.strip():
            continue
        slices.append(
            Slice(
                role=role,
                text=body,
                fraction=round(len(body) / pre_bib_len, 4) if pre_bib_len else 0.0,
                section_titles=tuple(
                    s.title for s in sections if s.role in _ROLES_FOR_SLICE.get(role, ())
                ),
            )
        )

    references, unkeyed = extract_references(document.text, bibliography_start)
    return SliceResult(
        sections=sections,
        slices=tuple(slices),
        references=references,
        full_text=document.text,
        page_count=document.page_count,
        bibliography_start=bibliography_start,
        body_font_size=body_font_size(structure.lines),
        unkeyed_references=unkeyed,
        strategies=tuple(sorted({s.strategy for s in sections})),
    )


_ROLES_FOR_SLICE: dict[str, tuple[str, ...]] = {
    SLICE_BRIEF: BRIEF_ROLES,
    SLICE_BODY: BODY_ROLES,
    SLICE_META: (),
}


def _bibliography_start(sections: tuple[Section, ...], document: DocumentText) -> int:
    """Where the bibliography begins — the strip point for every slice.

    A document with no detected references section has none, so the whole text is
    pre-bibliography. That is the honest answer: guessing a cut point from, say, the last
    20% of the pages would strip real content from every paper that ends with an appendix.
    """
    for section in sections:
        if section.role == ROLE_REFERENCES:
            return section.start
    return len(document.text)


def _page_ranges(
    document: DocumentText, indices: Iterable[int], limit: int
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for index in indices:
        if index < 0 or index >= document.page_count:
            continue
        start, end = document.page_spans[index]
        end = min(end, limit)
        if end > start:
            out.append((start, end))
    return out


def _kept_page_ranges(
    document: DocumentText, bibliography_start: int
) -> tuple[tuple[int, int], ...]:
    """§5's floor: the first ``KEEP_FIRST_PAGES`` and last ``KEEP_LAST_PAGES``
    PRE-BIBLIOGRAPHY pages, always retained regardless of what detection found.

    "Pre-bibliography" is load-bearing: the last pages of a paper are its bibliography,
    and a floor that kept them would defeat the reference-stripping the same slice does.
    """
    pre_bib_pages = [
        index
        for index, (start, _end) in enumerate(document.page_spans)
        if start < bibliography_start
    ]
    if not pre_bib_pages:
        return ()
    # `pre_bib_pages[-0:]` is the WHOLE list, not the empty one — so a zero keep-count has
    # to be spelled out, or turning the floor OFF turns it maximally ON. (Found by the
    # falsification that set both counts to 0 and got every page back.)
    first = pre_bib_pages[:KEEP_FIRST_PAGES] if KEEP_FIRST_PAGES > 0 else []
    last = pre_bib_pages[-KEEP_LAST_PAGES:] if KEEP_LAST_PAGES > 0 else []
    return _merge_ranges(_page_ranges(document, sorted(set(first + last)), bibliography_start))


def _clamped_brief(
    document: DocumentText, sections: tuple[Section, ...], bibliography_start: int
) -> str:
    """``brief`` = abstract + intro + conclusion, clamped into the §5 fraction band."""
    ranges = _merge_ranges(
        [(s.start, min(s.end, bibliography_start)) for s in sections if s.role in BRIEF_ROLES]
    )
    text = _text_for(document, ranges)
    budget = max(0, bibliography_start)
    if not budget:
        return text
    # A floor rounds UP and a ceiling rounds DOWN. Truncating the floor lands the topped-up
    # brief a character or two BELOW the fraction it is supposed to guarantee, which is not
    # a floor — it is a floor that fails its own assertion.
    floor_chars = math.ceil(budget * BRIEF_MIN_FRACTION)
    ceiling_chars = int(budget * BRIEF_MAX_FRACTION)
    if len(text) < floor_chars:
        # Too small to be a brief (often: no abstract heading was detected). Top up from
        # the leading text, which for a paper is the title block and abstract region —
        # the same content the roles would have selected had they been found.
        ranges = _merge_ranges([*ranges, (0, floor_chars)])
        text = _text_for(document, ranges)
    if len(text) > ceiling_chars:
        text = _truncate_on_whitespace(text, ceiling_chars)
    return text


def _truncate_on_whitespace(text: str, limit: int) -> str:
    """Cut at or before *limit*, on a whitespace boundary when one is near enough."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind(" ")
    return head[:cut].rstrip() if cut > 0 else head.rstrip()


def _merge_ranges(ranges: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Sort + merge overlapping char ranges, so no byte of the document is emitted twice.

    Slices are built from ranges rather than concatenated strings for exactly this
    reason: a body section that overlaps a kept page would otherwise appear twice, and a
    duplicated method section is a doubled token bill for whoever reads the slice.
    """
    ordered = sorted((s, e) for s, e in ranges if e > s)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _text_for(document: DocumentText, ranges: Iterable[tuple[int, int]]) -> str:
    return "\n\n".join(
        chunk for chunk in (document.text[s:e].strip() for s, e in ranges) if chunk
    ).strip()


# ══ REFERENCE EXTRACTION ═════════════════════════════════════════════════════════

#: Bracketed entry markers (``[12]``) — the dominant numeric citation style, and the one
#: that survives line-wrapping, since the marker can appear mid-line.
_ENTRY_BRACKET = re.compile(r"\[\d{1,3}\]")
#: ``12. Author…`` at a line start — the other common numbered style.
_ENTRY_NUMBERED = re.compile(r"(?m)^\s*\d{1,3}\.\s+")
#: A year in parentheses or bare.
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
#: A surname-shaped token: capitalized, ≥2 letters, not an all-caps acronym run.
_SURNAME = re.compile(r"\b([A-Z][a-z]{1,})\b")
#: Title candidates sit between sentence delimiters (or quotes) inside an entry.
_TITLE_SPLIT = re.compile(r"[.;]\s+|[\"“”]")


def extract_references(
    full_text: str, bibliography_start: int
) -> tuple[tuple[Reference, ...], int]:
    """Extract the bibliography's references by the §5 four-tier cascade.

    Returns ``(references, unkeyed_count)``.

    The cascade is ordered by how much an identifier can be TRUSTED, not by how often it
    appears:

    1. ``arxiv`` — an arXiv id names exactly one paper, globally. Nothing beats it.
    2. ``doi`` — likewise exact. Second only because a preprint's DOI and its arXiv id
       can both appear in one entry, and the arXiv id is the one this codebase can
       re-fetch (:func:`sniff_source`).
    3. ``title`` — no identifier, so the title becomes the key, fuzzily matched against
       titles already keyed from THIS bibliography so one work cited twice in two formats
       collapses to one reference. This is the deterministic replacement for asking a
       model "are these the same paper".
    4. ``author_year`` — nothing but a name and a date. Weakest, and last, because it
       collides (one author, one prolific year) in a way the tiers above cannot.

    An entry satisfying none of the four is NOT emitted with a made-up key; it is counted
    as unkeyed. A fabricated citation key is worse than an admitted gap, because a later
    linking pass (KNOWLEDGE-SYNTHESIS) would treat it as real.
    """
    text = str(full_text or "")
    if bibliography_start >= len(text):
        return (), 0
    body = text[bibliography_start:]
    references: list[Reference] = []
    seen_keys: set[str] = set()
    unkeyed = 0
    for entry in _split_entries(body)[:MAX_REFERENCES]:
        reference = _key_entry(entry, references)
        if reference is None:
            unkeyed += 1
            continue
        if reference.key in seen_keys:
            continue  # the same work cited twice — one reference, per the title tier
        seen_keys.add(reference.key)
        references.append(reference)
    return tuple(references), unkeyed


def _split_entries(body: str) -> list[str]:
    """Split a bibliography into entries, trying the two numbered styles then blank lines.

    The bracket style is split by MARKER POSITION rather than by line, because PDF text
    extraction wraps entries mid-line and a line-based split would fuse two citations.
    """
    without_heading = re.sub(
        r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:references|bibliography|works\s+cited)\b[^\n]*\n?",
        "",
        body,
        count=1,
        flags=re.IGNORECASE,
    )
    marks = [m.start() for m in _ENTRY_BRACKET.finditer(without_heading)]
    if not marks:
        marks = [m.start() for m in _ENTRY_NUMBERED.finditer(without_heading)]
    if marks:
        bounds = marks + [len(without_heading)]
        pieces = [without_heading[bounds[i] : bounds[i + 1]] for i in range(len(marks))]
    else:
        pieces = re.split(r"\n\s*\n", without_heading)
    return [
        " ".join(piece.split())
        for piece in pieces
        if len(" ".join(piece.split())) >= MIN_REFERENCE_CHARS
    ]


def _key_entry(entry: str, existing: list[Reference]) -> Reference | None:
    year_match = _YEAR.search(entry)
    year = year_match.group(1) if year_match else ""

    if _ARXIV_MARKER.search(entry):
        found = _ARXIV_ID.search(entry)
        if found:
            return Reference(
                key=f"arXiv:{found.group(1)}",
                tier=TIER_ARXIV,
                raw=entry,
                title=_title_candidate(entry),
                year=year,
            )

    doi = _DOI.search(entry)
    if doi:
        return Reference(
            key=f"doi:{doi.group(1).rstrip('.')}",
            tier=TIER_DOI,
            raw=entry,
            title=_title_candidate(entry),
            year=year,
        )

    title = _title_candidate(entry)
    if title:
        merged = _fuzzy_match(title, existing)
        return Reference(
            key=merged or f"title:{_normalize_title(title)}",
            tier=TIER_TITLE,
            raw=entry,
            title=title,
            year=year,
        )

    surname = _SURNAME.search(entry)
    if surname and year and entry.find(year) <= AUTHOR_YEAR_PROXIMITY_CHARS:
        return Reference(
            key=f"author_year:{surname.group(1).lower()}:{year}",
            tier=TIER_AUTHOR_YEAR,
            raw=entry,
            year=year,
        )
    return None


def _title_candidate(entry: str) -> str:
    """The longest sentence-delimited span in *entry* with enough words to be a title.

    Longest rather than first: an entry begins with an author list, which is itself
    sentence-delimited, and picking the first span would key every reference by its
    authors. Ties break to the EARLIER span so two runs agree.
    """
    best = ""
    for piece in _TITLE_SPLIT.split(entry):
        candidate = " ".join(str(piece or "").split()).strip("\"'“”.,;: ")
        if len(candidate.split()) < TITLE_MIN_WORDS:
            continue
        if len(candidate) > len(best):
            best = candidate
    return best


def _fuzzy_match(title: str, existing: list[Reference]) -> str | None:
    """The key of an already-seen reference whose title matches *title*, else None.

    A SLIDING WINDOW rather than a whole-string ratio: an entry's title span often
    carries a leading author fragment of unknown length, and comparing whole strings
    would score two records of the same paper well below ``TITLE_MATCH_RATIO``. The
    window walks the candidate in ``TITLE_WINDOW_STEP_CHARS`` strides and keeps the best
    overlap, which is what makes the match robust to that fragment.
    """
    needle = _normalize_title(title)
    if not needle:
        return None
    for reference in existing:
        haystack = _normalize_title(reference.title)
        if not haystack:
            continue
        if _sliding_ratio(needle, haystack) >= TITLE_MATCH_RATIO:
            return reference.key
    return None


def _sliding_ratio(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    width = len(needle)
    if width >= len(haystack):
        return SequenceMatcher(None, needle, haystack).ratio()
    best = 0.0
    for start in range(0, len(haystack) - width + 1, TITLE_WINDOW_STEP_CHARS):
        best = max(best, SequenceMatcher(None, needle, haystack[start : start + width]).ratio())
    return best


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(title or "").lower()).strip()


# ══ PERSISTENCE ══════════════════════════════════════════════════════════════════


def slice_rows(result: SliceResult) -> list[dict[str, Any]]:
    """*result*'s slices as ``extracted_contents`` row payloads, in ``PERSISTED_SLICES``
    order — the ONE place a slice's row shape is defined.

    Rows land on the SAME item. No chunk rows, no child items: §5's slices are role-sized
    views of one document, and the repo removed the chunk-item model on purpose.
    """
    rows: list[dict[str, Any]] = []
    for role in PERSISTED_SLICES:
        piece = result.slice_for(role)
        if piece is None:
            continue
        rows.append(
            {
                "node_type": slice_node_type(role),
                "text": piece.text,
                "metadata": {
                    "role": role,
                    "chars": len(piece.text),
                    "fraction_of_document": piece.fraction,
                    "sections": list(piece.section_titles),
                    "references_stripped": result.bibliography_start < len(result.full_text),
                },
            }
        )
    return rows


def reference_metadata(result: SliceResult) -> dict[str, Any]:
    """*result*'s structural findings, for the item's ``file_metadata``.

    §5 stores references and stops there: cross-item reference LINKING is
    KNOWLEDGE-SYNTHESIS's relate-on-persist step, so this emits the extracted records and
    resolves nothing.
    """
    return {
        "sections": [
            {"title": s.title, "role": s.role, "page": s.page, "strategy": s.strategy}
            for s in result.sections
        ],
        "section_strategies": list(result.strategies),
        "references": [
            {"key": r.key, "tier": r.tier, "title": r.title, "year": r.year}
            for r in result.references
        ],
        "references_unkeyed": result.unkeyed_references,
    }
