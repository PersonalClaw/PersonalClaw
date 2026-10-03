"""An edit of a running workflow lets no step act alone or change things without your yes.

A running workflow is edited through its mutation queue (``workflow_edit``, the run page's edit, a
loop's replan). ``update_node`` sets any key of a step's config, and ``insert`` adds a step whole,
so an agent could give a step of a run already under way ``approval_mode: "auto"`` or ``capability:
"mutating"``, and the step acted on it the moment it ran, with nobody asked. An edit is now held to
the rule every save of a definition is:

* an agent's edit that would let a step do more is refused, saying where its owner gives that yes,
  and nothing is queued;
* the owner's own edit asks her first (the consent question), and her ``confirm`` lets it through;
* an edit that lets no step do more is queued as before, a step inserted before an allowed one
  included: a step is the one with the same id, wherever it now sits.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from typing import Any

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


def _spec() -> dict[str, Any]:
    return {
        "name": "nightly-tidy",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "gather", "config": {"expr": {"said": "notes"}}},
                {
                    "kind": "stage",
                    "id": "fix",
                    "label": "Fix the lint",
                    "config": {"prompt": "Fix every lint error.", "approval_mode": "auto"},
                },
                {
                    "kind": "stage",
                    "id": "report",
                    "label": "Report",
                    "config": {"prompt": "Say what changed."},
                },
            ],
        },
    }


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _paused() -> RunController:
    spec = _spec()
    run = store.create(WorkflowRun(id="", workflow_name="nightly-tidy", mode="background"))
    store.write_spec(run.id, spec)
    c = RunController(run, copy.deepcopy(spec), services=EngineServices())
    store.request_pause(run.id)
    await c.start()
    await _until(lambda: c.run.status == RunStatus.PAUSED, what="the run to pause")
    return c


async def _stop(c: RunController) -> None:
    """End the paused run's loop, if it is still there: the test is done with it."""
    task = getattr(c, "_task", None)
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _Supervisor:
    def __init__(self, controller: RunController) -> None:
        self._controller = controller

    def controller(self, run_id: str) -> RunController | None:
        return self._controller if run_id == self._controller.run.id else None


def _act_alone(node_id: str) -> dict[str, Any]:
    return {"op": "update_node", "node_id": node_id, "fields": {"approval_mode": "auto"}}


async def test_an_agents_edit_that_lets_a_step_act_alone_is_refused(tmp_path):
    c = await _paused()
    try:
        refused = service.edit_run(c.run.id, [_act_alone("report")], supervisor=_Supervisor(c))

        assert refused["ok"] is False and refused["code"] == "WF_MUT_NEEDS_OWNER_YES", refused
        assert "“Report”" in refused["message"] and "workflow_author" in refused["message"]
        assert c._pending_mutations == [], "a refused edit was queued"
    finally:
        await _stop(c)


async def test_an_inserted_step_that_acts_alone_is_refused(tmp_path):
    c = await _paused()
    try:
        insert = {
            "op": "insert",
            "parent_id": "s",
            "index": 3,
            "node": {
                "kind": "stage",
                "id": "push",
                "label": "Push it",
                "config": {"prompt": "Push the branch.", "capability": "mutating"},
            },
        }
        refused = service.edit_run(c.run.id, [insert], supervisor=_Supervisor(c))

        assert refused["code"] == "WF_MUT_NEEDS_OWNER_YES", refused
        assert c._pending_mutations == []
    finally:
        await _stop(c)


async def test_changing_what_an_allowed_step_does_is_refused_too(tmp_path):
    """Her yes was to the step she was shown: an agent may not give an allowed step new work."""
    c = await _paused()
    try:
        repurpose = {
            "op": "update_node",
            "node_id": "fix",
            "fields": {"prompt": "Delete the old release branches."},
        }
        refused = service.edit_run(c.run.id, [repurpose], supervisor=_Supervisor(c))

        assert refused["code"] == "WF_MUT_NEEDS_OWNER_YES", refused
        assert "“Fix the lint”" in refused["message"], refused
    finally:
        await _stop(c)


async def test_an_edit_that_lets_no_step_do_more_is_queued(tmp_path):
    c = await _paused()
    try:
        prompt = {"op": "update_node", "node_id": "report", "fields": {"prompt": "Say it short."}}
        before = {
            "op": "insert",
            "parent_id": "s",
            "index": 0,
            "node": {"kind": "transform", "id": "warmup", "config": {"expr": {"said": "hi"}}},
        }

        for ops in ([prompt], [before]):
            queued = service.edit_run(c.run.id, ops, supervisor=_Supervisor(c))
            assert queued.get("queued") is True, queued
    finally:
        await _stop(c)


async def test_the_owners_edit_asks_her_and_her_confirm_lets_it_through(tmp_path):
    from personalclaw.workflows import handlers

    c = await _paused()

    class _Req:
        def __init__(self, body: dict) -> None:
            self._body = body
            self.match_info = {"run_id": c.run.id}
            self.headers: dict[str, str] = {}
            self.app = {"state": type("S", (), {"workflows": _Supervisor(c)})()}

        async def read(self) -> bytes:
            return json.dumps(self._body).encode()

        async def json(self) -> dict:
            return self._body

        def get(self, key: str, default: Any = None) -> Any:
            return {"user": "owner"}.get(key, default)

    try:
        body = {"ops": [_act_alone("report")]}
        asked = await handlers.api_run_edit(_Req(body))

        assert asked.status == 400, asked.body
        error = json.loads(asked.body)["error"]
        assert error["code"] == "confirmation_required", error
        assert error["detail"]["field"].endswith("root.children[2].approval_mode"), error
        assert c._pending_mutations == []

        allowed = await handlers.api_run_edit(_Req({**body, "confirm": True}))
        assert allowed.status == 200, allowed.body
        assert json.loads(allowed.body).get("queued") is True
    finally:
        await _stop(c)
