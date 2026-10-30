"""A chat's run state: what its turn is doing, said as one ``work_phase`` frame per change.

The chat runner measures each step of a turn (``chat_runner.run_chat``): the turn accepted, its
model starting, each tool call by what it is, the first word of its answer, a helper it hands work
to. The registries add what the turn is waiting on — an approval (``DashboardApprovalState``) or a
question (``owner_questions``) for this chat. This module turns those into the chat's one payload
(:class:`RunState`): its phase from ``personalclaw.work_phase`` and the line that says it, the turn
it is about, when its latest step was, whether a stop is asked of it and whether it takes a steer,
how the chat's last turn ended, and how many messages wait behind it.

**What it waits on is typed.** While a turn runs, ``waiting`` and ``helpers_waiting`` are the
waiting list's ids (``personalclaw.waiting``) of the asks it raised itself and of those its helpers
raised, and in ``approval`` or ``input`` ``wait`` is the newest ask's ``work_wait.Wait``: what ends
it, by when, and the registry that says so, never the model's words.

**Sent on a change, never per chunk.** :func:`say` records a step and sends the payload only when
what it says changed; :func:`touch` moves the latest step's time without sending.

**Liveness.** While a turn runs, its payload is sent again every :data:`RESEND_SECS` with its
latest step's time, as a frame that carries data: a turn whose model is silent still says it is
running, and a surface that hears nothing for longer reads the chat again. One timer per running
turn, stopped where its last frame is sent (:func:`end`).

**Served as it was sent.** The chat list and a chat's detail serve the last payload sent
(:func:`served`), so a page that missed frames reads what one that heard them knows; once nothing
runs it is served idle, with how the last turn ended. A chat that never ran here is served
:func:`idle_payload`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from personalclaw.instants import utc_iso
from personalclaw.security import redact_for_display
from personalclaw.textfmt import clip_words
from personalclaw.work_phase import (
    ENDINGS,
    LINE_LIMIT,
    WorkPhase,
    classify_call,
    done_in,
    family_of,
)
from personalclaw.work_wait import Resume, Wait
from personalclaw.workref import work_ref

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)

#: How often a running turn says its phase again, in seconds.
RESEND_SECS = 15.0

#: The clock a phase's times are read from, and the wait between two re-sends of a running turn's
#: phase: the gateway's own, which a test replaces with a clock it moves itself.
_now = time.time
_sleep = asyncio.sleep

#: The line of each phase whose line is the phase alone.
_LINES = {
    WorkPhase.QUEUED: "Waiting to start",
    WorkPhase.THINKING: "Thinking",
    WorkPhase.PLANNING: "Planning",
    WorkPhase.STREAMING: "Writing",
}


@dataclass(frozen=True)
class RunState:
    """What a chat's turn is doing: the payload of its ``work_phase`` frame, served as the chat's
    ``run_state``.

    ``work`` names the chat as a piece of work (``chat:<session>``, ``personalclaw.workref``) and
    ``session`` as the conversation an app's socket may or may not hear of. ``turn`` is the running
    turn — the row that started it (``message_id``, its time) and when it started — or ``None``
    when nothing runs. ``since`` is when the phase or its line last changed and ``last_step_at``
    when the turn's runtime last did anything. ``last_outcome`` is how the chat's latest settled
    turn ended (``chat_runner.terminal_outcome_for_turn``). ``seq`` is the stream stamp
    (``DashboardState.next_stream_seq``) a page compares with its snapshot's, applying only a newer
    frame. Times are UTC instants.
    """

    work: str
    session: str
    kind: str
    phase: str
    line: str
    since: str | None
    turn: dict[str, str] | None
    last_step_at: str | None
    stopping: bool
    steerable: bool
    last_outcome: str | None
    queued_messages: int
    seq: int
    #: Set only in ``retrying``: ``{attempt, of, next_at, reason}`` — this attempt of how many,
    #: when it is made (``None`` once it is, or for a fail-over) and why the last one failed.
    retry: dict[str, Any] | None = None
    #: Set only in ``waiting_model``: ``{model, behind}`` — the local model it waits for, and how
    #: many calls are ahead of it there.
    queue: dict[str, Any] | None = None
    #: While a turn runs, the waiting list's ids (``personalclaw.waiting``) of the asks it raised
    #: itself and of those its helpers raised, oldest first.
    waiting: list[str] = field(default_factory=list)
    helpers_waiting: list[str] = field(default_factory=list)
    #: Set only in ``approval`` and ``input``: the typed wait on the newest ask
    #: (``work_wait.Wait``), which says what ends it and the registry it is known from.
    wait: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Turn:
    """The running turn, as its run state needs it."""

    message_id: str
    started_at: float
    #: How many rows the chat's transcript held when the turn began: the rows after them are its.
    rows_at_start: int = 0
    #: How it ended, once its runner says (:func:`ended`); "" before then.
    outcome: str = ""
    #: The sentence a turn ends on when its agent stopped after the owner declined a step.
    declined: str = ""
    #: Handed on to its retry (:func:`carry_on`): the turn goes on, and the retry ends it.
    carried: bool = False


@dataclass
class ChatRun:
    """One chat's run state between its frames, kept on the chat (``_ChatSession._run``)."""

    turn: _Turn | None = None
    #: What the turn's runner last measured, and its line. Once the turn ends, how it ended.
    phase: WorkPhase = WorkPhase.IDLE
    line: str = ""
    #: When the turn's runtime last did anything.
    last_step: float | None = None
    last_outcome: str | None = None
    #: The phase and line the last frame said, and when they started being said.
    shown: tuple[WorkPhase, str] | None = None
    since: float | None = None
    #: The last payload sent, which the routes serve.
    sent: dict[str, Any] | None = None
    liveness: "asyncio.Task[None] | None" = None
    #: While ``retrying``, the retry's payload field, and while ``waiting_model``, the queue's.
    retry: dict[str, Any] | None = None
    queue: dict[str, Any] | None = None


def _run(session: _ChatSession) -> ChatRun:
    run = session._run
    if run is None:
        run = session._run = ChatRun()
    return run


def idle_payload(key: str) -> dict[str, Any]:
    """The run state of a chat with nothing running and no turn on record here."""
    return RunState(
        work=work_ref("chat", key),
        session=key,
        kind="chat",
        phase=WorkPhase.IDLE.value,
        line="",
        since=None,
        turn=None,
        last_step_at=None,
        stopping=False,
        steerable=False,
        last_outcome=None,
        queued_messages=0,
        seq=0,
    ).as_dict()


def served(session: _ChatSession) -> dict[str, Any]:
    """The chat's run state as the routes serve it: the last payload it was sent, idle and waiting
    on nothing once nothing runs; :func:`idle_payload` when it was sent none."""
    run = session._run
    if run is None or run.sent is None:
        return idle_payload(session.key)
    if run.turn is None:
        idle = {"phase": WorkPhase.IDLE.value, "line": "", "turn": None, "wait": None}
        return {**run.sent, **idle, "waiting": [], "helpers_waiting": []}
    return dict(run.sent)


def _clip(line: str) -> str:
    return clip_words(redact_for_display(str(line or "")), LINE_LIMIT)


@dataclass(frozen=True)
class _Asks:
    """What a chat's running turn waits on the owner for: the waiting list's ids of its own asks
    and of its helpers', and the phase, line and wait the newest ask is said with."""

    own: list[str] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)
    newest: tuple[WorkPhase, str, dict[str, Any]] | None = None


def _ts(ask: dict[str, Any]) -> float:
    return float(ask.get("ts") or 0.0)


def _deadline(state: DashboardState, ask: dict[str, Any]) -> str | None:
    """When an unanswered *ask* ends: the owner's window after it was asked
    (``agent.approval_timeout_minutes``), which an approval and a question each wait before it
    ends unanswered (``owner_questions.ask``), as the waiting list says. ``None`` when the window
    cannot be read."""
    try:
        window = float(state.approval_window_secs())
    except Exception:  # noqa: BLE001 — a window that cannot be read is no deadline
        window = 0.0
    return utc_iso(_ts(ask) + window) if _ts(ask) and window > 0 else None


def _asks(state: DashboardState, session: _ChatSession) -> _Asks:
    """The approvals that name the chat (its own calls', or a helper's it started: the approval's
    id names the helper, ``subagent.approval_subagent_id``) and the questions it asks the owner,
    by their waiting list ids. The newest approval, else the newest question, is the phase."""
    from personalclaw.subagent import approval_subagent_id

    pending = getattr(state, "_pending_approvals", None) or {}
    named = (e for e in pending.values() if str(e.get("session") or "") == session.key)
    approvals = sorted((e for e in named if e.get("id")), key=_ts)
    questions = getattr(state, "owner_questions", None)
    asked = questions.pending_for(session.key) if questions is not None else []
    cards = sorted((c for c in asked if c.get("id")), key=_ts)
    mine = [("approval", e) for e in approvals if not approval_subagent_id(str(e["id"]))]
    theirs = [e for e in approvals if approval_subagent_id(str(e["id"]))]
    own = sorted(mine + [("question", c) for c in cards], key=lambda ask: _ts(ask[1]))
    asks = _Asks([f"{kind}:{a['id']}" for kind, a in own], [f"approval:{e['id']}" for e in theirs])
    if approvals:
        newest = approvals[-1]
        resume = Resume("answered", f"approval:{newest['id']}")
        wait = Wait("owner_answer", resume, _deadline(state, newest), "approval_registry")
        line = f"Needs your OK: {newest.get('tool') or 'a step'}"
        return _Asks(asks.own, asks.helpers, (WorkPhase.APPROVAL, line, wait.as_dict()))
    if cards:
        newest = cards[-1]
        resume = Resume("answered", f"question:{newest['id']}")
        wait = Wait("input", resume, _deadline(state, newest), "question_registry")
        question = (newest.get("questions") or [{}])[0].get("question") or "a question"
        return _Asks(asks.own, asks.helpers, (WorkPhase.INPUT, f"Asks: {question}", wait.as_dict()))
    return asks


def _shown(
    state: DashboardState, session: _ChatSession, run: ChatRun
) -> tuple[WorkPhase, str, _Asks]:
    asks = _asks(state, session) if run.turn is not None else _Asks()
    if asks.newest is not None:
        return asks.newest[0], _clip(asks.newest[1]), asks
    return run.phase, run.line, asks


def _coarse(payload: dict[str, Any]) -> tuple[bool, str, bool]:
    """What a chat list row shows of a payload: running or not, its family, and a stop asked."""
    return (payload["turn"] is not None, family_of(payload["phase"]).value, payload["stopping"])


#: The fields that are not news: a frame is sent when anything else changed.
_STAMPS = ("seq", "last_step_at")


def _send(state: DashboardState, session: _ChatSession, run: ChatRun, *, again=False) -> None:
    """Send the chat's payload when what it says changed; *again*, whether or not it did."""
    phase, line, asks = _shown(state, session, run)
    if run.shown != (phase, line):
        run.shown, run.since = (phase, line), _now()
    turn = run.turn
    payload = RunState(
        work=work_ref("chat", session.key),
        session=session.key,
        kind="chat",
        phase=phase.value,
        line=line,
        since=utc_iso(run.since) if run.since is not None else None,
        turn=(
            {"message_id": turn.message_id, "started_at": utc_iso(turn.started_at)}
            if turn is not None
            else None
        ),
        last_step_at=utc_iso(run.last_step) if run.last_step is not None else None,
        # Asked of the running turn only: a stop is over with the turn it was asked of, whether or
        # not the owner's Stop has heard so yet.
        stopping=turn is not None and session._stopping,
        steerable=turn is not None and session._takes_steers,
        last_outcome=run.last_outcome,
        queued_messages=session.queue_depth,
        seq=0,
        retry=run.retry if phase is WorkPhase.RETRYING else None,
        queue=run.queue if phase is WorkPhase.WAITING_MODEL else None,
        waiting=asks.own,
        helpers_waiting=asks.helpers,
        wait=asks.newest[2] if asks.newest is not None else None,
    ).as_dict()
    previous = run.sent
    if not again and previous is not None:
        if all(previous[k] == payload[k] for k in payload if k not in _STAMPS):
            return
    payload["seq"] = state.next_stream_seq()
    run.sent = payload
    state.broadcast_ws("work_phase", payload)
    # The running list moved with it, unless this says again what it said (`running_now`).
    try:
        from personalclaw import running_now

        running_now.chat_said(state, session, again=again)
    except Exception:
        logger.debug("could not ring the running list for %s", session.key, exc_info=True)
    if previous is None or _coarse(previous) != _coarse(payload):
        state.push_sessions_update()
    # A running loop's worker is a chat: what its turn does, its loop does (`work_phases`). Only
    # read from the chat's frame, so a failure saying the loop's never stops the chat's own.
    try:
        from personalclaw import work_phases

        work_phases.worker_said(state, session.key, payload)
    except Exception:
        logger.debug("could not say the loop phase of %s", session.key, exc_info=True)


async def _keep_saying(state: DashboardState, session: _ChatSession, turn: _Turn) -> None:
    """Say the running turn's phase again on the gateway's clock, until it ends."""
    run = _run(session)
    while run.turn is turn:
        await _sleep(RESEND_SECS)
        if run.turn is turn:
            _send(state, session, run, again=True)


def begin(state: DashboardState, session: _ChatSession, message_id: str = "") -> None:
    """A turn was accepted on *session*: it is waiting to start, said at once, and said again until
    it ends. *message_id* is the time of the row that started it; a turn with no row of its own is
    named by an id minted for it."""
    run = _run(session)
    _stop_saying(run)
    run.turn = _Turn(
        message_id=message_id or f"turn-{uuid.uuid4().hex[:12]}",
        started_at=_now(),
        rows_at_start=len(session.messages),
    )
    run.phase, run.line, run.last_step = WorkPhase.QUEUED, _LINES[WorkPhase.QUEUED], None
    _send(state, session, run)
    # Held by the chat's run state, which cancels it where the turn ends: it is the turn's own,
    # not background work of the gateway's that anything else waits on.
    run.liveness = asyncio.get_running_loop().create_task(
        _keep_saying(state, session, run.turn), name=f"work_phase:{session.key}"
    )


def take_up(state: DashboardState, session: _ChatSession, message_id: str = "") -> None:
    """A retry of the chat's turn starts (``chat_runner``'s ``_send_again``): it goes on as the same
    turn, which this run now ends. With no turn on record to take up, it begins one."""
    run = _run(session)
    if run.turn is not None and run.turn.carried:
        run.turn.carried = False
        return
    begin(state, session, message_id)


def carry_on(state: DashboardState, session: _ChatSession, task: "asyncio.Task[Any]") -> None:
    """The chat's turn goes on as its retry, *task*, which says how it ended. A retry that never
    takes it up — dropped before its first step — ends it here."""
    run = _run(session)
    turn = run.turn
    if turn is None:
        return
    turn.carried, turn.outcome, turn.declined = True, "", ""

    def _dropped(_task: "asyncio.Task[Any]") -> None:
        if run.turn is turn and turn.carried:
            turn.carried = False
            end(state, session)

    task.add_done_callback(_dropped)


def ended(session: _ChatSession, outcome: str, *, declined: str = "") -> None:
    """Record how the chat's running turn ended (``terminal_outcome_for_turn``), for its last frame:
    *declined* is the sentence it ends on when its agent stopped after the owner declined a step."""
    turn = _run(session).turn
    if turn is not None:
        turn.outcome, turn.declined = outcome, declined


def _reason(session: _ChatSession, turn: _Turn) -> str:
    """The error the turn ended on: the latest error row written since it began, never an earlier
    turn's."""
    for message in reversed(session.messages[turn.rows_at_start :]):
        if message.get("role") == "error" and message.get("content"):
            return str(message["content"])
    return ""


def _ending_line(session: _ChatSession, ending: str, turn: _Turn) -> str:
    if ending == "complete":
        return done_in(max(0.0, _now() - turn.started_at))
    if ending == "declined":
        return turn.declined
    reason = _reason(session, turn) if ending in ("error", "interrupted") else ""
    return f"Stopped: {reason}" if reason else "Stopped"


def end(state: DashboardState, session: _ChatSession, *, unsaid: str = "stopped") -> None:
    """The chat's turn is over: say how it ended, in the last frame it is sent, and stop saying it
    runs. A turn handed on to its retry is not over. *unsaid* is how a turn whose runner never said
    how it ended is said to have: stopped, unless it raised."""
    run = _run(session)
    turn = run.turn
    if turn is None or turn.carried:
        return
    outcome = turn.outcome or unsaid
    ending = "declined" if turn.declined else outcome
    run.turn = None
    _stop_saying(run)
    run.last_outcome = outcome
    run.phase, run.line = ENDINGS[ending], _clip(_ending_line(session, ending, turn))
    _send(state, session, run)
    # Its ending joins what just finished (`running_now`). Only read from the frame, so a failure
    # there never stops the chat's own.
    try:
        from personalclaw import running_now

        running_now.chat_ended(state, session)
    except Exception:
        logger.debug("could not keep the ending of %s", session.key, exc_info=True)


def _stop_saying(run: ChatRun) -> None:
    if run.liveness is not None:
        run.liveness.cancel()
        run.liveness = None


def touch(session: _ChatSession) -> None:
    """The running turn's runtime did something: its latest step is now. Sends nothing."""
    run = session._run
    if run is not None and run.turn is not None:
        run.last_step = _now()


def say(
    state: DashboardState, session: _ChatSession, phase: WorkPhase, line: str | None = None
) -> None:
    """The chat's running turn is now in *phase*, said with *line* (the phase's own words by
    default). Sent when it changes what the chat's payload says."""
    run = _run(session)
    if run.turn is None:
        return
    run.phase, run.line = phase, _clip(_LINES.get(phase, "") if line is None else line)
    if phase is not WorkPhase.RETRYING:
        run.retry = None
    if phase is not WorkPhase.WAITING_MODEL:
        run.queue = None
    _send(state, session, run)


def thinking(session: _ChatSession) -> WorkPhase:
    """What the turn does between its steps: plans, in plan mode; thinks, otherwise."""
    return (
        WorkPhase.PLANNING if getattr(session, "_task_mode", "") == "plan" else WorkPhase.THINKING
    )


def step(state: DashboardState, session: _ChatSession, title: str, kind: str) -> None:
    """The turn's runtime started a call (its card's *title* and its declared *kind*)."""
    say(state, session, *classify_call(title, kind))


def writing(state: DashboardState, session: _ChatSession) -> None:
    """The turn's answer streams: said once, on its first word after any other phase."""
    run = session._run
    if run is not None and run.phase is not WorkPhase.STREAMING:
        say(state, session, WorkPhase.STREAMING)


def restate(state: DashboardState, session: _ChatSession) -> None:
    """Something the payload carries beside the phase changed: a stop was asked or answered, a
    message joined the queue or left it, the turn's steer was wired. Sent when it changed."""
    if session._run is not None:
        _send(state, session, session._run)


def waiting_changed(state: DashboardState, key: str) -> None:
    """An approval or a question naming the chat *key* was raised or ended: while its turn runs, the
    phase is what it waits on, and goes back to its step when nothing is left waiting."""
    session = getattr(state, "_sessions", {}).get(str(key or ""))
    if session is None or session._run is None or session._run.turn is None:
        return
    touch(session)
    _send(state, session, session._run)


def helpers_at_work(state: DashboardState, key: str, count: int) -> None:
    """*count* helpers work for the chat *key* now. While its turn runs it is handing off to them;
    once none is left, the turn is back to thinking unless it has moved on. A helper that outlives
    its turn holds nothing."""
    session = getattr(state, "_sessions", {}).get(str(key or ""))
    run = session._run if session is not None else None
    if session is None or run is None or run.turn is None:
        return
    if count > 0:
        say(state, session, WorkPhase.DELEGATING, f"Handing off to {count} helper{_s(count)}")
    elif run.phase is WorkPhase.DELEGATING:
        say(state, session, thinking(session))


def _s(count: int) -> str:
    return "" if count == 1 else "s"


# ── Retries and waits, from what was measured (#3833) ───────────────────────────────────────────


def retrying(
    state: DashboardState,
    session: _ChatSession,
    *,
    attempt: int,
    of: int,
    reason: str = "",
    next_in: float | None = None,
    model: str = "",
) -> None:
    """The running turn's model call failed and is tried again: this *attempt* of *of*, in
    *next_in* seconds, after *reason* (the provider's failure in its sentence); or, for a fail-over,
    on the next *model* of its chain. A retry is not a failure until it fails, so it is live work,
    said as ``retrying`` until the retry starts (:func:`retry_started`)."""
    run = _run(session)
    if run.turn is None:
        return
    next_at = _now() + next_in if next_in is not None else None
    if model:
        line = f"Trying {model}"
    else:
        line = f"Retrying ({attempt} of {of})"
        # A retry a moment away needs no time; one a minute or more away says when.
        if next_at is not None and next_in is not None and next_in >= 60:
            line += f" · next try {time.strftime('%H:%M', time.localtime(next_at))}"
    # Set before it is said, so the one frame carries it.
    run.retry = {
        "attempt": attempt,
        "of": of,
        "next_at": utc_iso(next_at) if next_at is not None else None,
        "reason": _clip(reason),
    }
    say(state, session, WorkPhase.RETRYING, line)


def retry_started(state: DashboardState, session: _ChatSession) -> None:
    """The retry's call is made, or the next model answers: the turn is thinking again."""
    run = session._run
    if run is not None and run.turn is not None and run.phase is WorkPhase.RETRYING:
        say(state, session, thinking(session))


def model_waits_changed(state: DashboardState, waits: list[dict[str, Any]]) -> None:
    """The local model queue's waits changed (``guardrails.local_queue.waits``): each running chat
    whose call waits behind other calls on a local model says ``waiting_model``, with the model and
    how many are ahead, and goes back to thinking when the model takes its call."""
    waiting: dict[str, dict[str, Any]] = {}
    for row in waits:
        key = str(row.get("session") or "").removeprefix("dashboard:")
        if key and key not in waiting:
            waiting[key] = row
    for key, session in list(getattr(state, "_sessions", {}).items()):
        run = session._run
        if run is None or run.turn is None:
            continue
        held = waiting.get(key)
        if held is not None:
            model = str(held.get("model") or "").partition(":")[2] or str(held.get("model") or "")
            behind = int(held.get("ahead") or 0)
            run.queue = {"model": str(held.get("model") or ""), "behind": behind}
            say(state, session, WorkPhase.WAITING_MODEL, f"Waiting for {model} · {behind} ahead")
        elif run.phase is WorkPhase.WAITING_MODEL:
            say(state, session, thinking(session))
