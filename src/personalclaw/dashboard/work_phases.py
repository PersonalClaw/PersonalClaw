"""What a helper, a loop and a workflow run are doing: each said as one ``work_phase`` frame per
change, the frame a chat's turn says its phases in (``chat_phase``).

Each kind is read from what the gateway already measures, and classified by
``personalclaw.work_phase``:

- **A helper** (``subagent:<id>``), from the subagent manager's events
  (``gateway._subagent_event``): waiting for a slot, started, each call by what it is (the chat's
  own rule, ``classify_call``), its first words, and how it ended; and from the approval registry,
  while its start or one of its calls waits on the owner's OK.
- **A loop** (``loop:<id>``), from its status as the loop store writes it; while it runs, from
  its worker's turn, which is a chat's and says its own phases: the loop says them too; and from
  the incident switch, which holds a running loop without writing its status.
- **A workflow run** (``workflow:<run>``), from its run's events: its status and whether incident
  mode holds it, each step it starts (a tool, named as its author named the step), and the ask of
  a gate it waits at.

**The frame** is ``{work, kind, session, phase, line, since, seq}``. ``session`` is the chat the
work is for: the chat a helper works for, its own or the one whose batch it is a task of
(:func:`chat_of`). It is ``""`` for work no chat started: a loop, a run, and a helper started by
anything else (a workflow step no chat's batch is, an app's own run, another helper, a loop's
worker, a schedule), whatever key its parent runs under. A frame naming a chat reaches an app's
socket only when the app started that chat, as every chat frame does; a frame about work no chat
started reaches no app's socket, only the owner's (``ws_state.WORK_FRAMES``). The line is the
state only, redacted and capped as a chat's is, and ``seq`` is the stream stamp a chat's frames
carry.

**Sent on a change, never per chunk.** A frame goes out when the phase or its line changes; a wait
on the owner is said over the step it interrupts, which is said again when the wait ends.

**Ended work is forgotten here.** Its last frame says how it ended, and nothing is kept of it
after: :func:`current` answers only for work that has not ended. The running list keeps that last
frame a while, in its just-finished tail (``running_now.ended``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from personalclaw import session_keys
from personalclaw.dashboard.chat_phase import LINE_LIMIT
from personalclaw.instants import utc_iso
from personalclaw.security import redact_for_display
from personalclaw.textfmt import clip_words
from personalclaw.work_phase import (
    HELD,
    RUN_STATUS_PHASES,
    WorkPhase,
    classify_call,
    gate_phase,
    helper_ending,
    loop_phase,
    run_phase,
)
from personalclaw.workref import work_ref

logger = logging.getLogger(__name__)

#: The clock a phase's times are read from: the gateway's own, which a test replaces.
_now = time.time

#: The phases that end a piece of work.
_ENDINGS = (WorkPhase.DONE, WorkPhase.ERROR)

#: Each phase whose line is the phase alone.
_LINES = {
    WorkPhase.QUEUED: "Waiting to start",
    WorkPhase.THINKING: "Thinking",
    WorkPhase.STREAMING: "Writing",
}


@dataclass
class _Work:
    """One piece of work between its frames."""

    kind: str
    session: str
    #: What it is doing, as its producer last measured it, and that step's line.
    step: tuple[WorkPhase, str]
    #: The approvals it waits on the owner for, by id, each with its line.
    asks: dict[str, str] = field(default_factory=dict)
    #: The phase and line its last frame said, and when they started being said.
    said: tuple[WorkPhase, str] | None = None
    since: float = 0.0
    #: The last payload it was sent.
    sent: dict[str, Any] | None = None
    #: A loop's status as last written, so its worker's turn speaks for it only while it runs.
    status: str = ""
    #: A workflow run's gate's ask, which outlasts the run's own needs-input status.
    gate: tuple[WorkPhase, str] | None = None
    #: Why incident mode holds a running workflow run, as its publisher last said (``""``: it
    #: does not).
    held: str = ""


def _book(state: Any) -> dict[str, _Work]:
    """The work *state* is saying the phases of, by its name."""
    book = getattr(state, "_work_phases", None)
    if not isinstance(book, dict):
        book = {}
        state._work_phases = book
    return book


def _clip(line: str) -> str:
    return clip_words(redact_for_display(str(line or "")), LINE_LIMIT)


def current(state: Any, work: str) -> dict[str, Any] | None:
    """The last payload *work* was sent, while it has not ended; ``None`` otherwise."""
    held = _book(state).get(work)
    return dict(held.sent) if held is not None and held.sent is not None else None


def _send(state: Any, work: str, held: _Work) -> None:
    """Send *work*'s phase when what it says changed: the wait it is held in, else its step."""
    phase, line = (WorkPhase.APPROVAL, list(held.asks.values())[-1]) if held.asks else held.step
    if held.said == (phase, line):
        return
    held.said, held.since = (phase, line), _now()
    payload = {
        "work": work,
        "kind": held.kind,
        "session": held.session,
        "phase": phase.value,
        "line": line,
        "since": utc_iso(held.since),
        "seq": state.next_stream_seq(),
    }
    held.sent = payload
    state.broadcast_ws("work_phase", payload)
    # The running list moved with it (`running_now`).
    try:
        from personalclaw import running_now

        running_now.changed(state, work, held.session)
    except Exception:
        logger.debug("could not ring the running list for %s", work, exc_info=True)


def _say(
    state: Any, work: str, kind: str, phase: WorkPhase, line: str | None = None, *, session=None
) -> _Work:
    """*work* is now in *phase*, said with *line* (the phase's own words by default); its last
    frame when *phase* ends it, after which it is forgotten. *session* is the chat it is for, when
    the producer knows it."""
    book = _book(state)
    held = book.get(work)
    step = (phase, _clip(_LINES.get(phase, "") if line is None else line))
    if held is None:
        held = book[work] = _Work(kind=kind, session=str(session or ""), step=step)
    else:
        held.step = step
        if session is not None:
            held.session = str(session)
    if phase in _ENDINGS:
        held.asks.clear()
        book.pop(work, None)
    _send(state, work, held)
    if phase in _ENDINGS and held.sent is not None:
        # Its ending joins what just finished (`running_now`). Only read from the frame, so a
        # failure there never stops the frame.
        try:
            from personalclaw import running_now

            running_now.ended(state, held.sent)
        except Exception:
            logger.debug("could not keep the ending of %s", work, exc_info=True)
    return held


# ── A helper ─────────────────────────────────────────────────────────────────────────────────


def chat_of(session: str) -> str:
    """The chat a helper works for, from the key *session* its events and its asks name its parent
    by (``dashboard:`` or not): a chat's own name, or for a workflow step's key, the chat whose
    batch the step is a task of (``batch_start.task_of_chat``). ``""`` for any other key, as for
    none: a step no chat's batch is, an app's own run, another helper, a loop's worker, a schedule
    and every other kind of work (``session_keys``) is work no chat started."""
    name = str(session or "").removeprefix(session_keys.DASHBOARD.prefix)
    kind = session_keys.kind_of(name)
    if kind is None or kind is session_keys.CHAT:
        return name
    if kind is session_keys.WORKFLOW_STEP:
        from personalclaw.workflows.batch_start import task_of_chat

        return task_of_chat(name)[0]
    return ""


def helper_event(state: Any, event: str, info: Any, extra: Mapping[str, Any], *, session: str):
    """The helper *info* sent *event* (``SubagentManager``'s, through ``gateway._subagent_event``),
    its parent named *session*, as its event frames name it: its phase, when it changed, for the
    chat it works for (:func:`chat_of`)."""
    work = work_ref("subagent", str(info.id))
    if event == "subagent_queued":
        if getattr(info, "done", False):
            return  # stopped before its wait was heard: its ending is already said, and forgotten
        _say(state, work, "subagent", WorkPhase.QUEUED, session=chat_of(session))
    elif event == "subagent_spawn":
        _say(state, work, "subagent", WorkPhase.THINKING, session=chat_of(session))
    elif event == "subagent_tool":
        phase, line = classify_call(str(extra.get("tool") or ""), str(extra.get("tool_kind") or ""))
        _say(state, work, "subagent", phase, line, session=chat_of(session))
    elif event == "subagent_chunk":
        held = _book(state).get(work)
        if held is None or held.step[0] is not WorkPhase.STREAMING:
            _say(state, work, "subagent", WorkPhase.STREAMING, session=chat_of(session))
    elif event == "subagent_done":
        _say(state, work, "subagent", *helper_ending(info), session=chat_of(session))


def approval_changed(state: Any, entry: Mapping[str, Any], *, waiting: bool) -> None:
    """An approval was raised (*waiting*) or ended: while one a helper asked for waits — its start's
    or one of its calls' — the helper waits on the owner's OK, and is back on its step after."""
    from personalclaw.subagent import approval_subagent_id

    approval = str(entry.get("id") or "")
    agent = approval_subagent_id(approval)
    if not agent:
        return
    work = work_ref("subagent", agent)
    book = _book(state)
    held = book.get(work)
    if waiting:
        if held is None:
            # A start that asks before it runs: it waits to start, on her OK.
            step = (WorkPhase.QUEUED, _LINES[WorkPhase.QUEUED])
            session = chat_of(str(entry.get("session") or ""))
            held = book[work] = _Work(kind="subagent", session=session, step=step)
        held.asks[approval] = _clip(f"Needs your OK: {entry.get('tool') or 'a step'}")
    elif held is None or held.asks.pop(approval, None) is None:
        return
    _send(state, work, held)


# ── A loop ───────────────────────────────────────────────────────────────────────────────────


def loop_status(state: Any, loop: Any) -> None:
    """The loop *loop* (``loop.loop.Loop``) was written with its status: its phase, when it
    changed, held while incident mode holds it running. A loop has no chat of its own."""
    from personalclaw.loop.loop import held_reason

    status = str(getattr(loop.status, "value", loop.status))
    phase, line = loop_phase(
        status,
        stop_reason=str(getattr(loop, "stop_reason", "") or ""),
        held=held_reason(status),
        reason=str(getattr(loop, "error_message", "") or ""),
    )
    held = _say(state, work_ref("loop", str(loop.id)), "loop", phase, line, session="")
    held.status = status


def loop_deleted(state: Any, loop_id: str) -> None:
    """The loop *loop_id* was deleted: one that had not ended says it stopped, its last frame."""
    work = work_ref("loop", str(loop_id))
    if work in _book(state):
        _say(state, work, "loop", *loop_phase("stopped"))


def _running() -> tuple[WorkPhase, str]:
    """What a running loop says between its worker's turns: running, or held while incident mode
    holds it (its views' own reading, ``loop.loop.held_reason``)."""
    from personalclaw.loop.loop import held_reason

    return loop_phase("running", held=held_reason("running"))


def worker_said(state: Any, key: str, payload: Mapping[str, Any]) -> None:
    """The chat *key* said its run state, *payload*: when it is a running loop's worker, the loop is
    doing what its worker's turn does, and is running again once that turn ends. A loop that is
    not running, or that incident mode holds, is not doing what its worker's turn still says."""
    if not session_keys.LOOP.names(key):
        return
    from personalclaw.loop.manager import worker_ids

    loop_id, _task = worker_ids(key)
    work = work_ref("loop", loop_id) if loop_id else ""
    held = _book(state).get(work) if work else None
    if held is None or held.status != "running":
        return
    phase, line = _running()
    if payload.get("turn") is not None and (phase, line) != HELD:
        phase, line = WorkPhase(str(payload.get("phase"))), str(payload.get("line") or "")
    _say(state, work, "loop", phase, line)


def incident_changed(state: Any) -> None:
    """The incident switch moved: each running loop says whether the switch holds it now, since a
    hold writes no status of its own. A run says its own hold as it publishes (:func:`run_event`).
    """
    for work, held in list(_book(state).items()):
        if held.kind == "loop" and held.status == "running":
            phase, line = _running()
            if held.step == HELD or (phase, line) == HELD:
                _say(state, work, "loop", phase, line)


# ── A workflow run ───────────────────────────────────────────────────────────────────────────


def run_event(state: Any, run_id: str, event: str, payload: Mapping[str, Any]) -> None:
    """The workflow run *run_id* published *event* (its watchdog's publisher, before any
    batching): its phase, when it changed. A run has no chat of its own. Incident mode holds a
    running run without changing its status: the run says so, and that it carries on, in the
    ``held`` of its update (``workflows.incident_hold``), which an update that names none leaves
    as it was while the run runs."""
    work = work_ref("workflow", str(run_id))
    held = _book(state).get(work)
    if event == "workflow_run_update":
        status = str(payload.get("status") or "")
        if status not in RUN_STATUS_PHASES:
            return
        if "held" in payload:
            hold = str(payload.get("held") or "")
        else:
            hold = held.held if held is not None and status == "running" else ""
        phase, line = run_phase(status, reason=str(payload.get("error") or ""), held=hold)
        if status == "needs_input" and held is not None and held.gate is not None:
            phase, line = held.gate
        if status != "needs_input" and held is not None:
            held.gate = None
        held = _say(state, work, "workflow", phase, line, session="")
        held.held = hold if status == "running" else ""
    elif event == "workflow_node_started":
        if held is not None and held.gate is not None:
            return
        step = str(payload.get("label") or payload.get("node_id") or "")
        _say(state, work, "workflow", WorkPhase.TOOL, step, session="")
    elif event == "workflow_attention":
        ask = payload.get("ask")
        if payload.get("kind") == "escalation" or not isinstance(ask, Mapping):
            return
        gate = gate_phase(ask)
        held = _say(state, work, "workflow", *gate, session="")
        held.gate = held.step
    elif event == "workflow_gate_resolved" and held is not None and held.gate is not None:
        held.gate = None
        _say(state, work, "workflow", *run_phase("running"))
