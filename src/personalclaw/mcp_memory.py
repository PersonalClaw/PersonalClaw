"""Memory tool category — persistent lessons + on-demand recall as a native tool group.

One of the cohesive native tool-provider categories. Save/list/forget durable lessons and recall
query-relevant facts from persistent memory.

Exposes ``_list_tools`` / ``_call_tool`` (the same shape as ``mcp_core`` / ``mcp_schedule``)
so the in-process ``InProcessMcpToolProvider`` and the aggregating ``mcp-core`` MCP server
both consume it through one path. The HTTP plumbing (``_get`` / ``_post`` / ``_delete``)
is owned by ``mcp_core`` and reused here.
"""

import urllib.parse
from typing import Any

from personalclaw.mcp_core import _delete, _get, _post


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "memory_remember",
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
                        "enum": ["tool", "preference", "knowledge"],
                        "description": "Category: tool, preference, or knowledge",
                    },
                    "negative": {
                        "type": "string",
                        "description": "What NOT to do (optional)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "workspace"],
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
            "description": "List all saved lessons and corrections",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "memory_forget",
            "description": "Remove lessons whose rule contains the given substring",
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
            "description": (
                "Look up your persistent memory on demand — query-relevant facts "
                "and past conversation fragments. Your always-on context only "
                "carries a small manifest of your most-used facts; call this when "
                "you need to recall something specific the user told you before, "
                "or context from an earlier session. Set deep=true for a broader, "
                "deeper search."
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
            "name": "triage_rules",
            "description": (
                "List, add, or revoke the triage approval rules — what the proactive "
                "digest may do without asking again. action='list' shows every rule "
                "with its hit count and where it came from; action='add' needs a "
                "pattern (like 'archive:sender:noreply.github.com') and a verdict "
                "('approve' or 'deny'); action='revoke' needs the rule id from list. "
                "A deny rule always beats an approve rule, so adding a deny is the "
                "safe way to stop a class of proposal."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "revoke"],
                        "description": "list | add | revoke",
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
                        "enum": ["approve", "deny"],
                        "description": "approve = auto-execute, deny = silently skip (add only)",
                    },
                    "id": {
                        "type": "string",
                        "description": "The rule id (user.approval.*) to revoke",
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
            return "Error: rule is required"
        scope = args.get("scope", "global")
        payload: dict[str, str] = {"rule": rule, "category": category, "scope": scope}
        if scope == "workspace":
            ws = args.get("workspace", "")
            if not ws:
                return "Error: workspace name is required when scope='workspace'"
            payload["workspace"] = ws
        d = _post("/api/lessons", payload)
        err_val = d.get("error")
        if err_val:
            # Map the backend session-scope error to a user-actionable
            # message so the LLM can explain the situation instead of
            # leaking an opaque HTTP 400 as a "transport failed" error.
            # See api_lessons_create in dashboard/handlers/schedule.py: the
            # "unknown session" response is returned when the X-Session-Key
            # matches neither a live in-memory session, a restricted key, the
            # channel: namespace, nor a persisted session JSONL — so the
            # remaining cases are genuinely unrecognised keys (forged, or
            # ephemeral/incognito sessions that never wrote to disk), not
            # merely evicted real sessions.
            if "unknown session" in str(err_val):
                return (
                    "Lesson was NOT saved: this session is not recognised "
                    "by the gateway (no active session, restricted key, or "
                    "persisted history found for this session key). Start "
                    "a new channel thread or dashboard tab and re-state the "
                    "lesson you want to save — it will not carry over "
                    "from this session automatically."
                )
            return f"Error: {err_val}"
        return f"Saved lesson ({scope}): {rule}"

    if name == "memory_list":
        d = _get("/api/lessons")
        lessons = d.get("lessons", [])
        if not lessons:
            return "No lessons saved."
        lines = []
        for le in lessons:
            # This is the INVENTORY (every scope), so a workspace-scoped lesson is
            # labeled with the directory it belongs to. Listing it unlabeled beside
            # global rules would present a project-local rule as a universal one —
            # the same confusion the scope exists to prevent, one surface over.
            ws = le.get("workspace") or ""
            suffix = f" (workspace: {ws})" if le.get("scope") == "workspace" and ws else ""
            lines.append(f"[{le.get('category', '?')}] {le['rule']}{suffix}")
        return "\n".join(lines)

    if name == "memory_forget":
        query = args["query"]
        d = _delete("/api/lessons", {"rule": query})
        if d.get("error"):
            return f"Error: {d['error']}"
        return f"Removed lessons matching: {query}"

    if name == "memory_recall":
        query = (args.get("query") or "").strip()
        if not query:
            return "Error: query is required"
        qs = f"q={urllib.parse.quote(query)}"
        if args.get("deep"):
            qs += "&deep=true"
        d = _get(f"/api/memory/recall?{qs}")
        if d.get("error"):
            return f"Error: {d['error']}"
        return d.get("result", "No matching memory found.")

    if name == "triage_rules":
        return _triage_rules(args)

    return f"Unknown tool: {name}"


def _triage_rules(args: dict[str, Any]) -> str:
    """The approval-memory management surface (PROACTIVE-ASSISTANT §4).

    Every branch is explicit and an unknown action is an error, not a fallthrough
    to `list` — a mistyped action must not silently read as the harmless one, or a
    typo'd `add` reports success while teaching nothing.
    """
    action = str(args.get("action") or "").strip().lower()

    if action == "list":
        d = _get("/api/memory/approval-rules")
        if d.get("error"):
            return f"Error: {d['error']}"
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

    if action == "add":
        pattern = str(args.get("pattern") or "").strip()
        verdict = str(args.get("verdict") or "").strip().lower()
        if not pattern:
            return "Error: pattern is required to add a rule"
        if verdict not in ("approve", "deny"):
            return "Error: verdict must be 'approve' or 'deny'"
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
            return f"Error: {d['error']}"
        rule = d.get("rule") or {}
        return f"Added {verdict} rule for {pattern} (id: {rule.get('key', '?')})"

    if action == "revoke":
        rule_id = str(args.get("id") or "").strip()
        if not rule_id:
            return "Error: id is required to revoke a rule (get it from action='list')"
        d = _delete(f"/api/memory/approval-rules/{urllib.parse.quote(rule_id)}", {})
        if d.get("error"):
            return f"Error: {d['error']}"
        return f"Revoked rule {rule_id}"

    return f"Error: unknown action {action!r} — use list, add, or revoke"


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against the shared MCP schema; unschem'd tools pass through."""
    from personalclaw.validation import MCP_CORE_SCHEMAS, validate_tool_args

    schema = MCP_CORE_SCHEMAS.get(name)
    if schema:
        return validate_tool_args(args, schema)
    return args


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
