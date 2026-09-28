"""The shared web content extractor (web/extract.py).

Covers sanitization of untrusted HTML and its refusal without nh3, main-content extraction
(trafilatura when present, html2text fallback), title recovery, and graceful empty handling.
Does not hit the network — extraction is pure over an HTML string.
"""

from __future__ import annotations

import pytest

from personalclaw.web import extract as ex
from personalclaw.web.extract import ExtractedDoc, extract_main_content, sanitize_html

_PAGE = """
<!DOCTYPE html>
<html><head><title>  Real  Title </title></head>
<body>
  <nav>home about contact</nav>
  <script>alert('xss'); window.evil=1;</script>
  <style>.x{color:red}</style>
  <article>
    <h1>The Heading</h1>
    <p>This is the genuine article body with enough words to be treated as the
       main content of the page by a boilerplate-removing extractor.</p>
    <p>A second meaningful paragraph continues the article content here.</p>
  </article>
  <footer>copyright 2026</footer>
</body></html>
"""


def test_sanitize_strips_script_and_style():
    out = sanitize_html(_PAGE)
    assert "alert(" not in out
    assert "window.evil" not in out


def test_sanitize_empty():
    assert sanitize_html("") == ""


def test_extract_returns_main_content():
    doc = extract_main_content(_PAGE, url="https://example.com/post")
    assert isinstance(doc, ExtractedDoc)
    assert "genuine article body" in doc.text
    # boilerplate chrome should be gone
    assert "home about contact" not in doc.text
    # never leaks script
    assert "alert(" not in doc.text
    assert doc.char_count == len(doc.text)
    assert doc.extractor in {"trafilatura", "html2text"}


def test_extract_recovers_title():
    # A title is recovered (trafilatura may prefer the <h1> over <title>; either is a
    # real page title). The exact <title>-tag path is pinned by the fallback test.
    doc = extract_main_content(_PAGE, url="https://example.com/post")
    assert doc.title.strip() != ""


def test_extract_empty_html():
    doc = extract_main_content("")
    assert doc.text == ""
    assert doc.extractor == "raw"


def test_fallback_path_when_trafilatura_absent(monkeypatch):
    # Force the html2text fallback (trafilatura unavailable) — still extracts text,
    # still never leaks script (sanitize runs regardless).
    monkeypatch.setattr(ex, "_trafilatura", None)
    doc = extract_main_content(_PAGE, url="https://example.com/post")
    assert doc.extractor == "html2text"
    assert "genuine article body" in doc.text
    assert "alert(" not in doc.text


def test_fallback_title_from_title_tag(monkeypatch):
    monkeypatch.setattr(ex, "_trafilatura", None)
    doc = extract_main_content(
        "<html><head><title>Just A Title</title></head><body><p>hi there friend</p></body></html>"
    )
    assert doc.title == "Just A Title"


#: Markup a weaker pass than nh3 lets through: an event handler and a ``javascript:`` link.
_UNCLEAN = '<p>Body.</p><img src=x onerror="alert(1)"><a href="javascript:alert(2)">link</a>'


def test_without_nh3_the_sanitizer_refuses_and_says_why(monkeypatch):
    """Proves the sanitizer has no weaker pass to fall back on: with nh3 missing it raises a
    refusal that names nh3 and the fix, and never returns the markup."""
    monkeypatch.setattr(ex, "_nh3", None)

    with pytest.raises(RuntimeError, match="nh3") as refused:
        sanitize_html(_UNCLEAN)

    assert isinstance(refused.value, ex.SanitizerUnavailable)
    assert "Reinstall PersonalClaw" in str(refused.value)


@pytest.mark.parametrize(
    "entry", ["sanitize_markup", "extract_main_content", "document_from_html", "report_text"]
)
def test_without_nh3_no_way_into_the_sanitizer_hands_the_markup_on(monkeypatch, entry):
    """Proves none of the sanitizer's callers has a fallback of its own: with nh3 missing, each
    raises the refusal instead of returning the markup, or text made from it."""
    from personalclaw.documents.from_markup import document_from_html
    from personalclaw.knowledge.reports import _text as report_text
    from personalclaw.knowledge_providers.web_source import sanitize_markup

    monkeypatch.setattr(ex, "_nh3", None)
    page = f"<html><body><article>{_UNCLEAN}</article></body></html>"
    call = {
        "sanitize_markup": lambda: sanitize_markup(_UNCLEAN),
        "extract_main_content": lambda: extract_main_content(page, url="https://example.com/p"),
        "document_from_html": lambda: document_from_html(_UNCLEAN),
        "report_text": lambda: report_text(_UNCLEAN),
    }[entry]

    with pytest.raises(RuntimeError, match="nh3") as refused:
        call()

    assert isinstance(refused.value, ex.SanitizerUnavailable)


# ── meta-refresh redirect stubs (#265) ──────────────────────────────────────────
#
# A docs site that moves a page answers 200 OK with a body whose only content is a meta
# refresh. That is a redirect the HTTP layer never sees, so a fetcher reading only status
# codes stored the ~167-byte stub as the article. `meta_refresh_target` is the shared parser
# that finds the destination; following it is the connector's job (and must re-enter the
# egress guard, which is why this function only ever RETURNS a URL).

_BASE = "https://openzfs.github.io/openzfs-docs/Basic%20Concepts/RAIDZ.html"

#: The exact stub measured on the real page in #265.
_REAL_STUB = (
    '<meta http-equiv="refresh" content="0; url=Pool Structure/RAIDZ.html"/>\n\n'
    "You should have been redirected.\n\n"
    "[If not, click here to continue.](Pool Structure/RAIDZ.html)"
)


def test_meta_refresh_resolves_the_real_relative_target():
    """The measured failing page: a RELATIVE target, meaningless unless resolved against
    the URL it was fetched from. This is the case the bug report called out."""
    assert (
        ex.meta_refresh_target(_REAL_STUB, url=_BASE)
        == "https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Pool Structure/RAIDZ.html"
    )


def test_meta_refresh_accepts_either_attribute_order_and_quote_style():
    assert (
        ex.meta_refresh_target('<meta content="0;url=/a.html" http-equiv="refresh">', url=_BASE)
        == "https://openzfs.github.io/a.html"
    )
    assert (
        ex.meta_refresh_target(
            "<meta http-equiv='REFRESH' content='1;URL=b.html'>", url="https://x.test/dir/page.html"
        )
        == "https://x.test/dir/b.html"
    )


def test_meta_refresh_ignores_a_delay_that_means_read_this_page_first():
    """Above MAX_META_REFRESH_DELAY the page is meant to be SEEN before it moves, so its
    content is real. Following would discard what the user asked to save."""
    assert (
        ex.meta_refresh_target('<meta http-equiv="refresh" content="30; url=late.html">', url=_BASE)
        == ""
    )


def test_meta_refresh_ignores_a_self_refresh_with_no_target():
    assert ex.meta_refresh_target('<meta http-equiv="refresh" content="0">', url=_BASE) == ""


def test_meta_refresh_refuses_non_http_schemes():
    """`file:`/`javascript:` are narrowed here so they never reach the fetcher. This is an
    early filter, NOT the security boundary — the egress guard is."""
    for target in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x"):
        html = f'<meta http-equiv="refresh" content="0; url={target}">'
        assert ex.meta_refresh_target(html, url=_BASE) == "", target


def test_meta_refresh_returns_a_private_target_for_the_guard_to_refuse():
    """Deliberate: the parser does NOT judge reachability. It hands the URL back so the
    caller re-enters `net.fetch`, which re-resolves and re-pins it. A parser that silently
    dropped private targets would be a second, weaker copy of that guard — and callers
    would then believe the check had already happened."""
    html = '<meta http-equiv="refresh" content="0; url=http://169.254.169.254/latest/meta-data/">'
    assert ex.meta_refresh_target(html, url=_BASE) == "http://169.254.169.254/latest/meta-data/"


def test_meta_refresh_absent_on_an_ordinary_page():
    assert ex.meta_refresh_target(_PAGE, url=_BASE) == ""
    assert ex.meta_refresh_target('<meta charset="utf-8">', url=_BASE) == ""
    assert ex.meta_refresh_target("", url=_BASE) == ""
