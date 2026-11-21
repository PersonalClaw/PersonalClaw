"""An A2A retry returns the task it started, after a restart and past the retry cache's minutes.

``POST /a2a/tasks`` turns a client's ``messageId`` (or ``idempotencyKey``) into the run start's
caller key, and the A2A door promises that a retried send returns the same task. The key lived only
in the start dedupe (``workflows.effects.START_DEDUPE``), a process-local cache that forgets after
15 minutes, and nothing wrote it onto the run: a retry after a gateway restart, or 15 minutes on,
started a second run of the same template, and the template's side effects ran twice. And the door
started its runs on no supervisor, so a send created a run that never launched and answered that
the supervisor was unavailable.

Now the key is kept with the run (``store.run_for_caller_key``), so a retry finds its task for as
long as the run is kept, and the door starts its run on the gateway's supervisor.

Driven through the real A2A routes and admission, the real ``workflows.service.start_run`` and
run store in a home of its own. The supervisor is a stand-in that records what it launches.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbound import a2a, auth
from personalclaw.inbound import caps as caps_mod
from personalclaw.inbound.clients import create_client
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import store
from personalclaw.workflows.effects import START_DEDUPE

SURFACE_TOKEN = "t" * 48
SPEC = {
    "name": "triage",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "t", "config": {"expr": "1"}}],
    },
}


class _Supervisor:
    """The gateway's workflow supervisor, as the start seam reaches it: what it launched."""

    def __init__(self) -> None:
        self.launched: list[str] = []

    async def launch(self, run, spec):
        self.launched.append(run.id)
        return SimpleNamespace(run=run)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch) -> Iterator[None]:
    """A home of its own, the A2A surface on with its token, its one template published and
    served, and no remembered start."""
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_PORT", "10771")
    monkeypatch.setenv(auth.token_env_key(a2a.SURFACE), SURFACE_TOKEN)
    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(enabled=True, a2a=Surface(enabled=True))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))

    async def published():
        return [{"id": SPEC["name"]}], ""

    monkeypatch.setattr(a2a, "published_skills", published)

    class _Defs(defs_mod.WorkflowDefProvider):
        name = "a2a-retry-defs"
        readonly = True

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return [SPEC], 1

        async def get_def(self, name: str):
            await asyncio.sleep(0.01)  # a read that yields, as a stored definition's does
            return SPEC if name == SPEC["name"] else None

    monkeypatch.setattr(defs_mod, "_providers", {})
    defs_mod.register_provider(_Defs())
    caps_mod.reset_for_tests()
    START_DEDUPE._entries.clear()
    yield
    START_DEDUPE._entries.clear()
    caps_mod.reset_for_tests()
    os.environ.pop(auth.token_env_key(a2a.SURFACE), None)


async def _gateway(supervisor: _Supervisor) -> TestClient:
    app = web.Application()
    app["state"] = SimpleNamespace(workflows=supervisor)
    a2a.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _send(client: TestClient, body: dict, token: str = SURFACE_TOKEN) -> dict:
    resp = await client.post(
        a2a.ROUTE_TASKS, data=json.dumps(body), headers={"Authorization": f"Bearer {token}"}
    )
    text = await resp.text()
    assert resp.status == 200, text
    return json.loads(text)


def _runs() -> list[str]:
    runs, _total = store.list_runs(workflow_name=SPEC["name"], limit=100)
    return [run.id for run in runs]


def _restart() -> None:
    """What a gateway restart does to the start dedupe: it starts empty."""
    START_DEDUPE._entries.clear()


TASK = {"skillId": SPEC["name"], "message": {"messageId": "m-1"}}


@pytest.mark.asyncio
async def test_the_door_starts_its_run_on_the_gateways_supervisor():
    """🔴 Red before: the run was made, never launched, and the send answered a 400."""
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    try:
        task = await _send(client, TASK)
    finally:
        await client.close()

    assert supervisor.launched == [task["id"]]


@pytest.mark.asyncio
async def test_a_retry_after_a_restart_returns_the_task_it_started():
    """🔴 Red before: the retry started a second run of the template."""
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    try:
        first = await _send(client, TASK)
        _restart()
        again = await _send(client, TASK)
    finally:
        await client.close()

    assert again["id"] == first["id"]
    assert _runs() == [first["id"]]
    assert supervisor.launched == [first["id"]]


@pytest.mark.asyncio
async def test_a_retry_past_the_caches_minutes_returns_it_too(monkeypatch):
    """🔴 Red before: 15 minutes on, the cache had forgotten the key and a second run started."""
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    now = [1000.0]
    monkeypatch.setattr(START_DEDUPE, "clock", lambda: now[0])
    try:
        first = await _send(client, {**TASK, "message": {"messageId": "m-late"}})
        now[0] += START_DEDUPE.ttl_secs + 60
        again = await _send(client, {**TASK, "message": {"messageId": "m-late"}})
    finally:
        await client.close()

    assert again["id"] == first["id"]
    assert _runs() == [first["id"]]


@pytest.mark.asyncio
async def test_another_send_starts_a_task_of_its_own():
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    try:
        first = await _send(client, TASK)
        _restart()
        other = await _send(client, {**TASK, "message": {"messageId": "m-2"}})
        unkeyed = [await _send(client, {"skillId": SPEC["name"]}) for _ in range(2)]
    finally:
        await client.close()

    ids = [first["id"], other["id"], *(t["id"] for t in unkeyed)]
    assert len(set(ids)) == 4
    assert sorted(_runs()) == sorted(ids)


@pytest.mark.asyncio
async def test_another_clients_send_with_the_same_key_after_a_restart_is_its_own():
    record, token = create_client("second caller", surfaces=[a2a.SURFACE])
    assert record.client_id
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    try:
        first = await _send(client, TASK)
        _restart()
        theirs = await _send(client, TASK, token=token)
    finally:
        await client.close()

    assert theirs["id"] != first["id"]


@pytest.mark.asyncio
async def test_a_retry_of_a_task_whose_run_was_deleted_starts_it_again():
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    try:
        first = await _send(client, TASK)
        _restart()
        assert store.delete(first["id"])
        again = await _send(client, TASK)
    finally:
        await client.close()

    assert again["id"] != first["id"]
    assert _runs() == [again["id"]]


async def _post(client: TestClient, body: dict, token: str = SURFACE_TOKEN):
    resp = await client.post(
        a2a.ROUTE_TASKS, data=json.dumps(body), headers={"Authorization": f"Bearer {token}"}
    )
    return resp.status, json.loads(await resp.text())


@pytest.mark.asyncio
async def test_a_different_request_under_a_used_key_is_refused_not_answered_with_the_old_task():
    """🔴 Red before: the key alone was matched, so another request under it got the first task
    back, with nothing to tell the client it had not been started."""
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    try:
        first = await _send(client, {**TASK, "inputs": {"since": "1h"}})
        _restart()
        status, body = await _post(client, {**TASK, "inputs": {"since": "7d"}})
    finally:
        await client.close()

    assert status == 409
    assert body["error"]["code"] == "idempotency_key_reused"
    assert first["id"] not in json.dumps(body)
    assert _runs() == [first["id"]]


@pytest.mark.asyncio
async def test_two_sends_at_once_under_one_key_start_one_run():
    """🔴 Red before: each missed the remembered start while the other was still starting, and
    each started a run."""
    supervisor = _Supervisor()
    client = await _gateway(supervisor)
    try:
        answers = await asyncio.gather(*(_post(client, TASK) for _ in range(4)))
    finally:
        await client.close()

    assert [status for status, _body in answers] == [200] * 4
    assert len({body["id"] for _status, body in answers}) == 1
    assert len(_runs()) == 1


def test_the_gateway_gives_the_door_its_workflow_supervisor():
    """The door starts its runs on ``request.app["state"].workflows``: the gateway sets it on the
    state its dashboard serves, and the door is registered on that dashboard's app."""
    import inspect

    from personalclaw import gateway
    from personalclaw.dashboard import routes

    assert "self.dashboard_state.workflows = self.workflow_watchdog" in inspect.getsource(
        gateway.GatewayOrchestrator
    )
    assert "_register_a2a(app)" in inspect.getsource(routes)


class _FailsOnce(_Supervisor):
    """A supervisor whose first launch fails, as one that cannot start a run that moment does."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = 0

    async def launch(self, run, spec):
        if not self.failed:
            self.failed += 1
            raise RuntimeError("no slot for it now")
        return await super().launch(run, spec)


@pytest.mark.asyncio
async def test_a_retry_after_a_launch_that_failed_starts_a_run_that_launches():
    """🔴 Red before: the run that never launched kept the key, so every retry was answered with
    it, a task that would never start, for as long as that run was kept."""
    supervisor = _FailsOnce()
    client = await _gateway(supervisor)
    try:
        status, failed = await _post(client, TASK)
        again = await _send(client, TASK)
        _restart()
        later = await _send(client, TASK)
    finally:
        await client.close()

    assert status == 400 and "could not start the run" in json.dumps(failed)
    assert supervisor.launched == [again["id"]]
    assert later["id"] == again["id"]
