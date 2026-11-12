"""Each row says where its thing came from, by the thing's current title.

An artifact made in a chat names that chat, a loop's deliverable names its loop, and an item a
workflow run wrote names its run. ``from.title`` is "" once the thing it names is gone, and the row
keeps the ref.
"""

from __future__ import annotations

import pytest
from library_shared import artifacts, client, filed, isolate, loop, project


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def _rows(body: dict) -> dict:
    return {r["ref"]: r for r in body["items"]}


@pytest.mark.asyncio
async def test_a_row_names_the_chat_loop_or_run_it_came_from(home):
    from personalclaw.history import ConversationLog
    from personalclaw.loop import store as loop_store
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import WorkflowRun

    log = ConversationLog()
    log.append("dashboard:chat-lisbon", "user", "Plan three days in Lisbon")
    log.set_title("dashboard:chat-lisbon", "Lisbon weekend")
    prov = artifacts()
    page = prov.create(
        name="Lisbon trip plan",
        content="# Day one",
        kind="markdown",
        source="chat",
        session_id="dashboard:chat-lisbon",
    )
    nas = loop("Back up the NAS", project_id=project("Homelab"))
    plan = prov.create(name="Backup plan", content="# Plan", kind="markdown", tags=[f"loop:{nas}"])
    run = run_store.create(WorkflowRun(id="", workflow_name="Weekly digest"))
    garden = project("Garden")
    written = await filed("What the digest found", garden, run_id=run.id)
    lost = await filed("What a gone run found", garden, run_id="r-gone")
    async with client(home) as c:
        before = _rows(await (await c.get("/api/library")).json())
        loop_store.delete(nas)
        after = _rows(await (await c.get("/api/library")).json())
    assert before[f"artifact:{page.slug}"]["from"] == {
        "ref": "chat:dashboard:chat-lisbon",
        "title": "Lisbon weekend",
    }
    assert before[f"artifact:{plan.slug}"]["from"] == {
        "ref": f"loop:{nas}",
        "title": "Back up the NAS",
    }
    assert after[f"artifact:{plan.slug}"]["from"] == {"ref": f"loop:{nas}", "title": ""}
    assert before[f"knowledge:{written}"]["from"] == {
        "ref": f"workflow:{run.id}",
        "title": "Weekly digest",
    }
    assert before[f"knowledge:{lost}"]["from"] == {"ref": "workflow:r-gone", "title": ""}
