"""Tool providers — pluggable tool execution backends.

The external tool ADAPTERS (MCP server client, OpenAI-tool-schema adapter) ship as
apps now (apps/mcp-tools, apps/openai-tools) and import ``personalclaw.sdk.tool``.
Core keeps the ABC + the native in-process tool machinery (registry, projection,
result_store, tool_prefs, and ``agents.native.tools.InProcessMcpToolProvider``).
"""

from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

__all__ = [
    "ToolDefinition",
    "ToolProvider",
    "ToolResult",
]
