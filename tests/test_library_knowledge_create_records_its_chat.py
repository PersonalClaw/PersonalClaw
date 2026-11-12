"""A note an agent saves, and a decision it logs, record the chat they were made in, and in a
project's chat that project, private.

Both tools are driven by name through ``invoke()`` with the turn's session key and project bound,
as the native runtime binds them. The library's row then says the thing came from that chat, was
made in chats, and belongs to the project.
"""

from __future__ import annotations

import pytest
from library_shared import client, isolate, knowledge_store, metadata, project

CHAT = "dashboard:chat-garden"


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.knowledge as knowledge
    from personalclaw.agents.native import builtin_tools

    made = isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(knowledge, "_store", None)
    monkeypatch.setattr(builtin_tools, "_enrich_in_background", lambda item_id: None)
    from personalclaw.history import ConversationLog

    log = ConversationLog()
    log.append(CHAT, "user", "Plan the spring beds")
    log.set_title(CHAT, "Spring beds")
    return made


async def _in_chat(tool: str, args: dict, *, session_key: str, project_id: str = "") -> None:
    from personalclaw import mcp_core
    from personalclaw.agents.native.builtin_tools import (
        NativeBuiltinToolProvider,
        bind_tool_context,
        reset_tool_context,
    )

    key = mcp_core.set_current_session_key(session_key)
    turn = bind_tool_context(cwd=None, project_id=project_id)
    try:
        result = await NativeBuiltinToolProvider().invoke(tool, args)
    finally:
        reset_tool_context(turn)
        mcp_core.reset_current_session_key(key)
    assert result.success, result.error


def _id(title: str) -> str:
    row = knowledge_store().db.execute("SELECT id FROM items WHERE title = ?", (title,)).fetchone()
    assert row, title
    return str(row[0])


async def _row(home, item_id: str) -> dict:
    async with client(home) as c:
        body = await (await c.get("/api/library")).json()
    return {r["ref"]: r for r in body["items"]}[f"knowledge:{item_id}"]


@pytest.mark.asyncio
async def test_a_note_saved_in_a_project_chat_is_that_projects_and_names_the_chat(home):
    garden = project("Garden")
    await _in_chat(
        "knowledge_create",
        {"type": "note", "title": "Tomatoes go in the south bed", "content": "Full sun."},
        session_key=CHAT,
        project_id=garden,
    )
    await _in_chat(
        "knowledge_create",
        {"type": "note", "title": "Basil beside them", "content": "Companion."},
        session_key=CHAT,
    )
    filed, loose = _id("Tomatoes go in the south bed"), _id("Basil beside them")
    meta = metadata(filed)
    assert meta["origin"] == "chat"
    assert meta["session_key"] == CHAT
    assert meta["project_id"] == garden
    assert meta["sharing_policy"] == "private"
    assert {k: metadata(loose).get(k) for k in ("session_key", "project_id", "sharing_policy")} == {
        "session_key": CHAT,
        "project_id": None,
        "sharing_policy": None,
    }
    row = await _row(home, filed)
    assert row["from"] == {"ref": f"chat:{CHAT}", "title": "Spring beds"}
    assert row["made"] == "chats"
    assert row["project"] == {"id": garden, "name": "Garden"}
    assert (await _row(home, loose))["project"] is None


@pytest.mark.asyncio
async def test_a_decision_logged_in_a_project_chat_is_filed_under_it_and_names_the_chat(home):
    garden = project("Garden")
    await _in_chat(
        "log_decision",
        {
            "summary": "Raised beds this year",
            "expectation": "fewer weeds by June",
            "confidence": 0.7,
            "domain": "personal",
        },
        session_key=CHAT,
        project_id=garden,
    )
    item_id = _id("Raised beds this year")
    meta = metadata(item_id)
    assert meta["session_key"] == CHAT
    assert meta["project_id"] == garden
    assert meta["sharing_policy"] == "private"
    assert meta["decision"]["status"] == "pending"
    row = await _row(home, item_id)
    assert row["from"] == {"ref": f"chat:{CHAT}", "title": "Spring beds"}
    assert row["project"] == {"id": garden, "name": "Garden"}
