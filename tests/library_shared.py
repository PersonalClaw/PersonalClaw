"""A scratch home with projects, loops, artifacts and knowledge in it, and a client that serves the
routes the library reads: the tasks and projects routes, the artifact routes, the knowledge routes
and the library's own. Shared by the ``test_library_*`` files."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def isolate(tmp_path: Path, monkeypatch: Any) -> Path:
    """Point every store at a fresh home under *tmp_path*, and forget the providers a previous
    test built over another home."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: home)
    from personalclaw.artifacts import registry as artifact_registry
    from personalclaw.tasks import registry as task_registry

    monkeypatch.setattr(artifact_registry, "_providers", {})
    task_registry._providers.clear()
    return home


def knowledge_store() -> Any:
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def artifacts() -> Any:
    from personalclaw.artifacts.registry import get_provider

    return get_provider()


def project(name: str) -> str:
    """A project named *name*, and its id."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    return HierarchyStore().create_project(name=name).id


def loop(name: str, *, project_id: str = "", tasks_project_id: str = "") -> str:
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop

    made = loop_store.create(
        Loop(
            id="",
            name=name,
            kind="goal",
            task=name,
            project_id=project_id,
            tasks_project_id=tasks_project_id,
        )
    )
    return made.id


def note(title: str, content: str = "body", **kw: Any) -> str:
    return knowledge_store().create_typed_item(item_type="note", title=title, content=content, **kw)


def mirror(slug: str, title: str) -> str:
    """The search mirror the artifacts index keeps of artifact *slug* (its guid is the slug)."""
    from personalclaw.knowledge.artifact_ingest import ARTIFACT_ITEM_TYPE

    return knowledge_store().create_typed_item(
        item_type=ARTIFACT_ITEM_TYPE, title=title, content=title, guid=slug
    )


def report(name: str, **kw: Any) -> Any:
    """A scheduled report named *name*, saved."""
    from personalclaw.knowledge.research_reports import ReportDefinition, save_report
    from personalclaw.schedule import ScheduleDefinition

    return save_report(
        ReportDefinition(
            id=kw.pop("id", ""),
            name=name,
            prompt="What changed?",
            schedule=ScheduleDefinition(kind="every", every_secs=3600),
            **kw,
        )
    )


class ScriptedModel:
    """The model a report run calls, scripted: it answers *reply* and keeps each prompt."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def scripted_model(monkeypatch: Any, reply: str = "What changed [1].") -> ScriptedModel:
    from personalclaw.action_providers import knowledge_report_provider as krp

    model = ScriptedModel(reply)
    monkeypatch.setattr(krp, "_one_shot", model)
    return model


async def report_run(report_id: str) -> dict:
    """Run report *report_id* once, as its automation does, and answer what the run said."""
    from personalclaw.action_providers import knowledge_report_provider as krp
    from personalclaw.action_providers.base import ActionContext

    result = await krp.KnowledgeReportActionProvider().execute(
        {"report_id": report_id}, ActionContext(event="manual", payload={})
    )
    assert result.success, result.error
    return json.loads(result.stdout)


def key_points(item_id: str, *points: str) -> None:
    """Give item *item_id* the key points a reading of it wrote, as the ingest pass leaves them."""
    store = knowledge_store()
    store.db.execute(
        "UPDATE items SET insights = ? WHERE id = ?",
        (json.dumps({"key_points": list(points)}), item_id),
    )
    store.db.commit()


def conflict_pass(item_id: str) -> list[dict]:
    """The ingest pipeline's conflict pass over *item_id*, as a reading of it runs it."""
    from personalclaw.action_providers.knowledge_persist_provider import run_ingest_conflict_pass

    return run_ingest_conflict_pass(knowledge_store(), item_id)


def conflict_id(a: str, b: str) -> str:
    """The id of the pair (*a*, *b*), as the brief spells it."""
    import hashlib

    lo, hi = sorted((a, b))
    return hashlib.sha256(f"conflict:{lo}:{hi}".encode()).hexdigest()[:16]


def write_up(name: str, content: str) -> str:
    """A report's write-up as its runs keep it: the research-finding item of the report's name."""
    from personalclaw.knowledge.semantics import RESEARCH_FINDING_KIND, logical_key

    store = knowledge_store()
    item_id = store.create_typed_item(item_type="note", title=name, content=content)
    store.db.execute(
        "UPDATE items SET kind = ?, logical_key = ? WHERE id = ?",
        (RESEARCH_FINDING_KIND, logical_key(RESEARCH_FINDING_KIND, name), item_id),
    )
    return item_id


def intent(intent_id: str, goal: str, *findings: str) -> None:
    """An intent, and the findings it has kept, newest last."""
    from personalclaw.knowledge.intents import Intent, IntentStore

    store = knowledge_store()
    IntentStore(Path(store.db_path).parent / "intents.json").upsert(Intent(id=intent_id, goal=goal))
    for i, takeaway in enumerate(findings):
        store.record_intent_outcome(
            intent_id, intent_name=goal, item_id=f"src-{i}", takeaway=takeaway
        )


async def filed(
    title: str, project_id: str, *, policy: str | None = None, run_id: str = "r-1"
) -> str:
    """A knowledge item a run in *project_id* persisted: filed under the project, by its tag."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.knowledge_persist_provider import (
        KnowledgePersistActionProvider,
    )

    cfg: dict = {"title": title, "content": f"{title} body", "kind": "fact"}
    if policy is not None:
        cfg["sharing_policy"] = policy
    result = await KnowledgePersistActionProvider().execute(
        cfg,
        ActionContext(
            event="workflow_node",
            payload={"node_id": "n1", "run_id": run_id, "project_id": project_id},
        ),
    )
    assert result.success, result.error
    return json.loads(result.stdout)["item_id"]


def metadata(item_id: str) -> dict:
    row = (
        knowledge_store()
        .db.execute("SELECT file_metadata FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    return json.loads(row[0] or "{}") if row else {}


@asynccontextmanager
async def client(home: Path, *, knowledge: Any | None = None) -> AsyncIterator[TestClient]:
    """A test client over the library's routes, on loopback, for this test only."""
    from personalclaw.artifacts.handlers import register_artifact_routes
    from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes
    from personalclaw.dashboard.handlers.research_reports import setup_research_report_routes
    from personalclaw.tasks.handlers import register_task_routes

    with (
        patch("personalclaw.tasks.native.config_dir", return_value=home),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
    ):
        from personalclaw.knowledge_providers.native import create_native_provider

        store = knowledge or knowledge_store()
        queued: list[str] = []
        app = web.Application()
        app["state"] = SimpleNamespace(
            knowledge_store=store,
            _sessions={},
            knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=queued.append),
            knowledge_provider=lambda: create_native_provider(store, enqueue=queued.append),
        )
        register_task_routes(app)
        register_artifact_routes(app)
        setup_knowledge_routes(app)
        setup_research_report_routes(app)
        try:
            from personalclaw.dashboard.handlers.library import setup_library_routes
        except ImportError:  # main has no library read: the cases that need it are red there
            pass
        else:
            setup_library_routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1")) as c:
            yield c
