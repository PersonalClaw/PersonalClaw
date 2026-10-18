"""The phases of a piece of work: what an agent is doing, said the same way on every surface.

A chat turn, a loop, a workflow run and a helper are each, at any moment, in one phase of the
closed set below. Each phase has one meaning, one tone, one of four shared visual families, and the
copy row its one line is written from. The gateway decides the phase from what it measures
(``work_phase`` frames, a chat's ``run_state``), and every surface reads the phase's tone, family
and words from ``web/src/lib/workPhase.ts``, which ``tests/test_work_phase_vocabulary.py`` pins to
this table in both directions — two tables of one vocabulary drift, as the loop status's did.

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
