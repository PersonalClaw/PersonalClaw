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
                        "description": "Where to save: 'global' (default, all workspaces) or 'workspace' (active workspace only)",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace name (required when scope='workspace'). Use the workspace name from your session context.",
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
                    "query": {"type": "string", "description": "What to recall (a topic, name, or question)"},
                    "deep": {"type": "boolean", "description": "Broader/deeper search (default false)"},
                },
                "required": ["query"],
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
            lines.append(f"[{le.get('category', '?')}] {le['rule']}")
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

    return f"Unknown tool: {name}"


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
        name, raw_args, _validate_args, _call_tool_inner,
        session_key="mcp_core", downstream_service="personalclaw-memory",
    )
