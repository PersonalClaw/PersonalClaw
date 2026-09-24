"""Native in-process agent runtime (E2-P4).

The native ``AgentProvider`` runs the agent turn loop *inside* the PersonalClaw
process: it inferences through a :class:`~personalclaw.llm.base.ModelProvider`
(governed by Settings → Models), executes tools through the
:class:`~personalclaw.tool_providers.base.ToolProvider` seam, gates them through an
in-process :class:`~personalclaw.agents.native.approval.ApprovalGate`, and emits the
same neutral :class:`~personalclaw.llm.events.AgentEvent` stream the chat runner
already consumes from ACP. No external CLI subprocess is involved.
"""

from personalclaw.agents.native.approval import ApprovalGate
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider

__all__ = [
    "ApprovalGate",
    "InProcessMcpToolProvider",
    "NativeAgentRuntime",
    "NativeBuiltinToolProvider",
]
