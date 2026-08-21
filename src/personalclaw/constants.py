"""Shared constants used across cli and gateway modules."""

DATA_WARNING = (
    "⚠️  Do not share confidential, sensitive, or regulated data with AI models.\n"
    "   Review your organization's AI usage and data handling policies\n"
    "   before entering sensitive information."
)

CHAT_TURN_TIMEOUT = 600.0

#: JSON-RPC 2.0's "the peer does not implement this method" code. Lives here, not in a
#: protocol module, because THREE unrelated subsystems speak JSON-RPC and each needs the
#: same number to mean the same thing: the MCP HTTP server and the stdio MCP server both
#: *emit* it for an unknown method, and the ACP client *reads* it off an agent's terminal
#: error frame to tell "this agent cannot do that at all" from "that attempt failed".
#: A second literal would let those two readings drift apart silently.
JSONRPC_METHOD_NOT_FOUND = -32601
