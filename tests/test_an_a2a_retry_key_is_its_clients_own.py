"""An A2A client's retry key names its own request, never another client's.

``POST /a2a/tasks`` turns a client's ``messageId`` (or ``idempotencyKey``) into the run start's
caller key, so a retried send gets the task it already started instead of a second run. The key
is the client's: two registered clients that send one ``messageId`` each get a task of their own,
and neither answer carries the other's outputs.

Driven through the real A2A routes and the real admission (two clients registered for the A2A
surface, each with its own token), the real start dedupe (``workflows.effects.START_DEDUPE``) and
the real run store. The start itself is a stand-in that does what ``workflows.service.start_run``
does with a caller key: it answers the run the dedupe remembers for the key, else stores a run
under the session key it is given and remembers it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbound import a2a, auth
from personalclaw.inbound import caps as caps_mod
from personalclaw.inbound.clients import create_client
from personalclaw.workflows import service as wf_service
from personalclaw.workflows import store
from personalclaw.workflows.effects import START_DEDUPE
from personalclaw.workflows.models import RunOrigin, RunStatus, WorkflowRun

pytestmark = pytest.mark.security

#: What the first client's task produced: no answer to the second client may hold it.
FIRST_OUTPUT = "the first client's findings"
#: The surface's own token, which a surface needs before it serves anyone.
SURFACE_TOKEN = "t" * 48


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch) -> Iterator[None]:
    """A home of its own, the A2A surface on with its token, its skill published, and no
    remembered start."""
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
        return [{"id": "triage"}], ""

    monkeypatch.setattr(a2a, "published_skills", published)
    caps_mod.reset_for_tests()
    START_DEDUPE._entries.clear()
    yield
    START_DEDUPE._entries.clear()
    caps_mod.reset_for_tests()
    os.environ.pop(auth.token_env_key(a2a.SURFACE), None)


@pytest.fixture
def started(monkeypatch) -> list[dict]:
    """The start seam, keyed as ``start_run`` keys it; each call's arguments, in order."""
    calls: list[dict] = []

    async def start_run(**kwargs):
        calls.append(kwargs)
        key = kwargs["idempotency_key"]
        existing = START_DEDUPE.lookup(key)
        if existing:
            return {"ok": True, "run_id": existing, "deduped": True}
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=kwargs["name"],
                origin=RunOrigin(kind=kwargs["origin_kind"], session_key=kwargs["session_key"]),
            )
        )
        START_DEDUPE.remember(key, run.id)
        return {"ok": True, "run_id": run.id}

    monkeypatch.setattr(wf_service, "start_run", start_run)
    return calls


def _finished_with(monkeypatch, run_id: str, output: str) -> None:
    """The run *run_id* ended, its one step having produced *output*."""
    run = store.get(run_id)
    assert run is not None
    run.status = RunStatus.COMPLETE
    store.save(run)
    real_nodes = wf_service._nodes_of
    real_output = wf_service.output
    monkeypatch.setattr(
        wf_service,
        "_nodes_of",
        lambda rid: (
            [{"node_id": "answer", "state": "succeeded"}] if rid == run_id else real_nodes(rid)
        ),
    )
    monkeypatch.setattr(
        wf_service,
        "output",
        lambda rid, node_id: (
            {"ok": True, "output": output} if rid == run_id else real_output(rid, node_id)
        ),
    )


async def _client() -> TestClient:
    app = web.Application()
    a2a.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _registered(label: str) -> tuple[str, str]:
    """A client registered for the A2A surface: its id and its token."""
    record, token = create_client(label, surfaces=[a2a.SURFACE])
    return record.client_id, token


async def _send(client: TestClient, token: str, body: dict) -> dict:
    resp = await client.post(
        a2a.ROUTE_TASKS, data=json.dumps(body), headers={"Authorization": f"Bearer {token}"}
    )
    text = await resp.text()
    assert resp.status == 200, text
    return json.loads(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "keyed",
    [
        pytest.param({"message": {"messageId": "m-1"}}, id="messageId"),
        pytest.param({"idempotencyKey": "k-1"}, id="idempotencyKey"),
    ],
)
async def test_one_key_from_two_clients_starts_two_tasks(started, monkeypatch, keyed):
    first_id, first_token = _registered("first")
    second_id, second_token = _registered("second")
    client = await _client()
    try:
        first = await _send(client, first_token, {"skillId": "triage", **keyed})
        _finished_with(monkeypatch, first["id"], FIRST_OUTPUT)
        second = await _send(client, second_token, {"skillId": "triage", **keyed})
    finally:
        await client.close()

    assert second["id"] != first["id"], "the second client got the first client's task"
    assert FIRST_OUTPUT not in json.dumps(second), second
    runs = {r.id: r for r in store.all_runs()}
    assert runs[first["id"]].origin.session_key == a2a.session_key_for(first_id)
    assert runs[second["id"]].origin.session_key == a2a.session_key_for(second_id)
    assert len(started) == 2 and started[0]["idempotency_key"] != started[1]["idempotency_key"]


@pytest.mark.asyncio
async def test_a_client_that_sends_its_key_again_gets_its_own_task_back(started):
    """The retry the key is for still holds, for the client that sent it."""
    _client_id, token = _registered("first")
    client = await _client()
    try:
        first = await _send(client, token, {"skillId": "triage", "message": {"messageId": "m-1"}})
        again = await _send(client, token, {"skillId": "triage", "message": {"messageId": "m-1"}})
    finally:
        await client.close()

    assert again["id"] == first["id"]
    assert len(store.all_runs()) == 1, "a retry starts no second run"


@pytest.mark.asyncio
async def test_the_surface_token_and_a_client_keep_apart_too(started, monkeypatch):
    """The surface's own token is a principal of its own (its runs' session key is
    ``inbound:a2a:a2a``), so it shares no key with a registered client either."""
    _client_id, client_token = _registered("first")
    client = await _client()
    try:
        mine = await _send(client, client_token, {"skillId": "triage", "idempotencyKey": "k-1"})
        _finished_with(monkeypatch, mine["id"], FIRST_OUTPUT)
        theirs = await _send(client, SURFACE_TOKEN, {"skillId": "triage", "idempotencyKey": "k-1"})
    finally:
        await client.close()

    assert theirs["id"] != mine["id"]
    assert FIRST_OUTPUT not in json.dumps(theirs), theirs
