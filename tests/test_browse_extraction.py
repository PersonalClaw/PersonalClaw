"""BA-1 — text-only STRUCTURAL page extraction (browse/extraction.py).

Covers the done_when parts that live in the extraction module:
  * a structured representation from fixture HTML — text ≤4000 chars, a Links DSL, a Forms DSL;
  * ElementRef STABILITY across a DOM mutation — mutate an unrelated part, re-extract, and an
    unchanged element keeps its ref (the load-bearing property).

Pure over an HTML string — no network, no home touched.
"""

from __future__ import annotations

from personalclaw.browse.extraction import (
    MAX_TEXT_CHARS,
    ElementRef,
    extract_page,
    render_forms_dsl,
    render_links_dsl,
)

_PAGE = """
<!DOCTYPE html>
<html><head><title>Fixture</title></head>
<body>
  <nav><a href="/home">Home</a><a href="/about">About</a></nav>
  <script>window.evil = 1; fetch('/beacon');</script>
  <style>.x{color:red}</style>
  <main>
    <h1>The Heading</h1>
    <p>This is the genuine article body with enough words to be treated as the
       main content of the page by a boilerplate-removing extractor.</p>
    <img src="data:image/png;base64,AAAABBBBCCCCDDDD" alt="a diagram">
    <a href="/pricing">Pricing</a>
    <a href="https://docs.example.com/intro">Documentation</a>
    <a href="#section">Fragment only</a>
    <a href="/logo.png">logo image</a>
    <a href="javascript:void(0)">js link</a>
  </main>
  <form name="login">
    <input name="email" type="email" required>
    <input name="password" type="password" required>
    <input name="remember" type="checkbox">
    <input type="submit" value="Log in">
  </form>
  <form name="search">
    <input name="q" type="text" placeholder="Search...">
    <button>Go</button>
  </form>
</body></html>
"""


def test_extract_returns_structured_representation():
    page = extract_page(_PAGE, url="https://example.com/")
    assert page.url == "https://example.com/"
    assert "genuine article body" in page.text
    assert len(page.text) <= MAX_TEXT_CHARS
    assert page.links  # Links DSL source present
    assert page.forms  # Forms DSL source present


def test_text_is_capped_at_4000_chars():
    big = "<main>" + "<p>word word word word word.</p>" * 5000 + "</main>"
    page = extract_page(big, url="https://x/")
    assert len(page.text) <= MAX_TEXT_CHARS


def test_text_body_has_no_base64_from_images():
    # An <img src="data:image/...;base64,..."> must NOT leak into the text body — it becomes
    # an [IMAGE: alt] placeholder (the §1.1 rule that keeps base64 out of context).
    page = extract_page(_PAGE, url="https://example.com/")
    assert "base64" not in page.text
    assert "[IMAGE: a diagram]" in page.text


def test_script_content_not_in_text_or_labels():
    page = extract_page(_PAGE, url="https://example.com/")
    assert "window.evil" not in page.text
    assert "fetch(" not in page.text


def test_links_filtered_and_labelled():
    page = extract_page(_PAGE, url="https://example.com/")
    targets = {e.target for e in page.links}
    labels = {e.label for e in page.links}
    assert "/pricing" in targets
    assert "https://docs.example.com/intro" in targets
    assert "Documentation" in labels
    # Filtered out: fragment-only, asset extension, javascript: scheme.
    assert not any(t.startswith("#") for t in targets)
    assert "/logo.png" not in targets
    assert not any(t.startswith("javascript:") for t in targets)


def test_link_tracking_params_stripped():
    html = '<a href="/results?q=cats&utm_source=news&ref=abc">Search cats</a>'
    page = extract_page(html)
    (link,) = page.links
    assert link.target == "/results?q=cats"  # utm_source/ref dropped, q kept


def test_long_url_rejected():
    html = f'<a href="/{"x" * 200}">too long</a>'
    page = extract_page(html)
    assert page.links == ()


def test_forms_dsl_captures_fields_and_types():
    page = extract_page(_PAGE, url="https://example.com/")
    forms = {f.name: f for f in page.forms}
    assert "login" in forms and "search" in forms
    login_fields = {e.label: e for e in forms["login"].fields}
    assert "email" in login_fields
    assert "type=email" in login_fields["email"].note
    assert "required" in login_fields["email"].note
    assert login_fields["remember"].role == "checkbox"
    assert login_fields["remember"].state == "unchecked"
    # submit button is captured as a button role
    assert any(e.role == "button" for e in forms["login"].fields)
    # <button>Go</button> inner text becomes its label
    assert any(e.label == "Go" for e in forms["search"].fields)


def test_all_refs_are_unique_and_stable_shape():
    page = extract_page(_PAGE)
    refs = [e.ref for e in page.links]
    for f in page.forms:
        refs.extend(e.ref for e in f.fields)
    assert len(refs) == len(set(refs)), "refs collided"
    assert all(len(r) == 8 for r in refs), "ref not sha1[:8]"


def test_links_dsl_render_is_ref_addressable():
    page = extract_page(_PAGE)
    dsl = render_links_dsl(page.links)
    assert dsl.startswith("## Links")
    for e in page.links:
        assert f"[{e.ref}] {e.label} → {e.target}" in dsl


def test_forms_dsl_render_shape():
    page = extract_page(_PAGE)
    dsl = render_forms_dsl(page.forms)
    assert '[form: "login"]' in dsl
    assert "## Forms" in dsl


# ── The load-bearing property: ElementRef stability across an unrelated DOM mutation ──

_BEFORE = """
<body>
  <main><p>original intro paragraph with several words here.</p></main>
  <form name="login">
    <input name="email" type="email" required>
    <input name="password" type="password" required>
    <button>Sign in</button>
  </form>
</body>
"""

# Same login form; an UNRELATED part of the page changed (new banner + a new nav link + the
# intro text rewritten). A positional-index scheme would renumber the form's fields; identity
# refs must not move.
_AFTER = """
<body>
  <div class="banner">Flash sale! 40% off today only.</div>
  <nav><a href="/deals">Deals</a></nav>
  <main><p>a completely different intro paragraph, rewritten entirely.</p></main>
  <form name="login">
    <input name="email" type="email" required>
    <input name="password" type="password" required>
    <button>Sign in</button>
  </form>
</body>
"""


def _field_refs(html: str) -> dict[str, str]:
    page = extract_page(html)
    out: dict[str, str] = {}
    for form in page.forms:
        for e in form.fields:
            out[f"{form.name}:{e.role}:{e.label}"] = e.ref
    return out


def test_element_refs_survive_unrelated_dom_mutation():
    before = _field_refs(_BEFORE)
    after = _field_refs(_AFTER)
    # Every login field present before is still present after WITH THE SAME REF.
    assert before, "fixture produced no fields"
    for key, ref in before.items():
        assert key in after, f"element {key} disappeared after mutation"
        assert after[key] == ref, f"ref for {key} changed across an unrelated mutation"


def test_ref_is_pure_identity_not_position():
    # Two forms; adding a field to the FIRST form must not change the SECOND form's field ref.
    a = '<form name="a"><input name="x" type="text"></form><form name="b"><input name="y"></form>'
    b = (
        '<form name="a"><input name="new" type="text"><input name="x" type="text"></form>'
        '<form name="b"><input name="y"></form>'
    )
    ref_a = next(e.ref for f in extract_page(a).forms if f.name == "b" for e in f.fields)
    ref_b = next(e.ref for f in extract_page(b).forms if f.name == "b" for e in f.fields)
    assert ref_a == ref_b


def test_same_ref_for_identical_element_across_calls():
    r1 = extract_page(_BEFORE)
    r2 = extract_page(_BEFORE)
    refs1 = [e.ref for f in r1.forms for e in f.fields]
    refs2 = [e.ref for f in r2.forms for e in f.fields]
    assert refs1 == refs2  # deterministic


def test_empty_html():
    page = extract_page("")
    assert page.text == ""
    assert page.links == ()
    assert page.forms == ()


def test_formless_field_grouped_under_implicit_form():
    page = extract_page('<button>Standalone</button><input name="loose" type="text">')
    (form,) = page.forms
    assert form.name == ""
    assert {e.label for e in form.fields} == {"Standalone", "loose"}


def test_elementref_is_frozen():
    e = ElementRef(ref="abc", role="link", label="x")
    try:
        e.ref = "y"  # type: ignore[misc]
    except Exception as exc:  # frozen dataclass raises FrozenInstanceError
        assert "cannot assign" in str(exc) or "frozen" in str(exc).lower()
    else:
        raise AssertionError("ElementRef should be frozen")
