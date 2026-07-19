"""Regression guard for the UT1 split of the monolithic NativeBuiltinToolProvider
into per-entity category providers (docs/plans/unified-tool-provider-universe.md).

Invariants that hold the split together:
1. The category providers PARTITION the full builtin tool set — their union equals
   the monolith's tools, with no tool lost and none double-counted.
2. Each category provider stamps its own provider name on its tools (so /api/tools
   + the registry group them correctly and each tool appears once).
3. The platform bundle owns filesystem + shell + the tool_result_get affordance
   (always-on); the rest are the installable app categories.
"""

from __future__ import annotations

import asyncio

import personalclaw.agents.native.builtin_tools as BT


def _names(provider) -> set[str]:
    return {t.name for t in asyncio.get_event_loop().run_until_complete(provider.list_tools())}


_FACTORIES = {
    "personalclaw-filesystem": BT.create_platform_tools_provider,
    "personalclaw-knowledge-tools": BT.create_knowledge_tools_provider,
    "personalclaw-tasks-tools": BT.create_tasks_tools_provider,
    "personalclaw-project-tools": BT.create_project_tools_provider,
    "personalclaw-inbox-tools": BT.create_inbox_tools_provider,
}


def test_categories_partition_the_full_set():
    full = _names(BT.NativeBuiltinToolProvider(cwd="/tmp"))  # categories=None → all
    union: set[str] = set()
    counts: dict[str, int] = {}
    for factory in _FACTORIES.values():
        for n in _names(factory()):
            counts[n] = counts.get(n, 0) + 1
            union.add(n)
    assert union == full, f"missing={full - union} extra={union - full}"
    dupes = {n for n, c in counts.items() if c > 1}
    assert not dupes, f"tool in >1 category: {dupes}"


def test_each_provider_stamps_its_own_name():
    for name, factory in _FACTORIES.items():
        prov = factory()
        defs = asyncio.get_event_loop().run_until_complete(prov.list_tools())
        assert defs, f"{name} surfaced no tools"
        assert all(t.provider == name for t in defs), f"{name} mis-stamped a tool's provider"


def test_platform_owns_filesystem_shell_and_affordance():
    plat = _names(BT.create_platform_tools_provider())
    assert {"read_file", "write_file", "edit_file", "list_dir", "glob", "grep", "repo_map"} <= plat
    assert "bash" in plat
    assert "tool_result_get" in plat
    # platform must NOT carry the installable-app categories
    assert not ({"knowledge_search", "task_create", "project_run_create", "post_to_inbox"} & plat)


def test_app_categories_are_the_installable_entities():
    assert _names(BT.create_knowledge_tools_provider()) == {
        "knowledge_search", "knowledge_create", "knowledge_get", "knowledge_update", "knowledge_stats",
    }
    assert _names(BT.create_inbox_tools_provider()) == {"post_to_inbox"}
    runs = _names(BT.create_project_tools_provider())
    assert {"project_run_create", "project_run_start", "project_run_status", "project_run_list"} <= runs


def test_category_map_covers_every_tool():
    # every tool the monolith exposes must have a category, else it'd be dropped by
    # every category provider (orphaned).
    full = _names(BT.NativeBuiltinToolProvider(cwd="/tmp"))
    uncategorized = {n for n in full if n not in BT._CATEGORY_OF}
    assert not uncategorized, f"tools with no category: {uncategorized}"
