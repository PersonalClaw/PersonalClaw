import html as _html
import re
from abc import ABC, abstractmethod

try:
    import html2text as _html2text
except ImportError:
    _html2text = None  # type: ignore[assignment]


def _meta_content(html: str, *attr_patterns: str) -> str:
    """Return the ``content`` of the first <meta> tag matching any attr pattern
    (e.g. ``name=["']description["']``), order-independent of where content sits."""
    for pat in attr_patterns:
        # content before the matched attr
        m = re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*{pat}', html, re.I)
        if m:
            return _html.unescape(m.group(1)).strip()
        # content after the matched attr
        m = re.search(rf'<meta[^>]+{pat}[^>]+content=["\']([^"\']*)', html, re.I)
        if m:
            return _html.unescape(m.group(1)).strip()
    return ""


def extract_html_metadata(html: str) -> dict:
    """Pull a page's display title + description from its HTML head — preferring
    OpenGraph (og:title/og:description) over <title>/<meta name=description>.

    Pure-regex (no parser dependency); returns ``{}`` keys absent when not found.
    Used to give bookmarks a real link-card title/description instead of guessing
    from the scraped body text.
    """
    if not html:
        return {}
    out: dict = {}
    title = _meta_content(html, r'property=["\']og:title["\']')
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            title = _html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())
    if title:
        out["title"] = title[:200]
    desc = _meta_content(
        html,
        r'property=["\']og:description["\']',
        r'name=["\']description["\']',
    )
    if desc:
        out["description"] = desc[:300]
    return out


# Elements whose text is never page content, stripped wherever they appear: scripts,
# styles, site nav, sidebars, forms, inline SVG icon text, no-JS fallbacks. html2text
# strips <script>/<style> but keeps the rest — so a scrape of e.g. a GitHub repo page
# would lead with "Skip to content / Navigation Menu / Sign in / …".
_CHROME_TAGS = ("script", "style", "nav", "aside", "form", "svg", "noscript")
# Page-frame elements stripped ONLY at the page level (not inside a chosen main/article
# region): a site <header>/<footer> is chrome, but an *article's* own <header> usually
# holds its title/byline, so we keep those once we've narrowed to the content root.
_FRAME_TAGS = ("header", "footer")


def _strip_tags(html: str, tags: tuple[str, ...]) -> str:
    """Remove the named element blocks (with content) from HTML, iterating so an inner
    block revealed by stripping its wrapper is also removed."""
    pat = re.compile(r"<(" + "|".join(tags) + r")\b[^>]*>.*?</\1>", re.I | re.S)
    prev = None
    while prev != html:
        prev = html
        html = pat.sub("", html)
    return html


def strip_html_chrome(html: str) -> str:
    """Reduce HTML to its likely content before text conversion.

    If the page exposes a <main>/<article> region, narrow to it (the single biggest
    scrape-quality win for sites that wrap content in boilerplate) and strip only true
    non-content (nav/aside/form/svg/script/style) — keeping any <header>/<footer> there,
    since an article's header is usually its title/byline. With no main region, strip the
    page-frame <header>/<footer> too (they're site chrome)."""
    if not html:
        return html
    m = re.search(r"<(main|article)\b[^>]*>(.*?)</\1>", html, re.I | re.S)
    if m:
        return _strip_tags(m.group(2), _CHROME_TAGS)
    return _strip_tags(html, _CHROME_TAGS + _FRAME_TAGS)


def html_to_text(html: str) -> str:
    """Convert HTML to plain text / markdown.

    Strips site-chrome (nav/header/footer/…) first so the scraped text is the page's
    real content, not boilerplate. Uses ``html2text`` when available (full markdown,
    unwrapped lines); falls back to a minimal tag-strip otherwise. Shared by every
    connector that ingests HTML.
    """
    html = strip_html_chrome(html)
    if _html2text is not None:
        h = _html2text.HTML2Text()
        h.body_width = 0
        return h.handle(html)
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


#: Markup's tell: an element tag or a character reference. Text with neither is not HTML and
#: is not handed to an HTML parser, which would fold its line breaks into one paragraph.
_MARKUP_RE = re.compile(
    r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>"  # an element tag
    r"|&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]{1,31});"  # a character reference
)

#: A "<" that would open markup where inline HTML is rendered: before a tag name, a closing
#: tag, a comment/declaration or a processing instruction. An autolink is a link, not markup.
_TAG_OPEN_RE = re.compile(r"<(?!(?:https?://|mailto:))(?=[A-Za-z/!?])")

#: Fenced blocks and code spans, where a "<" is already text. A backslash-escaped backtick
#: opens nothing, so it never shields what follows it.
_CODE_RE = re.compile(r"(```.*?```|~~~.*?~~~|(?<!\\)`[^`\n]*`)", re.S)


def without_raw_html(markdown: str) -> str:
    """*markdown* with no raw HTML left in it: every markup-opening "<" outside code is written
    as ``&lt;``, which markdown shows as the character it is.

    For untrusted text that becomes a knowledge item's body. The item page renders a body as
    markdown with inline HTML passed through, and html2text turns an entity-encoded tag that a
    page only SHOWED (``&lt;iframe …&gt;``) back into the characters of a real one. So a
    converter's output is not safe markdown by itself, and this is the step that makes it so.
    Code is left alone: inside a code span or a fenced block a tag is already text.
    """
    parts = _CODE_RE.split(markdown or "")
    for i in range(0, len(parts), 2):  # even parts are outside code
        parts[i] = _TAG_OPEN_RE.sub("&lt;", parts[i])
    return "".join(parts)


def readable_text(value: str) -> str:
    """An untrusted body (a feed entry, a page's excerpt) as the markdown text a reader sees.

    Markup goes through :func:`html_to_text`, the one html→text conversion (the one a preview
    snippet uses too); text with no markup is kept as written. Either way no raw HTML is left
    (:func:`without_raw_html`).
    """
    text = value or ""
    if _MARKUP_RE.search(text):
        text = html_to_text(text).strip()
    return without_raw_html(text)


def plain_line(value: str) -> str:
    """An untrusted one-line field (a title) as plain text: tags dropped, character
    references decoded, whitespace collapsed. A title is shown as text everywhere, so a
    feed's ``Don&#8217;t`` or ``<b>new</b>`` would otherwise be read out as written."""
    text = value or ""
    if _MARKUP_RE.search(text):
        text = _html.unescape(re.sub(r"<[^>]*>", "", text))
    return " ".join(text.split())


class BaseConnector(ABC):
    """Base class for remote source connectors."""

    @abstractmethod
    async def fetch(self, source: dict) -> tuple[str, dict]:
        """Fetch content from source. Returns (text_content, metadata)."""
        ...

    @abstractmethod
    async def detect_changes(self, source: dict) -> bool:
        """Return True if source has changed since last sync."""
        ...

    @abstractmethod
    def validate_config(self, config: dict) -> tuple[bool, str]:
        """Validate source config. Returns (is_valid, error_message)."""
        ...

    @abstractmethod
    def source_type(self) -> str:
        """Return the source_type string (e.g., 'web_url', 'local_file')."""
        ...
