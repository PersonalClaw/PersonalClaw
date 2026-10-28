"""The phases of a piece of work: what an agent is doing, said the same way on every surface.

A chat turn, a loop, a workflow run and a helper are each, at any moment, in one phase of the
closed set below (each kind's producer is in :data:`PHASE_PRODUCERS`). Each phase has one meaning,
one tone, one of four shared visual families, and the copy row its one line is written from. The
gateway decides the phase from what it measures (``work_phase`` frames, a chat's ``run_state``),
and every surface reads the phase's tone, family and words from ``web/src/lib/workPhase.ts``, which
``tests/test_work_phase_vocabulary.py`` pins to this table in both directions — two tables of one
vocabulary drift, as the loop status's did.

**The four families** carry urgency: ``active`` (work in flight), ``waiting`` (on the owner's yes
or her answer), ``blocked`` (it failed) and ``quiet`` (it finished, or nothing runs). Each
surface's own looks are variants inside them, and the line always carries the exact phase, so no
surface shows a phase by colour or motion alone.

**The tones.** Live work is the scheme's primary (coral), never a colour of its own; waiting on the
owner's yes is warn and on her answer info; a finish is ok and a failure danger; nothing running
carries no accent. Listening keeps warn, which matches the system's own microphone dot: the in-app
half of a capture indicator is not recoloured here.

**A line holds the state only** ("Searching your knowledge", "Needs your OK: shell"): a surface adds
the work's name, cut to its own length.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from personalclaw.auth.lifetimes import duration_words

#: The longest line a phase frame carries. Each surface cuts it shorter, to its own room.
LINE_LIMIT = 160


class WorkPhase(str, Enum):
    """What a piece of work is doing now."""

    QUEUED = "queued"
    THINKING = "thinking"
    SEARCHING = "searching"
    TOOL = "tool"
    PLANNING = "planning"
    DELEGATING = "delegating"
    STREAMING = "streaming"
    LISTENING = "listening"
    APPROVAL = "approval"
    INPUT = "input"
    DONE = "done"
    ERROR = "error"
    RETRYING = "retrying"
    WAITING_MODEL = "waiting_model"
    IDLE = "idle"


class Family(str, Enum):
    """The four shared visual families every phase reads as."""

    QUIET = "quiet"
    ACTIVE = "active"
    WAITING = "waiting"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PhaseLook:
    """How a phase is shown: what it means, its tone token, its family, and the copy row its line is
    written from."""

    meaning: str
    tone: str
    family: Family
    line_key: str


_ACTIVE, _WAITING, _BLOCKED, _QUIET = Family.ACTIVE, Family.WAITING, Family.BLOCKED, Family.QUIET

#: Every phase, with its look. The one table of them in the gateway.
PHASES: Mapping[WorkPhase, PhaseLook] = MappingProxyType(
    {
        WorkPhase.QUEUED: PhaseLook("accepted, not started yet", "primary", _ACTIVE, "queued"),
        WorkPhase.THINKING: PhaseLook("started, no output yet", "primary", _ACTIVE, "thinking"),
        WorkPhase.SEARCHING: PhaseLook(
            "searching knowledge, memory, the web or the files", "primary", _ACTIVE, "searching"
        ),
        WorkPhase.TOOL: PhaseLook("running any other tool", "primary", _ACTIVE, "tool"),
        WorkPhase.PLANNING: PhaseLook("making or revising a plan", "primary", _ACTIVE, "planning"),
        WorkPhase.DELEGATING: PhaseLook(
            "handing work to helpers", "primary", _ACTIVE, "delegating"
        ),
        WorkPhase.STREAMING: PhaseLook("writing its answer", "primary", _ACTIVE, "streaming"),
        WorkPhase.LISTENING: PhaseLook(
            "push-to-talk capture is live", "warn", _ACTIVE, "listening"
        ),
        WorkPhase.APPROVAL: PhaseLook("waiting on the owner's yes", "warn", _WAITING, "approval"),
        WorkPhase.INPUT: PhaseLook("waiting on the owner's answer", "info", _WAITING, "question"),
        WorkPhase.DONE: PhaseLook("finished", "ok", _QUIET, "done"),
        WorkPhase.ERROR: PhaseLook("failed", "danger", _BLOCKED, "error"),
        WorkPhase.RETRYING: PhaseLook(
            "a retry or a fail-over is pending", "primary", _ACTIVE, "retrying"
        ),
        WorkPhase.WAITING_MODEL: PhaseLook(
            "held behind other calls on a local model", "primary", _ACTIVE, "waiting_model"
        ),
        WorkPhase.IDLE: PhaseLook("nothing queued or running", "neutral", _QUIET, "idle"),
    }
)

#: The gateway's own states, by the family each reads as.
SYSTEM_STATES: Mapping[str, Family] = MappingProxyType(
    {"idle": Family.QUIET, "restarting": Family.ACTIVE, "offline": Family.BLOCKED}
)


def family_of(phase: WorkPhase | str) -> Family:
    """The family *phase* reads as."""
    return PHASES[WorkPhase(phase)].family


#: Where each of PersonalClaw's own search tools looks, in the words its line says it. Read by the
#: tool's name: its own runtime declares no kind for a call.
SEARCH_SCOPES: Mapping[str, str] = MappingProxyType(
    {
        "knowledge_search": "your knowledge",
        "memory_recall": "your memory",
        "grep": "the files",
        "glob": "the files",
        "tool_search": "your tools",
        "web_search": "the web",
    }
)


def classify_call(title: str, kind: str = "") -> tuple[WorkPhase, str]:
    """What a tool call shows its work doing, and its line: *title* is the call's own label (its
    card's title) and *kind* the kind its runtime declared for it, ``""`` when it declared none.

    An agent CLI declares a kind per call: a ``search`` is searching, under its own label, since it
    does not say where; a ``think`` is planning; any other kind is a tool, labelled as its card is.
    PersonalClaw's own runtime declares none, so its search tools are known by name and say where
    they look; any other of its calls is a tool."""
    if kind:
        if kind == "search":
            return WorkPhase.SEARCHING, title
        if kind == "think":
            return WorkPhase.PLANNING, "Planning"
        return WorkPhase.TOOL, title
    where = SEARCH_SCOPES.get(title)
    if where:
        return WorkPhase.SEARCHING, f"Searching {where}"
    return WorkPhase.TOOL, title


#: How a piece of work's ending reads, by how it ended: a Stop and a turn its agent ended after the
#: owner declined a step are done, and a failure and a turn the gateway cut short (a restart, a
#: shutdown) are an error, whose line gives the reason.
ENDINGS: Mapping[str, WorkPhase] = MappingProxyType(
    {
        "complete": WorkPhase.DONE,
        "stopped": WorkPhase.DONE,
        "declined": WorkPhase.DONE,
        "error": WorkPhase.ERROR,
        "interrupted": WorkPhase.ERROR,
    }
)


def done_in(seconds: float) -> str:
    """The line of work that finished after *seconds*."""
    return f"Done in {duration_words(seconds)}" if round(seconds) else "Done in under a second"


def _stopped(reason: str) -> str:
    return f"Stopped: {reason}" if reason else "Stopped"


# ── Every kind of work, and where it says its phases ─────────────────────────────────────────────

#: The kinds of work (``workref.WORK_KINDS``) that say their phases, each with the module that says
#: them. ``tests/test_every_kind_of_work_says_its_phase.py`` holds every kind to this table or to
#: :data:`NO_PHASE_OF_ITS_OWN`.
PHASE_PRODUCERS: Mapping[str, str] = MappingProxyType(
    {
        "chat": "personalclaw.dashboard.chat_phase",
        "subagent": "personalclaw.work_phases",
        "loop": "personalclaw.work_phases",
        "workflow": "personalclaw.work_phases",
    }
)

#: The kinds of work that say no phase of their own, each with why.
NO_PHASE_OF_ITS_OWN: Mapping[str, str] = MappingProxyType(
    {
        "trigger": (
            "A fire is over once it has started its work: the helper or the workflow run it "
            "started, which its history row names (ActionResult.work_id), says its own phases."
        ),
        "webhook": (
            "Its agent turn runs outside the chat runner, in a session nobody watches: the "
            "running list holds it while it runs (running_now), and nothing yet says its steps."
        ),
        "job": (
            "Nothing starts a background command yet: the change that runs one, owned by its "
            "chat, gives it its phases or its reason here."
        ),
    }
)


# ── A loop's phase, by its status ────────────────────────────────────────────────────────────────

#: What running work incident mode holds says: it waits to start again, for the switch, not for
#: her, and the line says what moves it again (#3813). A loop's views and a run's say why
#: (``loop.loop.held_reason``, ``incident_hold``).
HELD: tuple[WorkPhase, str] = (WorkPhase.QUEUED, "Held until incident mode is off")

#: The phase each loop status reads as (``loop.loop.LoopStatus``), with its line, and the two
#: statuses its views derive (``web/src/lib/loopStatus.ts``, ``effectiveLoopStatus``): ``held``, a
#: running loop incident mode holds, and ``ended_early``, a finished loop that stopped at one of its
#: ceilings rather than meeting its goal. Its plan waiting for her launch is her OK; stalled,
#: blocked and a question all wait on her answer; her own pause is quiet, as nothing runs.
LOOP_STATUS_PHASES: Mapping[str, tuple[WorkPhase, str]] = MappingProxyType(
    {
        "intake": (WorkPhase.THINKING, "Analyzing"),
        "planning": (WorkPhase.PLANNING, "Planning"),
        "review": (WorkPhase.APPROVAL, "Needs your OK: its plan"),
        "ready": (WorkPhase.QUEUED, "Waiting to start"),
        "running": (WorkPhase.THINKING, "Running"),
        "paused": (WorkPhase.IDLE, "Paused"),
        "stagnant": (WorkPhase.INPUT, "Stalled"),
        "blocked": (WorkPhase.INPUT, "Blocked"),
        "needs_input": (WorkPhase.INPUT, "Needs your answer"),
        "complete": (WorkPhase.DONE, "Done"),
        "failed": (WorkPhase.ERROR, "Stopped"),
        "stopped": (WorkPhase.DONE, "Stopped"),
        "held": HELD,
        "ended_early": (WorkPhase.ERROR, "Ended early"),
    }
)


def loop_phase(
    status: str, *, stop_reason: str = "", held: str = "", reason: str = ""
) -> tuple[WorkPhase, str]:
    """What a loop in *status* is doing, and its line. *held* is its views' sentence while incident
    mode holds it, *stop_reason* why it ended (``LoopStopReason``) and *reason* its error, which a
    failure says."""
    if status == "running" and held:
        status = "held"
    elif status == "complete" and stop_reason and stop_reason != "done":
        status = "ended_early"
    phase, line = LOOP_STATUS_PHASES[status]
    if status == "failed":
        line = _stopped(reason)
    return phase, line


# ── A workflow run's phase, by its status, its step and its gate ────────────────────────────────

#: The phase each workflow run status reads as (``workflows.models.RunStatus``), with its line. A
#: step it runs is a tool, named as its author named the step; a gate it waits at reads by its ask
#: (:func:`gate_phase`). A cancel is a stop, done; a decline is her own decision, done; a failure
#: and an escalation are errors.
RUN_STATUS_PHASES: Mapping[str, tuple[WorkPhase, str]] = MappingProxyType(
    {
        "draft": (WorkPhase.QUEUED, "Waiting to start"),
        "running": (WorkPhase.THINKING, "Running"),
        "paused": (WorkPhase.IDLE, "Paused"),
        "needs_input": (WorkPhase.INPUT, "Needs your answer"),
        "complete": (WorkPhase.DONE, "Done"),
        "failed": (WorkPhase.ERROR, "Stopped"),
        "cancelled": (WorkPhase.DONE, "Stopped"),
        "escalated": (WorkPhase.ERROR, "Stopped"),
        "declined": (WorkPhase.DONE, "Declined"),
    }
)


def run_phase(status: str, *, reason: str = "", held: str = "") -> tuple[WorkPhase, str]:
    """What a workflow run in *status* is doing, and its line: a failure says its *reason*, a
    decline its own sentence, and a running run incident mode holds (*held*, its views' sentence)
    says it is held."""
    if status == "running" and held:
        return HELD
    phase, line = RUN_STATUS_PHASES[status]
    if phase is WorkPhase.ERROR:
        line = _stopped(reason)
    elif status == "declined" and reason:
        line = reason
    return phase, line


#: The phase a gate's ask reads as, by its kind (``workflows.human_input.AskKind``): a yes or no is
#: her OK, any other answer is her input, and an event gate waits for something to happen, not for
#: her.
GATE_PHASES: Mapping[str, WorkPhase] = MappingProxyType(
    {
        "approval": WorkPhase.APPROVAL,
        "choice": WorkPhase.INPUT,
        "text": WorkPhase.INPUT,
        "form": WorkPhase.INPUT,
        "event": WorkPhase.QUEUED,
    }
)


def gate_phase(ask: Mapping[str, Any]) -> tuple[WorkPhase, str]:
    """What a run waiting at a gate with *ask* is doing, and its line. An ask of a kind there is not
    reads as an approval, as the engine renders it (``Ask.from_dict``)."""
    phase = GATE_PHASES.get(str(ask.get("kind") or ""), WorkPhase.APPROVAL)
    prompt = str(ask.get("prompt") or "")
    if phase is WorkPhase.APPROVAL:
        return phase, f"Needs your OK: {prompt or 'a step'}"
    if phase is WorkPhase.INPUT:
        return phase, f"Asks: {prompt or 'a question'}"
    return phase, prompt or "Waiting for its event"


# ── A helper's ending ────────────────────────────────────────────────────────────────────────────


def helper_ending(info: Any) -> tuple[WorkPhase, str]:
    """How the helper *info* (``subagent.SubagentInfo``) ended, and its line: a stop is done; her
    Deny of its start is done, in the decline's own words; a failure says its error."""
    error = str(getattr(info, "error", "") or "")
    if getattr(info, "cancelled", False) or getattr(info, "stopped_by_you", False):
        return WorkPhase.DONE, "Stopped"
    if getattr(info, "declined", False):
        return WorkPhase.DONE, error or "Declined"
    if error:
        return WorkPhase.ERROR, _stopped(error)
    return WorkPhase.DONE, done_in(float(getattr(info, "elapsed", 0.0) or 0.0))
