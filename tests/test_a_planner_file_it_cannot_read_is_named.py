"""A planner file PersonalClaw cannot read is named for what is wrong with it, to the planner and
to its owner.

Measured on a Code loop's walkthrough: the planner called ``write_file`` for its step artifact three
times, and each time the JSON had an unescaped double quote inside a string. The parse failed with
no reason kept, and the retry told the planner "your previous attempt produced NO usable artifact …
only an actual write_file call creates the file we read" — a misdiagnosis, since it HAD called
write_file. So it wrote the same text again, the step went back to pending with nothing said, and
the walkthrough only ever read "this step has been quiet for a while". The check reads what was
written and says where it breaks; a pass that ran out of time or ended without writing says that.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds
from personalclaw.loop import plan_walkthrough as pw
from personalclaw.loop import store
from personalclaw.loop.loop import Loop
from personalclaw.planning import runner as R
from personalclaw.planning import session as PS
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus

#: A step artifact whose markdown quotes code with bare double quotes: not valid JSON.
_BROKEN = (
    '{\n  "markdown": "The template sets autoescape(["html"]) already, so the manual escape '
    'doubles it.",\n  "key_points": ["escape once"]\n}\n'
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    return tmp_path


def _code_loop(*, error: str = "") -> Loop:
    loop = store.create(Loop(id="", name="", kind="code", task="stop escaping titles twice"))
    step = PlanStep(id="step-0", kind="problem_framing", title="Frame it", objective="o")
    step.error = error
    loop_files.write_plan_session(PlanSession(project_id=loop.id, steps=[step]))
    return loop


def _scripted(monkeypatch, *passes: R.PlannerPass) -> list[str]:
    """The planner's passes, in order; returns the briefs each pass was given."""
    briefs: list[str] = []
    queue = list(passes)

    async def _fake_run_pass(state, svc, lp, wt, *, brief, sentinel, timeout_secs=None):
        briefs.append(brief)
        return queue.pop(0)

    monkeypatch.setattr(pw, "_run_pass", _fake_run_pass)
    return briefs


def _step(loop: Loop) -> PlanStep:
    return loop_files.read_plan_session(loop.id).steps[0]


# ── the retry says what was wrong ─────────────────────────────────────────────


def test_a_file_that_is_not_valid_json_is_named_with_where_it_breaks(monkeypatch):
    loop = _code_loop()
    wrote = R.PlannerPass(text=_BROKEN, ended=R.WROTE, limit_secs=600)
    briefs = _scripted(monkeypatch, wrote, wrote)

    assert _run(pw.run_step_pass(object(), object(), loop.id, "step-0")) is None

    assert len(briefs) == 2, "tries the pass and exactly one corrected retry"
    retry = briefs[1]
    assert "not valid JSON" in retry and "line 2, column" in retry, retry
    assert 'autoescape(["html"])' in retry, "the retry does not show where the file breaks"
    assert (
        "NO usable artifact" not in retry and "Do NOT paste" not in retry
    ), "the retry still tells a planner that wrote the file that it never wrote it"
    step = _step(loop)
    assert step.status == StepStatus.PENDING.value
    assert "not valid JSON" in step.error and "line 2, column" in step.error, step.error


def test_a_pass_that_ran_out_of_time_says_so(monkeypatch):
    loop = _code_loop()
    late = R.PlannerPass(ended=R.TIMED_OUT, limit_secs=600)
    briefs = _scripted(monkeypatch, late, late)

    _run(pw.run_step_pass(object(), object(), loop.id, "step-0"))

    assert "ran out of time" in briefs[1], briefs[1]
    assert "10 minutes" in _step(loop).error, _step(loop).error


def test_a_pass_that_ended_without_writing_still_asks_for_the_write(monkeypatch):
    loop = _code_loop()
    gone = R.PlannerPass(ended=R.STOPPED, limit_secs=600)
    briefs = _scripted(monkeypatch, gone, gone)

    _run(pw.run_step_pass(object(), object(), loop.id, "step-0"))

    assert "write_file" in briefs[1] and "without writing" in briefs[1], briefs[1]
    assert "without writing" in _step(loop).error


def test_a_retried_step_tells_the_planner_what_went_wrong_last_time(monkeypatch):
    loop = _code_loop(error="The planner wrote step_artifact.json, but it is not valid JSON.")
    good = R.PlannerPass(text='{"markdown": "Framed."}', ended=R.WROTE, limit_secs=600)
    briefs = _scripted(monkeypatch, good)

    step = _run(pw.run_step_pass(object(), object(), loop.id, "step-0"))

    assert "it is not valid JSON" in briefs[0], briefs[0]
    assert step is not None and step.status == StepStatus.AWAITING_REVIEW.value
    assert step.error == "", "a step with a draft still carries its old failure"


def test_a_design_pass_names_what_is_wrong_with_its_step_list(monkeypatch):
    loop = store.create(Loop(id="", name="", kind="code", task="stop escaping titles twice"))
    _scripted(monkeypatch, R.PlannerPass(text='{"summary": "s"}', ended=R.WROTE))

    assert _run(pw.run_design_pass(object(), object(), loop.id)) is None

    error = loop_files.read_plan_session(loop.id).design_error
    assert "plan_steps.json" in error and "steps" in error, error


# ── the step carries its failure to the page ──────────────────────────────────


def test_a_step_failure_round_trips_and_a_new_pass_clears_it():
    s = PlanSession(project_id="p-1", steps=[PlanStep(id="step-0", kind="k", title="t")])
    s.steps[0].status = StepStatus.RUNNING.value
    assert PS.fail_step(s, "step-0", "The planner ended its turn without writing it.") is True

    again = PlanSession.from_dict(s.to_dict())
    assert again.steps[0].status == StepStatus.PENDING.value
    assert again.steps[0].error == "The planner ended its turn without writing it."
    assert PS.wire(again)["steps"][0]["error"] == again.steps[0].error

    assert PS.mark_running(again, "step-0") is True
    assert again.steps[0].error == ""


# ── the runner says how a pass ended ──────────────────────────────────────────


class _Svc:
    def __init__(self, loop):
        self._loop = loop

    async def add(self, **kw):
        return None

    def get_by_session(self, _key):
        return self._loop

    async def remove(self, _id):
        return None


class _State:
    def get_or_create_session(self, **kw):
        return SimpleNamespace(
            _trust=False,
            acp_provider=None,
            acp_provider_agent=None,
            reasoning_effort="",
            acp_mode="",
            _extra_tool_roots=[],
        )

    def push_sessions_update(self):
        pass


def _pass(tmp_path, loop, *, timeout_secs=None):
    return _run(
        R.run_planner_pass(
            _State(),
            _Svc(loop),
            session_key="loop-plan-0a1b2c3d",
            agent_name="planner",
            workspace_dir="",
            files_dir=str(tmp_path),
            sentinel="step_artifact.json",
            brief="b",
            app="loops",
            timeout_secs=timeout_secs,
        )
    )


def test_the_runner_says_a_pass_ran_out_of_time(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    started = time.time()
    out = _pass(tmp_path, SimpleNamespace(id="L1", active=True), timeout_secs=0.1)
    assert time.time() - started < 5
    assert (out.ended, out.text, out.limit_secs) == (R.TIMED_OUT, "", 0.1)


def test_the_runner_says_a_pass_ended_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    out = _pass(tmp_path, SimpleNamespace(id="L1", active=False))
    assert (out.ended, out.text) == (R.STOPPED, "")
