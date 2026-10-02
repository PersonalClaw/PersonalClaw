"""Starting a compiled `subagent_run` batch, the one approval a batch that may change things needs,
and how a batch tells the conversation that started it how it ended.

`mcp_subagents._run_compiled_batch` compiles two or more tasks into one workflow (`batch_compile`)
and hands it here (``POST /api/workflows/batches``). A batch whose tasks only read is saved and
started at once. A task that may change things carries a posture key only the owner's consent may
put on a step (`automation_posture.POSTURE_SPECS`: the write grant, ``capability: "mutating"``), so
saving the batch needs that consent, and the agent that called the tool cannot give it. The batch
asks the owner instead, once, the way every other ask is asked (`DashboardState.request_approval`:
the card in the chat that started it, the Inbox, the phone, a channel), naming each task and what
it may change, and starts only on her Allow:

* **Only her answer.** The ask goes to the approval registry itself, so no standing grant (a chat's
  Trust, YOLO, an operator's source list) answers it; it is kept out of the answers a Trust or YOLO
  switch gives in bulk (``answered_alone``); and the registry takes an answer from the owner alone
  (`approval_answer`).
* **What she allowed is what runs.** On her Allow the definition is saved with the posture she was
  shown, her answer being the save's consent, and the run is stamped with it (:data:`CONSENT_KEY`),
  so its tasks start on that one Allow instead of each asking again
  (`approval_grants.batch_allowed`, read by `SubagentManager._spawn_grant`).
* **A Deny ends it declined**, and an ask nobody answers ends it unstarted: nothing is saved or
  run, and the conversation that started it is told so (:func:`never_started`), as it is told how
  a batch that ran ended (:func:`ending_of_run`).
* **Nobody to ask, nothing started.** A session that runs without asking anyone (a loop started
  Unattended) and a gateway with nowhere to ask are refused, saying why: a grant that approves calls
  on its own is not consent to a batch's writes.

The batch waits for her answer in the gateway, not in the tool call: an approval waits as long as
the owner's approval window, longer than any tool call may run, and the agent goes on meanwhile,
as it does when a single subagent's start asks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from personalclaw.workflows import service, store
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeKind,
    OriginKind,
    RunStatus,
    WorkflowRun,
    run_ending,
    walk,
)

if TYPE_CHECKING:
    from personalclaw.subagent import SubagentInfo

logger = logging.getLogger(__name__)

#: The run-record key (`WorkflowRun.extra`) of the owner's Allow of a batch's start: who answered
#: which ask, and when. Written at create, before the run's first step dispatches, and never copied
#: to a fork (a fork carries its parent's origin and inputs, not its record).
CONSENT_KEY = "batch_consent"

#: The approval-registry id of a batch's ask, before the batch's definition name.
_ASK_PREFIX = "batch:"

#: What each posture key lets a task do, as the ask says it. The keys are
#: `automation_posture.POSTURE_SPECS`'s, the one list of them; a test holds the two to the same
#: keys, so a key added there cannot reach a batch the ask says nothing about.
WHAT_IT_MAY_DO: dict[str, str] = {
    "capability": (
        "may change files and run commands, not only read: its file and shell tools, and any other "
        "tool it has that changes things"
    ),
    "approval_mode": "approves its own tool calls instead of asking you first",
}


@dataclass(frozen=True)
class Task:
    """One task of a batch, as its ask and its report name it."""

    path: str
    node_id: str
    label: str
    agent: str
    #: The posture keys it loosens over leaving them unset (`automation_posture.loosened_keys`).
    changes: tuple[str, ...]
    #: The paths it says it will write (the compiler's `writes`, informational: what each task
    #: may change is what its tools reach, and the ask says so).
    writes: tuple[str, ...]


def tasks_of(root: dict[str, Any], writes: dict[str, Any] | None = None) -> list[Task]:
    """The tasks of the batch *root*, each with the posture keys it loosens, read the way the
    definition's save reads them (`automation_posture.workflow_steps`). *writes* is the paths each
    task says it will write, by node id."""
    from personalclaw.automation_posture import loosened_keys, workflow_steps

    try:
        tree = Node.from_dict(dict(root))
    except Exception:
        logger.debug("batch spec did not parse for its ask", exc_info=True)
        return []
    steps = workflow_steps(root)
    declared = writes if isinstance(writes, dict) else {}
    tasks: list[Task] = []
    for path, node in walk(tree):
        if path not in steps:
            continue
        config = node.config if isinstance(node.config, dict) else {}
        said = declared.get(node.id or "")
        tasks.append(
            Task(
                path=path,
                node_id=node.id or "",
                label=node.label or node.id or path,
                agent=str(config.get("agent", "") or ""),
                changes=tuple(loosened_keys(steps[path][1])),
                writes=tuple(str(p) for p in said) if isinstance(said, list) else (),
            )
        )
    return tasks


def _ask_text(tasks: list[Task], *, folder: str, workspace: dict[str, Any]) -> tuple[str, str]:
    """``(purpose, input)`` of the batch's ask: what allowing it does, then each task by its name
    with what it may change, and where its tasks work. Every clause is read from the spec that
    runs, and true of it."""
    changing = sum(1 for task in tasks if task.changes)
    purpose = (
        f"Starts {len(tasks)} tasks at once, and {changing} of them may change things, not only "
        "read. Allow it and its tasks start; deny it and none does."
    )
    lines = []
    for number, task in enumerate(tasks, 1):
        name = f"{task.label} ({task.agent})" if task.agent else task.label
        if not task.changes:
            lines.append(f"{number}. {name}: only reads.")
            continue
        what = "; it ".join(WHAT_IT_MAY_DO[key] for key in task.changes)
        said = (
            f"It says it will write {', '.join(task.writes)}."
            if task.writes
            else "It names no file it will write."
        )
        lines.append(f"{number}. {name}: it {what}. {said}")
    where = []
    if str(workspace.get("mode") or "") == "scratch":
        where.append("its tasks work in a scratch folder of the batch's own")
    reads = _reads(folder)
    if reads:
        where.append(f"they read {reads}, the folder it was started in")
    if where:
        said = ", and ".join(where)
        lines.append(f"{said[0].upper()}{said[1:]}.")
    if folder and not reads:
        from personalclaw.file_scope import ALLOWED_SETTING

        lines.append(
            f"They do not read {folder}, the folder it was started in: a subagent reads the "
            f"workspace and the folders in {ALLOWED_SETTING}, and no other."
        )
    return purpose, "\n".join(lines)


def _reads(folder: str) -> str:
    """The folder a batch was started in, as its tasks read it, or ``""`` when they do not: only
    a folder a spawn may work in counts (`provisioning.step_reads`, `subagent.validate_cwd`), so the
    ask says what the run will do. Fails closed: a setting that cannot be read reaches nothing."""
    if not folder:
        return ""
    from personalclaw.config.loader import AppConfig
    from personalclaw.subagent import validate_cwd

    try:
        roots = AppConfig.load().agent.subagent_cwd_allowed_roots
    except Exception:
        logger.debug("allowed folders unreadable for a batch's ask", exc_info=True)
        return ""
    resolved, refused = validate_cwd(folder, list(roots))
    return "" if refused else resolved


def _nobody_to_ask(state: Any, chat: str) -> str:
    """Why nobody can be asked to allow the batch, as the rest of "…, which …"; "" when someone
    can. A session that acts on its own (`loop.posture`: an Unattended loop's) has nobody there to
    answer, and a gateway with no approval surface has nowhere to ask."""
    if state is None or not callable(getattr(state, "request_approval", None)):
        return "there is nowhere to ask for here"
    session = (getattr(state, "_sessions", None) or {}).get(chat)
    if session is not None and getattr(session, "_unattended", False) is True:
        return "this run cannot ask for: it acts on its own, with nobody there to answer"
    return ""


async def start(
    state: Any,
    supervisor: Any,
    *,
    name: str,
    root: dict[str, Any],
    workspace: dict[str, Any],
    inputs: dict[str, Any],
    writes: dict[str, Any],
    session_key: str,
    description: str = "",
) -> dict[str, Any]:
    """Start the compiled batch *name*, or, when a task may change things, ask its owner first.

    Returns the service envelope: ``run_id`` for a batch that started, ``awaiting_approval`` with
    the ask's id and the tasks that may change things for one that waits for her answer, or a
    failure saying why nothing started.
    """
    tasks = tasks_of(root, writes)
    begin = _starter(
        supervisor,
        name=name,
        root=root,
        workspace=workspace,
        inputs=inputs,
        description=description,
        session_key=session_key,
    )
    if not any(task.changes for task in tasks):
        return await begin(None)
    chat = session_key.removeprefix("dashboard:")
    why = _nobody_to_ask(state, chat)
    if why:
        changing = ", ".join(task.label for task in tasks if task.changes)
        return service._service_failure(
            "WF_BATCH_NOBODY_TO_ASK",
            f"{changing} may change things, and a batch like that starts only on the owner's own "
            f"Allow, which {why}. Make those changes in this session, or start the batch from a "
            "chat.",
        )
    ask_id = f"{_ASK_PREFIX}{name}"
    purpose, said = _ask_text(tasks, folder=str(inputs.get("cwd") or ""), workspace=workspace)
    waiter = asyncio.ensure_future(
        _ask_then_start(
            state,
            supervisor,
            ask_id=ask_id,
            purpose=purpose,
            said=said,
            chat=chat,
            name=name,
            tasks=tasks,
            session_key=session_key,
            begin=begin,
        )
    )
    held = getattr(state, "_background_tasks", None)
    if isinstance(held, set):
        held.add(waiter)
        waiter.add_done_callback(held.discard)
    return service._ok(
        status="awaiting_approval",
        approval=ask_id,
        tasks=len(tasks),
        may_change=[task.label for task in tasks if task.changes],
    )


def _starter(supervisor: Any, *, name: str, root: dict[str, Any], **fields: Any) -> Any:
    """The save-and-start of one batch, given the consent it starts on (``None``: it needs none)."""

    async def _begin(consent: dict[str, Any] | None) -> dict[str, Any]:
        saved = await service.author_def(
            name=name,
            root=root,
            description=str(fields["description"] or ""),
            # The compiled tree is machine-generated and lint-clean by construction; `strict` would
            # refuse it on a convention WARNING the compiler already approved.
            strict=False,
            provenance="user",
            workspace=fields["workspace"] or None,
        )
        if not saved.get("ok"):
            return saved
        return await service.start_run(
            name=name,
            inputs=fields["inputs"] or None,
            mode="background",
            supervisor=supervisor,
            origin_kind=OriginKind.SUBAGENT_TOOL,
            session_key=fields["session_key"],
            extra={CONSENT_KEY: consent} if consent else None,
        )

    return _begin


async def _ask_then_start(
    state: Any,
    supervisor: Any,
    *,
    ask_id: str,
    purpose: str,
    said: str,
    chat: str,
    name: str,
    tasks: list[Task],
    session_key: str,
    begin: Any,
) -> None:
    """Ask the owner to allow the batch, then start it on her Allow, or tell the conversation that
    started it why it never started. Never raises: it runs with nobody awaiting it."""
    from personalclaw.approval_grants import NOBODY, YOU, ToolDecision
    from personalclaw.tool_providers.base import RiskLevel

    try:
        allowed = await state.request_approval(
            ask_id,
            "subagent",
            "subagent_run",
            tool_input=said,
            tool_purpose=purpose,
            session=chat,
            risk_level=RiskLevel.CAUTION.value,
            answered_alone=True,
        )
        # How it ended, which the bool cannot say: her Allow or Deny, or no answer at all (nobody
        # answered in time, or its work stopped first).
        ending = str(state.ended_as(ask_id) or "") or ("approved" if allowed else "rejected")
        decision = ToolDecision(
            bool(allowed), ending, YOU if ending in ("approved", "rejected") else NOBODY
        )
    except Exception:
        logger.warning("batch %s: asking for its approval failed", name, exc_info=True)
        decision = ToolDecision(False, "rejected", "approval_failed")
    _audit(session_key, name, decision)
    if decision.outcome == "cancelled":
        # Its owner ended first (the loop or the turn that asked was stopped: `end_asks`), so there
        # is nobody left to tell, as a subagent stopped with its owner tells nobody either.
        return
    if decision:
        try:
            started = await begin({"approval": ask_id, "allowed_at": time.time(), "by": YOU})
        except Exception as exc:  # noqa: BLE001 - nobody awaits this; the chat hears why instead
            logger.warning("batch %s: allowed but its start raised", name, exc_info=True)
            started = {"ok": False, "message": str(exc) or type(exc).__name__}
        if started.get("ok"):
            return
        logger.warning("batch %s: allowed but could not start: %s", name, started.get("message"))
        endings = never_started(
            session_key,
            name,
            tasks,
            error=f"batch allowed, but it could not start: {started.get('message') or 'unknown'}",
        )
    else:
        from personalclaw.subagent_ask import spawn_refusal

        endings = never_started(
            session_key,
            name,
            tasks,
            error=spawn_refusal(decision, what="batch"),
            declined=decision.outcome == "rejected" and decision.decided_by == YOU,
        )
    announce = getattr(supervisor, "announce", None)
    if callable(announce):
        try:
            announce(endings)
        except Exception:
            logger.warning(
                "batch %s: could not tell its chat it never started", name, exc_info=True
            )


def _audit(session_key: str, name: str, decision: Any) -> None:
    from personalclaw.sel import sel

    try:
        sel().log_tool_invocation(
            session_key=session_key,
            source="subagent",
            tool_name="subagent_run",
            # Each one a word the audit log's filters read (`audit_outcome_families`): nobody
            # answering, or its work stopping first, is not a Deny.
            outcome=(
                "approved"
                if decision
                else (
                    "expired"
                    if decision.outcome == "expired"
                    else ("cancelled" if decision.outcome == "cancelled" else "rejected")
                )
            ),
            metadata={"batch": name, "decided_by": decision.decided_by},
        )
    except Exception:
        logger.debug("SEL audit failed for batch %s", name, exc_info=True)


def end_asks(state: Any, ended: Callable[[str, float], bool], *, reason: str) -> int:
    """End every batch still waiting for its owner's answer whose ask *ended* says is over, given
    the session it was asked for and when (``started_work.end_started``: a loop's ending, a turn's
    Stop): its ask is cancelled saying *reason* ("its loop “…” was stopped"), on every surface, so
    it can no longer be allowed, and the batch never starts. Returns how many."""
    pending = getattr(state, "_pending_approvals", None) or {}
    asks = [
        approval_id
        for approval_id, entry in list(pending.items())
        if approval_id.startswith(_ASK_PREFIX)
        and ended(str(entry.get("session") or ""), float(entry.get("ts") or 0.0))
    ]
    return sum(1 for approval_id in asks if state.cancel_approval(approval_id, reason=reason))


# ── telling the conversation that started it ─────────────────────────────────────────────────────


def reports_to_a_chat(run: Any) -> bool:
    """Whether *run* is a batch a conversation started, which it tells how it ended: a subagent
    batch with the session that started it, and not a sub-run (whose ending is its parent's step,
    though its origin is the same kind)."""
    origin = getattr(run, "origin", None)
    return bool(
        getattr(origin, "kind", None) == OriginKind.SUBAGENT_TOOL
        and getattr(origin, "session_key", "")
        and not getattr(run, "parent_run_id", None)
    )


def tells_its_chat(run: Any, status: RunStatus) -> bool:
    """Whether the batch *run*, ended *status*, tells the conversation that started it how it
    ended. Not a run someone stopped: whoever stopped it knows, and what stopped it may have
    ended that conversation's work too (its loop's Stop), as a subagent stopped with its owner
    tells nobody. Not a run whose loop has ended either (`loop.children.parent_ended`): that loop's
    worker has nobody left to report to."""
    if status is RunStatus.CANCELLED or not reports_to_a_chat(run):
        return False
    from personalclaw.loop.children import parent_ended

    return not parent_ended(run)


def never_started(
    session_key: str, name: str, tasks: list[Task], *, error: str, declined: bool = False
) -> list[SubagentInfo]:
    """How a batch that never started ended, as the one completion its conversation reads: the
    batch by its definition's name, its tasks by theirs, and why none of them ran."""
    from personalclaw.subagent import SubagentInfo

    labels = "; ".join(task.label for task in tasks)
    return [
        SubagentInfo(
            id=name,
            task=f"{len(tasks)} tasks: {labels}",
            parent_session_key=session_key,
            done=True,
            error=error,
            declined=declined,
        )
    ]


def ending_of_run(run: WorkflowRun, status: RunStatus, *, subagents: Any = None) -> list[Any]:
    """How each task of the batch *run* ended, one completion per task for the conversation that
    started it: the task by its step's name, with what it returned, or why it did not. A task's
    full answer is its subagent's own when this process still knows it, else the run's stored
    output."""
    from personalclaw.subagent import SubagentInfo

    spec = store.read_spec(run.id) or {}
    try:
        tree = Node.from_dict(spec.get("root") or {})
    except Exception:
        logger.debug("run %s: spec unreadable for its batch's report", run.id, exc_info=True)
        return []
    instances = store.read_state(run.id)
    endings = []
    for path, node in walk(tree):
        if node.kind is not NodeKind.STAGE:
            continue
        inst = instances.get(path)
        agent_id = getattr(inst, "subagent_id", "") or ""
        known = subagents.get(agent_id) if agent_id and subagents is not None else None
        config = node.config if isinstance(node.config, dict) else {}
        info = SubagentInfo(
            id=agent_id or node.id or path,
            task=node.label or node.id or path,
            parent_session_key=run.origin.session_key,
            agent=str(config.get("agent", "") or ""),
            done=True,
        )
        state = getattr(inst, "state", None)
        failure = getattr(inst, "failure", None)
        cause = str(getattr(failure, "cause_plain", "") or "")
        if state in (InstanceState.DONE, InstanceState.DEGRADED):
            info.result = str(getattr(known, "result", "") or "") or _stored(run.id, path)
        elif state is InstanceState.DECLINED:
            info.declined, info.error = True, cause or "its start was denied, so it never started"
        elif state in (InstanceState.FAILED, InstanceState.CANCELLED, InstanceState.ESCALATED):
            info.error = cause or f"it {state.value}: the batch {run_ending(status)}"
        else:
            info.error = f"it never started: the batch {run_ending(status)}"
        endings.append(info)
    return endings


def _stored(run_id: str, path: str) -> str:
    output = store.read_output(run_id, path)
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    import json

    try:
        return json.dumps(output, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(output)
