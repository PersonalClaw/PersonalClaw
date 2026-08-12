"""Workflows tool category — author, run, steer and inspect composable workflows.

The chat surface over the v2 engine: 19 tools (WORKFLOWS-V2 §4). Every one delegates to
`workflows.service`, the single implementation the REST routes (Slice 7a) will also call —
two surfaces over one engine must not grow two behaviours.

Exposes `_list_tools` / `_call_tool` in the same shape as `mcp_prompts` / `mcp_memory`, so
the in-process `InProcessMcpToolProvider` and the aggregating `mcp-core` server consume it
through one path. Unlike the other categories this one does NOT go over HTTP: the engine is
in-process, and a chat tool that round-tripped through the gateway to reach an object in the
same process would add a failure mode (and a port dependency) for nothing.

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
import json
import logging
from typing import Any

from personalclaw.workflows import grill_protocol as grill_mod
from personalclaw.workflows import intent as intent_mod
from personalclaw.workflows import rigor as rigor_mod
from personalclaw.workflows import service, template_pipeline
from personalclaw.workflows.context_block import needs_staging, staged_spec_echo

logger = logging.getLogger(__name__)

#: Read-only tools — no state change, safe to call while thinking. Kept explicit so a
#: reviewer can see at a glance which tools can be called freely.
READ_ONLY_TOOLS = frozenset(
    {
        "workflow_list_defs",
        "workflow_get_def",
        "workflow_status",
        "workflow_observe",
        "workflow_output",
        "workflow_manifest",
    }
)


def _list_tools() -> list[dict[str, Any]]:
    run_id = {"type": "string", "description": "The run id (from workflow_start)."}
    return [
        {
            "name": "workflow_author",
            "description": (
                "Save a workflow definition from an explicit DAG spec — the low-level "
                "authoring tool. Use when you already know the node structure; use "
                "workflow_plan instead to turn a natural-language goal into a spec. Pass "
                "save=false to VALIDATE ONLY and get the issue list back without writing "
                "anything, which is the cheap way to iterate. Never put a literal API key "
                "in the spec: reference credentials as {{secret:KEY}}."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Definition name: lowercase letters, digits, hyphens.",
                    },
                    "description": {"type": "string"},
                    "root": {
                        "type": "object",
                        "description": (
                            "The root node of the spec tree. Call workflow_manifest for the "
                            "node taxonomy, binding pipes and allowed shapes."
                        ),
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Declared inputs: name → {type, required, default, help}.",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "save": {
                        "type": "boolean",
                        "description": "false = validate only, write nothing (default true).",
                    },
                },
                "required": ["name", "root"],
            },
        },
        {
            "name": "workflow_plan",
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
                        "type": "object",
                        "description": "Values for the definition's declared inputs.",
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
            "description": (
                "Edit a RUNNING workflow's unexecuted nodes. Ops: update_node, insert, "
                "delete, move, set_input, skip. Returns a cascade preview naming every node "
                "that would re-run; if it would re-run already-completed work you must "
                "resubmit with confirm_cascade=true. Running and finished nodes cannot be "
                "edited — rewind one first. Pass expect_version from workflow_status to "
                "avoid editing a spec that changed under you."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "ops": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Mutation ops. See workflow_manifest for the catalog.",
                    },
                    "expect_version": {"type": "integer"},
                    "confirm_cascade": {
                        "type": "boolean",
                        "description": "Accept re-running completed nodes.",
                    },
                    "preview_only": {
                        "type": "boolean",
                        "description": "true = compute the cascade and queue NOTHING.",
                    },
                },
                "required": ["run_id", "ops"],
            },
        },
        {
            "name": "workflow_skip",
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
            "description": (
                "Reset a node AND everything that consumes its output, so they re-run — the "
                "in-place fix for 'redo this stage with a better prompt'. Consumers are "
                "found through data bindings, not tree position, so a later sibling reading "
                "the node's output is reset too. Outputs are archived, not destroyed. If a "
                "node in the reset region already fired an external effect, pass "
                "redo_effects=true to deliberately fire it again."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "node_id": {"type": "string"},
                    "redo_effects": {"type": "boolean"},
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
            "description": (
                "Re-run only what comes AFTER a node, keeping that node's output as-is — "
                "'redo the synthesis with the same gathered data'. Cheaper than rewind when "
                "the upstream work was expensive and correct."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": run_id, "node_id": {"type": "string"}},
                "required": ["run_id", "node_id"],
            },
        },
        {
            "name": "workflow_fork",
            "description": (
                "Branch a NEW run from this one, leaving the original untouched — for "
                "exploring an alternative when the first result must be preserved. Works on "
                "a finished run. The fork shares the filesystem workspace and any external "
                "resources the original created; the response names exactly what is NOT "
                "isolated. The child starts as a draft so you can edit it before running it."
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
            "name": "workflow_pause",
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
            "description": (
                "Answer a workflow that is waiting on a human, or clear a pause. For an "
                "approval gate pass answer=true/false; for a choice or form pass the value "
                "or object. To change ONE step instead of accepting or rejecting the whole "
                'plan, pass answer={"revise": {"step_ref": "<step id>", "comment": "what to '
                "change\"}} — that step's instruction is amended and the gate re-asks, "
                "leaving every other step exactly as it was. With no answer this just lifts a "
                "pause. Each answer is consumed once — calling twice will not approve twice. "
                "If several gates are pending you must name one with resume_token."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "answer": {
                        "description": (
                            "true/false for an approval; a value or object otherwise; or "
                            '{"revise": {"step_ref", "comment"}} to amend one step and re-ask.'
                        )
                    },
                    "resume_token": {
                        "type": "string",
                        "description": "Which gate to answer (required if several are pending).",
                    },
                    "always_allow": {
                        "type": "boolean",
                        "description": (
                            "Auto-approve this same operation for the rest of THIS run "
                            "(cleared if the run is rewound)."
                        ),
                    },
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_cancel",
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
            "description": (
                "Diagnose workflow runs that drifted — nodes stuck running, gates nobody "
                "can answer, expired waits, runs whose status was never written. Defaults "
                "to dry_run=true, which only REPORTS. Pass dry_run=false to repair; a run "
                "with a live controller is reported and left alone either way."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dry_run": {
                        "type": "boolean",
                        "description": "true (default) = report only; false = repair.",
                    }
                },
            },
        },
        {
            "name": "workflow_manifest",
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


def _supervisor() -> Any:
    """The workflow supervisor, or None when the gateway has not wired one.

    Fetched per call rather than cached: a cached None taken at import time would make
    every tool permanently inert in a process that wires services later.
    """
    try:
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
    except Exception:
        return None
    return getattr(services, "workflows", None) if services else None


def _run(coro: Any) -> Any:
    """Run a coroutine from the sync tool boundary.

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

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _fmt(body: dict[str, Any], *, summary: str = "") -> str:
    """Render a service result for the model.

    A failure leads with its CODE so the model can branch on it, and keeps the payload so
    it can see the issue list rather than guessing what to fix.
    """
    if not body.get("ok", True):
        code = body.get("code", "WF_ERROR")
        message = body.get("message", "")
        extra = {k: v for k, v in body.items() if k not in ("ok", "code", "message")}
        text = f"Error [{code}]: {message}"
        if extra:
            text += "\n" + json.dumps(extra, indent=2, ensure_ascii=False, default=str)
        return text
    payload = {k: v for k, v in body.items() if k != "ok"}
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    return f"{summary}\n{rendered}" if summary else rendered


def _call_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch one tool, appending the staged-turn spec echo where it applies.

    The echo (WF2-R20f) is the whole reason inspect tools are worth calling before a
    mutation: the model mutates what it just SAW rather than the spec it generated earlier,
    which diverges from disk the moment anything else touches the run.
    """
    out = _dispatch(name, args or {})
    if needs_staging(name):
        echo = staged_spec_echo(str((args or {}).get("run_id", "") or ""))
        if echo:
            return f"{out}\n\n{echo}"
    return out


def _dispatch(name: str, args: dict[str, Any]) -> str:
    args = args or {}
    run_id = str(args.get("run_id", "") or "")

    if name == "workflow_manifest":
        return _fmt(
            service.manifest(),
            summary="Workflow authoring reference (generated from the engine):",
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

    if name == "workflow_author":
        root = args.get("root")
        if not isinstance(root, dict):
            return "Error [WF_DEF_ROOT_REQUIRED]: 'root' must be the spec's root node object."
        save = bool(args.get("save", True))
        result = _run(
            service.author_def(
                name=str(args.get("name", "") or ""),
                root=root,
                description=str(args.get("description", "") or ""),
                inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else None,
                tags=[str(t) for t in (args.get("tags") or [])],
                save=save,
            )
        )
        if not save and result.get("ok") and result.get("valid"):
            # UP-R9 discover-then-freeze: a generated spec that VALIDATED but was not saved is
            # exactly what used to be thrown away. Freezing it as a session-scoped candidate is
            # what stops the next similar intent re-generating a different graph. Not saved to the
            # library — SESSION scope, promoted by reuse, because a spec that parsed is not yet a
            # spec that worked.
            _freeze_authored_candidate(args)
        return _fmt(result)

    if name == "workflow_plan":
        return _plan(args)

    if name == "workflow_start":
        return _fmt(
            _run(
                service.start_run(
                    name=str(args.get("name", "") or ""),
                    inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else None,
                    mode=str(args.get("mode", "background") or "background"),
                    supervisor=_supervisor(),
                    project_id=str(args.get("project_id", "") or ""),
                    idempotency_key=str(args.get("idempotency_key", "") or ""),
                )
            ),
            summary="Workflow run started.",
        )

    if name == "workflow_status":
        return _fmt(service.status(run_id))

    if name == "workflow_observe":
        return _fmt(_run(service.observe(run_id, int(args.get("duration_ms") or 0))))

    if name == "workflow_output":
        return _fmt(service.output(run_id, str(args.get("node_id", "") or "")))

    if name == "workflow_edit":
        ops = args.get("ops")
        if not isinstance(ops, list) or not ops:
            return "Error [WF_MUT_NO_OPS]: 'ops' must be a non-empty array of mutation ops."
        if bool(args.get("preview_only")):
            return _fmt(service.preview_edit(run_id, ops))
        expect = args.get("expect_version")
        return _fmt(
            service.edit_run(
                run_id,
                ops,
                supervisor=_supervisor(),
                expect_version=int(expect) if isinstance(expect, (int, float)) else None,
                confirm_cascade=bool(args.get("confirm_cascade")),
            )
        )

    if name == "workflow_skip":
        node_ids = args.get("node_ids")
        if not isinstance(node_ids, list) or not node_ids:
            return "Error [WF_NO_NODE_IDS]: 'node_ids' must be a non-empty array."
        return _fmt(
            service.skip_nodes(run_id, [str(n) for n in node_ids], supervisor=_supervisor())
        )

    if name == "workflow_rewind":
        return _fmt(
            service.rewind_run(
                run_id,
                str(args.get("node_id", "") or ""),
                supervisor=_supervisor(),
                redo_effects=bool(args.get("redo_effects")),
                force=bool(args.get("force")),
            )
        )

    if name == "workflow_run_from":
        return _fmt(
            service.run_from(run_id, str(args.get("node_id", "") or ""), supervisor=_supervisor())
        )

    if name == "workflow_fork":
        return _fmt(
            service.fork_run(
                run_id,
                checkpoint_id=str(args.get("checkpoint_id", "") or ""),
                note=str(args.get("note", "") or ""),
                supervisor=_supervisor(),
            ),
            summary="Forked a new run; the original is unchanged.",
        )

    if name == "workflow_pause":
        return _fmt(service.pause_run(run_id, supervisor=_supervisor()))

    if name == "workflow_resume":
        return _fmt(
            service.resume_run(
                run_id,
                supervisor=_supervisor(),
                token=str(args.get("resume_token", "") or ""),
                answer=args.get("answer"),
                always_allow=bool(args.get("always_allow")),
            )
        )

    if name == "workflow_cancel":
        return _fmt(service.cancel_run(run_id, supervisor=_supervisor()))

    if name == "workflow_audit":
        return _fmt(
            service.audit(dry_run=bool(args.get("dry_run", True)), supervisor=_supervisor())
        )

    if name == "workflow_delete_def":
        return _fmt(_run(service.delete_def(str(args.get("name", "") or ""))))

    return f"Error: unknown workflows tool {name!r}."


def _plan(args: dict[str, Any]) -> str:
    """Scaffold + manifest, or a real TEMPLATE when one is named (WORKFLOWS-V2 §4, 9b).

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
                f"Error [WF_PLAN_SESSION_NOT_MINEABLE]: no transcript with usable turns for "
                f"session {source_session_id!r}; pass 'goal' explicitly."
            )
        return "Error [WF_PLAN_GOAL_REQUIRED]: 'goal' is required."

    project_id = str(args.get("project_id", "") or "").strip()

    template = str(args.get("template", "") or "").strip()
    if template:
        # An explicitly user-named template WINS. The router's job is choosing when nobody has
        # chosen; overriding a stated request would make the matcher an obstacle.
        return _plan_from_template(goal, template, mined=mined, source_session_id=source_session_id)

    # UNIVERSAL-PLANNING S1: classify, then match, before any generation. Both are zero-token and
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

    # S45: the rigor axis, applied to the SCAFFOLD path only — a matched template returned above
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
        "routing": {
            "intent": classified.to_dict(),
            "match": match.to_dict() if match is not None else {"reason": "matcher unavailable"},
        },
        "proposed_root": proposed,
        # S45: which rigor path ran and why. A user who got a thin plan needs to know it was the
        # fast path rather than the planner doing badly.
        "rigor_note": rigor_mod.rigor_note(classified, requested=requested),
        # UP-R9: what the source session actually did, when one was named. Present only when
        # mining produced something — an empty block would read as "that session did nothing".
        **_mined_surface(mined, source_session_id),
        **({"grounding": grounded} if grounded else {}),
        **_grill_surface(goal, classified, {"root": proposed}, topics=_plan_topics(goal)),
        "next_step": (
            "Adapt this tree to the goal, then call workflow_author with save=false to "
            "validate it before saving."
        ),
        "note": (
            "Fill the shape's slots and adapt the tree, then call workflow_author with "
            "save=false to validate. The `grounding` block below is read from THIS system's live "
            "registries — anything not in it does not exist here. If you cannot plan the goal "
            'with what is listed, return {"cannot_plan": "<why>"} rather than inventing a node.'
        ),
        "manifest": {k: v for k, v in service.manifest().items() if k != "ok"},
    }
    return _fmt(body, summary=f"Draft plan for: {goal}")


def _freeze_authored_candidate(args: dict[str, Any]) -> None:
    """Freeze a validated-but-unsaved spec as a SESSION-scoped candidate (UP-R9).

    Best-effort and silent: freezing is an optimization for the NEXT similar request, and a store
    write that failed must not turn a successful dry-run validation into an error. The user asked to
    validate a spec; they get that answer either way.

    Parameterized before freezing, so the candidate the matcher later offers is a reusable shape
    rather than one run's literal entities — a candidate carrying a concrete hostname would match a
    similar goal and then plan against the wrong target.
    """
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
    """This turn's session key, or '' — the SESSION scope's identity.

    The session KEY (`dashboard:chat-1-…`), which is what a chat turn can actually know; the on-disk
    session id belongs to `SessionMap` and is not reachable from a tool call. What matters for
    scoping is only that the value is stable within a conversation and distinct between them, and
    the key is both. '' when unresolvable, which makes the candidate visible to every session —
    correct for a headless caller that has no session to be private to.
    """
    import os

    return os.environ.get("PERSONALCLAW_SESSION_KEY", "")


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
            # WF2UNI-13: and which of those stops SURVIVE at unattended. The recommended mode is
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
        from personalclaw.llm_helpers import one_shot_completion

        prompt = (
            "Rephrase this workflow request as a short, plain description of the desired OUTCOME, "
            "in one sentence, using concrete nouns. Do not name any template or tool.\n\n"
            f"Request: {intent_text}"
        )
        try:
            return str(_run(one_shot_completion(prompt, use_case="background")) or "").strip()
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
) -> str:
    """Plan by starting from a real template's tree rather than a generic scaffold.

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
            f"Error [WF_PLAN_TEMPLATE_NOT_FOUND]: no workflow definition named "
            f"{template!r}. Available: {', '.join(names) or 'none'}."
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
        # UP-R4/R6: what autonomy this plan may be RUN at, and what it will stop for. Computed at
        # plan time so "this will stop you twice" is a fact before approval rather than a discovery
        # made while waiting.
        **_autonomy_surface(definition),
        "proposed_root": definition.get("root"),
        "template_inputs": definition.get("inputs") or {},
        # How this template is actually driven — few-shot for the edit the model is about to make.
        "steering_examples": meta.get("steering_examples") or [],
        "next_step": (
            f"Adapt this template's tree to the goal, then call workflow_author with save=false "
            f"to validate it. To run {template!r} UNCHANGED, skip authoring and call "
            f"workflow_start with its inputs instead — a template that already fits does not "
            f"need a copy."
        ),
        "note": (
            "This is the template's expanded tree: macros are already compiled to core nodes and "
            "shared blocks are already substituted, so what you see is what the engine runs."
        ),
    }
    return _fmt(body, summary=f"Plan for '{goal}' from template {template!r}")
