"""Every kind of work says what it is doing in the one phase contract, or is listed with why not.

Only a chat's turn said its phases (``dashboard/chat_phase.py``). A helper waiting for a slot, a
loop stalled on its owner and a workflow run parked at a gate said none, so a surface that shows
what runs had to read each kind's own status words again. Now a helper, a loop and a workflow run
say their phases through the same ``work_phase`` frame (``work_phases.py``), each
classified by ``personalclaw.work_phase``, and this census holds every kind of work
(``workref.WORK_KINDS``) to one of two lists: the kinds that say their phases, each with the module
that says them, and the kinds that say none of their own, each with why.

**The status tables are pinned both ways.** Every loop status (with the two its views derive), every
run status and every kind of ask a gate makes reads as one phase: a status with no phase reds this,
and so does a row for a status there is not. The phase each one reads as is pinned too, so a change
to one is a decision rather than drift.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from personalclaw import work_phase, workref
from personalclaw.loop.loop import ATTENTION_STATUSES, ENDED_STATUSES, LoopStatus
from personalclaw.work_phase import (
    GATE_PHASES,
    LOOP_STATUS_PHASES,
    NO_PHASE_OF_ITS_OWN,
    PHASE_PRODUCERS,
    RUN_STATUS_PHASES,
    Family,
    WorkPhase,
    family_of,
)
from personalclaw.workflows.human_input import AskKind
from personalclaw.workflows.models import ENDED_RUN_STATUSES, RunStatus

#: The two statuses a loop's views derive (``web/src/lib/loopStatus.ts``'s
#: ``effectiveLoopStatus``): a running loop incident mode holds, and a finished loop that stopped at
#: one of its ceilings rather than meeting its goal.
DERIVED_LOOP_STATUSES = {"held", "ended_early"}

#: The phase each loop status reads as.
LOOPS = {
    "intake": "thinking",
    "planning": "planning",
    "review": "approval",
    "ready": "queued",
    "running": "thinking",
    "paused": "idle",
    "stagnant": "input",
    "blocked": "input",
    "needs_input": "input",
    "complete": "done",
    "failed": "error",
    "stopped": "done",
    "held": "queued",
    "ended_early": "error",
}

#: The phase each workflow run status reads as.
RUNS = {
    "draft": "queued",
    "running": "thinking",
    "paused": "idle",
    "needs_input": "input",
    "complete": "done",
    "failed": "error",
    "cancelled": "done",
    "escalated": "error",
    "declined": "done",
}

#: The phase a gate's ask reads as, by its kind: a yes or no is the owner's OK, any other answer is
#: her input, and an event gate waits for something to happen, not for her.
GATES = {
    "approval": "approval",
    "choice": "input",
    "text": "input",
    "form": "input",
    "event": "queued",
}


def _unlisted(
    kinds: Iterable[str], producers: Mapping[str, str], reasons: Mapping[str, str]
) -> set[str]:
    """The kinds of work in *kinds* that neither say a phase nor say why not."""
    return set(kinds) - set(producers) - set(reasons)


def test_every_kind_of_work_says_its_phase_or_is_listed_with_why_not() -> None:
    assert workref.WORK_KINDS, "no kinds of work — vacuous"
    assert not _unlisted(workref.WORK_KINDS, PHASE_PRODUCERS, NO_PHASE_OF_ITS_OWN)
    assert not set(PHASE_PRODUCERS) & set(NO_PHASE_OF_ITS_OWN), "a kind is on both lists"
    listed = set(PHASE_PRODUCERS) | set(NO_PHASE_OF_ITS_OWN)
    assert listed == set(workref.WORK_KINDS), listed ^ set(workref.WORK_KINDS)
    for kind, why in NO_PHASE_OF_ITS_OWN.items():
        assert why.strip(), f"{kind} says no phase of its own and does not say why"
    assert {"chat", "subagent", "loop", "workflow"} <= set(PHASE_PRODUCERS)


def test_a_kind_of_work_with_no_row_is_caught() -> None:
    """The positive control: a planted kind on neither list is the one the census names."""
    planted = (*workref.WORK_KINDS, "job")
    assert _unlisted(planted, PHASE_PRODUCERS, NO_PHASE_OF_ITS_OWN) == {"job"}


def _said_by(module: Any, kind: str) -> list[dict[str, Any]]:
    """The ``work_phase`` payloads *module* says for one piece of work of *kind*, through the entry
    point its producer is fed by; none when it has no such entry point."""
    state = MagicMock()
    state._work_phases = {}
    state.next_stream_seq.return_value = 1
    one = SimpleNamespace(id="w1", done=False, status="ready", stop_reason="", error_message="")
    try:
        if kind == "chat":
            state.broadcast_ws("work_phase", module.idle_payload("chat-census-1"))
        elif kind == "subagent":
            module.helper_event(state, "subagent_spawn", one, {}, session="")
        elif kind == "loop":
            module.loop_status(state, one)
        elif kind == "workflow":
            module.run_event(state, "w1", "workflow_run_update", {"status": "running"})
    except AttributeError:
        return []
    return [c.args[1] for c in state.broadcast_ws.call_args_list if c.args[0] == "work_phase"]


def test_each_producer_says_the_phase_of_its_own_kind_of_work() -> None:
    """The module each kind is listed with says that kind's phase, named as that kind's work."""
    assert set(PHASE_PRODUCERS) == {"chat", "subagent", "loop", "workflow"}, "a producer unread"
    for kind, module in PHASE_PRODUCERS.items():
        said = _said_by(importlib.import_module(module), kind)
        assert said, f"{module} says no {kind} phase"
        assert {(p["kind"], workref.parse(p["work"]).kind) for p in said} == {(kind, kind)}


def test_a_producer_listed_for_work_it_does_not_say_is_caught() -> None:
    """The positive control: a module listed for a kind whose phase it does not say says none."""
    assert _said_by(importlib.import_module(PHASE_PRODUCERS["chat"]), "loop") == []


def test_every_loop_status_reads_as_one_phase() -> None:
    statuses = {s.value for s in LoopStatus}
    assert statuses, "LoopStatus is empty — import drift?"
    assert set(LOOP_STATUS_PHASES) == statuses | DERIVED_LOOP_STATUSES
    assert {s: p.value for s, (p, _line) in LOOP_STATUS_PHASES.items()} == LOOPS


def test_every_run_status_reads_as_one_phase() -> None:
    statuses = {s.value for s in RunStatus}
    assert statuses, "RunStatus is empty — import drift?"
    assert set(RUN_STATUS_PHASES) == statuses
    assert {s: p.value for s, (p, _line) in RUN_STATUS_PHASES.items()} == RUNS


def test_every_kind_of_ask_a_gate_makes_reads_as_one_phase() -> None:
    kinds = {k.value for k in AskKind}
    assert kinds, "AskKind is empty — import drift?"
    assert set(GATE_PHASES) == kinds
    assert {k: p.value for k, p in GATE_PHASES.items()} == GATES


def test_work_that_waits_on_her_reads_as_waiting_and_ended_work_as_over() -> None:
    """A loop waiting on its owner (every attention status but her own pause) is in the waiting
    family; a loop or a run that has ended reads as quiet or blocked, never as live work."""
    over = {Family.QUIET, Family.BLOCKED}
    waiting = {s.value for s in ATTENTION_STATUSES} - {LoopStatus.PAUSED.value}
    assert waiting, "no attention statuses — vacuous"
    for status in waiting:
        assert family_of(LOOP_STATUS_PHASES[status][0]) is Family.WAITING, status
    for status in ENDED_STATUSES:
        assert family_of(LOOP_STATUS_PHASES[status.value][0]) in over, status
    for run_status in ENDED_RUN_STATUSES:
        assert family_of(RUN_STATUS_PHASES[run_status.value][0]) in over, run_status
    assert family_of(RUN_STATUS_PHASES[RunStatus.NEEDS_INPUT.value][0]) is Family.WAITING


def test_a_loops_derived_statuses_are_read_from_its_record() -> None:
    """Held and ended early are derived as its views derive them: a running loop incident mode
    holds, and a finished loop whose stop reason names a ceiling. A failure says its reason."""
    held = "Held: incident mode is on, so this loop starts no new cycle. It carries on by itself."
    assert work_phase.loop_phase("running", held=held) == (
        WorkPhase.QUEUED,
        "Held until incident mode is off",
    )
    assert work_phase.loop_phase("running") == (WorkPhase.THINKING, "Running")
    assert work_phase.loop_phase("complete", stop_reason="cycle_budget") == (
        WorkPhase.ERROR,
        "Ended early",
    )
    assert work_phase.loop_phase("complete", stop_reason="done") == (WorkPhase.DONE, "Done")
    assert work_phase.loop_phase("failed", reason="the worker stopped answering") == (
        WorkPhase.ERROR,
        "Stopped: the worker stopped answering",
    )


def test_a_runs_ending_and_a_gates_ask_say_their_own_words() -> None:
    assert work_phase.run_phase("failed", reason="step 2 raised") == (
        WorkPhase.ERROR,
        "Stopped: step 2 raised",
    )
    assert work_phase.run_phase("declined", reason="You declined the deploy gate.") == (
        WorkPhase.DONE,
        "You declined the deploy gate.",
    )
    assert work_phase.gate_phase({"kind": "approval", "prompt": "Deploy to prod?"}) == (
        WorkPhase.APPROVAL,
        "Needs your OK: Deploy to prod?",
    )
    assert work_phase.gate_phase({"kind": "text", "prompt": "Which branch?"}) == (
        WorkPhase.INPUT,
        "Asks: Which branch?",
    )
    # An ask of a kind there is not reads as an approval, as the engine renders it (`Ask`).
    assert work_phase.gate_phase({"kind": "poll", "prompt": "Go?"})[0] is WorkPhase.APPROVAL
