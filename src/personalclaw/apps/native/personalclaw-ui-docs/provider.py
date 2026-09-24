"""UI-docs tool provider — ``ui_search`` / ``ui_get`` / ``ui_list`` (Platform-Legibility §5).

This is a BUNDLED app that owns its own provider code (the native capability contract,
APE-5 — see ``apps/native_contract.py`` and ``docs/architecture/app-platform.md``). It
imports core only through ``personalclaw.sdk.*``, exactly like an installed app, and its
``app.json`` points ``provider.implementation`` at ``provider:create_provider`` — a
bundle-relative module rather than a core dotted path. Growing this capability (the
``ui_list`` tool below was added this way) touches nothing outside this directory.

Exposes the ``web/src/ui`` design-system kit as documentation-as-data an
app-building agent can query, so it reaches for a shipped primitive (Button,
SidePanel, HeaderActions…) instead of hand-rolling a ``<button>`` or a drawer.

The data is the ``ui-docs.json`` the web build emits (``scripts/buildUiDocs.mjs``):
each component's authored keywords/description/per-prop docs/best-practices/anatomy
fused with prop ``type``/``required`` derived from the TypeScript source, plus the
design-token registry. This provider only READS that artifact.

Three tools, deliberately — not one per component:
* ``ui_search(query)`` — a keyword index over component names/keywords/descriptions
  and design-token names/labels; returns brief hits with a follow-up hint.
* ``ui_get(name, section?)`` — the full doc for one component (or a design token /
  the token catalog), optionally narrowed to a section.
* ``ui_list(kind?)`` — the whole catalog by name. ``ui_search`` requires a query, so
  without this an agent has to GUESS a keyword to discover that a primitive exists at
  all; the kit is small enough to enumerate, and the enumeration is the cheapest way to
  stop a hand-rolled component.

``list_tools`` is STATIC — the definitions exist independent of whether
``ui-docs.json`` has been built — so the offline manifest/drift harness sees them.
The JSON is read LAZILY in ``invoke`` (off the ``static/dist`` symlink, falling back
to ``web/dist``); if it isn't built yet, the tools return a clear FIX pointing at the
web build rather than vanishing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.sdk.tool import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Keep brief search results legible + cheap: cap hits and per-hit prose.
_DEFAULT_SEARCH_LIMIT = 8
_MAX_SEARCH_LIMIT = 25
_BRIEF_DESC_CHARS = 160


class UiDocsToolProvider(ToolProvider):
    """Serve the ``web/src/ui`` kit docs as ``ui_search`` / ``ui_get``."""

    @property
    def name(self) -> str:
        return "personalclaw-ui-docs"

    @property
    def display_name(self) -> str:
        return "PersonalClaw UI Docs"

    async def list_tools(self) -> list[ToolDefinition]:
        # STATIC — independent of ui-docs.json existing, so the offline manifest
        # drift harness (bare process, no web build) still sees both tools.
        return [
            ToolDefinition(
                name="ui_search",
                description=(
                    "Search the web/src/ui design-system kit (components + design "
                    "tokens) by keyword. Returns brief hits — name, kind, one-line "
                    "description — so you can find the right primitive to reach for "
                    "instead of hand-rolling markup. Follow up with ui_get(name) for "
                    "the full props + best-practices of any hit."
                ),
                provider=self.name,
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Search terms, e.g. 'button', 'side panel', 'text "
                                "input', or a token like 'primary color'."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"Max hits to return (default {_DEFAULT_SEARCH_LIMIT}, "
                                f"cap {_MAX_SEARCH_LIMIT})."
                            ),
                        },
                    },
                    "required": ["query"],
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="ui_get",
                description=(
                    "Get the full documentation for one ui/ component (props with "
                    "types + whether required, best-practice Do/Don'ts, and the "
                    "anatomy), or for a design token, or the whole token catalog "
                    "(name='tokens'). Optionally narrow to one section."
                ),
                provider=self.name,
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": (
                                "Component name (e.g. 'Button', 'SidePanel'), a design "
                                "token var (e.g. '--color-primary'), or 'tokens' for "
                                "the full token catalog."
                            ),
                        },
                        "section": {
                            "type": "string",
                            "description": (
                                "Optional: restrict the component doc to one of "
                                "'props', 'bestPractices', 'anatomy', or 'description'."
                            ),
                        },
                    },
                    "required": ["name"],
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="ui_list",
                description=(
                    "List the whole ui/ design-system catalog by name — every component "
                    "(with a one-line description) and/or every design token. Use this "
                    "first when you don't yet know what the kit contains: ui_search "
                    "needs a query, so listing is what tells you a primitive exists. "
                    "Follow up with ui_get(name) for full props + best practices."
                ),
                provider=self.name,
                parameters={
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["components", "tokens", "all"],
                            "description": (
                                "What to list: 'components' (default), 'tokens', or "
                                "'all' for both."
                            ),
                        },
                    },
                },
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        data = _load_ui_docs()
        if data is None:
            return ToolResult(
                success=False,
                error=(
                    "WHAT: The UI-docs data (ui-docs.json) is not built.\n"
                    "WHY: The web frontend hasn't been built in this checkout, so the "
                    "design-system docs artifact doesn't exist yet.\n"
                    "FIX: Build the web app from the repo root (`make web-build`, or "
                    "`npm run build --workspace web`); the build emits web/dist/"
                    "ui-docs.json, which this tool reads."
                ),
                recovery_hints=[
                    "Run `make web-build` from the repo root, then retry.",
                ],
            )
        try:
            if tool_name == "ui_search":
                return _ui_search(data, arguments)
            if tool_name == "ui_get":
                return _ui_get(data, arguments)
            if tool_name == "ui_list":
                return _ui_list(data, arguments)
        except Exception as exc:  # noqa: BLE001 - surface any lookup error to the model
            logger.debug("ui-docs tool %s failed: %s", tool_name, exc, exc_info=True)
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=False, error=f"Unknown tool: {tool_name}")


def create_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Manifest factory for the ``personalclaw-ui-docs`` tool surface.

    Named ``create_provider`` because ``app.json`` resolves it as
    ``provider:create_provider`` — the same entry-point shape every installed app uses.
    """
    return UiDocsToolProvider()


# ── data loading ────────────────────────────────────────────────────────────


# This bundle ships INSIDE the distribution (``<personalclaw>/apps/native/<name>/``), so
# the packaged asset it reads is a fixed number of levels up from its own file:
#   parents[0] this bundle · [1] native · [2] apps · [3] <personalclaw> · [4] src · [5] repo
# Reading a packaged sibling by path is the one thing a bundled app may do that an
# installed app cannot (it is in the same distribution); it is NOT an import, so the
# SDK-only boundary is intact. ``test_native_capability_contract`` pins _dist_dir()
# against the dir the dashboard actually serves, so a layout move reds the build here
# instead of silently returning "not built" at runtime.
_BUNDLE_DIR = Path(__file__).resolve().parent


def _dist_dir() -> Path:
    """The served dist dir — ``<personalclaw>/static/dist`` (a symlink to web/dist)."""
    return _BUNDLE_DIR.parents[2] / "static" / "dist"


def _ui_docs_path() -> Path | None:
    """Locate ``ui-docs.json``: the served static/dist first, else repo web/dist."""
    served = _dist_dir() / "ui-docs.json"
    if served.is_file():
        return served
    # Fallback: the source tree's web/dist (only exists in a dev checkout, where the repo
    # root is two levels above the package; an installed wheel simply has no such file).
    parents = _BUNDLE_DIR.parents
    if len(parents) > 4:
        repo_web = parents[4] / "web" / "dist" / "ui-docs.json"
        if repo_web.is_file():
            return repo_web
    return None


def _load_ui_docs() -> dict[str, Any] | None:
    """Read + parse ui-docs.json lazily, or None if it hasn't been built."""
    path = _ui_docs_path()
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read ui-docs.json at %s: %s", path, exc)
        return None


# ── ui_search ─────────────────────────────────────────────────────────────


def _ui_search(data: dict[str, Any], args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip().lower()
    if not query:
        return ToolResult(success=False, error="ui_search requires a non-empty 'query'.")
    limit = args.get("limit")
    try:
        limit = int(limit) if limit is not None else _DEFAULT_SEARCH_LIMIT
    except (TypeError, ValueError):
        limit = _DEFAULT_SEARCH_LIMIT
    limit = max(1, min(_MAX_SEARCH_LIMIT, limit))

    terms = [t for t in query.replace("-", " ").split() if t]
    hits: list[tuple[int, dict[str, Any]]] = []

    for comp in data.get("components", []):
        score = _score_component(comp, terms)
        if score > 0:
            hits.append((score, {"kind": "component", "comp": comp}))

    for tok in data.get("tokens", []):
        score = _score_token(tok, terms)
        if score > 0:
            hits.append((score, {"kind": "token", "tok": tok}))

    hits.sort(key=lambda h: (-h[0], _hit_name(h[1])))
    top = hits[:limit]

    if not top:
        return ToolResult(
            success=True,
            output=(
                f"No ui/ components or design tokens matched {query!r}. Try a broader "
                "term (e.g. 'button', 'input', 'panel', 'color')."
            ),
        )

    lines: list[str] = [f"{len(top)} match(es) for {query!r} (of {len(hits)} total):", ""]
    for _score, hit in top:
        if hit["kind"] == "component":
            comp = hit["comp"]
            desc = _brief(comp.get("description", ""))
            lines.append(f"• {comp['name']} — component ({comp.get('source', '')})")
            if desc:
                lines.append(f"    {desc}")
            lines.append(f"    → ui_get('{comp['name']}') for full props + best practices")
        else:
            tok = hit["tok"]
            lines.append(
                f"• {tok.get('varName', '?')} — token · {tok.get('label', '')} "
                f"[{tok.get('group', '')}/{tok.get('kind', '')}]"
            )
            lines.append(f"    → ui_get('{tok.get('varName', '')}') for the full token spec")
    return ToolResult(success=True, output="\n".join(lines))


def _score_component(comp: dict[str, Any], terms: list[str]) -> int:
    name = str(comp.get("name", "")).lower()
    keywords = [str(k).lower() for k in comp.get("keywords", [])]
    desc = str(comp.get("description", "")).lower()
    score = 0
    for term in terms:
        if term == name:
            score += 10
        elif term in name:
            score += 5
        if term in keywords:
            score += 4
        elif any(term in k for k in keywords):
            score += 2
        if term in desc:
            score += 1
    return score


def _score_token(tok: dict[str, Any], terms: list[str]) -> int:
    var = str(tok.get("varName", "")).lower()
    label = str(tok.get("label", "")).lower()
    group = str(tok.get("group", "")).lower()
    score = 0
    for term in terms:
        if term in var:
            score += 4
        if term in label:
            score += 3
        if term in group:
            score += 1
    return score


def _hit_name(hit: dict[str, Any]) -> str:
    if hit["kind"] == "component":
        return str(hit["comp"].get("name", ""))
    return str(hit["tok"].get("varName", ""))


def _brief(text: str) -> str:
    text = " ".join(str(text).split())
    if len(text) <= _BRIEF_DESC_CHARS:
        return text
    return text[: _BRIEF_DESC_CHARS - 1].rstrip() + "…"


# ── ui_list ───────────────────────────────────────────────────────────────
#
# The capability this bundle GAINED after it started owning its own provider code
# (APE-5): a real provider method reachable through the ordinary tool dispatch path,
# added with no edit to any core module that implements, resolves or dispatches it.


def _ui_list(data: dict[str, Any], args: dict[str, Any]) -> ToolResult:
    """Enumerate the kit. ``ui_search`` needs a query; discovery needs a list."""
    kind = str(args.get("kind", "") or "components").strip().lower()
    if kind not in {"components", "tokens", "all"}:
        return ToolResult(
            success=False,
            error=f"ui_list: unknown kind {kind!r} — use 'components', 'tokens', or 'all'.",
        )

    components = data.get("components", [])
    tokens = data.get("tokens", [])
    out: list[str] = []

    if kind in {"components", "all"}:
        out.append(f"# Components ({len(components)})")
        out.append("")
        for comp in sorted(components, key=lambda c: str(c.get("name", ""))):
            desc = _brief(comp.get("description", ""))
            src = comp.get("source", "")
            head = f"- {comp.get('name', '?')}" + (f"  (web/src/ui/{src})" if src else "")
            out.append(head)
            if desc:
                out.append(f"    {desc}")
        out.append("")

    if kind in {"tokens", "all"}:
        by_group: dict[str, list[dict[str, Any]]] = {}
        for tok in tokens:
            by_group.setdefault(str(tok.get("group", "")), []).append(tok)
        out.append(f"# Design tokens ({len(tokens)})")
        out.append("")
        for group in sorted(by_group):
            names = ", ".join(
                str(t.get("varName", "")) for t in sorted(by_group[group], key=_token_var)
            )
            out.append(f"- {group}: {names}")
        out.append("")

    out.append("Call ui_get(name) for a component's props + best practices, or ui_get('--var').")
    return ToolResult(success=True, output="\n".join(out))


def _token_var(tok: dict[str, Any]) -> str:
    return str(tok.get("varName", ""))


# ── ui_get ────────────────────────────────────────────────────────────────


def _ui_get(data: dict[str, Any], args: dict[str, Any]) -> ToolResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(success=False, error="ui_get requires a 'name'.")
    section = str(args.get("section", "")).strip().lower() or None

    if name.lower() == "tokens":
        return ToolResult(success=True, output=_render_token_catalog(data))

    # Design token by varName.
    if name.startswith("--"):
        for tok in data.get("tokens", []):
            if str(tok.get("varName", "")) == name:
                return ToolResult(success=True, output=_render_token(tok))
        return ToolResult(
            success=False,
            error=f"No design token named {name!r}. Use ui_search or ui_get('tokens').",
        )

    # Component by name (case-insensitive).
    comp = next(
        (c for c in data.get("components", []) if str(c.get("name", "")).lower() == name.lower()),
        None,
    )
    if comp is None:
        return ToolResult(
            success=False,
            error=(
                f"No ui/ component named {name!r}. Use ui_search('{name}') to find the "
                "right primitive, or ui_get('tokens') for design tokens."
            ),
        )
    return ToolResult(success=True, output=_render_component(comp, section))


def _render_component(comp: dict[str, Any], section: str | None) -> str:
    name = comp.get("name", "")
    out: list[str] = []
    show_all = section is None

    if show_all:
        src = comp.get("source", "")
        out.append(f"# {name}  (web/src/ui/{src})" if src else f"# {name}")
        keywords = comp.get("keywords", [])
        if keywords:
            out.append(f"keywords: {', '.join(keywords)}")
        out.append("")

    if show_all or section == "description":
        if comp.get("description"):
            out.append(comp["description"])
            out.append("")

    if show_all or section == "props":
        props = comp.get("props", [])
        out.append("## Props")
        if not props:
            out.append("(no props — reads its own state/context)")
        for p in props:
            req = "required" if p.get("required") else "optional"
            typ = p.get("type", "")
            head = f"- {p['name']}: {typ}  ({req})" if typ else f"- {p['name']}  ({req})"
            out.append(head)
            if p.get("description"):
                out.append(f"    {p['description']}")
        out.append("")

    if show_all or section == "bestpractices":
        bps = comp.get("bestPractices", [])
        if bps:
            out.append("## Best practices")
            for bp in bps:
                mark = "Do" if bp.get("guidance") else "Don't"
                out.append(f"- {mark}: {bp.get('description', '')}")
            out.append("")

    if show_all or section == "anatomy":
        anat = comp.get("anatomy", [])
        if anat:
            out.append("## Anatomy")
            for part in anat:
                out.append(f"- {part}")
            out.append("")

    return "\n".join(out).rstrip() + "\n"


def _render_token(tok: dict[str, Any]) -> str:
    kind = tok.get("kind", "")
    lines = [
        f"# {tok.get('varName', '')}  ({tok.get('label', '')})",
        f"kind: {kind} · group: {tok.get('group', '')}",
    ]
    if kind == "color":
        lines.append(f"default: dark {tok.get('dark', '')} / light {tok.get('light', '')}")
    elif kind == "scalar":
        unit = tok.get("unit", "") or ""
        lines.append(
            f"default: {tok.get('value', '')}{unit} "
            f"(min {tok.get('min', '')}, max {tok.get('max', '')}, step {tok.get('step', '')})"
        )
    elif kind == "select":
        lines.append(
            f"default: {tok.get('value', '')} · options: {', '.join(tok.get('options', []))}"
        )
    return "\n".join(lines) + "\n"


def _render_token_catalog(data: dict[str, Any]) -> str:
    tokens = data.get("tokens", [])
    by_group: dict[str, list[dict[str, Any]]] = {}
    for tok in tokens:
        by_group.setdefault(str(tok.get("group", "")), []).append(tok)
    out = [f"# Design tokens ({len(tokens)} total)", ""]
    for group in sorted(by_group):
        out.append(f"## {group}")
        for tok in by_group[group]:
            out.append(
                f"- {tok.get('varName', '')} — {tok.get('label', '')} ({tok.get('kind', '')})"
            )
        out.append("")
    out.append("Call ui_get('--some-var') for a token's defaults/range.")
    return "\n".join(out) + "\n"
