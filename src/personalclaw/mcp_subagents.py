"""Subagents tool category — spawn + track background subagents as a native tool group.

One of the cohesive native tool-provider categories. ``subagent_run`` fire-and-forget spawns one or
more background subagents (results arrive as completion events); ``subagent_list`` /
``subagent_status`` track them.

Exposes ``_list_tools`` / ``_call_tool`` (the same shape as ``mcp_core`` / ``mcp_schedule``)
so the in-process ``InProcessMcpToolProvider`` and the aggregating ``mcp-core`` MCP server
both consume it through one path. The session/HTTP plumbing (``_resolve_session_key`` —
so a spawn's completions inject back into the parent session — plus ``_get`` / ``_post``)
is owned by ``mcp_core`` and reused here.
"""

from typing import Any

from personalclaw.mcp_core import _get, _post, _resolve_session_key


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "subagent_run",
            "description": (
                "Spawn subagent(s) to run tasks in the background. "
                "Returns immediately — results arrive as [Subagent completion event] "
                "messages in your conversation. For parallel work, use 'tasks' array. "
                "Tasks are automatically batched if they exceed the concurrency limit. "
                "WAIT for all completion events before responding to the user."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Single task description",
                    },
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple tasks to run in parallel",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Agent name for the subagent. Use subagent_list to see available agents.",  # noqa: E501
                    },
                    "agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Agent names corresponding to each task in 'tasks' array",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": "Override tool-call budget for this spawn (default: config or 100)",  # noqa: E501
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Optional absolute path to launch the subagent subprocess in, "
                            "instead of the default sandbox. Enables cwd-relative resource globs "
                            "(.personalclaw/steering, AGENTS.md) to resolve against this directory. "  # noqa: E501
                            "Must be under a configured subagent_cwd_allowed_roots entry "
                            "(default: [~/workspace, ~/workplace]). Applies to all tasks in a batch spawn."  # noqa: E501
                        ),
                    },
                },
            },
        },
        {
            "name": "subagent_list",
            "description": "List all running and completed subagents (read-only, no commands executed)",  # noqa: E501
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "subagent_status",
            "description": (
                "Call with the agent ID from a subagent completion event "
                "to retrieve the full output in the event of truncation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Subagent ID from completion event",
                    },
                },
                "required": ["agent_id"],
            },
        },
    ]


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    if name == "subagent_run":
        # Re-validate to make schema enforcement visible at the extraction point.
        # _call_tool() already validates, but defense-in-depth ensures agent/agents
        # are schema-clean even if the call chain changes.
        from personalclaw.validation import SPAWN_RUN_SCHEMA, validate_tool_args

        args = validate_tool_args(args, SPAWN_RUN_SCHEMA)

        tasks = args.get("tasks")
        task = args.get("task")

        # Support both single task and batch tasks
        if tasks and isinstance(tasks, list):
            task_list = [t for t in tasks if isinstance(t, str) and t.strip()]
        elif task:
            task_list = [task]
        else:
            return "Error: task or tasks is required"

        # Read parent session key so completions inject back into this session.
        parent_session = _resolve_session_key()

        # Fire-and-forget — gateway's SubagentManager queues excess tasks
        # and auto-spawns them as sessions free up.
        agent = args.get("agent") or ""
        agents_list = args.get("agents") or []
        max_turns = args.get("max_turns") or 0
        cwd = args.get("cwd") or ""
        if agents_list and len(agents_list) != len(task_list):
            return f"Error: agents length ({len(agents_list)}) must match tasks length ({len(task_list)})"  # noqa: E501

        agent_ids: list[str] = []
        agent_names: list[str] = []
        errors: list[str] = []
        for i, t in enumerate(task_list):
            a = agents_list[i] if agents_list else agent
            body: dict[str, Any] = {"task": t, "agent": a, "parent_session": parent_session}
            if max_turns:
                body["max_turns"] = max_turns
            if cwd:
                body["cwd"] = cwd
            d = _post("/api/spawn", body)
            if d.get("error"):
                errors.append(f"{t[:60]}: {d['error']}")
                continue
            agent_ids.append(d.get("id", "?"))
            agent_names.append(a)

        spawn_lines: list[str] = []
        if agent_ids:
            spawn_lines.append(
                f"Spawned {len(agent_ids)} subagent(s). Results will arrive as completion events:"
            )
            for aid, a, t in zip(agent_ids, agent_names, task_list):
                label = f"{aid} ({a})" if a else aid
                spawn_lines.append(f"  {label}: {t[:80]}")
        if errors:
            spawn_lines.append(f"\n{len(errors)} task(s) queued (at capacity):")
            for e in errors:
                spawn_lines.append(f"  - {e}")
        if agent_ids:
            spawn_lines.append(
                "\nWait for [Subagent completion event] messages before responding to the user."
            )
        else:
            spawn_lines.append("All tasks queued — results will arrive as completion events.")
        return "\n".join(spawn_lines)

    if name == "subagent_list":
        d = _get("/api/spawn")
        agents = d.get("agents", [])

        def _redact(text: str) -> str:
            from personalclaw.security import redact_credentials, redact_exfiltration_urls

            text, _ = redact_exfiltration_urls(text)
            text, _ = redact_credentials(text)
            return text

        lines: list[str] = []
        if not agents:
            lines.append("No subagents running.")
        else:
            for a in agents:
                status = "done" if a.get("done") else "running"
                err = f" error: {_redact(a['error'])}" if a.get("error") else ""
                progress = ""
                if not a.get("done"):
                    turns = a.get("turns", 0)
                    tool = _redact(a.get("last_tool", ""))
                    elapsed = a.get("elapsed", 0)
                    parts = [f"{elapsed}s"]
                    if turns:
                        parts.append(f"{turns} turns")
                    if tool:
                        parts.append(tool)
                    progress = f" ({', '.join(parts)})"
                lines.append(f"{a['id']}  [{status}]{err}{progress}  {_redact(a['task'])[:60]}")
        # Append configured agent names from AppConfig
        try:
            from personalclaw.config.loader import AppConfig

            names = sorted(n for n in AppConfig.load().agents if n.isascii() and len(n) < 100)
            if names:
                lines.append(f"\nAvailable agents: {', '.join(names)}")
        except Exception:
            pass
        return "\n".join(lines)

    if name == "subagent_status":
        agent_id = args.get("agent_id", "")
        if not agent_id or not agent_id.isalnum():
            return "Error: invalid agent_id"
        d = _get(f"/api/spawn/{agent_id}")
        if d.get("error"):
            return f"Error: {d['error']}"
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        result = d.get("result") or "_No result._"
        result, _ = redact_exfiltration_urls(result)
        result, _ = redact_credentials(result)
        return result

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
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key="mcp_core",
        downstream_service="personalclaw-subagents",
    )
