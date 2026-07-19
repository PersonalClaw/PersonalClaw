"""Workflows tool category — operate the Workflows entity (SOPs) as a native tool group.

One of the cohesive native tool-provider categories. list/get/run surface + author/promote the
ordered playbooks the user maintains; ``workflow_create`` auto-binds an agent/session-
scoped SOP to the live owner so a narrow-scoped workflow can actually match.

Exposes ``_list_tools`` / ``_call_tool`` (the same shape as ``mcp_core`` / ``mcp_schedule``)
so the in-process ``InProcessMcpToolProvider`` and the aggregating ``mcp-core`` MCP server
both consume it through one path. The HTTP/session/agent plumbing (``_get`` / ``_post`` /
``_resolve_session_key`` / ``_CURRENT_AGENT_ID``) is owned by ``mcp_core`` and reused here.
"""

import os
import urllib.parse
from typing import Any

from personalclaw.mcp_core import (
    _CURRENT_AGENT_ID,
    _get,
    _post,
    _resolve_session_key,
)


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "workflow_list",
            "description": (
                "List the workflow SOPs (standard operating procedures) available "
                "to you — defined, ordered playbooks the user maintains for recurring "
                "tasks. Matching SOPs are also auto-injected as guidance when the turn "
                "matches one; use this to see the full catalog, confirm a workflow "
                "exists, or recall its steps on demand. Read-only. Filter by 'scope' "
                "(global/workspace/agent/session) or 'tag'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["global", "workspace", "agent", "session"],
                        "description": "Only list workflows in this scope",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Only list workflows carrying this tag",
                    },
                },
            },
        },
        {
            "name": "workflow_get",
            "description": (
                "Retrieve one workflow SOP in full — its description and every "
                "ordered step (title + instruction). Use this to recall the exact "
                "procedure for a known workflow before following it. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "The workflow id or name (from workflow_list).",
                    },
                },
                "required": ["workflow_id"],
            },
        },
        {
            "name": "workflow_run",
            "description": (
                "Load a workflow SOP as the procedure to follow for the current "
                "task. Workflows are guidance, not executable code: this returns the "
                "ordered steps as an actionable checklist for you to carry out with "
                "your other tools, in order. Use when a defined playbook covers what "
                "the user asked for."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "The workflow id or name (from workflow_list) to follow.",
                    },
                },
                "required": ["workflow_id"],
            },
        },
        {
            "name": "prompt_render",
            "description": (
                "Load a saved Prompt and render it with variable values filled in, "
                "returning the final prompt text for you to act on. Saved Prompts are "
                "reusable, parameterized instructions the user maintains (with "
                "{{variable}} placeholders). Use when a defined prompt covers what you "
                "need — e.g. to follow a standard report/checklist procedure on demand "
                "for a specific subject. Pass values for the prompt's variables in "
                "'vars'. Read-only: this returns the rendered text; you then carry it "
                "out with your other tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt_id": {
                        "type": "string",
                        "description": "The saved prompt name to render.",
                    },
                    "vars": {
                        "type": "object",
                        "description": "Values for the prompt's {{variable}} placeholders (name → value).",
                    },
                },
                "required": ["prompt_id"],
            },
        },
        {
            "name": "workflow_create",
            "description": (
                "Author a new workflow SOP — an ordered, reusable playbook for a "
                "recurring task. Capture a procedure you've worked out so it can be "
                "recalled or auto-surfaced later. Choose the narrowest scope that "
                "fits: 'session' (this chat only), 'agent' (this agent), 'workspace' "
                "(this project dir), or 'global'. Provide 'match_text' (a natural-"
                "language description of when this SOP applies) so it can be matched "
                "to future turns."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Lowercase handle, e.g. 'release-checklist' (^[a-z0-9][a-z0-9-]{0,62}$).",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line summary of what this workflow accomplishes.",
                    },
                    "steps": {
                        "type": "array",
                        "description": "Ordered steps. Each: {title, instruction?}.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Imperative step line (e.g. 'Run the tests')"},
                                "instruction": {"type": "string", "description": "Optional how-to detail for the step"},
                            },
                            "required": ["title"],
                        },
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "workspace", "agent", "session"],
                        "description": "Visibility/promotion scope (default: session).",
                    },
                    "match_text": {
                        "type": "string",
                        "description": "Natural-language intent this SOP answers (used for auto-surfacing).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for filtering.",
                    },
                },
                "required": ["name", "steps"],
            },
        },
        {
            "name": "workflow_promote",
            "description": (
                "Widen a workflow's visibility once it has proven useful. Scope only "
                "moves UP the ladder: session → agent → workspace → global. Use this "
                "to graduate an SOP you first captured for one chat so it applies to "
                "this agent, this project, or everywhere. Promoting to 'workspace' "
                "needs a scope_ref (the project dir); 'global' clears it."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "The workflow id (from workflow_list) to promote.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["agent", "workspace", "global"],
                        "description": "The wider target scope (must be above the current one).",
                    },
                    "scope_ref": {
                        "type": "string",
                        "description": "Required for 'workspace' (the project dir). Omit for 'global'; reused for 'agent'.",
                    },
                },
                "required": ["workflow_id", "scope"],
            },
        },
    ]


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    if name == "workflow_list":
        qs = []
        scope = (args.get("scope") or "").strip()
        if scope:
            qs.append(f"scope={urllib.parse.quote(scope)}")
        tag = (args.get("tag") or "").strip()
        if tag:
            qs.append(f"tag={urllib.parse.quote(tag)}")
        path = "/api/workflows" + (f"?{'&'.join(qs)}" if qs else "")
        d = _get(path)
        if d.get("error"):
            return f"Error: {d['error']}"
        workflows = [w for w in d.get("workflows", []) if w.get("enabled", True)]
        if not workflows:
            return "No workflows defined." + (
                f" (filter: scope={scope or '*'}, tag={tag or '*'})" if (scope or tag) else ""
            )
        lines: list[str] = []
        for w in workflows:
            scope_str = w.get("scope", "global")
            ref = w.get("scope_ref") or ""
            scope_disp = f"{scope_str}:{ref}" if ref else scope_str
            tags = w.get("tags") or []
            tag_disp = f"  [{', '.join(tags)}]" if tags else ""
            lines.append(f"• {w.get('name', '?')} ({scope_disp}){tag_disp}")
            desc = (w.get("description") or "").strip()
            if desc:
                lines.append(f"    {desc}")
            for i, step in enumerate(w.get("steps", []), 1):
                title = (step.get("title") or "").strip()
                if title:
                    lines.append(f"    {i}. {title}")
        return "\n".join(lines)

    if name in ("workflow_get", "workflow_run"):
        wid = (args.get("workflow_id") or "").strip()
        if not wid:
            return "Error: workflow_id is required."
        d = _get(f"/api/workflows/{urllib.parse.quote(wid)}")
        # The LLM naturally passes the NAME (that's all workflow_list surfaces — the
        # opaque wf-<hash> id is never shown), but the GET route keys off the id and
        # 404s on a name. Resolve name→id via the list so a by-name reference works —
        # matching prompt_render, whose sibling accepts the prompt NAME. Without this,
        # `workflow_run` on a named workflow 404s and the run silently fails.
        if d.get("error"):
            listing = _get("/api/workflows")
            match = None
            if not listing.get("error"):
                for w in listing.get("workflows", []):
                    if (w.get("name") or "").strip() == wid:
                        match = w.get("id")
                        break
            if match:
                d = _get(f"/api/workflows/{urllib.parse.quote(match)}")
        if d.get("error"):
            return f"Error: {d['error']}"
        steps = d.get("steps", [])
        header = (
            f"Following workflow '{d.get('name', wid)}' — carry out these steps in order:"
            if name == "workflow_run"
            else f"Workflow '{d.get('name', wid)}':"
        )
        lines = [header]
        desc = (d.get("description") or "").strip()
        if desc:
            lines.append(desc)
        for i, step in enumerate(steps, 1):
            title = (step.get("title") or "").strip()
            lines.append(f"  {i}. {title}")
            instr = (step.get("instruction") or "").strip()
            if instr:
                lines.append(f"     {instr}")
        return "\n".join(lines)

    if name == "prompt_render":
        pid = (args.get("prompt_id") or "").strip()
        if not pid:
            return "Error: prompt_id is required."
        variables = args.get("vars") or {}
        if not isinstance(variables, dict):
            return "Error: 'vars' must be an object (variable name → value)."
        d = _post(
            f"/api/prompts/{urllib.parse.quote(pid)}/render",
            {"variables": variables},
        )
        if d.get("error"):
            return f"Error: {d['error']}"
        rendered = (d.get("rendered") or "").strip()
        if not rendered:
            return f"Error: prompt {pid!r} rendered empty."
        return (
            f"Rendered prompt '{pid}' — carry out the following:\n\n{rendered}"
        )

    if name == "workflow_create":
        wf_name = (args.get("name") or "").strip()
        if not wf_name:
            return "Error: name is required."
        raw_steps = args.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            return "Error: at least one step is required."
        steps = [
            {"title": (s.get("title") or "").strip(), "instruction": (s.get("instruction") or "").strip()}
            for s in raw_steps
            if isinstance(s, dict) and (s.get("title") or "").strip()
        ]
        if not steps:
            return "Error: each step needs a title."
        scope = (args.get("scope") or "session").strip()
        # Auto-bind scope_ref to the live owner so a narrow-scoped SOP can actually
        # match (and be cleaned up). The agent never has to know its own session
        # key / binding id. session → current session key; agent → resolved agent
        # id; workspace → cwd; global → none. An explicit scope_ref arg wins.
        scope_ref = (args.get("scope_ref") or "").strip()
        if not scope_ref:
            if scope == "session":
                scope_ref = _resolve_session_key()
            elif scope == "agent":
                scope_ref = _CURRENT_AGENT_ID.get()
            elif scope == "workspace":
                scope_ref = os.environ.get("PERSONALCLAW_WORKSPACE_DIR", "") or os.getcwd()
        if scope == "agent" and not scope_ref:
            return "Error: cannot create an agent-scoped workflow — no current agent id is bound. Use scope 'session' or 'global'."
        body: dict[str, Any] = {
            "name": wf_name,
            "description": (args.get("description") or "").strip(),
            "steps": steps,
            "scope": scope,
            "scope_ref": scope_ref,
            "match_text": (args.get("match_text") or "").strip(),
            "tags": [t for t in (args.get("tags") or []) if isinstance(t, str)],
        }
        d = _post("/api/workflows", body)
        if d.get("error"):
            return f"Error: {d['error']}"
        return f"Created workflow '{d.get('name', wf_name)}' (scope={d.get('scope', 'session')}, {len(steps)} steps)."

    if name == "workflow_promote":
        wid = (args.get("workflow_id") or "").strip()
        target = (args.get("scope") or "").strip()
        if not wid or not target:
            return "Error: workflow_id and scope are required."
        # Auto-fill scope_ref for the common agent/workspace cases so the agent
        # need not know its own binding id / cwd.
        scope_ref = (args.get("scope_ref") or "").strip()
        if not scope_ref:
            if target == "agent":
                scope_ref = _CURRENT_AGENT_ID.get()
            elif target == "workspace":
                scope_ref = os.environ.get("PERSONALCLAW_WORKSPACE_DIR", "") or os.getcwd()
        body = {"scope": target, "scope_ref": scope_ref}
        d = _post(f"/api/workflows/{urllib.parse.quote(wid)}/promote", body)
        if d.get("error"):
            return f"Error: {d['error']}"
        return f"Promoted workflow '{d.get('name', wid)}' to scope={d.get('scope', target)}."

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
        session_key="mcp_core", downstream_service="personalclaw-workflows",
    )
