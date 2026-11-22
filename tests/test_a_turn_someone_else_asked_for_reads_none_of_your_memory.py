"""A turn someone other than the owner asked for reads none of her memory.

A colleague's message in a shared channel thread no longer ran on her standing grants and could not
search her chats, but her memory still reached it: the turn was built with her saved facts, lessons
and standing instructions, ``memory_recall`` answered it, and the agent's file tools read her
memory documents. So a colleague could ask the agent what it remembers of her and be told.

Now work someone else asked for reads none of it, by any path: the turn's context carries none of
her memory, the memory tools answer why there is none, and her memory documents are withheld from
its file tools (``memory_reads``). Her own message in the same thread is built and answered as
before.

Driven as the turn drives it: the real gateway over a scratch home, the door a channel's message
crosses, the real turn engine on a native runtime whose scripted model calls the tools, and those
tools' calls back to the gateway's routes over its own API.
"""

from __future__ import annotations

import json

import pytest
from test_a_standing_grant_answers_only_the_owners_own_asks import _a_shared_channel  # noqa: F401
from test_a_standing_grant_answers_only_the_owners_own_asks import _gateway
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (
    COLLEAGUE,
    OWNER,
    PROVIDER,
)

from personalclaw import memory_reads, session_restrictions
from personalclaw.tool_providers.registry import create_memory_provider

#: A fact of hers, as a saved fact and in her memory document.
FACT = "Mira is interviewing with another company next week"
#: What a memory read made for his turn is answered with, as the agent is told it.
REFUSED = (
    "Nothing was read from the owner's memory: Jonas (U0JONASCOL) on teamchat asked for this, and "
    "nothing says they are the owner, whose saved facts, lessons and earlier conversations are "
    "read only for their own requests."
)


def _her_memory(gw) -> str:
    """Her fact, saved in her memory's record store and written in her preferences.md, which the
    turn's context is built with; the document's path, as the agent's file tools take it."""
    from personalclaw.memory import memory_folders
    from personalclaw.memory_service import service_for
    from personalclaw.vector_memory import VectorMemoryStore

    memory = gw.state.context_builder.memory
    if getattr(memory, "vector_store", None) is None:
        # The record store a gateway's memory holds (`handlers._shared._get_memory`).
        store = VectorMemoryStore(db_path=gw.home / "memory.db")
        store.init()
        memory.vector_store = store
    saved = service_for(memory).set_semantic("user.job_search", FACT, 1.0, "user_explicit")
    assert saved is None, saved
    folder = memory_folders()[0]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "preferences.md").write_text(f"# Preferences\n\n- {FACT}\n", encoding="utf-8")
    return str(folder / "preferences.md")


def _handed(gw) -> str:
    """Everything the model was handed for the turn's first step: the context it was built with."""
    return json.dumps(gw.agent.handed[0])


@pytest.mark.asyncio
async def test_a_colleagues_turn_is_built_with_none_of_her_saved_facts(tmp_path, monkeypatch):
    """🔴 Red before: his turn's context carried her saved fact."""
    async with _gateway(tmp_path, monkeypatch, tools=(create_memory_provider(),)) as gw:
        _her_memory(gw)
        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "What do you know about Mira?")
        handed = _handed(gw)
    assert FACT not in handed


@pytest.mark.asyncio
async def test_her_own_turn_in_the_thread_is_built_with_them(tmp_path, monkeypatch):
    """The control: her own first message, through the same door into the same thread."""
    async with _gateway(tmp_path, monkeypatch, tools=(create_memory_provider(),)) as gw:
        _her_memory(gw)
        gw.agent.calls.append([])
        await gw.say(OWNER, "What's on my plate?")
        handed = _handed(gw)
    assert FACT in handed


@pytest.mark.asyncio
async def test_a_colleagues_memory_recall_is_refused_and_told_why(tmp_path, monkeypatch):
    """🔴 Red before: ``memory_recall`` answered his turn from her memory."""
    async with _gateway(tmp_path, monkeypatch, tools=(create_memory_provider(),)) as gw:
        _her_memory(gw)
        gw.agent.calls.append(("memory_recall", {"query": "interviewing"}))
        await gw.say(COLLEAGUE, "Is Mira looking for a new job?")
        told = gw.agent.told()
    assert told == REFUSED, told


@pytest.mark.asyncio
async def test_her_own_memory_recall_in_the_thread_finds_it(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch, tools=(create_memory_provider(),)) as gw:
        _her_memory(gw)
        gw.agent.calls.append(("memory_recall", {"query": "interviewing"}))
        await gw.say(OWNER, "What did I say about interviewing?")
        told = gw.agent.told()
    assert FACT in told, told


@pytest.mark.asyncio
async def test_a_colleagues_turn_reads_none_of_her_memory_documents(tmp_path, monkeypatch):
    """🔴 Red before: the agent's file tools read her preferences.md for his turn."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        path = _her_memory(gw)
        gw.agent.calls.append(("read_file", {"path": path}))
        await gw.say(COLLEAGUE, "Read Mira's preferences file.")
        told = gw.agent.told()
    assert FACT not in told, told


@pytest.mark.asyncio
async def test_her_own_turn_reads_her_memory_document(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        path = _her_memory(gw)
        gw.agent.calls.append(("read_file", {"path": path}))
        await gw.say(OWNER, "Read my preferences file.")
        told = gw.agent.told()
    assert FACT in told, told


def _jonas() -> dict[str, str]:
    from personalclaw.turn_source import arrived_on

    return arrived_on("1790800000.000100", COLLEAGUE, PROVIDER)


def test_lasting_work_he_asked_for_reads_none_of_it(monkeypatch):
    """🔴 Red before: a loop's cycle or a run's step he asked for read her memory once his turn
    was over, since only its record says who asked."""
    from personalclaw import lasting_work

    monkeypatch.setattr(
        lasting_work, "asker_of", lambda key: _jonas() if key == "loop:l1:worker" else None
    )
    assert memory_reads._asked_refusal_of(["loop:l1:worker"]) == REFUSED
    assert memory_reads._asked_refusal_of(["loop:l2:worker"]) == ""


def test_the_running_turns_mark_says_whose_it_is():
    """The newest mark on a session decides: his turn's refuses, hers (``{}``) reads."""
    key = "dashboard:chat-31-1790900031"
    his = session_restrictions.mark_asked_by(key, _jonas())
    try:
        assert memory_reads._asked_refusal_of([key]) == REFUSED
        hers = session_restrictions.mark_asked_by(key, {})
        try:
            assert memory_reads._asked_refusal_of([key]) == ""
        finally:
            session_restrictions.unmark_asked_by(key, hers)
    finally:
        session_restrictions.unmark_asked_by(key, his)


def test_an_agent_clis_tool_server_asks_the_gateway_whose_turn_it_is(monkeypatch):
    """🔴 Red before: an agent CLI's tool server holds no turn, so the check its file tools and
    the sandbox ask said nothing of who asked. It asks the gateway, as a write does."""
    from personalclaw import mcp_core

    monkeypatch.setattr(mcp_core, "serves_an_agent_cli", lambda: True)
    monkeypatch.setattr(mcp_core, "asker_from_the_gateway", _jonas)
    assert memory_reads.memory_read_refusal() == REFUSED
    monkeypatch.setattr(mcp_core, "asker_from_the_gateway", lambda: {})
    assert memory_reads.memory_read_refusal() == ""
