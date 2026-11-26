"""Every tool call sorts into one family, for the words that describe it (shared contracts §3.3.1).

``tool_families.family_of`` reads a call's tool, its kind and its declaration: PersonalClaw's own
tools by a frozen mapping (a call to one through an agent CLI's tool server by the tool it names),
a shell call as a command, an agent CLI's own tool by the kind it declares, and everything else as
``other``. Each of core's own tools, and each tool of the apps it ships, has a row, and each row
names one of them, so a tool added without a row, or a row a renamed tool left, fails here.
"""

from __future__ import annotations

import asyncio
import typing

import pytest
from table_rows import row

from personalclaw import tool_families, turns


def _core_tools() -> set[str]:
    """Every tool PersonalClaw itself serves: the native built-ins, the native runtime's own
    discovery tools, and the tool server an agent CLI is handed."""
    from personalclaw import mcp_core
    from personalclaw.agents.native import builtin_tools
    from personalclaw.agents.native.runtime import NativeAgentRuntime

    served = {t["name"] for t in mcp_core._aggregated_list_tools()}
    return set(builtin_tools._CATEGORY_OF) | set(NativeAgentRuntime.META_TOOLS) | served


def _shipped_app_tools(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Every tool the apps PersonalClaw ships serve, registered as the gateway registers them at
    start-up: no app registers on import, so a bare process sees none of them."""
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers import registry as provider_registry
    from personalclaw.providers.loader import BUNDLED_DIR
    from personalclaw.tool_providers import registry as tool_registry

    monkeypatch.setattr(provider_registry, "_registry", None)
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})
    registry = provider_registry.get_provider_registry()
    for manifest_file in sorted(BUNDLED_DIR.glob("*/app.json")):
        manifest = AppManifest.from_json_file(manifest_file)
        if manifest.all_providers():
            registry.register(manifest, enabled=True)
    served = asyncio.run(tool_registry.list_all_tools())
    return {tool.name for tool in served if tool.provider != "mcp"}


@pytest.fixture
def own_tools(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    tools = _core_tools() | _shipped_app_tools(monkeypatch)
    assert len(tools) > 100, f"found {len(tools)} of PersonalClaw's own tools — vacuous"
    return tools


def test_every_core_tool_has_a_family(own_tools: set[str]) -> None:
    """🔴 Before the shipped apps were read: ``ask_user``, ``calendar_events``, ``code_map`` and
    four more had no row."""
    assert sorted(own_tools - set(tool_families.CORE_FAMILIES)) == []


def test_every_row_names_a_tool_core_serves(own_tools: set[str]) -> None:
    """A row left behind by a renamed tool keeps a family nothing calls, while the new name has
    none until someone notices."""
    assert sorted(set(tool_families.CORE_FAMILIES) - own_tools) == []


def test_the_families_are_the_contracts_and_each_has_its_words(subtests) -> None:
    assert tuple(tool_families.PHRASES) == typing.get_args(turns.Family)
    for family, n, said in (
        ("read", 3, "read 3 files"),
        ("edit", 1, "edited 1 file"),
        ("command", 8, "ran 8 commands"),
        ("search", 2, "searched 2 times"),
        ("search", 1, "searched once"),
        ("read", 1, "read 1 file"),
        ("fetch", 1, "fetched 1 page"),
        ("browse", 4, "browsed 4 pages"),
        ("delegate", 2, "started 2 helpers"),
        ("send", 1, "sent 1 message"),
        ("other", 5, "used 5 tools"),
    ):
        with row(subtests, family=family, n=n):
            assert tool_families.phrase(family, n) == said


def test_a_call_sorts_by_its_tool_its_kind_and_its_declaration(subtests) -> None:
    for tool, kind, declared, family in (
        ("read_file", "", "safe", "read"),
        ("write_file", "", "caution", "edit"),
        ("bash", "", "destructive", "command"),
        ("memory_recall", "", "safe", "search"),
        ("subagent_run", "", "caution", "delegate"),
        ("notify", "", "caution", "send"),
        ("workflow_start", "", "caution", "other"),
        # A shipped app's tool is core's own.
        ("code_map", "", "safe", "read"),
        ("ui_search", "", "safe", "search"),
        ("ask_user", "", "safe", "other"),
        # A call to core's own tool through an agent CLI's tool server is that tool's.
        ("mcp__personalclaw-core__chat_search", "other", "", "search"),
        ("mcp.personalclaw-core.notify", "execute", "", "send"),
        # An agent CLI's own tools, by the kind each declares.
        ("Read", "read", "", "read"),
        ("Edit notes.md", "edit", "", "edit"),
        ("Delete old.md", "delete", "", "edit"),
        ("Terminal", "execute", "", "command"),
        ("Find TODOs", "search", "", "search"),
        ("Fetch the docs", "fetch", "", "fetch"),
        ("Think", "think", "", "other"),
        # An MCP or app tool whose name looks like one of core's is not one of them.
        ("mcp/files/read_file", "", "safe", "other"),
        ("team_post", "", "caution", "other"),
    ):
        with row(subtests, tool=tool, kind=kind):
            assert tool_families.family_of(tool, kind, declared, {}) == family
