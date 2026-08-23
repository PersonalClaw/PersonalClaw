"""Markdown / HTML → a DocumentModel.

The PRIMARY authoring path, not a convenience. The agent is already good at markdown, and
"model writes the source, code renders the artifact" is both cheaper and far more reliable
than teaching a model a binary file format.

Security: HTML reaching here is agent- or web-authored and therefore untrusted. It is
routed through the platform's EXISTING sanitizer (`web/extract.sanitize_html`) and
credential redactor (`security.redact_credentials`) — never a second implementation.
"""

from __future__ import annotations

import re
from dataclasses import replace

from personalclaw.documents.model import Block, DeckModel, DocumentModel, Run, Slide

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
# A markdown table's separator row: |---|:--:|. Distinguishes a real table from a line
# that merely happens to contain pipes.
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_HRULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
# Speaker notes for the deck path, kept out of the visible body.
_NOTES = re.compile(r"^\s*<!--\s*notes:\s*(.*?)\s*-->\s*$", re.IGNORECASE | re.DOTALL)


_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_WORD = re.compile(r"[0-9A-Za-z_]")


def _delim_run(text: str, i: int) -> int:
    """Length of the run of identical emphasis characters starting at ``i``."""
    ch = text[i]
    j = i
    while j < len(text) and text[j] == ch:
        j += 1
    return j - i


def _opens(text: str, i: int, take: int, ch: str) -> bool:
    """Can the delimiter run at ``i`` open emphasis?

    Two rules, both there to stop ordinary prose from being read as markup: an opener is
    never followed by whitespace (so ``2 * 3`` keeps its asterisk), and an ``_`` opener
    never sits inside a word (so ``snake_case_name`` keeps its underscores).
    """
    after = text[i + take : i + take + 1]
    if not after or after.isspace():
        return False
    return not (ch == "_" and bool(_WORD.match(text[i - 1 : i])))


def _closes(text: str, j: int, take: int, ch: str) -> bool:
    """Can the delimiter run at ``j`` close emphasis? The mirror of :func:`_opens`."""
    if text[j - 1].isspace():
        return False
    return not (ch == "_" and bool(_WORD.match(text[j + take : j + take + 1])))


def _find_closer(text: str, content_start: int, take: int, ch: str) -> int:
    """Index of the closing delimiter run, or ``-1``.

    Only a run of the SAME length closes, which is what makes nesting work: scanning for
    the ``**`` that ends a bold span steps over the single ``*`` of an inner italic
    instead of closing early on it.
    """
    j = content_start
    while j < len(text):
        c = text[j]
        if c == "`":  # a code span's contents are literal and never close emphasis
            close = text.find("`", j + 1)
            j = close + 1 if close > j + 1 else j + 1
            continue
        if c != ch:
            j += 1
            continue
        run = _delim_run(text, j)
        if run == take and j > content_start and _closes(text, j, take, ch):
            return j
        j += run
    return -1


def _scan(text: str, *, bold: bool, italic: bool, link: str) -> list[Run]:
    """Tokenize ``text`` into runs, recursing through nested emphasis and link labels."""
    out: list[Run] = []
    buf: list[str] = []

    def flush_literal() -> None:
        if buf:
            out.append(Run(text="".join(buf), bold=bold, italic=italic, link=link))
            buf.clear()

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "`":
            close = text.find("`", i + 1)
            if close > i + 1:  # an empty span (``) is not a code span
                flush_literal()
                body = text[i + 1 : close]
                out.append(Run(text=body, bold=bold, italic=italic, code=True, link=link))
                i = close + 1
                continue
        elif ch == "[":
            m = _LINK.match(text, i)
            if m and m.group(1):  # an empty label is not a link
                flush_literal()
                out.extend(_scan(m.group(1), bold=bold, italic=italic, link=m.group(2) or link))
                i = m.end()
                continue
        elif ch in "*_":
            run = _delim_run(text, i)
            # 1 = italic, 2 = bold, 3 = both. A run of 4+ is nobody's emphasis and stays
            # literal rather than losing characters to a half-consumed marker.
            if run <= 3 and _opens(text, i, run, ch):
                close = _find_closer(text, i + run, run, ch)
                if close >= 0:
                    flush_literal()
                    out.extend(
                        _scan(
                            text[i + run : close],
                            bold=bold or run >= 2,
                            italic=italic or run != 2,
                            link=link,
                        )
                    )
                    i = close + run
                    continue
            buf.append(text[i : i + run])  # unmatched marker → literal, never dropped
            i += run
            continue
        buf.append(ch)
        i += 1

    flush_literal()
    return out


def parse_inline(text: str) -> list[Run]:
    """Parse inline markdown into styled runs: bold, italic, code and links.

    The same shallow subset a generated document actually uses, but the formatting now
    survives INTO the model instead of being thrown away — one parser feeding both the
    runs-carrying surfaces and, via :func:`inline_text`, the ones that stay plain strings.

    No input is ever dropped: an unmatched ``**``, an unterminated ``[t](`` and an
    intra-word underscore all stay literal. Edge whitespace is trimmed and empty runs
    discarded, so blank or whitespace-only input yields NO runs at all.
    """
    runs = _scan(text or "", bold=False, italic=False, link="")
    if runs:
        runs[0] = replace(runs[0], text=runs[0].text.lstrip())
        runs[-1] = replace(runs[-1], text=runs[-1].text.rstrip())
    return [r for r in runs if r.text]


def inline_text(runs: list[Run]) -> str:
    """Plain text of a run list, for the surfaces that stay ``list[str]``."""
    return "".join(r.text for r in runs)


def document_from_markdown(md: str, *, title: str = "") -> DocumentModel:
    """Parse the markdown subset a generated document actually uses.

    Not a full CommonMark implementation, and deliberately so: it handles headings,
    paragraphs, both list kinds, fenced code, tables and rules — the shapes an agent
    produces when asked for a document. Anything unrecognized becomes a paragraph, so no
    input is ever silently dropped.
    """
    model = DocumentModel(title=title.strip())
    lines = (md or "").replace("\r\n", "\n").split("\n")
    para: list[str] = []
    bullets: list[str] = []
    numbered: list[str] = []
    code: list[str] | None = None
    table: list[list[str]] = []

    def flush() -> None:
        nonlocal para, bullets, numbered, table
        if para:
            # `text` is left to the model's derivation rather than computed a second time.
            model.blocks.append(Block(kind="paragraph", runs=parse_inline(" ".join(para))))
            para = []
        if bullets:
            items = [inline_text(parse_inline(b)) for b in bullets]
            model.blocks.append(Block(kind="bullets", items=items))
            bullets = []
        if numbered:
            items = [inline_text(parse_inline(n)) for n in numbered]
            model.blocks.append(Block(kind="numbered", items=items))
            numbered = []
        if table:
            model.blocks.append(Block(kind="table", rows=table))
            table = []

    for line in lines:
        if _FENCE.match(line):
            if code is None:
                flush()
                code = []
            else:
                model.blocks.append(Block(kind="code", text="\n".join(code)))
                code = None
            continue
        if code is not None:
            code.append(line)  # verbatim — no inline stripping inside a code fence
            continue

        if not line.strip():
            flush()
            continue
        if _TABLE_SEP.match(line):
            continue  # the |---| separator carries no data
        m = _TABLE_ROW.match(line)
        if m:
            if para or bullets or numbered:
                flush()
            # Cells stay plain strings: `Block.rows` is `list[list[str]]` and every reader
            # (writers, API serializers, tests) depends on that. Widening it to carry runs
            # is the model third's call, not something to guess at from here.
            table.append([inline_text(parse_inline(c)) for c in m.group(1).split("|")])
            continue
        if table:
            flush()  # a non-table line ends the table
        if _HRULE.match(line):
            flush()
            model.blocks.append(Block(kind="pagebreak"))
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            runs = parse_inline(m.group(2))
            # An H1 with no explicit title becomes the document title rather than a
            # duplicate heading under it. A title is a plain string, so it takes the text.
            if level == 1 and not model.title and not model.blocks:
                model.title = inline_text(runs)
            else:
                model.blocks.append(Block(kind="heading", runs=runs, level=level))
            continue
        m = _BULLET.match(line)
        if m:
            if para or numbered:
                flush()
            bullets.append(m.group(1))
            continue
        m = _NUMBERED.match(line)
        if m:
            if para or bullets:
                flush()
            numbered.append(m.group(1))
            continue
        para.append(line.strip())

    if code is not None:  # an unterminated fence still carries content
        model.blocks.append(Block(kind="code", text="\n".join(code)))
    flush()
    return model


def document_from_html(html: str, *, title: str = "") -> DocumentModel:
    """Sanitize untrusted HTML, convert to markdown, then reuse the markdown path.

    One parser, not two. The sanitize + redact steps are the platform's existing ones.
    """
    from personalclaw.security import redact_credentials
    from personalclaw.web.extract import sanitize_html

    safe = sanitize_html(html or "")
    safe, _ = redact_credentials(safe)
    try:
        import html2text

        conv = html2text.HTML2Text()
        conv.body_width = 0  # never hard-wrap: a wrap would become a paragraph break
        md = conv.handle(safe)
    except Exception:  # noqa: BLE001 — degrade to tag-stripped text rather than failing
        md = re.sub(r"<[^>]+>", " ", safe)
    return document_from_markdown(md, title=title)


def deck_from_markdown(md: str, *, title: str = "") -> DeckModel:
    """`#`/`##` start slides; body lines become bullets; `<!-- notes: … -->` are notes.

    A deck is an outline, so bullets are the default body shape — prose paragraphs on a
    slide are what makes generated decks unreadable.
    """
    deck = DeckModel(title=title.strip())
    current: Slide | None = None
    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        note = _NOTES.match(raw)
        if note:
            if current is not None:
                current.notes = (current.notes + "\n" + note.group(1)).strip()
            continue
        m = _HEADING.match(raw)
        if m and len(m.group(1)) <= 2:
            text = inline_text(parse_inline(m.group(2)))
            # A leading H1 titles the DECK when no title was supplied, rather than
            # becoming a content-free first slide.
            if len(m.group(1)) == 1 and not deck.title and current is None:
                deck.title = text
                continue
            current = Slide(title=text)
            deck.slides.append(current)
            continue
        if not raw.strip():
            continue
        if current is None:  # body before any heading — an untitled opening slide
            current = Slide(title=deck.title or "")
            deck.slides.append(current)
        b = _BULLET.match(raw) or _NUMBERED.match(raw)
        # A slide body is `list[str]`; the atom does not widen it, so join the runs' text.
        current.body.append(inline_text(parse_inline(b.group(1) if b else raw)))
    return deck
