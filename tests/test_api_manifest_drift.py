"""Drift guard for the generated self-description manifest (PLATFORM-LEGIBILITY §1.2).

The manifest (:mod:`personalclaw.manifest`) is generated from the live registries,
but the two facts they can't carry — a response-type discriminator and worked
examples — live in the hand-maintained :mod:`personalclaw.manifest_meta`. This
suite is what keeps that map honest, so a tool or route can't ship as a silent,
undocumented surface an agent has to reverse-engineer:

* **Tools half (the meat).** Every registered in-process tool (the aggregation
  seam ``list_all_tools`` minus the per-install ``mcp`` fan-in, unioned with
  ``mcp_core``) MUST have a ``TOOL_META`` entry with a non-empty description, at
  least one example, and every example arg a REAL parameter of that tool — the
  plan's central failure mode is an example that invents a parameter. A stale
  ``TOOL_META`` key (no matching live tool) also fails.
* **Routes half (AST, house precedent —** ``test_server_route_handlers_exist`` **).**
  Every literal HTTP route path registered under ``dashboard/`` that is NOT an
  ``/api`` surface MUST be in ``MANIFEST_EXCLUDE`` with a reason, and no
  ``MANIFEST_EXCLUDE`` entry may be stale. Booting the whole dashboard to walk the
  live table has heavy security-critical startup side effects (extension load,
  binding migration), so routes are audited statically like the house route-handler
  guard — the live table is still what the running ``/api/manifest`` walks.
* **Error-codes.** Any ``error_codes`` a tool declares must exist in the §2
  AgentError registry (vacuously true until §2 lands and populates it).
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

import personalclaw.dashboard.server as server_mod
from personalclaw.apps.manifest import AppManifest
from personalclaw.manifest_meta import MANIFEST_EXCLUDE, TOOL_META, canonical_route
from personalclaw.providers.loader import BUNDLED_DIR

# aiohttp route-registration verbs whose FIRST string literal arg is the path
# (``add_route`` is the exception: it's ``add_route(method, path, handler)``).
_ADD_VERBS = {
    "add_get",
    "add_post",
    "add_put",
    "add_delete",
    "add_patch",
    "add_head",
    "add_options",
    "add_static",
}


@pytest.fixture()
def registered_tools():
    """Register every native provider manifest home-free and return the tool union.

    Providers only register at gateway startup, not on import, so a bare process
    sees zero tools. Registering the native manifests directly from ``BUNDLED_DIR``
    reproduces the exact in-process tool surface deterministically and offline —
    the same seam the S3 build-time reference uses. Both process-global registries
    are reset around the test so it can't leak into (or inherit from) siblings.
    """
    from personalclaw.providers import registry as prov_reg
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

        from personalclaw import mcp_core
        from personalclaw.tool_providers.registry import list_all_tools

        live = asyncio.run(list_all_tools())
        by_name = {t.name: t for t in live if t.provider != "mcp"}
        # mcp_core IS the personalclaw-core provider (same 8 names), so the union is
        # already covered by by_name; assert that invariant rather than assume it.
        core_names = {
            (t["name"] if isinstance(t, dict) else t.name) for t in mcp_core._list_tools()
        }
        assert core_names <= set(by_name), core_names - set(by_name)
        yield by_name
    finally:
        tool_reg._providers.clear()
        prov_reg._registry = None


def _param_names(tool) -> set[str]:
    params = tool.parameters or {}
    props = params.get("properties", {}) if isinstance(params, dict) else {}
    return set(props.keys())


def test_every_tool_has_a_faithful_meta_entry(registered_tools):
    """Each live tool has a TOOL_META entry: non-empty description, ≥1 example,
    and every example arg a real parameter (no invented signatures)."""
    problems: list[str] = []
    for name, tool in sorted(registered_tools.items()):
        if name not in TOOL_META:
            problems.append(f"{name}: no TOOL_META entry (add one with a response_type + example)")
            continue
        if not (tool.description or "").strip():
            problems.append(f"{name}: empty description on the registered tool")
        meta = TOOL_META[name]
        examples = meta.get("examples") or []
        if not examples:
            problems.append(f"{name}: TOOL_META entry has no examples")
        real = _param_names(tool)
        for i, ex in enumerate(examples):
            if not ex.get("summary", "").strip():
                problems.append(f"{name}: example[{i}] has no summary")
            for arg in ex.get("args", {}):
                if arg not in real:
                    problems.append(
                        f"{name}: example[{i}] uses '{arg}' which is not a parameter "
                        f"(real: {sorted(real)})"
                    )
    assert not problems, "Manifest tool drift:\n" + "\n".join(problems)


def test_no_stale_tool_meta_entries(registered_tools):
    """Every TOOL_META key maps to a live tool — a removed tool must lose its entry."""
    stale = sorted(set(TOOL_META) - set(registered_tools))
    assert not stale, (
        "TOOL_META has entries for tools that no longer exist " f"(remove them): {stale}"
    )


def test_tool_meta_shape(registered_tools):
    """Every entry carries the three fields the manifest reads, well-typed."""
    for name, meta in TOOL_META.items():
        assert isinstance(meta.get("response_type", ""), str), name
        assert isinstance(meta.get("error_codes", []), list), name
        assert isinstance(meta.get("examples", []), list), name


def _literal_route_paths() -> set[str]:
    """Every literal route path registered under ``dashboard/``, gathered by AST.

    Static, like the house route-handler guard: it reasons about registrations in
    source without booting the security-critical startup path. Covers ``add_get``/
    ``add_post``/… (path is arg 0), ``add_route`` (path is arg 1), and ``add_static``
    (mount prefix is arg 0).
    """
    dash_dir = Path(server_mod.__file__).parent
    paths: set[str] = set()
    for py in dash_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            args = node.args
            if fn.attr in _ADD_VERBS and args:
                first = args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    paths.add(canonical_route(first.value))
            elif fn.attr == "add_route" and len(args) >= 2:
                p = args[1]
                if isinstance(p, ast.Constant) and isinstance(p.value, str):
                    paths.add(canonical_route(p.value))
    return paths


def _non_api(paths: set[str]) -> set[str]:
    return {p for p in paths if not (p.startswith("/api/") or p == "/api")}


def test_every_non_api_route_is_excluded():
    """Every registered non-/api route path is in MANIFEST_EXCLUDE — a new one
    can't slip in as an undocumented, silently-ignored surface."""
    undocumented = sorted(_non_api(_literal_route_paths()) - set(MANIFEST_EXCLUDE))
    assert not undocumented, (
        "Non-/api routes registered but not in MANIFEST_EXCLUDE "
        f"(add each with a reason, or serve it under /api): {undocumented}"
    )


def test_no_stale_exclusions():
    """Every MANIFEST_EXCLUDE entry matches a route that still registers — the
    exclusion allowlist can't rot into a fiction."""
    stale = sorted(set(MANIFEST_EXCLUDE) - _non_api(_literal_route_paths()))
    assert not stale, (
        "MANIFEST_EXCLUDE lists paths that no longer register " f"(remove them): {stale}"
    )


def test_excluded_routes_dont_leak_into_the_live_walk():
    """The live route walk and MANIFEST_EXCLUDE must agree in aiohttp's CANONICAL
    space, not just source space.

    Regression: the app routes register with a regex segment
    (``/apps/{name}/api/{tail:.*}``), but aiohttp's ``resource.canonical`` reports
    them as ``{tail}`` — so an exclusion keyed on the source form matched the AST
    walk yet leaked into the live ``/api/manifest``. This asserts a route whose
    canonical form is excluded is actually dropped by the live ``_routes_section``.
    """
    from aiohttp import web

    from personalclaw.manifest import _routes_section

    app = web.Application()

    async def _proxy(_req):  # pragma: no cover - never called
        return web.Response()

    app.router.add_route("*", "/apps/{name}/api/{tail:.*}", _proxy)
    app.router.add_get("/apps/{name}/ui/{tail:.*}", _proxy)
    paths = {r["path"] for r in _routes_section(app)}
    assert not (paths & set(MANIFEST_EXCLUDE)), (
        "excluded app routes leaked into the live manifest: "
        f"{sorted(paths & set(MANIFEST_EXCLUDE))}"
    )
    assert paths == set(), f"expected all routes excluded, got {sorted(paths)}"


def test_canonical_route_strips_regex():
    """The normalizer matches aiohttp: ``{name:regex}`` → ``{name}``, plain kept."""
    assert canonical_route("/apps/{name}/api/{tail:.*}") == "/apps/{name}/api/{tail}"
    assert canonical_route("/api/tasks/{id}") == "/api/tasks/{id}"
    assert canonical_route("/api/tools") == "/api/tools"


def test_declared_error_codes_exist():
    """Any error_code a tool declares exists in the §2 AgentError registry.

    Vacuous until PLATFORM-LEGIBILITY §2 lands the registry (all error_codes are
    empty now); this asserts the wiring so §2 can populate codes tool-by-tool and
    this guard immediately starts enforcing them.
    """
    try:
        from personalclaw.errors import ERROR_CODES  # type: ignore
    except Exception:
        ERROR_CODES = None  # §2 not landed yet
    for name, meta in TOOL_META.items():
        for code in meta.get("error_codes", []):
            if ERROR_CODES is not None:
                assert code in ERROR_CODES, f"{name}: unknown error_code {code!r}"
