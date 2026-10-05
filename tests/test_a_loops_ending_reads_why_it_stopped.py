"""A loop's ending reads why it actually stopped: the judge's ruling, the real wait, the budget.

Measured on two first-run loops (`general-project`, a one-cycle budget, as the onboarding card
starts it):

* the judge ruled ESCALATE — it could not check what the task needed, because the file tools could
  not reach the folder — and the run page read "Stopped at its budget … the workflow needs no
  change. For more cycles, fork it": the budget was not why it stopped, and more cycles would have
  hit the same wall;
* the judge's time limit ran out while one of its calls waited for an answer, and the ending read
  "Timed out after 30 minutes while waiting for you to answer bash [turn 2/0 | last tool: bash |
  elapsed: 2100s]" beside "A new run of this workflow fails the same way until the step changes":
  the open ask had waited twelve minutes, three counters reached a person, and nothing about the
  step needed changing.

So why a run stopped is decided once (`ending_sentence.loop_stop`) and carried on the escalation
record — its cause, its sentence, its remedy — and the loop row's status, stop reason and error
come from one mapping (`loop_view.loop_ending`). Every case below drives the REAL bundled spec
through a REAL `RunController`; only the subagent manager is scripted.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from personalclaw.loop.loop import LoopStatus, LoopStopReason
from personalclaw.subagent_time_limit import time_limit_stop
from personalclaw.workflows import loop_view, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

TEMPLATE = "general-project"
RUN_TIMEOUT = 20.0
JUDGE_PROMPT = "You are verifying work you did not do"

#: Words that are the engine's or an agent's own bookkeeping, never a person's sentence.
INTERNAL = re.compile(
    r"\[turn|\bturn \d+/\d+|elapsed:|last tool:|`max_iterations`|iterations_failed"
)

#: The judge's own words when it could not decide — invented, in the shape the contract asks for.
COULD_NOT_CHECK = (
    "Whether the note covers every deadline: the worker could not list the home folder, "
    "and neither could I."
)


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Subagents:
    """`spawn` + `get`, as `dispatch_stage` calls them. The worker always answers in its declared
    shape and claims progress; the judge answers with *judge* (a payload), or ends with
    *judge_error* (a subagent's own error), applied at lookup as a real one is."""

    def __init__(self, *, judge: dict[str, Any] | None = None, judge_error: str = "") -> None:
        self.judge = judge or {}
        self.judge_error = judge_error
        self.infos: dict[str, _Info] = {}
        self.judges: set[str] = set()

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        agent_id = f"sub{len(self.infos) + 1}"
        if JUDGE_PROMPT in prompt:
            self.judges.add(agent_id)
            info = _Info(agent_id, json.dumps(self.judge))
        else:
            info = _Info(
                agent_id,
                json.dumps(
                    {
                        "summary": "drafted the note",
                        "meaningful_progress": True,
                        "evidence": "the note is in notes.md",
                    }
                ),
            )
        self.infos[agent_id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is None:
            return None
        info.done = True
        if agent_id in self.judges and self.judge_error:
            info.error, info.result = self.judge_error, ""
        return info


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


def _drive(subagents: _Subagents) -> RunController:
    """The onboarding loop's shape: the bundled template, a one-cycle budget, started as a loop."""
    spec = read_template(TEMPLATE).to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={
                "task": "Draft a short note describing what I could use an agent for this week.",
                "exit_condition": "The task is complete and the evidence shows it",
            },
            loop_kind="general",
            title="First loop",
            policy_overrides={"max_cycles": 1},
        )
    )
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(subagents=subagents))
    asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    return controller


def _judge(verdict: str, **extra: Any) -> dict[str, Any]:
    return {
        "reasoning": "read the note against the task",
        "verdict": verdict,
        "scores": {"the step accomplished something real": 2, "evidence is checkable": 1},
        "evidence_refs": ["notes.md"],
        "proof": "",
        "cannot_judge": "",
        **extra,
    }


def _row(controller: RunController) -> dict[str, Any]:
    view = loop_view.get_loop_view(controller.run.id)
    assert view is not None, "the run is not listed as a loop"
    return view


# ── 1. the judge would not decide: the ending is the judge's, not the budget's ───────────────


def test_a_judge_that_could_not_decide_ends_the_loop_in_its_own_words() -> None:
    """🔴 Before: cause-less `budget: true`, "It used its budget of 1 cycle, and the judge did not
    accept the last one", and a fork-for-more-cycles remedy — for a judge that had escalated."""
    controller = _drive(_Subagents(judge=_judge("REJECT", cannot_judge=COULD_NOT_CHECK)))
    run = controller.run

    assert run.status is RunStatus.ESCALATED, run.status
    attention = run.attention or {}
    assert attention.get("cause") == "judge", attention
    assert attention.get("reason") == "judge_escalated", attention
    detail = str(attention.get("detail") or "")
    assert "The judge could not decide whether the work is done" in detail, detail
    assert COULD_NOT_CHECK.rstrip(".") in detail, "the judge's own reason is not in the ending"
    assert "budget" not in detail.lower(), detail
    remedy = str(attention.get("remedy") or "")
    assert "Allowed working directories" in remedy, remedy
    assert "change the step" not in remedy.lower() and "more cycles" not in remedy, remedy

    # The run's own ending line says the same, with nothing internal in it.
    ending = str(run.error_message or "")
    assert ending.startswith("The judge could not decide"), ending
    assert not INTERNAL.search(ending), ending


def test_the_control_a_judge_that_rejected_still_ends_at_the_budget() -> None:
    """The same loop, the same one-cycle budget, a judge that ruled and did not accept."""
    controller = _drive(_Subagents(judge=_judge("REJECT")))
    attention = controller.run.attention or {}
    assert attention.get("cause") == "budget", attention
    assert attention.get("reason") == "max_iterations", attention
    assert (
        attention.get("detail") == "It used its budget of 1 cycle, and the judge did not accept "
        "the last one."
    ), attention
    assert "Max cycles" in str(attention.get("remedy") or ""), attention


def test_a_pass_the_contract_set_aside_is_not_read_as_a_judge_that_did_not_accept() -> None:
    """🔴 Before: "the judge did not accept the last one" over a judge that ruled PASS — the run's
    contract set the pass aside (here its reasoning read as a forbidden success mode), and the
    ending blamed work the judge had accepted."""
    controller = _drive(
        _Subagents(
            judge=_judge(
                "PASS",
                reasoning="The work claimed is done, and the note is the evidence.",
                scores={"the step accomplished something real": 2, "evidence is checkable": 2},
            )
        )
    )
    attention = controller.run.attention or {}
    assert attention.get("cause") == "budget", attention
    detail = str(attention.get("detail") or "")
    assert detail.startswith(
        "It used its budget of 1 cycle. The judge passed the last one, but its pass was set aside: "
    ), detail
    assert "work claimed without evidence" in detail, detail
    assert "did not accept" not in detail, detail


# ── 2. an ask that timed out: the real wait, and re-run-and-answer ────────────────────────


def test_an_ask_the_time_limit_ended_names_the_limit_and_the_real_wait() -> None:
    """🔴 Before: "Timed out after 30 minutes while waiting for you to answer bash [turn 2/0 |
    last tool: bash | elapsed: 2100s]", "“Work until done” escalated.", and the advice to change
    the step. The judge ran its full 30 minutes; the ask that was open had waited 702 seconds."""
    asked_at = 5000.0
    error = time_limit_stop(1800, ("bash", asked_at), asked_at + 702)
    controller = _drive(_Subagents(judge_error=error))
    run = controller.run

    assert run.status is RunStatus.ESCALATED, run.status
    judge = next(i for p, i in controller.instances.items() if p.endswith("children[1]"))
    assert judge.failure is not None
    assert judge.failure.failure_class.value == "timeout", judge.failure
    assert "answer its asks" in judge.failure.remediation, judge.failure.remediation

    attention = run.attention or {}
    assert attention.get("cause") == "approval_timeout", attention
    detail = str(attention.get("detail") or "")
    assert "time limit of 30 minutes" in detail, detail
    assert "waiting 12 minutes for your answer" in detail, detail
    assert "stopped:" in detail and "failed" not in detail, detail
    remedy = str(attention.get("remedy") or "")
    assert "answer its asks" in remedy and "Subagent timeout" in remedy, remedy
    assert "change the step" not in remedy.lower(), remedy

    ending = str(run.error_message or "")
    assert "waiting 12 minutes for your answer" in ending, ending
    assert not INTERNAL.search(ending), ending
    assert not INTERNAL.search(detail), detail


# ── 3. the loop row: status, stop reason and error from one mapping ───────────────────────


def test_the_loop_row_of_an_escalated_run_agrees_with_itself() -> None:
    """🔴 Before: status "complete", stop_reason "worker_failed", error "“Work until done”
    escalated." — finished, failed and escalated at once."""
    for subagents in (
        _Subagents(judge=_judge("REJECT", cannot_judge=COULD_NOT_CHECK)),
        _Subagents(judge_error=time_limit_stop(1800, ("bash", 0.0), 702.0)),
    ):
        controller = _drive(subagents)
        row = _row(controller)
        assert (row["status"], row["stop_reason"]) == ("failed", "worker_failed"), row
        assert row["error_message"] == controller.run.error_message, row
        assert "escalated" not in str(row["error_message"]).lower(), row


def test_a_loop_row_that_ran_out_of_budget_ended_early_and_says_which() -> None:
    controller = _drive(_Subagents(judge=_judge("REJECT")))
    row = _row(controller)
    assert (row["status"], row["stop_reason"]) == ("complete", "cycle_budget"), row
    assert (
        row["error_message"] == "It used its budget of 1 cycle, and the judge did not accept "
        "the last one."
    ), row


@pytest.mark.parametrize(
    ("status", "attention", "error", "expected"),
    [
        (RunStatus.RUNNING, None, "", ("running", "", None)),
        (RunStatus.COMPLETE, None, "", ("complete", "done", None)),
        (
            RunStatus.FAILED,
            None,
            "“work” failed: it broke.",
            ("failed", "worker_failed", "“work” failed: it broke."),
        ),
        (RunStatus.CANCELLED, None, "", ("stopped", "user", None)),
        (RunStatus.DECLINED, None, "“go” was declined.", ("stopped", "user", "“go” was declined.")),
        (
            RunStatus.ESCALATED,
            {
                "kind": "escalation",
                "reason": "max_iterations",
                "cause": "budget",
                "detail": "Used 1 cycle.",
            },
            "",
            ("complete", "cycle_budget", "Used 1 cycle."),
        ),
        (
            RunStatus.ESCALATED,
            {
                "kind": "escalation",
                "reason": "token_cap",
                "cause": "budget",
                "detail": "Used its tokens.",
            },
            "",
            ("complete", "cost_budget", "Used its tokens."),
        ),
        (
            RunStatus.ESCALATED,
            {
                "kind": "escalation",
                "reason": "judge_escalated",
                "cause": "judge",
                "detail": "The judge could not decide.",
            },
            "",
            ("failed", "worker_failed", "The judge could not decide."),
        ),
    ],
)
def test_one_mapping_gives_every_ending_its_three_fields(
    status: RunStatus, attention: Any, error: str, expected: tuple[str, str, str | None]
) -> None:
    run = WorkflowRun(id="r1", workflow_name=TEMPLATE, loop_kind="general", status=status)
    run.attention = attention
    run.error_message = error
    got = loop_view.loop_ending(run)
    assert (got[0].value, got[1], got[2]) == expected, got
    # Agreement, stated as the rule it is: an ended row names why, a live row has no stop reason,
    # and "complete" is never paired with a failure.
    ended = got[0] in (LoopStatus.COMPLETE, LoopStatus.FAILED, LoopStatus.STOPPED)
    assert bool(got[1]) is ended, got
    if got[0] is LoopStatus.COMPLETE:
        assert got[1] != LoopStopReason.WORKER_FAILED.value, got


# ── the vocabulary, on both ends ──────────────────────────────────────────────


def test_every_cause_the_engine_writes_has_a_headline_on_the_run_page() -> None:
    """`ending_sentence.CAUSES` is closed, and the run page heads each one: by its own headline
    (`CAUSE_HEADLINE`), or by its reason token for the two causes the token says more about
    (`REASON_HEADED_CAUSES`). A cause added on one side only would print the raw word, or be read
    as a step that gave up."""
    from personalclaw.workflows import ending_sentence

    meta = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "pages"
        / "workflows"
        / "attentionMeta.ts"
    ).read_text(encoding="utf-8")
    headline_block = meta.split("export const CAUSE_HEADLINE", 1)[1].split("}", 1)[0]
    headed = set(re.findall(r"^\s*([a-z_]+):", headline_block, flags=re.MULTILINE))
    by_reason = re.search(r"REASON_HEADED_CAUSES[^=]*=\s*new Set\(\[([^\]]*)\]\)", meta)
    assert headed and by_reason, "attentionMeta.ts no longer declares the cause headlines"
    reasoned = set(re.findall(r"'([a-z_]+)'", by_reason.group(1)))
    assert not headed & reasoned, headed & reasoned
    assert headed | reasoned == set(ending_sentence.CAUSES), (headed, reasoned)


def test_a_step_a_spend_cap_refused_is_not_told_to_change() -> None:
    """A spend cap refused the model call a step needed before it was made (`FailureClass.BUDGET`,
    `failure_taxonomy`): nothing about the step or the workflow needs changing, so its stop names
    the cap and where it is lifted, and the step-change remedy is kept for a step's own failure."""
    from personalclaw.workflows import ending_sentence
    from personalclaw.workflows.models import Failure, FailureClass

    refused = Failure(
        failure_class=FailureClass.BUDGET,
        cause_plain="Daily spend cap reached: $5.00 of $5.00 spent today",
        remediation="raise or remove the cap in Settings → Guardrails, then Retry",
    )
    stop = ending_sentence.step_stop(refused)
    assert stop.cause == ending_sentence.SPEND_CAP, stop
    assert "Settings › Guardrails" in stop.remedy and "needs no change" in stop.remedy, stop
    assert "change the step" not in stop.remedy.lower(), stop

    broke = Failure(failure_class=FailureClass.INTERNAL, cause_plain="it broke")
    assert ending_sentence.step_stop(broke).cause == ending_sentence.STEP
    assert "change the step" in ending_sentence.step_stop(broke).remedy
