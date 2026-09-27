"""A room member's turn writes a usage row, and every row names the provider entry that answered.

Two gaps #3709 left, measured on main:

* ``rooms/turn.py::run_member_turn`` wrote no usage row at all. ``ModelCallGuard`` metered the
  member's budget, but ``usage/turns.jsonl`` never saw a room, so Settings → Usage counted none of
  its spend.
* The ledger's ``provider`` column held the runtime kind the caller passed (``native``) instead of
  the provider entry ``TurnUsage`` documents, so every native turn read ``native`` and the routing
  fold built refs like ``native:gpt-4o`` that name no entry.

The fakes are #3709's: scripted models behind the real registry, the real native runtime, the real
``run_chat`` and the real ``run_member_turn``. The member's own model fails and the next answers,
so a row priced or attributed by the wrong one cannot pass.
"""

from __future__ import annotations

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent
from tests.test_usage_and_rooms_follow_the_model_that_answered import (  # noqa: F401 — fixtures
    DOWN,
    DOWN_REF,
    OVERLOADED,
    UP,
    UP_REF,
    _chat_turn,
    _MemberSessions,
    _price,
    _runtime,
    room_config,
    world,
)

pytestmark = pytest.mark.asyncio


def _rows() -> list[dict]:
    from personalclaw.usage_ledger import _iter_rows

    return _iter_rows()


async def test_a_room_member_turn_writes_one_row_priced_by_the_model_that_answered(
    world, room_config  # noqa: F811 - the imported fixtures, by name
):
    """🔴 Red on main: the ledger stayed empty after a member's turn."""
    from personalclaw.rooms import store, turn

    world.active["chat"] = [UP_REF, DOWN_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    room = store.create_room("Counted")
    store.add_member(room.id, "critic")

    await turn.run_member_turn(_MemberSessions(), room.id, "critic")

    rows = _rows()
    assert len(rows) == 1, f"one member turn, one row: {rows}"
    (row,) = rows
    assert (row["source"], row["session_key"], row["agent"]) == (
        "room",
        turn.session_key(room.id, "critic"),
        "critic",
    )
    assert (row["provider"], row["model"]) == (UP, "gpt-4o"), "the entry and model that answered"
    assert (row["cost_usd"], row["priced"]) == (_price("gpt-4o"), True)


async def test_each_member_of_a_round_writes_its_own_row(
    world, room_config  # noqa: F811 - the imported fixtures, by name
):
    from personalclaw.config.loader import AgentProfile
    from personalclaw.rooms import store, turn

    room_config.agents["scribe"] = AgentProfile(model=UP_REF)
    world.active["chat"] = [UP_REF]
    room = store.create_room("Two voices")
    store.add_member(room.id, "critic")
    store.add_member(room.id, "scribe")
    world.failures[DOWN] = RuntimeError(OVERLOADED)

    sessions = _MemberSessions()
    await turn.run_member_turn(sessions, room.id, "critic")
    await turn.run_member_turn(sessions, room.id, "scribe")

    assert sorted((r["agent"], r["session_key"]) for r in _rows()) == [
        ("critic", turn.session_key(room.id, "critic")),
        ("scribe", turn.session_key(room.id, "scribe")),
    ]


async def test_a_chat_turn_names_the_provider_entry_that_answered(
    world, tmp_path  # noqa: F811 - the imported fixture, by name
):
    """🔴 Red on main: the row said ``native``, the runtime, where the entry belongs."""
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime(session_key="dashboard:chat-priced", pick=DOWN_REF)

    await _chat_turn(rt, tmp_path, pick=DOWN_REF)

    (row,) = _rows()
    assert (row["provider"], row["model"]) == (UP, "gpt-4o")


async def test_the_seam_names_the_entry_and_an_acp_turn_keeps_its_runtime():
    """The one seam every write site shares: a served ref wins, and an event that names none (an
    ACP agent CLI) keeps what its caller passed."""
    from personalclaw.usage_ledger import record_from_event

    served = LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=1)
    served.served_model_ref = "local-llm:gpt-oss:20b"
    record_from_event(served, source="subagent", provider="acp", model="")
    unnamed = LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=1)
    record_from_event(unnamed, source="chat", provider="acp:claude-code", model="claude-sonnet-4.5")

    assert [(r["provider"], r["model"]) for r in _rows()] == [
        ("local-llm", "gpt-oss:20b"),
        ("acp:claude-code", "claude-sonnet-4.5"),
    ]


async def test_settings_usage_reads_a_room_as_interactive_spend_by_its_provider(
    world, room_config  # noqa: F811 - the imported fixtures, by name
):
    """What the two tables and the fold Settings → Usage reads say about one room turn."""
    from personalclaw.rooms import store, turn
    from personalclaw.routing.usage import purpose_for_source
    from personalclaw.usage_ledger import rollup

    world.active["chat"] = [UP_REF, DOWN_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    room = store.create_room("On the page")
    store.add_member(room.id, "critic")
    await turn.run_member_turn(_MemberSessions(), room.id, "critic")

    assert [(r["source"], r["turns"]) for r in rollup(group_by="source")] == [("room", 1)]
    assert [(r["provider"], r["turns"]) for r in rollup(group_by="provider")] == [(UP, 1)]
    # Not an app named "room": a room is a conversation the human reads, like a chat.
    assert purpose_for_source("room") == ("interactive", "")
