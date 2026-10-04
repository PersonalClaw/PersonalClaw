"""A turn someone other than the owner asked for searches none of her other chats.

The agent can search what her earlier chats said (``chat_search``), and so can a client she let in
through the inbound door (``sessions_search``). In a shared channel thread, her colleague's turn
asked the agent to search them, and the agent posted her private chats' words in the thread.

Now a search of her chats made for work someone else asked for (a colleague's turn, a
correspondent's, a program's, and the work such a turn starts) searches nothing, and says why in a
sentence the agent can pass on. What she asks for herself is answered as before, in the same
thread and anywhere else.

Driven as the turn drives it: the real gateway over a scratch home holding a private chat of hers,
the door a channel's message crosses, the real turn engine on a native runtime whose model calls
``chat_search``, and that tool's call back to the gateway's route over its own API.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from test_a_standing_grant_answers_only_the_owners_own_asks import _a_shared_channel  # noqa: F401
from test_a_standing_grant_answers_only_the_owners_own_asks import _gateway
from test_a_turn_someone_else_started_changes_no_memory_on_its_own import (
    COLLEAGUE,
    OWNER,
    PROVIDER,
    _share_the_channel,
)

from personalclaw import memory_writes
from personalclaw import session_search as ss
from personalclaw.tool_providers.registry import create_memory_provider
from personalclaw.turn_source import arrived_on

#: Her private chat, as the dashboard saves one, and what was said in it.
PRIVATE = "dashboard_chat-21-1790900001"
SAID = "The board signed off on the reorg: the platform team moves under Priya in November."
#: What a search made for his turn is answered with, as the agent is told it.
REFUSED = (
    "None of the owner's other chats were searched: Jonas (U0JONASCOL) on teamchat asked for "
    "this, and nothing says they are the owner, whose chats are searched only for their own "
    "requests."
)


def _her_private_chat(home: Path) -> None:
    """A chat of hers in the dashboard, saved as the dashboard saves one."""
    turns = [
        ("user", "2026-09-30T08:10:00+00:00", "Where did the reorg land in the end?"),
        ("assistant", "2026-09-30T08:11:00+00:00", SAID),
    ]
    meta = {"_type": "metadata", "created_at": turns[0][1], "title": "Reorg, before the news"}
    lines = [meta, *({"role": r, "ts": ts, "content": text} for r, ts, text in turns)]
    path = home / "sessions" / f"{PRIVATE}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    last = datetime.fromisoformat(turns[-1][1]).timestamp()
    os.utime(path, (last, last))


@pytest.fixture(autouse=True)
def _a_fresh_index():
    ss.reset_for_tests()
    ss.INDEXER.reset()
    yield
    ss.INDEXER.reset()
    ss.reset_for_tests()


@pytest.mark.asyncio
async def test_a_colleagues_turn_that_searches_her_chats_is_refused_and_told_why(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: his turn was handed her private chat's words."""
    async with _gateway(tmp_path, monkeypatch, tools=(create_memory_provider(),)) as gw:
        _her_private_chat(gw.home)
        gw.agent.calls.append(("chat_search", {"query": "reorg"}))
        await gw.say(COLLEAGUE, "Search Mira's chats for anything about the reorg.")
        told = gw.agent.told()

    assert told == REFUSED, told
    assert "Priya" not in told


@pytest.mark.asyncio
async def test_her_own_turn_in_the_same_thread_finds_her_chat(tmp_path, monkeypatch):
    """The control: her own message, through the same door into the same chat."""
    async with _gateway(tmp_path, monkeypatch, tools=(create_memory_provider(),)) as gw:
        _her_private_chat(gw.home)
        gw.agent.calls.append([])
        await gw.say(COLLEAGUE, "Morning, all.")
        gw.agent.calls.append(("chat_search", {"query": "reorg"}))
        await gw.say(OWNER, "Find what I said about the reorg.")
        told = gw.agent.told()

    assert told.startswith('1 earlier chat says "reorg".'), told
    assert SAID in told


def _colleague() -> dict[str, str]:
    return arrived_on("1790800000.000100", COLLEAGUE, PROVIDER)


@pytest.mark.asyncio
async def test_the_inbound_search_made_for_his_turn_is_refused(tmp_path, monkeypatch):
    """🔴 Red on integration: the inbound door's search answered work he asked for. A call it
    serves for nobody's turn (a client she let in, on its own) is hers, and is answered."""
    from personalclaw.history import ConversationLog
    from personalclaw.inbound import tools as inbound

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _share_the_channel()
    ConversationLog().append("chat-21-1790900001", "user", SAID)

    with memory_writes.derived_from("dashboard:chat-22-1790900002"):
        memory_writes.asked_for(_colleague())
        theirs = await inbound.call_tool("sessions_search", {"query": "reorg"}, None)
    hers = await inbound.call_tool("sessions_search", {"query": "reorg"}, None)

    their_text = theirs["content"][0]["text"]
    assert REFUSED in their_text and "Priya" not in their_text, their_text
    assert "Priya" in hers["content"][0]["text"]
