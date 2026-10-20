"""The owner's trust in an MCP server's read-only labels: the Tools page's Trust, Review and Stop.

The trust is sealed to each tool's definition (`personalclaw.mcp_read_only_trust`). A server's card
(`GET /api/mcp`) carries it as ``readOnlyTrust``: whether the owner trusts the labels, the digest of
each tool the server lists now, and, while she does, which tools were added, changed or removed
since. Trust and Review are one write: the page sends the tools it showed her, each with the digest
it showed, and those still defined that way are sealed. Stop trusting needs no question.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from personalclaw.http_errors import consent_required, json_error
from personalclaw.request_validation import json_object_body
from personalclaw.safety_flags import confirm_granted
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

#: The most tools one Trust or Review names: far more than a server lists, and a bound on a body.
MAX_SEEN_TOOLS = 1000


def read_only_trust_of(server: Any) -> dict[str, Any] | None:
    """*server*'s ``readOnlyTrust``, as its card reads it, or ``None`` for PersonalClaw's own
    server, whose tools say what they do themselves and need no trust. The listing it is read
    against is what the server's last start found as it is defined now; while there is none, the
    record says whether the owner trusts the labels, and nothing of what changed."""
    from personalclaw import mcp_grants, mcp_read_only_trust

    if mcp_grants.exempt(server):
        return None
    tools = server.tools if server.status == "ok" else None
    return mcp_read_only_trust.review(server.name, tools).as_dict()


def _read_only_names(tools: list[Any], seen: dict[str, str]) -> list[str]:
    """The tools of *tools* the owner reviewed (*seen*) that label themselves read-only."""
    from personalclaw.mcp_read_only_trust import definition

    names = []
    for tool in tools:
        d = definition(tool)
        if d.name in seen and d.annotations.get("readOnlyHint") is True:
            names.append(d.name)
    return sorted(names)


def consent(name: str, read_only: list[str]) -> str:
    """The sentence the owner agrees to before trusting server *name*'s labels. Product copy."""
    if read_only:
        which = (
            f"the tool {read_only[0]}"
            if len(read_only) == 1
            else f"the tools {', '.join(read_only)}"
        )
        runs = (
            f"Trusting “{name}”'s read-only labels lets {which}, which it labels read-only, run "
            "without asking you, and in Ask and Plan mode."
        )
    else:
        runs = (
            f"Trusting “{name}”'s read-only labels lets no tool run without asking yet: it labels "
            "none of these tools read-only."
        )
    return (
        f"{runs} If it labels a tool that changes something as read-only, that change happens "
        "without anyone being asked. A tool it adds or changes later asks you until you review it "
        "on the Tools page."
    )


def _seen_tools(body: dict[str, Any]) -> dict[str, str] | web.Response:
    """The body's ``tools``: each tool the page showed, with the digest it showed. A refusal when it
    is not a map of tool names to digests."""
    from personalclaw.mcp_read_only_trust import is_digest

    raw = body.get("tools")
    if not isinstance(raw, dict) or len(raw) > MAX_SEEN_TOOLS:
        return json_error(
            "invalid_request",
            message=(
                f"tools must be an object of at most {MAX_SEEN_TOOLS} tool names, each with the "
                "digest of the definition the page showed."
            ),
            status=400,
        )
    bad = sorted(str(n) for n, d in raw.items() if not isinstance(n, str) or not is_digest(d))
    if bad:
        return json_error(
            "invalid_request",
            message=f"Not a digest of a tool's definition for: {', '.join(bad[:5])}.",
            status=400,
        )
    return dict(raw)


async def api_mcp_server_read_only_trust(request: web.Request) -> web.Response:
    """POST/DELETE /api/mcp/servers/{name}/read-only-trust — the owner's trust in its labels.

    POST trusts the server's read-only labels, or reviews them once it lists tools that changed.
    The body names ``tools``: each tool the Tools page showed, with the digest of its definition it
    showed (``readOnlyTrust.listed``). Each one the server's listing still defines that way is
    sealed (`mcp_read_only_trust.seal`); one that changed since the page read it is not, and still
    asks. The server's live connections are then closed, so each one's next use lists its tools as
    she reviewed them. Without ``"confirm": true`` the answer is ``400 confirmation_required`` with
    the sentence saying which tools will run without asking. ``409 mcp_tools_not_listed`` while no
    listing of the server as it is defined now is known.

    DELETE — stop trusting its labels: every one of its tools asks again. No question.
    """
    from personalclaw import mcp_grants, mcp_read_only_trust, mcp_status
    from personalclaw.dashboard.handlers.mcp import _SERVER_NAME_RE
    from personalclaw.mcp_client import close_connections
    from personalclaw.mcp_discovery import list_servers

    name = request.match_info["name"].strip()
    if not _SERVER_NAME_RE.fullmatch(name):
        return json_error("bad_request", message=f"{name!r} is not an MCP server name.", status=400)
    server = next((s for s in list_servers(include_disabled=True) if s.name == name), None)
    if server is None:
        return json_error(
            "not_found", message=f"No MCP server named '{name}' is configured.", status=404
        )
    if mcp_grants.exempt(server):
        return json_error(
            "bad_request",
            message=(
                f"“{name}” is PersonalClaw's own server: its tools say what they do themselves, so "
                "there is no trust to give or take."
            ),
            status=400,
        )
    caller = request.get("user", "dashboard")
    if request.method == "DELETE":
        mcp_read_only_trust.revoke(name)
        mcp_status.announce(name)
        sel().log_api_access(
            caller=caller, operation="mcp_read_only_distrust", outcome="completed", resources=name
        )
        return web.json_response({"ok": True, "name": name, "trusted": False})

    body = await json_object_body(request, empty_ok=False)
    seen = _seen_tools(body)
    if isinstance(seen, web.Response):
        return seen
    if server.status != "ok":
        return json_error(
            "mcp_tools_not_listed",
            message=(
                f"PersonalClaw has not listed “{name}”'s tools as it is defined now, so there is "
                "nothing to trust yet. Reconnect it, then trust its labels."
            ),
            status=409,
        )
    if not confirm_granted(body):
        return consent_required(
            f"mcp.servers.{name}.readOnlyTrust",
            consent(name, _read_only_names(server.tools, seen)),
            title="Trust this MCP server's read-only labels?",
        )
    sealed = mcp_read_only_trust.seal(name, server.tools, seen)
    # The tools she reviewed are what the server's last start listed. A connection an agent holds
    # listed its tools when it started, maybe before the server changed them: closed, the next use
    # starts it again and lists them as she reviewed them, instead of asking about a listing older
    # than the one she trusted.
    close_connections(lambda n: n == name)
    mcp_status.announce(name)
    sel().log_api_access(
        caller=caller,
        operation="mcp_read_only_trust",
        outcome="completed",
        resources=f"{name}: {len(sealed.sealed)} tools",
    )
    return web.json_response(
        {
            "ok": True,
            "name": name,
            "sealed": list(sealed.sealed),
            # Reviewed, and defined differently by the time the review landed: they still ask.
            "changedSince": list(sealed.changed_since),
            "readOnlyTrust": read_only_trust_of(server),
        }
    )
