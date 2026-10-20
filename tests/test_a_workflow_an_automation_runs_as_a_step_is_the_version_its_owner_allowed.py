"""A workflow an allowed automation runs as a step is the version its owner allowed with it.

A workflow can start others as its steps: a ``subworkflow`` step, or a ``run-workflow`` action
step. Each is looked up by name when the step runs, so the Allow of an automation records the
version of each workflow its workflow starts, at every depth, as it was then, and the run the
automation starts carries those versions on its record: a step starts the version allowed with the
automation, or a newer one the owner saved in its editor herself, never one anything else saved
since. A version the owner saves of the workflow records the versions of the workflows it starts as
they were at her save, so a step she adds runs what she saw. Use vN on the automation moves them
too, asking first, and the workflow's page lists the automations that run it as a step.

Driven end to end: the owner's editor route and the agent's ``workflow_author`` tool save; the
automation's Allow (``grants.give``) and its fire (the ``run-workflow`` action) start a real run on
a real supervisor, whose steps start their own runs; the Triggers page's Use vN route answers.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.config import loader as config_loader
from personalclaw.stale_write import revision_of
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import handlers as H
from personalclaw.workflows import service, store, versions
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.models import RunStatus
from personalclaw.workflows.native_defs import NativeWorkflowDefProvider
from personalclaw.workflows.watchdog import WorkflowWatchdog

pytestmark = pytest.mark.anyio

PARENT = "weekly-report"
PART = "report-part"
TRIGGER_ID = "manual:weekly-report"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _one_home(monkeypatch):
    """The Triggers page's store reads the home every other door here reads."""
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.triggers.config_dir", config_loader.config_dir
    )


@pytest.fixture(autouse=True)
def _native_store():
    before = defs_mod.get_provider("native")
    defs_mod.register_provider(NativeWorkflowDefProvider())
    yield
    defs_mod.unregister_provider("native")
    if before is not None:
        defs_mod.register_provider(before)


class _Supervisor(WorkflowWatchdog):
    """The real supervisor, keeping each run it launches so a test can wait for it."""

    def __init__(self) -> None:
        super().__init__(None, EngineServices())
        self.launched: dict[str, Any] = {}

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> Any:
        controller = await super().launch(run, spec, depth=depth)
        self.launched[run.id] = controller
        return controller


@pytest.fixture
def supervisor(monkeypatch) -> _Supervisor:
    supervisor = _Supervisor()
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=supervisor),
    )
    return supervisor


def _part(said: str) -> dict[str, Any]:
    """The workflow run as a step, whose one step says *said*: what tells its versions apart."""
    return {
        "kind": "sequence",
        "id": "main",
        "children": [{"kind": "transform", "id": "tail", "config": {"expr": said}}],
    }


def _runs_the_part_as_a_subworkflow(ref: str = PART) -> dict[str, Any]:
    return {
        "kind": "sequence",
        "id": "main",
        "children": [{"kind": "subworkflow", "id": "part", "config": {"ref": ref, "inputs": {}}}],
    }


def _starts_the_part_as_an_action(name: str = PART) -> dict[str, Any]:
    return {
        "kind": "sequence",
        "id": "main",
        "children": [
            {
                "kind": "action",
                "id": "start",
                "config": {"provider": "run-workflow", "with": {"workflow": name}},
            }
        ],
    }


def _request(
    method: str,
    path: str,
    body: dict[str, Any],
    *,
    match: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    state: Any = None,
) -> web.Request:
    """A request the owner's signed-in session makes."""
    app = web.Application()
    if state is not None:
        app["state"] = state
    request = make_mocked_request(
        method, path, headers=headers or {}, app=app, match_info=match or {}
    )
    request["user"] = "owner"

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[method-assign]
    return request


def _reply(response: web.StreamResponse) -> dict[str, Any]:
    return json.loads(response.body.decode())  # type: ignore[attr-defined]


async def _owner_saves(name: str, root: dict[str, Any]) -> None:
    """Save *name* from its editor: the owner's door, naming the revision it edited."""
    claim: dict[str, str] = {}
    stored = await service.get_def(name)
    if stored.get("ok"):
        claim["If-Match"] = revision_of(stored["definition"])
    body = {"name": name, "root": root}
    saved = _reply(await H.api_def_save(_request("POST", "/api/workflows", body, headers=claim)))
    assert saved.get("saved"), saved


def _agent_saves(name: str, root: dict[str, Any]) -> None:
    """Save *name* with the agent's own tool, ``workflow_author``."""
    from personalclaw.mcp_workflows import _call_tool

    answer = _call_tool("workflow_author", {"name": name, "root": json.dumps(root)})
    assert '"saved": true' in str(answer), answer


def _allowed_automation() -> Trigger:
    """A "Run workflow" automation for the parent workflow, which the owner allowed (her yes)."""
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Weekly report",
        kind="manual",
        created_by="user",
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": PARENT}}},
    )
    asked = grants.question(trigger)
    assert asked is not None and asked.shown is not None, asked
    assert grants.give(trigger, allowing=grants.allowing(trigger, asked.shown)) == ["run-workflow"]
    TriggerStore(base_dir=config_loader.config_dir()).upsert(trigger)
    return trigger


def _stored() -> Trigger:
    row = TriggerStore(base_dir=config_loader.config_dir()).get(TRIGGER_ID)
    assert row is not None
    return row.trigger


class _Fired(SimpleNamespace):
    """A fire's own run, and every run it started on the way, its steps' included."""

    run: Any
    started: list[Any]


async def _fire(supervisor: _Supervisor, *, trigger_id: str = TRIGGER_ID) -> _Fired:
    """Fire the automation's action and drive every run it starts to its end."""
    before = {r.id for r in store.list_runs()[0]}
    result = await RunWorkflowActionProvider().execute(
        {"workflow": PARENT}, ActionContext(event="manual", trigger_id=trigger_id)
    )
    assert result.success, result.error
    await _finish(supervisor)
    run = store.get(json.loads(result.stdout)["run_id"])
    return _Fired(run=run, started=[r for r in store.list_runs()[0] if r.id not in before])


async def _finish(supervisor: _Supervisor) -> None:
    """Drive every launched run to its end, those its steps launch on the way included."""
    done: set[str] = set()
    while set(supervisor.launched) - done:
        for run_id in sorted(set(supervisor.launched) - done):
            await supervisor.launched[run_id].run_to_completion(timeout=30)
            done.add(run_id)


def _what_the_part_ran(fired: _Fired) -> tuple[int, str]:
    """The version of the part the fire's run started, and what its step said."""
    parts = [r for r in fired.started if r.workflow_name == PART]
    assert len(parts) == 1, [(r.id, r.workflow_name) for r in fired.started]
    said = store.read_output(parts[0].id, "root.children[0]")
    spec = store.read_spec(parts[0].id) or {}
    assert spec["root"]["children"][0]["config"]["expr"] == said
    return parts[0].spec_version, said


def _step_failure(fired: _Fired) -> str:
    failures = [i.failure.cause_plain for i in store.read_state(fired.run.id).values() if i.failure]
    assert failures, store.read_state(fired.run.id)
    return failures[0]


# ── what a step runs ─────────────────────────────────────────────────────────────────────────


async def test_a_newer_version_of_a_subworkflow_an_agent_saved_is_not_what_its_step_runs(
    supervisor,
):
    """🔴 Before: the owner allowed the automation, an agent then saved a new version of the
    workflow it runs as a step, and the step ran the agent's version."""
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _allowed_automation()
    _agent_saves(PART, _part("the agent's part"))

    fired = await _fire(supervisor)

    assert fired.run.status == RunStatus.COMPLETE
    assert _what_the_part_ran(fired) == (1, "the owner's part")


async def test_a_newer_version_of_a_subworkflow_the_owner_saved_is_followed(supervisor):
    """The control: her own save of the part is her yes to it, so its step runs it."""
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _allowed_automation()
    await _owner_saves(PART, _part("the owner's second part"))

    _version, said = _what_the_part_ran(await _fire(supervisor))

    assert said == "the owner's second part"


async def test_a_subworkflows_run_records_the_version_it_ran(supervisor):
    """🔴 Before: a step's child run recorded version 1 whatever version of its workflow it ran,
    so its record could not say which one that was."""
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PART, _part("the owner's second part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _allowed_automation()

    assert _what_the_part_ran(await _fire(supervisor)) == (2, "the owner's second part")


async def test_a_run_workflow_step_starts_the_version_allowed_with_its_automation(supervisor):
    """🔴 Before: a ``run-workflow`` action step started the workflow it names as it was then,
    whoever had saved it since the automation's Allow."""
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _starts_the_part_as_an_action())
    _allowed_automation()
    _agent_saves(PART, _part("the agent's part"))

    assert _what_the_part_ran(await _fire(supervisor)) == (1, "the owner's part")


async def test_a_step_starts_no_workflow_its_automation_was_not_allowed_with(supervisor):
    """🔴 Before: a workflow that did not exist when the automation was allowed, which an agent
    then made under the name a step starts, ran under the owner's Allow."""
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _allowed_automation()
    _agent_saves(PART, _part("the agent's part"))

    fired = await _fire(supervisor)

    assert fired.run.status == RunStatus.FAILED
    assert f"this automation was not allowed to run “{PART}”" in _step_failure(fired)
    assert [r.workflow_name for r in fired.started] == [PARENT]


async def test_a_run_no_allowed_automation_started_runs_its_steps_as_they_are(supervisor):
    """The control: the Run button's run starts each workflow its steps name as it is."""
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _agent_saves(PART, _part("the agent's part"))

    before = {r.id for r in store.list_runs()[0]}
    started = await service.start_run(name=PARENT, supervisor=supervisor, skip_preflight=True)
    assert started.get("ok"), started
    await _finish(supervisor)
    fired = _Fired(
        run=store.get(started["run_id"]),
        started=[r for r in store.list_runs()[0] if r.id not in before],
    )

    _version, said = _what_the_part_ran(fired)

    assert said == "the agent's part"


async def test_a_step_the_owners_save_added_runs_the_workflow_as_it_was_at_her_save(supervisor):
    """Her save of the workflow that adds a step is her yes to what that step starts, as it was:
    an agent's version of it saved after hers is not followed."""
    await _owner_saves(PARENT, {"kind": "transform", "id": "only", "config": {"expr": 1}})
    _allowed_automation()
    _agent_saves(PART, _part("an agent's part, before her save"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _agent_saves(PART, _part("an agent's part, after her save"))

    assert _what_the_part_ran(await _fire(supervisor)) == (1, "an agent's part, before her save")
    record = versions.get_version(PARENT, 2)
    assert record is not None and record.saved_by == "owner"
    assert record.calls[PART]["version"] == 1, record.calls


async def test_only_the_owners_save_records_the_versions_its_steps_start():
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _agent_saves(PARENT, _starts_the_part_as_an_action())

    owners, agents = versions.get_version(PARENT, 1), versions.get_version(PARENT, 2)
    assert owners is not None and agents is not None
    assert owners.calls == {PART: {"version": 1, "digest": versions.digest(_spec_of(PART, 1))}}
    assert agents.saved_by == "agent" and agents.calls == {}


def _spec_of(name: str, version: int) -> dict[str, Any]:
    record = versions.get_version(name, version)
    assert record is not None
    return record.spec


# ── what the row says, and Use vN ────────────────────────────────────────────────────────────


class _State:
    def push_refresh(self, *_args: Any) -> None:
        return None


async def _use(body: dict[str, Any]) -> web.Response:
    """Use vN on the automation's row: the Triggers page's route, as the owner's session."""
    from personalclaw.dashboard.handlers.triggers import api_trigger_workflow_version

    return await api_trigger_workflow_version(
        _request(
            "POST",
            f"/api/triggers/store:{TRIGGER_ID}/workflow-version",
            body,
            match={"id": f"store:{TRIGGER_ID}"},
            state=_State(),
        )
    )


async def test_the_row_says_a_step_waits_and_use_asks_before_it_moves_it(supervisor):
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _allowed_automation()
    _agent_saves(PART, _part("the agent's part"))

    row = grants.workflow_version(_stored())
    assert row is not None and (row["runs"], row["newer"]) == (1, 0), row
    assert row["steps"] == [
        {
            "workflow": PART,
            "via": PARENT,
            "runs": 1,
            "saved_by": "owner",
            "newer": 2,
            "since": [{"version": 2, "saved_by": "agent"}],
            "problem": "",
        }
    ]
    assert row["use"] == {"version": 1, "steps": {PART: 2}}

    asked = await _use({**row["use"]})
    assert asked.status == 400
    detail = _reply(asked)["error"]["detail"]
    assert detail["title"] == f"Run the newest version of “{PART}”?", detail
    assert f"Its steps start “{PART}” at version 2 instead of version 1." in detail["consent"]
    assert "v2 by an agent" in detail["consent"], detail
    assert detail["change"] == f"“{PART}” v1 → v2"
    assert _what_the_part_ran(await _fire(supervisor)) == (1, "the owner's part")

    used = await _use({**row["use"], "confirm": True})
    assert used.status == 200, _reply(used)
    assert _reply(used)["trigger"]["workflow_version"]["use"] is None
    assert _what_the_part_ran(await _fire(supervisor)) == (2, "the agent's part")


async def test_use_for_a_step_saved_again_after_she_looked_changes_nothing(supervisor):
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _allowed_automation()
    _agent_saves(PART, _part("the agent's part"))
    shown = grants.workflow_version(_stored())
    assert shown is not None and shown["use"] == {"version": 1, "steps": {PART: 2}}
    _agent_saves(PART, _part("the agent's second part"))

    stale = await _use({**shown["use"], "confirm": True})

    assert stale.status == 409 and _reply(stale)["error"]["code"] == "stale_write"
    assert "changed since you looked" in _reply(stale)["error"]["message"]
    assert grants.workflow_version(_stored())["steps"][0]["runs"] == 1


async def test_the_allow_says_it_holds_the_workflows_its_steps_start():
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Weekly report",
        kind="manual",
        created_by="user",
        workflow={"inline": {"provider": "run-workflow", "config": {"workflow": PARENT}}},
    )

    asked = grants.question(trigger)

    assert asked is not None
    assert f"and “{PART}”, which it runs as steps, as they are then too" in asked.sentence


async def test_the_workflows_page_lists_an_automation_that_runs_it_as_a_step():
    await _owner_saves(PART, _part("the owner's part"))
    await _owner_saves(PARENT, _runs_the_part_as_a_subworkflow())
    _allowed_automation()

    listed = _reply(
        await H.api_def_automations(
            _request("GET", f"/api/workflows/{PART}/automations", {}, match={"name": PART})
        )
    )

    assert [(a["id"], a["via"]) for a in listed["automations"]] == [(TRIGGER_ID, PARENT)]
    step = listed["automations"][0]["workflow_version"]["steps"][0]
    assert (step["workflow"], step["runs"], step["newer"]) == (PART, 1, 0)
