"""Structural chunker for knowledge indexing (KL-9).

Splits an item's consolidated text into embeddable chunks that respect the document's
real structure. Because the file readers already normalize every structured type to
markdown — pptx slides render as ``## Slide N: …``, xlsx/csv sheets as ``## <sheet>``,
docx/markdown headings as ``#…`` — a single markdown-heading rule captures headings,
slides AND sheets. Anything with no headings (a PDF whose pages are joined with plain
newlines, a raw note) has one implicit section and falls back to size-based splitting.

Each chunk carries the source ``section`` label and its 1-based ``line_start``/
``line_end`` span, so a retrieval hit can be cited to a place in the document. Long
sections and structureless blobs are split by size with a trailing character overlap so
a fact straddling a boundary still lands whole in at least one chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Target maximum chunk size in characters. A section at or under this is one chunk;
#: larger sections (and structureless documents) are greedily split on line boundaries.
MAX_CHARS = 1500

#: Characters of trailing context carried into the next chunk when a section is split,
#: so a sentence spanning the cut is not orphaned. Kept well under ``MAX_CHARS``.
OVERLAP = 200

#: A markdown heading opens a section. Up to three leading spaces are tolerated (the
#: CommonMark allowance); one to six ``#`` then whitespace then non-space content. This
#: matches the readers' ``## Slide N: …`` / ``## <sheet>`` / docx ``#…`` output alike.
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")


@dataclass
class Chunk:
    """One embeddable slice of a document. ``chunk_index`` is filled by ``chunk_text``
    in document order; ``section`` is the heading text (``None`` for a preamble or a
    structureless document); the line span is 1-based and inclusive. ``embedding`` is the
    serialized vector BLOB (set by the ingest embed step; ``None`` until then)."""

    text: str
    section: str | None
    line_start: int
    line_end: int
    chunk_index: int = 0
    embedding: bytes | None = None


@dataclass(frozen=True)
class Boundary:
    """One section boundary in a document: where it starts and what heading opens it.

    ``offset`` is a 0-based CHARACTER offset into the exact string that was passed in, so a
    caller can slice the document at it without re-deriving anything. ``line`` is 1-based to
    match `Chunk`'s spans and what an editor shows.
    """

    offset: int
    line: int
    title: str
    level: int


def section_boundaries(content: str) -> list[Boundary]:
    """Every heading-opened section boundary in *content*, in document order.

    🔴 This exists so KL-19's split verb cuts on the SAME rule the chunker sections on. A
    split whose "section boundary" came from a second heading regex would hand the chunk layer
    a document whose sections it does not agree with — the halves would re-chunk along
    different seams than the UI drew, and the outline a reader chose from would not be the
    thing that moved. One rule, one owner, two readers.

    The offset of a boundary is the start of its heading LINE, not the line after it: the
    heading is part of the section it opens (the same choice `_split_into_sections` makes),
    which is what lets a split give each half its own title.
    """
    if not content:
        return []
    out: list[Boundary] = []
    offset = 0
    for index, text in enumerate(content.split("\n"), start=1):
        if _HEADING.match(text):
            stripped = text.lstrip()
            hashes = len(stripped) - len(stripped.lstrip("#"))
            out.append(
                Boundary(
                    offset=offset,
                    line=index,
                    title=stripped[hashes:].strip(),
                    level=hashes,
                )
            )
        offset += len(text) + 1  # +1 for the "\n" that `split` consumed
    return out


def chunk_text(content: str, *, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[Chunk]:
    """Chunk *content* on structural boundaries, size-splitting oversized sections.

    Returns chunks in document order with ``chunk_index`` assigned 0..N-1. Whitespace-only
    chunks are dropped. An empty/blank document yields no chunks.
    """
    if not content or not content.strip():
        return []
    lines = content.split("\n")
    out: list[Chunk] = []
    for label, numbered in _split_into_sections(lines):
        out.extend(_size_split(numbered, label, max_chars, overlap))
    # Drop whitespace-only chunks (e.g. a section of blank lines) and renumber so the
    # index is a dense 0..N-1 sequence the store can rely on.
    kept = [c for c in out if c.text.strip()]
    for i, c in enumerate(kept):
        c.chunk_index = i
    return kept


def _split_into_sections(lines: list[str]) -> list[tuple[str | None, list[tuple[int, str]]]]:
    """Group physical lines into (heading-label, [(lineno, text), …]) sections.

    A heading line opens a new section and is kept as that section's first line (it is
    real content and orients the chunk). Text before the first heading is a preamble
    section labelled ``None``. A document with no headings is a single ``None`` section.
    """
    sections: list[tuple[str | None, list[tuple[int, str]]]] = []
    label: str | None = None
    cur: list[tuple[int, str]] = []
    for lineno, text in enumerate(lines, 1):
        if _HEADING.match(text):
            if cur:
                sections.append((label, cur))
            label = text.strip().lstrip("#").strip()
            cur = [(lineno, text)]
        else:
            cur.append((lineno, text))
    if cur:
        sections.append((label, cur))
    return sections


def _size_split(
    numbered: list[tuple[int, str]], label: str | None, max_chars: int, overlap: int
) -> list[Chunk]:
    """Greedily pack a section's lines into chunks of at most *max_chars*, carrying an
    *overlap* of trailing lines into each successor chunk. A single physical line longer
    than *max_chars* is emitted as its own character windows (same line number)."""
    chunks: list[Chunk] = []
    buf: list[tuple[int, str]] = []

    def buf_len() -> int:
        # Joined length: line chars plus the newlines between them.
        return sum(len(t) for _, t in buf) + max(0, len(buf) - 1)

    for lineno, text in numbered:
        if len(text) > max_chars:
            # An over-long single line can't share a chunk sensibly — flush, then window it.
            if buf:
                chunks.append(_mk(buf, label))
                buf = []
            for win in _char_windows(text, max_chars, overlap):
                chunks.append(Chunk(text=win, section=label, line_start=lineno, line_end=lineno))
            continue
        if buf and buf_len() + 1 + len(text) > max_chars:
            chunks.append(_mk(buf, label))
            buf = _tail_overlap(buf, overlap)
        buf.append((lineno, text))
    if buf:
        chunks.append(_mk(buf, label))
    return chunks


def _mk(buf: list[tuple[int, str]], label: str | None) -> Chunk:
    # Trim leading/trailing blank lines so the line span and text point at real content
    # — a chunk whose line_end lands on a trailing blank line cites nothing. An all-blank
    # buffer collapses to an empty chunk that chunk_text's kept-filter drops.
    trimmed = buf
    while trimmed and not trimmed[0][1].strip():
        trimmed = trimmed[1:]
    while trimmed and not trimmed[-1][1].strip():
        trimmed = trimmed[:-1]
    if not trimmed:
        return Chunk(text="", section=label, line_start=buf[0][0], line_end=buf[-1][0])
    return Chunk(
        text="\n".join(t for _, t in trimmed),
        section=label,
        line_start=trimmed[0][0],
        line_end=trimmed[-1][0],
    )


def _tail_overlap(buf: list[tuple[int, str]], overlap: int) -> list[tuple[int, str]]:
    """The trailing lines of *buf* whose cumulative length fits in *overlap* chars — the
    context seeded into the next chunk so a boundary-straddling passage stays whole."""
    if overlap <= 0:
        return []
    carried: list[tuple[int, str]] = []
    total = 0
    for lineno, text in reversed(buf):
        add = len(text) + (1 if carried else 0)
        if total + add > overlap:
            break
        carried.insert(0, (lineno, text))
        total += add
    return carried


def _char_windows(text: str, size: int, overlap: int) -> list[str]:
    """Split one over-long line into overlapping character windows of at most *size*."""
    step = max(1, size - overlap)
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        out.append(text[i : i + size])
        if i + size >= n:
            break
        i += step
    return out
