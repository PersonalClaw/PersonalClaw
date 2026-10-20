"""A value the gateway generates while it waits never replaces one the owner set meanwhile.

Three more writers read a value, waited for something slow, and then wrote what they generated
from that earlier read:

* a bookmark's scrape decided whether to put the page's title on the item, and whether to fill
  its text, from the item as it was read before the page was fetched, so a title or text the owner
  typed while it was fetched was replaced (`knowledge.pipeline.runner.ingest_item`);
* a chat's title was set when the model answered, so a chat renamed while the model was asked for
  a title took the generated one (`dashboard.chat_title`);
* a consolidation's skill refinement rewrote the skill's SKILL.md whole when the model answered,
  so an edit made on the Skills page while it ran was gone (`HistoryConsolidator`).

Each now decides from the value as it is when it writes: the owner's later write wins.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# ── a bookmark's scrape ───────────────────────────────────────────────────────────────────────

PAGE = "# Example Domain\n\nThis domain is for examples."


@pytest.fixture
def store(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    return KnowledgeStore(str(tmp_path / "k.db"))


async def _edited_on_its_page(store, item_id: str, body: dict) -> None:
    """The owner changes the item on its page (``PATCH /api/knowledge/items/{id}``)."""
    from personalclaw.dashboard.handlers.knowledge import update_item

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    app.router.add_patch("/api/knowledge/items/{id}", update_item)
    headers = {}
    if "content" in body:
        from personalclaw.stale_write import revision_of

        headers["If-Match"] = f'"{revision_of(store.get_item(item_id).get("content") or "")}"'
    with patch("personalclaw.dashboard.handlers.knowledge.sel"):
        async with TestClient(TestServer(app)) as c:
            resp = await c.patch(f"/api/knowledge/items/{item_id}", json=body, headers=headers)
            assert resp.status == 200, await resp.text()


def _scrape_page(monkeypatch, meanwhile: Callable | None = None) -> None:
    async def fetch(self, source):
        if meanwhile is not None:
            await meanwhile()
        return PAGE, {"url": source["uri"]}

    monkeypatch.setattr("personalclaw.knowledge.connectors.web_url.WebUrlConnector.fetch", fetch)


def _bookmark(store) -> str:
    # As the create handler makes one with no title typed: titled with its URL.
    return store.create_typed_item(
        item_type="bookmark", title="https://example.com/", url="https://example.com/"
    )


def test_a_title_typed_while_a_bookmark_is_scraped_is_kept(store, monkeypatch):
    """🔴 Red before: the scrape read the item before the page was fetched, saw its URL as the
    title, and put the page's title over the one the owner typed meanwhile."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item

    ensure_nodes_registered()
    iid = _bookmark(store)

    async def renamed() -> None:
        await _edited_on_its_page(store, iid, {"title": "Reading list: examples"})

    _scrape_page(monkeypatch, renamed)
    asyncio.run(ingest_item(store, iid))

    item = store.get_item(iid)
    assert item["title"] == "Reading list: examples"
    assert item["url_title"] == "Example Domain", "the page's title is still kept as its own"


def test_text_typed_while_a_bookmark_is_scraped_is_kept(store, monkeypatch):
    """🔴 Red before: the scrape filled the item's text with the page because the item it read
    before the fetch had none, over the notes the owner wrote meanwhile."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item

    ensure_nodes_registered()
    iid = _bookmark(store)

    async def noted() -> None:
        await _edited_on_its_page(
            store, iid, {"content": "Read the second section first.", "reingest": False}
        )

    _scrape_page(monkeypatch, noted)
    asyncio.run(ingest_item(store, iid))

    assert store.get_item(iid)["content"] == "Read the second section first."


def test_a_bookmark_nobody_renamed_takes_the_pages_title_and_text(store, monkeypatch):
    """The control: with nothing typed meanwhile, the URL placeholder gives way to the page's
    title, and the empty item takes the page's text."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item

    ensure_nodes_registered()
    iid = _bookmark(store)
    _scrape_page(monkeypatch)
    asyncio.run(ingest_item(store, iid))

    item = store.get_item(iid)
    assert item["title"] == "Example Domain"
    assert "This domain is for examples." in item["content"]


def test_a_title_the_owner_set_is_kept_whatever_it_says(store, monkeypatch):
    """A title a person set is never replaced, even one that reads as the URL: as for the AI
    title, the scraped one stays on the item as its own (``url_title``)."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item

    ensure_nodes_registered()
    iid = store.create_typed_item(
        item_type="bookmark",
        title="https://example.com/",
        url="https://example.com/",
        extra={"title_source": "user"},
    )
    _scrape_page(monkeypatch)
    asyncio.run(ingest_item(store, iid))

    item = store.get_item(iid)
    assert item["title"] == "https://example.com/"
    assert item["url_title"] == "Example Domain"


# ── a chat's title ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from chat_test_helpers import _make_state

    from personalclaw.dashboard.state import _ChatSession

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    session = _ChatSession("s1")
    session.messages = [
        {"role": "user", "content": "help me plan the offsite"},
        {"role": "assistant", "content": "sure, where and when?"},
    ]
    state._sessions["s1"] = session
    return state, session


def _chat_app(state) -> web.Application:
    from chat_test_helpers import _api_app

    from personalclaw.dashboard.chat_title import (
        api_chat_session_generate_title,
        api_chat_session_rename,
    )

    app = _api_app(state)
    app.router.add_patch("/api/chat/sessions/{session}/title", api_chat_session_rename)
    app.router.add_post(
        "/api/chat/sessions/{session}/generate-title", api_chat_session_generate_title
    )
    return app


def _model_titles(monkeypatch, title: str, meanwhile: Callable | None = None) -> None:
    """The title chore (``chores.run_chore``) answers *title*, after *meanwhile* ran."""

    async def run_chore(prompt, **_kw):
        if meanwhile is not None:
            await meanwhile()
        return title

    monkeypatch.setattr("personalclaw.chores.run_chore", run_chore)


async def _renamed(state, name: str) -> None:
    """The owner renames the chat from its header (``PATCH …/title``)."""
    with patch("personalclaw.dashboard.chat_title.sel"):
        async with TestClient(TestServer(_chat_app(state))) as c:
            resp = await c.patch("/api/chat/sessions/s1/title", json={"title": name})
            assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
async def test_a_chat_renamed_while_its_title_is_asked_for_keeps_its_name(chat, monkeypatch):
    """🔴 Red before: the auto-title set the model's answer unconditionally, over the name the
    owner gave the chat while the model was asked."""
    from personalclaw.dashboard.chat_title import _maybe_auto_title

    state, session = chat

    async def owner_renames() -> None:
        await _renamed(state, "Lisbon offsite")

    _model_titles(monkeypatch, "Offsite Planning", owner_renames)
    await _maybe_auto_title(state, session)

    assert session.title == "Lisbon offsite"
    assert session._titled is True


@pytest.mark.asyncio
async def test_a_chat_renamed_while_generate_title_runs_keeps_its_name(chat, monkeypatch):
    """🔴 Red before: Generate title set its answer over a rename made while it ran, and said
    so. Now the rename stands, and the answer says the chat's name as it is."""
    state, session = chat

    async def owner_renames() -> None:
        await _renamed(state, "Lisbon offsite")

    _model_titles(monkeypatch, "Offsite Planning", owner_renames)
    with patch("personalclaw.dashboard.chat_title.sel"):
        async with TestClient(TestServer(_chat_app(state))) as c:
            resp = await c.post("/api/chat/sessions/s1/generate-title")
            assert resp.status == 200, await resp.text()
            answered = await resp.json()

    assert answered["title"] == "Lisbon offsite"
    assert session.title == "Lisbon offsite"


@pytest.mark.asyncio
async def test_a_chat_nobody_renamed_takes_the_generated_title(chat, monkeypatch):
    """The control: the auto-title and Generate title both set the model's answer."""
    from personalclaw.dashboard.chat_title import _maybe_auto_title

    state, session = chat
    _model_titles(monkeypatch, "Offsite Planning")
    await _maybe_auto_title(state, session)
    assert session.title == "Offsite Planning"

    _model_titles(monkeypatch, "Planning the Lisbon offsite")
    with patch("personalclaw.dashboard.chat_title.sel"):
        async with TestClient(TestServer(_chat_app(state))) as c:
            resp = await c.post("/api/chat/sessions/s1/generate-title")
            answered = await resp.json()
    assert answered["title"] == session.title == "Planning the Lisbon offsite"


# ── a skill a consolidation refines ───────────────────────────────────────────────────────────

SKILL = "auto/trip-planner"


@pytest.fixture
def refining(tmp_path):
    """A consolidation set to refine skills, over a session that used ``auto/trip-planner``."""
    from personalclaw.history import ConversationLog, HistoryConsolidator
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import AutoSkillProvenance, SkillsLoader

    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    memory = MemoryStore(workspace=tmp_path / "workspace")
    memory.init()
    skills = SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False)
    assert skills.create_auto_skill(
        "trip-planner",
        description="Plan a trip",
        triggers="trip, travel",
        procedure_md="1. Book the train.\n2. Book the hotel.",
        provenance=AutoSkillProvenance(session_key="seed", created_at="2026-05-05T11:00:00+00:00"),
    )
    for i in range(5):
        log.append("dashboard:trip", "assistant", f"step {i}", tools=["skill_invoke"])
    consolidator = HistoryConsolidator(
        log=log,
        memory=memory,
        skills_loader=skills,
        auto_skills_enabled=True,
        auto_refine_enabled=True,
        auto_min_tool_calls=2,
    )
    return consolidator, skills


async def _refine(consolidator, meanwhile: Callable | None = None) -> list[dict]:
    """One consolidation whose model refines ``auto/trip-planner`` after *meanwhile* ran. Returns
    the audit rows it wrote."""

    async def answer(_prompt, _key, **_kw):
        if meanwhile is not None:
            await meanwhile()
        return {
            "history_entry": "Planned a trip.",
            "refined_skill": {
                "name": SKILL,
                "description": "Plan a trip, booking early",
                "triggers": "trip, travel",
                "procedure_md": "1. Book the train a month ahead.\n2. Book the hotel.",
            },
        }

    recorded: list[dict] = []
    audit = MagicMock()
    audit.log_tool_invocation = lambda **row: recorded.append(row)
    with (
        patch.object(consolidator, "_call_llm", side_effect=answer),
        patch("personalclaw.history.sel", return_value=audit),
    ):
        await consolidator._consolidate("dashboard:trip", include_history=True)
    return recorded


async def _edited_on_the_skills_page(skills, change: Callable[[str], str]) -> None:
    """The owner edits the skill on the Skills page (``PUT /api/skills/{name}``)."""
    from personalclaw.dashboard.handlers import api_skill_detail

    app = web.Application()
    app["state"] = SimpleNamespace(context_builder=SimpleNamespace(skills=skills))
    app.router.add_get("/api/skills/{name:.+}", api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", api_skill_detail)
    async with TestClient(TestServer(app)) as c:
        read = await (await c.get(f"/api/skills/{SKILL}")).json()
        resp = await c.put(
            f"/api/skills/{SKILL}",
            json={"content": change(read["content"])},
            headers={"If-Match": f'"{read["revision"]}"'},
        )
        assert resp.status == 200, await resp.text()


def _owners_edit(text: str) -> str:
    return text.replace("2. Book the hotel.", "2. Book the hotel near the station.")


@pytest.mark.asyncio
async def test_a_skill_edited_while_a_pass_refines_it_keeps_the_edit(refining):
    """🔴 Red before: the refinement rewrote SKILL.md whole when the model answered, over the edit
    the owner saved on the Skills page while it ran. Now it is not applied, and the audit log
    says why."""
    consolidator, skills = refining

    async def owner_edits() -> None:
        await _edited_on_the_skills_page(skills, _owners_edit)

    recorded = await _refine(consolidator, owner_edits)

    body = skills.load_skill(SKILL)
    assert "2. Book the hotel near the station." in body
    assert "a month ahead" not in body
    refused = [
        r
        for r in recorded
        if r.get("tool_name") == "auto_skill_refine" and r.get("outcome") == "rejected"
    ]
    assert [r["metadata"]["reason"] for r in refused] == ["changed_while_refining"]


@pytest.mark.asyncio
async def test_a_skill_nobody_edited_is_refined(refining):
    """The control: with no edit meanwhile, the refinement is written."""
    consolidator, skills = refining
    recorded = await _refine(consolidator)

    assert "1. Book the train a month ahead." in skills.load_skill(SKILL)
    assert any(
        r.get("tool_name") == "auto_skill_refine" and r.get("outcome") == "invoked"
        for r in recorded
    )
