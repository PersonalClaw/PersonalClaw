"""Shared HTML/content extraction core — boilerplate-removing main-content extraction.

ONE extractor for both the ``web_fetch`` tool and the knowledge web-url connector
(no two forks). The pipeline:

  ① sanitize untrusted HTML (nh3) — strip scripts/styles/event handlers before parsing.
  ② extract the page's MAIN content as markdown (trafilatura — ~0.91 F1 vs html2text's
     ~0.66; falls back to chrome-stripped html2text when trafilatura is absent).
  ③ recover a title (trafilatura metadata → <title>).

Content fetched from the web is untrusted: sanitization runs FIRST so a malicious page
can't smuggle script/markup through the extractor. Everything degrades gracefully when
an optional dependency is missing, so extraction never hard-fails.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import trafilatura as _trafilatura
except ImportError:
    _trafilatura = None  # type: ignore[assignment]

try:
    import nh3 as _nh3
except ImportError:
    _nh3 = None  # type: ignore[assignment]


@dataclass
class ExtractedDoc:
    """The result of extracting a fetched document."""

    text: str           # main content as markdown
    title: str = ""
    char_count: int = 0
    extractor: str = ""  # which path produced it ("trafilatura" | "html2text" | "raw")


def sanitize_html(html: str) -> str:
    """Strip scripts/styles/dangerous markup from untrusted HTML before extraction.

    nh3 (ammonia) drops ``<script>``/``<style>``/event handlers and unsafe URLs. When
    nh3 is unavailable, a minimal regex strips the two highest-risk tags so we never
    feed raw script into a downstream parser.
    """
    if not html:
        return ""
    if _nh3 is not None:
        return _nh3.clean(html)
    # Minimal fallback: drop script/style blocks (content + tags).
    out = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
    return out


def extract_main_content(html: str, *, url: str = "") -> ExtractedDoc:
    """Extract the main readable content of an HTML page as markdown.

    Sanitizes first (untrusted input), then uses trafilatura for boilerplate-free
    main-content extraction, falling back to chrome-stripped html2text. ``url`` is
    passed to trafilatura to improve link resolution + metadata when available.
    """
    if not html:
        return ExtractedDoc(text="", title="", char_count=0, extractor="raw")

    clean = sanitize_html(html)
    # Title is recovered from the ORIGINAL html: trafilatura reads <head> metadata, and
    # the <title> fallback regex needs the tag the sanitizer strips out of `clean`.
    title = _title(html, url)

    if _trafilatura is not None:
        try:
            text = _trafilatura.extract(
                clean, output_format="markdown", url=url or None,
                include_links=True, include_tables=True, with_metadata=False,
            )
        except Exception:
            logger.debug("trafilatura extract failed; falling back", exc_info=True)
            text = None
        if text:
            return ExtractedDoc(text=text.strip(), title=title,
                                char_count=len(text.strip()), extractor="trafilatura")

    # Fallback: the connector's existing chrome-stripped html2text path.
    from personalclaw.knowledge.connectors.base import html_to_text
    text = html_to_text(clean)
    return ExtractedDoc(text=text.strip(), title=title,
                        char_count=len(text.strip()), extractor="html2text")


def _title(html: str, url: str) -> str:
    """Best-effort page title: trafilatura metadata → <title> tag. Reads the ORIGINAL
    (pre-sanitize) html so the <title> in <head> is still present."""
    if _trafilatura is not None:
        try:
            md = _trafilatura.extract_metadata(html, default_url=url or None)
            if md and getattr(md, "title", None):
                return str(md.title).strip()
        except Exception:
            logger.debug("trafilatura metadata failed", exc_info=True)
    m = re.search(r"<title\b[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""
