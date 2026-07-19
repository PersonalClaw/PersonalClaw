"""ACP package — vendor-neutral Agent Client Protocol primitives.

Implements the open `Agent Client Protocol
<https://github.com/zed-industries/agent-client-protocol>`__
(JSON-RPC 2.0 over stdio). The package is intentionally agent-agnostic:
:class:`AcpClient` and :class:`AcpProcessDied` are protocol primitives
usable against any ACP-compliant agent. This package contains no
agent-specific defaults; callers that need backend-specific launch
helpers import them from a dedicated module.
"""

from personalclaw.acp.client import (
    AcpClient,
    AcpError,
    AcpPermissionNeeded,
    AcpProcessDied,
    AcpTimeoutError,
)
from personalclaw.acp.types import AcpEvent, AcpPromptStats, JsonRpcMessage, JsonRpcRequest

__all__ = [
    "AcpClient",
    "AcpError",
    "AcpPermissionNeeded",
    "AcpProcessDied",
    "AcpTimeoutError",
    "AcpEvent",
    "AcpPromptStats",
    "JsonRpcMessage",
    "JsonRpcRequest",
]
