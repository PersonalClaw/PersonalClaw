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
