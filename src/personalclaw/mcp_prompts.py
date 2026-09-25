"""Prompts tool category — render the user's saved, parameterized Prompts.

One of the cohesive native tool-provider categories. Saved Prompts are their own
entity (``/api/prompts``), unrelated to workflows; ``prompt_render`` only lived in
``mcp_workflows`` because both were authored in the same sitting. It is relocated here
so the Prompts surface survives the workflow feature's replacement (WORKFLOWS-V2
Phase 0) — the old module is deleted wholesale in Phase 1, and a tool the user relies
on must not go with it.

Exposes ``_list_tools`` / ``_call_tool`` (the same shape as ``mcp_core`` /
``mcp_schedule``) so the in-process ``InProcessMcpToolProvider`` and the aggregating
``mcp-core`` MCP server both consume it through one path. The HTTP plumbing (``_post``)
is owned by ``mcp_core`` and reused here.
"""

import urllib.parse
from typing import Any

from personalclaw.mcp_core import _post
from personalclaw.tool_providers.base import tool_failure
from personalclaw.validation import decode_json_text


def _list_tools() -> list[dict[str, Any]]:
    return [
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
                    # JSON TEXT: a name → value map has no portable schema, and a strict provider
                    # rejects the whole request over one (tool_providers.portable_schema).
                    "vars": {
                        "type": "string",
                        "description": (
                            "Values for the prompt's {{variable}} placeholders, as JSON text: an "
                            "object mapping each variable name to its value."
                        ),
                    },
                },
                "required": ["prompt_id"],
            },
        },
    ]


def _call_tool(name: str, args: dict[str, Any]) -> str:
    if name == "prompt_render":
        pid = (args.get("prompt_id") or "").strip()
        if not pid:
            return tool_failure("prompt_id is required.")
        variables = decode_json_text(args.get("vars")) or {}
        if not isinstance(variables, dict):
            return tool_failure("'vars' must be a JSON object (variable name → value).")
        d = _post(
            f"/api/prompts/{urllib.parse.quote(pid)}/render",
            {"variables": variables},
        )
        if d.get("error"):
            return tool_failure(f"{d['error']}")
        rendered = (d.get("rendered") or "").strip()
        if not rendered:
            return tool_failure(f"prompt {pid!r} rendered empty.")
        return f"Rendered prompt '{pid}' — carry out the following:\n\n{rendered}"

    return tool_failure(f"unknown prompts tool {name!r}.")
