"""The agent can search what the user's earlier chats said, and a summary of recent work does.

An on-call handoff written from a saved prompt read the error tracker, the notes and the code host,
and left out what only her chats held: the Tuesday p99 spike she had worked through in a chat, and
Thursday's outages. The chat list and the command palette search chats by what was said in them;
the agent had no tool that did, so the turn could not have found them.

Driven here the way the agent drives it: the real gateway asking for a sign-in, fixture chats in its
home, the memory tools' provider the native agent calls (bound to the chat it is called from), and a
turn of the native loop with a scripted model, handed the handoff prompt among a catalog large
enough that a turn carries only some schemas in full.

What holds:

* the tool finds the spike in the earlier chat and hands back its title, dates, the turns that say
  it and where to open it, and never a tool's output;
* an Incognito chat and a Temporary chat are never in what it finds, nor the chat it is asked from
  (with an older file of the same chat);
* from a Temporary chat, and from a subagent working for one, it searches nothing;
* in a conversation an app started, it finds only the conversations that app started;
* a turn asked to sum up recent work carries the tool's full schema, and its call is answered.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from personalclaw import mcp_core, session_restrictions
from personalclaw import session_search as ss
from personalclaw.history import ConversationLog
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from personalclaw.tool_providers.registry import create_memory_provider

#: Every auth shortcut a test process might inherit; each would admit a call before the internal
#: credential is looked at.
_AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_DEV_NO_AUTH",
    "PERSONALCLAW_SESSION_KEY",
)

#: The chat the agent is asked from.
HERE = "dashboard:chat-15-1790500005"
#: The earlier chat that holds the spike.
SPIKE = "dashboard_chat-11-1790000001"
SPIKE_ANSWER = (
    "p99 climbed from about 13:10, peaked near 380 ms at 14:05, about four times the baseline, "
    "and was back near 90 ms by 15:10. The deploy at 14:00 did not start it."
)
#: A tool's output in that chat, which says the words too and is never shown.
TOOL_OUTPUT = "p99 spike raw series: 88 91 290 380 371 265 172 125 103 90"
OUTAGES = "dashboard_chat-12-1790200002"
#: What a Temporary chat's call is answered with (`chat_recall.TEMPORARY`).
TEMPORARY = (
    "This is a Temporary chat: it starts blank and reads nothing from your other chats, so none "
    "were searched."
)

#: The handoff prompt a user keeps, as the chat expands it (invented wording).
HANDOFF = (
    "Write my on-call handoff for the incoming primary.\n"
    "1. Open error-tracker issues for the ingest services with more than 100 events in the last "
    "7 days, grouped by whether they are new this week.\n"
    "2. Anything I mitigated but did not fix, with the link to the PR or the flag I flipped.\n"
    "3. Upcoming risk: API changes, deploy freezes, peak days.\n"
    "Keep it to one screen. Put the thing most likely to page them first."
)


def _chat(
    home: Path,
    key: str,
    *turns: tuple[str, str, str],
    title: str = "",
    mode: str = "",
    tab: str = "",
    app: str = "",
    created: str = "2026-09-29T13:39:04+00:00",
) -> None:
    """A chat's transcript as the dashboard saves one: its metadata line, then a line per message
    (``(role, ts, text)``), dated by its last message."""
    meta: dict = {"_type": "metadata", "created_at": created, "last_consolidated": 0}
    for field, value in (("title", title), ("memory_mode", mode), ("tab_id", tab)):
        if value:
            meta[field] = value
    if app:
        meta["created_by_app"] = app
    lines = [meta, *({"role": role, "ts": ts, "content": text} for role, ts, text in turns)]
    path = home / "sessions" / f"{key}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    from datetime import datetime

    last = datetime.fromisoformat(turns[-1][1]).timestamp() if turns else 1790000000.0
    os.utime(path, (last, last))


def _history(home: Path) -> None:
    """Her chats: the spike on Tuesday, the outages on Thursday, an Incognito and a Temporary chat
    that mention the spike, and the chat she is in now, with an older file of it, that does too."""
    _chat(
        home,
        SPIKE,
        ("user", "2026-09-29T13:39:04+00:00", "What did p99 do around the 14:00 deploy?"),
        ("tool", "2026-09-29T13:39:30+00:00", TOOL_OUTPUT),
        (
            "assistant",
            "2026-09-29T13:40:10+00:00",
            "The p99 spike, from the chart: " + SPIKE_ANSWER,
        ),
        title="P99 latency spike around deploy",
    )
    _chat(
        home,
        OUTAGES,
        ("user", "2026-10-01T14:02:00+00:00", "Two carrier outages today, 09:40 and 16:20."),
        ("assistant", "2026-10-01T14:03:00+00:00", "Both outages were on the webhook path."),
        title="Carrier outages",
        created="2026-10-01T14:02:00+00:00",
    )
    _chat(
        home,
        "dashboard_chat-13-1790300003",
        (
            "user",
            "2026-09-30T10:00:00+00:00",
            "Private: my own notes on the p99 spike, kept quiet.",
        ),
        mode="incognito",
    )
    _chat(
        home,
        "dashboard_chat-14-1790400004",
        ("user", "2026-09-30T11:00:00+00:00", "Scratch: guesses about the p99 spike, kept quiet."),
        mode="temporary",
    )
    _chat(
        home,
        "dashboard_chat-15-1790500005",
        ("user", "2026-10-02T13:30:00+00:00", "Draft the handoff: lead with the p99 spike."),
        tab="tab-handoff",
        created="2026-10-02T13:30:00+00:00",
    )
    _chat(
        home,
        "dashboard_chat-16-1790500000",
        ("user", "2026-10-02T13:00:00+00:00", "Earlier part of this chat: the p99 spike, again."),
        tab="tab-handoff",
        created="2026-10-02T13:00:00+00:00",
    )


@dataclass
class _Gateway:
    home: Path
    port: int
    state: object


@contextlib.asynccontextmanager
async def _gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Gateway]:
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    # The user's zone, so a chat's day reads as she would say it.
    monkeypatch.setattr(
        "personalclaw.timezones.resolve_zone", lambda *a: ZoneInfo("America/Toronto")
    )
    ss.reset_for_tests()
    ss.INDEXER.reset()
    _history(home)

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    runner, state = await server_mod.start_dashboard(
        sessions=MagicMock(count=0), port=0, conversation_log=ConversationLog()
    )
    port = runner.addresses[0][1]
    monkeypatch.setenv("PERSONALCLAW_PORT", str(port))
    try:
        yield _Gateway(home=home, port=port, state=state)
    finally:
        await runner.cleanup()
        ss.INDEXER.reset()
        ss.reset_for_tests()


async def _search(arguments: dict, *, asked_from: str = HERE) -> tuple[bool, str]:
    """One call through the provider the native agent's memory tools come from, made in the chat
    *asked_from*, as the native loop binds it."""
    token = mcp_core.set_current_session_key(asked_from)
    try:
        result: ToolResult = await create_memory_provider().invoke("chat_search", arguments)
    finally:
        mcp_core.reset_current_session_key(token)
    return result.success, (result.output if result.success else result.error) or ""


@pytest.fixture(params=["read directly", "from the index"])
def indexed(request) -> bool:
    """Both ways a search answers: by reading the chats directly, while the index is still empty,
    and from the index once it holds them."""
    return request.param == "from the index"


@pytest.mark.asyncio
async def test_the_tool_finds_what_an_earlier_chat_said(tmp_path, monkeypatch, indexed):
    """🔴 Red on integration: no such tool ("Unknown tool: chat_search")."""
    async with _gateway(tmp_path, monkeypatch):
        if indexed:
            assert ss.probe().fts5, "this build's SQLite has the full-text index"
            ss.reindex_all(ConversationLog())
            ss.INDEXER.check()

        ok, text = await _search({"query": "p99 spike"})

    assert ok, text
    assert text.startswith('1 earlier chat says "p99 spike". This chat is not searched.'), text
    assert "Title: P99 latency spike around deploy" in text
    assert "Started Tuesday, 2026-09-29 09:39 EDT." in text
    assert "Last active Tuesday, 2026-09-29 09:40 EDT." in text
    assert "[Tue 2026-09-29 09:40 EDT] assistant: The p99 spike, from the chart: " in text
    assert SPIKE_ANSWER in text
    assert "Open it: #/chat/chat-11-1790000001?find=p99%20spike" in text
    # Quoted as what was said, not as a request made now.
    assert "<untrusted_content" in text and "source_id=dashboard_chat-11-1790000001" in text
    # A tool's output is not a turn.
    assert TOOL_OUTPUT not in text
    # Never an Incognito or a Temporary chat, nor the chat it is asked from, nor its older file.
    for unsaid in ("Private:", "Scratch:", "Draft the handoff", "Earlier part of this chat"):
        assert unsaid not in text, unsaid


@pytest.mark.asyncio
async def test_an_incognito_or_temporary_chat_is_never_found(tmp_path, monkeypatch, indexed):
    async with _gateway(tmp_path, monkeypatch):
        if indexed:
            ss.reindex_all(ConversationLog())
            ss.INDEXER.check()

        ok, text = await _search({"query": "kept quiet"})

    assert ok, text
    assert text == (
        'No earlier chat says "kept quiet". This chat is not searched. '
        "A chat is found by the words said in it: try fewer of them."
    )


@pytest.mark.asyncio
async def test_the_route_answers_with_the_chats_it_found(tmp_path, monkeypatch):
    """What the route serves beside the agent's text: each chat's dates, turns and address."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        secret = (gw.home / ".local_secret").read_text().strip()
        async with aiohttp.ClientSession() as http:
            resp = await http.get(
                f"http://127.0.0.1:{gw.port}/api/sessions/recall",
                params={"q": "outages", "limit": "5"},
                headers={"X-Internal-Secret": secret, "X-Session-Key": HERE},
            )
            body = await resp.json()

    assert resp.status == 200, body
    assert [chat["key"] for chat in body["chats"]] == [OUTAGES]
    chat = body["chats"][0]
    assert chat["title"] == "Carrier outages"
    assert chat["created"] == "2026-10-01T14:02:00+00:00"
    assert chat["route"] == "#/chat/chat-12-1790200002?find=outages"
    assert chat["link"] == "", "no dashboard address is configured, so there is no absolute link"
    assert [t["role"] for t in chat["turns"]] == ["user", "assistant"]
    assert chat["turns"][0]["at"] == "2026-10-01T14:02:00+00:00"
    # Of her six chats, two are searched: not the Incognito or Temporary one, nor the two files of
    # the chat the call is made from.
    assert body["complete"] is True and body["searched"] == {"chats": 2, "of": 2}


@pytest.mark.asyncio
async def test_a_temporary_chat_searches_nothing(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch):
        session_restrictions.mark_temporary(HERE)

        ok, text = await _search({"query": "p99 spike"})

    assert ok, text
    assert text == TEMPORARY
    assert SPIKE_ANSWER not in text


@pytest.mark.asyncio
async def test_a_subagent_working_for_a_temporary_chat_searches_nothing(tmp_path, monkeypatch):
    """A subagent's calls name its own key; the chat it works for is the one that starts blank."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        session_restrictions.mark_temporary(HERE)
        info = SimpleNamespace(parent_session_key=HERE, app="")
        monkeypatch.setattr(
            gw.state, "subagents", SimpleNamespace(get=lambda agent_id: info, count=0)
        )

        ok, text = await _search({"query": "p99 spike"}, asked_from="subagent:a1b2c3d4")

    assert ok, text
    assert text == TEMPORARY


@pytest.mark.asyncio
async def test_a_subagent_does_not_find_the_chat_it_works_for(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        info = SimpleNamespace(parent_session_key=HERE, app="")
        monkeypatch.setattr(
            gw.state, "subagents", SimpleNamespace(get=lambda agent_id: info, count=0)
        )

        ok, text = await _search({"query": "p99 spike"}, asked_from="subagent:a1b2c3d4")

    assert ok, text
    assert SPIKE_ANSWER in text
    assert "Draft the handoff" not in text and "Earlier part of this chat" not in text


@pytest.mark.asyncio
async def test_a_conversation_an_app_started_finds_only_that_apps_conversations(
    tmp_path, monkeypatch
):
    async with _gateway(tmp_path, monkeypatch) as gw:
        _chat(
            gw.home,
            "dashboard_chat-20-1790600000",
            ("user", "2026-10-02T15:00:00+00:00", "Track the p99 spike for the status page."),
            app="status-notes",
        )
        _chat(
            gw.home,
            "dashboard_chat-21-1790600001",
            ("user", "2026-10-02T15:10:00+00:00", "Status page: the p99 spike is resolved."),
            app="status-notes",
        )

        ok, text = await _search({"query": "p99 spike"}, asked_from="dashboard:chat-21-1790600001")

    assert ok, text
    assert "Track the p99 spike for the status page." in text
    assert SPIKE_ANSWER not in text, "an app's conversation never reaches one of yours"


@pytest.mark.asyncio
async def test_an_absolute_link_is_given_when_the_dashboard_address_is_known(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch) as gw:
        (gw.home / "config.json").write_text(
            json.dumps({"dashboard": {"url": "http://127.0.0.1:19870"}}), encoding="utf-8"
        )

        ok, text = await _search({"query": "outages"})

    assert ok, text
    assert "Open it: http://127.0.0.1:19870/#/chat/chat-12-1790200002?find=outages" in text


@pytest.mark.asyncio
async def test_a_credential_said_in_an_earlier_chat_is_masked(tmp_path, monkeypatch):
    """Masked whole before the turn is cut to its window, so no piece of it is handed on."""
    key = "fake-replay-test-" + "q7x" * 10
    filler = "the replay notes go on and on " * 25
    async with _gateway(tmp_path, monkeypatch) as gw:
        _chat(
            gw.home,
            "dashboard_chat-30-1790700000",
            (
                "assistant",
                "2026-10-02T16:00:00+00:00",
                f"{filler}Use the staging key api_key={key} for the replay. {filler}",
            ),
            title="Replay setup",
        )

        ok, text = await _search({"query": "staging key"})

    assert ok, text
    assert "Title: Replay setup" in text
    assert "Use the staging key [REDACTED: credential] for the replay." in text
    assert not any(key[i : i + 12] in text for i in range(len(key) - 11)), text


@pytest.mark.asyncio
async def test_a_query_too_short_to_search_is_refused_in_words(tmp_path, monkeypatch):
    async with _gateway(tmp_path, monkeypatch):
        ok, text = await _search({"query": "p"})

    assert not ok
    assert text == "Give at least two characters to search your chats for."


def test_the_offered_limit_is_the_routes_own():
    from personalclaw import chat_recall
    from personalclaw.validation import CHAT_SEARCH_SCHEMA

    limit = next(spec for spec in CHAT_SEARCH_SCHEMA.fields if spec.name == "limit")
    assert (limit.min_val, limit.max_val) == (1, chat_recall.MAX_CHATS)


# ── a turn asked to sum up recent work ───────────────────────────────────────────────────────


class _Library(ToolProvider):
    """A long tail of other tools, so the turn's catalog is large enough to be reduced."""

    @property
    def name(self) -> str:
        return "library"

    @property
    def display_name(self) -> str:
        return "Library"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=f"library_tool_{n}",
                description=f"Does library thing {n}.",
                parameters={"type": "object", "properties": {"x": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            )
            for n in range(80)
        ]

    async def invoke(self, tool_name: str, arguments: dict) -> ToolResult:
        return ToolResult(success=True, output="")


class _Scripted:
    """A model that calls ``chat_search`` once, then answers; it keeps the tools each call offered
    and the messages it was handed."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.tools: list[list[dict]] = []
        self.messages: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        from personalclaw.llm.events import (
            EVENT_COMPLETE,
            EVENT_TEXT_CHUNK,
            EVENT_TOOL_CALL,
            AgentEvent,
        )

        self.tools.append(list(tools or []))
        self.messages.append(list(messages))
        if len(self.tools) == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c1",
                title="chat_search",
                tool_input=json.dumps({"query": "p99 spike"}),
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Handoff written.")
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)


@pytest.mark.asyncio
async def test_a_turn_that_sums_up_recent_work_carries_chat_search_and_its_call_is_answered(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: the turn carries no tool that searches chats, and the call fails."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    async with _gateway(tmp_path, monkeypatch):
        model = _Scripted()
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
            model_provider=model,
            tool_providers=[create_memory_provider(), _Library()],
            session_key=HERE,
        )
        await runtime.start()
        [event async for event in runtime.stream(HANDOFF)]

    offered = [tool["function"]["name"] for tool in model.tools[0]]
    assert len(offered) < 80, "the catalog is large enough that the turn carries only some schemas"
    assert "chat_search" in offered, offered
    handed = json.dumps(model.messages[1])
    assert "P99 latency spike around deploy" in handed
    assert "peaked near 380 ms at 14:05" in handed
