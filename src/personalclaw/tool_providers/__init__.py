"""Tool providers — pluggable tool execution backends.

The external tool ADAPTERS ship as apps and import ``personalclaw.sdk.tool``: the MCP
server adapter as the built-in ``mcp-tools`` app (bundled, because the gateway already runs
the servers it serves), the OpenAI-tool-schema adapter as the ``openai-tools`` Store app.
Core keeps the ABC + the native in-process tool machinery (registry, projection,
result_store, tool_prefs, and ``agents.native.tools.InProcessMcpToolProvider``).
"""

from personalclaw.tool_providers.base import (
    ToolDefinition,
    ToolFailure,
    ToolProvider,
    ToolResult,
    tool_failure,
)

__all__ = [
    "ToolDefinition",
    "ToolFailure",
    "ToolProvider",
    "ToolResult",
    "tool_failure",
]
