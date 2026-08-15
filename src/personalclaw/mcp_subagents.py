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

import os
import re
import time
from typing import Any

from personalclaw.mcp_core import _get, _post, _resolve_session_key
from personalclaw.workflows import batch_compile
from personalclaw.workflows.batch_compile import Capability, LeafTask


def _wf_depth() -> int:
    """This process's workflow depth, from the env `lineage_env` threads into a leaf.

    Read from the environment rather than passed as a tool argument on purpose: a depth the
    CALLER supplies is a depth a leaf can understate, and `depth_lint` refusing a nested batch
    would then be advisory. The engine writes it (``engine.WF_DEPTH_KEY``); a leaf inherits it.
    """
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    try:
        return int(os.environ.get(WF_DEPTH_KEY, "0") or "0")
    except ValueError:
        return 0


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
    """One `tasks[]` item as a `LeafTask`.

    Missing declarations are passed through EMPTY rather than defaulted: `contract_lint` exists
    to refuse an under-specified leaf, and a synthesized objective would satisfy the gate without
    satisfying the requirement — the exact failure the contract was written to prevent.
    """
    raw_capability = str(spec.get("capability", "") or "").strip().lower()
    capability = Capability.MUTATING if raw_capability == "mutating" else Capability.RESEARCH
    writes = [str(w) for w in (spec.get("writes") or []) if str(w).strip()]
    schema = spec.get("output_schema")
    return LeafTask(
        task=text,
        objective=str(spec.get("objective", "") or ""),
        output_format=str(spec.get("output_format", "") or ""),
        boundary=str(spec.get("boundary", "") or ""),
        agent=str(spec.get("agent", "") or agent or ""),
        model_ref=str(spec.get("model", "") or ""),
        capability=capability,
        writes=writes,
        output_schema=schema if isinstance(schema, dict) else None,
    )


#: Compiled-batch def names are minted per call and must satisfy `models.valid_name` (lowercase,
#: digits, hyphens — it becomes a directory).
_NAME_UNSAFE = re.compile(r"[^a-z0-9-]+")


def _batch_def_name() -> str:
    return f"subagent-batch-{int(time.time() * 1000)}"


def _findings_report(result: batch_compile.CompileResult) -> str:
    """A refusal a model can act on: the findings, then what to do about them."""
    lines = ["Error: the batch did not compile — each leaf needs an explicit contract."]
    for finding in result.findings:
        lines.append(f"  [{finding.severity}] {finding.code}: {finding.message}")
    lines.append(
        "\nPass each item of 'tasks' as an object with 'task', 'objective', 'output_format' "
        "and 'boundary' (each declaration at least "
        f"{batch_compile.MIN_DECLARATION_CHARS} characters), plus 'capability' and 'writes' "
        "when the leaf mutates."
    )
    return "\n".join(lines)


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
    leaves = [
        _to_leaf(text, spec, agents_list[i] if i < len(agents_list) else agent)
        for i, (text, spec) in enumerate(leaf_specs)
    ]
    name = _batch_def_name()
    result = batch_compile.compile_batch(leaves, depth=depth, run_name=name)
    if not result.compiled or not result.ok:
        return _findings_report(result)

    root = result.spec.get("root")
    if not isinstance(root, dict):
        return "Error: the compiler produced no root node"
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
        return f"Error: could not persist the compiled batch: {saved['error']}"

    body: dict[str, Any] = {"name": name, "mode": "background"}
    if cwd:
        body["inputs"] = {"cwd": cwd}
    started = _post("/api/workflows/runs", body)
    if started.get("error"):
        return f"Error: could not start the compiled batch: {started['error']}"
    run_id = str(started.get("run_id", "") or "")

    lines = [
        f"Compiled {len(leaves)} tasks into one batch run ({run_id or 'pending'}).",
        "Progress is a live widget; each branch is individually retryable.",
    ]
    for index, leaf in enumerate(leaves):
        node_id = leaf.node_id(index)
        posture = result.postures.get(node_id, {})
        mode = "read-only" if posture.get("read_only") else "mutating"
        lines.append(f"  {node_id} [{mode}]: {leaf.task[:70]}")
    if result.serialized:
        lines.append(f"\nWrite-bearing leaves run one at a time: {', '.join(result.serialized)}")
    warnings = [f for f in result.findings if f.severity == "warn"]
    for finding in warnings:
        lines.append(f"  [warn] {finding.code}: {finding.message}")
    if parent_session:
        lines.append("\nResults arrive as completion events in this session.")
    return "\n".join(lines)


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
            "name": "best_of_n",
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
        return "Error: provide a `prompt` to sample."
    n = int(args.get("n") or 3)
    criteria = str(args.get("criteria", "") or "")
    try:
        result = _run_async(best_of_n(prompt, n, criteria))
    except Exception as exc:  # noqa: BLE001 — a tool must answer, not traceback
        return f"Error: best-of-N sampling failed: {type(exc).__name__}: {exc}"
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

        tasks = args.get("tasks")
        task = args.get("task")

        # Support both single task and batch tasks. A batch item may be a plain string (the
        # legacy shape) or a contract object — `_leaf_specs` normalizes both to (text, spec)
        # so the compile path below sees one shape.
        if tasks and isinstance(tasks, list):
            leaf_specs = _leaf_specs(tasks)
            task_list = [text for text, _ in leaf_specs]
        elif task:
            leaf_specs = [(str(task), {})]
            task_list = [str(task)]
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

        # N>=2 is a BATCH: compiled to one `parallel[stage...]` run rather than N independent
        # fire-and-forget spawns. The difference is not cosmetic — N spawns have no run record, so
        # they cannot be shown as one widget, cannot survive a restart, and cannot be retried per
        # branch. `compile_batch` owns the threshold (COMPILE_THRESHOLD), the lints and the
        # capability posture; this seam only routes into it and reports what it decided.
        if len(task_list) >= batch_compile.COMPILE_THRESHOLD:
            return _run_compiled_batch(
                leaf_specs,
                agent=agent,
                agents_list=[str(a) for a in agents_list],
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
