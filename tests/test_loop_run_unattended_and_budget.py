"""An "Unattended" General loop RUNS unattended, can WRITE, and is bounded by ITS OWN budget.

Measured 2026-09-25 on build 7b0e2eb19: a General loop created "Unattended" stopped on "This step
needs your approval to run" for every stage it started — six clicks before it escalated — and SEL
logged `subagent.scoped_trust_session_not_found` each time. Two causes, both measured here on the
real `general-project` spec driven through a real `RunController`:

1. **The loop door dropped the knobs.** `POST /api/loops` for a ported kind handed the run the task
   and nothing else, so `attended: false`, `max_cycles` and the loop's name never reached the run.
2. **Nothing carried the grant to the spawn.** A stage spawns under the RUN-OWNED key
   `workflow:<run>:<node>`, which is never a dashboard session, so the approval callback's trust
   lookup could only miss. The run is the thing that knows it is unattended; `dispatch_stage` now
   spawns an unattended run's stages `approval_mode="auto"`.

A third defect sat underneath and made the loop useless even when approved: `general-project`'s
`work` stage declares `tools_posture: full` and no `capability`, and since AG-11 made `capability`
the one decision, every such stage ran READ-ONLY — the worker's writes were denied, and the judge
found "no artifacts". `stage_capability` makes the declared posture load-bearing.

The only thing faked is the subagent manager, on the two methods `dispatch_stage` uses; what is
asserted is what the engine HANDED the manager, which is exactly the seam the defect lived on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.workflows import service, store, supervisor_policy
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import stage_capability
from personalclaw.workflows.models import RunStatus, WorkflowRun

TEMPLATE = "general-project"
TASK = "write a three-item checklist for the weekly team update"
RUN_TIMEOUT = 20.0


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Both stores under `tmp_path` — nothing here may touch the real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    return home


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _RecordingSubagents:
    """`SubagentManager` on `spawn` + `get`, recording every spawn's keyword arguments.

    ``progress`` decides the work stage's `meaningful_progress`: False ends the `until_dry` loop on
    its streak of 2, True keeps it going so only an iteration CAP can stop it.
    """

    def __init__(self, *, progress: bool = False) -> None:
        self.progress = progress
        self.spawns: list[dict[str, Any]] = []
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        self.spawns.append(kw)
        prompt = str(kw.get("task") or "")
        if "You are verifying work you did not do" in prompt:
            payload: dict[str, Any] = {
                "reasoning": "read the checklist file",
                "verdict": "PASS",
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["checklist.md"],
                "proof": "cat checklist.md",
                "cannot_judge": "",
            }
        else:
            payload = {
                "summary": f"step {len(self.spawns)}",
                "meaningful_progress": self.progress,
                "evidence": "checklist.md",
            }
        info = _Info(f"sub{len(self.spawns)}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


def _drive(overrides: dict[str, Any], fake: _RecordingSubagents) -> WorkflowRun:
    """Drive the REAL bundled spec (macros expanded) under ``overrides`` to a terminal status."""
    wf = read_template(TEMPLATE)
    assert wf is not None, f"{TEMPLATE} did not load through the bundled provider"
    spec = wf.to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={"task": TASK, "exit_condition": "the checklist exists"},
            policy_overrides=dict(overrides),
        )
    )
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    status = asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    assert status in (RunStatus.COMPLETE, RunStatus.ESCALATED), f"the run ended {status}"
    final = store.get(run.id)
    assert final is not None
    return final


def _by_stage(fake: _RecordingSubagents) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"work": [], "judge": []}
    for kw in fake.spawns:
        key = "judge" if "You are verifying work you did not do" in str(kw.get("task")) else "work"
        out[key].append(kw)
    return out


# ── the grant ──


def test_an_unattended_run_spawns_every_stage_without_asking() -> None:
    """`attended: false` in the overlay → every stage spawns `approval_mode="auto"`.

    At `origin/main` `dispatch_stage` passed only the node's own `approval_mode` (absent on every
    `general-project` stage), so each spawn went to `SubagentManager._spawn_with_approval` — the
    "This step needs your approval" card, once per stage.
    """
    fake = _RecordingSubagents()
    _drive({"attended": False}, fake)
    assert len(fake.spawns) >= 2, f"the run spawned {len(fake.spawns)} stage(s) — vacuous"
    modes = {kw.get("approval_mode") for kw in fake.spawns}
    assert modes == {"auto"}, f"an unattended run's stages spawned with approval modes {modes}"


@pytest.mark.parametrize("overrides", [{}, {"attended": True}, {"attended": 0}])
def test_a_run_without_the_explicit_grant_still_asks(overrides: dict[str, Any]) -> None:
    """The grant is an EXPLICIT `attended: false`, never a default.

    `Attention.AFK` is every template's declared default posture, so a run started from the
    Workflows page (empty overlay) must keep asking per stage exactly as it does today — and a
    malformed falsy value written by some other core is not consent either.
    """
    fake = _RecordingSubagents()
    _drive(overrides, fake)
    assert fake.spawns, "no stage spawned — vacuous"
    assert {kw.get("approval_mode") for kw in fake.spawns} == {None}, fake.spawns


def test_the_grant_predicate_reads_only_an_explicit_false() -> None:
    assert supervisor_policy.unattended_grant({"attended": False}) is True
    for not_consent in ({}, {"attended": True}, {"attended": 0}, {"attended": ""}, None):
        assert supervisor_policy.unattended_grant(not_consent) is False, not_consent


# ── the capability ──


def test_the_work_stage_may_write_and_the_judge_may_not() -> None:
    """`tools_posture: full` → mutating; the judge's `verify` posture stays read-only.

    At `origin/main` BOTH spawned `capability_class="research"`: the worker of a loop whose task
    was to write a checklist could not write one, and the judge then rejected it for having
    produced no artifact.
    """
    fake = _RecordingSubagents()
    _drive({"attended": False}, fake)
    stages = _by_stage(fake)
    assert stages["work"] and stages["judge"], f"missing a stage in {list(stages)}"
    assert {kw["capability_class"] for kw in stages["work"]} == {"mutating"}
    assert {kw["capability_class"] for kw in stages["judge"]} == {"research"}


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"tools_posture": "full"}, "mutating"),
        ({"tools_posture": "verify"}, "research"),
        ({}, "research"),
        ({"tools_posture": "full", "capability": "research"}, "research"),
        ({"capability": "mutating"}, "mutating"),
        ({"capability": "bogus", "tools_posture": "full"}, "mutating"),
    ],
)
def test_the_one_capability_decision(cfg: dict[str, Any], expected: str) -> None:
    assert stage_capability(cfg) == expected


# ── the budget ──


def test_the_runs_own_cycle_budget_bounds_the_loop() -> None:
    """`max_cycles: 1` stops a loop that keeps reporting progress after ONE iteration.

    Positive control below: with no override the same loop runs to the template's declared cap. At
    `origin/main` the override did not reach `max_iterations`, so both ran the template's literal 6.
    """
    fake = _RecordingSubagents(progress=True)
    _drive({"attended": False, "max_cycles": 1}, fake)
    assert (
        len(_by_stage(fake)["work"]) == 1
    ), f"a run with a budget of 1 cycle ran {len(_by_stage(fake)['work'])} work stages"


def test_without_a_budget_the_template_cap_applies() -> None:
    fake = _RecordingSubagents(progress=True)
    _drive({"attended": False}, fake)
    declared = (read_template(TEMPLATE).to_dict()["root"]["config"])["max_iterations"]
    assert len(_by_stage(fake)["work"]) == declared


# ── the door ──


class _RecordingSupervisor:
    def __init__(self) -> None:
        self.launched: list[str] = []

    def controller(self, run_id: str) -> None:
        return None

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> None:
        self.launched.append(run.id)


class _FakeState:
    def __init__(self) -> None:
        self.workflows = _RecordingSupervisor()
        self._sessions: dict[str, Any] = {}
        self._restricted_keys: set[str] = set()
        self._sse = None

    def push_refresh(self, *kinds: str) -> None:
        pass


class _OkPreflight:
    ok = True
    errors: tuple[Any, ...] = ()
    warnings: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ok": True}


@contextlib.contextmanager
def _launchable() -> Any:
    from personalclaw.workflows import preflight as preflight_mod
    from personalclaw.workflows.bundled_defs import PROVIDER_NAME, register_bundled_provider
    from personalclaw.workflows.defs import get_provider, unregister_provider

    preexisting = get_provider(PROVIDER_NAME) is not None
    register_bundled_provider()
    original = preflight_mod.preflight
    preflight_mod.preflight = lambda _spec: _OkPreflight()  # type: ignore[assignment]
    try:
        yield
    finally:
        preflight_mod.preflight = original  # type: ignore[assignment]
        if not preexisting:
            unregister_provider(PROVIDER_NAME)


def _create(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    app = web.Application()
    app["state"] = _FakeState()
    request = make_mocked_request("POST", "/api/loops", app=app)

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[assignment]
    with _launchable():
        response = asyncio.run(H.api_loop_create(request))
    return response.status, json.loads(response.body.decode())


def test_the_loop_door_carries_the_name_the_mode_and_the_budget_onto_the_run() -> None:
    """What the composer sends is what the run holds. At `origin/main` the run carried the task
    alone: no title, no loop kind, and an empty overlay — so it ran attended, on the template's
    budget, listed nowhere as a loop."""
    status, body = _create(
        {
            "kind": "general",
            "task": TASK,
            "name": "Weekly checklist",
            "attended": False,
            "max_cycles": 30,
        }
    )
    assert status == 202, body
    run = store.get(body["run_id"])
    assert run is not None
    assert run.loop_kind == "general"
    assert run.title == "Weekly checklist"
    assert run.policy_overrides == {"attended": False, "max_cycles": 30}


def test_the_title_falls_back_like_a_loops_row_does() -> None:
    status, body = _create({"kind": "general", "task": TASK, "title": "From the classifier"})
    assert status == 202, body
    assert store.get(body["run_id"]).title == "From the classifier"
    status, body = _create({"kind": "general", "task": TASK})
    assert status == 202, body
    assert store.get(body["run_id"]).title == TASK


def test_a_body_that_never_said_attended_leaves_the_overlay_without_it() -> None:
    status, body = _create({"kind": "general", "task": TASK})
    assert status == 202, body
    assert "attended" not in store.get(body["run_id"]).policy_overrides


def test_scratch_cleanup_is_refused_for_a_run_backed_kind_not_dropped() -> None:
    """A run-backed loop has no scratch dir; the flag used to vanish at the door. Positive control:
    an un-ported kind still accepts it."""
    status, body = _create({"kind": "general", "task": TASK, "auto_teardown_on_complete": True})
    assert status == 400, body
    assert any("Scratch" in e for e in body.get("errors", [])), body
    status, body = _create({"kind": "goal", "task": TASK, "auto_teardown_on_complete": True})
    assert status == 201, body


def test_start_run_refuses_an_unknown_overlay_key() -> None:
    """The create-time door is as strict as the write seam: a typo'd knob is refused, not stored."""

    async def _go() -> dict[str, Any]:
        with _launchable():
            return await service.start_run(
                name=TEMPLATE,
                inputs={"task": TASK},
                policy_overrides={"atended": False},
                skip_preflight=True,
            )

    result = asyncio.run(_go())
    assert result.get("ok") is False
    assert result.get("code") == "WF_POLICY_KEY_UNKNOWN", result
    runs, _ = store.list_runs()
    assert runs == [], "a refused start still created a run"
