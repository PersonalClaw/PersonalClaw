"""The source-tree checks split from
``tests/test_rendering_registry_parity.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import source_index
from rendering_registry_parity_shared import _WEB, _code_only, _sink_counts

# web is optional in some checkouts (backend-only installs); skip cleanly then.
pytestmark = pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")


# The dead capability Sets the engine deleted — must never be re-declared anywhere.
_FORBIDDEN_DECL = re.compile(r"\b(IFRAME_KINDS|EDITABLE_KINDS)\b\s*=")


def _web_sources() -> list[Path]:
    return [p for p in source_index.web_files() if p.suffix in {".ts", ".tsx"}]


def test_no_resurrected_capability_sets():
    """The IFRAME_KINDS / EDITABLE_KINDS dispatch Sets stay deleted (registry owns this)."""
    offenders = []
    for p in _web_sources():
        if _FORBIDDEN_DECL.search(_code_only(source_index.text(p))):
            offenders.append(str(p.relative_to(_WEB)))
    assert not offenders, (
        "content-type capability Sets were re-introduced (the registry's edit/sandbox "
        f"capabilities replace them): {offenders}"
    )


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


def _app_sources() -> list[Path]:
    """The web app's own sources: tests plant these strings on purpose, and a doc is data."""
    return [p for p in _web_sources() if not re.search(r"\.(test|spec)\.tsx?$|\.doc\.ts$", p.name)]


def _markup_census() -> dict[str, dict[str, int]]:
    """Every app source's markup sites, by kind."""
    census = {}
    for p in _app_sources():
        found = _sink_counts(_code_only(source_index.text(p)))
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
        n = len(re.findall(r"<Markdown\b[^\n>]*\swidgets\b", _code_only(source_index.text(p))))
        if n:
            opted_in[str(p.relative_to(_WEB))] = n
    # The reply in the chat's transcript, and the history page's peek at a chat (its saved replies
    # and the one streaming in): the same three sites since the transcript left ChatPage.tsx.
    assert opted_in == {
        "pages/ChatPage.tsx": 2,
        "pages/chat/transcript/AssistantSegments.tsx": 1,
    }
