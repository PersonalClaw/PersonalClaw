"""WS5b — the shared web content extractor (web/extract.py).

Covers sanitization of untrusted HTML, main-content extraction (trafilatura when
present, html2text fallback), title recovery, and graceful empty handling. Does not
hit the network — extraction is pure over an HTML string.
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
    doc = extract_main_content("<html><head><title>Just A Title</title></head><body><p>hi there friend</p></body></html>")
    assert doc.title == "Just A Title"


def test_sanitize_fallback_without_nh3(monkeypatch):
    monkeypatch.setattr(ex, "_nh3", None)
    out = sanitize_html("<p>ok</p><script>bad()</script>")
    assert "bad()" not in out
    assert "ok" in out
