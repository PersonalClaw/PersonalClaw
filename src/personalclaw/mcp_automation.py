"""In-process MCP surface for the `automation_*` tools (§4 — S92).

Exposes ``_list_tools`` / ``_call_tool`` (the same shape as ``mcp_core`` / ``mcp_schedule``), so
the native ``InProcessMcpToolProvider`` and the aggregating ``mcp-core`` server both surface these
tools in chat. Without this module `triggers/tools.py` would be a tested library nothing calls —
the present-and-inert defect this whole program keeps finding, and the exact thing S83 warned
about when it deferred criterion 2 for lack of a store to write to.

The tool LOGIC lives in `triggers/tools.py` (pure functions over a `TriggerStore`, driven end to
end in tests without a model). This module is the thin adapter: schema in, store built from
`config_dir()`, `ToolResult.text` out. Keeping the two apart is what let the logic be tested
against the real store while this layer stays a translation with nothing to hide.

**Runner boundary.** `automation_run` needs the LLM turn, which this stdio-shaped surface does not
own — S90's executor + `SubagentManager.spawn` do. So an immediate run routes through the gateway's
HTTP `/run` (the shipped `schedule_trigger` pattern the plan's recon note calls out) rather than
executing here; a `dry_run` needs no turn and is answered locally.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _store() -> Any:
    """The shared trigger store, rooted at the active home.

    Built per call rather than cached: MCP tools run in a separate process writing the same
    `triggers.json`, and the store's own mtime-`_sync` is what keeps a long-lived handle honest.
    A module-level singleton would serve a stale view after another process wrote.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=config_dir())


def _list_tools() -> list[dict[str, Any]]:
    """§4's eight-tool namespace. `automation_pause`/`automation_resume` share a handler but are
    separate tools, so an agent reads the intent from the name it called."""
    trigger_id = {"type": "string", "description": "The automation id (e.g. 'file:my-notes')."}
    return [
        {
            "name": "automation_create",
            "description": (
                "Create an automation from ONE natural-language message. Use for 'when a file "
                "in ~/notes changes', 'every weekday at 9', 'when my nightly run finishes'. The "
                "`when` phrase is routed to the right trigger kind (file/clock/web_watch/…) — a "
                "cadence becomes a cron schedule, an event becomes an event trigger. Give `when` "
                "+ `name` + `message` (what the automation should do). Announced to you on "
                "creation, and capped by workflows.self_schedule_max_outstanding."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A short name for the automation."},
                    "when": {
                        "type": "string",
                        "description": "Plain English for WHEN it runs: a cadence ('every "
                        "weekday at 9') or an event ('when a file in ~/notes changes').",
                    },
                    "message": {
                        "type": "string",
                        "description": "What the automation should do when it fires.",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional explicit kind, bypassing NL routing "
                        "(file/clock/event/web_watch/idle/webhook/run_completed).",
                    },
                    "spec": {
                        "type": "object",
                        "description": "Optional explicit trigger spec when `kind` is given.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "automation_list",
            "description": "List automations with health rollups. Optional `kind` and `state` "
            "('active'/'paused') filters. Broken rows are shown, not hidden.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "state": {"type": "string", "enum": ["active", "paused"]},
                },
            },
        },
        {
            "name": "automation_update",
            "description": "Patch an automation. Only settable fields apply (name, spec, gates, "
            "workflow, enabled, delivery, …); health/run fields are rejected and reported.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": trigger_id,
                    "patch": {"type": "object", "description": "Fields to change."},
                },
                "required": ["id", "patch"],
            },
        },
        {
            "name": "automation_pause",
            "description": "Pause an automation — it stops firing on its own but is not deleted.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": trigger_id},
                "required": ["id"],
            },
        },
        {
            "name": "automation_resume",
            "description": "Resume a paused automation. Refuses (with the reason) if the row has "
            "a parse error that must be fixed first.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": trigger_id},
                "required": ["id"],
            },
        },
        {
            "name": "automation_run",
            "description": "Fire an automation now. `dry_run: true` walks the gates and reports "
            "what WOULD run without executing. A manual run bypasses quiet-hours and duty limits "
            "but never the injection screen, capability allowlist, or budget.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": trigger_id,
                    "dry_run": {"type": "boolean", "description": "Observe without executing."},
                },
                "required": ["id"],
            },
        },
        {
            "name": "automation_history",
            "description": "Recent run/fire rows for an automation, with typed outcomes — to "
            "self-debug why an automation did or did not do something.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": trigger_id,
                    "n": {"type": "integer", "description": "How many rows (default 10)."},
                },
                "required": ["id"],
            },
        },
        {
            "name": "automation_delete",
            "description": "Delete an automation permanently. Requires confirm: true — pause it "
            "instead if you might want it back.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": trigger_id,
                    "confirm": {"type": "boolean"},
                },
                "required": ["id", "confirm"],
            },
        },
        {
            "name": "automation_delete_all",
            # Scoped in the DESCRIPTION as well as the code: a bulk-delete tool whose blast radius
            # is only discoverable by reading the implementation is one an agent will misuse.
            "description": "Delete every automation YOU created (created_by=agent), in one call. "
            "Requires confirm: true. Never touches automations the user made.",
            "inputSchema": {
                "type": "object",
                "properties": {"confirm": {"type": "boolean"}},
                "required": ["confirm"],
            },
        },
        {
            # WF2LOO-9. `automation_create` can already build either of these — but an agent
            # scheduling ITSELF is a different act from an agent building the user an automation,
            # and the name is what makes the difference legible in the tool log and in the
            # approval prompt. Both route through `T.create(created_by="agent")`, so they inherit
            # the outstanding-task bound, the command screening and the announcement rather than
            # re-implementing any of it.
            "name": "set_onetime_task",
            "description": (
                "Schedule YOURSELF to do something ONCE at a later time, then stop. Use when you "
                "need to wait for something outside this turn — 'check the build in 20 minutes', "
                "'follow up tomorrow morning'. The task wakes you with `message` as the "
                "instruction. Counts against your outstanding-task allowance; it frees a slot "
                "when it fires, since a one-time task disables itself."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A short name for the task."},
                    "when": {
                        "type": "string",
                        "description": "When to wake, in plain language: 'in 20 minutes', "
                        "'tomorrow at 9am', '2026-09-01 14:00'.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The instruction to give yourself when it fires.",
                    },
                },
                "required": ["name", "when", "message"],
            },
        },
        {
            "name": "set_recurring_task",
            "description": (
                "Schedule YOURSELF to do something REPEATEDLY on a cadence — 'every weekday at "
                "9', 'hourly', 'every Monday'. Use for ongoing monitoring you should keep doing "
                "rather than a single follow-up. Counts against your outstanding-task allowance "
                "for as long as it stays enabled, so pause or delete one you no longer need."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A short name for the task."},
                    "cadence": {
                        "type": "string",
                        "description": "How often, in plain language: 'every weekday at 9', "
                        "'hourly', 'every Monday at 08:00'.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The instruction to give yourself each time it fires.",
                    },
                },
                "required": ["name", "cadence", "message"],
            },
        },
    ]


def _http_runner(payload: dict[str, Any]) -> Any:
    """Fire an automation immediately via the gateway's HTTP `/run`.

    Mirrors `schedule_trigger`: an MCP process cannot own the LLM turn, so an immediate run posts
    to the in-process gateway rather than spawning a subagent here. Returns the response dict, or a
    string describing why it could not — never raises into the tool result.
    """
    from personalclaw.mcp_core import _post

    trigger_id = str(payload.get("trigger_id") or "")
    try:
        return _post(f"/api/triggers/{trigger_id}/run", {})
    except Exception as exc:  # noqa: BLE001 - a failed dispatch is a reported outcome, not a crash
        logger.debug("automation_run HTTP dispatch failed for %s", trigger_id, exc_info=True)
        return f"could not dispatch: {exc}"


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    from personalclaw.triggers import tools as T

    store = _store()
    if name == "automation_create":
        result = T.create(
            store,
            name=str(args.get("name") or ""),
            when=str(args.get("when") or ""),
            kind=str(args.get("kind") or ""),
            spec=args.get("spec") if isinstance(args.get("spec"), dict) else None,
            message=str(args.get("message") or ""),
            created_by="agent",
        )
    elif name == "set_onetime_task":
        result = T.create(
            store,
            name=str(args.get("name") or ""),
            when=str(args.get("when") or ""),
            message=str(args.get("message") or ""),
            created_by="agent",
        )
    elif name == "set_recurring_task":
        # `cadence` is the caller-facing word (a recurrence, not an instant); `when` is what the
        # NL router takes. Same routing either way — a cadence phrase becomes a cron spec.
        result = T.create(
            store,
            name=str(args.get("name") or ""),
            when=str(args.get("cadence") or ""),
            message=str(args.get("message") or ""),
            created_by="agent",
        )
    elif name == "automation_list":
        result = T.list_automations(
            store, kind=str(args.get("kind") or ""), state=str(args.get("state") or "")
        )
    elif name == "automation_update":
        patch = args.get("patch")
        if not isinstance(patch, dict):
            return "Error: 'patch' must be an object."
        result = T.update(store, trigger_id=str(args.get("id") or ""), patch=patch)
    elif name == "automation_pause":
        result = T.set_paused(store, trigger_id=str(args.get("id") or ""), paused=True)
    elif name == "automation_resume":
        result = T.set_paused(store, trigger_id=str(args.get("id") or ""), paused=False)
    elif name == "automation_run":
        dry = bool(args.get("dry_run"))
        result = T.run(
            store,
            trigger_id=str(args.get("id") or ""),
            dry_run=dry,
            runner=None if dry else _http_runner,
        )
    elif name == "automation_history":
        result = T.history(store, trigger_id=str(args.get("id") or ""), n=int(args.get("n") or 10))
    elif name == "automation_delete":
        result = T.delete(
            store, trigger_id=str(args.get("id") or ""), confirm=bool(args.get("confirm"))
        )
    elif name == "automation_delete_all":
        # `created_by` is NOT taken from the args. The scope is the caller's identity, and an agent
        # able to pass `created_by="user"` could mass-delete the automations the human built — which
        # is precisely the access control the retired `schedule_remove_all` enforced.
        result = T.delete_all(store, created_by="agent", confirm=bool(args.get("confirm")))
    else:
        return f"Error: unknown automation tool {name!r}."

    # The tool's own text is the agent-facing message; the structured data rides in a trailing
    # JSON line for a surface that wants it, matching how the other category modules answer.
    if result.data:
        return f"{result.text}\n\n<automation-data>{json.dumps(result.data)}</automation-data>"
    return result.text


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate against the shared MCP schema; unschem'd tools pass through unchanged."""
    from personalclaw.validation import MCP_AUTOMATION_SCHEMAS, validate_tool_args

    schema = MCP_AUTOMATION_SCHEMAS.get(name)
    return validate_tool_args(args, schema) if schema else args


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key="mcp_automation",
        downstream_service="personalclaw-automation",
    )
