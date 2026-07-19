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
   ``EDITABLE_KINDS`` Sets this plan deleted, or a raw ``dangerouslySetInnerHTML``
   on artifact/document content that bypasses the registry's sanitizer). These are
   the exact drifts the rendering engine consolidated; this guard stops their return.
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


def _registry_kinds() -> set[str]:
    """The artifact `kinds` the FE registry declares (the `kinds: [...]` arrays).

    Types reached only by file-extension (code/csv/image/pdf) declare no `kinds`
    and are intentionally excluded — they aren't artifact kinds.
    """
    text = _REGISTER.read_text(encoding="utf-8")
    kinds: set[str] = set()
    for arr in re.findall(r"kinds:\s*\[([^\]]*)\]", text):
        kinds.update(re.findall(r"'([^']+)'", arr))
    return kinds


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
        f"backend ALLOWED_KINDS has kinds the FE registry doesn't render: {sorted(missing_in_registry)}. "
        "Register them in web/src/ui/content/registerBuiltins.ts (or remove from ALLOWED_KINDS)."
    )


# Files allowed to contain content-type dispatch / raw HTML injection: the registry
# itself + its renderers (where the sanitizer + sandbox live).
_DISPATCH_ALLOWED = {
    "ui/content/registerBuiltins.ts",
    "ui/content/contentTypes.ts",
    "ui/content/renderers.tsx",
    "ui/content/sanitize.ts",
    "ui/content/ContentSurface.tsx",
    "ui/content/chatEmbeds.tsx",
    "ui/content/InfographicView.tsx",
    "ui/content/exporters.ts",
}

# The dead capability Sets the engine deleted — must never be re-declared anywhere.
_FORBIDDEN_DECL = re.compile(r"\b(IFRAME_KINDS|EDITABLE_KINDS)\b\s*=")


def _web_sources() -> list[Path]:
    return [p for p in _WEB.rglob("*.ts*") if p.suffix in {".ts", ".tsx"}]


def test_no_resurrected_capability_sets():
    """The IFRAME_KINDS / EDITABLE_KINDS dispatch Sets stay deleted (registry owns this)."""
    offenders = []
    for p in _web_sources():
        if _FORBIDDEN_DECL.search(p.read_text(encoding="utf-8")):
            offenders.append(str(p.relative_to(_WEB)))
    assert not offenders, (
        "content-type capability Sets were re-introduced (the registry's edit/sandbox "
        f"capabilities replace them): {offenders}"
    )


def test_no_raw_html_injection_outside_registry():
    """`dangerouslySetInnerHTML` is allowed only in the registry's renderers (where
    content is sanitized) + the markdown/code highlighters (hljs-escaped output).
    A new one elsewhere is a sanitizer-bypass risk — route it through the registry."""
    # hljs syntax-highlight output is a trusted transform (escapes its input).
    hljs_ok = {"ui/Markdown.tsx", "pages/skills/SkillInspector.tsx"}
    allowed = _DISPATCH_ALLOWED | hljs_ok
    offenders = []
    for p in _web_sources():
        rel = str(p.relative_to(_WEB))
        if rel in allowed:
            continue
        if "dangerouslySetInnerHTML" in p.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert not offenders, (
        "raw dangerouslySetInnerHTML outside the content registry (sanitizer bypass risk): "
        f"{offenders}. Render through <ContentSurface> / a registered content type instead."
    )
