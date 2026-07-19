"""In-process tool providers expose real tools.

The tool_providers factories wrap the in-process modules (mcp_core / mcp_schedule)
via InProcessMcpToolProvider and yield their tools. ``get_mcp_registry`` is not a
real symbol; importing it would silently yield zero tools, so a guard test keeps
it from resurfacing.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.tool_providers.registry import (
    create_native_provider,
    create_schedule_provider,
)


@pytest.mark.asyncio
async def test_native_factory_yields_core_tools():
    prov = create_native_provider()
    tools = await prov.list_tools()
    assert prov.name == "personalclaw-core"
    assert len(tools) > 0, "core provider must expose tools"
    # Every tool is tagged with the provider name.
    assert all(t.provider == "personalclaw-core" for t in tools)


@pytest.mark.asyncio
async def test_schedule_factory_yields_schedule_tools():
    prov = create_schedule_provider()
    tools = await prov.list_tools()
    assert prov.name == "personalclaw-schedule"
    assert len(tools) > 0, "schedule provider must expose tools"


@pytest.mark.asyncio
async def test_inprocess_provider_defaults_to_core():
    """The default InProcessMcpToolProvider is the personalclaw-core surface the
    native loop uses."""
    p = InProcessMcpToolProvider()
    assert p.name == "personalclaw-core"
    assert len(await p.list_tools()) > 0


def test_get_mcp_registry_has_no_importers():
    """The dead symbol must stay dead: no source module may import it (it does not
    exist, so any importer silently degrades to zero tools)."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"
    offenders = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "import get_mcp_registry" in text or "mcp_discovery import get_mcp_registry" in text:
            offenders.append(str(p.relative_to(root)))
    assert offenders == [], f"dead get_mcp_registry import resurfaced in: {offenders}"
