"""HTTP-level tests for the task API: project/task-list CRUD, ready, search,
bulk ops, repeatable reset, and the exit-criteria complete-gate."""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes


@asynccontextmanager
async def _client(tmp_path):
    """A test client over the task routes with isolated filesystem stores."""
    registry._providers.clear()
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path), \
         patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


# ── Projects ──

@pytest.mark.asyncio
async def test_default_projects_listed(tmp_path):
    async with _client(tmp_path) as client:
        resp = await client.get("/api/projects")
        assert resp.status == 200
        names = {p["name"] for p in (await resp.json())["projects"]}
        assert {"Personal", "Repeatable"} <= names


@pytest.mark.asyncio
async def test_project_create_get_update_delete(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post("/api/projects", json={"name": "Website"})
        assert r.status == 201
        pid = (await r.json())["id"]
        assert (await client.get(f"/api/projects/{pid}")).status == 200
        r = await client.put(f"/api/projects/{pid}", json={"name": "Site"})
        assert (await r.json())["name"] == "Site"
        assert (await client.delete(f"/api/projects/{pid}")).status == 200
        assert (await client.get(f"/api/projects/{pid}")).status == 404


@pytest.mark.asyncio
async def test_project_linked_lists_bound_loops_and_code(tmp_path):
    # The /linked endpoint surfaces the Goal Loops + Code projects scoped under a
    # project (the integration payoff). Seed one of each bound to the project.
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop
    with patch("personalclaw.loop.store.config_dir", return_value=tmp_path):
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Effort"})).json())["id"]
            loop_store.create(Loop(id="", kind="goal", name="Loopy", task="g" * 30, project_id=pid))
            loop_store.create(Loop(id="", kind="code", name="Codey", task="t" * 20, tasks_project_id=pid))
            # an UNbound loop must NOT appear under this project
            loop_store.create(Loop(id="", kind="goal", name="Other", task="x" * 30))
            # a project-tied artifact surfaces under linked work too
            from personalclaw.artifacts.native import NativeArtifactProvider
            from personalclaw.artifacts import registry as art_reg
            prov = NativeArtifactProvider(root=tmp_path / "artifacts")
            prov.create(name="Spec", content="<p>x</p>", project_id=pid)
            with patch.object(art_reg, "get_provider", lambda name=None: prov):
                r = await client.get(f"/api/projects/{pid}/linked")
            assert r.status == 200
            body = await r.json()
            assert [l["name"] for l in body["loops"]] == ["Loopy"]
            assert [c["name"] for c in body["code"]] == ["Codey"]
            assert [a["name"] for a in body["artifacts"]] == ["Spec"]
            # each row carries error_message so the FE can tell a genuine 'complete'
            # from a budget-exhausted finish ('Ended early') — present (None when unset).
            assert "error_message" in body["loops"][0] and "error_message" in body["code"][0]


@pytest.mark.asyncio
async def test_project_linked_missing_404(tmp_path):
    async with _client(tmp_path) as client:
        assert (await client.get("/api/projects/p-nope/linked")).status == 404


@pytest.mark.asyncio
async def test_project_delete_blocked_by_bound_work_unless_forced(tmp_path):
    # Deleting a project with bound Goal Loops / Code must 409 (would orphan live work
    # + rmtree its worktrees) — unless ?force=true.
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop
    with patch("personalclaw.loop.store.config_dir", return_value=tmp_path):
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Busy"})).json())["id"]
            lp = loop_store.create(Loop(id="", kind="goal", name="L", task="g" * 30, project_id=pid))
            r = await client.delete(f"/api/projects/{pid}")
            assert r.status == 409
            body = await r.json()
            assert body["loops"] == 1 and body["code"] == 0
            # force deletes the project AND tears down the bound loop (no orphan with a
            # dangling project_id + leaked worktrees).
            assert (await client.delete(f"/api/projects/{pid}?force=true")).status == 200
            assert (await client.get(f"/api/projects/{pid}")).status == 404
            assert loop_store.get(lp.id) is None  # bound loop torn down, not orphaned


@pytest.mark.asyncio
async def test_project_delete_guard_counts_chats_and_force_unbinds_them(tmp_path):
    # Project-bound CHATS are first-class project work (surfaced in /linked), so the
    # delete-guard must count them too — and force-delete UNBINDS them (clears
    # project_id) rather than leaving them dangling at a gone project. Chats are the
    # user's conversations: detached, never deleted.
    class _FakeChat:
        def __init__(self, key, pid):
            self.key = key; self.project_id = pid; self._app = ""; self.title = key
    async with _client(tmp_path) as client:
        # inject a state with a project-bound chat onto the app the test client serves
        chat = _FakeChat("chat-1-999", None)
        client.app["state"] = type("S", (), {"_sessions": {}})()
        pid = (await (await client.post("/api/projects", json={"name": "Chatty"})).json())["id"]
        chat.project_id = pid
        client.app["state"]._sessions["chat-1-999"] = chat
        # plain delete → 409 naming the bound chat
        r = await client.delete(f"/api/projects/{pid}")
        assert r.status == 409
        body = await r.json()
        assert body["chats"] == 1 and body["loops"] == 0 and body["code"] == 0
        # force → project deleted AND the chat unbound (kept, project_id cleared)
        assert (await client.delete(f"/api/projects/{pid}?force=true")).status == 200
        assert (await client.get(f"/api/projects/{pid}")).status == 404
        assert chat.project_id == ""  # unbound, not deleted
        assert "chat-1-999" in client.app["state"]._sessions  # conversation preserved


@pytest.mark.asyncio
async def test_project_delete_clean_when_no_bound_work(tmp_path):
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Free"})).json())["id"]
        assert (await client.delete(f"/api/projects/{pid}")).status == 200


@pytest.mark.asyncio
async def test_project_payload_includes_context_dir_and_counts(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post("/api/projects", json={"name": "Rich", "workspace_dir": "/tmp/repo"})
        body = await r.json()
        # S2 enrichment: context_dir path, workspace_dir round-trip, task-list count.
        assert body["workspace_dir"] == "/tmp/repo"
        assert body["context_dir"].endswith(f"/projects/{body['id']}/context")
        assert body["task_list_count"] == 0
        # add a list → the list endpoint reflects the count
        await client.post("/api/task-lists", json={"name": "L1", "project_id": body["id"]})
        projects = (await (await client.get("/api/projects")).json())["projects"]
        rich = next(p for p in projects if p["id"] == body["id"])
        assert rich["task_list_count"] == 1 and "context_dir" in rich


@pytest.mark.asyncio
async def test_project_duplicate_name_400(tmp_path):
    async with _client(tmp_path) as client:
        await client.post("/api/projects", json={"name": "Dup"})
        r = await client.post("/api/projects", json={"name": "Dup"})
        assert r.status == 400


@pytest.mark.asyncio
async def test_default_project_undeletable(tmp_path):
    async with _client(tmp_path) as client:
        projects = (await (await client.get("/api/projects")).json())["projects"]
        personal = next(p for p in projects if p["name"] == "Personal")
        assert (await client.delete(f"/api/projects/{personal['id']}")).status == 400


# ── Task lists ──

@pytest.mark.asyncio
async def test_task_list_routes_to_personal_by_default(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (await client.post("/api/task-lists", json={"name": "Misc"})).json()
        projects = (await (await client.get("/api/projects")).json())["projects"]
        personal = next(p for p in projects if p["name"] == "Personal")
        assert tl["project_id"] == personal["id"]


@pytest.mark.asyncio
async def test_task_list_under_project_and_filter(tmp_path):
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "P"})).json())["id"]
        await client.post("/api/task-lists", json={"name": "L1", "project_id": pid})
        await client.post("/api/task-lists", json={"name": "L2", "project_id": pid})
        r = await client.get(f"/api/task-lists?project_id={pid}")
        assert len((await r.json())["task_lists"]) == 2


# ── Create: project_id → find-or-create "General" list ──

@pytest.mark.asyncio
async def test_create_with_project_id_attaches_general_list(tmp_path):
    # A task created with a project choice but no explicit list must land on
    # that project's "General" list (created on demand) so the project label
    # sticks; a second create reuses the same list. An explicit task_list_id
    # always wins over project_id.
    async with _client(tmp_path) as client:
        pid = (await (await client.post("/api/projects", json={"name": "Proj"})).json())["id"]
        t1 = await (await client.post("/api/tasks", json={"title": "T1", "project_id": pid})).json()
        assert t1["project"] == "Proj"
        assert t1["task_list_id"]
        t2 = await (await client.post("/api/tasks", json={"title": "T2", "project_id": pid})).json()
        assert t2["task_list_id"] == t1["task_list_id"]
        lists = await (await client.get(f"/api/task-lists?project_id={pid}")).json()
        assert [tl["name"] for tl in lists["task_lists"]] == ["General"]
        named = await (await client.post("/api/task-lists", json={"name": "Named", "project_id": pid})).json()
        t3 = await (await client.post("/api/tasks", json={
            "title": "T3", "project_id": pid, "task_list_id": named["id"]})).json()
        assert t3["task_list_id"] == named["id"]


# ── Ready / search ──

@pytest.mark.asyncio
async def test_ready_excludes_blocked_then_includes_after_done(tmp_path):
    async with _client(tmp_path) as client:
        a = await (await client.post("/api/tasks", json={"title": "A"})).json()
        b = await (await client.post("/api/tasks", json={
            "title": "B",
            "dependencies": [{"depends_on_task_id": a["id"], "dependency_type": "BLOCKS"}],
        })).json()
        ready_ids = {t["id"] for t in (await (await client.get("/api/tasks/ready")).json())["tasks"]}
        assert a["id"] in ready_ids
        assert b["id"] not in ready_ids
        await client.put(f"/api/tasks/{a['id']}", json={"status": "done"})
        ready_ids = {t["id"] for t in (await (await client.get("/api/tasks/ready")).json())["tasks"]}
        assert b["id"] in ready_ids


@pytest.mark.asyncio
async def test_search_query_and_priority_filter(tmp_path):
    async with _client(tmp_path) as client:
        await client.post("/api/tasks", json={"title": "Migrate database schema", "priority": "critical"})
        await client.post("/api/tasks", json={"title": "Write docs", "priority": "low"})
        body = await (await client.post("/api/tasks/search", json={"query": "database"})).json()
        assert body["total"] == 1
        body = await (await client.post("/api/tasks/search", json={"priority": ["critical"]})).json()
        assert body["total"] == 1
        assert body["tasks"][0]["priority"] == "critical"


# ── Comment count (surfaced for the comment badge) ──

@pytest.mark.asyncio
async def test_comment_count_in_list_and_get(tmp_path):
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "Has comments"})).json()
        # no comments yet → 0
        listed = await (await client.get("/api/tasks")).json()
        assert listed["tasks"][0]["comment_count"] == 0
        assert (await (await client.get(f"/api/tasks/{t['id']}")).json())["comment_count"] == 0
        # add two comments → count reflects them in both list and single-get
        await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "one"})
        await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "two"})
        listed = await (await client.get("/api/tasks")).json()
        assert listed["tasks"][0]["comment_count"] == 2
        assert (await (await client.get(f"/api/tasks/{t['id']}")).json())["comment_count"] == 2


# ── Bulk ──

@pytest.mark.asyncio
async def test_bulk_create(tmp_path):
    async with _client(tmp_path) as client:
        body = await (await client.post("/api/tasks/bulk", json={
            "op": "create", "items": [{"title": "A"}, {"title": "B"}]})).json()
        assert body["succeeded"] == 2
        assert (await (await client.get("/api/tasks")).json())["total"] == 2


@pytest.mark.asyncio
async def test_bulk_validate_all_aborts(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post("/api/tasks/bulk", json={
            "op": "create", "items": [{"title": "A"}, {"title": ""}]})
        assert r.status == 400
        assert (await (await client.get("/api/tasks")).json())["total"] == 0


# ── Repeatable reset ──

@pytest.mark.asyncio
async def test_reset_requires_repeatable_project(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (await client.post("/api/task-lists", json={"name": "L"})).json()
        assert (await client.post(f"/api/task-lists/{tl['id']}/reset", json={})).status == 400


@pytest.mark.asyncio
async def test_reset_repeatable_list(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (await client.post("/api/task-lists", json={"name": "Weekly", "repeatable": True})).json()
        t = await (await client.post("/api/tasks", json={"title": "step", "task_list_id": tl["id"]})).json()
        await client.put(f"/api/tasks/{t['id']}", json={"status": "done"})
        assert (await client.post(f"/api/task-lists/{tl['id']}/reset", json={})).status == 200
        reloaded = await (await client.get(f"/api/tasks/{t['id']}")).json()
        assert reloaded["status"] == "open"


@pytest.mark.asyncio
async def test_reset_blocked_when_incomplete(tmp_path):
    async with _client(tmp_path) as client:
        tl = await (await client.post("/api/task-lists", json={"name": "Weekly", "repeatable": True})).json()
        await client.post("/api/tasks", json={"title": "step", "task_list_id": tl["id"]})
        assert (await client.post(f"/api/task-lists/{tl['id']}/reset", json={})).status == 400


# ── Exit-criteria complete gate ──

@pytest.mark.asyncio
async def test_done_blocked_by_incomplete_criteria(tmp_path):
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={
            "title": "Ship", "exit_criteria": [{"description": "tests pass", "met": False}]})).json()
        r = await client.put(f"/api/tasks/{t['id']}", json={"status": "done"})
        assert r.status == 400
        assert "exit criteria" in (await r.json())["error"]


@pytest.mark.asyncio
async def test_invalid_status_is_400_not_silent_noop(tmp_path):
    """PUT with a bad status ("completed" is the natural guess — the board column
    is even labeled Completed) must 400 with the valid set named, not 200 with the
    task silently unchanged."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "A"})).json()
        r = await client.put(f"/api/tasks/{t['id']}", json={"status": "completed"})
        assert r.status == 400
        err = (await r.json())["error"]
        assert "completed" in err and "done" in err  # names the fix
        # And the task is untouched.
        got = await (await client.get(f"/api/tasks/{t['id']}")).json()
        assert got["status"] == "open"
