"""A workflow step whose helper a spend cap stopped ends as the cap's stop, with Retry.

🔴 The defect (Backlog: "A spend cap that stops a General loop's step is told to change the
step"). A spend cap's refusal is typed where it is raised and in the engine's own calls, but a
stage's helper kept only the cap's sentence (`SubagentInfo.error`), and the stage settlement had no
case for it: it settled INTERNAL, "check the subagent's transcript for the failing turn". So an
unattended General loop the cap stopped ended Escalated with "the loop spent its iterations failing
rather than working", told her to change the step that gave up, and offered no Retry.

Now the helper keeps the cap's own words (`SubagentInfo.spend_cap`, where `budget_refusal`
matched), and the stage settles as the cap's stop (`failure_taxonomy.budget_failure`, the shape the
engine's own calls get): class `budget`, the cap's headline, its fix and Retry, which the loop's
stop reads as a step that stopped on a spend cap.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.guardrails.failure import NO_ROOM, BudgetExceededError
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.workflows import loop_aliases, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.ending_sentence import SPEND_CAP, step_cause
from personalclaw.workflows.models import FailureClass, InstanceState, RunStatus, WorkflowRun

TEMPLATE = loop_aliases.KIND_TO_TEMPLATE["general"]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


WORK_PROMPT = "Do the next meaningful step"


def _refusal(scope: str = "day") -> BudgetExceededError:
    return BudgetExceededError(
        scope, "dollars", 4.0, 3.944, why=NO_ROOM, needed=0.299, ref="cloud:example-large"
    )


async def _as_run_leaves_it(refusal: BudgetExceededError) -> SubagentInfo:
    """A helper the cap refused, as `SubagentManager._run` leaves it."""
    sessions = MagicMock()
    sessions.reset = AsyncMock()
    manager = SubagentManager(sessions=sessions, ctx_builder=MagicMock())

    async def _refused(info: Any, session_key: str) -> None:
        raise refusal

    manager._run_inner = _refused  # type: ignore[method-assign]
    manager._write_tombstone = lambda *_a, **_kw: None  # type: ignore[method-assign]
    manager._fire_event = AsyncMock()  # type: ignore[method-assign]
    info = SubagentInfo(id="sub", task="Do the next meaningful step.")
    await asyncio.wait_for(manager._run(info), timeout=5)
    return info


def test_a_helper_a_spend_cap_refused_keeps_the_caps_own_words():
    """🔴 Before: only the sentence survived (`info.error`), so nothing could tell it was a cap."""
    for scope in ("day", "run"):
        refusal = _refusal(scope)
        info = asyncio.run(_as_run_leaves_it(refusal))
        assert info.error == refusal.sentence()
        assert info.spend_cap == {
            "headline": refusal.headline(),
            "fix": refusal.fix(),
            "scope": scope,
        }, info.spend_cap
    ordinary = SubagentInfo(id="x", task="t")
    assert ordinary.spend_cap == {}, "no cap, nothing kept"


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""
        self.spend_cap: dict[str, str] = {}


class _Helpers:
    """The template's helpers: the work step's is ended by *ending* (a cap's, or an error of its
    own); the judge, which never runs once the work fails, would pass."""

    def __init__(self, ending: SubagentInfo) -> None:
        self.ending = ending
        self.infos: dict[str, _Info] = {}
        self.ends: set[str] = set()

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        info = _Info(f"sub{len(self.infos) + 1}", json.dumps({"summary": "s"}))
        self.infos[info.id] = info
        if WORK_PROMPT in prompt:
            self.ends.add(info.id)
        return info

    def get(self, agent_id: str) -> _Info | None:
        """The helper as it is when it has ended: the work step's with *ending*'s error and cap,
        which `SubagentManager._run` writes once the helper has started, never at its spawn."""
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
            if agent_id in self.ends:
                info.result = ""
                info.error = self.ending.error
                info.spend_cap = dict(getattr(self.ending, "spend_cap", None) or {})
        return info


def _drive(ending: SubagentInfo) -> RunController:
    template = read_template(TEMPLATE)
    assert template is not None
    spec = template.to_dict()
    run = store.create(
        WorkflowRun(id="", workflow_name=TEMPLATE, inputs={"task": "t", "exit_condition": "e"})
    )
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(subagents=_Helpers(ending)))
    asyncio.run(controller.run_to_completion(timeout=20.0))
    return controller


def _failed(controller: RunController) -> Any:
    failed = [i for i in controller.instances.values() if i.state is InstanceState.FAILED]
    assert failed, "positive control: the work step failed"
    return failed[0].failure


def test_a_general_loop_a_spend_cap_stopped_ends_as_the_caps_stop():
    """🔴 Before: the step settled INTERNAL, the loop said it spent its iterations failing, told her
    to change the step, and the run page offered no Retry."""
    refusal = _refusal()
    controller = _drive(asyncio.run(_as_run_leaves_it(refusal)))
    failure = _failed(controller)
    assert failure.failure_class is FailureClass.BUDGET, failure
    assert failure.cause_plain == refusal.headline(), failure
    assert failure.remediation == f"{refusal.fix()}, then Retry", failure
    assert failure.budget_scope == refusal.scope == "day", failure
    assert step_cause(failure) == SPEND_CAP
    assert controller.run.status is RunStatus.ESCALATED, controller.run.status
    attention = controller.run.attention or {}
    assert attention.get("cause") == SPEND_CAP, attention
    detail = str(attention.get("detail") or "")
    assert " stopped: " in detail and " failed: " not in detail, detail
    assert "change the step" not in json.dumps(attention).lower(), attention


def test_a_helper_that_failed_on_its_own_is_still_the_steps_failure():
    """The control: an error no cap raised is the step's, as before."""
    ending = SubagentInfo(id="sub", task="t")
    ending.error = "the helper ended without an answer"
    failure = _failed(_drive(ending))
    assert failure.failure_class is not FailureClass.BUDGET, failure
    assert step_cause(failure) != SPEND_CAP


@pytest.mark.parametrize("scope", ["day", "run"])
def test_the_engines_own_call_a_cap_refused_is_the_same_stop_with_its_scope(scope: str):
    """The engine's own call the ceiling refused (`BudgetExceededError`) is read as the stage's is:
    one shape (`failure_taxonomy.budget_failure`), the ceiling's scope with it, so a day ceiling's
    stop is held as one wherever it was met."""
    from personalclaw.workflows.failure_taxonomy import classify_exception

    refusal = _refusal(scope)
    failure = classify_exception(refusal)
    assert failure.failure_class is FailureClass.BUDGET, failure
    assert (failure.cause_plain, failure.remediation, failure.budget_scope) == (
        refusal.headline(),
        f"{refusal.fix()}, then Retry",
        scope,
    )


def test_a_cap_record_with_no_headline_is_not_a_caps_stop():
    """A helper whose record holds a cap with no headline (no refusal matched it) failed on its
    own: its step settles as its own failure, never as a spend cap's stop with no words."""
    ending = SubagentInfo(id="sub", task="t")
    ending.error = "the helper ended without an answer"
    ending.spend_cap = {"fix": "Raise the ceiling in Settings", "scope": "day"}
    failure = _failed(_drive(ending))
    assert failure.failure_class is not FailureClass.BUDGET, failure
    assert step_cause(failure) != SPEND_CAP
