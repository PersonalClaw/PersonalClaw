"""The owner's yes to what an MCP server runs, for a test about something that happens after it.

A server runs only once the owner allowed what it runs (`personalclaw.mcp_grants`). A test whose
subject is a server's secrets, its transport, its sign-in or its tools, rather than that yes, gives
the yes here, the way the owner does on the Tools page. The tests of the yes itself are
``test_only_the_owner_decides_what_an_mcp_server_runs.py``.
"""

from __future__ import annotations

from typing import Any


def allow_configured(*names: str) -> None:
    """Allow each named server (every server, when none is named) as it is configured now."""
    from personalclaw import mcp_grants
    from personalclaw.mcp_discovery import list_servers

    for server in list_servers(include_disabled=True):
        if not names or server.name in names:
            mcp_grants.give(server)


def allow(server: Any) -> None:
    """Allow one server the test built itself (an ``McpServerInfo``)."""
    from personalclaw import mcp_grants

    mcp_grants.give(server)


def confirmed(body: dict[str, Any]) -> dict[str, Any]:
    """*body* as the page resends it once the owner agreed in the consent dialog."""
    return {**body, "confirm": True}


def trust_labels(name: str, tools: list[Any]) -> None:
    """The owner's trust in server *name*'s read-only labels, given for *tools* as they are
    listed now, the way the Tools page's Trust gives it (`mcp_read_only_trust.seal`). The tests of
    the trust itself are ``test_trusting_an_mcp_servers_labels_covers_the_tools_she_saw.py``."""
    from personalclaw import mcp_read_only_trust

    mcp_read_only_trust.seal(
        name,
        tools,
        {mcp_read_only_trust.definition(t).name: mcp_read_only_trust.digest(t) for t in tools},
    )
