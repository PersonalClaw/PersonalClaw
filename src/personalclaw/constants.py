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

#: The namespace a DASHBOARD chat session's key is wrapped in for the provider, history
#: and ledger layers (``dashboard:<session name>``).
#:
#: Lives here, not in ``dashboard/chat_utils`` where the wrapper is applied, because
#: three layers below the HTTP surface have to agree on it and each learned it the hard
#: way: ``guardrails.policy`` classifies the wrapped form (a headless session read as
#: ATTENDED while its bare key read unattended), and ``usage_ledger`` rows are KEYED by
#: the wrapped form (a bare-key query returned a confident 0 tokens for a turn that had
#: really billed 22,979). A private copy in each of those modules is the same literal
#: three times, free to drift; and importing ``chat_utils`` to get it inverts the
#: dependency — core would need the web app stood up to name a session.
DASHBOARD_SESSION_PREFIX = "dashboard:"


def dashboard_session_key(session_name: str) -> str:
    """Wrap a dashboard chat session's own key into its ``dashboard:`` namespace.

    Idempotent: an already-wrapped key is returned unchanged, so a caller that cannot
    tell which form it holds is still safe. ``dashboard/chat_utils._history_key_for`` is
    the richer variant (it also normalizes the legacy ``dashboard_`` filename form) and
    delegates here for the wrapping itself.
    """
    if session_name.startswith(DASHBOARD_SESSION_PREFIX):
        return session_name
    return f"{DASHBOARD_SESSION_PREFIX}{session_name}"
