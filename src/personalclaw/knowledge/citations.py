"""Per-marker citation records -- which retrieved source supports WHICH sentence.

The synthesis path already asks the model to cite: ``_pipe_fenced_sources``
(``workflows/bindings.py``) numbers the retrieved items ``[1]..[n]`` and instructs "Cite them
as [n] when you use them". Nothing parsed those markers. The bundled synthesis template
satisfied its citation requirement by storing the WHOLE retrieved set
(``{{nodes.recall.output.items | map('item_id')}}``), which answers "what did we look at"
and cannot answer "which source supports this sentence" -- the only question a reader
challenging a synthesized claim actually asks. This module makes the marker the unit of
record instead of the retrieval batch.

Three rules earn their own code here, because each one is a way the naive version corrupts
attribution:

1. **The marker NUMBER is the key, never the order of appearance.** A model that writes
   "... [3] ... [1] ..." means sources 3 and 1. Renumbering by first use would silently
   re-point every claim at the wrong source, and the resulting record would look perfectly
   well-formed.
2. **A marker naming no registered source is REMOVED from the prose.** A dangling ``[7]``
   reaching the reader is worse than no citation at all: it looks like attribution and
   resolves to nothing. It is dropped, and the drop is reported as a warning rather than
   swallowed, so a chronically over-citing model is visible instead of merely tidy.
3. **A previously-synthesized item is stripped before it re-enters a prompt as a source.**
   Its own ``[1]`` refers to ITS sources, not this turn's numbering; left in place it
   collides with the fresh ``[1]`` and the collision is undetectable after the fact. Only
   marker-shaped spans go: ``[TODO]``, ``[]`` and the markdown link ``[1](url)`` survive,
   the last via the ``(?!\\()`` lookahead that keeps a link label from being read as a
   citation.

``persist_form`` encodes one citation as ``cite:<marker>:<chunk_index>:<item_id>`` -- a
greppable prefix, two integers, and the id as an unbounded tail so an id containing a colon
still parses (split with ``maxsplit=3``). The excerpt is NOT in the string form; the
relational rows (``item_citations``) are the truth for it. ``parse_persist_form`` SKIPS the
legacy bare ``item:<id>`` values rather than raising, because a store written before this
module exists holds exactly those and refusing to read it would turn old rows into an error
instead of a known gap.

This module deliberately does not import the store, and the store does not import this
module: they meet on plain dicts. A synthesis-time text utility and a SQLite schema have no
reason to be coupled, and the absent edge is what lets either be tested without the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

#: A citation marker: ``[12]``. Capped at three digits because a synthesis prompt shows
#: tens of sources, not thousands, and an unbounded ``\d+`` would read a year (``[2026]``)
#: or a bracketed line number as attribution. The ``(?!\()`` lookahead excludes a markdown
#: link label -- ``[1](https://x)`` is a link, and treating it as a citation would both
#: invent an attribution and mangle the link when the marker is removed.
MARKER_RE: re.Pattern[str] = re.compile(r"\[(\d{1,3})\](?!\()")

#: Excerpt cap. Long enough to recognize the passage the marker points at, short enough
#: that a citation row never becomes a second copy of the source item.
EXCERPT_MAX = 280

# Whitespace repairs applied ONLY when a marker was actually removed, so text with no
# dropped markers is returned byte-identical. Removing "[9]" from "the claim [9] holds"
# otherwise leaves a double space, and from "the claim [9]." a space before the period --
# both of which read as a typo the model did not make.
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+(?=[.,;:!?)])")
_COLLAPSED_RUN = re.compile(r"(?<=\S)[ \t]{2,}")
_TRAILING_ON_LINE = re.compile(r"[ \t]+(?=\n)")


@dataclass(frozen=True)
class SourceRef:
    """One numbered source the prompt actually showed the model.

    ``marker`` is the number the PROMPT displayed, not an index into any later list -- it is
    the only thing the model's ``[n]`` can be matched against.
    """

    marker: int
    item_id: str
    chunk_index: int = -1  # -1 == the whole item, not a chunk of it
    excerpt: str = ""


@dataclass(frozen=True)
class Citation:
    """One RESOLVED marker occurrence, keyed by marker NUMBER.

    ``item_id`` is the SOURCE item -- the thing cited. The citing item is not carried here;
    it is the key the store writes these rows under.
    """

    marker: int
    item_id: str
    chunk_index: int = -1
    excerpt: str = ""


@dataclass(frozen=True)
class Resolution:
    """The outcome of matching a synthesis's markers against the sources it was shown."""

    citations: tuple[Citation, ...]  # one per distinct marker that resolved, ascending
    dropped: tuple[int, ...]  # marker numbers with no registered source
    warnings: tuple[str, ...]  # one human-readable line per dropped marker
    text: str  # the prose with dropped markers removed


def _excerpt(raw: Any) -> str:
    """Collapse whitespace and cap at :data:`EXCERPT_MAX`.

    Whitespace is collapsed rather than preserved because the excerpt is shown inline in an
    attribution UI, where a source's own newlines and indentation are noise that breaks the
    surrounding layout.
    """
    text = " ".join(str(raw or "").split())
    if len(text) <= EXCERPT_MAX:
        return text
    return text[: EXCERPT_MAX - 3].rstrip() + "..."


def _chunk_index(raw: Any) -> int:
    """Coerce a chunk index, defaulting to -1 (whole item).

    Written as an explicit ``None`` check rather than ``int(raw or -1)`` because chunk 0 is a
    real chunk and falsy: the terse form would silently relabel every item's FIRST chunk as
    "the whole item".
    """
    if raw is None:
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def register_sources(items: Sequence[Any]) -> tuple[SourceRef, ...]:
    """Number the retrieved items exactly as the prompt will show them.

    ``enumerate(items, start=1)`` is not a stylistic choice -- it must match
    ``_pipe_fenced_sources`` character for character, because that function is what the model
    reads. Any divergence (skipping an item without an id, sorting, filtering) shifts every
    later number and mis-attributes silently.

    Accepts the shapes the retrieval path actually yields: dicts keyed ``item_id``/``id``
    with ``content``/``summary``/``excerpt`` bodies, or plain strings. A string carries no
    stored identity, so it registers with an empty ``item_id`` and STILL consumes its marker
    number -- dropping it would renumber the sources that follow it.
    """
    refs: list[SourceRef] = []
    for marker, item in enumerate(items, start=1):
        if isinstance(item, dict):
            item_id = str(item.get("item_id") or item.get("id") or "").strip()
            chunk_index = _chunk_index(item.get("chunk_index"))
            body = item.get("content") or item.get("summary") or item.get("excerpt") or ""
        else:
            item_id = ""
            chunk_index = -1
            body = item
        refs.append(
            SourceRef(
                marker=marker,
                item_id=item_id,
                chunk_index=chunk_index,
                excerpt=_excerpt(body),
            )
        )
    return tuple(refs)


def _remove(text: str, should_drop: Callable[[int], bool]) -> str:
    """Delete the markers *should_drop* selects, then repair the holes they leave."""
    out = MARKER_RE.sub(lambda m: "" if should_drop(int(m.group(1))) else m.group(0), text)
    if out == text:
        return text
    out = _SPACE_BEFORE_PUNCT.sub("", out)
    out = _COLLAPSED_RUN.sub(" ", out)
    out = _TRAILING_ON_LINE.sub("", out)
    if not text[:1].isspace():
        # A marker that opened the text leaves the whole string indented by one space.
        out = out.lstrip(" \t")
    return out


def strip_markers(text: str) -> str:
    """Remove every citation marker, for text about to be quoted back into a prompt.

    Used on a previously-synthesized item before it is offered as a source this turn: its
    ``[1]`` names a source from ITS synthesis, and once inside this turn's numbered fence
    that number silently means something else.
    """
    return _remove(text, lambda _marker: True)


def parse_markers(text: str) -> tuple[int, ...]:
    """The distinct marker numbers cited, ascending.

    Ascending by NUMBER and de-duplicated -- deliberately not "in order of appearance".
    Order of appearance is a property of the prose; the number is the identity of the source,
    and the two disagree the moment a model cites ``[3]`` before ``[1]``.
    """
    return tuple(sorted({int(m.group(1)) for m in MARKER_RE.finditer(text)}))


def resolve(text: str, sources: Sequence[SourceRef]) -> Resolution:
    """Match a synthesis's markers against the sources it was shown.

    Resolved markers stay in the prose untouched -- the reader needs them to look the
    attribution up. Unresolvable ones are removed and reported: a model citing ``[7]`` when
    six sources were shown has attributed a claim to nothing, and leaving the marker in place
    would present that nothing as a source.
    """
    by_marker = {ref.marker: ref for ref in sources}
    cited = parse_markers(text)
    citations = tuple(
        Citation(
            marker=marker,
            item_id=by_marker[marker].item_id,
            chunk_index=by_marker[marker].chunk_index,
            excerpt=by_marker[marker].excerpt,
        )
        for marker in cited
        if marker in by_marker
    )
    dropped = tuple(marker for marker in cited if marker not in by_marker)
    warnings = tuple(
        f"citation [{marker}] names no retrieved source; removed from the text"
        for marker in dropped
    )
    dropped_set = frozenset(dropped)
    return Resolution(
        citations=citations,
        dropped=dropped,
        warnings=warnings,
        text=_remove(text, lambda marker: marker in dropped_set),
    )


def persist_form(citations: Sequence[Citation]) -> list[str]:
    """Encode citations for a string-list column: ``cite:<marker>:<chunk_index>:<item_id>``.

    The excerpt is intentionally absent -- it is prose, it would need escaping, and the
    ``item_citations`` rows already hold it. This form exists so a list-of-strings field that
    used to hold bare item ids can carry the marker too.
    """
    return [f"cite:{c.marker}:{c.chunk_index}:{c.item_id}" for c in citations]


def parse_persist_form(values: Sequence[str]) -> tuple[Citation, ...]:
    """Decode :func:`persist_form`, SKIPPING anything that is not in that form.

    Skipping rather than raising is the point: a store written before this module holds the
    legacy bare ``item:<id>`` (and plain-id) values, and a reader that raised on them would
    make every pre-existing synthesis unopenable instead of merely under-attributed.
    """
    out: list[Citation] = []
    for value in values:
        text = str(value or "").strip()
        if not text.startswith("cite:"):
            continue
        parts = text.split(":", 3)
        if len(parts) != 4:
            continue
        try:
            marker = int(parts[1])
            chunk_index = int(parts[2])
        except ValueError:
            continue
        out.append(Citation(marker=marker, item_id=parts[3], chunk_index=chunk_index))
    return tuple(out)
