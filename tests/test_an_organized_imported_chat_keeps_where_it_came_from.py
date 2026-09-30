"""A chat brought over from another tool keeps where it came from, however you organise it.

The importer writes each conversation with ``imported_from`` on its metadata line (the tool and
the session it came from), and setup reads that line to tell "already imported" from "someone
else's conversation with this id" (``onboarding_import.writers._plan_conversation``). Pinning,
filing or archiving the chat saves it again, and the save rebuilds the metadata line from the
chat as it is held — which carried no ``imported_from``, so the key was dropped and running setup
again called the chat a conflict. The save keeps it, as it keeps the other facts written once
(``created_at``, ``app``).
"""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_folder_app, _make_state

import personalclaw.config.loader as loader
from personalclaw.history import import_conversation, session_path
from personalclaw.onboarding_import.model import ImportCategory, ImportItem, ItemState
from personalclaw.onboarding_import.writers import _plan_conversation, conversation_key

SOURCE = "claude_code"
TARGET = "83c14d7c-1d87-46d5-ae48-c8a43655fda7"
ORIGIN = {"source": SOURCE, "session": TARGET, "cwd": "/home/user/trips"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _item() -> ImportItem:
    return ImportItem(source=SOURCE, category=ImportCategory.CONVERSATIONS, key=TARGET, name=TARGET)


def _import() -> str:
    """What `writers._write_conversation` writes for this Claude Code session. Returns the chat's
    name as the dashboard addresses it."""
    key = conversation_key(_item())
    import_conversation(
        key,
        metadata={
            "created_at": "2026-07-10T21:38:00Z",
            "title": "Plan Oct 23 to Nov 1",
            "last_consolidated": 2,
            "imported_from": dict(ORIGIN),
        },
        messages=[
            {"role": "user", "content": "Plan Oct 23 to Nov 1", "ts": "", "cls": "msg msg-u"},
            {"role": "assistant", "content": "Here is a plan.", "ts": "", "cls": "msg msg-a"},
        ],
    )
    return key.removeprefix("dashboard_")


def _meta(name: str) -> dict:
    with session_path(f"dashboard_{name}").open(encoding="utf-8") as handle:
        return json.loads(handle.readline())


@pytest.mark.asyncio
async def test_pinning_filing_and_archiving_an_imported_chat_keep_its_origin(home) -> None:
    name = _import()
    assert _plan_conversation(_item()).state is ItemState.EXISTING, "precondition"
    state = _make_state(home / "sessions")
    app = _make_folder_app(state)
    from personalclaw.dashboard.session_bulk import api_chat_session_lifecycle

    app.router.add_patch("/api/chat/sessions/{session}/lifecycle", api_chat_session_lifecycle)

    async with TestClient(TestServer(app)) as client:
        pinned = await client.patch(f"/api/chat/sessions/{name}/pin", json={"pinned": True})
        assert pinned.status == 200, await pinned.text()
        assert _meta(name)["imported_from"] == ORIGIN

        folder = await client.post("/api/chat/folders", json={"name": "Trips"})
        assert folder.status == 201, await folder.text()
        folder_id = (await folder.json())["id"]
        filed = await client.patch(
            f"/api/chat/sessions/{name}/folder", json={"folder_id": folder_id}
        )
        assert filed.status == 200, await filed.text()
        assert _meta(name)["imported_from"] == ORIGIN

        archived = await client.patch(
            f"/api/chat/sessions/{name}/lifecycle", json={"lifecycle": "archived"}
        )
        assert archived.status == 200, await archived.text()

    meta = _meta(name)
    assert meta["imported_from"] == ORIGIN
    assert meta.get("pinned") is True and meta.get("folder_id") == folder_id
    # Setup, run again, recognises it as the conversation it brought over.
    assert _plan_conversation(_item()).state is ItemState.EXISTING
