"""A secret a workflow run is handed stays its reference until a step of the run uses it.

Three things hand a run its inputs: an automation's Run workflow action (its fire, and Run now), a
workflow step that starts a run, and a subworkflow step. A ``{{secret:NAME}}`` in those inputs was
filled in where they were handed on, so the secret's value was written into everything that records
the run: its inputs, the opening row of its ledger, the run list the API serves, and the prompt a
model step of the run was given, which the model was sent; and a preview of the action wrote it into
the automation's history. Measured before this change, every test below that looks for the value
found it.

What the code does now: a run is handed the reference, and the record keeps it. The run fills it in
where one of its steps uses the input, through the one resolver (the run's project first, then the
global secret), and that step's ledger row names the secret and where it came from. A step whose
text goes to a model keeps it as the name. A run fills only the references that the one who started
it wrote: text that reads as one in any other input stays text, and a step that would hand a run
such text beside a reference to the same secret is refused. A run's record that already holds a
stored secret's value is rewritten to hold the reference instead (``input_secrets.redact_home``).

Every test writes credentials, so the home is redirected and the redirect asserted first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import live_runs
import pytest
import pytest_asyncio

from personalclaw.action_providers.base import ActionResult
from personalclaw.config import credentials as cred
from personalclaw.config import loader
from personalclaw.workflows import defs as defs_mod

NAME = "ORCHARD_TOKEN"
OTHER = "PRUNING_TOKEN"
PROJECT = "p-4e5f6a7b"

#: Long, unique, sharing no substring with a key name, so a match can only be a VALUE.
VALUE = "ov-6d2b91f0-ORCHARD-SECRET-VALUE"
PROJECT_VALUE = "pv-8c1d2e3f-ORCHARD-PROJECT-VALUE"
OTHER_VALUE = "xv-0a9b8c7d-PRUNING-SECRET-VALUE"
ALL_VALUES = (VALUE, PROJECT_VALUE, OTHER_VALUE)

REFERENCE = "{{secret:" + NAME + "}}"
OTHER_REFERENCE = "{{secret:" + OTHER + "}}"

#: A name only the gateway's environment holds: no run reads it, so nothing that hands one on
#: starts.
HOST = "ORCHARD_HOST_TOKEN"
HOST_VALUE = "hv-3c9e1a77-ORCHARD-HOST-VALUE"
HOST_REFERENCE = "{{secret:" + HOST + "}}"

pytestmark = pytest.mark.security


def _project_key(project_id: str, name: str = NAME) -> str:
    from personalclaw.secrets_vault import project_secret_key

    return project_secret_key(project_id, name)


@pytest_asyncio.fixture(autouse=True)
async def _stopped_runs():
    """Each run a test started, stopped before its loop closes (``live_runs``)."""
    yield
    await live_runs.stop_started()


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The test's own home (the suite redirects it), asserted before anything is written."""
    cfg = loader.config_dir()
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    assert cfg != Path.home() / ".personalclaw"
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    for key in (NAME, OTHER, HOST, _project_key(PROJECT)):
        # Registered first, so monkeypatch removes whatever a save mirrors in.
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id"):
        monkeypatch.delenv(key, raising=False)
    return cfg


def _store(name: str = NAME, value: str = VALUE, project_id: str = "") -> None:
    """A secret as Settings → Secrets keeps it, out of this process's environment, as after a
    restart."""
    key = _project_key(project_id, name) if project_id else name
    cred.save_credential(key, value)
    os.environ.pop(key, None)


# ── the runs ─────────────────────────────────────────────────────────────────


def _inputs(*names: str) -> dict[str, Any]:
    return {n: {"type": "string", "required": True} for n in names}


#: The workflow that uses the secret: one step that sends it in a header.
SYNC = {
    "name": "orchard-sync",
    "version": 1,
    "inputs": _inputs("token"),
    "root": {
        "kind": "action",
        "id": "send",
        "config": {"provider": "recorder", "with": {"auth": "Bearer {{inputs.token}}"}},
    },
}
#: One whose step is a model's text.
NOTE = {
    "name": "orchard-note",
    "version": 1,
    "inputs": _inputs("token"),
    "root": {"kind": "infer", "id": "write", "config": {"prompt": "Note {{inputs.token}}."}},
}
#: A step that runs another workflow as a child run, naming the secret itself.
PARENT = {
    "name": "orchard-parent",
    "version": 1,
    "root": {
        "kind": "subworkflow",
        "id": "child",
        "config": {"ref": "orchard-sync", "inputs": {"token": REFERENCE}},
    },
}
#: A step that hands its child an input its own run was handed.
RELAY = {
    "name": "orchard-relay",
    "version": 1,
    "inputs": _inputs("token"),
    "root": {
        "kind": "subworkflow",
        "id": "child",
        "config": {"ref": "orchard-sync", "inputs": {"token": "{{inputs.token}}"}},
    },
}
#: A step that starts a run of another workflow (fire and forget).
STARTER = {
    "name": "orchard-starter",
    "version": 1,
    "root": {
        "kind": "action",
        "id": "start",
        "config": {
            "provider": "run-workflow",
            "with": {"workflow": "orchard-sync", "inputs": {"token": REFERENCE}},
        },
    },
}
#: STARTER with its provider written as a binding that the run's input `which` fills.
BOUND_STARTER = {
    "name": "orchard-bound-starter",
    "version": 1,
    "inputs": _inputs("which"),
    "root": {
        "kind": "action",
        "id": "start",
        "config": {
            "provider": "{{inputs.which}}",
            "with": {"workflow": "orchard-sync", "inputs": {"token": REFERENCE}},
        },
    },
}
#: The same workflow as SYNC, with a second input a step's output fills.
SYNC_WITH_NOTE = {
    "name": "orchard-sync-noted",
    "version": 1,
    "inputs": _inputs("token", "note"),
    "root": {
        "kind": "action",
        "id": "send",
        "config": {
            "provider": "recorder",
            "with": {"auth": "Bearer {{inputs.token}}", "note": "{{inputs.note}}"},
        },
    },
}


def _passes_on(note_from_output: bool, *, with_token: bool) -> dict[str, Any]:
    """A run whose first step's output says something, handed on to a child by the second."""
    inputs: dict[str, Any] = {"note": "{{nodes.read.output.note}}"} if note_from_output else {}
    if with_token:
        inputs["token"] = REFERENCE
    return {
        "name": f"orchard-pass-{int(note_from_output)}-{int(with_token)}",
        "version": 1,
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "action", "id": "read", "config": {"provider": "says", "with": {}}},
                {
                    "kind": "subworkflow",
                    "id": "child",
                    "config": {
                        "ref": "orchard-sync-noted" if with_token else "orchard-note-only",
                        "inputs": inputs,
                    },
                },
            ],
        },
    }


NOTE_ONLY = {
    "name": "orchard-note-only",
    "version": 1,
    "inputs": _inputs("note"),
    "root": {
        "kind": "action",
        "id": "send",
        "config": {"provider": "recorder", "with": {"note": "{{inputs.note}}"}},
    },
}
#: SYNC as it reads after its input was declared a number.
COUNT = {**SYNC, "name": "orchard-count", "inputs": {"token": {"type": "number"}}}

#: A workflow whose first step leaves a mark, before the step that reads its input.
FIRST_STEP = {
    "name": "orchard-first-step",
    "version": 1,
    "inputs": _inputs("token"),
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "action", "id": "mark", "config": {"provider": "recorder", "with": {"m": 1}}},
            {
                "kind": "action",
                "id": "send",
                "config": {"provider": "recorder", "with": {"auth": "Bearer {{inputs.token}}"}},
            },
        ],
    },
}
#: The two steps that start FIRST_STEP, handing it the name only the environment holds.
HOST_STARTER = {
    "name": "orchard-host-starter",
    "version": 1,
    "root": {
        "kind": "action",
        "id": "start",
        "config": {
            "provider": "run-workflow",
            "with": {"workflow": "orchard-first-step", "inputs": {"token": HOST_REFERENCE}},
        },
    },
}
HOST_PARENT = {
    "name": "orchard-host-parent",
    "version": 1,
    "root": {
        "kind": "subworkflow",
        "id": "child",
        "config": {"ref": "orchard-first-step", "inputs": {"token": HOST_REFERENCE}},
    },
}

SPECS = (
    FIRST_STEP,
    SYNC,
    COUNT,
    NOTE,
    PARENT,
    RELAY,
    STARTER,
    SYNC_WITH_NOTE,
    NOTE_ONLY,
    _passes_on(True, with_token=True),
    _passes_on(True, with_token=False),
)


class _Defs(defs_mod.WorkflowDefProvider):
    @property
    def name(self) -> str:
        return "orchard-defs"

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return list(SPECS)[offset : offset + limit], len(SPECS)

    async def get_def(self, name: str):
        return next((dict(spec) for spec in SPECS if spec["name"] == name), None)


class _Recorder:
    """The action a run's step sends (``recorder``), and the model its infer step asks: each keeps
    what it is handed."""

    hands_config_to_a_model = False

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.prompts: list[str] = []

    async def execute(self, cfg: dict[str, Any], ctx: Any, timeout: int = 30) -> ActionResult:
        self.sent.append(json.loads(json.dumps(cfg, default=str)))
        return ActionResult(success=True, stdout=json.dumps({"ok": True}))

    async def complete(self, prompt: str, *, use_case: str = "", output_type: Any = None) -> str:
        self.prompts.append(prompt)
        return "noted"


class _Says:
    """A step whose output carries text that reads as a secret reference (``says``)."""

    hands_config_to_a_model = False

    def __init__(self) -> None:
        self.note = ""

    async def execute(self, cfg: dict[str, Any], ctx: Any, timeout: int = 30) -> ActionResult:
        return ActionResult(success=True, stdout=json.dumps({"note": self.note}))


class _Supervisor:
    """Starts each run as the gateway's supervisor does, with the two fixture actions."""

    def __init__(self) -> None:
        self.recorder = _Recorder()
        self.says = _Says()
        self.controllers: list[Any] = []

    def _provider(self, name: str) -> Any:
        from personalclaw.action_providers.registry import get_action_provider

        return {"recorder": self.recorder, "says": self.says}.get(name) or get_action_provider(name)

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> Any:
        from personalclaw.workflows.controller import EngineServices, RunController

        services = EngineServices(
            get_provider=self._provider, completion=self.recorder.complete, supervisor=self
        )
        controller = live_runs.started(RunController(run, spec, services=services, depth=depth))
        self.controllers.append(controller)
        await controller.start()
        return controller

    async def settle(self) -> None:
        """Wait until every run it started, and every run those started, has ended and its
        projected tasks are written (a run's task write lands on a thread of its own)."""
        done = 0
        while done < len(self.controllers):
            await self.controllers[done].wait_for_terminal(timeout=20)
            await self.controllers[done].drain_projection_writes()
            done += 1


@pytest.fixture
def runs(home, monkeypatch) -> _Supervisor:
    from personalclaw.action_providers import registry
    from personalclaw.action_providers import services as services_mod

    registry._ensure_default_providers_registered()
    supervisor = _Supervisor()
    monkeypatch.setattr(
        services_mod,
        "_services",
        services_mod.ActionServices(state=MagicMock(), workflows=supervisor),
    )
    provider = _Defs()
    defs_mod.register_provider(provider)
    yield supervisor
    defs_mod.unregister_provider(provider.name)


def _automation(config: dict[str, Any], tid: str = "clock:orchard") -> Any:
    return SimpleNamespace(
        id=tid,
        name="Orchard sync",
        kind="clock",
        workflow={"inline": {"provider": "run-workflow", "config": config}},
        # Granted, as a created trigger is (`triggers.grants`).
        capabilities={"providers": ["run-workflow"]},
    )


async def _fire(trigger: Any) -> None:
    """The automation's scheduled fire, through the gateway's one dispatch for it."""
    from personalclaw.gateway import GatewayOrchestrator

    await object.__new__(GatewayOrchestrator)._fire_store_trigger(
        trigger, {"trigger_id": trigger.id}
    )


async def _run_now(trigger: Any) -> tuple[bool, str]:
    """Run now on the Triggers page, through the dispatch every run by hand takes: whether it ran,
    and what the page tells the owner."""
    from personalclaw.dashboard.handlers.trigger_runs import _dispatch_store_action

    return await _dispatch_store_action(trigger, {}, state=None)


DISPATCHES = {"its-fire": _fire, "run-now": _run_now}


async def _start(spec: dict[str, Any], runs: _Supervisor, *, project_id: str = "", **inputs: Any):
    """A run started by hand (``store.create`` and its supervisor, as the Run button's start
    does once its checks pass)."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import WorkflowRun

    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs, project_id=project_id)
    )
    store.write_spec(run.id, spec)
    await runs.launch(run, spec)
    await runs.settle()
    return store.get(run.id)


def _runs_of(name: str) -> list[Any]:
    from personalclaw.workflows import store

    return store.list_runs(workflow_name=name, limit=200)[0]


def _only_run_of(name: str) -> Any:
    found = _runs_of(name)
    assert len(found) == 1, [r.id for r in found]
    return found[0]


def _homes(home: Path) -> list[Path]:
    """The home, and where the Triggers page's handlers keep the trigger store and its history
    (a test may point those at a home of its own)."""
    from personalclaw.dashboard.handlers.triggers import _runs_store

    return sorted({home, Path(_runs_store()._dir).parent})


#: How many times the home is listed and read before a scan that keeps losing files fails.
_SCANS = 5


def _holders(home: Path, values: tuple[str, ...] = ALL_VALUES) -> list[str]:
    """Every file that holds one of *values*, but the credential store's own: under the home, and
    where the Triggers page's handlers write.

    What one listing holds, every file of it read. A store writes a file by renaming a temporary
    file over it, and a new record is not in the listing until then, so a file listed and gone by
    its check or its read sends the scan round again rather than being passed over. A file a round
    read holding a value stays named though it is gone by the next round."""
    held: set[str] = set()
    gone: list[str] = []
    for _ in range(_SCANS):
        found: list[str] = []
        gone = []
        for root in _homes(home):
            for path in sorted(root.rglob("*")):
                if path.name == ".env":
                    continue
                if not path.is_file():
                    # A folder, or a file gone since the listing: is_file() says no to both.
                    if not os.path.lexists(path):
                        gone.append(str(path))
                    continue
                try:
                    data = path.read_bytes()
                except FileNotFoundError:
                    gone.append(str(path))
                    continue
                if any(value.encode() in data for value in values):
                    found.append(str(path))
        held.update(found)
        if not gone:
            return sorted(held)
    raise AssertionError(
        f"{_SCANS} scans of the home in a row lost a file before it was read: {gone}"
    )


def _secret_rows(run_id: str) -> list[tuple[str, str]]:
    from personalclaw.workflows import journal

    return [
        (str(e.get("name")), str(e.get("scope")))
        for e in journal.ledger(run_id)
        if e.get("kind") == "secret_read"
    ]


def _history(home: Path, tid: str) -> str:
    """What the automation's history holds, wherever its dispatch recorded it."""
    paths = [root / "cron-history" / f"{tid}.jsonl" for root in _homes(home)]
    return "".join(p.read_text(encoding="utf-8") for p in paths if p.exists())


# ── an automation's Run workflow action ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_an_automation_hands_its_run_the_reference_and_the_step_uses_the_value(
    home, runs, dispatch
):
    _store()

    await DISPATCHES[dispatch](
        _automation({"workflow": "orchard-sync", "inputs": {"token": REFERENCE}})
    )
    await runs.settle()

    # The positive control: where the run uses the input, it has the value.
    assert runs.recorder.sent == [{"auth": f"Bearer {VALUE}"}], runs.recorder.sent
    run = _only_run_of("orchard-sync")
    assert run.inputs == {"token": REFERENCE}
    assert _secret_rows(run.id) == [(NAME, "global")]
    # Nothing that records the run or the fire holds the value: the run's inputs, its ledger, its
    # files, the run list and the automation's history.
    assert _holders(home) == []
    from personalclaw.workflows import store

    assert VALUE not in json.dumps([r.to_dict() for r in store.list_runs(limit=50)[0]])


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_a_preview_of_the_action_keeps_the_reference_in_the_automations_history(
    home, runs, dispatch
):
    _store()
    trigger = _automation(
        {"workflow": "orchard-sync", "dry_run": True, "inputs": {"token": REFERENCE}},
        tid="clock:orchard-preview",
    )

    await DISPATCHES[dispatch](trigger)

    history = _history(home, trigger.id)
    assert _holders(home) == []
    assert REFERENCE in history, "vacuity: the preview's row names the inputs it would start with"
    assert _runs_of("orchard-sync") == [], "a preview starts nothing"


@pytest.mark.asyncio
async def test_a_model_step_is_handed_the_name_and_never_the_value(home, runs):
    _store()

    await _fire(_automation({"workflow": "orchard-note", "inputs": {"token": REFERENCE}}))
    await runs.settle()

    assert runs.recorder.prompts == [f"Note {REFERENCE}."], runs.recorder.prompts
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_runs_project_reads_its_own_secret_for_a_reference_it_was_handed(home, runs):
    """The run is the project's work, so a reference it was handed reads as one its own step
    writes: the project's secret first, then the global one."""
    _store()
    _store(value=PROJECT_VALUE, project_id=PROJECT)

    await _fire(
        _automation(
            {"workflow": "orchard-sync", "project_id": PROJECT, "inputs": {"token": REFERENCE}}
        )
    )
    await runs.settle()

    assert runs.recorder.sent == [{"auth": f"Bearer {PROJECT_VALUE}"}], runs.recorder.sent
    run = _only_run_of("orchard-sync")
    assert _secret_rows(run.id) == [(NAME, "project")]
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_secret_only_the_runs_project_holds_starts_the_run(home, runs):
    _store(value=PROJECT_VALUE, project_id=PROJECT)

    await _fire(
        _automation(
            {"workflow": "orchard-sync", "project_id": PROJECT, "inputs": {"token": REFERENCE}}
        )
    )
    await runs.settle()

    assert runs.recorder.sent == [{"auth": f"Bearer {PROJECT_VALUE}"}], runs.recorder.sent


@pytest.mark.asyncio
async def test_a_secret_that_is_not_stored_refuses_the_fire_by_its_name(home, runs):
    trigger = _automation({"workflow": "orchard-sync", "inputs": {"token": REFERENCE}})

    await _fire(trigger)
    await runs.settle()

    assert _runs_of("orchard-sync") == [], "nothing starts on a secret that is not there"
    history = _history(home, trigger.id)
    assert '"refused"' in history and NAME in history, history


@pytest.mark.asyncio
async def test_a_workflow_input_that_changed_type_is_refused_without_quoting_the_value(home, runs):
    """The workflow can change after the automation was saved: its refusal quotes what it was
    handed, and that is the reference."""
    _store()
    trigger = _automation({"workflow": "orchard-count", "inputs": {"token": REFERENCE}})

    await _fire(trigger)

    history = _history(home, trigger.id)
    assert _holders(home) == []
    assert f"expected number, got '{REFERENCE}'" in history, history


# ── a step that starts a run ───────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", [PARENT, STARTER], ids=["a-subworkflow", "a-run-workflow-step"])
async def test_a_step_hands_the_run_it_starts_the_reference(home, runs, spec):
    _store()

    await _start(spec, runs)

    assert runs.recorder.sent == [{"auth": f"Bearer {VALUE}"}], runs.recorder.sent
    child = _only_run_of("orchard-sync")
    assert child.inputs == {"token": REFERENCE}
    assert _secret_rows(child.id) == [(NAME, "global")]
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_run_workflow_step_named_by_an_input_hands_the_run_it_starts_the_reference(
    home, runs
):
    """A step whose provider comes from its run's input is the step it names: the run it starts is
    handed the reference, counted on its record, and fills it where its own step uses it.
    🔴 Red before: the child run's inputs held the value, with nothing on its record."""
    _store()

    await _start(BOUND_STARTER, runs, which="run-workflow")

    assert runs.recorder.sent == [{"auth": f"Bearer {VALUE}"}], runs.recorder.sent
    child = _only_run_of("orchard-sync")
    assert child.inputs == {"token": REFERENCE}
    assert _secret_rows(child.id) == [(NAME, "global")]
    assert _holders(home) == []


_HANDS_ON = {"workflow": "orchard-first-step", "inputs": {"token": HOST_REFERENCE}}


async def _by_a_fire(home: Path, runs: _Supervisor) -> str:
    """An automation's fire, refused: what its history shows the owner."""
    trigger = _automation(_HANDS_ON, "clock:orchard-host")
    await _fire(trigger)
    rows = [json.loads(line) for line in _history(home, trigger.id).splitlines() if line.strip()]
    refused = [row for row in rows if row.get("status") == "refused"]
    assert refused, rows
    return json.dumps(refused)


async def _by_run_now(home: Path, runs: _Supervisor) -> str:
    """Run now, refused: what the page tells the owner."""
    ran, why = await _run_now(_automation(_HANDS_ON, "clock:orchard-host"))
    assert ran is False, why
    return why


def _by_a_step(spec: dict[str, Any]) -> Any:
    """A step that would start the run, refused: what its parent run's failure tells the owner."""

    async def _door(home: Path, runs: _Supervisor) -> str:
        from personalclaw.workflows.models import RunStatus

        parent = await _start(spec, runs)
        assert parent.status == RunStatus.FAILED, parent.status
        return str(parent.error_message)

    return _door


async def _by_a_hook(home: Path, runs: _Supervisor) -> str:
    """A lifecycle trigger whose action is Run workflow, as it runs when its event comes: what its
    result tells the owner."""
    from personalclaw.hooks import ScriptHookStore, run_script_hook

    store = ScriptHookStore(home)
    store.create(
        {
            "id": "orchard-hook",
            "name": "Orchard on stop",
            "event": "Stop",
            "provider": "run-workflow",
            "provider_config": _HANDS_ON,
            "capabilities": {"providers": ["run-workflow"]},
        }
    )
    result = await run_script_hook(store.get("orchard-hook"), "", {"hook_event_name": "Stop"})
    return result.error


#: Each door that would hand a run the reference, and what it tells the owner when it refuses.
HOST_DOORS = {
    "an-automations-fire": _by_a_fire,
    "run-now": _by_run_now,
    "a-run-workflow-step": _by_a_step(HOST_STARTER),
    "a-subworkflow-step": _by_a_step(HOST_PARENT),
    "a-lifecycle-trigger": _by_a_hook,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("door", sorted(HOST_DOORS))
async def test_a_secret_only_the_gateways_environment_holds_starts_no_run(
    home, runs, monkeypatch, door
):
    """What hands a run a reference checks it as the run will read it, from Settings → Secrets
    alone: a name only the gateway's environment holds is one the run doesn't have, so no run is
    started, nothing of it runs, and the refusal the owner is shown names it. 🔴 Red before: the
    run started, and its first step ran."""
    monkeypatch.setenv(HOST, HOST_VALUE)

    said = await HOST_DOORS[door](home, runs)
    await runs.settle()

    assert _runs_of("orchard-first-step") == [], "a run was started"
    assert runs.recorder.sent == [], "a step of the run ran"
    assert HOST in said, said
    assert HOST_VALUE not in said


@pytest.mark.asyncio
async def test_a_reference_a_run_was_handed_goes_on_to_its_child_as_the_reference(home, runs):
    _store()

    await _fire(_automation({"workflow": "orchard-relay", "inputs": {"token": REFERENCE}}))
    await runs.settle()

    assert runs.recorder.sent == [{"auth": f"Bearer {VALUE}"}], runs.recorder.sent
    assert _only_run_of("orchard-relay").inputs == {"token": REFERENCE}
    assert _only_run_of("orchard-sync").inputs == {"token": REFERENCE}
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_reference_typed_as_a_runs_input_stays_text(home, runs):
    """A run fills only what the one who started it handed it as a reference: text that reads as
    one in an input typed at Run, given by an agent or by a caller from outside is that text."""
    _store()

    await _start(SYNC, runs, token=REFERENCE)

    assert runs.recorder.sent == [{"auth": f"Bearer {REFERENCE}"}], runs.recorder.sent
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_reference_a_steps_output_carries_reaches_the_child_as_text(home, runs):
    _store(name=OTHER, value=OTHER_VALUE)
    runs.says.note = OTHER_REFERENCE

    parent = await _start(_passes_on(True, with_token=False), runs)

    from personalclaw.workflows.models import RunStatus

    assert parent.status == RunStatus.COMPLETE, parent.error_message
    assert runs.recorder.sent == [{"note": OTHER_REFERENCE}], runs.recorder.sent
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_step_that_would_hand_on_text_naming_the_secret_it_hands_on_is_refused(home, runs):
    """The run could not tell that text from the reference its author wrote, so the step that
    starts it is refused and says why."""
    _store()
    runs.says.note = REFERENCE

    parent = await _start(_passes_on(True, with_token=True), runs)

    from personalclaw.workflows.models import RunStatus

    assert parent.status != RunStatus.COMPLETE
    assert _runs_of("orchard-sync-noted") == [], "the child is never started"
    assert runs.recorder.sent == []
    from personalclaw.workflows import store

    failure = store.read_state(parent.id)["root.children[1]"].failure
    assert failure is not None and NAME in failure.cause_plain, failure
    assert _holders(home) == []


# ── a fork, and the records written before this change ──────────────────────────


@pytest.mark.asyncio
async def test_a_fork_of_the_run_fills_the_reference_as_its_run_did(home, runs):
    from personalclaw.workflows import store
    from personalclaw.workflows.checkpoints import fork_run

    _store()
    await _fire(_automation({"workflow": "orchard-sync", "inputs": {"token": REFERENCE}}))
    await runs.settle()
    parent = _only_run_of("orchard-sync")

    fork = fork_run(parent, store.read_spec(parent.id) or SYNC, {}).child
    await runs.launch(fork, store.read_spec(fork.id) or SYNC)
    await runs.settle()

    assert store.get(fork.id).inputs["token"] == REFERENCE
    assert runs.recorder.sent == [{"auth": f"Bearer {VALUE}"}] * 2, runs.recorder.sent
    assert _holders(home) == []


def _a_run_as_it_was_recorded(prompt: bool = True) -> str:
    """Runs an automation started before this change: their inputs, their opening ledger rows, the
    prompt a model step was given and a preview's history row all hold the value. Their controllers
    saved them as they went, and each save that moved a row left its old copy in the database
    file's free space. The id of the first."""
    from personalclaw.workflows import store
    from personalclaw.workflows.journal import Journal
    from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

    made = []
    for _ in range(6):
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name="orchard-sync",
                inputs={"token": VALUE, "count": 3},
                origin=RunOrigin(kind=OriginKind.HOOK, trigger_id="clock:orchard"),
            )
        )
        store.write_spec(run.id, SYNC)
        Journal(run.id).run_started("orchard-sync", inputs=dict(run.inputs), spec_version=1)
        made.append(run)
    for tick in range(4):
        for run in made:
            run.status = RunStatus.COMPLETE if tick == 3 else RunStatus.RUNNING
            run.started_at = "2026-10-04T07:41:38Z"
            run.error_message = "still syncing " * tick
            store.save(run)
    if prompt:
        store.write_output(made[0].id, "root::prompt", f"Note {VALUE}.")
    history = loader.config_dir() / "cron-history" / "clock:orchard.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    preview = json.dumps({"dry_run": True, "inputs": {"token": VALUE}})
    history.write_text(json.dumps({"summary": preview, "trace": preview}) + "\n", encoding="utf-8")
    return made[0].id


def test_a_record_written_before_holds_the_reference_once_the_home_is_redacted(home):
    from personalclaw.workflows import input_secrets, journal, store

    _store()
    run_id = _a_run_as_it_was_recorded()
    assert _holders(home), "vacuity: the record holds the value as it was written"

    input_secrets.redact_home()

    assert _holders(home) == []
    run = store.get(run_id)
    assert run.inputs == {"token": REFERENCE, "count": 3}
    opening = journal.journal_records(run_id, kinds={"run_started"})
    assert opening and opening[0]["inputs"] == {"token": REFERENCE, "count": 3}, opening
    assert REFERENCE in _history(home, "clock:orchard")


@pytest.mark.asyncio
async def test_a_redacted_run_fills_the_reference_where_a_fork_uses_it(home, runs):
    from personalclaw.workflows import input_secrets, store
    from personalclaw.workflows.checkpoints import fork_run

    _store()
    run_id = _a_run_as_it_was_recorded(prompt=False)
    input_secrets.redact_home()

    parent = store.get(run_id)
    fork = fork_run(parent, SYNC, {}).child
    await runs.launch(fork, SYNC)
    await runs.settle()

    assert runs.recorder.sent == [{"auth": f"Bearer {VALUE}"}], runs.recorder.sent


def test_redacting_twice_changes_nothing_the_first_pass_did_not(home):
    from personalclaw.workflows import input_secrets

    _store()
    _a_run_as_it_was_recorded()
    first = input_secrets.redact_home()
    snapshot = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}

    second = input_secrets.redact_home()

    assert first and not second, (first, second)
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == snapshot


def test_a_value_no_stored_secret_holds_is_left_as_it_was(home):
    """Only a stored secret's value is known to be one: anything else in a run's inputs is the
    run's own."""
    from personalclaw.workflows import input_secrets, store

    _a_run_as_it_was_recorded(prompt=False)
    run_id = _runs_of("orchard-sync")[0].id

    assert not input_secrets.redact_home()
    assert store.get(run_id).inputs == {"token": VALUE, "count": 3}


# ── the scan ─────────────────────────────────────────────────────────────────


def test_a_file_read_holding_a_value_stays_named_when_a_later_round_lost_it(home, monkeypatch):
    """A round that loses a file goes round again, and a file that round read holding one of the
    values is still named though it is gone by the next listing: the scan never drops a finding.
    Here one temporary file holds the other secret's value and is removed just after its read,
    and another is gone at its read, so the second round finds neither, and the scan says the
    first held a value."""
    tasks = home / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    holder, lost = tasks / "tmp-a-holder.tmp", tasks / "tmp-b-lost.tmp"
    holder.write_text(f"pushed with {OTHER_VALUE}", encoding="utf-8")
    lost.write_text("{}", encoding="utf-8")
    read = Path.read_bytes

    def gone_after_its_read(self: Path) -> bytes:
        if self == lost and lost.exists():
            lost.unlink()
            raise FileNotFoundError(2, "No such file or directory", str(self))
        data = read(self)
        if self == holder:
            holder.unlink()
        return data

    monkeypatch.setattr(Path, "read_bytes", gone_after_its_read)

    assert _holders(home) == [str(holder)]
