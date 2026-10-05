"""Workflows tool category — author, run, steer and inspect composable workflows.

The chat surface over the v2 engine: 23 tools. Every one delegates to
`workflows.service`, the single implementation the REST routes (Slice 7a) will also call —
two surfaces over one engine must not grow two behaviours.

Exposes `_list_tools` / `_call_tool` in the same shape as `mcp_prompts` / `mcp_memory`, so
the in-process `InProcessMcpToolProvider` and the aggregating `mcp-core` server consume it
through one path. In the gateway this category does NOT go over HTTP: the engine is in-process,
and a chat tool that round-tripped through the gateway to reach an object in the same process
would add a failure mode (and a port dependency) for nothing. Being in the process is not being on
the engine's loop, though: these tools run in a worker thread, so a call that reaches a run is
handed to the workflow supervisor's loop and waited for (`_on_engine`), the loop the owner's own
start runs on; a run started on a loop of the call's own stopped as it closed. The one exception
there is a save that would let a step do more (`saved_by_the_gateway`), which only the owner's own
Allow saves and only the gateway can ask her for.

The tool server an agent CLI runs (`mcp-core`, its own process) holds no engine and no
definitions, so there every call is the gateway's, made through its own routes with that process's
internal credential and the chat it serves (`mcp_workflows_gateway`), and answered as it is here.

Two deliberate shapes in the descriptions:

**`workflow_plan` and `workflow_author` are separate tools.** Plan takes a natural-language
goal; author takes a spec. Overloading one name with both contracts was flagged in design
review as a semantic clash — a model cannot tell which contract it is fulfilling.

**Errors come back as readable text with a stable code.** A tool that raises burns the turn
on a traceback the model cannot act on. `WF_DEF_INVALID` plus the issue list is something it
can actually fix and retry.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from personalclaw import approval_answer
from personalclaw.safety_flags import confirm_granted, yes_or_no
from personalclaw.tool_providers.base import ToolFailure, tool_failure
from personalclaw.validation import decode_json_text
from personalclaw.workflows import chat_runs
from personalclaw.workflows import grill_protocol as grill_mod
from personalclaw.workflows import intent as intent_mod
from personalclaw.workflows import rigor as rigor_mod
from personalclaw.workflows import service, template_pipeline, versions
from personalclaw.workflows.context_block import needs_staging, staged_spec_echo

if TYPE_CHECKING:
    from personalclaw.subagent_reach import Reader

logger = logging.getLogger(__name__)

#: Read-only tools — no state change, safe to call while thinking. Kept explicit so a
#: reviewer can see at a glance which tools can be called freely; each one's own
#: `annotations.readOnlyHint` is what every posture acts on, and a test holds the two equal.
READ_ONLY_TOOLS = frozenset(
    {
        "workflow_list_defs",
        "workflow_get_def",
        "workflow_check",
        "workflow_plan",
        "workflow_status",
        "workflow_observe",
        "workflow_output",
        "workflow_edit_preview",
        "workflow_audit",
        "workflow_manifest",
    }
)


def _spec_schema() -> dict[str, Any]:
    """The spec `workflow_author` saves and `workflow_check` checks: one schema, so the check
    is of exactly what a save would take."""
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Definition name: lowercase letters, digits, hyphens.",
            },
            "description": {"type": "string"},
            # `root` and `inputs` are JSON TEXT, not objects: a free-form object has no
            # portable schema, and a strict provider rejects the whole request over one
            # (tool_providers.portable_schema). `validation.decode_json_text` reads it.
            "root": {
                "type": "string",
                "description": (
                    "The root node of the spec tree, as JSON text (one object). Call "
                    "workflow_manifest for the node taxonomy, binding pipes and allowed shapes."
                ),
            },
            "inputs": {
                "type": "string",
                "description": (
                    "Declared inputs, as JSON text: an object mapping each input name to "
                    "{type, required, default, help}."
                ),
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "root"],
    }


def _edit_schema(run_id: dict[str, Any], *, previews: bool) -> dict[str, Any]:
    """The ops `workflow_edit` applies and `workflow_edit_preview` computes the cascade of."""
    properties: dict[str, Any] = {
        "run_id": run_id,
        "ops": {
            "type": "string",
            "description": (
                "The mutation ops, as JSON text: an array of op objects. See "
                "workflow_manifest for the catalog."
            ),
        },
    }
    if not previews:
        # What applying adds to previewing, declared as every other tool's schema is: the version
        # it expects, and the consent to re-run, which only `safety_flags.confirm_granted` reads.
        properties.update(
            {
                "expect_version": {"type": "integer"},
                "confirm_cascade": {
                    "type": "boolean",
                    "description": "Accept re-running completed nodes.",
                },
            }
        )
    return {"type": "object", "properties": properties, "required": ["run_id", "ops"]}


def _list_tools() -> list[dict[str, Any]]:
    run_id = {"type": "string", "description": "The run id (from workflow_start)."}
    return [
        {
            "name": "workflow_author",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Save a workflow definition from an explicit DAG spec — the low-level "
                "authoring tool. Use when you already know the node structure; use "
                "workflow_plan instead to turn a natural-language goal into a spec, and "
                "workflow_check to get the issue list back without saving anything, which is "
                "the cheap way to iterate. Never put a literal API key in the spec: reference "
                "credentials as {{secret:KEY}}. A save that would let a step approve its own "
                "tool calls (approval_mode auto) or change things (capability mutating) where it "
                "did not before waits for your owner's own Allow, asked once; nothing is saved "
                "before they answer."
            ),
            "inputSchema": _spec_schema(),
        },
        {
            "name": "workflow_check",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Check a workflow definition from an explicit DAG spec without saving it: "
                "returns the issue list and writes nothing. Takes the spec workflow_author "
                "saves, so a spec that checks clean is one it will save, or, when "
                "needs_owner_allow names steps that would do more, one it asks your owner to "
                "allow first."
            ),
            "inputSchema": _spec_schema(),
        },
        {
            "name": "workflow_plan",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Turn a natural-language goal into a workflow spec for review BEFORE "
                "anything runs. Returns a draft spec plus its validation issues; nothing is "
                "saved or started, so the user approves first. Use for 'set up a workflow "
                "that…' requests. To save the result, pass it to workflow_author. To turn a "
                "conversation you just had into a workflow, pass source_session_id and the "
                "plan is mined from that transcript's real tool use."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "What the workflow should accomplish, in plain language. Optional "
                            "when source_session_id is given — the session's first user turn "
                            "is then the goal."
                        ),
                    },
                    "source_session_id": {
                        "type": "string",
                        "description": (
                            "Optional: mine an existing chat session. The plan then reports the "
                            "tools that session actually ran and the ones the user DENIED there, "
                            "so the workflow declares a pre-validated permission set instead of "
                            "a guessed one."
                        ),
                    },
                    "rigor": {
                        "type": "string",
                        "enum": ["minimal", "standard", "deep"],
                        "description": "How much structure to propose (default standard).",
                    },
                    "template": {
                        "type": "string",
                        "description": "Optional: a template name to base the plan on.",
                    },
                    "project_id": {
                        "type": "string",
                        "description": (
                            "Optional: a project this plan targets. When it binds an existing "
                            "codebase, the plan is grounded in that project's real layout, README "
                            "and stack so generated stages assume the right conventions."
                        ),
                    },
                },
                # Neither alone: a plan needs a goal OR a session to mine one from. Declaring
                # `goal` required here would make the mining-only call unrepresentable to a
                # schema-validating client, and the handler already names the both-absent case.
                "required": [],
            },
        },
        {
            "name": "workflow_list_defs",
            "annotations": {"readOnlyHint": True},
            "description": (
                "List the available workflow definitions — the user's own plus any bundled "
                "template packs. Read-only. Start here when the user asks what workflows "
                "exist or which one to run."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "Only defs carrying this tag."},
                    "source": {
                        "type": "string",
                        "description": "Filter by origin: 'user' or 'bundled'.",
                    },
                },
            },
        },
        {
            "name": "workflow_get_def",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Retrieve one workflow definition in full, including its node tree and "
                "declared inputs. Read-only. Credential values are replaced by _has_* "
                "presence flags — the definition tells you a key is SET, never what it is."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "workflow_start",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Start a workflow run from a saved definition. mode='background' (default) "
                "returns immediately with a run id — poll with workflow_status or watch with "
                "workflow_observe. mode='blocking' waits for the run to finish and returns "
                "the final state, which suits a short workflow the user is waiting on. Pass "
                "idempotency_key when retrying so a retry returns the EXISTING run instead "
                "of starting a second one."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The definition to instantiate."},
                    "inputs": {
                        "type": "string",
                        "description": (
                            "Values for the definition's declared inputs, as JSON text: an "
                            "object mapping each input name to its value."
                        ),
                    },
                    "mode": {"type": "string", "enum": ["blocking", "background"]},
                    "project_id": {"type": "string", "description": "Optional project binding."},
                    "idempotency_key": {
                        "type": "string",
                        "description": "Caller-chosen key; a retry with the same key is deduped.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "workflow_status",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Current status of a run plus per-node progress and any failure detail. "
                "Read-only. For watching a run that is actively moving, workflow_observe is "
                "cheaper than calling this in a loop."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": run_id},
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_observe",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Watch a run for a short bounded window and return what changed, with the "
                "events from that window. Read-only. Prefer this over repeated "
                "workflow_status calls: one call, one wait, a real delta. The window is "
                "clamped (100ms-30s) and returns early if the run finishes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "duration_ms": {
                        "type": "integer",
                        "description": "How long to watch, in ms (default 5000, max 30000).",
                    },
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_edit",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Edit a RUNNING workflow's unexecuted nodes. Ops: update_node, insert, "
                "delete, move, set_input, skip. Returns a cascade preview naming every node "
                "that would re-run; if it would re-run already-completed work you must "
                "resubmit with confirm_cascade=true. Running and finished nodes cannot be "
                "edited — rewind one first. Pass expect_version from workflow_status to "
                "avoid editing a spec that changed under you. workflow_edit_preview computes "
                "the same cascade and queues nothing. An edit that would let a step approve its "
                "own tool calls or change things, or give such a step new work, is refused: "
                "that goes into the workflow's definition with workflow_author, which asks your "
                "owner."
            ),
            "inputSchema": _edit_schema(run_id, previews=False),
        },
        {
            "name": "workflow_edit_preview",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Compute what workflow_edit would do to a RUNNING workflow — the cascade "
                "naming every node the ops would re-run — and queue NOTHING. Takes the ops "
                "workflow_edit applies."
            ),
            "inputSchema": _edit_schema(run_id, previews=True),
        },
        {
            "name": "workflow_skip",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Skip one or more pending nodes in a running workflow. A skipped node "
                "produces no output and its subtree is skipped with it, so anything binding "
                "its output will fail — skip leaves, or rewind and edit instead."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "node_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["run_id", "node_ids"],
            },
        },
        {
            "name": "workflow_rewind",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Reset a node AND everything that consumes its output, so they re-run — the "
                "in-place fix for 'redo this stage with a better prompt'. Consumers are "
                "found through data bindings, not tree position, so a later sibling reading "
                "the node's output is reset too. Outputs are archived, not destroyed. If a "
                "node in the reset region already completed, inspect the returned preview and "
                "resubmit with confirm_cascade=true. If it already fired an external effect, "
                "also pass redo_effects=true to deliberately fire it again."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "node_id": {"type": "string"},
                    "redo_effects": {"type": "boolean"},
                    "confirm_cascade": {
                        "type": "boolean",
                        "description": "Accept re-running completed nodes.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Re-run even where inputs are unchanged (skips cache).",
                    },
                },
                "required": ["run_id", "node_id"],
            },
        },
        {
            "name": "workflow_run_from",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Re-run only what comes AFTER a node, keeping that node's output as-is — "
                "'redo the synthesis with the same gathered data'. Cheaper than rewind when "
                "the upstream work was expensive and correct. If completed work is in the "
                "cascade, inspect the returned preview and resubmit with confirm_cascade=true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "node_id": {"type": "string"},
                    "confirm_cascade": {
                        "type": "boolean",
                        "description": "Accept re-running completed nodes.",
                    },
                },
                "required": ["run_id", "node_id"],
            },
        },
        {
            "name": "workflow_fork",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Branch a NEW run from this one, leaving the original untouched — for "
                "exploring an alternative when the first result must be preserved. Works on "
                "a finished run. The fork shares the filesystem workspace and any external "
                "resources the original created; the response names exactly what is NOT "
                "isolated. The child starts as a draft — edit it with workflow_edit, then launch "
                "it with workflow_start_draft."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "checkpoint_id": {
                        "type": "string",
                        "description": "Fork from this checkpoint instead of current state.",
                    },
                    "note": {"type": "string", "description": "Why this branch exists."},
                },
                "required": ["run_id"],
            },
        },
        {
            # The other half of `workflow_fork` (#372). Its description promised "the child starts
            # as a draft so you can edit it before running it" and no tool could run it: the nine
            # run verbs all address a run that has already started, and `workflow_start` takes a
            # def NAME, so pointing an agent at it would mint a second run and strand the fork.
            "name": "workflow_start_draft",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Start a run that already exists as a DRAFT — the launch step after "
                "workflow_fork (optionally with workflow_edit in between). Use workflow_start "
                "instead when you want a NEW run from a definition: this one takes a run id and "
                "launches that exact run, keeping the lineage the fork recorded. Refused on a run "
                "that has already launched or finished."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": run_id},
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_pause",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Pause a running workflow: in-flight nodes finish, nothing new launches. "
                "Resume with workflow_resume."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": run_id},
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_resume",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Lift a workflow's pause so it carries on. It answers no gate: a workflow "
                "waiting on a human (an approval, a choice, a form, a plan to review) is "
                "answered by the owner, never by an agent. Tell them it is waiting; they "
                "answer it in PersonalClaw (the Inbox, Home or the run's page)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": run_id},
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_cancel",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Cancel a run. The intent is persisted, so it is honoured even if the "
                "gateway restarts mid-cancel; in-flight nodes are stopped and the run "
                "finalizes as cancelled."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": run_id},
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_output",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Retrieve one node's structured output from a run. Read-only. Use after "
                "workflow_status shows the node is done, to read what it actually produced."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": run_id, "node_id": {"type": "string"}},
                "required": ["run_id", "node_id"],
            },
        },
        {
            "name": "workflow_audit",
            "annotations": {"readOnlyHint": True},
            "description": (
                "Diagnose workflow runs that drifted — nodes stuck running, gates nobody "
                "can answer, expired waits, runs whose status was never written. Only "
                "REPORTS; workflow_repair repairs what it finds."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "workflow_repair",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Repair the workflow runs that drifted, as workflow_audit reports them. A run "
                "with a live controller is reported and left alone."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "workflow_manifest",
            "annotations": {"readOnlyHint": True},
            "description": (
                "The authoring reference, generated from the engine itself: node kinds and "
                "their lanes, gate kinds, join and loop modes, binding pipes, mutation ops "
                "and outcome states. Read-only. Call this before authoring a spec by hand — "
                "it cannot drift from what the engine actually accepts."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "workflow_delete_def",
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
            "description": (
                "Delete a workflow definition. Existing runs of it are unaffected — they "
                "carry their own copy of the spec. Bundled templates cannot be deleted."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    ]


# ── dispatch ─────────────────────────────────────────────────────────────────


def _gateway_service(name: str) -> Any:
    """One of the services the gateway wires (its workflow supervisor ``workflows``, its dashboard
    state ``state``), or None when it has not wired them.

    Fetched per call rather than cached: a cached None taken at import time would make
    every tool permanently inert in a process that wires services later.
    """
    try:
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
    except Exception:
        return None
    return getattr(services, name, None) if services else None


def _supervisor() -> Any:
    """The workflow supervisor, or None when the gateway has not wired one."""
    return _gateway_service("workflows")


#: Each tool that changes something, as the operation the gateway's route for the same call names
#: (`handlers._guard`): a Temporary or Incognito chat's call is held to one rule
#: (`restricted_calls`), asked of the tool's call here as the route asks it of the call an agent
#: CLI makes through the gateway. Every tool that is not read-only is here.
_CHANGES = {
    "workflow_author": "workflow_agent_save",
    "workflow_delete_def": "workflow_def_delete",
    "workflow_repair": "workflow_audit_heal",
    "workflow_start": "workflow_run_start",
    "workflow_start_draft": "workflow_run_start",
    "workflow_edit": "workflow_run_edit",
    "workflow_skip": "workflow_run_edit",
    "workflow_rewind": "workflow_run_rewind",
    "workflow_run_from": "workflow_run_from",
    "workflow_fork": "workflow_run_fork",
    "workflow_pause": "workflow_run_pause",
    "workflow_resume": "workflow_run_resume",
    "workflow_cancel": "workflow_run_cancel",
}


def _reader() -> Reader:
    """Who the call being made now reads as (``subagent_reach.reader_of_work``): the session it
    runs as (:func:`_current_session_id`), over the gateway's live chats where it runs there."""
    from personalclaw.subagent_reach import reader_of_work

    return reader_of_work(_gateway_service("state"), _current_session_id())


def _chat_mode_refusal(name: str, run_id: str) -> ToolFailure | None:
    """The refusal of the tool call *name* (for the run *run_id*) that the session it runs as may
    not make (`restricted_calls`), or None. A start names no run, as the route's does not."""
    operation = _CHANGES.get(name)
    if operation is None:
        return None
    from personalclaw.workflows import restricted_calls

    why = restricted_calls.refusal(
        _current_session_id(),
        operation,
        run_id="" if name == "workflow_start" else run_id,
        state=_gateway_service("state"),
    )
    return tool_failure(restricted_calls.sentence(why), code=restricted_calls.CODE) if why else None


def _run(coro: Any) -> Any:
    """Run a coroutine that reaches no run from the sync tool boundary: a definition's read or
    write, or `observe`'s read of the run store. A call that reaches a run is made on the
    engine's loop instead (`_on_engine`), since what it starts or wakes lives there.

    The tool contract is sync while the service layer is async. In production the native
    runtime already calls `_call_tool` in a thread executor, so no loop is running here and
    `asyncio.run` is right. But a caller that invokes the tool directly from async code
    (a script, a test, a future in-process caller) would hit
    "asyncio.run() cannot be called from a running event loop" — a crash in the surface
    whose entire contract is that it never raises.

    So the running-loop case is handled explicitly: run the coroutine to completion on its
    own loop in a worker thread. Blocking the calling thread is acceptable at this boundary
    because the tool contract is synchronous by definition; what is NOT acceptable is the
    tool surface exploding based on who called it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # the normal path: no loop on this thread

    from personalclaw import memory_writes

    with memory_writes.ScopeCarryingExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _on_engine(work: Callable[[Any], Any]) -> Any:
    """Make one call into the workflow engine from the sync tool boundary, on the engine's loop.

    *work* is handed the supervisor and returns the service's answer, or a coroutine for it. A
    run is driven by its controller's tick loop, a task on the supervisor's loop (the gateway's),
    which is where the owner's start makes this same call (`handlers.api_run_start`). This boundary
    runs in a worker thread: a run it started on a loop of its own was cancelled on its first step
    as that loop closed with the call, and a pause it lifted restarted no loop at all. So the call
    is handed to the supervisor's loop and this thread waits for the answer
    (`WorkflowWatchdog.run_threadsafe`).

    With no supervisor to hand it to — a gateway that has not wired its supervisor yet, or one
    whose loop is not running — the call is made here without one, and the service answers for
    that, as the gateway's own routes answer then: nothing is started, and a cancel or a pause it
    records is applied by the supervisor once it drives the run. The tool server an agent CLI runs
    never comes here: it holds no engine, and makes every call on the gateway (`_dispatch`).
    """
    supervisor = _supervisor()
    if supervisor is None or supervisor.event_loop is None:
        answer = work(None)
        return _run(answer) if inspect.isawaitable(answer) else answer
    return supervisor.run_threadsafe(lambda: work(supervisor))


#: The line a successful call of these tools opens with, where it has one: the same in either
#: process, so an agent CLI's answer reads as the native runtime's does.
SUMMARIES = {
    "workflow_manifest": "Workflow authoring reference (generated from the engine):",
    "workflow_start": "Workflow run started.",
    "workflow_start_draft": "Draft run started.",
    "workflow_fork": "Forked a new run; the original is unchanged.",
}


def _fmt(body: dict[str, Any], *, summary: str = "") -> str:
    """Render a service result for the model.

    A failure leads with its CODE so the model can branch on it, and keeps the payload so
    it can see the issue list rather than guessing what to fix.
    """
    if not body.get("ok", True):
        code = body.get("code", "WF_ERROR")
        message = body.get("message", "")
        extra = {k: v for k, v in body.items() if k not in ("ok", "code", "message")}
        reason = message
        if extra:
            reason += "\n" + json.dumps(extra, indent=2, ensure_ascii=False, default=str)
        # The service's own ``ok`` flag IS the verdict, so it travels with the rendered text
        # rather than being re-read out of it downstream (#3487). This branch is why a
        # leading-``Error:`` predicate could not work: every coded failure here reads
        # ``Error [CODE]: …``, which that predicate never matched.
        return tool_failure(reason, code=code)
    payload = {k: v for k, v in body.items() if k != "ok"}
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    return f"{summary}\n{rendered}" if summary else rendered


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate against the shared MCP schema; unschem'd tools pass through unchanged."""
    from personalclaw.validation import MCP_WORKFLOW_SCHEMAS, validate_tool_args

    schema = MCP_WORKFLOW_SCHEMAS.get(name)
    return validate_tool_args(args, schema) if schema else args


def _preflight(name: str, raw_args: dict[str, Any]) -> Any:
    """What these tools refuse before anyone is asked to approve a call: a tool this leaf may not
    call, and arguments the tool's schema refuses (``mcp_shared.preflight_refusal``)."""
    from personalclaw.mcp_shared import preflight_refusal

    return preflight_refusal(name, raw_args, _validate_args)


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    """One boundary, the shared one (issue 592). This module used to jump straight to
    `_dispatch`, which silently skipped everything `call_tool_with_logging` provides:
    every one of the MCP_WORKFLOW_SCHEMAS was dead (defined, key-tested, never consulted), no
    workflow tool call was SEL-logged, and — the sharp edge — `leaf_tool_denial` never
    ran, so a compiled batch leaf could call `workflow_start`/`workflow_fork` past the
    orchestration denial that exists precisely to stop a leaf fanning out unbudgeted.
    """
    from personalclaw.mcp_shared import call_tool_with_logging

    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key="mcp_workflows",
        downstream_service="personalclaw-workflows",
    )


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    """Dispatch one tool, appending the staged-turn spec echo where it applies.

    The echo (WF2-R20f) is the whole reason inspect tools are worth calling before a
    mutation: the model mutates what it just SAW rather than the spec it generated earlier,
    which diverges from disk the moment anything else touches the run.
    """
    out = _dispatch(name, args or {})
    run_id = str((args or {}).get("run_id", "") or "")
    # Only of a run the call reads (`chat_runs`), in either process: another chat's own run is no
    # run to it, here as in the answer it was given.
    if needs_staging(name) and chat_runs.reads_id(_reader(), run_id):
        echo = staged_spec_echo(run_id)
        if echo:
            combined = f"{out}\n\n{echo}"
            # Formatting a ``ToolFailure`` yields a plain ``str``, which would silently
            # downgrade a stated failure back to a success at the bridge — so re-carry it.
            if isinstance(out, ToolFailure):
                return ToolFailure(combined, reason=out.reason)
            return combined
    return out


def _refusal(name: str, args: dict[str, Any]) -> ToolFailure | None:
    """What a call's own arguments refuse before the engine is asked anything, in either process:
    a spec with no root, an edit with no ops, a skip that names no step."""
    if name in ("workflow_author", "workflow_check") and not isinstance(args.get("root"), dict):
        return tool_failure(
            "'root' must be the spec's root node object.", code="WF_DEF_ROOT_REQUIRED"
        )
    if name in ("workflow_edit", "workflow_edit_preview"):
        ops = args.get("ops")
        if not isinstance(ops, list) or not ops:
            return tool_failure(
                "'ops' must be a non-empty array of mutation ops.", code="WF_MUT_NO_OPS"
            )
    if name == "workflow_skip":
        node_ids = args.get("node_ids")
        if not isinstance(node_ids, list) or not node_ids:
            return tool_failure("'node_ids' must be a non-empty array.", code="WF_NO_NODE_IDS")
    return None


def unknown_tool(name: str) -> ToolFailure:
    return tool_failure(f"unknown workflows tool {name!r}.")


def definition_fields(args: dict[str, Any]) -> dict[str, Any]:
    """The definition ``workflow_author`` saves and ``workflow_check`` checks, from the call."""
    return {
        "name": str(args.get("name", "") or ""),
        "root": args.get("root"),
        "description": str(args.get("description", "") or ""),
        "inputs": args.get("inputs") if isinstance(args.get("inputs"), dict) else None,
        "tags": [str(t) for t in (args.get("tags") or [])],
    }


def start_fields(args: dict[str, Any]) -> dict[str, Any]:
    """What ``workflow_start`` asks for: the definition, its inputs, how to run it and where."""
    return {
        "name": str(args.get("name", "") or ""),
        "inputs": args.get("inputs") if isinstance(args.get("inputs"), dict) else None,
        "mode": str(args.get("mode", "background") or "background"),
        "project_id": str(args.get("project_id", "") or ""),
        "idempotency_key": str(args.get("idempotency_key", "") or ""),
    }


def _dispatch(name: str, args: dict[str, Any]) -> str:
    args = args or {}
    run_id = str(args.get("run_id", "") or "")
    refused = _refusal(name, args)
    if refused is not None:
        return refused

    from personalclaw.mcp_core import serves_an_agent_cli

    if serves_an_agent_cli():
        # No engine and no definitions in this process: every call is the gateway's, whose routes
        # hold it to the rule a Temporary or Incognito chat's call is held to.
        from personalclaw.mcp_workflows_gateway import call_through_the_gateway

        return call_through_the_gateway(name, args)

    if run_id and chat_runs.hidden(_reader(), run_id, operation=name):
        # Another chat's own run (`chat_runs`) is no run to this call, before anything else is
        # asked of it: answered as an id that never existed is.
        return _fmt(service._run_not_found(run_id))
    denied = _chat_mode_refusal(name, run_id)
    if denied is not None:
        return denied

    if name == "workflow_manifest":
        return _fmt(
            service.manifest(),
            summary=SUMMARIES["workflow_manifest"],
        )

    if name == "workflow_list_defs":
        return _fmt(
            _run(
                service.list_defs(
                    tag=str(args.get("tag", "") or ""),
                    source=str(args.get("source", "") or ""),
                )
            )
        )

    if name == "workflow_get_def":
        return _fmt(_run(service.get_def(str(args.get("name", "") or ""))))

    if name in ("workflow_author", "workflow_check"):
        # A check is its own tool, never an argument of the save: a call either saves or writes
        # nothing, so what it declares is what it does (a check only reads, and asks nobody).
        save = name == "workflow_author"
        fields = definition_fields(args)
        result = _run(service.author_def(**fields, save=save, saved_by=versions.AGENT))
        if save and result.get("code") == "WF_DEF_NEEDS_OWNER_YES":
            return saved_by_the_gateway(fields, needs_her_allow=True)
        if not save and result.get("ok") and result.get("valid"):
            freeze_authored_candidate(args)
        return _fmt(result)

    if name == "workflow_plan":
        return _plan(args)

    if name == "workflow_start":
        from personalclaw.mcp_core import _resolve_session_key

        # Started by the chat whose turn this is, named as the owner's route names its caller
        # (`X-Session-Key`): the run keeps that chat's memory posture, and the turn's Stop ends it.
        session_key = _resolve_session_key()
        return _fmt(
            _on_engine(
                lambda supervisor: service.start_run(
                    **start_fields(args), supervisor=supervisor, session_key=session_key
                )
            ),
            summary=SUMMARIES["workflow_start"],
        )

    if name == "workflow_start_draft":
        from personalclaw.mcp_core import _resolve_session_key

        # Named for the chat whose turn this is, as `workflow_start` is: the work of a run an
        # allowed automation started starts no draft (`automation_version.bound_for`).
        caller = _resolve_session_key()
        return _fmt(
            _on_engine(
                lambda supervisor: service.start_draft_run(
                    run_id, supervisor=supervisor, session_key=caller
                )
            ),
            summary=SUMMARIES["workflow_start_draft"],
        )

    if name == "workflow_status":
        return _fmt(service.status(run_id))

    if name == "workflow_observe":
        return _fmt(_run(service.observe(run_id, int(args.get("duration_ms") or 0))))

    if name == "workflow_output":
        return _fmt(service.output(run_id, str(args.get("node_id", "") or "")))

    if name in ("workflow_edit", "workflow_edit_preview"):
        ops = args["ops"]
        if name == "workflow_edit_preview":
            return _fmt(service.preview_edit(run_id, ops))
        expect = args.get("expect_version")
        return _fmt(
            _on_engine(
                lambda supervisor: service.edit_run(
                    run_id,
                    ops,
                    supervisor=supervisor,
                    expect_version=int(expect) if isinstance(expect, (int, float)) else None,
                    confirm_cascade=confirm_granted(args, "confirm_cascade"),
                )
            )
        )

    if name == "workflow_skip":
        node_ids = [str(n) for n in args["node_ids"]]
        return _fmt(
            _on_engine(
                lambda supervisor: service.skip_nodes(run_id, node_ids, supervisor=supervisor)
            )
        )

    if name == "workflow_rewind":
        return _fmt(
            _on_engine(
                lambda supervisor: service.rewind_run(
                    run_id,
                    str(args.get("node_id", "") or ""),
                    supervisor=supervisor,
                    redo_effects=yes_or_no(args.get("redo_effects")) is True,
                    force=yes_or_no(args.get("force")) is True,
                    confirm_cascade=confirm_granted(args, "confirm_cascade"),
                )
            )
        )

    if name == "workflow_run_from":
        return _fmt(
            _on_engine(
                lambda supervisor: service.run_from(
                    run_id,
                    str(args.get("node_id", "") or ""),
                    supervisor=supervisor,
                    confirm_cascade=confirm_granted(args, "confirm_cascade"),
                )
            )
        )

    if name == "workflow_fork":
        return _fmt(
            _on_engine(
                lambda supervisor: service.fork_run(
                    run_id,
                    checkpoint_id=str(args.get("checkpoint_id", "") or ""),
                    note=str(args.get("note", "") or ""),
                    supervisor=supervisor,
                )
            ),
            summary=SUMMARIES["workflow_fork"],
        )

    if name == "workflow_pause":
        return _fmt(_on_engine(lambda supervisor: service.pause_run(run_id, supervisor=supervisor)))

    if name == "workflow_resume":
        # An agent lifts a pause and answers nothing (`approval_answer`). An answer it sends
        # anyway, left over from an older description of this tool, goes to the service as an
        # answer, which refuses it and audits it rather than quietly lifting the pause instead.
        return _fmt(
            _on_engine(
                lambda supervisor: service.resume_run(
                    run_id,
                    by=approval_answer.agent(_current_session_id()),
                    supervisor=supervisor,
                    token=str(args.get("resume_token", "") or ""),
                    answer=decode_json_text(args.get("answer")),
                    always_allow=yes_or_no(args.get("always_allow")) is True,
                )
            )
        )

    if name == "workflow_cancel":
        return _fmt(
            _on_engine(lambda supervisor: service.cancel_run(run_id, supervisor=supervisor))
        )

    if name in ("workflow_audit", "workflow_repair"):
        reader = _reader()
        only = None if reader.everyone else chat_runs.live_runs_read(reader)
        return _fmt(
            _on_engine(
                lambda supervisor: service.audit(
                    dry_run=name == "workflow_audit", supervisor=supervisor, only=only
                )
            )
        )

    if name == "workflow_delete_def":
        return _fmt(_run(service.delete_def(str(args.get("name", "") or ""))))

    return unknown_tool(name)


def gateway_answer(reply: dict[str, Any], *, doing: str, summary: str = "") -> str:
    """The gateway's *reply* to one of these tools' calls (``mcp_core._get``/``_post``/``_delete``),
    rendered as the call's answer is rendered when the engine is in this process (:func:`_fmt`).

    A refusal the gateway made is the service's own (``workflows.handlers._fail`` puts its code,
    sentence and detail in the envelope), so it reads as it would have here, issue list included.
    A call the gateway did not answer says what it was for (*doing*, as in "pause the run 'x'")
    and why: one that never reached it was not made, and one it did not answer in time may have
    been.
    """
    detail = reply.get("error_detail")
    if isinstance(detail, dict):
        given = detail.get("detail")
        extra: dict[str, Any] = given if isinstance(given, dict) else {}
        return _fmt(
            {
                **extra,
                "ok": False,
                "code": str(detail.get("service_code") or detail.get("code") or "WF_ERROR"),
                "message": str(detail.get("message") or reply.get("error") or ""),
            }
        )
    if reply.get("timed_out"):
        return tool_failure(
            f"Asked PersonalClaw's gateway to {doing}, and {reply['error']}: it may have done "
            "it, so look before you ask again.",
            code="WF_GATEWAY_UNANSWERED",
        )
    if reply.get("error"):
        return tool_failure(f"Could not {doing}: {reply['error']}", code="WF_GATEWAY_UNANSWERED")
    return _fmt({**reply, "ok": True}, summary=summary)


def saved_by_the_gateway(fields: dict[str, Any], *, needs_her_allow: bool = False) -> str:
    """Hand the save *fields* to the gateway (``POST /api/workflows/agent-saves``), which saves it
    or asks the owner once to allow it, and say which, in the shape every workflow tool answers.

    Two saves come here: one this process found would let a step do more (*needs_her_allow*),
    which only the owner's own Allow saves and only the gateway can ask her for, so it is not
    saved whatever became of the call; and every save the tool server an agent CLI runs makes,
    which holds no definitions."""
    from personalclaw.mcp_core import _post

    answer = _post("/api/workflows/agent-saves", fields)
    unanswered = answer.get("error") and not isinstance(answer.get("error_detail"), dict)
    if unanswered and needs_her_allow:
        return tool_failure(
            f"{fields['name']!r} was not saved: a save that lets a step do more is made only on "
            "your owner's own Allow, which only the gateway can ask them for, and the gateway "
            f"could not be reached ({answer['error']})",
            code="WF_DEF_NEEDS_OWNER_YES",
        )
    if answer.get("error") or answer.get("status") != "awaiting_approval":
        return gateway_answer(answer, doing=f"save the workflow {fields['name']!r}")
    steps = [f"“{label}”" for label in answer.get("steps") or []]
    lets = (
        f"its step {steps[0]} approve its own tool calls"
        if len(steps) == 1
        else f"{len(steps)} of its steps ({'; '.join(steps)}) approve their own tool calls"
    )
    return "\n".join(
        [
            json.dumps({"status": "awaiting_approval", "approval": answer.get("approval", "")}),
            f"Not saved yet: it would let {lets} or change things, not only read. A save like "
            "that waits for your owner's own Allow, asked once, the way every approval is asked, "
            "and nothing is saved before they answer. You are not told their answer: "
            "workflow_get_def shows the workflow once it is saved. Do not save it another way.",
        ]
    )


def _plan(args: dict[str, Any]) -> str:
    """``workflow_plan``'s answer: the plan (:func:`plan_answer`), rendered as every one of these
    tools renders the service's answer."""
    body, summary = plan_answer(args)
    return _fmt(body, summary=summary)


def plan_answer(args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Scaffold + manifest, or a real TEMPLATE when one is named (WORKFLOWS-V2 §4, 9b), as
    ``(the service-shaped body, its summary line)``: the body the gateway's planner route answers
    the tool server an agent CLI runs with (``POST /api/workflows/agent-plans``), which holds none
    of what a plan is made from (the definitions, and the registries the grounding reads).

    Returns a SCAFFOLD deliberately when no template is given: the template-aware planner is
    UNIVERSAL-PLANNING's, and inventing a half-planner here would have to be deleted when the
    real one lands. What the scaffold does give the model is everything it needs to author the
    spec itself in the next turn — the shapes the engine accepts, and a starting tree it can
    edit — instead of guessing at a schema.

    `template` was previously ACCEPTED AND IGNORED. A model that passed it got a generic
    scaffold back and no indication its request had been dropped, which is worse than the
    parameter not existing: it looks like the templates are useless. Now a named template
    returns that template's real (macro-expanded, block-resolved) tree, so "plan me a deep
    research workflow" starts from the shipped one instead of a three-node stub.

    UP-R9 (WF2UNI-7): `source_session_id` mines a real transcript. "We just did this in chat" is
    the highest-volume shape of a task worth repeating, and until now the transcript proving it
    was thrown away. Mining reads the session's own record — the tools it actually ran and the
    approvals it earned — so the plan carries a PRE-VALIDATED permission signature instead of a
    guessed one, and never re-requests a tool the user denied in that very session.
    """
    goal = str(args.get("goal", "") or "").strip()
    source_session_id = str(args.get("source_session_id", "") or "").strip()

    mined = _mine_source_session(source_session_id) if source_session_id else None

    if not goal and mined is not None:
        # The transcript's own first user turn IS the goal. Requiring the caller to retype what
        # the session already recorded would make mining strictly more work than not mining.
        goal = template_pipeline.mined_goal(mined).strip()
    if not goal:
        if source_session_id:
            return (
                service._service_failure(
                    "WF_PLAN_SESSION_NOT_MINEABLE",
                    f"no transcript with usable turns for "
                    f"session {source_session_id!r}; pass 'goal' explicitly.",
                ),
                "",
            )
        return service._service_failure("WF_PLAN_GOAL_REQUIRED", "'goal' is required."), ""

    project_id = str(args.get("project_id", "") or "").strip()

    template = str(args.get("template", "") or "").strip()
    if template:
        # An explicitly user-named template WINS. The router's job is choosing when nobody has
        # chosen; overriding a stated request would make the matcher an obstacle.
        return _plan_from_template(goal, template, mined=mined, source_session_id=source_session_id)

    # Classify, then match, before any generation. Both are zero-token and
    # offline-safe, so this runs on every plan rather than only when a model is reachable.
    classified = intent_mod.classify(goal)
    requested = str(args.get("rigor", "") or "").strip()
    if requested in ("minimal", "standard", "deep"):
        rigor = requested
    elif requested:
        # An INVALID value is a caller error. Substituting the classifier's opinion would hide it —
        # the caller asked for something and would get something else with no indication. The
        # documented fallback stands.
        rigor = "standard"
    else:
        # ABSENT: defer to the classifier. `trivial`/`fast` map onto the tool's existing three-value
        # vocabulary rather than widening it — the schema is a published contract, and a fourth
        # value would break every caller that validates against it.
        rigor = {
            intent_mod.Rigor.TRIVIAL: "minimal",
            intent_mod.Rigor.FAST: "minimal",
            intent_mod.Rigor.STANDARD: "standard",
            intent_mod.Rigor.DEEP: "deep",
        }[classified.rigor]

    match = _match_library(goal, classified)
    if match is not None and match.matched and _def_resolvable(match.primary):
        # A matched template beats a scaffold: starting from a shipped shape means the plan inherits
        # a tested structure instead of a three-node stub.
        #
        # `_def_resolvable` first: the matcher reads bundled templates from DISK, while
        # `_plan_from_template` resolves through the service's registered providers. Outside a
        # booted gateway those disagree, and the router proposed a real name the loader could not
        # find —
        # turning a working scaffold into WF_PLAN_TEMPLATE_NOT_FOUND. A router that breaks the
        # fallback is worse than one that never matched.
        return _plan_from_template(
            goal,
            match.primary,
            routing={"intent": classified.to_dict(), "match": match.to_dict()},
            mined=mined,
            source_session_id=source_session_id,
        )

    scaffold: dict[str, Any] = {
        "kind": "sequence",
        "id": "main",
        "children": [
            {
                "kind": "stage",
                "id": "gather",
                "config": {"prompt": f"Gather what is needed for: {goal}", "model_tier": "fast"},
            },
            {
                "kind": "stage",
                "id": "produce",
                "config": {
                    "prompt": (f"Using {{{{nodes.gather.output}}}}, do the work for: {goal}"),
                    "model_tier": "standard",
                },
            },
        ],
    }
    if rigor in ("standard", "deep"):
        scaffold["children"].append(
            {
                "kind": "gate",
                "id": "verify",
                "config": {
                    "kind": "judge",
                    "prompt": (
                        f"Does {{{{nodes.produce.output}}}} actually accomplish: {goal}? "
                        "Judge strictly."
                    ),
                    "risk": "safe",
                },
            }
        )
    if rigor == "deep":
        scaffold["children"].insert(
            2,
            {
                "kind": "stage",
                "id": "review",
                "config": {
                    "prompt": (
                        f"Critique {{{{nodes.produce.output}}}} against the goal: {goal}. "
                        "List concrete defects."
                    ),
                    "model_tier": "reasoning",
                },
            },
        )

    grounded = _grounding_for(goal, classified, project_id)

    # The rigor axis, applied to the SCAFFOLD path only — a matched template returned above
    # already carries a tested shape, and stapling a refinement gate onto it would refine against a
    # structure nobody asked to change.
    proposed = (grounded or {}).get("skeleton") or scaffold
    fast_spec = {"root": proposed}
    if rigor_mod.is_fast(classified, requested=requested):
        fast_spec = rigor_mod.schedule_refinement(fast_spec)
        proposed = fast_spec["root"]

    # UP-R14: the grounding preamble. A deterministic entity-resolution node goes FIRST so the
    # resolved identity (or a degraded name-only fallback) is established before any stage runs,
    # rather than each stage re-guessing who the goal names. Best-effort: a preamble that could not
    # be built loses grounding, never the plan.
    proposed = _prepend_grounding_preamble(goal, proposed)

    # Hoisted out of the body literal so `_review_surface` reads the SAME dict the caller is
    # shown. Rebuilding it there would let the announce block's intent chips drift from the
    # reported routing, which is the one disagreement a reader has no way to detect.
    routing = {
        "intent": classified.to_dict(),
        "match": match.to_dict() if match is not None else {"reason": "matcher unavailable"},
    }

    body = {
        "ok": True,
        # Renamed: this is no longer a bare structural stub. It carries the live grounding bundle,
        # a picked shape with a validated skeleton, and the constraint block — which is the whole
        # difference the plan measured (first-try-valid 0/5 → 4/5).
        "planner": "grounded-v1" if grounded else "scaffold-v1",
        "goal": goal,
        "rigor": rigor,
        # The routing is reported even when nothing matched: "no template fit, and here is why"
        # is the answer that tells a reader whether to add a template or fix a keyword list.
        "routing": routing,
        "proposed_root": proposed,
        # Which rigor path ran and why. A user who got a thin plan needs to know it was the
        # fast path rather than the planner doing badly.
        "rigor_note": rigor_mod.rigor_note(classified, requested=requested),
        # UP-R9: what the source session actually did, when one was named. Present only when
        # mining produced something — an empty block would read as "that session did nothing".
        **_mined_surface(mined, source_session_id),
        **({"grounding": grounded} if grounded else {}),
        # The announce block, the cost shape, the markdown artifact — and the REVISION
        # GRAMMAR. This path's `next_step` tells the model to adapt the tree while the
        # `NO_UPDATE` sentinel and the merge-by-id ops that make an adaptation safe only ever
        # reached the template path, so the instruction arrived without its vocabulary.
        **_review_surface(goal, {"root": proposed, "inputs": {}}, routing),
        # UP-R3: the same run-start checker, on this path too. A generated tree resolves model
        # tiers and can name action providers exactly as a template does, so leaving preflight on
        # one path would reproduce the asymmetry above with a different key.
        **_preflight_surface({"root": proposed, "inputs": {}}),
        **_grill_surface(goal, classified, {"root": proposed}, topics=_plan_topics(goal)),
        "next_step": (
            "Adapt this tree to the goal, then call workflow_check to validate it before "
            "workflow_author saves it."
        ),
        "note": (
            "Fill the shape's slots and adapt the tree, then call workflow_check to validate. "
            "The `grounding` block below is read from THIS system's live registries — anything "
            "not in it does not exist here. If you cannot plan the goal with what is listed, "
            'return {"cannot_plan": "<why>"} rather than inventing a node.'
        ),
        "manifest": {k: v for k, v in service.manifest().items() if k != "ok"},
    }
    return body, f"Draft plan for: {goal}"


def freeze_authored_candidate(args: dict[str, Any]) -> None:
    """Freeze a validated-but-unsaved spec as a SESSION-scoped candidate (UP-R9), after a
    ``workflow_check`` that found it valid.

    A generated spec that VALIDATED but was not saved is exactly what used to be thrown away.
    Freezing it is what stops the next similar intent re-generating a different graph. Not saved
    to the library — SESSION scope, promoted by reuse, because a spec that parsed is not yet a
    spec that worked.

    Best-effort and silent: freezing is an optimization for the NEXT similar request, and a store
    write that failed must not turn a successful dry-run validation into an error. The user asked to
    validate a spec; they get that answer either way.

    Parameterized before freezing, so the candidate the matcher later offers is a reusable shape
    rather than one run's literal entities — a candidate carrying a concrete hostname would match a
    similar goal and then plan against the wrong target.

    Work for a chat that keeps nothing (an Incognito or Temporary chat's, or work whose chat's mode
    cannot be read) freezes none: a candidate is kept on disk and learned from, which such a chat
    promises not to be (`memory_writes`).
    """
    from personalclaw import memory_writes

    if memory_writes.writes_refused():
        return
    try:
        from personalclaw.workflows import template_store

        goal = str(args.get("description", "") or "").strip() or str(args.get("name", "") or "")
        if not goal:
            return
        spec = {
            "name": str(args.get("name", "") or ""),
            "description": str(args.get("description", "") or ""),
            "root": args.get("root") or {},
            "inputs": args.get("inputs") if isinstance(args.get("inputs"), dict) else {},
        }
        generalized, _slots = template_pipeline.parameterize(spec)
        candidate = template_pipeline.freeze_candidate(
            generalized, goal, session_id=_current_session_id()
        )
        template_store.save_candidate(candidate)
    except Exception:
        logger.debug("candidate freeze skipped", exc_info=True)


def _current_session_id() -> str:
    """The session this call runs under, or '' — the SESSION scope's identity, and the agent a
    ``workflow_resume`` is made by.

    The session KEY (`dashboard:chat-1-…`), which is what a chat turn can actually know; the on-disk
    session id belongs to `SessionMap` and is not reachable from a tool call. What matters for
    scoping is only that the value is stable within a conversation and distinct between them, and
    the key is both. Read where every call to the gateway reads the session it names
    (`mcp_core._resolve_session_key`): the tool server an agent CLI runs is given it in its
    environment, and the native runtime binds it around each call it makes in the gateway, where
    no environment names a session. Work that runs as a session's outside a call of its runtime (a
    request made for it) is that session's (`memory_writes.source_session`). '' when nothing names
    one, which makes the candidate visible to every session — correct for a headless caller that
    has no session to be private to.
    """
    from personalclaw import memory_writes
    from personalclaw.mcp_core import _resolve_session_key

    return _resolve_session_key() or memory_writes.source_session()


def _mine_source_session(session_id: str) -> Any:
    """Mine a session transcript for what a template would need. None when unmineable.

    None rather than an empty `MinedSession`, so the caller can say "that session had nothing to
    mine" instead of reporting a confident empty mining it never actually performed — the two mean
    different things to a user who just asked to reuse a conversation.

    Resolution goes through `session_map.read_transcript`, which resolves the id to
    `sessions/<sid>.jsonl` under the LIVE home. The id is the on-disk session id (the same one
    `SessionMap.get` returns), not the session key.
    """
    if not session_id:
        return None
    try:
        from personalclaw import session_map

        records = session_map.read_transcript(session_id)
    except Exception:
        logger.debug("transcript read failed for %r", session_id, exc_info=True)
        return None
    if not records:
        return None
    mined = template_pipeline.mine_session(records)
    if not mined.user_turns and not mined.tools and not mined.title:
        # A file that parsed but yielded nothing is not a mining result. Reporting it as one would
        # put an empty permission signature on the plan and call it pre-validated.
        return None
    return mined


def _mined_surface(mined: Any, session_id: str) -> dict[str, Any]:
    """The mined block, as the planner reports it. Empty dict when nothing was mined.

    The DENIED list ships alongside the signature rather than being silently subtracted and
    forgotten: "these tools ran and you may declare them, and these you refused" is the fact that
    makes the signature trustworthy, and a reviewer who cannot see the refusals cannot check it.
    """
    if mined is None:
        return {}
    return {
        "mined_session": {
            "session_id": session_id,
            "title": mined.title,
            "user_turns": len(mined.user_turns),
            "observed_tools": [t.to_dict() for t in mined.tools],
            "permission_signature": mined.permission_signature,
            "denied": list(mined.denied),
            "note": (
                "Declare at most these tools: they ran in the session with the user present. "
                "Anything in `denied` was REFUSED there and must not be re-requested."
            ),
        }
    }


def _plan_topics(goal: str) -> list[str]:
    """The goal's topics (UP-R14), the retrieval queries the grill's lookup channels run.

    Best-effort: a topic-extraction failure loses the lookup queries, never the plan."""
    try:
        from personalclaw.workflows import preamble

        return preamble.extract_topics(goal)
    except Exception:
        logger.debug("topic extraction unavailable", exc_info=True)
        return []


def _prepend_grounding_preamble(goal: str, root: dict) -> dict:
    """Prepend the deterministic entity-resolution node to the proposed tree (UP-R14).

    The resolver is the LIVE memory graph when one is wired, so a goal naming a known entity
    resolves it up front; with no graph the preamble still emits, degraded to name-only context so
    the guard and prohibition reach the stages. Best-effort by construction: any failure returns the
    tree unchanged, because a grounding enhancement must never cost the plan.
    """
    try:
        from personalclaw.workflows import preamble

        node = preamble.build_preamble_node(goal, _entity_resolver())
        if node is None:
            return root
        return preamble.prepend_preamble(root, node)
    except Exception:
        logger.debug("grounding preamble unavailable", exc_info=True)
        return root


def _entity_resolver() -> Any:
    """The live entity resolver (`MemoryService.resolve_entities`), or None when no graph is wired.

    None is the degraded path: the preamble still emits a name-only node with the guard, and records
    `degraded: true` so a reviewer sees the graph was unavailable rather than the goal naming none.
    """
    svc = _memory_service()
    if svc is None or not svc.has_graph:
        return None
    return svc.resolve_entities


def _grill_surface(
    goal: str, classified: Any, spec: dict | None = None, *, topics: list[str] | None = None
) -> dict:
    """The `rigor: deep` protocol's plan-time half: whether to grill, and the stress probes.

    The ROUNDS are not built here, and that is deliberate. A round needs the planner's recommended
    answers, and a recommendation is what makes deep grilling fast rather than tedious — emitting
    rounds with empty recommendations would ship the tedium without the speed. So this surface
    reports the trigger and the probes (both derivable with no model call) and hands the caller the
    protocol's own vocabulary to build rounds with.

    The risk scan is passed through rather than skipped: the plan makes ANY risk-registry hit
    force `rigor: deep`, and `deep_triggered` implements it — but measured, nothing was feeding it
    hits, so that half of the trigger was present and inert. A destructive plan the classifier
    happened to call `standard` would have gone ungrilled.

    `topics` are the UP-R14 retrieval queries: the goal's nouns the lookup channels should search
    BEFORE asking, so a discoverable fact is looked up rather than put to the user as a question.

    Best-effort: a missing grill block loses advice, never enforcement.
    """
    try:
        hits = []
        if spec:
            from personalclaw.workflows.autonomy import scan_risk

            hits = scan_risk(spec)
        triggered, why = grill_mod.deep_triggered(classified, hits)
        if not triggered:
            return {}
        probes = grill_mod.stress_probes(goal)
        return {
            "grill": {
                "triggered": True,
                "why": why,
                # The topics the lookup channels query first — "understand, then check what I
                # know" — so a fact discoverable in memory/knowledge/codebase is not asked for.
                "lookup_topics": list(topics or []),
                "protocol": {
                    "questions_ship_recommendations": True,
                    "batch_at": 3,
                    "max_batch": grill_mod.MAX_BATCH,
                    "escape_hatch": grill_mod.OTHER,
                    "boundary_question": grill_mod.BOUNDARY_QUESTION,
                    "lookup_channels": [c.value for c in grill_mod.Channel if c.value != "ask"],
                },
                "stress_probes": [p.to_dict() for p in probes],
                "note": (
                    "Ship every question WITH your recommended answer as its default, route "
                    "discoverable facts to a lookup channel instead of asking, and treat an "
                    "unanswered load-bearing question as a BLOCKER rather than assuming a value."
                ),
            }
        }
    except Exception:
        logger.debug("grill surface failed", exc_info=True)
        return {}


def _autonomy_surface(definition: dict) -> dict:
    """The risk scan, the autonomy offer, the confirmations the recommended mode will raise, and
    which of those an unattended run would still stop for.

    Best-effort like the other surfaces. A missing autonomy block must not stop a plan reaching the
    user — but note the asymmetry: the ENGINE's own gate policy still governs what actually runs, so
    a failure here loses advice, never enforcement.
    """
    try:
        from personalclaw.workflows import autonomy as autonomy_mod

        spec = {"inputs": definition.get("inputs") or {}, "root": definition.get("root") or {}}
        meta = definition.get("metadata") or {}
        raw_floor = str(meta.get("autonomy_floor", "") or "") if isinstance(meta, dict) else ""
        floor = None
        if raw_floor:
            try:
                floor = autonomy_mod.Mode(raw_floor)
            except ValueError:
                logger.debug("unknown autonomy_floor %r — ignoring", raw_floor)

        hits = autonomy_mod.scan_risk(spec)
        offer = autonomy_mod.offer_autonomy(spec, template_floor=floor, hits=hits)
        confirmations = autonomy_mod.build_confirmations(spec, offer.recommended)
        return {
            "risk": [h.to_dict() for h in hits],
            "autonomy": offer.to_dict(),
            "attention": {k: v.value for k, v in autonomy_mod.type_attention(spec, hits).items()},
            "require_hitl": autonomy_mod.compile_require_hitl(spec, offer.recommended),
            "confirmations": [c.to_dict() for c in confirmations],
            # And which of those stops SURVIVE at unattended. The recommended mode is
            # usually `per_stage` (every risk signal caps there) while `unattended` stays on
            # offer, so without this the preview lists the stops for a mode the user may not
            # pick and says nothing about the one they are being offered.
            "unattended_interrupts": autonomy_mod.unattended_interrupts(confirmations),
        }
    except Exception:
        logger.debug("autonomy surface unavailable", exc_info=True)
        return {}


def _review_surface(goal: str, definition: dict, routing: dict | None) -> dict:
    """The announce block, a structural cost estimate, and the plan as markdown.

    Best-effort like the contract review: a header is an enhancement, and a failure to render one
    must not stop a working plan reaching the user.
    """
    try:
        from personalclaw.workflows import contracts as contracts_mod
        from personalclaw.workflows import revision as revision_mod

        spec = {"inputs": definition.get("inputs") or {}, "root": definition.get("root") or {}}
        stage_contracts = contracts_mod.derive_contracts(spec)
        decisions = contracts_mod.type_decisions(spec)
        cost = revision_mod.estimate_cost(spec)

        intent = None
        match = None
        if routing:
            from personalclaw.workflows import intent as intent_mod
            from personalclaw.workflows.matcher import Candidate, MatchResult

            raw_intent = routing.get("intent") or {}
            if raw_intent.get("rigor"):
                # Rebuilt rather than threaded: the routing dict has already crossed a JSON
                # boundary, and re-deriving from the goal would classify twice and could disagree
                # with what the caller was shown.
                intent = intent_mod.Intent(
                    rigor=intent_mod.Rigor(raw_intent["rigor"]),
                    stakes=intent_mod.Level(raw_intent.get("stakes", "low")),
                    irreversible=bool(raw_intent.get("irreversible")),
                    shape=str(raw_intent.get("shape", "") or ""),
                    signals=raw_intent.get("signals") or {},
                )
            raw_match = routing.get("match") or {}
            if raw_match.get("primary"):
                match = MatchResult(
                    primary=str(raw_match["primary"]),
                    confidence=float(raw_match.get("confidence") or 0.0),
                    reason=str(raw_match.get("reason", "") or ""),
                )
                _ = Candidate  # imported for the type's side of the contract

        header = revision_mod.announce_block(
            intent=intent, match=match, contracts=stage_contracts, decisions=decisions, cost=cost
        )
        return {
            "announce": header,
            "cost_estimate": cost,
            "plan_markdown": revision_mod.plan_markdown(
                spec, goal=goal, header=header, contracts=stage_contracts
            ),
            "inferred": revision_mod.inferred_chips(spec, goal),
            "revision_grammar": {
                "no_update_sentinel": revision_mod.NO_UPDATE,
                "ops": ["replace", "add", "remove", "annotate"],
                "semantics": (
                    "merge by node id — same id replaces, new id adds, absent id is preserved "
                    "untouched. Emit ONLY changed steps; a whole-spec rewrite re-rolls the stages "
                    "nobody complained about."
                ),
            },
        }
    except Exception:
        logger.debug("review surface unavailable", exc_info=True)
        return {}


def _preflight_surface(definition: dict) -> dict:
    """UP-R3: what this plan needs that this system does not have — BEFORE approval.

    The same `workflows/preflight` the run-start gate uses, so a plan-time green and a run-start
    green cannot disagree about credentials, binaries, models or action providers. Reached from
    the planner is the whole point: preflight at run start only tells the user their *approved*
    plan cannot run, which is the `plan-approved-run-dies-at-step-1` class this closes.

    The WHOLE definition is passed, not the narrowed `{inputs, root}` the other surfaces build:
    preflight reads `metadata.requirements` and `defaults.model_tiers`, and the secret scan walks
    every string, so narrowing would silently drop a `{{secret:KEY}}` reference living outside the
    tree. That also makes this call identical to the two run-start ones.

    `provider_requirement_gap` is appended because preflight cannot see one hop past a provider
    NAME — the plan's own execution log recorded that aggregation as blocked on requirement data
    the grounding bundle does not carry, and it still is: `ActionProvider` declares no
    requirements. Reporting that as a typed warning is the honest shape; letting the report say
    `ok` with a class unexamined is the inertness this plan family exists to catch.

    Best-effort like the other surfaces, and deliberately non-blocking: at plan time nothing has
    started, so a missing credential is advice about what approval commits to, not a refusal.
    """
    try:
        from personalclaw.workflows import preflight as preflight_mod

        report = preflight_mod.preflight(definition)
        report.findings.extend(preflight_mod.provider_requirement_gap(definition))
        return {"preflight": report.to_dict()}
    except Exception:
        logger.debug("preflight surface unavailable", exc_info=True)
        return {}


def _contract_review(definition: dict) -> dict:
    """The derived form, the per-stage contracts, and the typed decisions.

    Best-effort: a review surface is an enhancement to the plan, and a contract derivation that
    failed must not stop the user launching a template that works. Returns `{}` rather than partial
    keys so a caller cannot mistake an error for "this template has no contracts".
    """
    try:
        from personalclaw.workflows import contracts as contracts_mod

        spec = {
            "inputs": definition.get("inputs") or {},
            "root": definition.get("root") or {},
        }
        stage_contracts = contracts_mod.derive_contracts(spec)
        decisions = contracts_mod.type_decisions(spec)
        return {
            "parameters": [p.to_dict() for p in contracts_mod.resolve_unfilled_inputs(spec)],
            "parameter_types": contracts_mod.template_types(spec),
            "declared_but_unused": contracts_mod.declared_but_unused(spec),
            "stage_contracts": [c.to_dict() for c in stage_contracts],
            "contract_issues": contracts_mod.contract_issues(stage_contracts),
            "decisions": [d.to_dict() for d in decisions],
            "open_decisions": contracts_mod.open_decisions(decisions),
        }
    except Exception:
        logger.debug("contract review unavailable", exc_info=True)
        return {}


def _eval_surface(template: str, definition: dict) -> dict:
    """This template's DERIVED benchmark (UP-R13.3), produced at plan time.

    The eval is derived from the template artifact rather than maintained beside it, so it cannot
    go stale: a routing suite that passes while the template it picked is subtly wrong is exactly
    the false confidence this closes.

    `graded_checks` is reported as a COUNT and a list of what a judge would have to grade, never as
    a verdict — grading needs a judge and LEARNING-FLYWHEEL owns that harness. A spec whose quality
    checks are un-gradeable here is the correct output, and saying so is the point: it separates
    "this template validated" from "this template is good", which is what makes the number
    actionable at all.

    Best-effort, like every other review block: an eval derivation that failed must not cost the
    user the plan.
    """
    try:
        from personalclaw.workflows import eval_specs

        spec = {
            "inputs": definition.get("inputs") or {},
            "root": definition.get("root") or {},
        }
        metadata = definition.get("metadata") or {}
        derived = eval_specs.derive_eval_spec(template, spec, metadata)
        return {"eval_spec": derived.to_dict()}
    except Exception:
        logger.debug("eval spec derivation unavailable for %r", template, exc_info=True)
        return {}


def _grounding_for(goal: str, classified: Any, project_id: str = "") -> dict | None:
    """The grounding bundle, the picked shape, and the generated prompt.

    Returns None when grounding cannot be assembled, and the caller falls back to the bare
    scaffold — the bundle is an enhancement to planning, and a planner with a stub is still better
    off than one handed an exception.

    When `project_id` binds an existing codebase, the brownfield context pass (UP-R17) feeds the
    prompt's `codebase_context` so generated stages assume the project's real language, test
    framework and layout instead of generic scaffolding.
    """
    try:
        from personalclaw.workflows import generation, grounding, patterns

        bundle = grounding.build_bundle()
        shape, reason = patterns.pick_shape(goal, classifier_shape=getattr(classified, "shape", ""))
        codebase = _codebase_context_for(project_id)
        return {
            "bundle": bundle.to_dict(),
            "index": bundle.index(),
            "shape": shape.to_dict() if shape else None,
            "shape_reason": reason,
            "skeleton": dict(shape.skeleton) if shape else None,
            "prompt": generation.planning_prompt(
                goal,
                bundle=bundle,
                shape=shape,
                shape_reason=reason,
                codebase_context=codebase,
            ),
            **({"codebase_context": codebase} if codebase else {}),
            "emission_schema": (
                generation.spec_json_schema() if bundle.structured_output else None
            ),
            "self_check_rules": (
                "unique ids · valid kinds · gates have criteria · foreach has items · loops are "
                "bounded · a work node exists · a stopping condition exists · no unfilled slots · "
                "bindings resolve"
            ),
        }
    except Exception:
        logger.debug("grounding unavailable — falling back to the bare scaffold", exc_info=True)
        return None


def _codebase_context_for(project_id: str) -> str:
    """The brownfield `CODEBASE_CONTEXT` block for a project-scoped plan, or "" (UP-R17).

    A project grounds the plan only when it BINDS a codebase on disk (`workspace_dir`): a project
    whose context dir is its own working area has no source tree to read conventions from. The
    synthesis is cached per `(project_id, tree-hash)` under the config dir with a 7-day TTL, so
    replanning against the same unchanged project pays the walk once. Best-effort: any failure
    returns "" and the prompt simply omits the block.
    """
    if not project_id:
        return ""
    try:
        from pathlib import Path

        from personalclaw.config.loader import config_dir
        from personalclaw.tasks.hierarchy import HierarchyStore
        from personalclaw.workflows import brownfield

        project = HierarchyStore().get_project(project_id)
        workspace = str(getattr(project, "workspace_dir", "") or "") if project else ""
        if not workspace:
            return ""
        cache = brownfield.BrownfieldCache(config_dir() / "workflows" / "brownfield_cache.json")
        return brownfield.codebase_context(project_id, Path(workspace), cache=cache)
    except Exception:
        logger.debug("brownfield context unavailable for %s", project_id, exc_info=True)
        return ""


def _def_resolvable(name: str) -> bool:
    """Can `_plan_from_template` actually load this name?

    Asked BEFORE routing to it, because the matcher and the loader read different sources and a
    proposal the loader cannot honour replaces a usable scaffold with an error.

    Candidates count as resolvable: `_plan_from_template` falls back to the candidate store when the
    def registry has no such name. Without this a matched candidate would be rejected here and the
    freeze would be write-only.
    """
    if not name:
        return False
    try:
        if bool(_run(service.get_def(name)).get("ok")):
            return True
    except Exception:
        logger.debug("def resolvability check failed for %r", name, exc_info=True)
    return _candidate_definition(name) is not None


def _candidate_definition(name: str) -> dict | None:
    """A frozen candidate in the shape `_plan_from_template` reads, or None.

    The candidate's stored spec IS a def-shaped dict (that is what `freeze_candidate` was handed),
    so this normalizes rather than converts — and it marks the result so a reader can tell a
    candidate from a shipped template. A plan that silently presented a one-off guess as a library
    template would be claiming a provenance it does not have.
    """
    try:
        from personalclaw.workflows import template_store

        candidate = template_store.get_candidate(name)
    except Exception:
        logger.debug("candidate lookup failed for %r", name, exc_info=True)
        return None
    if candidate is None:
        return None
    spec = dict(candidate.spec or {})
    metadata = dict(spec.get("metadata") or {})
    metadata.setdefault("origin_goal", candidate.origin_goal)
    return {
        "name": candidate.name,
        "description": spec.get("description") or candidate.origin_goal,
        "root": spec.get("root") or {},
        "inputs": spec.get("inputs") or {},
        "metadata": metadata,
        "candidate": {
            "scope": candidate.scope,
            "reuses": candidate.reuses,
            "session_id": candidate.session_id,
            "origin_goal": candidate.origin_goal,
            "note": (
                "A FROZEN CANDIDATE, not a shipped template: it was generated for a similar "
                "request and kept so this one does not re-generate a different graph. It is "
                "promoted by REUSE, never by having parsed successfully."
            ),
        },
    }


def _match_library(goal: str, classified: Any) -> Any:
    """Match the goal against the bundled library. Returns None when the matcher cannot run.

    None rather than an empty result, so the caller can say "matcher unavailable" instead of
    reporting a confident no-match it never actually computed — the two mean different things to
    whoever is deciding whether to add a template.

    T4/T5 are wired here with LIVE injections: the embedder for the tie-breaker, the summarizer for
    the rephrase-and-rematch. Both degrade to None when their subsystem is unwired, so the matcher
    falls to the deterministic tiers rather than hard-failing — the whole point of the demotion.
    """
    try:
        from personalclaw.workflows.matcher import match_template

        profiles = _library_profiles(session_id=_current_session_id())
        if not profiles:
            return None
        return match_template(
            goal,
            profiles,
            shape=getattr(classified, "shape", ""),
            embedder=_live_embedder(),
            summarizer=_live_summarizer(),
            threshold=_match_threshold(),
        )
    except Exception:
        logger.debug("template matching unavailable", exc_info=True)
        return None


def _library_profiles(*, session_id: str = "") -> list[Any]:
    """The matchable library: bundled templates PLUS frozen candidates. Empty on read failure.

    UP-R9: candidates join the same tiered matcher as shipped templates, which is the whole point of
    freezing them. A candidate stored where the matcher cannot see it would leave the next similar
    intent re-generating a spec — and two runs of one request producing two different graphs is the
    drift the freeze exists to stop.

    Candidates come SECOND so a bundled template of the same name wins the tie: a shipped, tested
    shape beats a one-off guess frozen from a single successful parse.
    """
    from personalclaw.workflows import bundled_defs
    from personalclaw.workflows.matcher import TemplateProfile

    profiles: list[Any] = []
    seen: set[str] = set()
    for name in bundled_defs.template_names():
        spec = bundled_defs.read_template(name)
        if spec is not None:
            profiles.append(TemplateProfile.from_def(spec))
            seen.add(name)
    for candidate in _candidate_profiles(session_id=session_id):
        if candidate.name not in seen:
            profiles.append(candidate)
            seen.add(candidate.name)
    return profiles


def _candidate_profiles(*, session_id: str = "") -> list[Any]:
    """Frozen candidates as matchable profiles. Empty on any read failure.

    A candidate has no author-written keywords, so its `origin_goal` becomes the match text: the
    request that produced it is the best available description of what it serves, and it is what a
    similar next intent will actually resemble.
    """
    try:
        from personalclaw.workflows import template_store
        from personalclaw.workflows.matcher import TemplateProfile

        out: list[Any] = []
        for candidate in template_store.load_candidates(session_id=session_id):
            out.append(
                TemplateProfile(
                    name=candidate.name,
                    description=candidate.origin_goal,
                    tags=["candidate", f"scope:{candidate.scope}"],
                    keywords=[],
                    match_text=candidate.origin_goal,
                )
            )
        return out
    except Exception:
        logger.debug("candidate profiles unavailable", exc_info=True)
        return []


def _match_threshold() -> float:
    """The `workflows.match_threshold` tie-break floor, read from live config.

    Read here rather than in the matcher so the matcher stays pure and offline-safe. Falls back to
    the matcher's own default when config is unreadable — a planning path must not fail on a config
    read.
    """
    from personalclaw.workflows.matcher import MATCH_THRESHOLD

    try:
        from personalclaw.config.loader import AppConfig

        return float(AppConfig.load().workflows.match_threshold)
    except Exception:
        logger.debug("match_threshold config unreadable — using the matcher default", exc_info=True)
        return MATCH_THRESHOLD


def _live_embedder() -> Any:
    """An adapter around the wired MemoryService embedder, or None when none is wired.

    `MemoryService.embed(text) -> list[float] | None` is exactly the matcher's embedder protocol, so
    the adapter is a thin bound method. Resolved through the running gateway's context builder (the
    same store the Memory UI reads); None when the process has no memory wired — a headless script,
    or a boot before services are set — in which case T4 simply does not run.
    """
    svc = _memory_service()
    if svc is None or not svc.can_vector_search:
        return None
    return svc.embed


def _live_summarizer() -> Any:
    """A synchronous rephrase for T5, or None when no model is reachable.

    T5 re-enters the deterministic scorer with a MODEL's restatement of the intent; it never returns
    a template id. `one_shot_completion` is async, so this wraps it through the same sync bridge the
    tool surface already uses (`_run`). Returns None when the process has no model plumbing, so T5
    degrades to the tier below with a recorded reason rather than faking a summary.
    """
    if _memory_service() is None:
        # A cheap proxy for "is this a booted gateway with services wired?": the summarizer needs
        # the model bridge, only meaningful in the same process that wires memory. A bare script
        # or unbooted process gets None, and T5 stays inert as designed.
        return None

    def summarize(intent_text: str) -> str:
        from personalclaw.guardrails.local_queue import Attended
        from personalclaw.llm_helpers import one_shot_completion
        from personalclaw.mcp_core import get_current_session_key

        prompt = (
            "Rephrase this workflow request as a short, plain description of the desired OUTCOME, "
            "in one sentence, using concrete nouns. Do not name any template or tool.\n\n"
            f"Request: {intent_text}"
        )
        # The agent turn whose tool asked is waiting on this. Its session is read here, in the
        # tool's own context, before `_run` may carry the call to another thread.
        waiting = Attended("Matching a workflow", session=get_current_session_key())
        try:
            return str(
                _run(one_shot_completion(prompt, use_case="background", attended=waiting)) or ""
            ).strip()
        except Exception:
            logger.debug("T5 summarizer completion failed", exc_info=True)
            return ""

    return summarize


def _memory_service() -> Any:
    """The MemoryService over the gateway's context-builder store, or None.

    One resolution point for both the embedder (T4) and the model-availability probe (T5). Fetched
    per call like `_supervisor`: a cached None taken before services are wired would leave the
    matcher permanently deterministic in a process that wires memory later.
    """
    try:
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
        state = getattr(services, "state", None) if services else None
        builder = getattr(state, "context_builder", None) if state else None
        store = getattr(builder, "memory", None) if builder else None
        if store is None:
            return None
        from typing import cast

        from personalclaw.memory_providers.base import MemoryProvider
        from personalclaw.memory_service import service_for

        return service_for(cast("MemoryProvider", store))
    except Exception:
        logger.debug("memory service unavailable for the matcher", exc_info=True)
        return None


def _plan_from_template(
    goal: str,
    template: str,
    *,
    routing: dict | None = None,
    mined: Any = None,
    source_session_id: str = "",
) -> tuple[dict[str, Any], str]:
    """Plan by starting from a real template's tree rather than a generic scaffold, as
    :func:`plan_answer` answers.

    Returns the template's ALREADY-EXPANDED root (macros expanded, blocks resolved), because that
    is what the model will edit and then hand to `workflow_author` — handing back the authored
    form would make the model re-derive an expansion it cannot see the result of.

    The steering examples come along: they are the template's own record of how it is driven, and
    they are what turn "here is a tree" into "here is how this tree is used".
    """
    result = _run(service.get_def(template))
    definition = result.get("definition") or {} if result.get("ok") else {}
    if not definition:
        # UP-R9: a frozen candidate is a real plan source. Checked after the def registry so a
        # shipped template of the same name always wins.
        definition = _candidate_definition(template) or {}
    if not definition:
        available = _run(service.list_defs())
        names = [d["name"] for d in available.get("defs", [])]
        return (
            service._service_failure(
                "WF_PLAN_TEMPLATE_NOT_FOUND",
                f"no workflow definition named "
                f"{template!r}. Available: {', '.join(names) or 'none'}.",
            ),
            "",
        )

    meta = definition.get("metadata") or {}
    body = {
        "ok": True,
        "planner": "template-v1",
        "goal": goal,
        "template": template,
        # Present when the ROUTER chose this template, absent when the caller named it. A reader
        # needs to know which happened: an auto-matched template is a decision to check, a named
        # one is a decision already made.
        **({"routing": routing} if routing else {}),
        # UP-R9: mining travels with the template path too. A user who said "template this, from
        # that conversation" needs the session's real permission signature on the plan they are
        # about to adapt — dropping it here would make mining work only on the scaffold path.
        **_mined_surface(mined, source_session_id),
        # UP-R9: present ONLY for a frozen candidate. Its absence is what says "shipped template",
        # so a reader is never left guessing which provenance they are looking at.
        **({"candidate": definition["candidate"]} if definition.get("candidate") else {}),
        # UP-R3/R8/R16: the review surface. Derived from the tree rather than declared, so the
        # launch form and the spec cannot disagree — measured, three shipped templates offered an
        # input nothing read.
        **_contract_review(definition),
        # UP-R13.3: this template's derived benchmark, produced here rather than in a separate
        # script — an eval no live surface imports is an eval nobody runs.
        **_eval_surface(template, definition),
        # UP-R4/R7: the announce block, the cost shape, and the markdown artifact. Veto-first
        # ordering — detection and risk decide whether to read on; the pipeline is what they read
        # if they do.
        **_review_surface(goal, definition, routing),
        # UP-R3: and whether this system can actually RUN it. Same checker as the run-start gate,
        # so approving this plan is not approving a run that dies at node one.
        **_preflight_surface(definition),
        # UP-R4/R6: what autonomy this plan may be RUN at, and what it will stop for. Computed at
        # plan time so "this will stop you twice" is a fact before approval rather than a discovery
        # made while waiting.
        **_autonomy_surface(definition),
        "proposed_root": definition.get("root"),
        "template_inputs": definition.get("inputs") or {},
        # How this template is actually driven — few-shot for the edit the model is about to make.
        "steering_examples": meta.get("steering_examples") or [],
        "next_step": (
            f"Adapt this template's tree to the goal, then call workflow_check to validate "
            f"it. To run {template!r} UNCHANGED, skip authoring and call "
            f"workflow_start with its inputs instead — a template that already fits does not "
            f"need a copy."
        ),
        "note": (
            "This is the template's expanded tree: macros are already compiled to core nodes and "
            "shared blocks are already substituted, so what you see is what the engine runs."
        ),
    }
    return body, f"Plan for '{goal}' from template {template!r}"
