"""Rails for the task-project-integrity family.

Two invariants a scoped view depends on, each previously violated silently:

- #475: a task cannot be completed while a BLOCKS prerequisite is still open. The
  DONE write enforced only the task's OWN exit criteria, so a kanban drag (PUT
  status=done) completed a task with an unfinished prerequisite, left its
  ``blocked_reason_kind="auto"`` stamp on the now-DONE row, and counted it toward
  graph completion. The gate now refuses, mirroring the exit-criteria refusal.

- #457: deleting a project must not orphan its tasks. ``delete_project`` drops the
  project's task LISTS but the task rows live in the native provider keyed by
  ``task_list_id``; without a cascade they survived pointing at dead list ids,
  unreachable from every scoped view. The delete handler now deletes the
  project's tasks first.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.tasks.native as nat
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes
from personalclaw.tasks.models import TaskStatus


def _run(coro):
    return asyncio.run(coro)


# ── #475: the DONE gate refuses an open prerequisite ──


@pytest.fixture
def provider(tmp_path):
    with patch.object(nat, "config_dir", lambda: tmp_path):
        yield nat.NativeTaskProvider()


class TestCompleteGateHonoursDependencies:
    def test_cannot_complete_while_prerequisite_is_open(self, provider):
        async def _t():
            a = await provider.create_task(title="Prereq")
            b = await provider.create_task(
                title="Dependent", dependencies=[{"depends_on_task_id": a.id}]
            )
            with pytest.raises(ValueError, match="unfinished prerequisite"):
                await provider.update_task(b.id, status="done")
            # The refusal is total: b stays not-done, so nothing counts it complete.
            again = await provider.get_task(b.id)
            assert again.status is not TaskStatus.DONE

        _run(_t())

    def test_completes_once_the_prerequisite_is_terminal(self, provider):
        async def _t():
            a = await provider.create_task(title="Prereq")
            b = await provider.create_task(
                title="Dependent", dependencies=[{"depends_on_task_id": a.id}]
            )
            await provider.update_task(a.id, status="done")  # prereq terminal → b unblocks
            done_b = await provider.update_task(b.id, status="done")
            assert done_b.status is TaskStatus.DONE
            # A completed task carries no residual auto-block stamp.
            assert done_b.blocked_reason_kind != "auto"

        _run(_t())

    def test_a_cancelled_prerequisite_is_terminal_and_unblocks(self, provider):
        # reconcile treats cancel as terminal; the gate must agree, or a cancelled
        # prereq would strand its dependent uncompletable forever.
        async def _t():
            a = await provider.create_task(title="Prereq")
            b = await provider.create_task(
                title="Dependent", dependencies=[{"depends_on_task_id": a.id}]
            )
            await provider.update_task(a.id, status="cancelled")
            done_b = await provider.update_task(b.id, status="done")
            assert done_b.status is TaskStatus.DONE

        _run(_t())

    def test_exit_criteria_gate_still_independently_enforced(self, provider):
        # The dependency gate is additive — an un-blocked task with unmet exit
        # criteria is still refused by the original gate.
        async def _t():
            t = await provider.create_task(
                title="X", exit_criteria=[{"description": "tests pass", "status": "incomplete"}]
            )
            with pytest.raises(ValueError, match="unfinished exit criteria"):
                await provider.update_task(t.id, status="done")

        _run(_t())


class TestCreateHonoursTheDoneGate:
    # create_task builds a Task from a caller-supplied `status`, so `status="done"`
    # bypassed the gate update enforces — a backdoor to the same invalid state (#475).
    # create now runs the identical exit-criteria + dependency gate.
    def test_create_done_with_unfinished_exit_criteria_is_refused(self, provider):
        async def _t():
            with pytest.raises(ValueError, match="unfinished exit criteria"):
                await provider.create_task(
                    title="Born done",
                    status="done",
                    exit_criteria=[{"description": "ship it", "status": "incomplete"}],
                )

        _run(_t())

    def test_create_done_blocked_by_an_open_prerequisite_is_refused(self, provider):
        async def _t():
            a = await provider.create_task(title="Prereq")
            with pytest.raises(ValueError, match="unfinished prerequisite"):
                await provider.create_task(
                    title="Born done, still blocked",
                    status="done",
                    dependencies=[{"depends_on_task_id": a.id}],
                )

        _run(_t())

    def test_create_done_is_allowed_when_nothing_is_outstanding(self, provider):
        # A genuinely-complete row (no unfinished criteria, no live prerequisite) must
        # stay creatable — e.g. backfilling a historically-done task — so the gate refuses
        # INVALID done, not done-on-create itself.
        async def _t():
            a = await provider.create_task(title="Prereq")
            await provider.update_task(a.id, status="done")  # terminal prereq
            b = await provider.create_task(
                title="Born done, prereq terminal",
                status="done",
                dependencies=[{"depends_on_task_id": a.id}],
                exit_criteria=[{"description": "done", "status": "complete"}],
            )
            assert b.status is TaskStatus.DONE

        _run(_t())


# ── #457: deleting a project cascades its tasks ──


@asynccontextmanager
async def _client(tmp_path):
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


class TestProjectDeleteCascadesTasks:
    @pytest.mark.asyncio
    async def test_deleting_a_project_deletes_its_tasks(self, tmp_path):
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Website"})).json())[
                "id"
            ]
            t = await (
                await client.post("/api/tasks", json={"title": "Ship it", "project_id": pid})
            ).json()
            tid = t["id"]
            assert (await client.get(f"/api/tasks/{tid}")).status == 200

            assert (await client.delete(f"/api/projects/{pid}")).status == 200

            # The task is GONE, not orphaned pointing at a dead list id (#457).
            assert (await client.get(f"/api/tasks/{tid}")).status == 404
            listed, _ = await registry.list_all_tasks(limit=10_000)
            assert tid not in {x.id for x in listed}

    @pytest.mark.asyncio
    async def test_a_sibling_projects_tasks_survive(self, tmp_path):
        async with _client(tmp_path) as client:
            keep = (await (await client.post("/api/projects", json={"name": "Keep"})).json())["id"]
            drop = (await (await client.post("/api/projects", json={"name": "Drop"})).json())["id"]
            kept = await (
                await client.post("/api/tasks", json={"title": "Keeper", "project_id": keep})
            ).json()
            await client.post("/api/tasks", json={"title": "Doomed", "project_id": drop})

            assert (await client.delete(f"/api/projects/{drop}")).status == 200

            # Only the deleted project's task is cascaded; the sibling is untouched.
            assert (await client.get(f"/api/tasks/{kept['id']}")).status == 200
