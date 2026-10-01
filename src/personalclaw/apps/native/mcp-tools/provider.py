"""MCP adapter for the ToolProvider interface.

Surfaces the tools of the external MCP servers configured in ``~/.personalclaw/mcp.json``
through the unified ``ToolProvider`` abstraction, so they are discoverable on the Tools page
and invocable by the native agent loop. The gateway already connects to those servers
(``personalclaw.sdk.mcp.get_mcp_client_registry``); this provider is the one surface through
which an agent can call what they expose, which is why it ships with PersonalClaw rather than
as a Store install. With no server configured it serves no tools at all.

Tool names are namespaced ``mcp/<server>/<tool>`` so they never collide with the
builtin/in-process core tools, and ``invoke`` routes back to the owning server. What a tool is
taken to do comes from the server's own annotations (:func:`personalclaw.sdk.mcp.declared_risk`),
never from its name: a read asks nobody, like every tool that declares one, and every other tool
asks before it runs.
"""

from __future__ import annotations

from typing import Any, Callable

from personalclaw.sdk.mcp import McpClientRegistry
from personalclaw.sdk.tool import RiskLevel, ToolDefinition, ToolProvider, ToolResult

_TOOL_PREFIX = "mcp"


def create_mcp_provider(config: dict[str, Any] | None = None) -> "McpToolProvider":
    """Factory for the extension system: the adapter bound to the live in-process MCP client
    registry, whose servers are re-read from ``mcp.json`` on every call."""
    from personalclaw.sdk.mcp import get_mcp_client_registry

    return McpToolProvider(get_mcp_client_registry)


class McpToolProvider(ToolProvider):
    """Surfaces tools from all connected external MCP servers."""

    def __init__(self, get_mcp_registry_fn: Callable[[], McpClientRegistry]) -> None:
        self._get_registry = get_mcp_registry_fn

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "MCP Servers"

    async def list_tools(self) -> list[ToolDefinition]:
        from personalclaw.sdk.mcp import declared_risk

        tools: list[ToolDefinition] = []
        for server_name, conn in self._get_registry().items():
            for tool in await conn.list_tools():
                # What the server's annotations declare, never what the tool is called: a
                # read-only label counts only from a server the owner trusts (Tools page), a
                # destructive one from any server, and a tool that says nothing is a change.
                # A read asks nobody; a change asks.
                risk = declared_risk(server_name, tool)
                tools.append(
                    ToolDefinition(
                        name=f"{_TOOL_PREFIX}/{server_name}/{tool.name}",
                        description=tool.description,
                        provider="mcp",
                        parameters=tool.input_schema,
                        requires_approval=risk is not RiskLevel.SAFE,
                        risk_level=risk,
                        # The server's labels as it sent them, for the approval to show.
                        annotations=dict(tool.annotations or {}),
                    )
                )
        return tools

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        # Expected shape: mcp/<server>/<tool>. Tool ids may themselves contain a
        # slash, so split into at most 3 parts and validate the prefix.
        parts = tool_name.split("/", 2)
        if len(parts) != 3 or parts[0] != _TOOL_PREFIX:
            return ToolResult(success=False, error=f"Invalid MCP tool name: {tool_name}")
        _, server_name, tool = parts

        # Route to the caller's session-scoped connection for stateful servers, so
        # one session's browser/shell state can't leak into another's. Poolable
        # servers ignore the key and share one connection (registry decides).
        from personalclaw.sdk.mcp import get_current_session_key

        conn = self._get_registry().get(server_name, get_current_session_key())
        if not conn:
            return ToolResult(success=False, error=f"MCP server '{server_name}' not found")

        ok, output = await conn.call_tool(tool, arguments)
        if not ok:
            return ToolResult(success=False, output="", error=output)
        # MCP results are the highest-volume, least-controllable outputs (a server can return
        # anything). Apply the SAME dispatch-time projection discipline as native tools:
        # type-aware preview + retain the raw for tool_result_get. Uses the session-scoped raw
        # store (the same session key that routed the call above).
        from personalclaw.sdk.tool import DEFAULT_TOOL_OUTPUT_CAP as _MAX_OUTPUT_CHARS
        from personalclaw.sdk.tool import project_and_retain

        proj_text, meta = project_and_retain(
            output,
            session_key=get_current_session_key() or "",
            cap=_MAX_OUTPUT_CHARS,
        )
        return ToolResult(
            success=True,
            output=proj_text,
            truncated=("raw_ref" in meta),
            original_length=len(output),
            metadata=meta,
        )
