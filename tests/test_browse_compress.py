"""BA-1 — context compression (browse/compress.py).

Covers done_when part 4: a 100K-token DOM enters context as <1K tokens + a screenshot PATH,
with NO base64 in any rendered prompt (the load-bearing regression). Token counting uses the
SAME estimator (learning.surfacing.count_tokens) the outline budgets against.
"""

from __future__ import annotations

import pytest

from personalclaw.browse.compress import (
    DEFAULT_MAX_TOKENS,
    PageOutline,
    assert_no_base64,
    compress_page,
)
from personalclaw.browse.extraction import extract_page
from personalclaw.learning.surfacing import count_tokens


def _huge_dom() -> str:
    """A ~100K-token DOM: a big screenshot-bearing <img>, thousands of prose paragraphs, a
    forest of links, and a login form — the shape a real heavy SPA snapshot has."""
    base64_blob = "iVBORw0KGgoAAAANSUhEUg" + ("A" * 200_000)  # ~200KB base64 screenshot
    paras = "".join(
        f"<p>Paragraph {i} with a fair number of words to bulk out the DOM content here.</p>"
        for i in range(4000)
    )
    links = "".join(f'<a href="/item/{i}?q=x">Item number {i}</a>' for i in range(2000))
    return f"""
    <html><body>
      <img src="data:image/png;base64,{base64_blob}" alt="page screenshot">
      <main>{paras}</main>
      <nav>{links}</nav>
      <form name="login">
        <input name="email" type="email" required>
        <input name="password" type="password" required>
        <input type="submit" value="Log in">
      </form>
    </body></html>
    """


def test_huge_dom_compresses_under_1k_tokens():
    html = _huge_dom()
    # Sanity: the raw DOM really is enormous (well over the target we compress to).
    assert count_tokens(html) > 50_000
    page = extract_page(html, url="https://example.com/")
    outline = compress_page(page, screenshot_path="/runs/abc/step1.png")
    rendered = outline.render()
    assert count_tokens(rendered) < 1000, count_tokens(rendered)


def test_no_base64_in_rendered_outline():
    html = _huge_dom()
    outline = compress_page(extract_page(html, url="https://x/"), screenshot_path="/runs/a/s.png")
    rendered = outline.render()
    assert "base64" not in rendered
    assert "data:image" not in rendered
    # The guard does not raise on clean output.
    assert_no_base64(rendered)


def test_screenshot_referenced_by_path_not_inlined():
    outline = compress_page(extract_page("<p>hi</p>"), screenshot_path="/runs/x/step3.png")
    rendered = outline.render()
    assert "[SCREENSHOT: /runs/x/step3.png]" in rendered
    assert outline.screenshot_path == "/runs/x/step3.png"


def test_assert_no_base64_raises_on_inline_blob():
    poisoned = "# page\n\n[SCREENSHOT: data:image/png;base64,iVBORw0KGgo...]"
    with pytest.raises(ValueError, match="base64"):
        assert_no_base64(poisoned)


def test_assert_no_base64_case_insensitive():
    with pytest.raises(ValueError):
        assert_no_base64("DATA:IMAGE/PNG;BASE64,AAAA")


def test_interactive_elements_preserved_when_text_trimmed():
    # Even when prose is trimmed to fit the budget, the refs an agent must act on survive.
    html = _huge_dom()
    page = extract_page(html, url="https://x/")
    outline = compress_page(page, screenshot_path="/r/s.png", max_tokens=400)
    assert count_tokens(outline.render()) < 500
    # login form fields are still addressable
    labels = {e.label for e in outline.elements}
    assert "email" in labels and "password" in labels


def test_small_page_keeps_full_text():
    html = "<main><p>short and sweet page body.</p></main>"
    outline = compress_page(extract_page(html, url="https://x/"))
    assert "short and sweet" in outline.render()


def test_outline_render_has_no_screenshot_line_when_absent():
    outline = compress_page(extract_page("<p>hi there friends</p>"))
    assert "SCREENSHOT" not in outline.render()


def test_default_budget_constant_is_sane():
    assert 0 < DEFAULT_MAX_TOKENS < 1000


def test_pageoutline_is_frozen():
    o = PageOutline(url="u", text="t", elements=())
    with pytest.raises(Exception):
        o.text = "x"  # type: ignore[misc]
