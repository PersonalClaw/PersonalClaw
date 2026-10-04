"""In-process MCP surface for the `automation_*` tools.

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
own — the executor + `SubagentManager.spawn` do. So an immediate run routes through the gateway's
HTTP `/run` (the shipped `schedule_trigger` pattern the plan's recon note calls out) rather than
executing here; `automation_dry_run` needs no turn and is answered locally.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from personalclaw import lasting_work
from personalclaw.safety_flags import confirm_granted
from personalclaw.tool_providers.base import tool_failure

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


def _chat_channels() -> Any:
    """The chat channels a named one (`via`), or the one an id belongs to, is looked up in:
    ``None``, the registered ones, in the gateway, where the native runtime runs these tools;
    elsewhere (the tool server an agent CLI starts) nothing is registered, so the installed
    channels are built to be asked, as the CLI's ``--channel`` check builds them."""
    from personalclaw.channel_transports import WEBUI_TRANSPORT, list_transports

    if any(key != WEBUI_TRANSPORT for key in list_transports()):
        return None
    from personalclaw.providers.loader import build_channel_transports

    return {transport.name: transport for transport in build_channel_transports()}


def _event_spec_hint() -> str:
    """How an `event` spec is shaped, derived from the pattern table so it cannot drift from it.

    Without it an agent asked for "when a project.acme memory changes" has to guess the keys, and a
    guess the validator refuses costs a round trip for a shape the table already knows.
    """
    from personalclaw.event_triggers import EVENT_PATTERNS, PATTERN_MATCHER

    patterns = ", ".join(
        f"{pattern} ({PATTERN_MATCHER[pattern]})" if PATTERN_MATCHER[pattern] else pattern
        for pattern in EVENT_PATTERNS
    )
    return (
        ' For kind `event`: {"pattern": P} plus the one matcher key P reads, P one of '
        f"{patterns}; the source is derived from the pattern."
    )


def _catch_up_hint() -> str:
    """What `catch_up` does, from the sentences a created automation's announcement uses too."""
    from personalclaw.triggers.tools import CATCH_UP_OFF, CATCH_UP_ON

    return (
        "For an automation that runs at a time or on a schedule. false (the default): "
        f"{CATCH_UP_OFF} true: {CATCH_UP_ON} Set it true only when the owner wants a missed time "
        "run late rather than asked about ('even if my laptop is closed')."
    )


def _list_tools() -> list[dict[str, Any]]:
    """§4's eight-tool namespace. `automation_pause`/`automation_resume` share a handler but are
    separate tools, so an agent reads the intent from the name it called."""
    trigger_id = {"type": "string", "description": "The automation id (e.g. 'file:my-notes')."}
    return [
        {
            "name": "automation_create",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Create an automation from ONE natural-language message. Use for 'when a file "
                "in ~/notes changes', 'every weekday at 9', 'at 5pm', 'when my nightly run "
                "finishes', 'when the research run 9c2c10ab finishes'. The `when` phrase is routed "
                "to the right trigger kind (file/clock/web_watch/run_completed/…) — a time runs it "
                "once at that time, in the owner's timezone, a cadence becomes a repeating "
                "schedule, an event becomes an event trigger, and a run finishing runs it when "
                "that run ends: name the run in `when` by its id, or by the automation's or the "
                "workflow's name. Give `when` + `name` + `message` (what the automation should "
                "do). "
                "When it is to send the owner words they gave ('message me …: …', 'remind me …: "
                "…'), give them in `say` instead of `message`: they go out as written, and no "
                "agent runs. When the owner named the chat channel ('on Telegram'), "
                "give it in `via`: the words in `say`, or what the `message` task produced, go out "
                "there and on no other channel; a chat on it goes in `to`. "
                "For a button or anything the owner runs on demand ('make me a button that "
                "runs …', 'something I can run when I want'), give `kind` 'manual' and no `when`: "
                "it runs only when the owner runs it, and the chat shows it with a Run now button. "
                "A chat widget's button cannot run anything: it only sends its action back here. "
                "When the job writes something ('summarise it into ~/notes/kitchen.md'), name "
                "those files in `changes`: its agent then may change them and nothing else. "
                "Without `changes` its agent only reads. "
                "Announced to you on creation with the time it read, and capped by "
                "workflows.self_schedule_max_outstanding. One that sends `say` is active at once; "
                "one that runs `message` does not run until the owner allows it on the Triggers "
                "page. The result says which, so tell the owner what it says."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A short name for the automation."},
                    "when": {
                        "type": "string",
                        "description": "Plain English for WHEN it runs: one time ('at 5pm', "
                        "'tomorrow at 9am', 'in 20 minutes'), a cadence ('every weekday at 9') or "
                        "an event ('when a file in ~/notes changes').",
                    },
                    "message": {
                        "type": "string",
                        "description": "What the automation should do when it fires.",
                    },
                    "say": {
                        "type": "string",
                        "description": "Words to send the owner each time it fires, as written, "
                        "instead of a `message` for an agent: 'Bins out tonight.'",
                    },
                    "via": {
                        "type": "string",
                        "description": "The chat channel to send `say`, or the `message` "
                        "task's result, on, by its name (e.g. 'telegram'), when the owner named "
                        "one. Only that channel sends it: when it cannot, it goes to the owner's "
                        "Inbox saying why, never to another channel. A name that is not a chat "
                        "channel set up "
                        "here is refused with the ones that are, so you can ask which. Omit it: "
                        "`say` then reaches the owner on the first connected channel that knows "
                        "them, and a `message` task's result reaches them as a notification in "
                        "PersonalClaw.",
                    },
                    "to": {
                        "type": "string",
                        "description": "A chat on the `via` channel to send it to, by that "
                        "channel's own id for it, when the owner named one. Omit it for the "
                        "owner's direct messages there. An id "
                        "the channel does not take is refused in the channel's words, so you can "
                        "ask the owner for it.",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional explicit kind, bypassing NL routing "
                        "(file/clock/event/web_watch/idle/webhook/run_completed, or manual for "
                        "one that runs only when the owner runs it).",
                    },
                    "changes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The files the job changes, each a full path as the "
                        "owner named it ('~/notes/kitchen.md'). Its agent may change these and "
                        "nothing else; leave it out for a job that only reads or reports.",
                    },
                    "catch_up": {
                        "type": "boolean",
                        "description": _catch_up_hint(),
                    },
                    # JSON TEXT: a trigger spec's keys depend on its kind, and a free-form object
                    # has no portable schema (tool_providers.portable_schema) — a strict provider
                    # rejects the whole request over one. The validator decodes it.
                    "spec": {
                        "type": "string",
                        "description": (
                            "Optional explicit trigger spec when `kind` is given, as JSON text "
                            "(one object)." + _event_spec_hint()
                        ),
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "automation_list",
            "annotations": {"readOnlyHint": True},
            # The first sentence is what a turn that defers this schema lists it by (the catalog
            # keeps 100 characters), so it says what the tool reads.
            "description": "The owner's automations: whether each runs now, when it last ran and "
            "how that went, its next run. Read it before telling the owner whether an automation "
            "exists, ran or works: what you remember of one may be out of date. The ones that "
            "need the owner are marked. Optional `kind` and `state` ('active'/'paused') filters. "
            "Broken rows are shown, not hidden.",
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
            "annotations": {"readOnlyHint": False},
            "description": "Patch an automation. Only settable fields apply (name, spec, gates, "
            "workflow, enabled, delivery, failure_delivery, catch_up, …); health/run fields are "
            "rejected and reported. A `workflow` patch edits its action: each setting you send "
            "in its `config` replaces that one, a setting sent as null is removed, and the ones "
            "you leave out stay as saved; naming another provider replaces the action. "
            "`delivery` is where its results go and `failure_delivery` "
            "where its failures go: 'inbox' (a notification in PersonalClaw), 'none' (nothing is "
            "sent), or 'channel:<name>' for the owner's direct messages on a chat channel set up "
            "here ('channel:<name>:<chat id>' for a chat on it); a channel's own name "
            "('telegram') means its direct messages. Any other value is refused with the "
            "channels set up here, so you can ask the owner which. An edit that changes what its "
            "action runs switches it off until the owner allows the change on the Triggers page, "
            "and letting its agent approve its own tool calls is the owner's to change, not "
            "yours. The result says where its results go and whether it runs now, as saved: tell "
            "the owner what it says. `catch_up` (true or false) is what a missed time does: "
            + _catch_up_hint(),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": trigger_id,
                    "patch": {
                        "type": "string",
                        "description": "The fields to change, as JSON text (one object).",
                    },
                },
                "required": ["id", "patch"],
            },
        },
        {
            "name": "automation_pause",
            "annotations": {"readOnlyHint": False},
            "description": "Pause an automation — it stops firing on its own but is not deleted.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": trigger_id},
                "required": ["id"],
            },
        },
        {
            "name": "automation_resume",
            "annotations": {"readOnlyHint": False},
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
            "annotations": {"readOnlyHint": False},
            "description": "Fire an automation now. It fires as the automation fires on its own: "
            "every rule its own fires keep applies (its hourly cap, spacing, quiet hours, budget, "
            "a run still going), the run counts toward its hourly cap and the failure streak that "
            "pauses it, and an automation that is switched off or paused does not fire. A refusal "
            "says which rule held it. automation_dry_run reports what it would run without "
            "executing.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": trigger_id},
                "required": ["id"],
            },
        },
        {
            "name": "automation_dry_run",
            "annotations": {"readOnlyHint": True},
            "description": "Walk an automation's gates and report what automation_run WOULD do, "
            "executing nothing: the gates its fire would keep, and whether a real run would be "
            "refused.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": trigger_id},
                "required": ["id"],
            },
        },
        {
            "name": "automation_history",
            "annotations": {"readOnlyHint": True},
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
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
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
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
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
            # `automation_create` can already build either of these — but an agent
            # scheduling ITSELF is a different act from an agent building the user an automation,
            # and the name is what makes the difference legible in the tool log and in the
            # approval prompt. Both route through `T.create(created_by="agent")`, so they inherit
            # the outstanding-task bound, the command screening and the announcement rather than
            # re-implementing any of it.
            "name": "set_onetime_task",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Schedule YOURSELF to do something ONCE at a later time, then stop. Use when you "
                "need to wait for something outside this turn — 'check the build in 20 minutes', "
                "'follow up tomorrow morning' — and to remind the owner of something at a time "
                "('remind me at 5pm to call Sam': put what to tell them in `message`). When the "
                "owner named the chat channel for it ('remind me on Telegram …'), use "
                "automation_create with `say` and `via` instead: a task cannot choose the channel "
                "its reply reaches them on. The task "
                "wakes you with `message` as the instruction. Counts against your "
                "outstanding-task allowance; it frees a slot when it fires, since a one-time task "
                "disables itself. It does not run until the owner allows it on the Triggers page, "
                "unless it wakes a parked run (`resume_run_id`), which needs no allowing."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "A short name for the task."},
                    "when": {
                        "type": "string",
                        "description": "When to wake, in plain language, read in the owner's "
                        "timezone: 'at 5pm', 'in 20 minutes', 'tomorrow at 9am', "
                        "'2026-09-01 14:00'. One time only — a cadence is set_recurring_task's.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The instruction to give yourself when it fires.",
                    },
                    "resume_run_id": {
                        "type": "string",
                        "description": "Wake a PARKED workflow run instead of starting a new "
                        "task: the run id to resume, or 'self' from inside a workflow stage "
                        "to target your own run. The message becomes the answer to the event "
                        "gate the run is parked on. This is how a monitor run parks between "
                        "checks. It answers no other gate: an approval, a choice or a form "
                        "waits for the owner.",
                    },
                    "ttl_secs": {
                        "type": "number",
                        "description": "How long the task may stay armed before it expires "
                        "(default: 7 days). Every self-scheduled task expires — a forgotten "
                        "clock must not run forever.",
                    },
                },
                "required": ["name", "when", "message"],
            },
        },
        {
            "name": "set_recurring_task",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Schedule YOURSELF to do something REPEATEDLY on a cadence — 'every weekday at "
                "9', 'hourly', 'every Monday'. Use for ongoing monitoring you should keep doing "
                "rather than a single follow-up. Counts against your outstanding-task allowance "
                "for as long as it stays enabled, so pause or delete one you no longer need. It "
                "does not run until the owner allows it on the Triggers page, unless it wakes a "
                "parked run (`resume_run_id`), which needs no allowing."
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
                    "resume_run_id": {
                        "type": "string",
                        "description": "Wake a PARKED workflow run on each fire instead of "
                        "starting new tasks: the run id to resume, or 'self' from inside a "
                        "workflow stage to target your own run.",
                    },
                    "ttl_secs": {
                        "type": "number",
                        "description": "How long the task stays armed before it expires "
                        "(default: 30 days). Every self-scheduled task expires; renew "
                        "deliberately rather than holding a slot forever.",
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

    The id goes into the path percent-encoded, under the `schedule:` namespace, as the dashboard's
    Run button and `personalclaw cron trigger` send it. An app's job name can hold any character,
    and a space in one made the request unsendable.
    """
    from urllib.parse import quote

    from personalclaw.mcp_core import _post

    trigger_id = str(payload.get("trigger_id") or "")
    try:
        return _post(f"/api/triggers/schedule:{quote(trigger_id, safe='')}/run", {})
    except Exception as exc:  # noqa: BLE001 - a failed dispatch is a reported outcome, not a crash
        logger.debug("automation_run HTTP dispatch failed for %s", trigger_id, exc_info=True)
        return f"could not dispatch: {exc}"


def _resolve_resume_target(args: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The `workflow.resume` target a set_*_task call asked for, or (None, "") when it did not.

    `resume_run_id: "self"` resolves from the leaf's lineage (`mcp_shared.leaf_run_id`) — the one
    reader `mcp_shared.leaf_tool_denial` uses too, which sees the stage's lineage whether its tools
    run in an agent CLI's tool server or in-process on the native runtime. Resolved HERE, at
    creation, rather than stored symbolically: a persisted "self" would be re-resolved at fire time
    by whatever process the scheduler runs in, which is never the run it meant.
    """
    raw = str(args.get("resume_run_id") or "").strip()
    if not raw:
        return None, ""
    if raw.lower() == "self":
        from personalclaw.mcp_shared import leaf_run_id

        run_id = leaf_run_id()
        if not run_id:
            return None, tool_failure(
                "resume_run_id='self' only works from inside a workflow run — this "
                "session has no run lineage. Pass the explicit run id instead."
            )
        return {"run_id": run_id}, ""
    return {"run_id": raw}, ""


#: The tools that make an automation or change one, which the work of an Incognito or Temporary
#: chat may not do, nor work someone other than the owner asked for
#: (:mod:`personalclaw.lasting_work`): `triggers.tools` refuses it, and these say so before anyone
#: is asked to allow the call (:func:`_preflight`).
_LASTING_ACTS = {
    "automation_create": lasting_work.CREATE,
    "set_onetime_task": lasting_work.CREATE,
    "set_recurring_task": lasting_work.CREATE,
    "automation_update": lasting_work.CHANGE,
}


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    from personalclaw.triggers import tools as T

    store = _store()
    if name == "automation_create":
        via = str(args.get("via") or "")
        result = T.create(
            store,
            name=str(args.get("name") or ""),
            when=str(args.get("when") or ""),
            kind=str(args.get("kind") or ""),
            spec=args.get("spec") if isinstance(args.get("spec"), dict) else None,
            message=str(args.get("message") or ""),
            say=str(args.get("say") or ""),
            via=via,
            to=str(args.get("to") or ""),
            chat_channels=_chat_channels() if via.strip() else None,
            created_by="agent",
            changes=[str(c) for c in args.get("changes") or [] if isinstance(c, str)],
            catch_up=args.get("catch_up", False),
        )
    elif name == "set_onetime_task":
        resume, resume_err = _resolve_resume_target(args)
        if resume_err:
            return resume_err
        result = T.create(
            store,
            name=str(args.get("name") or ""),
            when=str(args.get("when") or ""),
            message=str(args.get("message") or ""),
            created_by="agent",
            resume=resume,
            ttl_secs=float(args.get("ttl_secs") or 0),
            recurrence=T.ONCE,
        )
    elif name == "set_recurring_task":
        # `cadence` is the caller-facing word (a recurrence, not an instant); `when` is what the
        # NL router takes. Same routing either way — a cadence phrase becomes a cron spec.
        resume, resume_err = _resolve_resume_target(args)
        if resume_err:
            return resume_err
        result = T.create(
            store,
            name=str(args.get("name") or ""),
            when=str(args.get("cadence") or ""),
            message=str(args.get("message") or ""),
            created_by="agent",
            resume=resume,
            ttl_secs=float(args.get("ttl_secs") or 0),
            recurrence=T.RECURRING,
        )
    elif name == "automation_list":
        result = T.list_automations(
            store, kind=str(args.get("kind") or ""), state=str(args.get("state") or "")
        )
    elif name == "automation_update":
        patch = args.get("patch")
        if not isinstance(patch, dict):
            return tool_failure("'patch' must be an object.")
        result = T.update(
            store,
            trigger_id=str(args.get("id") or ""),
            patch=patch,
            # The channels a `send-message` action's chat channel, or a route, is looked up in.
            chat_channels=_chat_channels() if T.asks_chat_channels(patch) else None,
        )
    elif name == "automation_pause":
        result = T.set_paused(store, trigger_id=str(args.get("id") or ""), paused=True)
    elif name == "automation_resume":
        result = T.set_paused(store, trigger_id=str(args.get("id") or ""), paused=False)
    elif name in ("automation_run", "automation_dry_run"):
        # A dry run is its own tool, never an argument of the run: a call either runs the
        # automation or executes nothing, so what it declares is what it does.
        dry = name == "automation_dry_run"
        # An agent asks for this run, so it is the automation firing, not a run by hand: the route
        # it posts to admits it as a fire, and the plan it reports says so.
        result = T.run(
            store,
            trigger_id=str(args.get("id") or ""),
            dry_run=dry,
            runner=None if dry else _http_runner,
            yours=False,
        )
    elif name == "automation_history":
        result = T.history(store, trigger_id=str(args.get("id") or ""), n=int(args.get("n") or 10))
    elif name == "automation_delete":
        result = T.delete(
            store, trigger_id=str(args.get("id") or ""), confirm=confirm_granted(args)
        )
    elif name == "automation_delete_all":
        # `created_by` is NOT taken from the args. The scope is the caller's identity, and an agent
        # able to pass `created_by="user"` could mass-delete the automations the human built — which
        # is precisely the access control the retired `schedule_remove_all` enforced.
        result = T.delete_all(store, created_by="agent", confirm=confirm_granted(args))
    else:
        return tool_failure(f"unknown automation tool {name!r}.")

    # The tool's own text is the agent-facing message; the structured data rides in a trailing
    # JSON line for a surface that wants it, matching how the other category modules answer.
    text = result.text
    if result.data:
        text = f"{text}\n\n<automation-data>{json.dumps(result.data)}</automation-data>"
    # A refusal says so by its type (#3487), which the bridge, the SEL and the chat's tool card
    # read. As a bare string it reached all three as a success.
    return text if result.ok else tool_failure(text.removeprefix("Error: "))


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate against the shared MCP schema; unschem'd tools pass through unchanged."""
    from personalclaw.validation import MCP_AUTOMATION_SCHEMAS, validate_tool_args

    schema = MCP_AUTOMATION_SCHEMAS.get(name)
    return validate_tool_args(args, schema) if schema else args


def _preflight(name: str, raw_args: dict[str, Any]) -> Any:
    """What these tools refuse before anyone is asked to approve a call: a tool this leaf may not
    call, and arguments the tool's schema refuses (``mcp_shared.preflight_refusal``), then an
    automation made or changed for the work of an Incognito or Temporary chat, or on someone
    else's say-so (:data:`_LASTING_ACTS`)."""
    from personalclaw.mcp_shared import preflight_refusal

    refused = preflight_refusal(name, raw_args, _validate_args)
    if refused is not None or name not in _LASTING_ACTS:
        return refused
    why = lasting_work.refused(lasting_work.AUTOMATION, _LASTING_ACTS[name])
    return tool_failure(str(why), code=why.code) if why is not None else None


def _answered(name: str, args: dict[str, Any]) -> str:
    """A call, its refusal by `triggers.tools` included: an automation made or changed for the work
    of an Incognito or Temporary chat, or on someone else's say-so, is answered as the tool's error,
    in the refusal's words and under its code."""
    try:
        return _call_tool_inner(name, args)
    except lasting_work.Refused as refused:
        return tool_failure(str(refused), code=refused.code)


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _answered,
        session_key="mcp_automation",
        downstream_service="personalclaw-automation",
    )
