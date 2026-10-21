"""A chat's run state: what its turn is doing, said as one ``work_phase`` frame per change.

The chat runner measures each step of a turn (``chat_runner.run_chat``): the turn accepted, its
model starting, each tool call by what it is, the first word of its answer, a helper it hands work
to. The registries add what the turn is waiting on — an approval (``DashboardApprovalState``) or a
question (``owner_questions``) for this chat. This module turns those into the chat's one payload
(:class:`RunState`): its phase from ``personalclaw.work_phase`` and the line that says it, the turn
it is about, when its latest step was, whether a stop is asked of it and whether it takes a steer,
how the chat's last turn ended, and how many messages wait behind it.

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
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from personalclaw.instants import utc_iso
from personalclaw.security import redact_for_display
from personalclaw.textfmt import clip_words
from personalclaw.work_phase import ENDINGS, WorkPhase, classify_call, done_in, family_of
from personalclaw.workref import work_ref

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)

#: How often a running turn says its phase again, in seconds.
RESEND_SECS = 15.0

#: The longest line a phase frame carries. Each surface cuts it shorter, to its own room.
LINE_LIMIT = 160

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
    """The chat's run state as the routes serve it: the last payload it was sent, idle once nothing
    runs; :func:`idle_payload` when it was sent none."""
    run = session._run
    if run is None or run.sent is None:
        return idle_payload(session.key)
    if run.turn is None:
        return {**run.sent, "phase": WorkPhase.IDLE.value, "line": "", "turn": None}
    return dict(run.sent)


def _clip(line: str) -> str:
    return clip_words(redact_for_display(str(line or "")), LINE_LIMIT)


def _waiting_on(state: DashboardState, session: _ChatSession) -> tuple[WorkPhase, str] | None:
    """What the chat's running turn waits on the owner for: the newest approval that names the chat
    (its own call's, or a helper's it started), else the newest question it asks her."""
    pending = getattr(state, "_pending_approvals", None) or {}
    asks = [e for e in pending.values() if str(e.get("session") or "") == session.key]
    if asks:
        newest = max(asks, key=lambda e: float(e.get("ts") or 0.0))
        return WorkPhase.APPROVAL, f"Needs your OK: {newest.get('tool') or 'a step'}"
    questions = getattr(state, "owner_questions", None)
    cards = questions.pending_for(session.key) if questions is not None else []
    if cards:
        newest = max(cards, key=lambda c: float(c.get("ts") or 0.0))
        asked = (newest.get("questions") or [{}])[0].get("question") or "a question"
        return WorkPhase.INPUT, f"Asks: {asked}"
    return None


def _shown(state: DashboardState, session: _ChatSession, run: ChatRun) -> tuple[WorkPhase, str]:
    if run.turn is not None:
        waiting = _waiting_on(state, session)
        if waiting is not None:
            return waiting[0], _clip(waiting[1])
    return run.phase, run.line


def _coarse(payload: dict[str, Any]) -> tuple[bool, str, bool]:
    """What a chat list row shows of a payload: running or not, its family, and a stop asked."""
    return (payload["turn"] is not None, family_of(payload["phase"]).value, payload["stopping"])


#: The fields that are not news: a frame is sent when anything else changed.
_STAMPS = ("seq", "last_step_at")


def _send(state: DashboardState, session: _ChatSession, run: ChatRun, *, again=False) -> None:
    """Send the chat's payload when what it says changed; *again*, whether or not it did."""
    phase, line = _shown(state, session, run)
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
        # not her Stop has heard so yet.
        stopping=turn is not None and session._stopping,
        steerable=turn is not None and session._takes_steers,
        last_outcome=run.last_outcome,
        queued_messages=session.queue_depth,
        seq=0,
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
        from personalclaw.dashboard import work_phases

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
