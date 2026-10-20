"""SDK: the tool-provider ABC + data types + the shared output-projection discipline.

Stable re-export of ``personalclaw.tool_providers.base`` (the ABC + types) plus the
dispatch-time output projection (``project_and_retain`` + its default cap) every tool
surface shares — so a tool APP (e.g. the MCP adapter) projects + retains large results
identically to the native builtins, without reaching into core internals.

``AgentError`` is on this surface because ``ToolResult.agent_error`` is: a published
dataclass field whose type an app cannot import is a field an app cannot fill, so a
third-party tool's failures could only ever be prose while the native builtins' carried a
stable code. Same reason it is on ``sdk.action``.
"""

from personalclaw.errors import AgentError  # noqa: F401
from personalclaw.tool_providers.base import (  # noqa: F401
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)
from personalclaw.tool_providers.projection import (  # noqa: F401
    DEFAULT_TOOL_OUTPUT_CAP,
    ProjectionRule,
    project_and_retain,
)

__all__ = [
    "ToolProvider",
    "ToolDefinition",
    "ToolResult",
    "RiskLevel",
    "ProjectionRule",
    "project_and_retain",
    "DEFAULT_TOOL_OUTPUT_CAP",
    "AgentError",
]
