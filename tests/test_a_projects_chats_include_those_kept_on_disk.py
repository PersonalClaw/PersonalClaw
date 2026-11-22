"""A project's chats, and the guard before its delete, count the chats kept only on disk.

Measured before this was written: ``GET /api/projects/{id}/linked`` listed the project's chats from
the chats the gateway holds, and the delete's guard counted those alone. With ``restore_sessions``
off, every chat is kept only on disk after a restart, so a project listed none of its chats until
each was opened again, and a plain delete went through as if it had none.

Now both read the chats kept on disk too, from their records, as the chat list does: a chat of the
owner's whose record names the project, once whether it is held or not, at rest. A worker's or an
app's chat is not one of the project's chats, as a held one is not. Each test saves the chat and
takes it out of the gateway first, as a restart does.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _gateway(tmp_path):
    registry._providers.clear()
    state = _make_state(tmp_path / "sessions")
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        app["state"] = state
        async with TestClient(TestServer(app)) as client:
            yield client, state
    registry._providers.clear()


async def _project(client, name: str = "Garden") -> str:
    return (await (await client.post("/api/projects", json={"name": name})).json())["id"]


def _chat(state, name: str, project_id: str, *, app: str = "", kept: bool = True):
    """A chat of *project_id* saved to disk, then taken out of the gateway unless *kept*."""
    chat = state.get_or_create_session(name)
    chat.append("user", "Which seeds ship this week?", "msg msg-u", broadcast=False)
    chat.title = name.replace("-", " ").capitalize()
    chat.project_id = project_id
    if app:
        chat._app = app
    save_session_to_history(state, chat, force=True)
    if not kept:
        state._sessions.pop(chat.key)
    return chat


async def test_the_projects_chats_include_one_kept_only_on_disk(tmp_path):
    """🔴 Red before: only the chat the gateway held was listed."""
    async with _gateway(tmp_path) as (client, state):
        garden, taxes = await _project(client), await _project(client, "Taxes")
        _chat(state, "seed-order", garden, kept=False)
        _chat(state, "receipts", taxes, kept=False)
        _chat(state, "notes-digest", garden, app="notes", kept=False)
        _chat(state, "plan-beds", garden)

        resp = await client.get(f"/api/projects/{garden}/linked")
        assert resp.status == 200
        chats = (await resp.json())["chats"]

        assert sorted(c["key"] for c in chats) == ["plan-beds", "seed-order"]
        kept = next(c for c in chats if c["key"] == "seed-order")
        assert kept["title"] == "Seed order"
        assert kept["run_state"] == state.idle_run_state("seed-order")
        assert kept["run_state"]["phase"] == "idle"


async def test_a_plain_delete_is_refused_for_a_chat_kept_only_on_disk(tmp_path):
    """🔴 Red before: it went through as if the project had no chat."""
    async with _gateway(tmp_path) as (client, state):
        garden = await _project(client)
        _chat(state, "seed-order", garden, kept=False)

        resp = await client.delete(f"/api/projects/{garden}")

        assert resp.status == 409
        assert (await resp.json())["chats"] == 1
        assert state.conversation_log.get_metadata("dashboard_seed-order")["project_id"] == garden


async def test_a_held_chat_saved_to_disk_is_counted_once(tmp_path):
    async with _gateway(tmp_path) as (client, state):
        garden = await _project(client)
        _chat(state, "plan-beds", garden)

        resp = await client.delete(f"/api/projects/{garden}")

        assert resp.status == 409
        assert (await resp.json())["chats"] == 1


async def test_a_force_delete_clears_the_project_from_a_chat_kept_only_on_disk(tmp_path):
    async with _gateway(tmp_path) as (client, state):
        garden = await _project(client)
        _chat(state, "seed-order", garden, kept=False)

        assert (await client.delete(f"/api/projects/{garden}?force=true")).status == 200

        assert "project_id" not in state.conversation_log.get_metadata("dashboard_seed-order")
        assert (await client.get(f"/api/projects/{garden}/linked")).status == 404
