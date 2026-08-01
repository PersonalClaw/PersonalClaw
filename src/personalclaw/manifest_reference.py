"""Build-time offline API/tool reference (PLATFORM-LEGIBILITY §3.1).

One source, two renderings — the CLI-as-truth rule. The gateway serves the live
manifest at ``GET /api/manifest`` (walking the running aiohttp route table); this
module renders the SAME :func:`personalclaw.manifest.build_manifest` output as a
set of offline markdown files shipped in the distribution, so an agent driving
PersonalClaw from outside a running gateway (an external Claude Code session, a
code loop working on a contributed app, a subagent) reads exact signatures
instead of guessing them — the dominant failure Quarkdown measured.

**Generated, never hand-written.** :func:`render_reference` returns
``{filename: markdown}`` deterministically (sorted, no timestamps), and the
checked-in copy under :mod:`personalclaw.reference` is byte-compared against a
fresh render by ``tests/test_agent_reference.py`` — a tool or route added without
its ``TOOL_META`` / route entry drifts the reference and reddens the build, the
same drift discipline as the live manifest. Regenerate with
``python -m personalclaw.manifest_reference``.

**Routes resolve from the AST, not a running app** (the design rule
:mod:`personalclaw.manifest` states): booting the dashboard has security-critical
startup side effects (extension load, binding migration), so route paths +
handler docstrings are read statically from ``dashboard/*.py`` — the house
route-handler-guard precedent, extended to carry each handler's docstring
summary. Tools + providers come from the offline registry registration the drift
test already uses (native manifests loaded straight from ``BUNDLED_DIR``).
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from personalclaw.manifest import API_VERSION, build_manifest
from personalclaw.manifest_meta import canonical_route, is_excluded_route

# The route-registration verbs whose path is a string-literal argument, mapped to
# the position of that argument (``add_route(method, path, handler)`` puts it
# second; every ``add_<verb>(path, handler)`` puts it first).
_VERB_PATH_ARG = {
    "add_get": 0,
    "add_post": 0,
    "add_put": 0,
    "add_delete": 0,
    "add_patch": 0,
    "add_route": 1,
}

_REFERENCE_PKG = "personalclaw.reference"

# Many handler docstrings open by restating the route signature
# (``GET /api/foo — does X`` or ``GET/PUT /api/foo — does X``). The reference
# already prints ``{method} {path}`` before the summary, so that prefix is pure
# duplication — strip it for the markdown, leaving the human description (which
# may be empty, honestly rendered as "no summary").
_ROUTE_SIG_PREFIX = re.compile(
    r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)"
    r"(?:/(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS))*"
    r"\s+/\S*\s*[—:-]?\s*"
)


def _clean_summary(summary: str) -> str:
    """Drop a leading route-signature restatement from a handler docstring summary."""
    return _ROUTE_SIG_PREFIX.sub("", summary).strip()


def reference_dir() -> Path:
    """On-disk path of the shipped reference directory (wheel / editable / source).

    Uses ``importlib.resources.files`` so ``personalclaw doctor --paths`` can point
    an external agent at the docs from the installed binary alone — the Quarkdown
    ``doctor get install-dir`` pattern.
    """
    return Path(str(resources.files(_REFERENCE_PKG)))


# ── Route source (static AST, no app boot) ──────────────────────────────────


def _route_source_files() -> list[Path]:
    """Every package file that could register or define an HTTP route.

    The WHOLE package, not just ``dashboard/``. Entity route families live beside their
    domain (``artifacts/handlers.py``, ``tasks/handlers.py``, ``workflows/handlers.py``) and
    are mounted by ``server.py`` via a ``register_*_routes(app)`` call — so a walk rooted at
    ``dashboard/`` sees the mount call but never the routes themselves, and those families
    were silently missing from the offline reference. An agent reading the reference to find
    an endpoint would conclude it does not exist.
    """
    import personalclaw

    root = Path(personalclaw.__file__).parent
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _handler_docstrings() -> dict[str, str]:
    """Map every function name in the package to its docstring.

    A global index: handlers are referenced at registration as a bare name
    (``api_autonudge_list``) or an attribute (``handlers.api_spawn``,
    ``_up.api_uploads_init``); in every form the callable's own name is the final
    identifier, so one flat name→docstring index resolves them all. First
    definition wins on the rare duplicate — deterministic under the sorted walk.
    """
    index: dict[str, str] = {}
    for py in _route_source_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in index:
                    continue
                doc = ast.get_docstring(node) or ""
                index[node.name] = doc
    return index


def _handler_name(node: ast.expr | None) -> str:
    """The final identifier of a handler reference (``a.b.c`` → ``c``, ``x`` → ``x``)."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _routes_from_ast() -> list[dict[str, Any]]:
    """Every literal HTTP route registered in the package, with its summary.

    Same AST walk as the drift test's ``_literal_route_paths`` (no boot), extended
    to pair each path with the handler's docstring first line. Excluded routes
    (UI transport / app proxy — :data:`MANIFEST_EXCLUDE`) are dropped, matching the
    live ``/api/manifest`` walk. Scans the whole package (see
    :func:`_route_source_files`) so entity families registered from outside
    ``dashboard/`` are not invisible here.
    """
    docs = _handler_docstrings()
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for py in _route_source_files():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            verb = node.func.attr
            if verb not in _VERB_PATH_ARG:
                continue
            path_idx = _VERB_PATH_ARG[verb]
            if len(node.args) <= path_idx:
                continue
            path_node = node.args[path_idx]
            if not (isinstance(path_node, ast.Constant) and isinstance(path_node.value, str)):
                continue
            path = canonical_route(path_node.value)
            method = "*" if verb == "add_route" else verb[len("add_") :].upper()
            if is_excluded_route(method, path):
                continue
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)
            handler_idx = path_idx + 1
            handler = node.args[handler_idx] if len(node.args) > handler_idx else None
            doc = docs.get(_handler_name(handler), "")
            summary = _clean_summary(doc.splitlines()[0].strip() if doc else "")
            out.append(
                {
                    "method": method,
                    "path": path,
                    "summary": summary,
                    "agent_callable": path.startswith("/api/") and not path.startswith("/api/ws"),
                }
            )
    out.sort(key=lambda d: (d["path"], d["method"]))
    return out


# ── Markdown rendering ───────────────────────────────────────────────────────


def _render_params(parameters: dict[str, Any]) -> list[str]:
    """Render a tool's JSON-schema parameters as a bullet list (name, type, required)."""
    props = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
    required = set(parameters.get("required", []) if isinstance(parameters, dict) else [])
    if not props:
        return ["- _(no parameters)_"]
    lines: list[str] = []
    for pname in sorted(props):
        spec = props[pname] if isinstance(props[pname], dict) else {}
        ptype = spec.get("type", "any")
        if isinstance(ptype, list):
            ptype = "|".join(str(t) for t in ptype)
        req = "required" if pname in required else "optional"
        desc = (spec.get("description") or "").strip().replace("\n", " ")
        suffix = f" — {desc}" if desc else ""
        lines.append(f"- `{pname}` ({ptype}, {req}){suffix}")
    return lines


def _render_tools(tools: list[dict[str, Any]]) -> str:
    lines = [
        "# PersonalClaw Tool Reference",
        "",
        f"Generated from the live tool registry (manifest apiVersion {API_VERSION}). "
        "Every registered in-process tool, grouped by provider, with its exact input "
        "schema and worked examples.",
        "",
        "**Never guess a tool signature — copy it from here.** A hallucinated "
        "parameter is the dominant driving failure; the arg names below are "
        "schema-verified against the registered tool by the drift test.",
        "",
    ]
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for t in tools:
        by_provider.setdefault(t["provider"], []).append(t)
    for provider in sorted(by_provider):
        lines.append(f"## {provider}")
        lines.append("")
        for t in sorted(by_provider[provider], key=lambda d: d["name"]):
            lines.append(f"### `{t['name']}`")
            lines.append("")
            desc = (t.get("description") or "").strip()
            if desc:
                lines.append(desc)
                lines.append("")
            rt = t.get("response_type") or ""
            if rt:
                lines.append(f"**Response type:** `{rt}`")
                lines.append("")
            codes = t.get("error_codes") or []
            if codes:
                lines.append("**Error codes:** " + ", ".join(f"`{c}`" for c in codes))
                lines.append("")
            approval = []
            if t.get("requires_approval"):
                approval.append("requires approval")
            risk = t.get("risk_level") or "safe"
            if risk and risk != "safe":
                approval.append(f"risk: {risk}")
            if approval:
                lines.append("**Safety:** " + ", ".join(approval))
                lines.append("")
            lines.append("**Parameters:**")
            lines.extend(_render_params(t.get("parameters") or {}))
            lines.append("")
            for ex in t.get("examples") or []:
                summary = (ex.get("summary") or "").strip()
                args = ex.get("args", {})
                lines.append(f"**Example — {summary}:**")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(args, indent=2, sort_keys=True))
                lines.append("```")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_routes(routes: list[dict[str, Any]]) -> str:
    agent = [r for r in routes if r["agent_callable"]]
    other = [r for r in routes if not r["agent_callable"]]
    lines = [
        "# PersonalClaw HTTP Route Reference",
        "",
        "The gateway's HTTP surface. **Agent-callable routes** (`/api/*`, non-websocket) "
        "are the ones an agent drives directly; the rest are websocket / internal and "
        "listed after for completeness.",
        "",
        "After any mutating call (POST/PUT/PATCH/DELETE), **read the entity back** to "
        "confirm the change took — the mandatory verify loop.",
        "",
        "## Agent-callable routes",
        "",
    ]
    for r in agent:
        summary = r["summary"] or "_(no summary)_"
        lines.append(f"- `{r['method']} {r['path']}` — {summary}")
    lines.append("")
    lines.append("## Websocket / internal routes")
    lines.append("")
    for r in other:
        summary = r["summary"] or "_(no summary)_"
        lines.append(f"- `{r['method']} {r['path']}` — {summary}")
    return "\n".join(lines).rstrip() + "\n"


def _render_providers(providers: dict[str, Any]) -> str:
    lines = [
        "# PersonalClaw Provider Reference",
        "",
        "The extension-provider taxonomy (the capability types an app can contribute) "
        "and the providers currently registered in this build.",
        "",
        "## Provider types",
        "",
    ]
    for t in providers.get("types", []):
        lines.append(f"- `{t}`")
    lines.append("")
    lines.append("## Registered providers")
    lines.append("")
    registered = providers.get("registered", [])
    if not registered:
        lines.append("_(none registered in this build)_")
    else:
        for p in registered:
            state = "enabled" if p.get("enabled") else "disabled"
            if p.get("error"):
                state += f", error: {p['error']}"
            caps = ", ".join(p.get("capabilities", [])) or "—"
            lines.append(
                f"- **{p['app']}** — type `{p['type']}` / `{p['provider_type']}` "
                f"({state}); capabilities: {caps}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _render_index(manifest: dict[str, Any]) -> str:
    tools = manifest["tools"]
    routes = manifest["routes"]
    providers = manifest["providers"]
    n_agent_routes = sum(1 for r in routes if r["agent_callable"])
    provider_names = sorted({t["provider"] for t in tools})
    lines = [
        "# PersonalClaw Agent Reference",
        "",
        f"Offline API/tool reference for PersonalClaw (manifest apiVersion "
        f"{API_VERSION}). Generated from the live registries — the same source as "
        "`GET /api/manifest`. Load the `pclaw-api` skill for the driving methodology; "
        "this reference is the exact-signature lookup it points to.",
        "",
        "## How to use this (orient, then drill)",
        "",
        "1. Read this index to locate the surface you need — don't read every file.",
        "2. Drill into the one relevant section:",
        f"   - **[tools.md](tools.md)** — {len(tools)} registered tools across "
        f"{len(provider_names)} providers, with exact input schemas + examples.",
        f"   - **[routes.md](routes.md)** — {n_agent_routes} agent-callable HTTP routes "
        f"(of {len(routes)} total), with summaries.",
        f"   - **[providers.md](providers.md)** — the provider-type taxonomy + "
        f"{len(providers.get('registered', []))} registered providers.",
        "3. Copy the exact signature — never guess a parameter name.",
        "4. After a mutating call, read the entity back to confirm it took.",
        "",
        "## Tool providers at a glance",
        "",
    ]
    by_provider: dict[str, int] = {}
    for t in tools:
        by_provider[t["provider"]] = by_provider.get(t["provider"], 0) + 1
    for provider in sorted(by_provider):
        lines.append(f"- `{provider}` — {by_provider[provider]} tools")
    lines.extend(
        [
            "",
            "## Repo gotchas that keep resurfacing",
            "",
            "These are environment invariants, not API facts — but they cost more "
            "driving turns than any signature:",
            "",
            "- **Installed apps run from `$PERSONALCLAW_HOME/apps/<name>/`, not the "
            "workspace tree.** Push code edits with `POST /api/apps/{name}/update` "
            "`{source, confirm:true}` — editing the workspace clone does nothing to "
            "the running app.",
            "- **`static/dist` is a SYMLINK to `web/dist`, not a copy.** A `cp -R` "
            "leaves a frozen dir that shadows it and serves a stale SPA. Rebuild the "
            "frontend in place; never replace the symlink with a copy.",
            "- **Use the venv interpreter.** Run the gateway and tools through the "
            "project's `.venv` (`.venv/bin/personalclaw`), not a system Python that "
            "lacks the installed dependencies.",
            "- **Locate this reference from the binary:** `personalclaw doctor --paths` "
            "prints the reference directory (and the config / skills / install dirs) so "
            "an external agent can find these files without knowing the install layout.",
            "",
            "## Scope — what NOT to do",
            "",
            "- Don't hand-roll UI when a tool or route already does the job; the "
            "manifest is the inventory of what already exists.",
            "- Don't bypass `POST /api/apps/{name}/update` by editing an installed "
            "app's files directly.",
            "- Don't call a route the manifest does not mark `agent_callable` as if it "
            "were an agent API.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_reference() -> dict[str, str]:
    """Render the full offline reference as ``{filename: markdown}``.

    Deterministic (sorted, no timestamps) so the checked-in copy byte-compares
    against a fresh render in CI. Tools + providers come from the offline registry
    (native manifests registered straight from ``BUNDLED_DIR``, the drift-test
    seam); routes from the static AST walk. No running gateway.
    """
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers import registry as prov_reg
    from personalclaw.providers.loader import BUNDLED_DIR
    from personalclaw.tool_providers import registry as tool_reg

    tool_reg._providers.clear()
    prov_reg._registry = None
    try:
        reg = prov_reg.get_provider_registry()
        for d in sorted(BUNDLED_DIR.iterdir()):
            mf = d / "app.json"
            if not mf.exists():
                continue
            manifest = AppManifest.from_json_file(mf)
            if manifest.provider:
                reg.register(manifest, enabled=True)
        doc = asyncio.run(build_manifest(app=None))
    finally:
        tool_reg._providers.clear()
        prov_reg._registry = None

    doc["routes"] = _routes_from_ast()
    return {
        "index.md": _render_index(doc),
        "tools.md": _render_tools(doc["tools"]),
        "routes.md": _render_routes(doc["routes"]),
        "providers.md": _render_providers(doc["providers"]),
    }


def write_reference(target: Path | None = None) -> list[Path]:
    """Write the rendered reference to disk; return the paths written."""
    target = target or reference_dir()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, content in render_reference().items():
        path = target / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover - regeneration entry point
    for p in write_reference():
        print(f"wrote {p}")
