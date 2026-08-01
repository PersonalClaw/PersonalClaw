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
from typing import Any

from personalclaw.workflows import service
from personalclaw.workflows.context_block import needs_staging, staged_spec_echo

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
                "that…' requests. To save the result, pass it to workflow_author."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "What the workflow should accomplish, in plain language.",
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
                },
                "required": ["goal"],
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
                "or object. With no answer this just lifts a pause. Each answer is consumed "
                "once — calling twice will not approve twice. If several gates are pending "
                "you must name one with resume_token."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": run_id,
                    "answer": {
                        "description": "true/false for an approval; a value or object otherwise."
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
        return _fmt(
            _run(
                service.author_def(
                    name=str(args.get("name", "") or ""),
                    root=root,
                    description=str(args.get("description", "") or ""),
                    inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else None,
                    tags=[str(t) for t in (args.get("tags") or [])],
                    save=bool(args.get("save", True)),
                )
            )
        )

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
    """Template-unaware v1 (WORKFLOWS-V2 §4).

    Returns a SCAFFOLD plus the manifest, deliberately: the template-aware planner is
    UNIVERSAL-PLANNING's, and inventing a half-planner here would have to be deleted when
    the real one lands. What this does give the model is everything it needs to author the
    spec itself in the next turn — the shapes the engine accepts, and a starting tree it can
    edit — instead of guessing at a schema.
    """
    goal = str(args.get("goal", "") or "").strip()
    if not goal:
        return "Error [WF_PLAN_GOAL_REQUIRED]: 'goal' is required."
    rigor = str(args.get("rigor", "standard") or "standard")
    if rigor not in ("minimal", "standard", "deep"):
        rigor = "standard"

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

    body = {
        "ok": True,
        "planner": "scaffold-v1",
        "goal": goal,
        "rigor": rigor,
        "proposed_root": scaffold,
        "next_step": (
            "Adapt this tree to the goal, then call workflow_author with save=false to "
            "validate it before saving."
        ),
        "note": (
            "This is a structural scaffold, not a domain plan — the template-aware planner "
            "lands with UNIVERSAL-PLANNING. Use the manifest below for the shapes the "
            "engine accepts."
        ),
        "manifest": {k: v for k, v in service.manifest().items() if k != "ok"},
    }
    return _fmt(body, summary=f"Draft plan for: {goal}")
