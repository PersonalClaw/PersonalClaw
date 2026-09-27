"""The owner's yes to what an MCP server runs.

A server started with a command is a program PersonalClaw runs as the owner, with everything the
owner can reach; a server at a URL is an address it connects to with the headers it holds. Adding
one used to start it at once: the Tools page's next read probed every name it had not seen, so a
definition written by anyone was launched with nobody asked. Measured on `main`: a fixture's
``npx search-mcp`` was a real npm package, and the probe downloaded and ran it.

So a server runs only once the owner allowed what it runs (`owner_grants`), sealed to its
DEFINITION: how it is reached, its command and arguments, the folder it starts in, the NAMES of
the variables it sets, and for a server at a URL, its address and the names of its headers. Those
are what the owner is shown before they say yes. Values are not in the seal: a secret is never
shown, so a yes could not be to it, and replacing one is not a new question. A change to anything
in the seal is: the server waits again, and keeps waiting until the owner allows the new one.

🔴 ONLY THE OWNER'S SURFACES GIVE A YES, and each shows what will run first
(`http_errors.consent_required`): the Tools page's Add and Edit, the MCP Tool Servers card in
Settings → Providers, and Allow on a server that waits. Everything else that writes a definition
(Import, bringing your setup over, a pack's connector, an app's manifest, a restore, a hand edit
of ``mcp.json``) writes one that waits. The two things that launch a server ask this module first:
the probe (`mcp_discovery.probe_server`) and the connections agents use
(`mcp_client._personalclaw_mcp_specs`).

PersonalClaw's own server (``personalclaw-core``) asks nothing: PersonalClaw writes it into the
agent config from its own code at every start (`agent._MANAGED_MCP_SERVERS`), and a server of that
name anywhere else is never started.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from personalclaw.owner_grants import GrantBook

if TYPE_CHECKING:
    from personalclaw.mcp_discovery import McpServerInfo

#: Where the owner's yes to each server is kept (`owner_grants`), keyed by the server's name.
BOOK = GrantBook("mcp_servers")

#: A server's status while it waits for the owner's Allow (`GET /api/mcp`, the probes).
WAITING = "waiting"

#: Why it does not run, as the probe and the MCP Tool Servers card say it.
WAITING_REASON = (
    "Not allowed to run yet. It starts only after you allow what it runs, with Allow on the "
    "Tools page."
)


def exempt(server: McpServerInfo) -> bool:
    """PersonalClaw's own server, as the agent config holds it. Never one ``mcp.json`` names."""
    from personalclaw.mcp_discovery import _MANAGED_SERVER_NAMES

    return server.name in _MANAGED_SERVER_NAMES and server.source == "agent"


def server_of(name: str, spec: dict[str, Any], source: str = "mcp.json") -> McpServerInfo:
    """*spec* as the probe reads it, so a seal is taken of one shape wherever it is asked."""
    from personalclaw.mcp_discovery import _server_from_spec

    return _server_from_spec(name, spec, source)


def definition(server: McpServerInfo) -> dict[str, Any]:
    """What the seal is taken of: what the server runs, or where it connects, and nothing else.

    ``env`` and ``headers`` are names: their values may be secrets, which no one is shown.
    """
    env = server.env if isinstance(server.env, dict) else {}
    headers = server.headers if isinstance(server.headers, dict) else {}
    return {
        "transport": server.transport,
        "command": server.command or "",
        "args": [str(a) for a in (server.args or [])],
        "cwd": server.cwd or "",
        "env": sorted(str(n) for n in env),
        "url": server.url or "",
        "headers": sorted(str(n) for n in headers),
    }


def _content(server: McpServerInfo) -> str:
    return json.dumps(definition(server), sort_keys=True, separators=(",", ":"))


def allowed(server: McpServerInfo) -> bool:
    """Whether the owner allowed *server* exactly as it is defined now."""
    return exempt(server) or BOOK.holds(server.name, _content(server))


def give(server: McpServerInfo) -> None:
    """Record the owner's yes to *server* as it is defined now. Only an owner surface calls this,
    after its question."""
    BOOK.give(server.name, _content(server))


def revoke(name: str) -> None:
    """Forget the yes to *name*: the server is gone."""
    BOOK.revoke(name)


def shown(server: McpServerInfo) -> dict[str, Any]:
    """What the owner is shown of *server* before they allow it: :func:`definition` with every
    credential in its arguments and address masked (`mcp_discovery.masked_args`, `masked_url`)."""
    from personalclaw.mcp_discovery import masked_args, masked_url

    seen = definition(server)
    seen["args"] = masked_args(seen["args"])
    seen["url"] = masked_url(seen["url"]) if seen["url"] else ""
    return {"name": server.name, **seen}


def revision(server: McpServerInfo) -> str:
    """The revision of what the owner is shown (`stale_write.revision_of`, taken of the masked
    view like every other). Allow names it, so a yes is never given to a definition that changed
    after the page read it."""
    from personalclaw.stale_write import revision_of

    return revision_of(shown(server))


def _names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def title(server: McpServerInfo) -> str:
    """The consent dialog's heading (`http_errors.consent_required`)."""
    if server.is_remote:
        return "Allow this MCP server to connect?"
    return "Allow this MCP server to run?"


def consent(server: McpServerInfo, *, saving: bool = False) -> str:
    """The sentence the owner agrees to before *server* first runs: exactly what it runs, or where
    it connects. Product copy."""
    seen = shown(server)
    verb = "Saving" if saving else "Allowing"
    if server.is_remote:
        headers = seen["headers"]
        sending = (
            f", sending the {'header' if len(headers) == 1 else 'headers'} {_names(headers)}"
            if headers
            else ""
        )
        return (
            f"{verb} “{server.name}” lets PersonalClaw connect to {seen['url']}{sending}. The "
            "server gets whatever your agents send its tools. A change to its address or headers "
            "asks you again."
        )
    line = " ".join([seen["command"], *seen["args"]]).strip()
    where = f" in {seen['cwd']}" if seen["cwd"] else ""
    env = seen["env"]
    setting = (
        f", with the {'variable' if len(env) == 1 else 'variables'} {_names(env)} set"
        if env
        else ""
    )
    return (
        f"{verb} “{server.name}” lets PersonalClaw run {line}{where} as you{setting}. Like any "
        "program you start, it can read and change your files and reach the network. A change "
        "to what it runs asks you again."
    )
