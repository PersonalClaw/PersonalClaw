"""Two edits queued on one paused run both apply, in the order they were made (ledger 293).

An edit to a running run is QUEUED, and the controller applies the queue at its next safe point —
for a paused run, when it is resumed. Each queued batch carried the spec it was prepared against,
and applying it swapped that spec in whole. So two edits made while paused were both prepared
against the same spec, and the second one's copy — the spec before the first edit — replaced the
first edit's on resume: measured on `main`, the first edit simply vanished. An edit that built on
the one before it (change the step that edit had just added) was refused at submit, since it was
checked against a spec that did not have that step yet.

What these pin:

* both edits land, in order, each on the spec the one before it left;
* an edit may build on an earlier edit still in the queue;
* the same holds when the gateway restarts between the edits and the resume.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun, walk

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


def _transform(node_id: str) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": {"said": "as written"}}}


def _spec() -> dict[str, Any]:
    return {
        "name": "edited",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [_transform("draft"), _transform("publish")],
        },
    }


def _say(node_id: str, words: str) -> dict[str, Any]:
    return {"op": "update_node", "node_id": node_id, "fields": {"expr": {"said": words}}}


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _paused() -> RunController:
    spec = _spec()
    run = store.create(WorkflowRun(id="", workflow_name="edited", mode="background"))
    store.write_spec(run.id, spec)
    c = RunController(run, copy.deepcopy(spec), services=EngineServices())
    store.request_pause(run.id)
    await c.start()
    await _until(lambda: c.run.status == RunStatus.PAUSED, what="the run to pause")
    return c


async def _resume(c: RunController) -> None:
    store.clear_pause(c.run.id)
    c.wake()
    await _until(lambda: c.run.is_terminal, what=f"the run to end (it is {c.run.status.value})")


def _said(run_id: str) -> dict[str, Any]:
    """What each step's config says now, in the spec the run executes."""
    root = Node.from_dict((store.read_spec(run_id) or {}).get("root") or {})
    return {
        n.id: (n.config or {}).get("expr", {}).get("said") for _p, n in walk(root) if n.id != "s"
    }


def _queued(body: dict[str, Any]) -> None:
    assert body.get("queued") is True, body


async def test_two_edits_queued_on_a_paused_run_both_apply() -> None:
    c = await _paused()
    version = c.run.spec_version
    _queued(c.submit_mutation([_say("draft", "first edit")], confirm=True))
    _queued(c.submit_mutation([_say("publish", "second edit")], confirm=True))
    await _resume(c)

    # The defect first: on `main` the second edit's spec replaced the first edit's.
    assert _said(c.run.id) == {"draft": "first edit", "publish": "second edit"}, "an edit was lost"
    assert c._outputs["draft"] == {"said": "first edit"}
    assert c._outputs["publish"] == {"said": "second edit"}
    assert c.run.spec_version == version + 2, "one version per edit"
    assert c.run.status == RunStatus.COMPLETE


async def test_the_later_of_two_edits_to_one_step_is_the_one_that_stands() -> None:
    c = await _paused()
    _queued(c.submit_mutation([_say("draft", "first edit")], confirm=True))
    _queued(c.submit_mutation([_say("draft", "second edit")], confirm=True))
    await _resume(c)
    assert _said(c.run.id)["draft"] == "second edit"
    edits = J.journal_records(c.run.id, kinds={J.USER_EDITED_MID_FLIGHT})
    assert [e["ops"][0]["fields"]["expr"]["said"] for e in edits] == ["first edit", "second edit"]


async def test_an_edit_may_build_on_an_earlier_one_still_in_the_queue() -> None:
    """Checked against the spec the queue will have left, not the one before it: the second edit
    changes the step the first one adds. On `main` it was refused at submit — no such step."""
    c = await _paused()
    add = {"op": "insert", "parent_id": "s", "index": 2, "node": _transform("notify")}
    _queued(c.submit_mutation([add], confirm=True))
    _queued(c.submit_mutation([_say("notify", "told them")], confirm=True))
    await _resume(c)
    assert _said(c.run.id) == {
        "draft": "as written",
        "publish": "as written",
        "notify": "told them",
    }
    assert c._outputs["notify"] == {"said": "told them"}


async def test_two_queued_edits_survive_a_restart_and_apply_in_order() -> None:
    c = await _paused()
    _queued(c.submit_mutation([_say("draft", "first edit")], confirm=True))
    _queued(c.submit_mutation([_say("publish", "second edit")], confirm=True))

    # The gateway restarts before anyone resumes the run: nothing of `c` survives but the disk.
    run_id = c.run.id
    fresh = RunController(
        store.get(run_id), store.read_spec(run_id) or {}, services=EngineServices()
    )
    await _resume(fresh)
    assert _said(run_id) == {"draft": "first edit", "publish": "second edit"}
    assert store.read_pending_mutations(run_id) == []
