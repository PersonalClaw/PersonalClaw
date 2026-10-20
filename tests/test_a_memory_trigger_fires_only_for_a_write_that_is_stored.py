"""A memory data-event trigger fires for a write that is stored, once, and for nothing else.

The memory store told the data-event triggers about a fact before it stored it: the event was
emitted, then the row was written, so a write the store refused a moment later (in a Temporary
chat's work, or an app's not given your memory) had already fired every trigger watching its key.
A write the store turned away on its own rules fired them too: one that would have replaced a
fact you set, and one below the confidence a fact needs. Each is still recorded in the memory's
history, as a write that did not happen; only a stored change reaches a trigger now.

Driven as a user's trigger runs: created through the Triggers API, matched by the gateway's own
event router, fired through the one store dispatch, and seen as the notification its action
raises.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from test_event_triggers_fire import (  # noqa: F401 - the fixtures are used by name
    _create_through_the_api,
    _history,
    _memory,
    _orch,
    home,
    state,
)

from personalclaw import memory_writes

#: The notification the trigger's action raises, for each key it fires on.
FIRED = "Acme changed: {}"

#: Work whose writes the memory store refuses: a Temporary chat's, and an app's that was not given
#: your memory (here, one that is not installed at all).
REFUSING: dict[str, Callable[[], AbstractContextManager[None]]] = {
    "temporary chat": lambda: memory_writes.derived_from(
        "dashboard:chat-1-1790800000", memory_mode="temporary"
    ),
    "app not given memory": lambda: memory_writes.derived_from(
        "dashboard:chat-2-1790800100", app="allotment-planner"
    ),
}


def _store(where):
    mem = _memory(where)
    mem._graph_enabled = False  # one write, one event: no entity links riding along
    return mem


def _fired(seen) -> list[str]:
    return [n["title"] for n in seen.sent if n["title"].startswith("Acme changed")]


@pytest.mark.parametrize("work", sorted(REFUSING))
def test_a_refused_write_fires_no_trigger_and_a_stored_one_fires_once(
    home, state, work  # noqa: F811 - the imported fixtures
):
    """🔴 Red on integration: the refused write fired the trigger before it was refused."""

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            trigger_id = (await _create_through_the_api(debounce_secs=0))["trigger"]["raw_id"]
            mem = _store(home)
            with REFUSING[work]():
                with pytest.raises(memory_writes.MemoryWriteRefused):
                    mem.set_semantic("project.acme.budget", "Raised to forty", 1.0, "user_explicit")
            await orch._event_router.settle()
            after_the_refusal = _fired(state)
            assert mem.set_semantic("project.acme.deadline", "Friday", 1.0, "user_explicit") is None
            await orch._event_router.settle()
            return after_the_refusal, await _history(trigger_id)
        finally:
            orch._stop_event_triggers()

    after_the_refusal, history = asyncio.run(scenario())

    assert after_the_refusal == []
    assert _fired(state) == [FIRED.format("project.acme.deadline")]
    assert len(history["runs"]) == 1


def test_a_write_the_store_turns_away_fires_no_trigger(
    home, state  # noqa: F811 - the imported fixtures
):
    """🔴 Red on integration: a write conflict resolution skipped, and one below the confidence
    a fact needs, each fired the trigger as though the fact had changed."""

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            trigger_id = (await _create_through_the_api(debounce_secs=0))["trigger"]["raw_id"]
            mem = _store(home)
            assert mem.set_semantic("project.acme.deadline", "Friday", 1.0, "user_explicit") is None
            await orch._event_router.settle()
            skipped = mem.set_semantic("project.acme.deadline", "Monday", 0.95, "consolidation")
            too_unsure = mem.set_semantic(
                "project.acme.owner", "the garden club", 0.1, "consolidation"
            )
            await orch._event_router.settle()
            events = [
                (e["event_type"], e["memory_key"])
                for e in mem.get_events(limit=10)
                if e["memory_key"].startswith("project.acme.")
            ]
            return skipped, too_unsure, events, await _history(trigger_id)
        finally:
            orch._stop_event_triggers()

    skipped, too_unsure, events, history = asyncio.run(scenario())

    assert skipped is not None and too_unsure is not None, "neither write was stored"
    assert _fired(state) == [FIRED.format("project.acme.deadline")], "only the stored write"
    assert len(history["runs"]) == 1, "nothing else reached the trigger, not even to be skipped"
    # Both are still in the memory's history, as writes that did not happen.
    assert ("conflict_skip", "project.acme.deadline") in events
    assert ("low_confidence", "project.acme.owner") in events
