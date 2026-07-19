"""Tool provider registry."""

import logging
from typing import Any

from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

logger = logging.getLogger(__name__)

# Operator-visible load failures. A provider that raises while enumerating its
# tools used to fail silently (the tool just never appeared); we now record the
# (provider, error) so the Tools page can show "provider X failed to load: …"
# rather than leaving the operator to guess why a tool is missing. Best-effort,
# in-memory, refreshed each catalog build.
_load_failures: list[dict[str, str]] = []


def record_failure(provider: str, error: str) -> None:
    """Record a tool-source load failure for operator surfacing (dedup by provider)."""
    _load_failures[:] = [f for f in _load_failures if f.get("provider") != provider]
    _load_failures.append({"provider": provider, "error": str(error)[:300]})


def get_load_failures() -> list[dict[str, str]]:
    """The recorded load failures (a copy)."""
    return list(_load_failures)


def clear_load_failures() -> None:
    """Reset the failure list — called at the start of each catalog build."""
    _load_failures.clear()


def create_native_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-core`` tool surface.

    Returns an in-process provider wrapping ``mcp_core`` directly — the same
    working path the native loop uses.
    """
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider()


def create_schedule_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-schedule`` tool surface — in-process
    over ``mcp_schedule`` (same dead-path fix as core)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_schedule",
        provider_name="personalclaw-schedule",
        display="PersonalClaw Schedule",
    )


def create_artifacts_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-artifacts`` tool surface — in-process
    over ``mcp_artifacts`` (the Artifacts entity tool group)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_artifacts",
        provider_name="personalclaw-artifacts",
        display="PersonalClaw Artifacts",
    )


def create_workflows_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-workflows`` tool surface — in-process
    over ``mcp_workflows`` (the Workflows/SOPs entity tool group)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_workflows",
        provider_name="personalclaw-workflows",
        display="PersonalClaw Workflows",
    )


def create_memory_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-memory`` tool surface — in-process
    over ``mcp_memory`` (persistent lessons + on-demand recall)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_memory",
        provider_name="personalclaw-memory",
        display="PersonalClaw Memory",
    )


def create_subagents_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``personalclaw-subagents`` tool surface — in-process
    over ``mcp_subagents`` (spawn + track background subagents)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="personalclaw.mcp_subagents",
        provider_name="personalclaw-subagents",
        display="PersonalClaw Subagents",
    )

_providers: dict[str, ToolProvider] = {}


def register_provider(provider: ToolProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> ToolProvider | None:
    return _providers.get(name)


def list_providers() -> list[ToolProvider]:
    return list(_providers.values())


async def list_all_tools() -> list[ToolDefinition]:
    """Aggregate tools from all registered providers.

    A provider that raises while listing its tools is recorded as a load failure
    (operator-visible via :func:`get_load_failures`) rather than silently
    dropped, and the remaining providers still contribute.
    """
    all_tools: list[ToolDefinition] = []
    for prov in _providers.values():
        try:
            tools = await prov.list_tools()
            for t in tools:
                t.provider = prov.name
            all_tools.extend(tools)
        except Exception as exc:
            logger.warning("Tool provider %r failed to list tools: %s", prov.name, exc, exc_info=True)
            record_failure(prov.name, str(exc))
    return all_tools
