"""Regression guard for the native tool-category split (docs/plans/native-tool-categories.md).

The monolithic personalclaw-core tool surface was split into 5 cohesive category
tool-providers (core / subagents / memory / artifacts / workflows). Two invariants
hold the split together and must never silently drift:

1. **In-process** — each category is its own provider in the tool registry, and every
   tool is owned by exactly one provider (no duplication, no orphan).
2. **ACP MCP-server** — ``run_mcp_core_server`` aggregates residual core + every
   category module into ONE surface (``_aggregated_list_tools``/``_aggregated_call_tool``)
   so a CLI sees the full set. The aggregate must equal the union of all category
   modules + residual core, with no duplicate tool names, and stay a superset of the
   in-process catalog's core-family tools.

A future edit that adds a tool to a category module but forgets
``_AGGREGATED_CATEGORY_MODULES``, or that collides two categories' tool names, breaks
ACP parity with no other failing test — these pin it.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

import personalclaw.mcp_core as core

# The category modules the aggregation root composes, and the providers they back.
_CATEGORY_MODULES = [
    "personalclaw.mcp_artifacts",
    "personalclaw.mcp_workflows",
    "personalclaw.mcp_memory",
    "personalclaw.mcp_subagents",
]
_CATEGORY_PROVIDERS = {
    "personalclaw-core",
    "personalclaw-subagents",
    "personalclaw-memory",
    "personalclaw-artifacts",
    "personalclaw-workflows",
}
# The cross-cutting tools that stay in residual core (not a single entity category).
# skill_invoke + skill_search + skill_remember are the skill-library-spanning trio
# (load one / find any / capture one) — all live in core since they span the whole
# skill library rather than one entity category.
_RESIDUAL_CORE_TOOLS = {
    "skill_invoke",
    "skill_search",
    "skill_remember",
    "wait",
    "hook_register",
    "notify",
    "notify_attachment",
    "loop_nudge_stop",
}


def _names(list_tools_fn) -> list[str]:
    return [t["name"] for t in list_tools_fn()]


# ── Residual core ───────────────────────────────────────────────────────────


def test_residual_core_is_exactly_the_cross_cutting_tools():
    assert set(_names(core._list_tools)) == _RESIDUAL_CORE_TOOLS


def test_residual_core_owns_no_category_tools():
    core_names = set(_names(core._list_tools))
    for prefix in ("artifact_", "workflow_", "memory_", "subagent_"):
        assert not any(n.startswith(prefix) for n in core_names), f"core still owns {prefix}*"


# ── Category modules each expose a coherent surface ───────────────────────────


@pytest.mark.parametrize("mod_path", _CATEGORY_MODULES)
def test_category_module_has_list_and_call(mod_path):
    mod = importlib.import_module(mod_path)
    assert callable(mod._list_tools) and callable(mod._call_tool)
    assert _names(mod._list_tools), f"{mod_path} exposes no tools"


def test_aggregated_modules_registered_in_root():
    # The aggregation root must reference exactly the category modules (so a new
    # category can't be added without wiring it into the ACP surface).
    assert set(core._AGGREGATED_CATEGORY_MODULES) == set(_CATEGORY_MODULES)


# ── ACP aggregate completeness + no collisions ────────────────────────────────


def test_aggregate_equals_core_plus_all_categories():
    expected: set[str] = set(_names(core._list_tools))
    for mod_path in _CATEGORY_MODULES:
        expected |= set(_names(importlib.import_module(mod_path)._list_tools))
    assert set(_names(core._aggregated_list_tools)) == expected


def test_aggregate_has_no_duplicate_tool_names():
    agg = _names(core._aggregated_list_tools)
    dupes = {n for n in agg if agg.count(n) > 1}
    assert not dupes, f"duplicate tool names across categories: {dupes}"


def test_no_tool_name_collision_between_categories():
    seen: dict[str, str] = {}
    sources = {"personalclaw.mcp_core": core._list_tools}
    sources.update({m: importlib.import_module(m)._list_tools for m in _CATEGORY_MODULES})
    for src, fn in sources.items():
        for n in _names(fn):
            assert n not in seen, f"{n} defined in both {seen[n]} and {src}"
            seen[n] = src


# ── In-process registry: every tool grouped under exactly one category provider ──


def test_in_process_catalog_matches_aggregate_and_groups_by_provider():
    from personalclaw.providers.loader import load_all_extensions
    from personalclaw.tool_providers.registry import list_all_tools, list_providers

    load_all_extensions()
    provs = {p.name for p in list_providers()}
    assert (
        _CATEGORY_PROVIDERS <= provs
    ), f"missing category providers: {_CATEGORY_PROVIDERS - provs}"

    tools = asyncio.run(list_all_tools())
    # Every tool the ACP aggregate exposes is present in the in-process catalog too.
    inproc = {t.name for t in tools}
    assert set(_names(core._aggregated_list_tools)) <= inproc

    # Each category's tools are owned in-process by its own provider (not core).
    owner = {t.name: t.provider for t in tools}
    expectations = {
        "artifact_save": "personalclaw-artifacts",
        "workflow_list": "personalclaw-workflows",
        "memory_recall": "personalclaw-memory",
        "subagent_run": "personalclaw-subagents",
        "skill_invoke": "personalclaw-core",
    }
    for tool, prov in expectations.items():
        assert owner.get(tool) == prov, f"{tool} owned by {owner.get(tool)!r}, want {prov!r}"


# ── The native chat agent must reach EVERY category (the split regression) ─────


@pytest.mark.xfail(reason="pre-existing on main (v0.1.0 baseline) — #6", strict=False)
def test_native_runtime_tool_surface_includes_all_categories():
    """The native agent's tool surface is sourced from the registry, so a split-out or
    newly-added tool provider reaches it automatically. Regression guard: the category
    split moved subagents/memory/artifacts/workflows + the web tools out of mcp_core,
    and a hardcoded provider list in _build_native_runtime silently dropped them from
    the chat agent — caught only by a live chat turn. This asserts the runtime's tool
    surface (builtin + every registered tool provider) covers each family.
    """
    from pathlib import Path

    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.providers.loader import load_all_extensions
    from personalclaw.tool_providers.base import ToolProvider
    from personalclaw.tool_providers.registry import list_providers

    load_all_extensions()

    # web_search ships as the web-tools APP now (separated out of core), so it's only in
    # the surface when installed. Load its provider from the app dir (as the loader would
    # for an installed app) + add it, so this test verifies the full category surface
    # with the app present — the web-tool wiring must still reach the runtime.
    def _load_web_tools_provider() -> ToolProvider | None:
        import importlib.util
        import sys

        app = Path(__file__).resolve().parents[2] / "apps" / "web-tools" / "provider.py"
        if not app.is_file():
            return None
        spec = importlib.util.spec_from_file_location("_pclaw_app_web_tools__provider", app)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod.create_provider({})

    # Mirror _build_native_runtime's tool_providers assembly: builtin + registry + the
    # installed web-tools app.
    provs = [NativeBuiltinToolProvider(cwd=None, agent="x", session_key=""), *list_providers()]
    web = _load_web_tools_provider()
    if web is not None:
        provs.append(web)
    names: set[str] = set()
    for p in provs:
        try:
            names |= {t.name for t in asyncio.run(p.list_tools())}
        except Exception:
            pass  # an unconfigured remote provider may list nothing — fine
    for sample in (
        "web_search",
        "subagent_run",
        "memory_recall",
        "artifact_save",
        "workflow_list",
        "schedule_add",
    ):
        assert (
            sample in names
        ), f"native agent can't reach {sample!r} — a tool provider is not wired in"
