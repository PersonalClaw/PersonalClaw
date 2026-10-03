"""The workflow tools in the tool server an agent CLI runs: every call is made by the gateway.

An agent on an agent CLI reaches PersonalClaw's tools through the CLI's tool server
(``personalclaw mcp-core``), a process of its own. The workflow engine that drives runs and the
workflow definitions live only in the gateway, so a call this process made with the engine itself
found no definitions (``workflow_start`` answered "no workflow definition named …" for one that
exists), started nothing, and a pause it lifted was recorded where nothing drove the run, which
stayed paused. So here every workflow tool call is the gateway's, made through the gateway's own
route for it, the one the owner's own action uses wherever there is one, with this process's
internal credential and the session of the chat it serves (``mcp_core._internal_headers``), and
answered as the same call is answered in the gateway (``mcp_workflows.gateway_answer``).

The routes take the call as an agent's (``approval_answer.of_request``): a run it starts is
started by its chat, so the run keeps that chat's memory mode and its turn's Stop ends it; it
answers no approval, so a resume lifts a pause and nothing more; and its edit carries no yes of
the owner's.

The planner and the bounded watch of a run have no route of the owner's, so the gateway serves
them to this process (``POST /api/workflows/agent-plans``, ``GET …/observe``), each answering what
the tool answers in the gateway. Two things stay in this process, as they do in the gateway: the
spec echo an inspect call carries (``context_block.staged_spec_echo``) and the candidate a valid
check freezes (``template_store``), each read or written in the home the gateway runs on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

from personalclaw.mcp_core import GATEWAY_READ_TIMEOUT_SECS, _delete, _get, _post
from personalclaw.mcp_workflows import (
    SUMMARIES,
    _fmt,
    definition_fields,
    freeze_authored_candidate,
    gateway_answer,
    saved_by_the_gateway,
    start_fields,
    unknown_tool,
)
from personalclaw.safety_flags import confirm_granted, yes_or_no
from personalclaw.validation import decode_json_text
from personalclaw.workflows import service
from personalclaw.workflows.models import TERMINAL_RUN_STATUSES, RunStatus

#: How long a blocking ``workflow_start`` waits here for its run to end before it answers with the
#: run still going: inside the time this process gives the gateway to answer a write
#: (``mcp_core.GATEWAY_WRITE_TIMEOUT_SECS``), so the answer is the run's state rather than "the
#: gateway did not answer".
BLOCKING_START_SECS = 25.0

#: The states a blocking start stops waiting at: an ending, or a question only the owner answers.
_STOPS_A_BLOCKING_START = frozenset(s.value for s in TERMINAL_RUN_STATUSES) | {
    RunStatus.NEEDS_INPUT.value
}


def call_through_the_gateway(name: str, args: dict[str, Any]) -> str:
    """Make the workflow tool call *name*(*args*) on the gateway, and answer it.

    *args* are validated, and refused by `mcp_workflows._refusal` where they name nothing to do."""
    through = CALLS.get(name)
    return through(args) if through is not None else unknown_tool(name)


def _segment(value: str) -> str:
    """*value* as one segment of a route's path, whatever it holds."""
    return quote(value, safe="")


def _run_id(args: dict[str, Any]) -> str:
    return str(args.get("run_id", "") or "")


def _no_run(run_id: str) -> str:
    """The answer for a call that names no run, as the engine gives it."""
    return _fmt(service._run_not_found(run_id))


def _no_name() -> str:
    """The answer for a definition call that names no definition, as the engine gives it."""
    return _fmt(service._service_failure("WF_DEF_NAME_REQUIRED", "a definition name is required"))


# ── definitions ──────────────────────────────────────────────────────────────


def _manifest(args: dict[str, Any]) -> str:
    return gateway_answer(
        _get("/api/workflows/manifest"),
        doing="read the workflow authoring reference",
        summary=SUMMARIES["workflow_manifest"],
    )


def _list_defs(args: dict[str, Any]) -> str:
    query = urlencode({key: str(args[key]) for key in ("tag", "source") if args.get(key)})
    return gateway_answer(_get(f"/api/workflows?{query}"), doing="list the workflows")


def _get_def(args: dict[str, Any]) -> str:
    name = str(args.get("name", "") or "")
    if not name:
        return _no_name()
    return gateway_answer(
        _get(f"/api/workflows/{_segment(name)}"), doing=f"read the workflow {name!r}"
    )


def _delete_def(args: dict[str, Any]) -> str:
    name = str(args.get("name", "") or "")
    if not name:
        return _no_name()
    return gateway_answer(
        _delete(f"/api/workflows/{_segment(name)}"), doing=f"delete the workflow {name!r}"
    )


def _author(args: dict[str, Any]) -> str:
    return saved_by_the_gateway(definition_fields(args))


def _check(args: dict[str, Any]) -> str:
    """The agent's dry run of a save: the gateway checks it and writes nothing."""
    fields = definition_fields(args)
    reply = _post("/api/workflows/agent-saves", {**fields, "save": False})
    if not reply.get("error") and reply.get("valid"):
        freeze_authored_candidate(args)
    return gateway_answer(reply, doing=f"check the workflow {fields['name']!r}")


def _plan(args: dict[str, Any]) -> str:
    reply = _post("/api/workflows/agent-plans", dict(args))
    plan = reply.get("plan")
    if reply.get("error") or not isinstance(plan, dict):
        return gateway_answer(reply, doing="plan the workflow")
    return gateway_answer(plan, doing="plan the workflow", summary=str(reply.get("summary") or ""))


# ── runs ─────────────────────────────────────────────────────────────────────


def _start(args: dict[str, Any]) -> str:
    fields = start_fields(args)
    blocking = fields["mode"] == "blocking"
    reply = _post(
        "/api/workflows/runs",
        {**fields, "blocking_timeout": BLOCKING_START_SECS} if blocking else fields,
    )
    summary = SUMMARIES["workflow_start"]
    if blocking and not reply.get("error") and reply.get("status") not in _STOPS_A_BLOCKING_START:
        summary = (
            f"Workflow run started. It is still running after {BLOCKING_START_SECS:g} s, so this "
            "answer is where it stands now: follow it with workflow_status or workflow_observe."
        )
    return gateway_answer(reply, doing=f"start the workflow {fields['name']!r}", summary=summary)


def _start_draft(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/start", {}),
        doing=f"start the draft run {run_id!r}",
        summary=SUMMARIES["workflow_start_draft"],
    )


def _status(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    return gateway_answer(
        _get(f"/api/workflows/runs/{_segment(run_id)}"), doing=f"read the run {run_id!r}"
    )


def _observe(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    asked = int(args.get("duration_ms") or 0)
    window = max(
        service.MIN_OBSERVE_MS, min(asked or service.DEFAULT_OBSERVE_MS, service.MAX_OBSERVE_MS)
    )
    # The route watches for the whole window before it answers, so this waits that long and then
    # as long as any read.
    return gateway_answer(
        _get(
            f"/api/workflows/runs/{_segment(run_id)}/observe?{urlencode({'duration_ms': asked})}",
            timeout=window / 1000 + GATEWAY_READ_TIMEOUT_SECS,
        ),
        doing=f"watch the run {run_id!r}",
    )


def _output(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    node_id = str(args.get("node_id", "") or "")
    if not run_id:
        return _no_run(run_id)
    if not node_id:
        return _fmt(service._service_failure("WF_NODE_NOT_FOUND", "no node '' in this run's spec"))
    return gateway_answer(
        _get(f"/api/workflows/runs/{_segment(run_id)}/outputs/{_segment(node_id)}"),
        doing=f"read the output of {node_id!r} in the run {run_id!r}",
    )


def _edit(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    body: dict[str, Any] = {
        "ops": args["ops"],
        "confirm_cascade": confirm_granted(args, "confirm_cascade"),
    }
    expect = args.get("expect_version")
    if isinstance(expect, (int, float)) and not isinstance(expect, bool):
        body["expect_version"] = int(expect)
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/edit", body),
        doing=f"edit the run {run_id!r}",
    )


def _edit_preview(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    return gateway_answer(
        _post(
            f"/api/workflows/runs/{_segment(run_id)}/edit",
            {"ops": args["ops"], "preview_only": True},
        ),
        doing=f"preview an edit of the run {run_id!r}",
    )


def _skip(args: dict[str, Any]) -> str:
    """A skip is an edit whose every op skips one step, as the engine applies one."""
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    ops = [{"op": "skip", "node_id": str(node_id)} for node_id in args["node_ids"]]
    return gateway_answer(
        _post(
            f"/api/workflows/runs/{_segment(run_id)}/edit", {"ops": ops, "confirm_cascade": True}
        ),
        doing=f"skip steps of the run {run_id!r}",
    )


def _rewind(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    body = {
        "node_id": str(args.get("node_id", "") or ""),
        "redo_effects": yes_or_no(args.get("redo_effects")) is True,
        "force": yes_or_no(args.get("force")) is True,
        "confirm_cascade": confirm_granted(args, "confirm_cascade"),
    }
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/rewind", body),
        doing=f"rewind the run {run_id!r}",
    )


def _run_from(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    body = {
        "node_id": str(args.get("node_id", "") or ""),
        "confirm_cascade": confirm_granted(args, "confirm_cascade"),
    }
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/run-from", body),
        doing=f"re-run the run {run_id!r} from a step",
    )


def _fork(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    body = {
        "checkpoint_id": str(args.get("checkpoint_id", "") or ""),
        "note": str(args.get("note", "") or ""),
    }
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/fork", body),
        doing=f"fork the run {run_id!r}",
        summary=SUMMARIES["workflow_fork"],
    )


def _pause(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/pause", {}),
        doing=f"pause the run {run_id!r}",
    )


def _resume(args: dict[str, Any]) -> str:
    """The owner's Resume route, which takes this call as the agent's: it lifts a pause, and an
    answer sent anyway is refused and audited there, as the engine refuses an agent's answer."""
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    body: dict[str, Any] = {}
    token = str(args.get("resume_token", "") or "")
    if token:
        body["resume_token"] = token
    answer = decode_json_text(args.get("answer"))
    if answer is not None:
        body["answer"] = answer
    if yes_or_no(args.get("always_allow")) is True:
        body["always_allow"] = True
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/resume", body),
        doing=f"resume the run {run_id!r}",
    )


def _cancel(args: dict[str, Any]) -> str:
    run_id = _run_id(args)
    if not run_id:
        return _no_run(run_id)
    return gateway_answer(
        _post(f"/api/workflows/runs/{_segment(run_id)}/cancel", {}),
        doing=f"cancel the run {run_id!r}",
    )


def _audit(args: dict[str, Any]) -> str:
    return gateway_answer(_get("/api/workflows/audit"), doing="audit the workflow runs")


def _repair(args: dict[str, Any]) -> str:
    return gateway_answer(
        _get("/api/workflows/audit?dry_run=false"), doing="repair the workflow runs"
    )


#: Each workflow tool, as the call this process makes on the gateway for it. Every tool
#: `mcp_workflows` lists has one (``tests/test_an_agent_clis_workflow_tools_reach_the_gateway.py``).
CALLS: dict[str, Callable[[dict[str, Any]], str]] = {
    "workflow_author": _author,
    "workflow_check": _check,
    "workflow_plan": _plan,
    "workflow_list_defs": _list_defs,
    "workflow_get_def": _get_def,
    "workflow_start": _start,
    "workflow_status": _status,
    "workflow_observe": _observe,
    "workflow_edit": _edit,
    "workflow_edit_preview": _edit_preview,
    "workflow_skip": _skip,
    "workflow_rewind": _rewind,
    "workflow_run_from": _run_from,
    "workflow_fork": _fork,
    "workflow_start_draft": _start_draft,
    "workflow_pause": _pause,
    "workflow_resume": _resume,
    "workflow_cancel": _cancel,
    "workflow_output": _output,
    "workflow_audit": _audit,
    "workflow_repair": _repair,
    "workflow_manifest": _manifest,
    "workflow_delete_def": _delete_def,
}
