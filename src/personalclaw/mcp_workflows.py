"""Workflows tool category — author, run, steer and inspect composable workflows.

The chat surface over the v2 engine: 23 tools. Every one delegates to
`workflows.service`, the single implementation the REST routes also call —
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

import inspect
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from personalclaw import approval_answer
from personalclaw.safety_flags import confirm_granted, yes_or_no
from personalclaw.tool_providers.base import ToolFailure, tool_failure
from personalclaw.validation import decode_json_text
from personalclaw.workflows import chat_runs, service, template_pipeline, versions
from personalclaw.workflows.agent_plans import _current_session_id, _run, plan_answer
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


#: The tools that change a run: an edit, a rewind, a run from a step, a fork (:func:`_change`).
_RUN_CHANGES = ("workflow_edit", "workflow_rewind", "workflow_run_from", "workflow_fork")


def _change(name: str, run_id: str, args: dict[str, Any]) -> str:
    """A change of the run *run_id* by the tool *name* (:data:`_RUN_CHANGES`), made for the chat
    whose turn this is, named as `workflow_start` names it: the work of a run an allowed automation
    started changes only a run of its own (`automation_version.change_refusal`)."""
    from personalclaw.mcp_core import _resolve_session_key

    caller = _resolve_session_key()
    if name == "workflow_fork":
        return _fmt(
            _on_engine(
                lambda supervisor: service.fork_run(
                    run_id,
                    checkpoint_id=str(args.get("checkpoint_id", "") or ""),
                    note=str(args.get("note", "") or ""),
                    supervisor=supervisor,
                    session_key=caller,
                )
            ),
            summary=SUMMARIES["workflow_fork"],
        )
    confirm = confirm_granted(args, "confirm_cascade")
    if name == "workflow_edit":
        expect = args.get("expect_version")
        return _fmt(
            _on_engine(
                lambda supervisor: service.edit_run(
                    run_id,
                    args["ops"],
                    supervisor=supervisor,
                    expect_version=int(expect) if isinstance(expect, (int, float)) else None,
                    confirm_cascade=confirm,
                    session_key=caller,
                )
            )
        )
    node_id = str(args.get("node_id", "") or "")
    if name == "workflow_rewind":
        return _fmt(
            _on_engine(
                lambda supervisor: service.rewind_run(
                    run_id,
                    node_id,
                    supervisor=supervisor,
                    redo_effects=yes_or_no(args.get("redo_effects")) is True,
                    force=yes_or_no(args.get("force")) is True,
                    confirm_cascade=confirm,
                    session_key=caller,
                )
            )
        )
    return _fmt(
        _on_engine(
            lambda supervisor: service.run_from(
                run_id, node_id, supervisor=supervisor, confirm_cascade=confirm, session_key=caller
            )
        )
    )


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

    The echo is the whole reason inspect tools are worth calling before a
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

    if name == "workflow_edit_preview":
        return _fmt(service.preview_edit(run_id, args["ops"]))

    if name in _RUN_CHANGES:
        return _change(name, run_id, args)

    if name == "workflow_skip":
        node_ids = [str(n) for n in args["node_ids"]]
        return _fmt(
            _on_engine(
                lambda supervisor: service.skip_nodes(run_id, node_ids, supervisor=supervisor)
            )
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


def freeze_authored_candidate(args: dict[str, Any]) -> None:
    """Freeze a validated-but-unsaved spec as a SESSION-scoped candidate, after a
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
