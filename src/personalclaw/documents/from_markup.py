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

from personalclaw.documents.model import Block, DeckModel, DocumentModel, Slide

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


def _strip_inline(text: str) -> str:
    """Drop inline markdown emphasis so it doesn't render as literal asterisks.

    Deliberately shallow: bold/italic/code/links, nothing else. Rich inline formatting
    would mean carrying styled runs through the model, which is a much larger surface for
    little gain in a generated document.
    """
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [label](url) → label
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.+?)\1", r"\2", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


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
            model.blocks.append(Block(kind="paragraph", text=_strip_inline(" ".join(para))))
            para = []
        if bullets:
            model.blocks.append(Block(kind="bullets", items=[_strip_inline(b) for b in bullets]))
            bullets = []
        if numbered:
            model.blocks.append(Block(kind="numbered", items=[_strip_inline(n) for n in numbered]))
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
            table.append([_strip_inline(c) for c in m.group(1).split("|")])
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
            text = _strip_inline(m.group(2))
            # An H1 with no explicit title becomes the document title rather than a
            # duplicate heading under it.
            if level == 1 and not model.title and not model.blocks:
                model.title = text
            else:
                model.blocks.append(Block(kind="heading", text=text, level=level))
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
            text = _strip_inline(m.group(2))
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
        current.body.append(_strip_inline(b.group(1) if b else raw))
    return deck
