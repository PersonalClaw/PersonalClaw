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

import json
import re
import time
from typing import Any

from personalclaw.mcp_core import _get, _post, _resolve_session_key
from personalclaw.tool_providers.base import ToolFailure, tool_failure
from personalclaw.workflows import batch_compile
from personalclaw.workflows.batch_compile import LeafTask


def _wf_depth() -> int:
    """The workflow depth of the leaf this call runs for, from the lineage `lineage_env` writes.

    Read from the leaf's lineage rather than passed as a tool argument on purpose: a depth the
    CALLER supplies is a depth a leaf can understate, and `depth_lint` refusing a nested batch
    would then be advisory. The engine writes it (``engine.WF_DEPTH_KEY``); a leaf inherits it —
    through its tool server's environment, or the native runtime's binding
    (`mcp_shared.leaf_depth`).
    """
    from personalclaw.mcp_shared import leaf_depth

    return leaf_depth()


def _leaf_specs(tasks: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Normalize `tasks[]` items to `(task_text, contract)` pairs.

    A batch item is either a plain string or a contract object carrying the declarations
    `compile_batch` requires. Both are normalized here so the compile path sees ONE shape —
    branching on the item type further down would mean two code paths over one input.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for item in tasks:
        if isinstance(item, dict):
            text = str(item.get("task", "") or "").strip()
            if text:
                out.append((text, item))
        elif isinstance(item, str) and item.strip():
            out.append((item.strip(), {}))
    return out


def _to_leaf(text: str, spec: dict[str, Any], agent: str) -> LeafTask:
    """One `tasks[]` item as a `LeafTask`, each declaration read by its type
    (`batch_compile.leaf_from_item`): a plain string item is its text, an object its contract."""
    return batch_compile.leaf_from_item(spec or text, agent=agent)


#: Compiled-batch def names are minted per call and must satisfy `models.valid_name` (lowercase,
#: digits, hyphens — it becomes a directory).
_NAME_UNSAFE = re.compile(r"[^a-z0-9-]+")


def _batch_def_name() -> str:
    return f"subagent-batch-{int(time.time() * 1000)}"


def _findings_report(result: batch_compile.CompileResult) -> str:
    """A refusal a model can act on: the findings, then what to do about them."""
    lines = ["the batch did not compile — each leaf needs an explicit contract."]
    for finding in result.findings:
        lines.append(f"  [{finding.severity}] {finding.code}: {finding.message}")
    lines.append(
        "\nPass each item of 'tasks' as an object with 'task', a short 'title' naming it for the "
        "owner, 'objective', 'output_format' and 'boundary' (each declaration at least "
        f"{batch_compile.MIN_DECLARATION_CHARS} characters), plus 'capability' and 'writes' "
        "(a list of paths) when the leaf mutates, and 'off_limits' (a list of paths) for what "
        "it must not write."
    )
    return tool_failure("\n".join(lines))


def _requested(args: dict[str, Any]) -> tuple[list[tuple[str, dict[str, Any]]], list[str]] | Any:
    """``(leaf specs, agents)`` a ``subagent_run`` call asks for, or why it asks for nothing that
    can start: no task, or an ``agents`` list that does not match its tasks."""
    tasks = args.get("tasks")
    task = args.get("task")
    # Support both single task and batch tasks. A batch item may be a plain string (the legacy
    # shape) or a contract object — `_leaf_specs` normalizes both to (text, spec) so the compile
    # path sees one shape.
    if tasks and isinstance(tasks, list):
        leaf_specs = _leaf_specs(tasks)
    elif task:
        leaf_specs = [(str(task), {})]
    else:
        return tool_failure("task or tasks is required")
    agents_list = [str(a) for a in args.get("agents") or []]
    if agents_list and len(agents_list) != len(leaf_specs):
        return tool_failure(
            f"agents length ({len(agents_list)}) must match tasks length ({len(leaf_specs)})"
        )
    return leaf_specs, agents_list


def _compile(
    leaf_specs: list[tuple[str, dict[str, Any]]],
    *,
    agent: str,
    agents_list: list[str],
    depth: int,
    name: str,
) -> tuple[list[LeafTask], batch_compile.CompileResult]:
    """The batch's leaves and what the compiler makes of them (`batch_compile.compile_batch`)."""
    leaves = [
        _to_leaf(text, spec, agents_list[i] if i < len(agents_list) else agent)
        for i, (text, spec) in enumerate(leaf_specs)
    ]
    return leaves, batch_compile.compile_batch(leaves, depth=depth, run_name=name)


def _spawn_refusal(args: dict[str, Any]) -> Any:
    """What ``subagent_run`` refuses before it starts anything, whatever anyone answers: a call
    that asks for nothing that can start (:func:`_requested`), and a batch that does not compile,
    in the compiler's own findings. None for a call it starts."""
    requested = _requested(args)
    if isinstance(requested, ToolFailure):
        return requested
    leaf_specs, agents_list = requested
    if len(leaf_specs) < batch_compile.COMPILE_THRESHOLD:
        return None
    _, result = _compile(
        leaf_specs,
        agent=str(args.get("agent") or ""),
        agents_list=agents_list,
        depth=_wf_depth(),
        name=_batch_def_name(),
    )
    return None if result.compiled and result.ok else _findings_report(result)


def _run_compiled_batch(
    leaf_specs: list[tuple[str, dict[str, Any]]],
    *,
    agent: str,
    agents_list: list[str],
    parent_session: str,
    depth: int,
    cwd: str,
) -> str:
    """Compile `tasks[]` into one run and start it.

    The persistence that makes the widget survive a restart is NOT a new store: the compiled spec
    is saved as a workflow definition and the run row references it by `workflow_name`, so a
    restarted gateway reloads both from disk and the widget rebuilds from the run record — the same
    path every other workflow run already uses. Per-branch retry is likewise the existing
    `run-from` route over the compiled node ids.
    """
    name = _batch_def_name()
    leaves, result = _compile(
        leaf_specs, agent=agent, agents_list=agents_list, depth=depth, name=name
    )
    if not result.compiled or not result.ok:
        return _findings_report(result)

    root = result.spec.get("root")
    if not isinstance(root, dict):
        return tool_failure("the compiler produced no root node")
    saved = _post(
        "/api/workflows",
        {
            "name": name,
            "root": root,
            "description": f"Compiled batch of {len(leaves)} leaf task(s) from subagent_run.",
            # The compiled tree is machine-generated and lint-clean by construction; `strict`
            # would reject on a convention WARNING and refuse a batch the compiler approved.
            "strict": False,
            # The compiler's §4.1 isolation declaration, sent EXPLICITLY. `root` alone would leave
            # it behind: the authoring path takes named fields, so a top-level key that is not
            # passed is a key the persisted def never sees — and the applier reads the def.
            "workspace": result.spec.get(batch_compile.WORKSPACE_KEY) or {},
        },
    )
    if saved.get("error"):
        return tool_failure(f"could not persist the compiled batch: {saved['error']}")

    body: dict[str, Any] = {"name": name, "mode": "background"}
    if cwd:
        body["inputs"] = {"cwd": cwd}
    started = _post("/api/workflows/runs", body)
    if started.get("error"):
        return tool_failure(f"could not start the compiled batch: {started['error']}")
    run_id = str(started.get("run_id", "") or "")

    lines = [
        # As JSON, the shape every workflow tool returns its run in: the chat reads the run id out
        # of it to show the batch's live progress card, each leaf a step with how it ended.
        json.dumps({"run_id": run_id, "status": "running"}),
        f"Compiled {len(leaves)} tasks into one batch run ({run_id or 'pending'}); each branch "
        "is individually retryable.",
    ]
    for index, leaf in enumerate(leaves):
        node_id = leaf.node_id(index)
        posture = result.postures.get(node_id, {})
        mode = "read-only" if posture.get("read_only") else "mutating"
        lines.append(f"  {node_id} [{mode}]: {leaf.label(index)}")
    if result.serialized:
        lines.append(f"\nWrite-bearing leaves run one at a time: {', '.join(result.serialized)}")
    warnings = [f for f in result.findings if f.severity == "warn"]
    for finding in warnings:
        lines.append(f"  [warn] {finding.code}: {finding.message}")
    if parent_session:
        lines.append(
            "\nIts results are not sent to this conversation. Wait for it with "
            "workflow_observe(run_id), then read each branch's outcome with "
            "workflow_status(run_id): a branch whose every tool call was refused ends failed, "
            "saying why."
        )
    return "\n".join(lines)


def _list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "subagent_run",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Spawn subagent(s) to run tasks in the background. One task ('task') returns "
                "at once, and its result arrives as a [Subagent completion event] message in "
                "your conversation: WAIT for it before responding to the user. Two or more "
                "('tasks') run in parallel as one batch run, each task a contract the batch is "
                "checked against before it starts. A batch's results are not sent to this "
                "conversation: wait with workflow_observe and read them with workflow_status, "
                "on the run_id it returns. More tasks than may run at once wait their turn."
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
                        "items": batch_compile.leaf_item_schema(),
                        "description": (
                            "Two or more tasks to run in parallel as one batch, each an object: "
                            "what to do, what it is for, the shape of its answer and what it "
                            "must not touch."
                        ),
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
                            "Must be inside the workspace, or under a folder the owner added to "
                            "agent.subagent_cwd_allowed_roots (none by default). "
                            "Applies to all tasks in a batch spawn."
                        ),
                    },
                },
            },
        },
        {
            "name": "best_of_n",
            "annotations": {"readOnlyHint": False},
            "description": (
                "Sample N candidate answers to the SAME prompt in parallel (each at a "
                "different temperature), have a judge score them against your criteria, "
                "and return the winner plus the full slate. COSTS N MODEL CALLS — confirm "
                "N and the criteria with the user first (the best-of-n skill owns that "
                "gate). N is capped at 5. Use for 'give me N versions and pick the best', "
                "'try a few options', 'sample and choose'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The prompt every candidate answers (identical for all N).",
                    },
                    "n": {
                        "type": "integer",
                        "description": "How many candidates to sample (1-5, default 3).",
                    },
                    "criteria": {
                        "type": "string",
                        "description": (
                            "What 'best' means here — the judge scores each candidate "
                            "against this. Confirm it with the user."
                        ),
                    },
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "subagent_list",
            "annotations": {"readOnlyHint": True},
            "description": "List all running and completed subagents (read-only, no commands executed)",  # noqa: E501
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "subagent_status",
            "annotations": {"readOnlyHint": True},
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


def _best_of_n(args: dict[str, Any]) -> str:
    """`best_of_n` — the chat/tool entry point for the sampling core (HARNESS-CRAFT §2.1).

    A thin wrapper over ``personalclaw.sampling.best_of_n``: the fan-out, judging,
    selection, metering and outcome record all live in the core so this tool, the
    bundled ``best-of-n`` skill and the HC-5 workflow template share ONE
    implementation. Returns the whole slate as JSON so the presenting model can show
    the winner, collapse the runners-up, and honor "use #2" verbatim.
    """
    import json as _json

    from personalclaw.mcp_artifacts import _run_async  # shared sync→async bridge
    from personalclaw.sampling import best_of_n

    prompt = str(args.get("prompt", "") or "").strip()
    if not prompt:
        return tool_failure("provide a `prompt` to sample.")
    n = int(args.get("n") or 3)
    criteria = str(args.get("criteria", "") or "")
    try:
        result = _run_async(best_of_n(prompt, n, criteria))
    except Exception as exc:  # noqa: BLE001 — a tool must answer, not traceback
        return tool_failure(f"best-of-N sampling failed: {type(exc).__name__}: {exc}")
    if result["winner"] is None:
        return (
            f"No candidate: {result['note']}. Nothing was selected — try again or answer directly."
        )
    return _json.dumps(result, ensure_ascii=False)


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    if name == "best_of_n":
        return _best_of_n(args)
    if name == "subagent_run":
        # Re-validate to make schema enforcement visible at the extraction point.
        # _call_tool() already validates, but defense-in-depth ensures agent/agents
        # are schema-clean even if the call chain changes.
        from personalclaw.validation import SPAWN_RUN_SCHEMA, validate_tool_args

        args = validate_tool_args(args, SPAWN_RUN_SCHEMA)

        requested = _requested(args)
        if isinstance(requested, ToolFailure):
            return requested
        leaf_specs, agents_list = requested
        task_list = [text for text, _ in leaf_specs]

        # Read parent session key so completions inject back into this session.
        parent_session = _resolve_session_key()

        # Fire-and-forget — gateway's SubagentManager queues excess tasks
        # and auto-spawns them as sessions free up.
        agent = args.get("agent") or ""
        max_turns = args.get("max_turns") or 0
        cwd = args.get("cwd") or ""

        # N>=2 is a BATCH: compiled to one `parallel[stage...]` run rather than N independent
        # fire-and-forget spawns. The difference is not cosmetic — N spawns have no run record, so
        # they cannot be shown as one widget, cannot survive a restart, and cannot be retried per
        # branch. `compile_batch` owns the threshold (COMPILE_THRESHOLD), the lints and the
        # capability posture; this seam only routes into it and reports what it decided.
        if len(task_list) >= batch_compile.COMPILE_THRESHOLD:
            return _run_compiled_batch(
                leaf_specs,
                agent=agent,
                agents_list=agents_list,
                parent_session=parent_session,
                depth=_wf_depth(),
                cwd=cwd,
            )

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
            return tool_failure("invalid agent_id")
        d = _get(f"/api/spawn/{agent_id}")
        if d.get("error"):
            return tool_failure(f"{d['error']}")
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


def _preflight(name: str, raw_args: dict[str, Any]) -> Any:
    """What these tools refuse before anyone is asked to approve a call: a tool this leaf may not
    call and arguments the tool's schema refuses (``mcp_shared.admitted_arguments``), then for
    ``subagent_run`` what it refuses before starting anything (:func:`_spawn_refusal`)."""
    from personalclaw.mcp_shared import admitted_arguments

    args = admitted_arguments(name, raw_args, _validate_args)
    if isinstance(args, ToolFailure):
        return args
    return _spawn_refusal(args) if name == "subagent_run" else None


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
