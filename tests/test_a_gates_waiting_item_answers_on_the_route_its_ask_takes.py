"""A gate on the waiting list is answered on the route its ask takes, and that answer lands.

Every gate carries a confirmation id (``gate_answers.ensure_continuation``), and the waiting list
advertised ``/confirm`` for all of them. ``/confirm`` resumes a gate with its verb's yes or no
(``service.resolve_confirmation``), so a choice, a text answer or a form refused it
(``invalid_answer``: "choice expects a string"), and Needs you, which answers only on the route an
item names, drew each as Approve / Deny.

Each gate here is a REAL run parked by the real supervisor. Its waiting item is read by
``GET /api/waiting``'s own reader and compared with the fixture the web's tests build their gates
from (``web/src/test/fixtures/waitingGates.json``), so a test of a card can only stub a gate as the
gateway serves it. Then it is answered over HTTP as Needs you sends it (``useNeedActions``), through
the real handlers, and the run moves on.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import waiting

FIXTURE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "test" / "fixtures" / "waitingGates.json"
)

#: Each ask a gate can make, as a workflow's gate step names it.
GATES: dict[str, dict[str, Any]] = {
    "approval": {"kind": "approval", "prompt": "Ship v1.2?"},
    "choice": {
        "kind": "approval",
        "ask_kind": "choice",
        "prompt": "Which bed first?",
        "choices": ["north", "south"],
    },
    "choice_open": {"kind": "approval", "ask_kind": "choice", "prompt": "Which bed first?"},
    "text": {"kind": "approval", "ask_kind": "text", "prompt": "What is the release called?"},
    "form": {
        "kind": "approval",
        "ask_kind": "form",
        "prompt": "Book the delivery",
        "fields": [
            {"name": "day", "type": "string", "label": "Which day", "required": True},
            {"name": "crates", "type": "number", "label": "How many crates", "default": 2},
        ],
    },
    "event": {"kind": "event", "prompt": "Parked until the build finishes."},
}


def _spec(key: str) -> dict[str, Any]:
    return {
        "name": f"gate-{key}",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "gate", "id": "ask", "config": GATES[key]},
                {"kind": "transform", "id": "after", "config": {"expr": "the run carried on"}},
            ],
        },
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """An isolated workflow home, patched where the store imports it (as
    ``test_only_the_owner_answers_an_approval.isolated``)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows.native_defs import register_native_provider

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    register_native_provider()
    try:
        yield home
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


async def _parked(monkeypatch, key: str):
    from test_triggers_resume_target import _attach, _parked_run

    run, watchdog = await _parked_run(_spec(key))
    _attach(monkeypatch, watchdog)
    return run, watchdog


def _gate_item(run_id: str) -> dict[str, Any]:
    snap = waiting.Snapshot(approvals=[], window=60.0)
    items, _unreadable = waiting.read(snap)
    [item] = [i for i in items if i["kind"] == "gate" and i["work"] == f"workflow:{run_id}"]
    return item


def _as_fixture(item: dict[str, Any], key: str, run_id: str) -> dict[str, Any]:
    """The item with what differs on every run named as the fixture names it: the run's id, its
    token, the confirmation id in the item's id, and the times (when it was asked, its age, its
    deadline)."""
    token = item["answer"]["body"]["resume_token"]
    text = json.dumps(item).replace(run_id, f"run-{key}").replace(token, f"tok-{key}")
    out = json.loads(text)
    head, _run, _conf, epoch = out["id"].split(":")
    out["id"] = f"{head}:run-{key}:conf-{key}:{epoch}"
    out["asked_at"] = "2026-11-02T11:50:00+00:00"
    out["age_secs"] = 0
    out["expires_at"] = None
    return out


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["gates"]


def _app(watchdog) -> web.Application:
    from types import SimpleNamespace

    from personalclaw.workflows import handlers as wf_handlers

    @web.middleware
    async def signed_in(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[signed_in])
    app["state"] = SimpleNamespace(workflows=watchdog, _sessions={})
    app.router.add_post("/api/workflows/runs/{run_id}/resume", wf_handlers.api_run_resume)
    app.router.add_post("/api/workflows/runs/{run_id}/confirm", wf_handlers.api_run_confirm)
    return app


def test_the_fixture_holds_every_ask_a_gate_makes():
    assert set(_fixture()) == set(GATES)


@pytest.mark.anyio
@pytest.mark.parametrize("key", sorted(GATES))
async def test_a_gate_is_listed_as_the_web_fixture_says(isolated, monkeypatch, key):
    """What ``GET /api/waiting`` serves for each ask is the item the web's tests stub. 🔴 Red
    before: a choice, a text, a form and an event gate were all listed on ``/confirm``."""
    run, _watchdog = await _parked(monkeypatch, key)
    assert _as_fixture(_gate_item(run.id), key, run.id) == _fixture()[key]["item"]


@pytest.mark.anyio
@pytest.mark.parametrize("key", sorted(GATES))
async def test_the_answer_needs_you_sends_lands_and_the_run_moves_on(isolated, monkeypatch, key):
    """Each gate answered on the route its item names, with the body Needs you sends there (the
    fixture's ``sends``): the answer is taken and the run goes past the gate. 🔴 Red before: the
    list named ``/confirm`` for every gate, which takes only a verb, so a choice, a text, a form
    or a wake was refused there (400 ``WF_CONFIRM_VERB_INVALID``), as Needs you's Approve on a
    choice gate was (400 ``invalid_answer``)."""
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.human_input import list_continuations
    from personalclaw.workflows.models import RunStatus

    run, watchdog = await _parked(monkeypatch, key)
    item = _gate_item(run.id)
    answer = item["answer"]
    body = {**_fixture()[key]["sends"], "resume_token": answer["body"]["resume_token"]}
    async with TestClient(TestServer(_app(watchdog))) as client:
        resp = await client.post(answer["path"], json=body)
        payload = await resp.json()
    assert resp.status == 200, payload
    assert payload.get("ok", True) is not False, payload
    deadline = time.monotonic() + 15
    while wstore.get(run.id).status == RunStatus.NEEDS_INPUT and time.monotonic() < deadline:
        await _sleep()
    assert wstore.get(run.id).status != RunStatus.NEEDS_INPUT, "the gate still waits"
    assert list_continuations(run.id) == [], "the gate's question is still open"
    assert not [
        i
        for i in waiting.read(waiting.Snapshot(approvals=[], window=60.0))[0]
        if i["kind"] == "gate" and i["work"] == f"workflow:{run.id}"
    ], "still listed"


async def _sleep() -> None:
    import asyncio

    await asyncio.sleep(0.02)
