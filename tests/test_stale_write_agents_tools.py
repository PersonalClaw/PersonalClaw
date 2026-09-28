"""An agent, a routing note, a theme and an MCP server are saved only over the copy they came from,
and a chat's tags change one tag at a time.

Each of these pages saves a WHOLE record built from the copy it read: the agent editor sends every
field of the profile, the routing-note editor the whole note, "Update theme" the name, emoji and
every color, the MCP edit form the whole definition. When another tab — or the gateway itself —
saved the same record between the page's read and its save, the save replaced that change without
a word. The chat list did the same with a session's tags: a toggle sent the page's whole list.

The contract (`personalclaw/stale_write.py`): the read carries the record's ``revision``, the save
names it in ``If-Match``, and a stale one is refused with ``409 stale_write`` before anything is
written; no ``If-Match`` is ``428 revision_required``. A create replaces nothing and needs none.
Tags are converted instead: ``{"add": [...], "remove": [...]}`` applied to the tags as stored, which
cannot undo anyone else's change and so needs no revision.

Every "server-side writer" below is the real function the gateway writes that record with.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mcp_owner_allowed import confirmed

from personalclaw.approval_answer import YOU
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import AgentProfile, AppConfig


def revision_of(document):
    """Imported per call, so this file collects on a tree that predates the module and each test
    reports its own verdict there."""
    from personalclaw.stale_write import revision_of as _revision_of

    return _revision_of(document)


def _based_on(revision: str | None) -> dict[str, str]:
    """The header a page's save sends — nothing at all for a write that names no base."""
    return {} if revision is None else {"If-Match": f'"{revision}"'}


async def _refusal(resp) -> str:
    """The error code of a refused write."""
    body = await resp.json()
    error = body.get("error")
    return error.get("code", "") if isinstance(error, dict) else str(error)


@web.middleware
async def _as_owner(request: web.Request, handler):
    """What the auth middleware sets for the owner's browser session."""
    request["user"] = "owner"
    return await handler(request)


# ── Agents: PUT /api/agents/{name} ──────────────────────────────────────────────────────────────

AGENT = "researcher"

#: The fields the agent editor sends on every save (`AgentForm.draftToPayload`).
EDITOR_FIELDS = (
    "description",
    "model",
    "system_prompt",
    "voice",
    "natural_voice",
    "approval_mode",
    "skills",
    "tools",
    "triggers",
    "default_dir",
    "memory_store",
    "specialty",
    "route_hints",
)


@pytest.fixture
def agents_home(monkeypatch):
    home = config_loader.config_dir()
    cfg = AppConfig()
    cfg.default_agent = AGENT
    cfg.agents = {AGENT: AgentProfile(description="reads papers", skills=["search"])}
    cfg.save()
    return home


def _agents_app() -> web.Application:
    from personalclaw.dashboard.handlers import (
        api_personalclaw_agent_update,
        api_personalclaw_agents,
    )

    app = web.Application(middlewares=[_as_owner])
    app.router.add_get("/api/agents", api_personalclaw_agents)
    app.router.add_put("/api/agents/{name}", api_personalclaw_agent_update)
    return app


async def _read_agent(c: TestClient, name: str = AGENT) -> dict:
    """What the Agents page paints for one agent — the record the editor is seeded from."""
    body = await (await c.get("/api/agents")).json()
    return next(a for a in body["agents"] if a["name"] == name)


def _editor_save(record: dict, **edits) -> dict:
    """The body the editor sends: every field it shows, as read, with the user's edits."""
    return {"name": record["name"], **{k: record[k] for k in EDITOR_FIELDS}, **edits}


def _stored_agent(name: str = AGENT) -> AgentProfile:
    return AppConfig.load().agents[name]


class TestAgentEditor:
    @pytest.mark.asyncio
    async def test_the_list_carries_each_agents_revision(self, agents_home) -> None:
        async with TestClient(TestServer(_agents_app())) as c:
            record = await _read_agent(c)
        assert "revision" in record, "the list hands out no revision to save over"
        # `model_unavailable` is derived from the models set up, not edited here, so it is not
        # part of the revision: a model going away must not refuse a save of the profile.
        rest = {k: v for k, v in record.items() if k not in ("revision", "model_unavailable")}
        assert record["revision"] == revision_of(rest)

    @pytest.mark.asyncio
    async def test_a_second_save_from_the_same_read_is_refused(self, agents_home) -> None:
        async with TestClient(TestServer(_agents_app())) as c:
            record = await _read_agent(c)  # both tabs paint this
            base = record.get("revision")
            tab_a = _editor_save(record, description="reads papers and datasets")
            tab_b = _editor_save(record, skills=[*record["skills"], "summarise"])

            first = await c.put(f"/api/agents/{AGENT}", json=tab_a, headers=_based_on(base))
            assert first.status == 200, await first.text()

            second = await c.put(f"/api/agents/{AGENT}", json=tab_b, headers=_based_on(base))
            assert second.status == 409, await second.text()
            assert await _refusal(second) == "stale_write"

        stored = _stored_agent()
        assert stored.description == "reads papers and datasets", "tab A's change was undone"
        assert stored.skills == ["search"], "the refused save was written anyway"

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_base_is_refused(self, agents_home) -> None:
        async with TestClient(TestServer(_agents_app())) as c:
            record = await _read_agent(c)
            resp = await c.put(
                f"/api/agents/{AGENT}", json=_editor_save(record, description="overwritten")
            )
            assert resp.status == 428, await resp.text()
            assert await _refusal(resp) == "revision_required"
        assert _stored_agent().description == "reads papers"

    @pytest.mark.asyncio
    async def test_always_allow_for_this_agent_between_read_and_save_is_not_undone(
        self, agents_home
    ) -> None:
        from chat_test_helpers import _make_state

        async with TestClient(TestServer(_agents_app())) as c:
            record = await _read_agent(c)  # the editor opens: approval mode "" (inherit)
            assert record["approval_mode"] == ""

            # In a chat with this agent the owner answers a tool prompt "Always allow for this
            # agent" — the gateway writes approval_mode="auto" onto the profile itself.
            state = _make_state(agents_home)
            session = state.get_or_create_session("chat-1")
            session.agent = AGENT
            session.append("permission", "bash", json.dumps({"request_id": "req-1"}))
            session._approval_futures["req-1"] = asyncio.get_running_loop().create_future()
            grant = state.decide_session_approval(session, "req-1", "trust_agent", by=YOU)
            assert grant == {"scope": "agent", "persisted": True, "agent": AGENT}

            # The editor, still holding approval mode "", saves a description edit.
            resp = await c.put(
                f"/api/agents/{AGENT}",
                json=_editor_save(record, description="reads papers, fast"),
                headers=_based_on(record.get("revision")),
            )
            assert resp.status == 409, await resp.text()
            assert await _refusal(resp) == "stale_write"

        stored = _stored_agent()
        assert stored.approval_mode == "auto", "the standing grant was reverted by a stale save"
        assert stored.description == "reads papers"

    @pytest.mark.asyncio
    async def test_the_save_answers_with_the_revision_the_next_read_reports(
        self, agents_home
    ) -> None:
        async with TestClient(TestServer(_agents_app())) as c:
            record = await _read_agent(c)
            resp = await c.put(
                f"/api/agents/{AGENT}",
                json=_editor_save(record, description="v2"),
                headers=_based_on(record.get("revision")),
            )
            assert resp.status == 200, await resp.text()
            saved = (await resp.json()).get("revision")
            assert saved and saved == (await _read_agent(c))["revision"]
            # An editor that stays open saves again over the answer, not over its first read.
            again = await c.put(
                f"/api/agents/{AGENT}",
                json=_editor_save(record, description="v3"),
                headers=_based_on(saved),
            )
            assert again.status == 200, await again.text()
        assert _stored_agent().description == "v3"

    @pytest.mark.asyncio
    async def test_one_scalar_field_alone_needs_no_base(self, agents_home) -> None:
        # The reserved agents' model picker sends only `model`: one value written over another is
        # the edit the user made, and there is no copy of anything else in the request to go stale.
        from personalclaw.agents.defaults import LITE_AGENT_NAME

        async with TestClient(TestServer(_agents_app())) as c:
            resp = await c.put(f"/api/agents/{LITE_AGENT_NAME}", json={"model": "glm-6"})
            assert resp.status == 200, await resp.text()
        assert _stored_agent(LITE_AGENT_NAME).model == "glm-6"


# ── Routing notes: PUT /api/agent-metadata/{name} ───────────────────────────────────────────────


def _notes_app() -> web.Application:
    from personalclaw.dashboard.handlers.agents import (
        api_agent_metadata_get,
        api_agent_metadata_put,
    )

    app = web.Application(middlewares=[_as_owner])
    app.router.add_get("/api/agent-metadata/{name}", api_agent_metadata_get)
    app.router.add_put("/api/agent-metadata/{name}", api_agent_metadata_put)
    return app


async def _read_note(c: TestClient) -> tuple[str, str | None]:
    body = await (await c.get(f"/api/agent-metadata/{AGENT}")).json()
    return body["content"], body.get("revision")


class TestRoutingNotes:
    @pytest.mark.asyncio
    async def test_a_second_save_from_the_same_read_is_refused(self, agents_home) -> None:
        from personalclaw import agent_metadata

        agent_metadata.save(AGENT, "use for literature reviews")
        async with TestClient(TestServer(_notes_app())) as c:
            _note, base = await _read_note(c)
            first = await c.put(
                f"/api/agent-metadata/{AGENT}",
                json={"content": "use for literature reviews and meta-analyses"},
                headers=_based_on(base),
            )
            assert first.status == 200, await first.text()
            second = await c.put(
                f"/api/agent-metadata/{AGENT}",
                json={"content": "prefers primary sources"},
                headers=_based_on(base),
            )
            assert second.status == 409, await second.text()
            assert await _refusal(second) == "stale_write"
        assert agent_metadata.load(AGENT) == "use for literature reviews and meta-analyses"

    @pytest.mark.asyncio
    async def test_replacing_a_note_without_a_base_is_refused(self, agents_home) -> None:
        from personalclaw import agent_metadata

        agent_metadata.save(AGENT, "use for literature reviews")
        async with TestClient(TestServer(_notes_app())) as c:
            resp = await c.put(f"/api/agent-metadata/{AGENT}", json={"content": "replaced"})
            assert resp.status == 428, await resp.text()
            assert await _refusal(resp) == "revision_required"
        assert agent_metadata.load(AGENT) == "use for literature reviews"

    @pytest.mark.asyncio
    async def test_the_first_note_needs_no_base(self, agents_home) -> None:
        from personalclaw import agent_metadata

        async with TestClient(TestServer(_notes_app())) as c:
            resp = await c.put(f"/api/agent-metadata/{AGENT}", json={"content": "a first note"})
            assert resp.status == 200, await resp.text()
            body = await resp.json()
            assert body["content"] == "a first note"
            assert body["revision"] == (await _read_note(c))[1]
        assert agent_metadata.load(AGENT) == "a first note"

    @pytest.mark.asyncio
    async def test_the_orchestrator_seeding_the_note_between_read_and_save_is_not_undone(
        self, agents_home
    ) -> None:
        from personalclaw import agent_metadata
        from personalclaw.orchestrator_skill import generate_orchestrator_skill

        async with TestClient(TestServer(_notes_app())) as c:
            note, base = await _read_note(c)  # the editor opens on an empty note
            assert note == ""

            # The orchestrator regenerates its roster and seeds the missing note from the
            # agent's description — the gateway writing the same file itself.
            generate_orchestrator_skill(SimpleNamespace(_dir=agents_home / "skills"))
            assert agent_metadata.load(AGENT) == "reads papers"

            resp = await c.put(
                f"/api/agent-metadata/{AGENT}",
                json={"content": "typed into the empty editor"},
                headers=_based_on(base),
            )
            assert resp.status == 409, await resp.text()
            assert await _refusal(resp) == "stale_write"
        assert agent_metadata.load(AGENT) == "reads papers"


# ── Themes: PUT /api/themes/{slug} ──────────────────────────────────────────────────────────────

DARK = {"--color-primary": "#112233", "--color-canvas": "#000000"}
LIGHT = {"--color-primary": "#445566", "--color-canvas": "#ffffff"}


def _themes_app() -> web.Application:
    from personalclaw.dashboard.handlers.agents import api_theme_detail, api_themes_create

    app = web.Application()
    app.router.add_post("/api/themes", api_themes_create)
    app.router.add_get("/api/themes/{slug}", api_theme_detail)
    app.router.add_put("/api/themes/{slug}", api_theme_detail)
    return app


async def _create_theme(c: TestClient) -> str:
    resp = await c.post(
        "/api/themes", json={"name": "Harbor", "emoji": "🌊", "dark": DARK, "light": LIGHT}
    )
    assert resp.status == 200, await resp.text()
    return (await resp.json())["slug"]


async def _read_theme(c: TestClient, slug: str) -> dict:
    return await (await c.get(f"/api/themes/{slug}")).json()


def _update(theme: dict, **edits) -> dict:
    """What "Update theme" sends: the saved theme's name and emoji, and this browser's colors."""
    return {
        "name": theme["name"],
        "emoji": theme["emoji"],
        "dark": theme["dark"],
        "light": theme["light"],
        **edits,
    }


def _stored_theme(slug: str) -> dict:
    return json.loads((config_loader.config_dir() / "themes" / f"{slug}.json").read_text())


class TestThemes:
    @pytest.mark.asyncio
    async def test_the_read_carries_the_themes_revision(self) -> None:
        async with TestClient(TestServer(_themes_app())) as c:
            slug = await _create_theme(c)
            theme = await _read_theme(c, slug)
        assert "revision" in theme, "the read hands out no revision to save over"
        assert theme["revision"] == revision_of({k: v for k, v in theme.items() if k != "revision"})

    @pytest.mark.asyncio
    async def test_a_second_save_from_the_same_read_is_refused(self) -> None:
        async with TestClient(TestServer(_themes_app())) as c:
            slug = await _create_theme(c)
            theme = await _read_theme(c, slug)  # both tabs load this
            base = theme.get("revision")
            first = await c.put(
                f"/api/themes/{slug}",
                json=_update(theme, dark={**DARK, "--color-primary": "#aa0000"}),
                headers=_based_on(base),
            )
            assert first.status == 200, await first.text()
            second = await c.put(
                f"/api/themes/{slug}", json=_update(theme, name="Harbour"), headers=_based_on(base)
            )
            assert second.status == 409, await second.text()
            assert await _refusal(second) == "stale_write"
        stored = _stored_theme(slug)
        assert stored["dark"]["--color-primary"] == "#aa0000", "tab A's color was undone"
        assert stored["name"] == "Harbor"

    @pytest.mark.asyncio
    async def test_a_save_that_names_no_base_is_refused(self) -> None:
        async with TestClient(TestServer(_themes_app())) as c:
            slug = await _create_theme(c)
            theme = await _read_theme(c, slug)
            resp = await c.put(f"/api/themes/{slug}", json=_update(theme, name="Overwritten"))
            assert resp.status == 428, await resp.text()
            assert await _refusal(resp) == "revision_required"
        assert _stored_theme(slug)["name"] == "Harbor"

    @pytest.mark.asyncio
    async def test_a_synced_version_landing_between_read_and_save_is_not_undone(self) -> None:
        from personalclaw.durability import inventory, reconcile, writeback

        async with TestClient(TestServer(_themes_app())) as c:
            slug = await _create_theme(c)
            theme = await _read_theme(c, slug)  # the Design panel loads the theme

            # Another device's version of the theme is written into the live store through the
            # durability writeback — the write a sync pull and a take-remote conflict
            # resolution both make.
            theirs = {**_stored_theme(slug), "name": "Harbor at dusk"}
            themes_dir = config_loader.config_dir() / "themes"
            entry = inventory.by_id("themes")
            writeback.apply_rows(
                entry,
                themes_dir,
                [{"id": slug, "data": theirs}],
                read=reconcile.read_local(entry, themes_dir),
            )

            resp = await c.put(
                f"/api/themes/{slug}",
                json=_update(theme, light={**LIGHT, "--color-primary": "#00aa00"}),
                headers=_based_on(theme.get("revision")),
            )
            assert resp.status == 409, await resp.text()
            assert await _refusal(resp) == "stale_write"
        assert _stored_theme(slug)["name"] == "Harbor at dusk"

    @pytest.mark.asyncio
    async def test_create_and_update_answer_with_the_revision_the_next_read_reports(self) -> None:
        async with TestClient(TestServer(_themes_app())) as c:
            created = await (
                await c.post("/api/themes", json={"name": "Dune", "dark": DARK, "light": LIGHT})
            ).json()
            theme = await _read_theme(c, created["slug"])
            assert created.get("revision") == theme["revision"]
            resp = await c.put(
                f"/api/themes/{created['slug']}",
                json=_update(theme, emoji="🔥"),
                headers=_based_on(created["revision"]),
            )
            assert resp.status == 200, await resp.text()
            assert (await resp.json())["revision"] == (await _read_theme(c, created["slug"]))[
                "revision"
            ]


# ── MCP servers: PUT /api/mcp/servers/{name} ────────────────────────────────────────────────────

SERVER = "files"


@pytest.fixture
def mcp_home():
    home = config_loader.config_dir()
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    return home


def _mcp_app() -> web.Application:
    from personalclaw.dashboard.handlers.mcp import api_mcp_server_detail

    app = web.Application()
    app.router.add_get("/api/mcp/servers/{name}", api_mcp_server_detail)
    app.router.add_put("/api/mcp/servers/{name}", api_mcp_server_detail)
    return app


async def _add_server(c: TestClient) -> None:
    resp = await c.put(
        f"/api/mcp/servers/{SERVER}",
        json=confirmed(
            {"transport": "stdio", "command": "npx", "args": ["-y", "server-files", "/data"]}
        ),
    )
    assert resp.status == 200, await resp.text()


async def _read_server(c: TestClient) -> dict:
    return await (await c.get(f"/api/mcp/servers/{SERVER}")).json()


def _stored_server(home) -> dict:
    return json.loads((home / "mcp.json").read_text())["mcpServers"][SERVER]


class TestMcpServerEdit:
    @pytest.mark.asyncio
    async def test_adding_needs_no_base_and_the_read_carries_a_revision(self, mcp_home) -> None:
        async with TestClient(TestServer(_mcp_app())) as c:
            await _add_server(c)
            view = await _read_server(c)
        assert "revision" in view, "the read hands out no revision to save over"
        assert view["revision"] == revision_of({k: v for k, v in view.items() if k != "revision"})

    @pytest.mark.asyncio
    async def test_a_second_edit_from_the_same_read_is_refused(self, mcp_home) -> None:
        async with TestClient(TestServer(_mcp_app())) as c:
            await _add_server(c)
            view = await _read_server(c)  # both tabs open the edit form on this
            base = view.get("revision")
            first = await c.put(
                f"/api/mcp/servers/{SERVER}",
                json=confirmed(
                    {
                        "transport": "stdio",
                        "command": "npx",
                        "args": ["-y", "server-files", "/srv"],
                    }
                ),
                headers=_based_on(base),
            )
            assert first.status == 200, await first.text()
            second = await c.put(
                f"/api/mcp/servers/{SERVER}",
                json=confirmed({"transport": "stdio", "command": "node", "args": view["args"]}),
                headers=_based_on(base),
            )
            assert second.status == 409, await second.text()
            assert await _refusal(second) == "stale_write"
        stored = _stored_server(mcp_home)
        assert stored["args"] == ["-y", "server-files", "/srv"], "tab A's edit was undone"
        assert stored["command"] == "npx"

    @pytest.mark.asyncio
    async def test_an_edit_that_names_no_base_is_refused(self, mcp_home) -> None:
        # The Add form reaching a name that is already configured sends no base — it read nothing.
        async with TestClient(TestServer(_mcp_app())) as c:
            await _add_server(c)
            resp = await c.put(
                f"/api/mcp/servers/{SERVER}",
                json=confirmed({"transport": "stdio", "command": "rm"}),
            )
            assert resp.status == 428, await resp.text()
            assert await _refusal(resp) == "revision_required"
        assert _stored_server(mcp_home)["command"] == "npx"

    @pytest.mark.asyncio
    async def test_the_provider_card_editing_it_between_read_and_save_is_not_undone(
        self, mcp_home
    ) -> None:
        from personalclaw.providers import mcp_instances

        async with TestClient(TestServer(_mcp_app())) as c:
            await _add_server(c)
            view = await _read_server(c)  # the Tools page opens the edit form

            # Settings → Providers → MCP Tool Servers edits the same server's arguments.
            mcp_instances.update_instance(
                SERVER,
                config={"transport": "stdio", "command": "npx", "args": "-y server-files /home"},
            )

            resp = await c.put(
                f"/api/mcp/servers/{SERVER}",
                json=confirmed({"transport": "stdio", "command": "uvx", "args": view["args"]}),
                headers=_based_on(view.get("revision")),
            )
            assert resp.status == 409, await resp.text()
            assert await _refusal(resp) == "stale_write"
        stored = _stored_server(mcp_home)
        assert stored["args"] == ["-y", "server-files", "/home"], "the card's edit was undone"
        assert stored["command"] == "npx"

    @pytest.mark.asyncio
    async def test_an_edit_of_a_server_removed_since_does_not_bring_it_back(self, mcp_home) -> None:
        from personalclaw.config.secret_refs import remove_mcp_servers

        async with TestClient(TestServer(_mcp_app())) as c:
            await _add_server(c)
            view = await _read_server(c)
            remove_mcp_servers([SERVER])  # removed in another tab while the form was open
            resp = await c.put(
                f"/api/mcp/servers/{SERVER}",
                json=confirmed({"transport": "stdio", "command": "npx", "args": view["args"]}),
                headers=_based_on(view.get("revision")),
            )
            assert resp.status == 409, await resp.text()
            assert await _refusal(resp) == "stale_write"
        assert SERVER not in json.loads((mcp_home / "mcp.json").read_text())["mcpServers"]

    @pytest.mark.asyncio
    async def test_the_save_answers_with_the_revision_the_next_read_reports(self, mcp_home) -> None:
        async with TestClient(TestServer(_mcp_app())) as c:
            await _add_server(c)
            view = await _read_server(c)
            resp = await c.put(
                f"/api/mcp/servers/{SERVER}",
                json=confirmed({"transport": "stdio", "command": "npx", "args": ["server-files"]}),
                headers=_based_on(view.get("revision")),
            )
            assert resp.status == 200, await resp.text()
            assert (await resp.json()).get("revision") == (await _read_server(c))["revision"]


# ── Chat tags: PUT /api/chat/sessions/{session}/tags, one tag at a time ─────────────────────────


@pytest.fixture
def tags_state(tmp_path, monkeypatch):
    from chat_test_helpers import _make_state

    from personalclaw.dashboard.state import _ChatSession

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state._tags = [
        {"id": tid, "name": tid.upper(), "color": "#6b7280", "order": i, "status": False}
        for i, tid in enumerate(("t0", "t1", "t2"))
    ]
    session = _ChatSession("s1")
    session.tags = ["t0"]
    state._sessions["s1"] = session
    return state


def _tags_app(state) -> web.Application:
    from chat_test_helpers import _make_tags_app

    from personalclaw.dashboard.session_bulk import api_chat_sessions_bulk

    app = _make_tags_app(state)
    app.router.add_post("/api/chat/sessions/bulk", api_chat_sessions_bulk)
    return app


class TestSessionTagsOneAtATime:
    @pytest.mark.asyncio
    async def test_two_tabs_edits_from_the_same_view_both_survive(self, tags_state) -> None:
        # Both tabs painted the session as tagged [t0].
        async with TestClient(TestServer(_tags_app(tags_state))) as c:
            a = await c.put("/api/chat/sessions/s1/tags", json={"add": ["t1"]})  # tab A tags t1
            assert a.status == 200, await a.text()
            b = await c.put(
                "/api/chat/sessions/s1/tags", json={"remove": ["t0"]}
            )  # tab B untags t0
            assert b.status == 200, await b.text()
            assert (await b.json())["tags"] == ["t1"]
        assert tags_state._sessions["s1"].tags == ["t1"], "tab A's tag was dropped by tab B"

    @pytest.mark.asyncio
    async def test_a_move_between_board_columns_is_one_remove_and_one_add(self, tags_state) -> None:
        tags_state._sessions["s1"].tags = ["t0", "t2"]
        async with TestClient(TestServer(_tags_app(tags_state))) as c:
            resp = await c.put("/api/chat/sessions/s1/tags", json={"remove": ["t0"], "add": ["t1"]})
            assert resp.status == 200, await resp.text()
        assert tags_state._sessions["s1"].tags == ["t2", "t1"]

    @pytest.mark.asyncio
    async def test_a_bulk_tag_by_the_gateway_survives_a_stale_tabs_edit(self, tags_state) -> None:
        async with TestClient(TestServer(_tags_app(tags_state))) as c:
            # The chat list painted [t0]. The bulk action tags the session t2 meanwhile.
            bulk = await c.post(
                "/api/chat/sessions/bulk", json={"op": "tag", "keys": ["s1"], "tag_id": "t2"}
            )
            assert bulk.status == 200, await bulk.text()
            resp = await c.put("/api/chat/sessions/s1/tags", json={"add": ["t1"]})
            assert resp.status == 200, await resp.text()
        assert tags_state._sessions["s1"].tags == ["t0", "t2", "t1"]

    @pytest.mark.asyncio
    async def test_the_whole_list_form_is_refused_and_changes_nothing(self, tags_state) -> None:
        async with TestClient(TestServer(_tags_app(tags_state))) as c:
            resp = await c.put("/api/chat/sessions/s1/tags", json={"tags": ["t1"]})
            assert resp.status == 400, await resp.text()
        assert tags_state._sessions["s1"].tags == ["t0"]

    @pytest.mark.asyncio
    async def test_adding_a_tag_that_does_not_exist_is_refused(self, tags_state) -> None:
        async with TestClient(TestServer(_tags_app(tags_state))) as c:
            resp = await c.put("/api/chat/sessions/s1/tags", json={"add": ["gone"]})
            assert resp.status == 400, await resp.text()
            assert await _refusal(resp) == "unknown_tag_id"
        assert tags_state._sessions["s1"].tags == ["t0"]

    def test_a_retag_run_keeps_a_tag_set_while_the_model_was_thinking(self, tags_state) -> None:
        from personalclaw.dashboard.chat_retag import _apply_tags, _Candidate

        # The run collected the session tagged [t0]; while its model call was out, the user
        # tagged it t1 in the chat list. The model then proposed the set {t2}.
        cand = _Candidate(key="s1", history_key="", in_memory=True, tags=["t0"])
        tags_state._sessions["s1"].tags = ["t0", "t1"]
        assert _apply_tags(tags_state, cand, ["t2"]) is True
        assert tags_state._sessions["s1"].tags == ["t1", "t2"], "the user's tag was dropped"
