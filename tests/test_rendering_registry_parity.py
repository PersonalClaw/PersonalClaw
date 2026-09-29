"""Rendering-engine registry guards (R6 of rendering-engine-architecture.md).

The frontend ContentTypeRegistry (web/src/ui/content/) is the ONE source of
truth for how a content type renders/edits/sanitizes. Two cross-tier invariants
keep it from forking again:

1. **Kind alignment** — every artifact `kind` the registry declares MUST be in the
   backend ``ALLOWED_KINDS`` and vice-versa. The registry is FE-authoritative
   (open-decision #2); this test is the "checked against it" half, so adding a kind
   on one tier without the other fails CI instead of silently 400-ing at save time.

2. **No parallel dispatch** — no web component outside ``ui/content/`` may
   re-introduce a content-type→renderer dispatcher (the ``IFRAME_KINDS`` /
   ``EDITABLE_KINDS`` Sets this plan deleted). These are the exact drifts the rendering
   engine consolidated; this guard stops their return.

3. **One way onto the page for text the app did not write** — a model's reply, a tool's
   result, a knowledge body, an inbox message, an app's description renders through
   ``ui/Markdown.tsx``, which shows embedded HTML as text. Every OTHER way the web app turns a
   string into live markup (``dangerouslySetInnerHTML``, ``innerHTML``, a parsed document, a
   frame's ``srcdoc``, an HTML blob, a markdown or HTML library) is censused by file and count
   with the reason it is safe, so a new one fails here instead of quietly becoming a second
   renderer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.artifacts.models import ALLOWED_KINDS

_REPO = Path(__file__).resolve().parent.parent
_WEB = _REPO / "web" / "src"
_REGISTER = _WEB / "ui" / "content" / "registerBuiltins.ts"

# web is optional in some checkouts (backend-only installs); skip cleanly then.
pytestmark = pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")


def _declared_kinds() -> list[str]:
    """Every artifact kind the FE registry declares, WITH multiplicity, in file order.

    A list rather than a set because the duplicate check below needs the repeats — a
    kind claimed by two types is exactly what a set silently collapses. A type reached
    only by file extension declares no `kinds` and does not appear here at all.
    """
    text = _REGISTER.read_text(encoding="utf-8")
    kinds: list[str] = []
    for arr in re.findall(r"kinds:\s*\[([^\]]*)\]", text):
        kinds.extend(re.findall(r"'([^']+)'", arr))
    return kinds


def _registry_kinds() -> set[str]:
    """The distinct artifact kinds the FE registry declares."""
    return set(_declared_kinds())


def test_registry_kinds_match_backend_allowed_kinds():
    registry = _registry_kinds()
    assert registry, "could not parse any `kinds:` from the registry — parser drift?"
    missing_in_backend = registry - ALLOWED_KINDS
    missing_in_registry = ALLOWED_KINDS - registry
    assert not missing_in_backend, (
        f"registry declares kinds the backend ALLOWED_KINDS rejects: {sorted(missing_in_backend)}. "
        "Add them to artifacts/models.py ALLOWED_KINDS."
    )
    assert not missing_in_registry, (
        f"backend ALLOWED_KINDS has kinds the FE registry doesn't render: {sorted(missing_in_registry)}. "  # noqa: E501
        "Register them in web/src/ui/content/registerBuiltins.ts (or remove from ALLOWED_KINDS)."
    )


def test_no_kind_is_claimed_by_two_content_types():
    """One kind, one type — because `resolveContentType` is FIRST-MATCH-WINS.

    ``contentTypes.ts:209`` walks the registration list in order and returns the first
    type whose `kinds` contains the probe's kind, so a second type claiming an existing
    kind does not conflict loudly: it silently SHADOWS, and which one wins depends on
    registration order in `registerBuiltins.ts`. `registerContentType` cannot catch it
    either — it de-duplicates on type `id`, not on the kinds a type claims.

    The alignment test above cannot see this, and that is not an oversight of its own
    design: it compares SETS against `ALLOWED_KINDS`, and a set collapses the duplicate
    it would need to notice. Hence a separate check over the declarations WITH
    multiplicity.
    """
    declared = _declared_kinds()
    duplicates = sorted({k for k in declared if declared.count(k) > 1})
    assert not duplicates, (
        f"these artifact kinds are claimed by more than one content type: {duplicates}. "
        "resolveContentType returns the FIRST registered match, so the later type never "
        "renders and the shadowing is silent — give the kind to exactly one type in "
        "web/src/ui/content/registerBuiltins.ts."
    )


# The dead capability Sets the engine deleted — must never be re-declared anywhere.
_FORBIDDEN_DECL = re.compile(r"\b(IFRAME_KINDS|EDITABLE_KINDS)\b\s*=")


def _web_sources() -> list[Path]:
    return [p for p in _WEB.rglob("*.ts*") if p.suffix in {".ts", ".tsx"}]


def _code_only(text: str) -> str:
    """`text` with TS/TSX comment bodies blanked out, string literals preserved.

    🪤 The scans below look for a literal token. A bare ``in`` test counts the token where an
    author merely NAMES it — and the file that most needs to name ``dangerouslySetInnerHTML`` is
    the sanitizer's own test suite, which explains in prose that it "injects the output exactly
    as `dangerouslySetInnerHTML` does". That docstring plus one inline comment made
    ``ui/content/sanitize.test.ts`` the sole offender, so the rail reported a sanitizer-bypass
    risk in the very file proving the sanitizer works.

    A state machine rather than a regex, because both regex shortcuts are wrong here: stripping
    ``//`` to end-of-line also truncates any line holding a ``https://`` URL, which silently
    HIDES a real token later on that line (a false negative in a security rail), and stripping
    ``/* */`` first corrupts a ``"/*"`` inside a string. Tracking the three states — in-string,
    in-line-comment, in-block-comment — is the only version with neither hole.

    Comment characters are replaced with spaces rather than deleted so reported line numbers and
    offsets stay faithful to the file as the author wrote it.
    """
    out = []
    i, n = 0, len(text)
    quote = None  # the open string/template delimiter, or None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:  # an escape consumes the next char, quote or not
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def test_the_comment_stripper_hides_prose_without_hiding_code():
    """Pin `_code_only`, because the rails above are only as honest as it is.

    The two cases that fail with either regex shortcut are pinned by name: a token later on a
    line that also holds a `https://` URL (a `//`-to-end-of-line strip hides it — a FALSE
    NEGATIVE in a security rail), and a `"/*"` inside a string literal (a block-comment strip
    swallows the rest of the file). A stripper that hid real code would make every scan here
    vacuous, so the negative half matters more than the positive one.
    """
    T = "dangerouslySetInnerHTML"
    hidden = [
        "// uses dangerouslySetInnerHTML here\nconst a = 1\n",
        "/* as dangerouslySetInnerHTML does */\nconst a = 1\n",
    ]
    for src in hidden:
        assert T not in _code_only(src), f"prose was not stripped: {src!r}"

    kept = [
        "<div dangerouslySetInnerHTML={{__html: x}} />\n",
        "const u = 'https://x.y' // note\nel.dangerouslySetInnerHTML = 1\n",
        "const u = 'https://x.y'; el.dangerouslySetInnerHTML = 1\n",
        "const s = 'dangerouslySetInnerHTML'\n",
        "const s = '/*'; el.dangerouslySetInnerHTML = 1\n",
    ]
    for src in kept:
        assert T in _code_only(src), f"real code was hidden — the rail would go vacuous: {src!r}"


def test_no_resurrected_capability_sets():
    """The IFRAME_KINDS / EDITABLE_KINDS dispatch Sets stay deleted (registry owns this)."""
    offenders = []
    for p in _web_sources():
        if _FORBIDDEN_DECL.search(_code_only(p.read_text(encoding="utf-8"))):
            offenders.append(str(p.relative_to(_WEB)))
    assert not offenders, (
        "content-type capability Sets were re-introduced (the registry's edit/sandbox "
        f"capabilities replace them): {offenders}"
    )


#: The ways a string becomes live markup in the web app, each matched in CODE only
#: (`_code_only`), so an author naming one in a comment is not a site.
_MARKUP_SINKS = {
    "renderer import": re.compile(
        r"""from\s+['"](?:react-markdown|rehype-raw|remark-rehype|hast-util-raw|"""
        r"""hast-util-to-jsx-runtime|mdast-util-to-hast|micromark|marked|markdown-it|dompurify|"""
        r"""sanitize-html)['"]"""
    ),
    "markup into the page": re.compile(
        # The prop being SET (`=` in JSX, `:` in an object), so an author naming it in prose
        # that `_code_only` cannot tell from code (after a regex literal) is not a site.
        r"dangerouslySetInnerHTML\s*[=:]|\.(?:inner|outer)HTML\s*=(?!=)|insertAdjacentHTML\s*\("
        r"|createContextualFragment\s*\(|document\.write(?:ln)?\s*\("
    ),
    "parsed into a document": re.compile(r"\bparseFromString\s*\("),
    "a document for a frame or file": re.compile(
        r"""\bsrcDoc=|setAttribute\(\s*['"]srcdoc['"]|type:\s*['"]text/html"""
    ),
}

#: Every site, by file and count, and why it is safe. A new one anywhere fails
#: `test_every_markup_path_is_a_listed_one` until someone decides it belongs here; one that
#: disappears fails it too, so the census stays true.
_MARKUP_PATHS: dict[str, tuple[dict[str, int], str]] = {
    "ui/Markdown.tsx": (
        {"renderer import": 2, "markup into the page": 1},
        "THE renderer (react-markdown, and rehype-raw behind the pass that lets only "
        "attribute-free formatting tags reach it); the one inner-HTML site is highlight.js "
        "output, which escapes every character of the code it is handed",
    ),
    "pages/skills/SkillInspector.tsx": (
        {"markup into the page": 1},
        "highlight.js output for a skill file's source",
    ),
    "ui/content/renderers.tsx": (
        {"markup into the page": 2, "a document for a frame or file": 2},
        "an svg or document ARTIFACT after the fail-closed allowlist sanitizer "
        "(ui/content/sanitize.ts); html and widget artifacts in a sandboxed blob frame",
    ),
    "ui/content/sanitize.ts": (
        {"parsed into a document": 2},
        "the sanitizer's own parse into an inert document; nothing it parses is attached",
    ),
    "ui/widget/MermaidBlock.tsx": (
        {"markup into the page": 2},
        "clearing the node, then mermaid's SVG rendered with securityLevel 'strict'",
    ),
    "ui/widget/widgetSrcdoc.ts": (
        {"markup into the page": 1},
        "inside the sandboxed widget document's own script: an escaped error message",
    ),
    "ui/widget/WidgetFrame.tsx": (
        {"a document for a frame or file": 4},
        "the sandboxed widget frame (a blob, an opaque origin), its open-in-tab wrapper and "
        "its download",
    ),
    "ui/widget/ReactWidgetFrame.tsx": (
        {"a document for a frame or file": 3},
        "the sandboxed React widget frame and its open-in-tab wrapper",
    ),
    "pages/artifacts/ArtifactCard.tsx": (
        {"a document for a frame or file": 1},
        "an artifact card's sandboxed, inert preview frame",
    ),
    "pages/files/browse/FilePreviews.tsx": (
        {"a document for a frame or file": 1},
        "an HTML file's preview in a sandboxed blob frame",
    ),
    "ui/content/exporters.ts": (
        {"a document for a frame or file": 1},
        "a document export's download (a file, not a page)",
    ),
    "pages/settings/MemoryPanel.tsx": (
        {"a document for a frame or file": 1},
        "the memory graph export's download (a file, not a page)",
    ),
}


def _sink_counts(code: str) -> dict[str, int]:
    counts = {name: len(pattern.findall(code)) for name, pattern in _MARKUP_SINKS.items()}
    return {name: n for name, n in counts.items() if n}


def _app_sources() -> list[Path]:
    """The web app's own sources: tests plant these strings on purpose, and a doc is data."""
    return [p for p in _web_sources() if not re.search(r"\.(test|spec)\.tsx?$|\.doc\.ts$", p.name)]


def _markup_census() -> dict[str, dict[str, int]]:
    """Every app source's markup sites, by kind."""
    census = {}
    for p in _app_sources():
        found = _sink_counts(_code_only(p.read_text(encoding="utf-8")))
        if found:
            census[str(p.relative_to(_WEB))] = found
    return census


def test_the_markup_census_finds_the_known_paths():
    """Not vacuously green: the scan reaches web/src and sees the renderer itself."""
    census = _markup_census()
    assert len(census) >= 10, census
    assert census.get("ui/Markdown.tsx", {}).get("renderer import") == 2, census


def test_every_markup_path_is_a_listed_one():
    """Text the app did not write renders through `ui/Markdown.tsx`; nothing else turns a
    string into live markup unless it is listed here with its reason."""
    expected = {rel: sites for rel, (sites, _why) in _MARKUP_PATHS.items()}
    assert _markup_census() == expected, (
        "A string reaches live markup somewhere new (or a listed site moved or went away). "
        "Stored or remote text must render through ui/Markdown.tsx; a genuinely different path "
        "(a sandboxed frame, a sanitizer for an HTML content type) goes on _MARKUP_PATHS with "
        "its reason."
    )


def test_only_a_chat_reply_runs_widgets():
    """`widgets` is the one way a `<widget>` in rendered text becomes a sandboxed program. The
    agent's chat reply is where the model is given that contract; nothing else opts in."""
    opted_in = {}
    for p in _app_sources():
        n = len(re.findall(r"<Markdown\b[^\n>]*\swidgets\b", _code_only(p.read_text("utf-8"))))
        if n:
            opted_in[str(p.relative_to(_WEB))] = n
    assert opted_in == {"pages/ChatPage.tsx": 3}


def test_each_sink_detector_recognises_what_it_is_for():
    """The detection direction, on planted text: a detector that matched nothing would make
    the census pass with every path unlisted."""
    planted = {
        "import ReactMarkdown from 'react-markdown'": {"renderer import": 1},
        "import DOMPurify from 'dompurify'": {"renderer import": 1},
        "<div dangerouslySetInnerHTML={{ __html: x }} />": {"markup into the page": 1},
        "createElement('div', { dangerouslySetInnerHTML: { __html: x } })": {
            "markup into the page": 1
        },
        "el.innerHTML = body; el.outerHTML = body": {"markup into the page": 2},
        "el.insertAdjacentHTML('beforeend', x)": {"markup into the page": 1},
        "if (el.innerHTML === '') return": {},
        "new DOMParser().parseFromString(x, 'text/html')": {"parsed into a document": 1},
        "<iframe srcDoc={doc} />": {"a document for a frame or file": 1},
        "new Blob([doc], { type: 'text/html' })": {"a document for a frame or file": 1},
        "// the old dangerouslySetInnerHTML gap": {},
    }
    for source, expected in planted.items():
        assert _sink_counts(_code_only(source)) == expected, source
