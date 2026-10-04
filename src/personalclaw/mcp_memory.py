"""Memory tool category — persistent lessons + on-demand recall as a native tool group.

One of the cohesive native tool-provider categories. Save/list/forget durable lessons, recall
query-relevant facts from persistent memory, and search what was said in the user's earlier chats
(``chat_search``, :mod:`personalclaw.chat_recall`).

Exposes ``_list_tools`` / ``_call_tool`` (the same shape as ``mcp_core`` / ``mcp_schedule``)
so the in-process ``InProcessMcpToolProvider`` and the aggregating ``mcp-core`` MCP server
both consume it through one path. The HTTP plumbing (``_get`` / ``_post`` / ``_delete``)
is owned by ``mcp_core`` and reused here.
"""

import urllib.parse
from typing import Any

from personalclaw.mcp_core import GATEWAY_READ_TIMEOUT_SECS, _delete, _get, _post
from personalclaw.safety_flags import yes_or_no
from personalclaw.tool_providers.base import tool_failure
from personalclaw.validation import ALLOWED_LESSON_CATEGORIES, ALLOWED_LESSON_SCOPES


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "memory_remember",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Save a learned correction or preference that persists across all "
                "future sessions. MUST be called when the user corrects you, says "
                "'always do X', 'never do Y', or 'remember that'. Include both "
                "the rule (what to do) and negative (what not to do)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rule": {"type": "string", "description": "The lesson to remember"},
                    "category": {
                        "type": "string",
                        "enum": sorted(ALLOWED_LESSON_CATEGORIES),
                        "description": "Category: tool, preference, or knowledge",
                    },
                    "negative": {
                        "type": "string",
                        "description": "What NOT to do (optional)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": sorted(ALLOWED_LESSON_SCOPES),
                        "description": "Where to save: 'global' (default, all workspaces) or 'workspace' (active workspace only)",  # noqa: E501
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Absolute working-directory path (required when scope='workspace'). Copy it verbatim from the WORKSPACE IDENTITY block in your session context — a relative name or a bare project name is refused, because a workspace lesson is matched to a directory exactly.",  # noqa: E501
                    },
                },
                "required": ["rule", "category"],
            },
        },
        {
            "name": "memory_list",
            "annotations": {"readOnlyHint": True},
            "description": (
                "List all saved lessons and corrections: those every chat keeps, and those the "
                "chats working in a folder keep in that folder's memory, each named by its folder"
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "memory_forget",
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
            "description": (
                "Remove lessons whose rule contains the given substring, from every chat's "
                "memory and from each folder's"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Substring to match"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "memory_recall",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Look up your persistent memory on demand — query-relevant facts "
                "and past conversation fragments. Your always-on context only "
                "carries a small manifest of your most-used facts; call this when "
                "you need to recall something specific the user told you before, "
                "or context from an earlier session. Set deep=true for a broader, "
                "deeper search. For what was actually said in an earlier chat, use "
                "chat_search."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to recall (a topic, name, or question)",
                    },
                    "deep": {
                        "type": "boolean",
                        "description": "Broader/deeper search (default false)",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "chat_search",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Search what was said in the user's earlier chats with you. Returns each "
                "matching chat's title, when it started and was last active, the turns that "
                "say it, and where to open it. Use it whenever the answer may be in an earlier "
                "conversation: a handoff, a standup, a weekly review or any summary of recent "
                "work, or a question about something you and the user discussed, decided or "
                "worked through before. It never returns this chat, an Incognito chat or a "
                "Temporary chat, and from a Temporary chat it searches nothing."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Words said in the chat: a topic, a name, an error, a ticket id"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "The most chats to return (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "triage_rules_list",
            "annotations": {"readOnlyHint": True},
            "description": (
                "List the triage approval rules — what the proactive digest may do "
                "without asking again. Shows every rule with its verdict, pattern, hit "
                "count, where it came from, scope and expiry, and the id triage_rules "
                "revokes it by."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "triage_rules",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Add or revoke a triage approval rule — what the proactive digest may do "
                "without asking again. action='add' needs a pattern (like "
                "'archive:sender:noreply.github.com') and the verdict 'deny'; "
                "action='revoke' needs the rule id from triage_rules_list. A deny rule "
                "always beats an approve rule, so adding a deny is the safe way to stop "
                "a class of proposal. Only the owner teaches an approve rule, by "
                "answering the digest: an agent cannot approve work ahead of time."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "revoke"],
                        "description": "add | revoke",
                    },
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Colon-delimited pattern, narrowest first segment is the "
                            "action type: <action>[:<qualifier>...] (add only)"
                        ),
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["deny"],
                        "description": "deny = silently skip matching proposals (add only)",
                    },
                    "id": {
                        "type": "string",
                        "description": (
                            "The rule id (user.approval.*) to revoke, from triage_rules_list"
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "workspace"],
                        "description": "Where the rule applies (default global)",
                    },
                    "expires_at": {
                        "type": "string",
                        "description": "Optional ISO-8601 expiry; the rule stops matching after it",
                    },
                },
                "required": ["action"],
            },
        },
    ]


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    if name == "memory_remember":
        rule = args.get("rule", "")
        category = args.get("category", "knowledge")
        if not rule:
            return tool_failure("rule is required")
        scope = args.get("scope", "global")
        payload: dict[str, str] = {"rule": rule, "category": category, "scope": scope}
        # The endpoint stores what not to do beside the rule; the tool asks for it, so it
        # has to arrive there.
        if args.get("negative"):
            payload["negative"] = args["negative"]
        if scope == "workspace":
            ws = args.get("workspace", "")
            if not ws:
                return tool_failure("workspace name is required when scope='workspace'")
            payload["workspace"] = ws
        d = _post("/api/lessons", payload)
        # A refusal says why in the gateway's words: an Incognito or Temporary chat's work keeps
        # nothing (a subagent's and a workflow step's included, judged by the chat they work for),
        # and an app's keeps nothing unless the app was given your memory.
        if d.get("error"):
            return tool_failure(f"{d['error']}")
        # Held for the owner's own word, in a turn someone else asked for: the gateway says so.
        if d.get("held"):
            return str(d.get("message") or "")
        return f"Saved lesson ({scope}): {rule}"

    if name == "memory_list":
        # Every memory she has: every chat's, and each folder's own (`?partition=*`).
        d = _get("/api/lessons?partition=*")
        # Work that reads no memory (a Temporary chat's, an app's not given it) is told why, and
        # an unanswered read is a failure: neither is "no lessons".
        if d.get("withheld"):
            return str(d["withheld"])
        if d.get("error"):
            return tool_failure(f"{d['error']}")
        lessons = d.get("lessons", [])
        if not lessons:
            return "No lessons saved."
        lines = []
        for le in lessons:
            # This is the INVENTORY (every scope, every memory), so a workspace-scoped lesson is
            # labeled with the directory it belongs to, and one a folder's memory keeps with that
            # folder. Listing either unlabeled beside global rules would present a project-local
            # rule as a universal one — the same confusion the scope exists to prevent.
            ws = le.get("workspace") or ""
            suffix = f" (workspace: {ws})" if le.get("scope") == "workspace" and ws else ""
            lines.append(f"[{le.get('category', '?')}] {le['rule']}{suffix}{_kept_in(le)}")
        return "\n".join(lines)

    if name == "memory_forget":
        query = args["query"]
        d = _delete("/api/lessons?partition=*", {"rule": query})
        if d.get("error"):
            return tool_failure(f"{d['error']}")
        if d.get("held"):
            return str(d.get("message") or "")
        removed = d.get("removed") or []
        if not removed:
            return f"No lesson matches: {query}"
        where = ", ".join(_memory_named(r) for r in removed)
        return f"Removed lessons matching: {query} (from {where})"

    if name == "memory_recall":
        query = (args.get("query") or "").strip()
        if not query:
            return tool_failure("query is required")
        qs = f"q={urllib.parse.quote(query)}"
        if yes_or_no(args.get("deep")) is True:
            qs += "&deep=true"
        d = _get(f"/api/memory/recall?{qs}")
        if d.get("timed_out"):
            # The gateway did not answer at all. The route bounds its own work and answers a
            # recall that ran out of time in words (`memory_recall_timeout`), passed on below.
            return tool_failure(
                f"memory search did not answer within {GATEWAY_READ_TIMEOUT_SECS:g} s, so "
                "nothing was recalled this time. The memories are intact; ask again in a moment."
            )
        if d.get("error"):
            return tool_failure(f"{d['error']}")
        return d.get("result", "No matching memory found.")

    if name == "chat_search":
        return _chat_search(args)

    if name == "triage_rules_list":
        return _triage_rules_list()

    if name == "triage_rules":
        return _triage_rules(args)

    return f"Unknown tool: {name}"


def _memory_named(row: dict[str, Any]) -> str:
    """The memory a lessons row names, in words: every chat's, or a folder's."""
    folder = str(row.get("folder") or "")
    if not row.get("partition"):
        return "the memory every chat keeps"
    named = f"the memory of {folder}" if folder else "a folder's memory no record names"
    return f"{named}, a folder that is gone" if row.get("folder_gone") else named


def _kept_in(row: dict[str, Any]) -> str:
    """Where a listed lesson is kept, when that is a folder's memory: "" for every chat's."""
    return f" (kept in {_memory_named(row)})" if row.get("partition") else ""


def _chat_search(args: dict[str, Any]) -> str:
    """What the user's earlier chats say about the query, as the gateway finds it for this chat
    (``GET /api/sessions/recall``): the chat asking is not searched, nor an Incognito or a
    Temporary chat."""
    query = str(args.get("query") or "").strip()
    if not query:
        return tool_failure("query is required")
    qs = f"q={urllib.parse.quote(query)}"
    if args.get("limit") is not None:
        qs += f"&limit={int(args['limit'])}"
    d = _get(f"/api/sessions/recall?{qs}")
    if d.get("timed_out"):
        return tool_failure(
            f"chat search did not answer within {GATEWAY_READ_TIMEOUT_SECS:g} s, so nothing was "
            "found this time. The chats are intact; ask again in a moment."
        )
    if d.get("error"):
        return tool_failure(f"{d['error']}")
    return str(d.get("result") or "")


def _triage_rules_list() -> str:
    """Every triage approval rule, one line each. Its own tool because it only reads: a
    tool declares one effect, and a listing that shared a tool with the writes was asked
    about, and refused in Ask mode, as the write it is not."""
    d = _get("/api/memory/approval-rules")
    # Work that reads no memory (a Temporary chat's, an app's not given it) is told why: that is
    # not "no rules".
    if d.get("withheld"):
        return str(d["withheld"])
    if d.get("error"):
        return tool_failure(f"{d['error']}")
    rules = d.get("rules") or []
    if not rules:
        return "No triage approval rules. The digest asks about everything."
    lines = []
    for r in rules:
        provenance = r.get("created_from_digest") or "manual"
        expiry = f", expires {r['expires_at']}" if r.get("expires_at") else ""
        send = ", send-capable" if r.get("send_capable") else ""
        lines.append(
            f"[{r.get('verdict')}] {r.get('pattern')} — {r.get('hit_count', 0)} hits, "
            f"from {provenance}, scope {r.get('scope', 'global')}{expiry}{send} "
            f"(id: {r.get('key')})"
        )
    unreadable = d.get("unreadable") or []
    if unreadable:
        # Surfaced, not swallowed: the matcher ignores these rows, so a user who
        # thinks a rule is live must be told it is not.
        lines.append(f"({len(unreadable)} unreadable rule row(s) ignored: {unreadable})")
    return "\n".join(lines)


def _triage_rules(args: dict[str, Any]) -> str:
    """Add or revoke a triage approval rule.

    Every branch is explicit and an unknown action is an error, never a fallthrough: a
    typo'd `add` must not report success while teaching nothing.
    """
    action = str(args.get("action") or "").strip().lower()

    if action == "add":
        pattern = str(args.get("pattern") or "").strip()
        verdict = str(args.get("verdict") or "").strip().lower()
        if not pattern:
            return tool_failure("pattern is required to add a rule")
        if verdict == "approve":
            # Refused here rather than left to the route's 403, so the reason reaches the agent
            # in words whatever the auth mode (`approval_answer`).
            return tool_failure(
                "only the owner teaches an approve rule, by answering the digest: an agent "
                "cannot approve the digest's work ahead of time. Add a deny rule, or ask them."
            )
        if verdict != "deny":
            return tool_failure("verdict must be 'deny'")
        payload: dict[str, Any] = {
            "pattern": pattern,
            "verdict": verdict,
            "scope": str(args.get("scope") or "global"),
            "created_from_digest": "tool:triage_rules",
        }
        if args.get("expires_at"):
            payload["expires_at"] = str(args["expires_at"])
        d = _post("/api/memory/approval-rules", payload)
        if d.get("error"):
            return tool_failure(f"{d['error']}")
        if d.get("held"):
            return str(d.get("message") or "")
        rule = d.get("rule") or {}
        return f"Added {verdict} rule for {pattern} (id: {rule.get('key', '?')})"

    if action == "revoke":
        rule_id = str(args.get("id") or "").strip()
        if not rule_id:
            return tool_failure("id is required to revoke a rule (get it from triage_rules_list)")
        d = _delete(f"/api/memory/approval-rules/{urllib.parse.quote(rule_id)}", {})
        if d.get("error"):
            return tool_failure(f"{d['error']}")
        if d.get("held"):
            return str(d.get("message") or "")
        return f"Revoked rule {rule_id}"

    return tool_failure(
        f"unknown action {action!r} — use add or revoke (triage_rules_list lists the rules)"
    )


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against the shared MCP schema; unschem'd tools pass through."""
    from personalclaw.validation import MCP_CORE_SCHEMAS, validate_tool_args

    schema = MCP_CORE_SCHEMAS.get(name)
    if schema:
        return validate_tool_args(args, schema)
    return args


def _preflight(name: str, raw_args: dict[str, Any]) -> Any:
    """What these tools refuse before anyone is asked to approve a call: a tool this leaf may not
    call, and arguments the tool's schema refuses (``mcp_shared.preflight_refusal``)."""
    from personalclaw.mcp_shared import preflight_refusal

    return preflight_refusal(name, raw_args, _validate_args)


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key="mcp_core",
        downstream_service="personalclaw-memory",
    )
