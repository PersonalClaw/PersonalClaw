"""A loop cycle you ended with a Deny is not run again into the same ask: it says so, and waits.

A General loop is a workflow run (`general-project`): each cycle's `work` stage runs as a subagent,
and its owner can Deny a call it asks for. Before, the subagent counted her Deny as "it was
declined" and told nobody: the stage settled done with no trace, the judge ruled on work she had
declined, and the next cycle ran the stage again with the judge's critique, which asked her for the
same write. Now her Deny travels with the stage (`SubagentInfo.declined_calls`), the cycle ends at
it, saying so, and the loop waits for her: the run pauses with that sentence and one Inbox item,
what she steers it with reaches the next cycle, and her Resume runs that cycle. A cycle she declined
nothing in still goes on with the judge's critique, as before.

Every case drives the REAL bundled spec (or a small spec of its own) through a REAL
`RunController`; only the subagent manager is scripted, as a stage's subagent ends.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.approval_answer import YOU
from personalclaw.declined_calls import declined_step
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import run_cockpit, service, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

TEMPLATE = "general-project"
JUDGE_PROMPT = "You are verifying work you did not do"
TIMEOUT = 20.0

#: Her Deny of the worker's write of the note, as the subagent keeps it.
WROTE_NOTE = declined_step("write_file", {"path": "notes/plan.md", "content": "# Plan\nWeek one"})
SAID = "Cycle 1 ended at “work”: you declined write_file (notes/plan.md)."
WAITS = f"{SAID} The loop waits for you: tell it what to do instead, or resume or stop it."
STEER = "Write it to drafts/plan.md instead."


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


@pytest.fixture
def inbox(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """What the run raised in the Inbox and what it closed, by the refs it named."""
    seen: dict[str, list[dict[str, Any]]] = {"raised": [], "resolved": []}

    def _emit(_state: Any, **kw: Any) -> str:
        seen["raised"].append(kw)
        return f"item-{len(seen['raised'])}"

    def _resolve(_state: Any, refs: dict[str, str], **_kw: Any) -> int:
        seen["resolved"].append(dict(refs))
        return 1

    monkeypatch.setattr("personalclaw.inbox.emit_attention_item", _emit)
    monkeypatch.setattr("personalclaw.inbox.resolve_attention_items", _resolve)
    return seen


class _Info:
    def __init__(self, agent_id: str, result: str, declined: list[dict[str, Any]]) -> None:
        self.id, self.result = agent_id, result
        self.done, self.error, self.reaped, self.agent, self.cancelled = False, "", False, "", False
        self.declined_calls = declined


class _Subagents:
    """`spawn` + `get`, as `dispatch_stage` calls them. The worker of cycle *n* ends with
    ``denies[n]`` declined (the last entry repeats); the judge rules ``verdicts[n]`` likewise."""

    def __init__(
        self, *, denies: list[list[dict[str, Any]]], verdicts: tuple[str, ...] = ("PASS",)
    ) -> None:
        self.denies, self.verdicts = denies, verdicts
        self.infos: dict[str, _Info] = {}
        self.work: list[str] = []
        self.judged: list[str] = []

    def spawn(self, **kw: Any) -> _Info:
        task = str(kw.get("task") or "")
        if JUDGE_PROMPT in task:
            verdict = self.verdicts[min(len(self.judged), len(self.verdicts) - 1)]
            self.judged.append(task)
            payload: dict[str, Any] = {
                "reasoning": "the plan is not in notes/plan.md yet",
                "verdict": verdict,
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["notes/plan.md"],
                "proof": "read notes/plan.md",
                "cannot_judge": "",
            }
            declined: list[dict[str, Any]] = []
        else:
            declined = self.denies[min(len(self.work), len(self.denies) - 1)]
            self.work.append(task)
            payload = {
                "summary": "drafted the plan" if not declined else "you declined my write",
                "meaningful_progress": True,
                "evidence": "notes/plan.md",
            }
        info = _Info(f"sub{len(self.infos) + 1}", json.dumps(payload), declined)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not info.cancelled:
            info.done = True
        return info

    async def cancel(self, agent_id: str, *, reason: str = "Cancelled by user") -> bool:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return False
        info.cancelled = info.done = True
        info.error = reason
        return True


class _State:
    """The attention state the controller is handed (its rows are captured by `inbox`)."""

    def notify(self, *_a: Any, **_kw: Any) -> None:
        return None


class _Supervisor:
    def __init__(self, controller: RunController) -> None:
        self._controller = controller

    def controller(self, run_id: str) -> RunController | None:
        return self._controller if run_id == self._controller.run.id else None


def _loop(spec: dict[str, Any] | None = None, **policy: Any) -> tuple[WorkflowRun, dict[str, Any]]:
    spec = spec or read_template(TEMPLATE).to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=spec.get("name") or TEMPLATE,
            inputs={
                "task": "Draft my week's plan in notes/plan.md.",
                "exit_condition": "it exists",
            },
            loop_kind="general",
            title="Week plan",
            policy_overrides={"max_cycles": 4, **policy},
        )
    )
    store.write_spec(run.id, spec)
    return run, spec


def _controller(run: WorkflowRun, spec: dict[str, Any], subagents: _Subagents) -> RunController:
    return RunController(
        run, spec, services=EngineServices(subagents=subagents, attention_state=_State())
    )


async def _until(predicate: Any, *, what: str, timeout: float = TIMEOUT) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.05)


def _declined_rows(run_id: str) -> list[dict[str, Any]]:
    return [
        r
        for r in journal_mod.ledger(run_id, kinds={journal_mod.ITERATION})
        if r.get("outcome") == "declined"
    ]


# ── her Deny ends the cycle there, and the loop waits for her ───────────────────────────────


def test_a_cycle_you_declined_is_not_run_again_into_the_same_ask(inbox: dict) -> None:
    """🔴 Before: the judge ruled on the declined work and cycle 2 asked for the same write."""
    subagents = _Subagents(denies=[[WROTE_NOTE], []])
    run, spec = _loop()
    controller = _controller(run, spec, subagents)

    async def _go() -> None:
        await controller.start()
        await _until(lambda: controller.run.status is RunStatus.PAUSED, what="the run to wait")
        # A wait, not a run that goes on: nothing more is spawned while it waits.
        await asyncio.sleep(0.6)

    asyncio.run(_go())

    # Positive control: the cycle's work did run, once.
    assert len(subagents.work) == 1, subagents.work
    # The judge never ruled on work she declined, and no second cycle put the write to her again.
    assert subagents.judged == []
    assert controller.run.status is RunStatus.PAUSED
    assert controller.run.error_message == WAITS
    assert store.pause_requested(run.id), "the wait must outlive a restart: a sticky pause"

    work, judge = (
        controller.instances["root.body@0.children[0]"],
        controller.instances["root.body@0.children[1]"],
    )
    assert work.declined == [WROTE_NOTE]
    assert judge.state is InstanceState.SKIPPED
    assert judge.degraded_reason == "not run: you declined write_file (notes/plan.md) in “work”"
    assert "root.body@1.children[0]" not in controller.instances, "cycle 2 started"

    # The ledger keeps the cycle as hers: neither a continue nor a failure.
    [row] = _declined_rows(run.id)
    assert (row["iteration"], row["detail"], row["declined"]) == (
        0,
        SAID,
        ["write_file (notes/plan.md)"],
    )

    # One Inbox item says so, deep-linked to the loop.
    [raised] = inbox["raised"]
    assert raised["title"] == "Loop waiting — you declined one of its steps"
    assert raised["body"] == f"Week plan — {SAID}"
    assert raised["refs"] == {
        "workflow": run.id,
        "declined_wait": "1",
        "loop": run.id,
        "loop_kind": "general",
    }


def test_the_run_page_says_what_you_declined(inbox: dict) -> None:
    subagents = _Subagents(denies=[[WROTE_NOTE]])
    run, spec = _loop()
    asyncio.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))

    status = service.status(run.id)
    assert status["status"] == "paused"
    assert status["error"] == WAITS
    # Said as her wait, not as a fault, and listed under "Declined by you".
    assert status["declined_wait"] is True
    assert status["declined"] == [SAID]
    row = next(n for n in status["nodes"] if n["instance_path"] == "root.body@0.children[0]")
    assert row["declined"] == "You declined write_file (notes/plan.md)."
    # What happens next if she says nothing: nothing, until she resumes it.
    nxt = run_cockpit._next_if_silent(store.get(run.id), status["nodes"], [])
    assert nxt == {"action": "waits", "detail": WAITS, "queued": []}
    # Introspect's timeline names the cycle that ended at her Deny.
    events = journal_mod.ledger(run.id)
    told = [e for e in run_cockpit.introspection_timeline(events) if e["kind"] == "iteration"]
    assert [e["detail"] for e in told] == [SAID]


# ── what she tells it reaches the next cycle, and her Resume runs it ───────────────────────────


def test_resume_runs_the_next_cycle_with_what_you_told_it(inbox: dict) -> None:
    subagents = _Subagents(denies=[[WROTE_NOTE], []])
    run, spec = _loop()
    controller = _controller(run, spec, subagents)
    supervisor = _Supervisor(controller)

    async def _go() -> RunStatus:
        await controller.start()
        await _until(lambda: controller.run.status is RunStatus.PAUSED, what="the run to wait")
        assert service.steer_run(run.id, STEER)["ok"] is True
        assert service.resume_run(run.id, by=YOU, supervisor=supervisor)["resumed"] is True
        return await asyncio.wait_for(controller.run_to_completion(), timeout=TIMEOUT)

    status = asyncio.run(_go())

    # Her Resume ran cycle 2, and what she told it while it waited is in its prompt.
    assert len(subagents.work) >= 2, subagents.work
    assert STEER in subagents.work[1], subagents.work[1][:400]
    assert STEER not in subagents.work[0]
    assert "root.body@1.children[0]" in controller.instances
    # The loop went on to its end: the judge passed cycle 2's work.
    assert status in (RunStatus.COMPLETE, RunStatus.ESCALATED), status
    assert controller.run.error_message != WAITS
    assert "deadlocked" not in (controller.run.error_message or "")
    # The wait's Inbox item closed when she resumed it.
    assert {"workflow": run.id, "declined_wait": "1"} in inbox["resolved"]
    # The cycle she declined is still listed after the wait is over.
    assert service.status(run.id)["declined"] == [SAID]
    assert service.status(run.id)["declined_wait"] is False


def test_a_restart_while_it_waits_resumes_into_the_next_cycle(inbox: dict) -> None:
    """The wait outlives the process: a fresh controller resumes into cycle 2, not into cycle 1's
    finished body (which reads as a deadlock), and what she told it while it waited is not lost."""
    run, spec = _loop()

    async def _go() -> tuple[_Subagents, RunController]:
        first = _controller(run, spec, _Subagents(denies=[[WROTE_NOTE]]))
        await first.run_to_completion(timeout=TIMEOUT)
        assert first.run.status is RunStatus.PAUSED
        assert service.steer_run(run.id, STEER)["ok"] is True
        store.clear_pause(run.id)  # her Resume, with no live controller
        second_mgr = _Subagents(denies=[[]])
        second = _controller(store.get(run.id), store.read_spec(run.id), second_mgr)
        await second.start()
        await _until(lambda: bool(second_mgr.work), what="cycle 2's work to start")
        return second_mgr, second

    second_mgr, second = asyncio.run(_go())
    assert "root.body@1.children[0]" in second.instances
    assert second._iterations.get("root", 0) >= 1
    assert second.run.error_message != WAITS
    assert STEER in second_mgr.work[0], second_mgr.work[0][:400]


# ── a loop with no cycle left after it does not wait ───────────────────────────────────────────


def test_a_loop_at_its_budget_stops_saying_its_last_cycle_ended_at_your_deny(inbox: dict) -> None:
    subagents = _Subagents(denies=[[WROTE_NOTE]])
    run, spec = _loop(max_cycles=1)
    status = asyncio.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))
    assert status is RunStatus.ESCALATED, status
    finished = store.get(run.id)
    attention = finished.attention or {}
    assert attention.get("cause") == "budget", attention
    assert attention.get("detail") == f"It used its budget of 1 cycle. {SAID}", attention
    assert not store.pause_requested(run.id)
    assert service.status(run.id)["declined"] == [SAID]


def test_a_counted_loop_that_ran_its_count_is_done(inbox: dict) -> None:
    spec = {
        "name": "one-pass",
        "root": {
            "kind": "loop",
            "id": "pass",
            "config": {"mode": "counted", "n": 1},
            "body": {"kind": "stage", "id": "work", "config": {"prompt": "Draft the plan."}},
        },
    }
    run, spec = _loop(spec)
    subagents = _Subagents(denies=[[WROTE_NOTE]])
    status = asyncio.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))
    assert status is RunStatus.COMPLETE, (status, store.get(run.id).error_message)
    assert service.status(run.id)["declined"] == [SAID]
    assert inbox["raised"] == [], "a loop with no cycle left does not wait"


# ── the controls: what she did not decline goes on as before ───────────────────────────────────


def test_a_cycle_you_declined_nothing_in_goes_on_with_the_judges_critique(inbox: dict) -> None:
    subagents = _Subagents(denies=[[]], verdicts=("REJECT", "PASS"))
    run, spec = _loop()
    status = asyncio.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))
    # Cycle 2 ran with the judge's ruling on cycle 1, and nobody was asked to wait.
    assert len(subagents.work) >= 2, subagents.work
    assert "REJECT" in subagents.work[1]
    assert status is not RunStatus.PAUSED
    assert _declined_rows(run.id) == []
    assert inbox["raised"] == []
    assert service.status(run.id)["declined"] == []


def test_a_step_outside_a_loop_goes_on_and_says_what_you_declined(inbox: dict) -> None:
    spec = {
        "name": "draft-and-tidy",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "stage", "id": "draft", "config": {"prompt": "Draft the plan."}},
                {"kind": "stage", "id": "tidy", "config": {"prompt": "Tidy the folder."}},
            ],
        },
    }
    run, spec = _loop(spec)
    subagents = _Subagents(denies=[[WROTE_NOTE], []])
    status = asyncio.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))
    # Nothing would run "draft" again, so nothing waits: "tidy" runs, and the run completes.
    assert status is RunStatus.COMPLETE, (status, store.get(run.id).error_message)
    assert len(subagents.work) == 2
    assert service.status(run.id)["declined"] == [
        "You declined write_file (notes/plan.md) in “draft”."
    ]
    assert inbox["raised"] == []


# ── what you tell a running loop reaches it ───────────────────────────────────────────────────


def test_a_steer_queued_while_a_cycle_works_reaches_the_next_cycle() -> None:
    """🔴 Before: the steer was written onto the run's stored row, and the live controller's next
    save wrote its own copy over it, so it never reached a prompt."""
    prompts: list[str] = []
    holder: dict[str, WorkflowRun] = {}

    async def recorder(prompt: str, *, use_case: str = "background", output_type: Any = None):
        prompts.append(prompt)
        if len(prompts) == 1:
            assert service.steer_run(holder["run"].id, "focus on the login flow")["ok"] is True
        return f"out{len(prompts)}"

    spec = {
        "name": "steerable",
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "counted", "n": 3, "session": "fresh"},
            "body": {"kind": "infer", "id": "b", "config": {"prompt": "work {{iter}}"}},
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="steerable"))
    store.write_spec(run.id, spec)
    holder["run"] = run
    controller = RunController(run, spec, services=EngineServices(completion=recorder))
    assert asyncio.run(controller.run_to_completion(timeout=TIMEOUT)) is RunStatus.COMPLETE
    assert len(prompts) == 3, prompts
    assert "focus on the login flow" not in prompts[0]
    assert "focus on the login flow" in prompts[1]
    assert store.pending_steering(run.id) == [], "an instruction is taken once"
