"""Rendering-engine registry guards.

The frontend ContentTypeRegistry (web/src/ui/content/) is the ONE source of
truth for how a content type renders/edits/sanitizes. Two cross-tier invariants
keep it from forking again:

1. **Kind alignment** — every artifact `kind` the registry declares MUST be in the
   backend ``ALLOWED_KINDS`` and vice-versa. The registry is FE-authoritative;
   this test is the "checked against it" half, so adding a kind
   on one tier without the other fails CI instead of silently 400-ing at save time.

2. **No parallel dispatch** — no web component outside ``ui/content/`` may
   re-introduce a content-type→renderer dispatcher (the ``IFRAME_KINDS`` /
   ``EDITABLE_KINDS`` Sets that were deleted). These are the exact drifts the rendering
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

import pytest
from rendering_registry_parity_shared import _WEB, _code_only, _sink_counts

from personalclaw.artifacts.models import ALLOWED_KINDS

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
