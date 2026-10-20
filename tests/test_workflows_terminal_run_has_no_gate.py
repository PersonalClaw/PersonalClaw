"""A TERMINAL run advertises no answerable gate (issue 583).

`GET /api/workflows/runs/{id}/continuations` checked only that the run EXISTS, so a run that had
failed hours ago still handed out live resume tokens. Measured in the field on two of them, and the
inbox faithfully rendered what the API advertised: a filled **Approve** button beside a "Handled"
chip, on a run that could never consume the answer.

Clicking it could not resurrect anything — `service.resume_run` calls `_live()`, which returns None
for a run with no controller, and refuses with `WF_RUN_NOT_LIVE`. So the user spent the click and
got a raw error code. **A control that cannot succeed is worse than an absent one**, because the
absent one at least tells the truth.

The filter belongs at the HTTP surface, not in `list_continuations`: that function's docstring
enumerates six consumers, including the controller's per-epoch idempotency check and the rewind
drop, which need the pending records whatever the run's status. Narrowing it would fix a rendering
bug by breaking the engine. What was wrong is the ADVERTISEMENT.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.workflows import handlers
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    TERMINAL_RUN_STATUSES,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _spec() -> dict:
    """One approval gate that parks indefinitely, then a step to carry on into."""
    return {
        "name": "terminal-gate",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "gate",
                    "id": "g",
                    "config": {"kind": "approval", "prompt": "approve?", "timeout_secs": 0},
                },
                {"kind": "transform", "id": "after", "config": {"expr": "done"}},
            ],
        },
    }


async def _parked_run() -> WorkflowRun:
    """A run parked on its gate, with one pending continuation on disk."""
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="terminal-gate",
            mode="background",
            origin=RunOrigin(kind=OriginKind.CHAT, session_key="owner-1"),
        )
    )
    spec = _spec()
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    status = await controller.run_to_completion(timeout=20)
    assert status == RunStatus.NEEDS_INPUT, f"the run did not park on a gate: {status}"
    assert HI.list_continuations(run.id), "no continuation was minted, so nothing is under test"
    return run


async def _client() -> TestClient:
    app = web.Application()
    app.router.add_get("/api/workflows/runs/{run_id}/continuations", handlers.api_run_continuations)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _get(run_id: str) -> dict:
    client = await _client()
    try:
        return await (await client.get(f"/api/workflows/runs/{run_id}/continuations")).json()
    finally:
        await client.close()


# ── the live case still works ────────────────────────────────────────────────────────────────


async def test_a_parked_run_still_advertises_its_gate():
    """Vacuity floor, and the whole point of the feature: the inbox answers gates in place, so a
    filter that hid a LIVE gate would break the surface it exists to serve."""
    run = await _parked_run()
    body = await _get(run.id)
    assert len(body["continuations"]) == 1
    assert body["continuations"][0]["resume_token"]
    assert body["run_status"] == "needs_input"


# ── a terminal run advertises nothing ────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", sorted(s.value for s in TERMINAL_RUN_STATUSES))
async def test_no_token_is_handed_out_for_a_terminal_run(status):
    """🔑 Parametrized over the CLOSED vocabulary rather than over `failed` alone: a completed,
    cancelled or escalated run that stopped holding an open gate is exactly as unanswerable, and
    naming one status would have left the other three advertising tokens."""
    run = await _parked_run()
    assert HI.list_continuations(run.id), "precondition: the gate is pending on disk"

    run.status = RunStatus(status)
    store.save(run)

    body = await _get(run.id)
    assert body["continuations"] == [], f"{status} still advertised an answerable gate"
    assert body["run_status"] == status


async def test_the_pending_record_is_NOT_deleted():
    """The fix is an advertisement change, not a data change. The continuation file stays: six
    other consumers read it (the rewind drop reclaims it, the controller's idempotency check
    counts it), and deleting it here would fix a button by corrupting the engine's own bookkeeping.
    """
    run = await _parked_run()
    run.status = RunStatus.FAILED
    store.save(run)

    await _get(run.id)

    assert HI.list_continuations(run.id), "the route must not consume or drop the record"


async def test_an_unknown_run_is_still_a_404_shaped_failure():
    """The existing contract for a missing run is unchanged — a terminal run and a nonexistent one
    are different answers, and collapsing them would hide a deleted run behind "nothing to do"."""
    body = await _get("no-such-run")
    assert body.get("ok") is False or "error" in body, body
    assert "WF_RUN_NOT_FOUND" in str(body)


async def test_the_status_rides_along_so_a_client_can_say_WHY():
    """An empty list alone cannot distinguish "you already answered this" from "the run died
    holding it", and the inbox printed the first sentence for both. The status is what makes the
    second one sayable without a second fetch."""
    run = await _parked_run()
    run.status = RunStatus.CANCELLED
    store.save(run)
    assert (await _get(run.id))["run_status"] == "cancelled"
