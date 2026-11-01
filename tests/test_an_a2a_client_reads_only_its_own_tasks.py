"""An A2A client reads only the tasks it started.

``GET /a2a/tasks/{task_id}`` takes a run id, and a run id is no secret. A client reads the run
only when it is a task that client started: a run under its own session key
(``a2a.session_key_for``). Any other run, another client's task, a run the surface's own token
started, or a chat's, a schedule's or a loop's run, answers exactly as an id that never existed
answers, so the answer says nothing of whether it exists or what it holds. The task a client
started itself still reads, outputs included.

Same harness as ``test_an_a2a_retry_key_is_its_clients_own.py``: the real A2A routes and
admission, two clients registered for the surface, and the real run store.
"""

from __future__ import annotations

import json

import pytest
from test_an_a2a_retry_key_is_its_clients_own import (  # noqa: F401 — the fixtures, used by name
    SURFACE_TOKEN,
    _client,
    _finished_with,
    _home,
    _registered,
    _send,
    started,
)

from personalclaw.inbound import a2a
from personalclaw.workflows import store
from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

pytestmark = pytest.mark.security

#: What a run holds: no answer to anyone who did not start it may carry it.
HELD = "what the run found"


async def _read(client, token: str, task_id: str) -> tuple[int, str]:
    resp = await client.get(f"/a2a/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"})
    return resp.status, await resp.text()


def _a_run(monkeypatch, *, kind: OriginKind, session_key: str) -> str:
    """A finished run started for *session_key*: its step produced :data:`HELD`, and its error
    names it too."""
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="triage",
            origin=RunOrigin(kind=kind, session_key=session_key),
        )
    )
    _finished_with(monkeypatch, run.id, HELD)
    stored = store.get(run.id)
    assert stored is not None
    stored.error_message = HELD
    store.save(stored)
    return run.id


@pytest.mark.asyncio
@pytest.mark.usefixtures("started")
async def test_a_client_reads_its_own_task_and_not_another_clients(monkeypatch):
    _first_id, first_token = _registered("first")
    _second_id, second_token = _registered("second")
    client = await _client()
    try:
        task = await _send(client, first_token, {"skillId": "triage"})
        _finished_with(monkeypatch, task["id"], HELD)
        unknown = await _read(client, second_token, "nosuchrun")
        theirs = await _read(client, second_token, task["id"])
        mine = await _read(client, first_token, task["id"])
    finally:
        await client.close()

    assert unknown[0] == 404, unknown
    assert theirs == unknown, "another client's task answers as an id that never existed"
    assert HELD not in theirs[1]
    assert mine[0] == 200, mine
    assert json.loads(mine[1])["id"] == task["id"]
    assert HELD in mine[1], "the client that started it still reads what it produced"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "session_key"),
    [
        pytest.param(OriginKind.CHAT, "web:chat-of-yours", id="a chat's run"),
        pytest.param(OriginKind.SUBAGENT_TOOL, "web:temporary-chat", id="a chat's batch"),
        pytest.param(OriginKind.SCHEDULE, "cron:nightly", id="a schedule's run"),
        pytest.param(OriginKind.MANUAL, "", id="a run you started"),
        pytest.param(OriginKind.API, a2a.session_key_for(""), id="the surface token's task"),
    ],
)
async def test_a_client_reads_no_run_it_did_not_start(monkeypatch, kind, session_key):
    _client_id, token = _registered("first")
    run_id = _a_run(monkeypatch, kind=kind, session_key=session_key)
    client = await _client()
    try:
        unknown = await _read(client, token, "nosuchrun")
        read = await _read(client, token, run_id)
    finally:
        await client.close()

    assert read == unknown, read
    assert HELD not in read[1]


@pytest.mark.asyncio
@pytest.mark.usefixtures("started")
async def test_the_surface_token_reads_no_clients_task(monkeypatch):
    """The surface's own token is a caller of its own: it reads the tasks it started, and no
    registered client's."""
    _client_id, token = _registered("first")
    client = await _client()
    try:
        theirs = await _send(client, token, {"skillId": "triage"})
        _finished_with(monkeypatch, theirs["id"], HELD)
        mine = await _send(client, SURFACE_TOKEN, {"skillId": "triage"})
        unknown = await _read(client, SURFACE_TOKEN, "nosuchrun")
        read = await _read(client, SURFACE_TOKEN, theirs["id"])
        own = await _read(client, SURFACE_TOKEN, mine["id"])
    finally:
        await client.close()

    assert read == unknown and HELD not in read[1], read
    assert own[0] == 200 and json.loads(own[1])["id"] == mine["id"], own
