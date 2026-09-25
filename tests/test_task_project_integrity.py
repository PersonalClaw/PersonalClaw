"""Rails for the task-project-integrity family.

Invariants a scoped view depends on, each previously violated silently:

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

- #2976: the same orphan condition through the SIBLING door. ``DELETE
  /api/task-lists/{id}`` was a bare ``delete_task_list()``, so its tasks survived
  pointing at the deleted list with their derived ``project`` label blanked — and then
  outlived the project delete too, because the #457 cascade above resolves doomed tasks
  by project NAME, which this door had already blanked.

- #2977: a task must not be BORN into that state. Neither parent id on the write path
  was validated, so ``POST/PUT /api/tasks`` accepted a nonexistent ``task_list_id`` at
  201/200 and STORED it, and swallowed a bad ``project_id`` outright — while the sibling
  ``POST /api/task-lists`` refused the identical id.

The last two are why the #457 cascade's resolve-by-NAME is no longer load-bearing for the
orphan state: both doors that could produce a task with a live FK and a blank label are
closed here, so the label the cascade reads can no longer be stale.
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


class TestTheDoneGateJudgesTheWriteItself:
    """The DONE gates judge the task AS THIS WRITE LEAVES IT, not as it was before.

    Measured through the dashboard: the task edit form sends one PUT carrying `status` BEFORE
    `exit_criteria` and `dependencies`, and the gates ran inside the field loop — so ticking the
    last criterion and choosing Completed in one Save was refused against the pre-edit checklist
    ("cannot complete: unfinished exit criteria — Copy reviewed by Sam", about a criterion the
    same request completed). The inverse leaked: a write that completed a task while ADDING an
    unmet criterion passed the gate and stored an invalid DONE row. `create_task` already gated
    the constructed task; update now agrees with it in both directions.
    """

    def test_ticking_the_last_criterion_and_completing_in_one_write_completes(self, provider):
        async def _t():
            t = await provider.create_task(
                title="Draft", exit_criteria=[{"description": "Copy reviewed by Sam"}]
            )
            # Dict order is the dashboard's payload order: status first.
            done = await provider.update_task(
                t.id,
                **{
                    "status": "done",
                    "exit_criteria": [{"description": "Copy reviewed by Sam", "met": True}],
                },
            )
            assert done.status is TaskStatus.DONE
            assert (await provider.get_task(t.id)).status is TaskStatus.DONE

        _run(_t())

    def test_completing_while_adding_an_unmet_criterion_is_refused_and_stores_nothing(
        self, provider
    ):
        async def _t():
            t = await provider.create_task(title="Draft")
            with pytest.raises(ValueError, match="unfinished exit criteria — New check"):
                await provider.update_task(
                    t.id,
                    **{"status": "done", "exit_criteria": [{"description": "New check"}]},
                )
            # The refusal is total: neither the status nor the criterion was written.
            again = await provider.get_task(t.id)
            assert again.status is TaskStatus.OPEN
            assert again.exit_criteria == []

        _run(_t())

    def test_the_order_of_the_fields_in_the_write_does_not_change_the_verdict(self, provider):
        async def _t():
            t = await provider.create_task(title="Draft", exit_criteria=[{"description": "a"}])
            done = await provider.update_task(
                t.id,
                **{"exit_criteria": [{"description": "a", "met": True}], "status": "done"},
            )
            assert done.status is TaskStatus.DONE

        _run(_t())

    def test_dropping_the_open_prerequisite_and_completing_in_one_write_completes(self, provider):
        async def _t():
            a = await provider.create_task(title="Prereq")
            b = await provider.create_task(
                title="Dependent", dependencies=[{"depends_on_task_id": a.id}]
            )
            done = await provider.update_task(b.id, **{"status": "done", "dependencies": []})
            assert done.status is TaskStatus.DONE

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


# ── #2976: deleting a task LIST cascades its tasks too ──


class TestTaskListDeleteCascadesTasks:
    """The #457 orphan condition through the sibling door.

    ``DELETE /api/task-lists/{id}`` was a bare ``delete_task_list()``: its tasks survived
    with ``task_list_id`` still naming the deleted list and their derived ``project`` label
    blanked. The project-delete path above documents exactly why that matters and guards
    against it; this door did the same removal with none of the guard.

    Worse, the orphan then outlived the project delete as well, because the #457 cascade
    resolves doomed tasks by project NAME and the list delete had already blanked that
    label — so the cascade written to catch the orphan could no longer see it.
    """

    @pytest.mark.asyncio
    async def test_deleting_a_list_deletes_its_tasks(self, tmp_path):
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Site"})).json())["id"]
            lid = (
                await (
                    await client.post("/api/task-lists", json={"name": "Sprint", "project_id": pid})
                ).json()
            )["id"]
            tid = (
                await (
                    await client.post("/api/tasks", json={"title": "Ship it", "task_list_id": lid})
                ).json()
            )["id"]

            r = await client.delete(f"/api/task-lists/{lid}")
            assert r.status == 200
            # The count is reported, the way the artifact-folder delete reports what it
            # unfiled: a cascade the caller cannot see is a cascade nobody can audit.
            assert (await r.json())["deleted_tasks"] == 1

            assert (await client.get(f"/api/tasks/{tid}")).status == 404
            listed, _ = await registry.list_all_tasks(limit=10_000)
            assert tid not in {x.id for x in listed}

    @pytest.mark.asyncio
    async def test_a_sibling_lists_tasks_survive(self, tmp_path):
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Site"})).json())["id"]

            async def _make_list(name):
                return (
                    await (
                        await client.post("/api/task-lists", json={"name": name, "project_id": pid})
                    ).json()
                )["id"]

            keep, drop = await _make_list("Keep"), await _make_list("Drop")
            kept = await (
                await client.post("/api/tasks", json={"title": "Keeper", "task_list_id": keep})
            ).json()
            await client.post("/api/tasks", json={"title": "Doomed", "task_list_id": drop})

            assert (await client.delete(f"/api/task-lists/{drop}")).status == 200
            assert (await client.get(f"/api/tasks/{kept['id']}")).status == 200

    @pytest.mark.asyncio
    async def test_an_empty_list_delete_still_answers_the_same_shape(self, tmp_path):
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Site"})).json())["id"]
            lid = (
                await (
                    await client.post("/api/task-lists", json={"name": "Empty", "project_id": pid})
                ).json()
            )["id"]
            r = await client.delete(f"/api/task-lists/{lid}")
            assert r.status == 200
            assert await r.json() == {"ok": True, "deleted_tasks": 0}

    @pytest.mark.asyncio
    async def test_no_task_outlives_the_project_through_the_list_door(self, tmp_path):
        # The second half of #2976, measured end to end: on main the task orphaned by the
        # list delete survived the project delete too, permanently unowned.
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Site"})).json())["id"]
            lid = (
                await (
                    await client.post("/api/task-lists", json={"name": "Sprint", "project_id": pid})
                ).json()
            )["id"]
            tid = (
                await (
                    await client.post("/api/tasks", json={"title": "Ship it", "task_list_id": lid})
                ).json()
            )["id"]

            assert (await client.delete(f"/api/task-lists/{lid}")).status == 200
            assert (await client.delete(f"/api/projects/{pid}")).status == 200
            assert (await client.get(f"/api/tasks/{tid}")).status == 404


# ── #2977: a task cannot be BORN pointing at a parent that does not exist ──


class TestTaskWriteValidatesItsParents:
    """Neither parent id on the task write path was validated, so a task could be created
    directly into the orphan state the two cascades above exist to prevent.

    ``POST /api/task-lists`` refuses the identical bad ``project_id``; the reference
    behavior is the sibling handler on the same parent, not a new convention.
    """

    @pytest.mark.asyncio
    async def test_create_refuses_an_unknown_project_id(self, tmp_path):
        # Measured on main: 201 with `project: ""` and `task_list_id: ""` — the project
        # choice silently discarded by `_attach_project_general_list`'s bare
        # `except ValueError: return`.
        async with _client(tmp_path) as client:
            r = await client.post("/api/tasks", json={"title": "x", "project_id": "p-nope-1234"})
            assert r.status == 400
            err = (await r.json())["error"]
            assert err["code"] == "invalid_request"
            assert "p-nope-1234" in err["message"]
            # Nothing was minted.
            listed, _ = await registry.list_all_tasks(limit=10_000)
            assert listed == []

    @pytest.mark.asyncio
    async def test_the_sibling_list_door_refuses_the_same_id(self, tmp_path):
        # The asymmetry #2977 is about: same parent, same bad id. Pinned so the two doors
        # cannot drift apart again.
        async with _client(tmp_path) as client:
            r = await client.post(
                "/api/task-lists", json={"name": "y", "project_id": "p-nope-1234"}
            )
            assert r.status == 400

    @pytest.mark.asyncio
    async def test_create_refuses_an_unknown_task_list_id(self, tmp_path):
        # Measured on main: 201, and the dangling FK stored verbatim.
        async with _client(tmp_path) as client:
            r = await client.post("/api/tasks", json={"title": "x", "task_list_id": "tl-nope-5678"})
            assert r.status == 400
            assert "tl-nope-5678" in (await r.json())["error"]["message"]
            listed, _ = await registry.list_all_tasks(limit=10_000)
            assert listed == []

    @pytest.mark.asyncio
    async def test_update_refuses_an_unknown_project_id_and_changes_nothing(self, tmp_path):
        async with _client(tmp_path) as client:
            t = await (await client.post("/api/tasks", json={"title": "x"})).json()
            r = await client.put(f"/api/tasks/{t['id']}", json={"project_id": "p-nope-9999"})
            assert r.status == 400
            after = await (await client.get(f"/api/tasks/{t['id']}")).json()
            assert after["task_list_id"] == t["task_list_id"]

    @pytest.mark.asyncio
    async def test_update_refuses_an_unknown_task_list_id_and_changes_nothing(self, tmp_path):
        async with _client(tmp_path) as client:
            t = await (await client.post("/api/tasks", json={"title": "x"})).json()
            r = await client.put(f"/api/tasks/{t['id']}", json={"task_list_id": "tl-nope-9999"})
            assert r.status == 400
            after = await (await client.get(f"/api/tasks/{t['id']}")).json()
            assert after["task_list_id"] == t["task_list_id"]

    @pytest.mark.asyncio
    async def test_bulk_create_refuses_the_same_dangling_parent(self, tmp_path):
        # Bulk is the cheapest way to mint many rows, so it inherits the same rule in
        # PHASE 1 — exactly as it already inherits the attribution rule.
        async with _client(tmp_path) as client:
            r = await client.post(
                "/api/tasks/bulk",
                json={"op": "create", "items": [{"title": "x", "task_list_id": "tl-nope-5678"}]},
            )
            assert r.status == 400
            assert "tl-nope-5678" in (await r.json())["errors"][0]["error"]
            listed, _ = await registry.list_all_tasks(limit=10_000)
            assert listed == []

    @pytest.mark.asyncio
    async def test_a_real_project_id_still_attaches_to_its_general_list(self, tmp_path):
        # The refusal must not cost the feature: a valid `project_id` alone still resolves
        # the project's find-or-create "General" list.
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Site"})).json())["id"]
            r = await client.post("/api/tasks", json={"title": "x", "project_id": pid})
            assert r.status == 201
            made = await r.json()
            assert made["project"] == "Site"
            assert made["task_list_id"]

    @pytest.mark.asyncio
    async def test_a_real_task_list_id_is_still_stored(self, tmp_path):
        async with _client(tmp_path) as client:
            pid = (await (await client.post("/api/projects", json={"name": "Site"})).json())["id"]
            lid = (
                await (
                    await client.post("/api/task-lists", json={"name": "Sprint", "project_id": pid})
                ).json()
            )["id"]
            r = await client.post("/api/tasks", json={"title": "x", "task_list_id": lid})
            assert r.status == 201
            assert (await r.json())["task_list_id"] == lid

    @pytest.mark.asyncio
    async def test_an_explicitly_empty_parent_is_not_a_dangling_one(self, tmp_path):
        # `""` is "no parent chosen" — the payload `TaskForm.draftToPayload` always sends —
        # and must keep meaning that rather than becoming an id lookup that fails.
        async with _client(tmp_path) as client:
            r = await client.post(
                "/api/tasks", json={"title": "x", "project_id": "", "task_list_id": ""}
            )
            assert r.status == 201
