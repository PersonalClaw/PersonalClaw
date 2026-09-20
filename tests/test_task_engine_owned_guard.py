"""The single-writer contract on a workflow-managed task, ENFORCED at the doors (#390).

`materialize.reject_write` refuses a direct write to `status`/`blocked_kind`/`preview`/
`done_criterion`/`evidence`/`attempts` on a task a workflow run owns. It was fully implemented,
unit-tested — and had **zero production callers**. Every reference in the tree was its own
definition plus a COMMENT in `controller._write_projected_task` asserting the invariant as though
it held: *"one writer for a managed task's status, and a refusal (naming the alternative) for
every other path."* Measured against the shipped code, every other path refused nothing:
`PUT /api/tasks/{id} {"preview": "…"}` on a managed task answered **200** and overwrote the
engine's projection.

Why that is a bug and not dead code to delete. The docstring names the harm precisely — a board
that disagrees with the run it is showing, while the user believes the board:

* a managed task hand-marked `done` while its workflow node is still running leaves the task list
  and the run ledger disagreeing, with nothing that reconciles them;
* a human-written `evidence` entry is indistinguishable from a machine-produced finding, which is
  the trail the engine's verification reads.

**Three doors, one reading.** The rule reaches a task id through
`tasks.registry.engine_owned_refusal`, and the three non-engine ways to write a task all call it:
`PUT /api/tasks/{id}`, `POST /api/tasks/bulk` with `op: update`, and the agent's `task_update`
tool. Enumerating them is the point — the agent tool's ten-field allowlist happens to exclude most
engine-owned fields, but it does NOT exclude `status`, which is the one that matters most.

🪤 THE FAKE VERSION OF THIS SUITE refuses everything. A guard that also blocked `title` and
`assignee` would make a projected task read-only, and a user who cannot leave a note on their own
board row would rightly call that broken — so every refusal leg below is paired with a leg proving
the same door still writes a user-owned field, and with one proving an UNMANAGED task is untouched.
Every assertion reads STORED state rather than the response echo: a door that answered 409 and
wrote anyway would pass a status-only test.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.tasks import registry
from personalclaw.tasks.handlers import register_task_routes
from personalclaw.workflows.materialize import ENGINE_OWNED_FIELDS

RUN_ID = "r-owned-1"


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


def _stored(tmp_path, task_id: str) -> dict:
    return json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))


async def _managed_task(client, *, node_id: str = "impl") -> dict:
    """A task shaped like the engine's own projection — `workflow_binding.managed: true`.

    Created through the API rather than hand-written to disk, so the binding travels the same
    round-trip the engine's write does: `NativeTaskProvider.create_task` builds its Task
    field-by-field, and a binding it does not name arrives empty.
    """
    r = await client.post(
        "/api/tasks",
        json={
            "title": "projected step",
            "workflow_binding": {
                "run_id": RUN_ID,
                "node_id": node_id,
                "node_path": "root.children[0]",
                "managed": True,
                "fingerprint": "abc123",
            },
        },
    )
    assert r.status == 201
    task = await r.json()
    assert task["workflow_binding"]["managed"] is True, "the fixture is not actually managed"
    return task


async def _plain_task(client) -> dict:
    r = await client.post("/api/tasks", json={"title": "mine alone"})
    assert r.status == 201
    return await r.json()


# ── door 1: PUT /api/tasks/{id} ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_refuses_an_engine_owned_write_and_writes_nothing(tmp_path):
    """The reported repro, verbatim: `{"preview": "…"}` answered 200 and the value landed."""
    async with _client(tmp_path) as client:
        task = await _managed_task(client)
        r = await client.put(
            f"/api/tasks/{task['id']}", json={"preview": "zz42c user overwrote engine field"}
        )
        assert r.status == 409, f"the write was accepted with {r.status}"
        body = await r.json()
        assert body["error"]["code"] == "engine_owned_field"
        assert _stored(tmp_path, task["id"])["preview"] == ""


@pytest.mark.asyncio
async def test_the_refusal_names_the_ALTERNATIVE(tmp_path):
    """A refusal that does not say what to do instead reads as the feature being broken. The
    message is `reject_write`'s own, so the HTTP body is ready-made rather than re-worded here."""
    async with _client(tmp_path) as client:
        task = await _managed_task(client)
        r = await client.put(f"/api/tasks/{task['id']}", json={"status": "done"})
        message = (await r.json())["error"]["message"]
        assert "workflow_skip" in message or "workflow_rewind" in message
        assert RUN_ID in message, "the refusal does not name the run that owns the field"


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", sorted(ENGINE_OWNED_FIELDS))
async def test_every_engine_owned_field_is_refused_over_HTTP(tmp_path, field_name):
    """Parametrized over the frozenset itself, not a hand-copied list: a field added to
    `ENGINE_OWNED_FIELDS` inherits this rail instead of needing to be remembered here."""
    async with _client(tmp_path) as client:
        task = await _managed_task(client)
        r = await client.put(f"/api/tasks/{task['id']}", json={field_name: "anything"})
        assert r.status == 409, f"{field_name} was writable on a managed task"


@pytest.mark.asyncio
async def test_a_USER_owned_field_still_writes_on_a_managed_task(tmp_path):
    """🪤 The vacuity floor for every refusal above. The engine owns the projection, not the whole
    task — refusing everything would make a projected row read-only."""
    async with _client(tmp_path) as client:
        task = await _managed_task(client)
        r = await client.put(f"/api/tasks/{task['id']}", json={"title": "renamed by the user"})
        assert r.status == 200
        assert _stored(tmp_path, task["id"])["title"] == "renamed by the user"


@pytest.mark.asyncio
async def test_an_UNMANAGED_task_accepts_a_status_write(tmp_path):
    """A standalone task must stay fully editable, and a PRODUCED task (binding with
    `managed: false`) tracks nothing — so the user is the only one who can say it is done."""
    async with _client(tmp_path) as client:
        plain = await _plain_task(client)
        r = await client.put(f"/api/tasks/{plain['id']}", json={"status": "done"})
        assert r.status == 200
        assert _stored(tmp_path, plain["id"])["status"] == "done"


# ── door 2: POST /api/tasks/bulk ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_update_refuses_the_batch_and_applies_NOTHING(tmp_path):
    """Refused in PHASE 1, like every other bulk rule.

    Bulk is validate-all-then-apply, and it is the cheapest way to reach many rows. A per-item
    refusal in phase 2 would land the honest edits beside the refused one — a partially applied
    batch is exactly the shape that leaves the board disagreeing with the run and nothing to point
    at. So the honest sibling below must be UNCHANGED, not merely reported.
    """
    async with _client(tmp_path) as client:
        managed_task = await _managed_task(client)
        plain = await _plain_task(client)
        r = await client.post(
            "/api/tasks/bulk",
            json={
                "op": "update",
                "items": [
                    {"id": plain["id"], "title": "honest edit"},
                    {"id": managed_task["id"], "status": "done"},
                ],
            },
        )
        assert r.status == 400
        body = await r.json()
        assert body["succeeded"] == 0
        assert any("workflow_skip" in e["error"] for e in body["errors"])
        assert _stored(tmp_path, plain["id"])["title"] == "mine alone"
        assert _stored(tmp_path, managed_task["id"])["status"] == "open"


@pytest.mark.asyncio
async def test_bulk_update_still_applies_a_USER_owned_field_on_a_managed_task(tmp_path):
    """🪤 The vacuity floor for the bulk leg."""
    async with _client(tmp_path) as client:
        task = await _managed_task(client)
        r = await client.post(
            "/api/tasks/bulk",
            json={"op": "update", "items": [{"id": task["id"], "title": "bulk rename"}]},
        )
        assert r.status == 200
        assert _stored(tmp_path, task["id"])["title"] == "bulk rename"


# ── door 3: the agent's task_update tool ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_agent_tool_refuses_a_managed_status_write(tmp_path):
    """The door the issue flagged as protected only by coincidence: the tool's allowlist excludes
    most engine-owned fields but NOT `status`, so an agent could mark a managed task done while its
    workflow node was still running."""
    registry._providers.clear()
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=home),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
    ):
        provider = NativeBuiltinToolProvider(ws)
        created = await registry.create_task(
            "native",
            title="projected step",
            workflow_binding={
                "run_id": RUN_ID,
                "node_id": "impl",
                "node_path": "root",
                "managed": True,
                "fingerprint": "abc123",
            },
        )
        refused = await provider.invoke("task_update", {"id": created.id, "status": "done"})
        assert refused.success is False
        assert "workflow_skip" in refused.error or "workflow_rewind" in refused.error
        assert (await registry.get_task(created.id)).status.value == "open"

        # 🪤 The vacuity floor: the same tool still edits a user-owned field on the same task.
        ok = await provider.invoke("task_update", {"id": created.id, "title": "agent rename"})
        assert ok.success is True
        assert (await registry.get_task(created.id)).title == "agent rename"
    registry._providers.clear()


# ── the guard fails OPEN, and the doors are enumerated ───────────────────────


@pytest.mark.asyncio
async def test_an_UNKNOWN_task_id_is_not_treated_as_a_violation(tmp_path):
    """Fails open on a read it cannot perform. An unreadable or vanished task is not evidence of a
    violation, and turning a provider hiccup into a 409 on an ordinary edit would make the board
    unusable for the many to protect the few — the 404 the route already owns is the right answer.
    """
    async with _client(tmp_path) as client:
        r = await client.put("/api/tasks/t-nope-zzz", json={"status": "done"})
        assert r.status == 404


def test_every_NON_ENGINE_write_door_consults_the_guard():
    """🪤 The rail that outlives this fix, and the reason #390 existed at all.

    The defect was not a wrong branch — it was a guard with NO call site, which no test of the
    guard's own logic could ever catch (the unit tests all passed while the invariant was
    unenforced). So this reads the source of each door that can reach `registry.update_task` with
    caller-supplied fields and requires the refusal in it. A new door reds HERE.

    `hierarchy_handlers`' task-list reset is deliberately absent: it writes a fixed
    `task_reset_payload`, and only for lists under the `Repeatable` project — it is not a
    caller-supplied field write.
    """
    import inspect

    from personalclaw.agents.native import builtin_tools as tool_mod
    from personalclaw.tasks import handlers as task_handlers

    doors = {
        "api_tasks_update": inspect.getsource(task_handlers.api_tasks_update),
        "api_tasks_bulk": inspect.getsource(task_handlers.api_tasks_bulk),
        "task_update tool": inspect.getsource(tool_mod.NativeBuiltinToolProvider._t_task_update),
    }
    unguarded = [name for name, src in doors.items() if "engine_owned_refusal" not in src]
    assert not unguarded, (
        f"{unguarded} reach the task write path with caller-supplied fields and never consult "
        "`registry.engine_owned_refusal`. A managed task's engine-owned fields are writable "
        "through them (#390)."
    )
