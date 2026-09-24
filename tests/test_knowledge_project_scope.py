"""Knowledge project tagging + the sharing-policy filter (WORK-CONTAINERS §1.6 — WF2WOR-11).

Knowledge stays ONE global library; a project is a tag plus item metadata. What is tested
here is the writer/reader pair that makes that scoping real:

* **Writer** — the `knowledge-persist` action provider stamps `project_id` / `run_id` /
  `sharing_policy` on what a run writes AND files the item under its project's tag.
* **Reader (brief)** — `session_brief.load_items` reads items BY that tag. Before this atom
  nothing wrote it, so the project brief was a live reader of a key no writer produced: it
  returned nothing for every project, silently. That is asserted here as an outcome.
* **Reader (view)** — `project_scope.project_items` (served through
  `GET /api/projects/{id}/linked`) shows a project its own items whatever their policy, plus
  other projects' `shared` items labeled with their source project. Another project's
  PRIVATE items never appear — the policy field's teeth.

`sharing_policy` is a CLOSED enum and is enumerated exhaustively; an unrecognised value
fails CLOSED (private), because the only safe reading of unknown intent for a visibility
control is "do not surface it elsewhere".
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.knowledge_persist_provider import (
    KnowledgePersistActionProvider,
)
from personalclaw.knowledge import project_scope
from personalclaw.knowledge.session_brief import build as build_brief
from personalclaw.knowledge.session_brief import project_tag
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """`PERSONALCLAW_HOME` so `knowledge_db_path()` — and every other import-bound store —
    resolves inside tmp. Patching `config_dir` alone would miss the modules that bound it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: home)
    return home


def _store() -> KnowledgeStore:
    return KnowledgeStore(db_path=str(knowledge_db_path()))


async def _persist(
    *,
    title: str,
    project_id: str = "",
    run_id: str = "r-1",
    policy: str | None = None,
    content: str = "body text",
) -> dict:
    cfg: dict = {"title": title, "content": content, "kind": "fact"}
    if policy is not None:
        cfg["sharing_policy"] = policy
    payload = {"node_id": "n1", "run_id": run_id}
    if project_id:
        payload["project_id"] = project_id
    result = await KnowledgePersistActionProvider().execute(
        cfg, ActionContext(event="workflow_node", payload=payload)
    )
    assert result.success, result.error
    return json.loads(result.stdout)


def _metadata(item_id: str) -> dict:
    rows = list(_store().db.execute("SELECT file_metadata FROM items WHERE id = ?", (item_id,)))
    assert rows, f"item {item_id} not found"
    return json.loads(rows[0]["file_metadata"] or "{}")


def _tags(item_id: str) -> set[str]:
    rows = _store().db.execute(
        "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id WHERE it.item_id = ?",
        (item_id,),
    )
    return {r["name"] for r in rows}


# ── the closed enum ─────────────────────────────────────────────────────────


class TestSharingPolicy:
    def test_the_enum_is_exactly_private_and_shared(self):
        assert {p.value for p in project_scope.SharingPolicy} == {"private", "shared"}

    def test_an_undeclared_or_unknown_policy_fails_closed_to_private(self):
        assert project_scope.normalize_policy(None) is project_scope.SharingPolicy.PRIVATE
        assert project_scope.normalize_policy("") is project_scope.SharingPolicy.PRIVATE
        assert project_scope.normalize_policy("world-readable") is (
            project_scope.SharingPolicy.PRIVATE
        )
        assert project_scope.DEFAULT_SHARING_POLICY is project_scope.SharingPolicy.PRIVATE

    def test_every_member_is_handled_by_the_cross_container_filter(self):
        """Enumerated, not spot-checked: each member must produce a decided answer for a
        FOREIGN project, so a future third member trips this instead of falling through."""
        decided = {
            policy: project_scope.visible_in_project(
                {"project_id": "p-owner", "sharing_policy": policy.value}, project_id="p-other"
            )
            for policy in project_scope.SharingPolicy
        }
        assert decided == {
            project_scope.SharingPolicy.PRIVATE: False,
            project_scope.SharingPolicy.SHARED: True,
        }

    def test_an_unscoped_item_is_not_claimed_by_any_project(self):
        assert not project_scope.visible_in_project({}, project_id="p-a")
        assert not project_scope.visible_in_project({"sharing_policy": "shared"}, project_id="p-a")


# ── the writer ──────────────────────────────────────────────────────────────


class TestRunWrittenItemsCarryScope:
    @pytest.mark.asyncio
    async def test_a_run_written_item_carries_project_run_and_a_private_default(self):
        out = await _persist(title="Cold start", project_id="p-alpha", run_id="r-99")

        meta = _metadata(out["item_id"])
        assert meta["project_id"] == "p-alpha"
        assert meta["run_id"] == "r-99"
        assert meta["sharing_policy"] == "private"
        assert project_tag("p-alpha") in _tags(out["item_id"])

    @pytest.mark.asyncio
    async def test_a_declared_shared_policy_is_honored(self):
        out = await _persist(title="Shared fact", project_id="p-alpha", policy="shared")

        assert _metadata(out["item_id"])["sharing_policy"] == "shared"

    @pytest.mark.asyncio
    async def test_an_unknown_declared_policy_fails_closed(self):
        out = await _persist(title="Typo", project_id="p-alpha", policy="pubic")

        assert _metadata(out["item_id"])["sharing_policy"] == "private"

    @pytest.mark.asyncio
    async def test_a_write_outside_a_run_gets_no_scope_fields(self):
        """A visibility field on a row that belongs to no container is a field nobody can act
        on — so it is not written at all."""
        result = await KnowledgePersistActionProvider().execute(
            {"title": "Manual", "content": "body text", "kind": "fact"},
            ActionContext(event="workflow_node", payload={}),
        )
        assert result.success
        meta = _metadata(json.loads(result.stdout)["item_id"])
        assert "sharing_policy" not in meta
        assert "project_id" not in meta

    @pytest.mark.asyncio
    async def test_another_projects_reinforce_does_not_steal_the_item(self):
        """First writer owns the container: a second project re-persisting identical content
        records corroboration without moving the item — otherwise "private to its project"
        would end wherever the last run happened to be."""
        first = await _persist(title="Shared truth", project_id="p-alpha", content="same body")
        second = await _persist(title="Shared truth", project_id="p-beta", content="same body")

        assert second["item_id"] == first["item_id"]  # reinforced, not duplicated
        meta = _metadata(first["item_id"])
        assert meta["project_id"] == "p-alpha"
        assert project_tag("p-alpha") in _tags(first["item_id"])
        assert project_tag("p-beta") not in _tags(first["item_id"])


# ── the readers ─────────────────────────────────────────────────────────────


class TestProjectReaders:
    @pytest.mark.asyncio
    async def test_the_project_brief_finally_finds_what_a_run_wrote(self):
        """The starved reader. `load_items` has always queried the project tag; until this
        atom nothing wrote it, so every project's brief was empty by construction."""
        await _persist(title="Cold start latency", project_id="p-alpha")

        brief = build_brief(_store(), project_id="p-alpha")

        assert not brief.empty
        assert "Cold start latency" in brief.render()
        assert build_brief(_store(), project_id="p-unrelated").empty

    @pytest.mark.asyncio
    async def test_the_project_view_shows_own_items_and_only_shared_foreign_ones(self):
        own_private = await _persist(title="Own private", project_id="p-alpha", content="a")
        foreign_private = await _persist(title="Foreign private", project_id="p-beta", content="b")
        foreign_shared = await _persist(
            title="Foreign shared", project_id="p-beta", policy="shared", content="c"
        )

        rows = project_scope.project_items(_store(), project_id="p-alpha")

        by_id = {r["id"]: r for r in rows}
        assert own_private["item_id"] in by_id
        assert foreign_shared["item_id"] in by_id
        assert foreign_private["item_id"] not in by_id
        assert by_id[own_private["item_id"]]["source_project"] == ""
        # A foreign row is labeled with its OWNING project, never presented as this
        # project's own output. The name is "" only when that project no longer exists.
        assert by_id[foreign_shared["item_id"]]["project_id"] == "p-beta"
        assert by_id[foreign_shared["item_id"]]["sharing_policy"] == "shared"

    @pytest.mark.asyncio
    async def test_an_unscoped_project_id_reads_nothing(self):
        await _persist(title="Own", project_id="p-alpha")

        assert project_scope.project_items(_store(), project_id="") == []


# ── the HTTP surface the FE reads ───────────────────────────────────────────


@asynccontextmanager
async def _client(home):
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=home),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


@pytest.mark.asyncio
async def test_linked_endpoint_serves_the_project_knowledge_section(_isolated_home):
    """The FE reader: `/linked` is what the project panel fetches, so the section has to be
    on THAT payload — a filter only the Python API can see is a filter no user benefits from.
    """
    async with _client(_isolated_home) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Alpha"})).json())["id"]
        await _persist(title="Persisted by a run", project_id=pid)
        await _persist(title="Someone else's secret", project_id="p-other", content="zz")

        body = await (await client.get(f"/api/projects/{pid}/linked")).json()

        titles = [k["title"] for k in body["knowledge"]]
        assert titles == ["Persisted by a run"]
        assert body["knowledge"][0]["sharing_policy"] == "private"
        assert body["knowledge"][0]["run_id"] == "r-1"
