"""A project a loop made for itself says so, until the owner makes it theirs.

A code session started on no project, and a goal loop whose plan's sub-goals are split into tasks
with no project chosen, each make a project of their own (`tasks_link.ensure_project`). The
Projects list folds those away under "Made for goal loops", so the project file carries the loop
it was made for, ``made_for: "loop:<id>"``, and the reads serve it as
``made_for: {work, kind, name}`` with the loop's own kind and name.

What these pin:

* launching a code session with no project marks the project it makes; one launched into a
  chosen project marks nothing;
* a goal loop with no project gets its own when its sub-goals are split, marked, through its
  plan's finalize too; one whose plan has no sub-goals makes none;
* renaming it, locking its name, writing its brief, starting a chat in it, or starting another
  loop of any kind in it by choice clears the mark; an empty brief, an archive, the same loop's
  second launch, and an Incognito, throwaway or app's chat do not; a name that opens a kept chat
  clears that chat's project, not the one the body names;
* nothing a caller sends carries it: a ``PUT`` naming it is refused, and a create body, an
  imported archive and the agent's ``project_create`` all make a project with none;
* the read serves the loop's kind and name, ``""`` for both once the loop is deleted, which
  clears nothing, and a ``None`` kind when the loops cannot be read;
* projects made before the mark existed get it once, at the gateway's boot, by the same rule read
  off the loops: an old project a code session made reads as made for it, and one the owner
  renamed (``name_locked``), gave a brief, or chose for a loop (``project_id``) reads ``null``,
  as a built-in one does; the oldest loop wins; a file it cannot write is passed over for the next
  boot; a second boot writes nothing; a read never writes.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.loop import store as loop_store
from personalclaw.loop import tasks_link
from personalclaw.loop.loop import Loop
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes
from personalclaw.tasks.hierarchy import HierarchyStore


@contextmanager
def _home(tmp_path):
    """Every store a loop's project touches, over one temp home."""
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
        patch("personalclaw.providers.entity_routes.config_dir", return_value=tmp_path),
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.store.config_dir", return_value=tmp_path),
        patch("personalclaw.workflows.leases.config_dir", return_value=tmp_path),
        patch("personalclaw.concurrency.config_dir", return_value=tmp_path),
        patch("personalclaw.loop.files.config_dir", return_value=tmp_path),
        patch("personalclaw.dashboard.state.config_dir", return_value=tmp_path),
    ):
        yield
    registry._providers.clear()


@asynccontextmanager
async def _client(tmp_path):
    with _home(tmp_path):
        from personalclaw.dashboard.request_boundary import request_boundary_middleware

        app = web.Application(middlewares=[request_boundary_middleware()])
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


async def _json(resp):
    assert resp.status in (200, 201), await resp.text()
    return await resp.json()


async def _row(client, pid: str) -> dict:
    rows = (await _json(await client.get("/api/projects")))["projects"]
    (row,) = [r for r in rows if r["id"] == pid]
    return row


def _stage(stage: str) -> dict:
    return {"stage": stage, "title": stage.title(), "objective": f"do {stage}", "exit_criteria": []}


def _code_session(name: str = "Fix the gate", **over) -> Loop:
    fields = dict(
        id="",
        name=name,
        kind="code",
        task=name.lower(),
        plan=[_stage("design"), _stage("implementation")],
        kind_config={"entry_stage": "design"},
    )
    fields.update(over)
    return loop_store.create(Loop(**fields))


def _goal_loop(name: str = "Weekly reading digest", sub_goals=("Pick the feeds",), **over) -> Loop:
    fields = dict(
        id="", name=name, kind="goal", task=name.lower(), kind_config={"sub_goals": list(sub_goals)}
    )
    fields.update(over)
    return loop_store.create(Loop(**fields))


def _launched(loop: Loop) -> str:
    out = tasks_link.provision(loop.id)
    assert out is not None and out.tasks_project_id
    return out.tasks_project_id


def _made_for(pid: str):
    return HierarchyStore().get_project(pid).made_for


# ── the mark, stamped where a loop makes its own project ──


@pytest.mark.asyncio
async def test_a_code_session_on_no_project_marks_the_project_it_makes(tmp_path):
    async with _client(tmp_path) as client:
        loop = _code_session()
        pid = _launched(loop)
        assert _made_for(pid) == f"loop:{loop.id}"
        want = {"work": f"loop:{loop.id}", "kind": "code", "name": "Fix the gate"}
        assert (await _row(client, pid))["made_for"] == want
        assert (await _json(await client.get(f"/api/projects/{pid}")))["made_for"] == want


@pytest.mark.asyncio
async def test_a_code_session_in_a_chosen_project_marks_nothing(tmp_path):
    async with _client(tmp_path) as client:
        garden = (await _json(await client.post("/api/projects", json={"name": "Garden"})))["id"]
        loop = _code_session(project_id=garden)
        assert _launched(loop) == garden
        assert _made_for(garden) is None
        assert (await _row(client, garden))["made_for"] is None


@pytest.mark.asyncio
async def test_a_goal_loop_gets_its_own_project_when_its_sub_goals_are_split(tmp_path):
    async with _client(tmp_path) as client:
        loop = _goal_loop()
        assert await tasks_link.decompose_sub_goals(loop.id)
        pid = loop_store.get(loop.id).tasks_project_id
        assert pid and _made_for(pid) == f"loop:{loop.id}"
        assert (await _row(client, pid))["made_for"] == {
            "work": f"loop:{loop.id}",
            "kind": "goal",
            "name": "Weekly reading digest",
        }
        bare = _goal_loop(name="No steps", sub_goals=())
        assert await tasks_link.decompose_sub_goals(bare.id) == []
        assert loop_store.get(bare.id).tasks_project_id == "", "no sub-goals, no project"


def _planned(loop_id: str, sub_goals: list[str]) -> None:
    """The walkthrough's approved sub-goals step, as the planner leaves it before finalize."""
    import time

    from personalclaw.loop import files as loop_files
    from personalclaw.planning import session as PS

    sess = PS.PlanSession(project_id=loop_id, created_at=time.time())
    step = PS.PlanStep(id="step-1", kind="sub_goals", title="Sub-goals")
    step.status = PS.StepStatus.APPROVED.value
    step.artifact = {"sub_goals": sub_goals}
    sess.steps = [step]
    loop_files.write_plan_session(sess)


@pytest.mark.asyncio
async def test_a_goal_loops_finalized_plan_makes_its_marked_project_only_with_sub_goals(tmp_path):
    from personalclaw.loop import plan_walkthrough

    with _home(tmp_path):
        loop = _goal_loop(sub_goals=())
        _planned(loop.id, ["Pick the feeds", "Write the digest"])
        assert await plan_walkthrough.finalize_plan(loop.id) is True
        pid = loop_store.get(loop.id).tasks_project_id
        assert pid and _made_for(pid) == f"loop:{loop.id}"

        bare = _goal_loop(name="No steps", sub_goals=())
        _planned(bare.id, [])
        assert await plan_walkthrough.finalize_plan(bare.id) is True
        assert loop_store.get(bare.id).tasks_project_id == "", "no sub-goals, no project"


# ── the mark, cleared once the owner makes the project theirs ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{"name": "The gate"}, {"name_locked": True}, {"brief": "Keep the gate shut."}],
    ids=["a rename", "a locked name", "a brief"],
)
async def test_a_put_that_makes_it_the_owners_clears_the_mark(tmp_path, body):
    async with _client(tmp_path) as client:
        pid = _launched(_code_session())
        await _json(await client.put(f"/api/projects/{pid}", json=body))
        assert _made_for(pid) is None
        assert (await _row(client, pid))["made_for"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{"brief": ""}, {"name_locked": False}, {"status": "archived"}],
    ids=["an empty brief", "an unlocked name", "an archive"],
)
async def test_a_put_that_leaves_it_the_loops_keeps_the_mark(tmp_path, body):
    async with _client(tmp_path) as client:
        loop = _code_session()
        pid = _launched(loop)
        await _json(await client.put(f"/api/projects/{pid}", json=body))
        assert _made_for(pid) == f"loop:{loop.id}"


@pytest.mark.asyncio
async def test_a_chat_started_in_it_clears_the_mark(tmp_path):
    from chat_test_helpers import _make_app_with_agent_routes, _make_state

    with _home(tmp_path):
        pid = _launched(_code_session())
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as client:
            r = await client.post("/api/chat/sessions", json={"project_id": pid})
            assert r.status == 200, await r.text()
        assert _made_for(pid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,app",
    [({"memory_mode": "incognito"}, ""), ({"ephemeral": True}, ""), ({}, "garden-helper")],
    ids=["an Incognito chat", "a throwaway chat", "an app's chat"],
)
async def test_a_chat_that_is_not_the_owners_own_leaves_the_mark(tmp_path, body, app):
    from chat_test_helpers import _make_app_with_agent_routes, _make_state

    @web.middleware
    async def as_an_app(request, handler):
        if app:
            request["app"] = app
        return await handler(request)

    with _home(tmp_path):
        loop = _code_session()
        pid = _launched(loop)
        served = _make_app_with_agent_routes(_make_state(tmp_path))
        served.middlewares.append(as_an_app)
        async with TestClient(TestServer(served)) as client:
            r = await client.post("/api/chat/sessions", json={"project_id": pid, **body})
            assert r.status == 200, await r.text()
        assert _made_for(pid) == f"loop:{loop.id}"


@pytest.mark.asyncio
async def test_a_name_that_opens_a_kept_chat_clears_that_chats_project_not_the_bodys(tmp_path):
    from chat_test_helpers import _make_app_with_agent_routes, _make_state

    with _home(tmp_path):
        loop = _code_session()
        made = _launched(loop)
        garden = HierarchyStore().create_project("Garden").id
        async with TestClient(TestServer(_make_app_with_agent_routes(_make_state(tmp_path)))) as c:
            r = await c.post("/api/chat/sessions", json={"name": "Notes", "project_id": garden})
            assert r.status == 200, await r.text()
            r = await c.post("/api/chat/sessions", json={"name": "Notes", "project_id": made})
            assert r.status == 200, await r.text()
        assert _made_for(made) == f"loop:{loop.id}", "the kept chat is Garden's, not this project's"


@pytest.mark.asyncio
async def test_a_name_that_opens_a_kept_chat_gives_it_no_other_projects_context(tmp_path):
    from chat_test_helpers import _make_app_with_agent_routes, _make_state

    with _home(tmp_path):
        store = HierarchyStore()
        garden = store.create_project("Garden").id
        taxes = store.create_project("Taxes").id
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_app_with_agent_routes(state))) as c:
            r = await c.post("/api/chat/sessions", json={"name": "Notes", "project_id": garden})
            assert r.status == 200, await r.text()
            r = await c.post("/api/chat/sessions", json={"name": "Notes", "project_id": taxes})
            assert r.status == 200, await r.text()
        roots = state.get_or_create_session("Notes")._extra_tool_roots or []
        assert str(store.context_dir(garden)) in roots, "its own project's context"
        assert str(store.context_dir(taxes)) not in roots


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
        from types import SimpleNamespace

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,fields",
    [
        ("research", {"task": "compare drip lines"}),
        ("goal", {"task": "tidy the beds", "kind_config": {"sub_goals": []}}),
    ],
    ids=["a research loop", "a goal loop with no sub-goals"],
)
async def test_a_loop_of_any_kind_started_into_it_by_choice_clears_the_mark(tmp_path, kind, fields):
    from personalclaw.loop import manager

    with _home(tmp_path):
        pid = _launched(_code_session())
        other = loop_store.create(Loop(id="", name="Another", kind=kind, project_id=pid, **fields))
        try:
            await manager.start(_State(), _Svc(), other.id)
        finally:
            manager._LOOP_GRANTS.clear()
        assert _made_for(pid) is None


def test_another_loop_launched_into_it_clears_the_mark_and_its_own_second_launch_does_not(
    tmp_path,
):
    with _home(tmp_path):
        first = _code_session()
        pid = _launched(first)
        _launched(loop_store.get(first.id))
        assert _made_for(pid) == f"loop:{first.id}", "its own loop's relaunch is not a choice"
        second = _code_session(name="Oil the hinge", project_id=pid)
        assert _launched(second) == pid
        assert _made_for(pid) is None


# ── nothing a caller sends carries it ──


@pytest.mark.asyncio
async def test_a_put_cannot_set_it(tmp_path):
    async with _client(tmp_path) as client:
        garden = (await _json(await client.post("/api/projects", json={"name": "Garden"})))["id"]
        r = await client.put(f"/api/projects/{garden}", json={"made_for": "loop:abcd1234"})
        assert r.status == 400, await r.text()
        assert _made_for(garden) is None


@pytest.mark.asyncio
async def test_a_create_body_an_import_and_project_create_never_carry_it(tmp_path):
    import aiohttp

    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.workflows import project_archive as pa
    from personalclaw.workflows.project_export import plan_export

    async with _client(tmp_path) as client:
        made = await _json(
            await client.post("/api/projects", json={"name": "Garden", "made_for": "loop:abcd1234"})
        )
        assert made["made_for"] is None and _made_for(made["id"]) is None

        record = json.dumps({"name": "Seeds", "made_for": "loop:abcd1234"}).encode()
        files = {"project.json": record, "context/overview.md": b"# Overview"}
        archive = pa.write_archive(plan_export("p-src", project_name="Seeds", files=files), files)
        form = aiohttp.FormData()
        form.add_field("file", archive, filename="seeds.zip", content_type="application/zip")
        imported = await _json(await client.post("/api/projects/import", data=form))
        assert _made_for(imported["project_id"]) is None

        ws = tmp_path / "ws"
        ws.mkdir()
        tool = await NativeBuiltinToolProvider(ws).invoke(
            "project_create", {"name": "Beds", "made_for": "loop:abcd1234"}
        )
        assert tool.success, tool.error
        assert HierarchyStore().get_project_by_name("Beds").made_for is None


# ── the read ──


@pytest.mark.asyncio
async def test_once_the_loop_is_deleted_the_mark_stays_and_reads_no_kind_or_name(tmp_path):
    async with _client(tmp_path) as client:
        loop = _code_session()
        pid = _launched(loop)
        assert loop_store.delete(loop.id)
        assert _made_for(pid) == f"loop:{loop.id}", "deleting the loop clears nothing"
        want = {"work": f"loop:{loop.id}", "kind": "", "name": ""}
        assert (await _row(client, pid))["made_for"] == want
        assert (await _json(await client.get(f"/api/projects/{pid}")))["made_for"] == want


@pytest.mark.asyncio
async def test_a_loop_store_that_cannot_be_read_serves_the_kind_as_unknown(tmp_path, monkeypatch):
    def unreadable(*_a, **_k):
        raise OSError("the loops cannot be read")

    async with _client(tmp_path) as client:
        loop = _code_session()
        pid = _launched(loop)
        monkeypatch.setattr(loop_store, "list_all", unreadable)
        monkeypatch.setattr(loop_store, "get", unreadable)
        want = {"work": f"loop:{loop.id}", "kind": None, "name": ""}
        assert (await _row(client, pid))["made_for"] == want
        assert (await _json(await client.get(f"/api/projects/{pid}")))["made_for"] == want


# ── projects made before the mark: once, at the gateway's boot ──


def _old_project(tmp_path, pid: str, name: str, **fields) -> None:
    """A project file as the gateway wrote it before the mark existed: no `made_for` key."""
    folder = tmp_path / "projects" / pid
    folder.mkdir(parents=True)
    record = {"id": pid, "name": name, "status": "active", "name_locked": False, "brief": ""}
    record.update(fields)
    (folder / "project.json").write_text(json.dumps(record))


def _raw(tmp_path, pid: str) -> dict:
    return json.loads((tmp_path / "projects" / pid / "project.json").read_text())


def _upgraded_home(tmp_path) -> dict[str, str]:
    """Old project files, and the loops that made or chose them. Returns each loop's id."""
    _old_project(tmp_path, "p-0000000a", "Fix the gate")
    _old_project(tmp_path, "p-0000000b", "The gate, renamed", name_locked=True)
    _old_project(tmp_path, "p-0000000c", "Briefed", brief="Keep the gate shut.")
    _old_project(tmp_path, "p-0000000d", "Chosen later")
    _old_project(tmp_path, "p-0000000e", "Personal", is_builtin=True)
    _old_project(tmp_path, "p-0000000f", "Two loops")
    _old_project(tmp_path, "p-00000010", "Nobody's")
    ids: dict[str, str] = {}
    for key, pid, chosen, at in (
        ("a", "p-0000000a", "", 100.0),
        ("b", "p-0000000b", "", 100.0),
        ("c", "p-0000000c", "", 100.0),
        ("d", "p-0000000d", "", 100.0),
        ("d2", "", "p-0000000d", 200.0),
        ("e", "p-0000000e", "", 100.0),
        ("f-new", "p-0000000f", "", 300.0),
        ("f-old", "p-0000000f", "", 50.0),
    ):
        loop = _code_session(name=f"Loop {key}", project_id=chosen, created_at=at)
        if pid:
            loop_store.set_tasks_links(loop.id, tasks_project_id=pid, task_list_ids={})
        ids[key] = loop.id
    return ids


def test_the_boot_gives_each_old_project_its_mark_once(tmp_path, monkeypatch):
    from personalclaw.projects import backfill_made_for

    with _home(tmp_path):
        ids = _upgraded_home(tmp_path)
        backfill_made_for()
        assert _raw(tmp_path, "p-0000000a")["made_for"] == f"loop:{ids['a']}"
        assert _raw(tmp_path, "p-0000000b")["made_for"] is None, "renamed by the owner"
        assert _raw(tmp_path, "p-0000000c")["made_for"] is None, "briefed by the owner"
        assert _raw(tmp_path, "p-0000000d")["made_for"] is None, "chosen for a loop"
        assert _raw(tmp_path, "p-0000000e")["made_for"] is None, "built in"
        assert _raw(tmp_path, "p-0000000f")["made_for"] == f"loop:{ids['f-old']}", "the oldest"
        assert _raw(tmp_path, "p-00000010")["made_for"] is None, "no loop made it"
        assert _raw(tmp_path, "p-0000000a")["name"] == "Fix the gate", "the rest is as written"

        before = {p: (p / "project.json").read_bytes() for p in (tmp_path / "projects").iterdir()}
        _old_project(tmp_path, "p-00000011", "Made after the first boot")
        loop = _code_session(name="Loop g")
        loop_store.set_tasks_links(loop.id, tasks_project_id="p-00000011", task_list_ids={})
        from personalclaw import projects as projects_svc

        writes: list[str] = []
        real = projects_svc.atomic_json_write
        monkeypatch.setattr(
            projects_svc,
            "atomic_json_write",
            lambda path, data: (writes.append(str(path)), real(path, data)),
        )
        backfill_made_for()
        assert writes == [
            str(tmp_path / "projects" / "p-00000011" / "project.json")
        ], "a second boot writes only the file still without the key"
        for folder, raw in before.items():
            assert (folder / "project.json").read_bytes() == raw


def test_a_file_the_boot_cannot_write_is_passed_over_and_the_rest_are_written(
    tmp_path, monkeypatch
):
    from personalclaw import projects as projects_svc
    from personalclaw.projects import backfill_made_for

    with _home(tmp_path):
        ids = _upgraded_home(tmp_path)
        stuck = str(tmp_path / "projects" / "p-0000000a" / "project.json")
        real = projects_svc.atomic_json_write

        def write(path, data):
            if str(path) == stuck:
                raise OSError("read-only")
            return real(path, data)

        monkeypatch.setattr(projects_svc, "atomic_json_write", write)
        assert backfill_made_for() == 6, "the six it could write"
        assert "made_for" not in _raw(tmp_path, "p-0000000a"), "a later boot decides it"
        assert _raw(tmp_path, "p-0000000f")["made_for"] == f"loop:{ids['f-old']}"


def test_an_unreadable_loop_store_writes_nothing_so_a_later_boot_decides(tmp_path, monkeypatch):
    from personalclaw.projects import backfill_made_for

    with _home(tmp_path):
        _upgraded_home(tmp_path)

        def unreadable():
            raise OSError("the loops cannot be read")

        monkeypatch.setattr(loop_store, "list_all", unreadable)
        backfill_made_for()
        assert "made_for" not in _raw(tmp_path, "p-0000000a")


@pytest.mark.asyncio
async def test_a_read_never_writes_the_mark(tmp_path):
    async with _client(tmp_path) as client:
        _upgraded_home(tmp_path)
        before = (tmp_path / "projects" / "p-0000000a" / "project.json").read_bytes()
        assert (await _row(client, "p-0000000a"))["made_for"] is None
        await _json(await client.get("/api/projects/p-0000000a"))
        assert (tmp_path / "projects" / "p-0000000a" / "project.json").read_bytes() == before


def test_the_gateway_boot_runs_it(tmp_path, monkeypatch):
    from personalclaw import cli_server

    ran: list[bool] = []
    monkeypatch.setattr("personalclaw.projects.backfill_made_for", lambda: ran.append(True))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    cli_server._boot_config()
    assert ran == [True]
