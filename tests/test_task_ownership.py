"""Tests for mine-vs-everyone task ownership (TEAM-SHARED-ENTITIES S2/§2.1).

The load-bearing guarantee: a multi-tenant provider may return tasks assigned to
other people, and those must NEVER be counted or picked as the owner's work. Ready
counts, "next task" pickers, and the agent's own work selection all flow through
`ready_tasks`, so that is where the filter lives and where these tests aim.
"""

import asyncio

import pytest

from personalclaw.tasks.models import Task


def _task(**kw) -> Task:
    return Task(id=kw.pop("id", "t1"), title=kw.pop("title", "a task"), **kw)


class TestBelongsTo:
    def test_assignee_decides_when_set(self):
        assert _task(assignee="keyur").belongs_to("keyur") is True
        assert _task(assignee="dana").belongs_to("keyur") is False

    def test_assignee_overrides_author_in_both_directions(self):
        """Who DOES it beats who wrote it — that's what assignment means."""
        assert _task(author="dana", assignee="keyur").belongs_to("keyur") is True
        assert _task(author="keyur", assignee="dana").belongs_to("keyur") is False

    def test_unassigned_falls_back_to_author(self):
        assert _task(author="keyur").belongs_to("keyur") is True
        assert _task(author="dana").belongs_to("keyur") is False

    def test_unattributed_tasks_are_the_owners(self):
        """Every task written before attribution existed has neither field; treating
        those as foreign would empty the counters on upgrade."""
        assert _task().belongs_to("keyur") is True

    def test_no_configured_username_means_everything_is_mine(self):
        """A single-user install must behave exactly as it does today."""
        assert _task(assignee="dana").belongs_to("") is True
        assert _task(author="dana").belongs_to("   ") is True

    def test_comparison_ignores_case_and_padding(self):
        assert _task(assignee="KEYUR").belongs_to("keyur") is True
        assert _task(assignee="  keyur  ").belongs_to("KEYUR") is True


class TestReadyTasksFiltering:
    """`ready_tasks` is the one funnel every work-selection path shares."""

    @pytest.fixture(autouse=True)
    def _own_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        yield

    def _patch(self, monkeypatch, tasks, owner="keyur"):
        from personalclaw.tasks import registry

        async def _list(**kwargs):
            return list(tasks), len(tasks)

        monkeypatch.setattr(registry, "list_all_tasks", _list)
        monkeypatch.setattr("personalclaw.identity.current_username", lambda: owner)

    def test_foreign_tasks_are_never_the_owners_ready_work(self, monkeypatch):
        from personalclaw.tasks import registry

        self._patch(
            monkeypatch,
            [
                _task(id="mine", assignee="keyur"),
                _task(id="theirs", assignee="dana"),
                _task(id="unclaimed"),
            ],
        )
        ids = {t.id for t in asyncio.run(registry.ready_tasks())}
        assert ids == {"mine", "unclaimed"}

    def test_everyone_view_is_opt_in(self, monkeypatch):
        from personalclaw.tasks import registry

        self._patch(
            monkeypatch,
            [_task(id="mine", assignee="keyur"), _task(id="theirs", assignee="dana")],
        )
        ids = {t.id for t in asyncio.run(registry.ready_tasks(mine_only=False))}
        assert ids == {"mine", "theirs"}

    def test_no_username_returns_everything(self, monkeypatch):
        from personalclaw.tasks import registry

        self._patch(
            monkeypatch,
            [_task(id="a", assignee="dana"), _task(id="b", assignee="sam")],
            owner="",
        )
        assert len(asyncio.run(registry.ready_tasks())) == 2

    def test_readiness_is_computed_over_the_full_set(self, monkeypatch):
        """A task of mine blocked by a colleague's unfinished prerequisite is NOT
        ready. Filtering before reconciliation would call it startable."""
        from personalclaw.tasks import registry
        from personalclaw.tasks.models import TaskDependency

        blocker = _task(id="theirs", assignee="dana", status="open")
        blocked = _task(
            id="mine",
            assignee="keyur",
            dependencies=[TaskDependency(depends_on_task_id="theirs")],
        )
        self._patch(monkeypatch, [blocker, blocked])
        ids = {t.id for t in asyncio.run(registry.ready_tasks())}
        assert "mine" not in ids, "a foreign blocker must still block my task"

    def test_the_agents_next_task_tool_inherits_the_filter(self):
        """The agent must not pick up someone else's work. It calls ready_tasks with
        no kwargs, so the owner-only default IS the guarantee."""
        import inspect

        from personalclaw.tasks import registry

        signature = inspect.signature(registry.ready_tasks)
        assert signature.parameters["mine_only"].default is True


class TestListEndpoint:
    @pytest.fixture(autouse=True)
    def _own_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        yield

    def _app(self, tasks, owner="keyur", monkeypatch=None):
        from aiohttp import web

        from personalclaw.tasks import handlers, registry

        async def _list(**kwargs):
            return list(tasks), len(tasks)

        monkeypatch.setattr(registry, "list_all_tasks", _list)
        monkeypatch.setattr("personalclaw.identity.current_username", lambda: owner)
        app = web.Application()
        app.router.add_get("/api/tasks", handlers.api_tasks_list)
        return app

    @pytest.mark.asyncio
    async def test_default_shows_everyone(self, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        tasks = [_task(id="mine", assignee="keyur"), _task(id="theirs", assignee="dana")]
        async with TestClient(TestServer(self._app(tasks, monkeypatch=monkeypatch))) as client:
            body = await (await client.get("/api/tasks")).json()
        assert {t["id"] for t in body["tasks"]} == {"mine", "theirs"}

    @pytest.mark.asyncio
    async def test_mine_narrows_to_the_owner(self, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        tasks = [_task(id="mine", assignee="keyur"), _task(id="theirs", assignee="dana")]
        async with TestClient(TestServer(self._app(tasks, monkeypatch=monkeypatch))) as client:
            body = await (await client.get("/api/tasks?mine=1")).json()
        assert {t["id"] for t in body["tasks"]} == {"mine"}

    @pytest.mark.asyncio
    async def test_total_describes_the_filtered_set(self, monkeypatch):
        """Otherwise the UI shows "1 of 2" for a list holding one row."""
        from aiohttp.test_utils import TestClient, TestServer

        tasks = [_task(id="mine", assignee="keyur"), _task(id="theirs", assignee="dana")]
        async with TestClient(TestServer(self._app(tasks, monkeypatch=monkeypatch))) as client:
            body = await (await client.get("/api/tasks?mine=1")).json()
        assert body["total"] == 1

    @pytest.mark.asyncio
    async def test_owner_is_reported_so_rows_can_be_labelled(self, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app([], monkeypatch=monkeypatch))) as client:
            body = await (await client.get("/api/tasks")).json()
        assert body["owner"] == "keyur"

    @pytest.mark.asyncio
    async def test_mine_is_a_noop_without_a_username(self, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        tasks = [_task(id="a", assignee="dana")]
        async with TestClient(
            TestServer(self._app(tasks, owner="", monkeypatch=monkeypatch))
        ) as client:
            body = await (await client.get("/api/tasks?mine=1")).json()
        assert len(body["tasks"]) == 1
