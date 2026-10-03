"""Starting a compiled `subagent_run` batch on its one ask, kept until it is answered, and how a
batch tells the conversation that started it how it ended.

`mcp_subagents._run_compiled_batch` compiles two or more tasks into one workflow (`batch_compile`)
and hands it here (``POST /api/workflows/batches``). Nothing is saved or run until the batch may
start, and that is decided once, for all of its tasks. The call that asked for it asks nobody
itself (`tool_providers.base.WORK_ASKS_META_KEY`), so a batch asks its owner once at most:

* **A batch that only reads** starts as a subagent its conversation started would: on the grant
  that starts that conversation's subagents without asking (its Trust, YOLO, the hook setting),
  under the operator ceiling, or else on the answer to one ask, asked as a subagent's start is
  (`SubagentManager._ask_to_start`: the card in that chat, the Inbox, the phone, a channel, and the
  grants that answer a start there). A Trust or YOLO switch answers it, as it answers a start.
* **A batch with a task that may change things** carries a posture key only the owner's consent may
  put on a step (`automation_posture.POSTURE_SPECS`: the write grant, ``capability: "mutating"``),
  so it starts on her own Allow alone (`owner_allow.ask`): its ask goes to the approval registry
  itself, so no standing grant answers it; it is kept out of the answers a Trust or YOLO switch
  gives in bulk (``answered_alone``); and the registry takes an answer from the owner alone
  (`approval_answer`).
* **What allowed it is what runs.** The definition is saved with the posture she was shown, and the
  run is stamped with what allowed its start (:data:`CONSENT_KEY`), so its tasks start on that and
  none asks again (`approval_grants.batch_allowed`, read by `SubagentManager._spawn_grant`).
* **A Deny ends it declined**, and an ask nobody answers ends it unstarted: nothing is saved or
  run, and the conversation that started it is told so (:func:`never_started`), as it is told how
  a batch that ran ended (:func:`ending_of_run`).
* **Nobody to ask, nothing started.** Where nothing lets it start on its own, a session that runs
  without asking anyone (a loop started Unattended) and a gateway with nowhere to ask are refused,
  saying why: a grant that approves calls on its own is not consent to a batch's writes.
* **A restart does not lose the ask.** A batch waiting for its answer is recorded until it ends
  (:func:`state_of` reads it, for the batch's card in its chat), and a gateway that comes back asks
  it again (:func:`resume`), or ends it unstarted once its approval window has passed, saying
  nobody answered in time. A record only ever asks: what starts a batch is the answer to its ask,
  or a grant standing at that moment.

The ask for her own Allow is the one every Allow an agent cannot give itself goes through
(`owner_allow`), and a batch waits for its answer in the gateway, not in the tool call, as a single
subagent's start does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.automation_posture import WHAT_IT_MAY_DO
from personalclaw.workflows import owner_allow, service, store
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeKind,
    OriginKind,
    RunStatus,
    WorkflowRun,
    run_ending,
    valid_name,
    walk,
)

if TYPE_CHECKING:
    from personalclaw.approval_grants import ToolDecision
    from personalclaw.subagent import SubagentInfo

logger = logging.getLogger(__name__)

#: The run-record key (`WorkflowRun.extra`) of what allowed a batch's start: her answer to which
#: ask, or the grant that started a batch that only reads, and when. Written at create, before the
#: run's first step dispatches, and never copied to a fork (a fork carries its parent's origin and
#: inputs, not its record).
CONSENT_KEY = "batch_consent"

#: How a recorded batch stands (:func:`state_of`): waiting for its answer, allowed and starting,
#: started (its run is its record from then on), or ended before it started, saying why.
ASKING = "asking"
STARTING = "starting"
STARTED = "started"
NOT_STARTED = "not_started"

#: How long the record of a batch that never started is kept, for its card in the chat that
#: started it: a week. The chat's own completion turn says it for good.
_KEPT_SECS = 7 * 24 * 3600


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


@dataclass(frozen=True)
class _Asking:
    """A batch waiting for its answer: what it is, who asked, and the ask it is asked with."""

    name: str
    session_key: str
    tasks: list[Task]
    #: What allowing it does, then each task by its name with what it may change (`_ask_text`).
    purpose: str
    said: str


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


def ask_id(name: str) -> str:
    """The approval-registry id of the ask of the batch *name*."""
    return f"{owner_allow.BATCH_PREFIX}{name}"


def batch_of(approval_id: str) -> str:
    """The batch an approval asks to start (its definition's name), or ``""`` for any other."""
    prefix = owner_allow.BATCH_PREFIX
    return approval_id[len(prefix) :] if approval_id.startswith(prefix) else ""


def _asking(
    name: str,
    session_key: str,
    *,
    root: dict[str, Any],
    workspace: dict[str, Any],
    inputs: dict[str, Any],
    writes: dict[str, Any],
) -> _Asking:
    """The batch *name* as it is asked: its tasks, and its ask's two lines (:func:`_ask_text`)."""
    tasks = tasks_of(root, writes)
    purpose, said = _ask_text(tasks, folder=str(inputs.get("cwd") or ""), workspace=workspace)
    return _Asking(name, session_key, tasks, purpose, said)


def _ask_text(tasks: list[Task], *, folder: str, workspace: dict[str, Any]) -> tuple[str, str]:
    """``(purpose, input)`` of the batch's ask: what allowing it does, then each task by its name
    with what it may change, and where its tasks work. Every clause is read from the spec that
    runs, and true of it."""
    changing = sum(1 for task in tasks if task.changes)
    purpose = (
        f"Starts {len(tasks)} tasks at once, and {changing} of them may change things, not only "
        "read. Allow it and its tasks start; deny it and none does."
        if changing
        else f"Starts {len(tasks)} tasks at once, each of which only reads. Allow it and its tasks "
        "start; deny it and none does."
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


def _start_grant(state: Any, session_key: str, name: str) -> str:
    """The grant that starts a batch that only reads from *session_key* without asking: the one
    that would start a subagent that session asked for (`SubagentManager._start_grant`: YOLO, its
    Trust, the hook setting), if the operator ceiling lets it stand. ``""`` when the batch asks."""
    from personalclaw import approval_grants

    manager = getattr(state, "subagents", None)
    grant = manager._start_grant(session_key) if manager is not None else ""
    if grant and approval_grants.stands(
        grant, caller=session_key or "subagent", subject=f"subagent_run,batch={name}"
    ):
        return grant
    return ""


def _nobody_to_ask(state: Any, chat: str, *, reads: bool) -> str:
    """Why nobody can be asked to allow the batch, as the rest of "…, which …"; "" when someone
    can (`owner_allow.nobody_to_ask`). A batch that only reads (*reads*) asks as a subagent's start
    does, so a gateway with no subagent manager has nowhere to ask it either."""
    door = getattr(getattr(state, "subagents", None), "_ask_to_start", None)
    if reads and not callable(door):
        return "there is nowhere to ask for here"
    return owner_allow.nobody_to_ask(state, chat)


def _nobody_sentence(tasks: list[Task], why: str) -> str:
    """What a batch refused for having nobody to ask says, by what its tasks may do."""
    changing = ", ".join(task.label for task in tasks if task.changes)
    if changing:
        return (
            f"{changing} may change things, and a batch like that starts only on the owner's own "
            f"Allow, which {why}. Make those changes in this session, or start the batch from a "
            "chat."
        )
    return (
        f"A batch starts only once its owner allows it, which {why}. Do that reading in this "
        "session, or start the batch from a chat."
    )


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
    """Start the compiled batch *name* on the grant that lets it start without asking, or ask its
    owner first.

    Returns the service envelope: ``run_id`` for a batch that started, ``awaiting_approval`` with
    the ask's id, the batch's name and the tasks that may change things for one that waits for
    its answer, or a failure saying why nothing started.
    """
    from personalclaw.approval_grants import ToolDecision

    asking = _asking(
        name, session_key, root=root, workspace=workspace, inputs=inputs, writes=writes
    )
    begin = _starter(
        supervisor,
        name=name,
        root=root,
        workspace=workspace,
        inputs=inputs,
        description=description,
        session_key=session_key,
    )
    changing = [task.label for task in asking.tasks if task.changes]
    if not changing and (grant := _start_grant(state, session_key, name)):
        _audit(session_key, name, ToolDecision(True, "auto_approved", grant))
        return await begin({"allowed_at": time.time(), "by": grant})
    why = _nobody_to_ask(state, session_key.removeprefix("dashboard:"), reads=not changing)
    if why:
        return service._service_failure(
            "WF_BATCH_NOBODY_TO_ASK", _nobody_sentence(asking.tasks, why)
        )
    _save_record(
        name,
        {
            "status": ASKING,
            "session_key": session_key,
            "asked_at": time.time(),
            "tasks": len(asking.tasks),
            "start": {
                "root": root,
                "workspace": workspace,
                "inputs": inputs,
                "writes": writes,
                "description": description,
            },
        },
    )
    _waiting(state, _ask_then_start(state, supervisor, asking, begin))
    return service._ok(
        status="awaiting_approval",
        approval=ask_id(name),
        batch=name,
        tasks=len(asking.tasks),
        may_change=changing,
    )


def _starter(supervisor: Any, *, name: str, root: dict[str, Any], **fields: Any) -> Any:
    """The save-and-start of one batch, given what allowed it (:data:`CONSENT_KEY`)."""

    async def _begin(consent: dict[str, Any]) -> dict[str, Any]:
        from personalclaw.approval_grants import YOU

        saved = await service.author_def(
            name=name,
            root=root,
            description=str(fields["description"] or ""),
            # The compiled tree is machine-generated and lint-clean by construction; `strict` would
            # refuse it on a convention WARNING the compiler already approved.
            strict=False,
            provenance="user",
            workspace=fields["workspace"] or None,
            # Her Allow of the ask that named each task and what it may change. A grant that
            # started a batch that only reads is no yes of hers, and that batch needs none: it
            # lets no step do more.
            owner_allowed=consent.get("by") == YOU,
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
            extra={CONSENT_KEY: consent},
        )

    return _begin


def _waiting(state: Any, work: Coroutine[Any, Any, None]) -> None:
    """Run a batch's wait for its answer in the background, held where the gateway keeps them."""
    waiter = asyncio.ensure_future(work)
    held = getattr(state, "_background_tasks", None)
    if isinstance(held, set):
        held.add(waiter)
        waiter.add_done_callback(held.discard)


async def _ask(state: Any, asking: _Asking) -> ToolDecision:
    """Ask the owner to allow the batch: one that only reads as a subagent's start is asked, one
    that may change things through the approval registry itself, for her answer alone."""
    from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
    from personalclaw.tool_providers.base import RiskLevel

    approval = ask_id(asking.name)
    if not any(task.changes for task in asking.tasks):
        ask = LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            request_id=approval,
            title="subagent_run",
            tool_purpose=asking.purpose,
            tool_input=asking.said,
            risk_level=RiskLevel.CAUTION.value,
            # Each task is held to the read-only tier, so the ask says it reads: read by its
            # name alone, "subagent" said it writes files, beside a purpose saying none does.
            annotations={"readOnlyHint": True},
        )
        return await state.subagents._ask_to_start(ask, asking.session_key)
    decision: ToolDecision = await owner_allow.ask(
        state,
        ask_id=approval,
        source="subagent",
        tool="subagent_run",
        purpose=asking.purpose,
        said=asking.said,
        session=asking.session_key.removeprefix("dashboard:"),
    )
    return decision


async def _ask_then_start(state: Any, supervisor: Any, asking: _Asking, begin: Any) -> None:
    """Ask the owner to allow the batch, then start it on her Allow, or tell the conversation that
    started it why it never started. Never raises: it runs with nobody awaiting it. Stopped with
    the gateway, it leaves the batch recorded as asking, for the next start to ask again."""
    from personalclaw.approval_grants import YOU, ToolDecision

    name, session_key = asking.name, asking.session_key
    try:
        decision = await _ask(state, asking)
    except Exception:
        logger.warning("batch %s: asking for its approval failed", name, exc_info=True)
        decision = ToolDecision(False, "rejected", "approval_failed")
    _audit(session_key, name, decision)
    if decision.outcome == "cancelled":
        # Its owner ended first (the loop or the turn that asked was stopped:
        # `owner_allow.end_asks`, which recorded why), so there is nobody left to tell, as a
        # subagent stopped with its owner tells nobody either.
        if (read_record(name) or {}).get("status") == ASKING:
            _end_record(name, error=_ended_before_its_answer("what asked for it ended"))
        return
    if decision:
        _save_record(name, {**(read_record(name) or {}), "status": STARTING})
        try:
            started = await begin(
                {"approval": ask_id(name), "allowed_at": time.time(), "by": decision.decided_by}
            )
        except Exception as exc:  # noqa: BLE001 - nobody awaits this; the chat hears why instead
            logger.warning("batch %s: allowed but its start raised", name, exc_info=True)
            started = {"ok": False, "message": str(exc) or type(exc).__name__}
        if started.get("ok"):
            # Its run is its record from here on (`state_of`).
            _drop_record(name)
            return
        logger.warning("batch %s: allowed but could not start: %s", name, started.get("message"))
        error = f"batch allowed, but it could not start: {started.get('message') or 'unknown'}"
        declined = False
    else:
        from personalclaw.subagent_ask import spawn_refusal

        error = spawn_refusal(decision, what="batch")
        declined = decision.outcome == "rejected" and decision.decided_by == YOU
    _end_record(name, error=error)
    endings = never_started(session_key, name, asking.tasks, error=error, declined=declined)
    _tell(supervisor, endings)


def _tell(supervisor: Any, endings: list[SubagentInfo]) -> None:
    """Hand the conversation that started a batch how it ended (`WorkflowWatchdog.announce`)."""
    announce = getattr(supervisor, "announce", None)
    if not callable(announce):
        return
    try:
        announce(endings)
    except Exception:
        logger.warning("could not tell a batch's chat it never started", exc_info=True)


def _audit(session_key: str, name: str, decision: Any) -> None:
    owner_allow.audit(
        session_key,
        source="subagent",
        tool="subagent_run",
        decision=decision,
        metadata={"batch": name},
    )


def _ended_before_its_answer(why: str) -> str:
    return f"batch not approved: {why} before anyone answered, so it never started"


def ended_unanswered(approval_id: str, reason: str) -> None:
    """Record that the batch *approval_id* asks to start ended before anyone answered, because
    *reason* ("its chat turn was stopped": `owner_allow.end_asks`), so its card says why, the wait
    it wakes tells nobody, and no gateway asks it again."""
    if name := batch_of(approval_id):
        _end_record(name, error=_ended_before_its_answer(reason))


# ── the record a waiting batch keeps ─────────────────────────────────────────────────────────────


def _record_path(name: str) -> Path:
    return store.workflows_dir() / "batches" / f"{name}.json"


def read_record(name: str) -> dict[str, Any] | None:
    """The record of the batch *name* while it waits for its answer, or once it ended before it
    started; ``None`` for no such batch, a name no batch can have, or an unreadable record, which
    reads as no batch: a record only ever asks."""
    if not valid_name(name):
        return None
    try:
        data = json.loads(_record_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save_record(name: str, record: dict[str, Any]) -> None:
    """Write the record of the batch *name*. Best-effort: a lost record costs a restart's ask and
    its card's word on a batch that never started, never the batch itself."""
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(_record_path(name), json.dumps({**record, "name": name}, ensure_ascii=False))
    except Exception:
        logger.warning("batch %s: could not record it", name, exc_info=True)


def _end_record(name: str, *, error: str) -> None:
    """Record that the batch *name* ended before it started, and why. What it would have started
    is dropped with it, so nothing can ask for it again."""
    record = {key: value for key, value in (read_record(name) or {}).items() if key != "start"}
    _save_record(name, {**record, "status": NOT_STARTED, "error": error, "ended_at": time.time()})


def _drop_record(name: str) -> None:
    try:
        _record_path(name).unlink(missing_ok=True)
    except OSError:
        logger.warning("batch %s: could not drop its record", name, exc_info=True)


def state_of(name: str) -> dict[str, Any]:
    """How the batch *name* stands, for its card in the chat that started it: ``started`` with its
    ``run_id`` (its newest run, the one record of a batch that started), ``asking`` for its
    answer, ``starting`` on it, or ``not_started`` with the ``error`` that says why."""
    if not valid_name(name):
        return service._service_failure("WF_BATCH_NOT_FOUND", f"no batch {name!r}")
    runs, _total = store.list_runs(workflow_name=name, limit=1)
    if runs:
        return service._ok(batch=name, status=STARTED, run_id=runs[0].id)
    record = read_record(name)
    if record is None:
        return service._service_failure("WF_BATCH_NOT_FOUND", f"no batch {name!r}")
    return service._ok(
        batch=name,
        status=str(record.get("status") or ASKING),
        tasks=int(record.get("tasks") or 0),
        error=str(record.get("error") or ""),
    )


def task_of_chat(parent_session_key: str) -> tuple[str, str, str]:
    """``(chat, run_id, step)`` for an agent started as a task of a chat's batch, from the key it
    runs under (``workflow:<run>:<node>``): the conversation whose batch it is (its session,
    without ``dashboard:``), the batch's run, and the step as the run names it. Its live events are
    shown in that chat, beside the subagents the chat started itself. Empty for any other agent."""
    from personalclaw.workflows import ownership

    step = ownership.parse_owned(parent_session_key)
    run = store.get(step[0]) if step is not None else None
    if step is None or run is None or not reports_to_a_chat(run):
        return "", "", ""
    label = step[1]
    try:
        for _path, node in walk(Node.from_dict((store.read_spec(run.id) or {}).get("root") or {})):
            if node.id == step[1]:
                label = node.label or step[1]
                break
    except Exception:  # noqa: BLE001 - a name is not worth the event
        logger.debug("run %s: its steps could not be named", run.id, exc_info=True)
    return run.origin.session_key.removeprefix("dashboard:"), run.id, label


def asked_in(approval_id: str) -> str:
    """The conversation whose batch the approval *approval_id* asks to start (its session, without
    ``dashboard:``), or ``""``: what the start's relay lists the ask under, so it is that chat's."""
    name = batch_of(approval_id)
    record = read_record(name) if name else None
    return str((record or {}).get("session_key") or "").removeprefix("dashboard:")


def asker(approval_id: str, title: str) -> str:
    """Who waits on a batch's ask, as the Inbox names it ("A batch of 2 subagent tasks from
    “Retry ceiling”"), or ``""`` when *approval_id* is no batch's ask."""
    name = batch_of(approval_id)
    if not name:
        return ""
    count = int((read_record(name) or {}).get("tasks") or 0)
    what = f"A batch of {count} subagent tasks" if count else "A batch of subagent tasks"
    return f"{what} from “{title}”" if title else what


def resume(state: Any, supervisor: Any) -> int:
    """Ask again every batch a stopped gateway left waiting for its answer, as it was first asked;
    how many. One whose approval window has passed since it was asked ends unstarted, saying
    nobody answered in time, and its conversation hears so, as it would have. One allowed as the
    gateway stopped is started already if its run exists, and asked again if not. A record of a
    batch that never started is dropped once a week old. Run once the workflow supervisor is up."""
    from personalclaw.approval_grants import NOBODY, ToolDecision, approval_window_secs
    from personalclaw.subagent_ask import spawn_refusal

    folder = store.workflows_dir() / "batches"
    asked = 0
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        name = path.stem
        record = read_record(name)
        if record is None:
            continue
        if record.get("status") == NOT_STARTED:
            if time.time() - float(record.get("ended_at") or 0.0) > _KEPT_SECS:
                _drop_record(name)
            continue
        start = record.get("start")
        if record.get("status") not in (ASKING, STARTING) or not isinstance(start, dict):
            continue
        if store.list_runs(workflow_name=name, limit=1)[0]:
            _drop_record(name)  # it started as the gateway stopped: its run is its record
            continue
        session_key = str(record.get("session_key") or "")
        fields = {key: dict(start.get(key) or {}) for key in ("root", "workspace", "inputs")}
        asking = _asking(name, session_key, **fields, writes=dict(start.get("writes") or {}))
        if time.time() - float(record.get("asked_at") or 0.0) > approval_window_secs():
            error = spawn_refusal(ToolDecision(False, "expired", NOBODY), what="batch")
            _end_record(name, error=error)
            _tell(supervisor, never_started(session_key, name, asking.tasks, error=error))
            continue
        _save_record(name, {**record, "status": ASKING})
        begin = _starter(
            supervisor,
            name=name,
            **fields,
            description=str(start.get("description") or ""),
            session_key=session_key,
        )
        _waiting(state, _ask_then_start(state, supervisor, asking, begin))
        asked += 1
    return asked


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
