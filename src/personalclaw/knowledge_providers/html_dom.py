"""A tiny, dependency-free HTML tree + CSS-subset selector (WATCHED-SOURCES §2.1/§2.2).

The web-source detectors need something ``web/extract.py`` deliberately does not provide.
That module is a READABILITY extractor: it sanitizes with ``nh3`` and returns the page's
prose. Both halves of that are wrong for item detection — nh3's prose allowlist strips
``<script>`` (where ``json_ld`` and ``json_state`` live) and drops ``class``/``id`` (which is
the entire input to ``selector_frequency`` and to a user's declarative selector config). So
structural work runs on the RAW markup, for exactly the reason ``browse/extraction.py``
documents for its own stdlib parse. Sanitization is not skipped, it is MOVED: it applies to
each extracted item's html field, where the untrusted bytes actually go (§2.2's default-on
``sanitize_html``).

Why stdlib rather than BeautifulSoup/lxml: the soul guardrail is personal scale — tens of
sources, not a scraping farm — and a new runtime dependency on every install (including the
desktop bundle) to read a handful of changelog pages is not a trade this project makes.
``html.parser`` is lenient about the malformed markup real pages ship, which is what matters
here; correctness of *rendering* is not this module's job.

The selector language is a deliberate SUBSET: type, ``*``, ``#id``, ``.class``,
``[attr]``/``[attr=value]``, descendant, child (``>``), and comma alternatives. It covers the
html2rss-shaped configs §2.2 describes; anything beyond it is refused by ``validate_spec``
rather than silently mis-matched, because a selector that quietly means something else than
the user wrote is worse than one that is rejected with a reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: Elements with no end tag — never pushed as an open parent, or every following sibling
#: would be parsed as their child and the whole tree shape would be wrong.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Raw-text elements whose contents are code/CSS, not page text. Their text is KEPT on the
#: node (``json_ld``/``json_state`` read it) but excluded from :meth:`Element.text`, so a
#: page's JavaScript can never be mistaken for an item's description.
NON_TEXT_TAGS = frozenset({"script", "style", "noscript", "template"})

_WS_RE = re.compile(r"\s+")


@dataclass
class Element:
    """One node in the parsed tree. Text nodes live in ``chunks`` beside child elements so
    document order survives — an item title split by an inline ``<em>`` must not reassemble
    out of order."""

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Element"] = field(default_factory=list)
    parent: "Element | None" = None
    #: Interleaved content in document order: ``str`` for text, ``Element`` for a child.
    chunks: list[object] = field(default_factory=list)

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(sorted((self.attrs.get("class") or "").split()))

    @property
    def text(self) -> str:
        """All descendant text, whitespace-collapsed, skipping script/style content."""
        return _WS_RE.sub(" ", "".join(self._text_parts())).strip()

    def _text_parts(self) -> list[str]:
        if self.tag in NON_TEXT_TAGS:
            return []
        out: list[str] = []
        for chunk in self.chunks:
            if isinstance(chunk, str):
                out.append(chunk)
            elif isinstance(chunk, Element):
                out.extend(chunk._text_parts())
                out.append(" ")
        return out

    @property
    def raw_text(self) -> str:
        """Text WITHOUT the script/style exclusion — the JSON payload of a data script."""
        out: list[str] = []
        for chunk in self.chunks:
            if isinstance(chunk, str):
                out.append(chunk)
            elif isinstance(chunk, Element):
                out.append(chunk.raw_text)
        return "".join(out)

    @property
    def inner_html(self) -> str:
        """The subtree re-serialized.

        Re-serialized rather than sliced out of the source: ``HTMLParser`` reports positions
        but not end offsets, and reconstructing from the tree is what makes the ``html``
        extractor's output identical for equivalent markup. Attribute order and whitespace
        are normalized; tags and attribute VALUES — the only things a downstream sanitizer or
        markdown converter reads — are preserved.
        """
        out: list[str] = []
        for chunk in self.chunks:
            if isinstance(chunk, str):
                out.append(chunk)
            elif isinstance(chunk, Element):
                out.append(chunk.outer_html)
        return "".join(out)

    @property
    def outer_html(self) -> str:
        attrs = "".join(f' {k}="{_attr_escape(v)}"' for k, v in self.attrs.items())
        if self.tag in VOID_TAGS:
            return f"<{self.tag}{attrs}>"
        return f"<{self.tag}{attrs}>{self.inner_html}</{self.tag}>"

    def iter_descendants(self):
        for child in self.children:
            yield child
            yield from child.iter_descendants()

    def find(self, tags: frozenset[str] | set[str]) -> "Element | None":
        """First descendant (document order) whose tag is in ``tags``."""
        for el in self.iter_descendants():
            if el.tag in tags:
                return el
        return None


def _attr_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


class _TreeParser(HTMLParser):
    """Build an :class:`Element` tree from markup, tolerating unclosed tags.

    An unmatched end tag closes up to its nearest matching ancestor if there is one and is
    otherwise ignored; real pages ship both, and a parser that gave up on them would fail on
    the majority of the web while a lenient one merely produces a slightly flatter tree.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element(tag="#document")
        self._stack: list[Element] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        el = Element(
            tag=tag,
            attrs={k.lower(): (v or "") for k, v in attrs},
            parent=self._stack[-1],
        )
        parent = self._stack[-1]
        parent.children.append(el)
        parent.chunks.append(el)
        if tag not in VOID_TAGS:
            self._stack.append(el)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        el = Element(
            tag=tag,
            attrs={k.lower(): (v or "") for k, v in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(el)
        self._stack[-1].chunks.append(el)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return
        # No matching open tag: stray close, ignored.

    def handle_data(self, data: str) -> None:
        self._stack[-1].chunks.append(data)


def parse_html(html: str) -> Element:
    """Parse ``html`` into a tree. Never raises — a truncated page yields a partial tree,
    because the detectors' answer for a partial page ("no items") is more useful to the
    caller than an exception it can only turn into the same thing."""
    parser = _TreeParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup degrades to a partial tree
        pass
    return parser.root


# ── the CSS subset ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Simple:
    """One compound selector: an optional type plus id/class/attribute conditions."""

    tag: str = ""
    ident: str = ""
    classes: tuple[str, ...] = ()
    attrs: tuple[tuple[str, str | None], ...] = ()

    def matches(self, el: Element) -> bool:
        if self.tag and self.tag != el.tag:
            return False
        if self.ident and el.attrs.get("id") != self.ident:
            return False
        have = set(el.classes)
        if any(c not in have for c in self.classes):
            return False
        for name, want in self.attrs:
            if name not in el.attrs:
                return False
            if want is not None and el.attrs[name] != want:
                return False
        return True


#: ``True`` = child combinator (``>``), ``False`` = descendant (whitespace).
_Step = tuple[_Simple, bool]

_SIMPLE_RE = re.compile(
    r"""
    (?P<tag>\*|[A-Za-z][\w-]*)?
    (?P<rest>(?:
        \#[\w-]+
      | \.[\w-]+
      | \[\s*[\w-]+\s*(?:=\s*(?:"[^"]*"|'[^']*'|[^\]]*))?\s*\]
    )*)
    """,
    re.VERBOSE,
)
_COND_RE = re.compile(
    r"""\#(?P<id>[\w-]+)
      | \.(?P<cls>[\w-]+)
      | \[\s*(?P<attr>[\w-]+)\s*(?:=\s*(?P<val>"[^"]*"|'[^']*'|[^\]]*))?\s*\]""",
    re.VERBOSE,
)


class SelectorError(ValueError):
    """A selector outside the supported subset. Raised so ``validate_spec`` can refuse the
    spec with a reason instead of a config that silently matches nothing."""


def _parse_simple(text: str) -> _Simple:
    m = _SIMPLE_RE.fullmatch(text)
    if not m or not text:
        raise SelectorError(f"unsupported selector component {text!r}")
    tag = m.group("tag") or ""
    ident = ""
    classes: list[str] = []
    attrs: list[tuple[str, str | None]] = []
    for cond in _COND_RE.finditer(m.group("rest") or ""):
        if cond.group("id"):
            ident = cond.group("id")
        elif cond.group("cls"):
            classes.append(cond.group("cls"))
        else:
            raw = cond.group("val")
            value = None if raw is None else raw.strip().strip("\"'")
            attrs.append((cond.group("attr").lower(), value))
    return _Simple(
        tag="" if tag == "*" else tag.lower(),
        ident=ident,
        classes=tuple(sorted(classes)),
        attrs=tuple(attrs),
    )


def parse_selector(selector: str) -> list[list[_Step]]:
    """Compile a selector into alternative step-chains. Raises :class:`SelectorError`.

    Compiled up front (and at validate time) rather than interpreted per element, so a
    malformed selector is a save-time error rather than a poll-time silence.
    """
    groups: list[list[_Step]] = []
    for alternative in (selector or "").split(","):
        parts = alternative.replace(">", " > ").split()
        if not parts:
            raise SelectorError("empty selector")
        steps: list[_Step] = []
        child_next = False
        for part in parts:
            if part == ">":
                if not steps:
                    raise SelectorError("selector may not start with '>'")
                child_next = True
                continue
            steps.append((_parse_simple(part), child_next))
            child_next = False
        if child_next:
            raise SelectorError("selector may not end with '>'")
        groups.append(steps)
    if not groups:
        raise SelectorError("empty selector")
    return groups


def _matches_chain(el: Element, steps: list[_Step]) -> bool:
    """Right-to-left match: the last step must match ``el``, earlier steps its ancestors."""
    simple, child = steps[-1]
    if not simple.matches(el):
        return False
    rest = steps[:-1]
    if not rest:
        return True
    if child:
        return el.parent is not None and _matches_chain(el.parent, rest)
    node = el.parent
    while node is not None:
        if _matches_chain(node, rest):
            return True
        node = node.parent
    return False


def select(root: Element, selector: str) -> list[Element]:
    """Every descendant of ``root`` matching ``selector``, in document order."""
    groups = parse_selector(selector)
    return [
        el for el in root.iter_descendants() if any(_matches_chain(el, steps) for steps in groups)
    ]


def select_one(root: Element, selector: str) -> Element | None:
    found = select(root, selector)
    return found[0] if found else None
