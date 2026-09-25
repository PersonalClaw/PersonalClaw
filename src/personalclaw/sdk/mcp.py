"""SDK: the MCP-integration seam a tool app builds on.

A tool provider APP (e.g. the MCP-server adapter) needs three generic, provider
-agnostic core services: the current native-loop session key (for per-session
provenance/result-retention), the MCP client registry (the pool of connected MCP
servers), and the risk classifier that maps a tool name to a RiskLevel. Exposed here
so the app imports the stable SDK path, not core internals.

The three registry TYPES are on this surface because ``get_mcp_client_registry()`` is
(#3511): its return is a ``McpClientRegistry``, whose ``get()`` returns a
``McpServerConn``, whose ``list_tools()`` returns ``McpToolSpec``s. An app could call
the published function and then not annotate a single value it walked out of the result,
which makes the typed half of this seam unusable while the runtime half works.
"""

from personalclaw.mcp_client import (  # noqa: F401
    McpClientRegistry,
    McpServerConn,
    McpToolSpec,
    get_mcp_client_registry,
)
from personalclaw.mcp_core import get_current_session_key  # noqa: F401
from personalclaw.task_modes import infer_risk_from_name  # noqa: F401

__all__ = [
    "get_current_session_key",
    "get_mcp_client_registry",
    "infer_risk_from_name",
    "McpClientRegistry",
    "McpServerConn",
    "McpToolSpec",
]
