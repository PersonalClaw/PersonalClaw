"""Shared constants used across cli and gateway modules."""

#: The name, drawn in figlet's "small" font, that ``personalclaw`` with no command and the
#: ``personalclaw chat`` prompt print. Defined here once and imported by both: when each
#: module drew its own copy, the two could say different things, and a word search reads
#: none of it (``tests/test_the_cli_banner_is_personalclaw.py``).
BANNER = r"""
   ___                           _  ___ _
  | _ \___ _ _ ___ ___ _ _  __ _| |/ __| |__ ___ __ __
  |  _/ -_) '_(_-</ _ \ ' \/ _` | | (__| / _` \ V  V /
  |_| \___|_| /__/\___/_||_\__,_|_|\___|_\__,_|\_/\_/

  Your personal AI agent
"""

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
    tell which form it holds is still safe. A name in its transcript's file form
    (``dashboard_<name>``) is read by :func:`dashboard_key_from_file_form`;
    :func:`dashboard_history_key` applies both rules.
    """
    if session_name.startswith(DASHBOARD_SESSION_PREFIX):
        return session_name
    return f"{DASHBOARD_SESSION_PREFIX}{session_name}"


#: A dashboard chat's FILE form: its transcript is filed as ``dashboard_<name>``
#: (``history._safe_key`` turns the ``:`` into ``_``), and resume round-trips once stacked the
#: prefix (``dashboard_dashboard_<name>``).
DASHBOARD_FILE_PREFIX = "dashboard_"


def dashboard_key_from_file_form(name: str) -> str:
    """``dashboard:<name>`` for a chat named in its file form, however stacked; *name* itself
    for any other chat, which is filed under its own key.

    Here rather than in ``dashboard/chat_utils`` for the reason :data:`DASHBOARD_SESSION_PREFIX`
    is: code below the HTTP surface names a chat from its transcript's file (the idle-chat
    compression pass records its usage under the key the chat's turns are recorded by), and
    :func:`dashboard_history_key` normalizes a session name with this same rule.
    """
    if not name.startswith(DASHBOARD_FILE_PREFIX):
        return name
    while name.startswith(DASHBOARD_FILE_PREFIX):
        name = name[len(DASHBOARD_FILE_PREFIX) :]
    return dashboard_session_key(name)


def dashboard_history_key(session_name: str) -> str:
    """The key a dashboard chat session is recorded under — its transcript, and the usage row
    each of its turns writes — from its name in whichever form it arrives: bare, already
    wrapped, or its transcript's file form.

    The ONE rule for that key. The chat turn writer (``dashboard/chat_utils._history_key_for``)
    and every reader of those rows key through it, so a reader cannot ask for a key the writer
    never wrote. One did: a loop's spend total read its worker's bare session name while every
    turn the worker ran had been booked under the wrapped one, so a loop that had spent real
    money read $0.00 and its cost cap could never trip.
    """
    return dashboard_session_key(dashboard_key_from_file_form(session_name))
