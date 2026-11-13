"""A delete clears the project from every chat that names it, the ones kept only on disk too.

A chat the gateway holds is unbound in memory. A chat kept only on disk (an older one, or every chat
after a restart with ``restore_sessions`` off) named the gone project in its record, so the chat
list served it under a project that no longer exists, and restoring it bound it again. Now, once
the project is gone, by a plain delete or a forced one, every record is cleared too, and every
session the gateway holds, an app's included, so none writes the project back. The transcript is
left as it is, and a chat in another project is untouched. A delete that is refused clears none.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.history import ConversationLog
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


class _LiveChat:
    def __init__(self, key: str, project_id: str, app: str = "") -> None:
        self.key = key
        self.project_id = project_id
        self._app = app
        self.title = key


@asynccontextmanager
async def _gateway(tmp_path):
    registry._providers.clear()
    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        app["state"] = type("S", (), {"_sessions": {}, "conversation_log": log})()
        async with TestClient(TestServer(app)) as client:
            yield client, log
    registry._providers.clear()


def _chat_on_disk(log: ConversationLog, key: str, project_id: str) -> None:
    log.append(key, "user", "Which seeds ship this week?")
    log.update_metadata(key, {"project_id": project_id})


@pytest.mark.asyncio
async def test_a_force_delete_clears_the_project_from_chats_kept_only_on_disk(tmp_path):
    async with _gateway(tmp_path) as (client, log):
        made = await client.post("/api/projects", json={"name": "Garden"})
        garden = (await made.json())["id"]
        other = (await (await client.post("/api/projects", json={"name": "Taxes"})).json())["id"]
        _chat_on_disk(log, "dashboard_seed-order", garden)
        _chat_on_disk(log, "dashboard_receipts", other)
        live = _LiveChat("plan-beds", garden)
        _chat_on_disk(log, "dashboard_plan-beds", garden)
        client.app["state"]._sessions[live.key] = live
        # An app's chat, which the guard does not count, would write the project back at its
        # next save.
        apps = _LiveChat("notes-digest", garden, app="notes")
        client.app["state"]._sessions[apps.key] = apps

        assert (await client.delete(f"/api/projects/{garden}?force=true")).status == 200

        assert "project_id" not in log.get_metadata("dashboard_seed-order")
        assert log.read_messages("dashboard_seed-order"), "the transcript was touched"
        # A held chat is unbound in memory and on its record, so a restart cannot bind it again.
        assert live.project_id == ""
        assert apps.project_id == ""
        assert "project_id" not in log.get_metadata("dashboard_plan-beds")
        assert log.get_metadata("dashboard_receipts").get("project_id") == other


@pytest.mark.asyncio
async def test_a_plain_delete_clears_the_project_from_chats_kept_only_on_disk(tmp_path):
    async with _gateway(tmp_path) as (client, log):
        garden = (await (await client.post("/api/projects", json={"name": "Garden"})).json())["id"]
        _chat_on_disk(log, "dashboard_seed-order", garden)

        # No chat it holds and no loop names Garden, so the dashboard's first, plain delete goes
        # through: the record kept on disk must not keep naming a project that is gone.
        assert (await client.delete(f"/api/projects/{garden}")).status == 200

        assert "project_id" not in log.get_metadata("dashboard_seed-order")
        assert log.read_messages("dashboard_seed-order"), "the transcript was touched"


@pytest.mark.asyncio
async def test_a_force_delete_the_store_refuses_clears_no_record_on_disk(tmp_path):
    from personalclaw.tasks.hierarchy import HierarchyStore
    from personalclaw.tasks.models import PERSONAL_PROJECT

    async with _gateway(tmp_path) as (client, log):
        store = HierarchyStore()
        store.ensure_defaults()
        (personal,) = [p.id for p in store.list_projects() if p.name == PERSONAL_PROJECT]
        _chat_on_disk(log, "dashboard_seed-order", personal)

        assert (await client.delete(f"/api/projects/{personal}?force=true")).status == 400
        assert log.get_metadata("dashboard_seed-order").get("project_id") == personal


@pytest.mark.asyncio
async def test_a_plain_delete_refused_for_a_held_chat_changes_no_record(tmp_path):
    async with _gateway(tmp_path) as (client, log):
        garden = (await (await client.post("/api/projects", json={"name": "Garden"})).json())["id"]
        _chat_on_disk(log, "dashboard_seed-order", garden)
        client.app["state"]._sessions["plan-beds"] = _LiveChat("plan-beds", garden)

        assert (await client.delete(f"/api/projects/{garden}")).status == 409
        assert log.get_metadata("dashboard_seed-order").get("project_id") == garden
