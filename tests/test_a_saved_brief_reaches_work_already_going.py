"""A brief saved on a project's page reaches the work already going in it, as the page says.

After a save, the page's line under the brief says a chat already going gets it the next time it
loads this project's context, and a loop already going when it resumes or starts its next stage.
This pins both halves on the gateway, so the line stays true:

- a paused goal loop resumed after the save, and a code session moved to its next stage, each
  write the saved brief into their own brief (``manager.write_brief``), and not the old one;
- ``GET /api/context`` for the project, read after the save as the ``get_context`` tool reads it,
  carries the saved brief in its rules.

It fails if a resume or a stage move stops rewriting the brief, or if the routed context stops
reading the project's brief as saved.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import context as context_handlers
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, manager
from personalclaw.loop import store as loops
from personalclaw.loop import tick
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.tasks.hierarchy import HierarchyStore

OLD = "Grow tomatoes on the south bed."
SAVED = "Grow beans on the north bed, and keep the south bed fallow."


def _garden() -> str:
    store = HierarchyStore()
    project = store.create_project("Garden")
    store.update_project(project.id, brief=OLD)
    return project.id


def _save(project_id: str) -> None:
    """The page's Save: the project's brief, written as the page's update writes it."""
    HierarchyStore().update_project(project_id, brief=SAVED)


def _brief(loop_id: str) -> str:
    return (loop_files.loop_dir(loop_id) / "brief.md").read_text()


class _State:
    """The slice of the dashboard's state the loop manager arms a worker through."""

    def __init__(self):
        from unittest.mock import MagicMock

        self._sessions: dict = {}
        self.sessions = MagicMock()

    def get_or_create_session(self, *, name, agent, model, workspace_dir, app, project_id=""):
        from personalclaw.dashboard.state import _ChatSession

        s = self._sessions.get(name)
        if s is None:
            s = _ChatSession(name, agent=agent, workspace_dir=workspace_dir, model=model)
            s._app = app
            self._sessions[name] = s
        return s

    def push_sessions_update(self):
        pass


class _Svc:
    """The nudge service the manager arms a loop's worker with."""

    def __init__(self):
        self._loops: dict = {}

    async def add(self, *, session_name, message, **_kw):
        lp = SimpleNamespace(id=f"N{len(self._loops) + 1}", session_name=session_name, active=True)
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)

    def list_all(self):
        return list(self._loops.values())

    async def update(self, loop_id, **kw):
        for k, v in kw.items():
            setattr(self._loops[loop_id], k, v)

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


class _StageCtx:
    """What a code session's stage move is given: no live worker, and its events kept."""

    def __init__(self):
        self.events: list[str] = []
        self.svc = SimpleNamespace(get_by_session=lambda _name: None)
        self.state = SimpleNamespace(_sessions={})

    def publish(self, loop_id, event, data=None):
        self.events.append(event)

    async def complete(self, loop_id, reason=""):
        raise AssertionError("the session had a next stage to move to")


@pytest.mark.asyncio
async def test_a_paused_goal_loop_resumed_after_the_save_reads_the_saved_brief():
    pid = _garden()
    loop = loops.create(
        Loop(id="", name="Plan the beds", kind="goal", task="plan the beds", project_id=pid,
             kind_config={"sub_goals": []})
    )  # fmt: skip
    manager.write_brief(loops.get(loop.id))
    assert OLD in _brief(loop.id), "the loop did not start with the project's brief"
    loops.update_status(loop.id, LoopStatus.PAUSED)

    _save(pid)
    try:
        await manager.start(_State(), _Svc(), loop.id)
    finally:
        manager._LOOP_GRANTS.clear()

    brief = _brief(loop.id)
    assert SAVED in brief, "a loop resumed after the save does not read the saved brief"
    assert OLD not in brief, "a loop resumed after the save kept the old brief"


@pytest.mark.asyncio
async def test_a_code_session_moved_to_its_next_stage_reads_the_saved_brief():
    pid = _garden()
    loop = loops.create(
        Loop(
            id="",
            name="Wire the sensors",
            kind="code",
            task="wire the bed sensors",
            project_id=pid,
            plan=[
                {
                    "stage": "implementation",
                    "title": "Implementation",
                    "exit_criteria": ["it reads"],
                },
                {"stage": "testing", "title": "Testing", "exit_criteria": ["it is tested"]},
            ],
            phase_status={"implementation": "active", "testing": "pending"},
        )
    )
    loop = loops.update_status(loop.id, LoopStatus.RUNNING)
    manager.write_brief(loops.get(loop.id))
    assert OLD in _brief(loop.id), "the session did not start with the project's brief"

    _save(pid)
    kinds.ensure_loaded()
    ctx = _StageCtx()
    done = await kinds.get("code")._apply_advance(
        loops.get(loop.id),
        0,
        "implementation",
        SimpleNamespace(action=tick.Action.ADVANCE),
        None,
        ctx,
    )

    assert (
        done is False and "stage_advance" in ctx.events
    ), "the session did not move to its next stage"
    brief = _brief(loop.id)
    assert SAVED in brief, "a session moved to its next stage does not read the saved brief"
    assert OLD not in brief, "a session moved to its next stage kept the old brief"


@pytest.mark.asyncio
async def test_the_context_a_chat_loads_after_the_save_carries_the_saved_brief(monkeypatch):
    """A chat already going loads the project's context through ``get_context``, which reads
    ``GET /api/context`` and hands its agent the rules: the saved brief, from the next read on."""
    monkeypatch.setattr(context_handlers, "_memory_service", lambda state, project: None)
    monkeypatch.setattr(context_handlers, "_knowledge_retriever", lambda: None)
    monkeypatch.setattr(context_handlers, "_skills_index", lambda query: [])
    pid = _garden()
    app = web.Application()
    app["state"] = None
    app.router.add_get("/api/context", context_handlers.api_context_get)

    async with TestClient(TestServer(app)) as client:
        before = await (await client.get(f"/api/context?project_id={pid}")).json()
        assert OLD in before["rules"], "the routed context did not read the project's brief"
        _save(pid)
        resp = await client.get(f"/api/context?project_id={pid}")
        assert resp.status == 200
        after = await resp.json()

    assert after["project_id"] == pid
    assert SAVED in after["rules"], "the context read after the save does not carry the saved brief"
    assert OLD not in after["rules"], "the context read after the save carries the old brief"
    assert SAVED in after["text"]
