"""A cycle she ended with a Deny is not asked again: it ends saying so, and its loop waits for her.

Measured on an Attended Goal loop told to change nothing: its owner denied the worker's two
``write_file`` calls (the cycle's finding and the findings log), and the cycle driver then told the
worker "You ended the turn without writing this cycle's deliverable. Do it NOW, in THIS turn …
write the files", twice. The worker would not go around her decision on its own; the product's
re-prompt argued against it, and its next cycle would have asked her for the same write again.

Driven here through the gateway's real cycle driver on the real chat runner, with a scripted worker
whose approvals are answered as she answered them:

* a turn in which she declined the finding's write is not re-prompted; the cycle ends saying
  "Cycle 1 ended without its finding: you declined write_file (findings/cycle_001.json).", named
  as a workflow cycle's Deny is (``declined_calls``), on the loop's ledger, as the question the
  loop waits on, in its one Inbox item and on its page; every worker's nudge loop is switched off,
  kept for her Resume;
* a turn in which she declined another step is not re-prompted either, and names that step;
* a turn that wrote no finding and met no Deny is still asked again, as before;
* the question a loop waits on is answered by her Resume, so the loop does not stop on it again;
* a loop planner's pass she ended with a Deny is not sent again, retried, or re-run by a restart.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_dashboard_approval import _context_builder, _make_state

from personalclaw.dashboard.state import _ChatSession
from personalclaw.declined_calls import waits_for_you
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    LLMEvent,
)
from personalclaw.loop import files as loop_files
from personalclaw.loop import journal as loop_journal
from personalclaw.loop import kinds, manager
from personalclaw.loop import plan_walkthrough as pw
from personalclaw.loop import posture, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.loop.manager import session_key
from personalclaw.planning import runner as R
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.triggers.nudge._INSTANCE", None)
    kinds.ensure_loaded()
    return tmp_path


def _attended_goal_loop() -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="Count the migrations",
            kind="goal",
            task="List the files in migrations/ and count them. Read only: change nothing.",
            attended=True,
        )
    )
    return store.update_status(loop.id, LoopStatus.RUNNING)


# ── the scripted worker ──────────────────────────────────────────────────────────────────────


def _scripted_runtime(tmp_path: Path):
    """A dashboard state whose sessions are served by one scripted runtime, which answers its
    synchronous questions as a runtime does (it measures no context)."""
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=None)
    state.sessions.record_success = MagicMock()
    return state, client


def _ask(request_id: str, tool: str, tool_input: dict) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=tool,
        tool_kind="edit" if tool == "write_file" else "execute",
        request_id=request_id,
        tool_call_id=f"call-{request_id}",
        tool_input=tool_input,
    )


def _says(text: str) -> list[LLMEvent]:
    return [LLMEvent(kind=EVENT_TEXT_CHUNK, text=text), LLMEvent(kind=EVENT_COMPLETE)]


def _writes_its_finding(loop_id: str) -> LLMEvent:
    finding = f"{loop_files.loop_dir(loop_id)}/findings/cycle_001.json"
    return _ask("901", "write_file", {"path": finding, "content": '{"cycle": 1, "summary": "2"}'})


class _Row:
    def __init__(self, row_id: str, key: str) -> None:
        self.id, self.session_name, self.active, self.cycle_count = row_id, key, True, 0


class _Nudges:
    """`AutoNudgeService`'s public surface, so the gateway hands it its REAL `_fire` callback."""

    last: "_Nudges | None" = None

    def __init__(self, *, base_dir: Any, on_fire: Any) -> None:
        self.on_fire = on_fire
        self.rows: dict[str, _Row] = {}
        _Nudges.last = self

    async def start(self) -> None:
        return None

    def subscribe(self, _observer: Any) -> None:
        return None

    def get_by_session(self, name: str) -> _Row | None:
        return next((r for r in self.rows.values() if r.session_name == name), None)

    def list_all(self) -> list[_Row]:
        return list(self.rows.values())

    async def update(self, row_id: str, *, active: bool | None = None, **_: Any) -> _Row | None:
        row = self.rows.get(row_id)
        if row is not None and active is not None:
            row.active = active
        return row

    async def remove(self, row_id: str) -> None:
        self.rows.pop(row_id, None)

    def notify_turn_complete(self, *_a: Any, **_kw: Any) -> None:
        return None


class _Cycle:
    """One cycle of a loop's stage worker, fired through the gateway's real cycle driver onto
    the real chat runner. Each turn the worker takes plays the next script; each approval it asks
    for is answered as *answers* says (her Deny is ``rejected``)."""

    def __init__(self, tmp_path: Path, loop: Loop, scripts: list[list[Any]], answers: dict):
        self.loop, self.key = loop, session_key(loop.id)
        self.state, self.client = _scripted_runtime(tmp_path)
        self.hub = MagicMock()
        self.state.loop_sse = lambda: self.hub  # type: ignore[method-assign]
        self.session = _ChatSession(self.key)
        self.session._app = "loop"
        posture.arm(self.session, posture.of(loop))  # as the loop arms its worker: Attended
        self.state._sessions[self.key] = self.session
        self.prompts: list[str] = []
        self.answers = dict(answers)
        queue = deque(scripts)

        async def _play(events: list[Any]):
            for event in events:
                if callable(event):  # what the worker does at that point of its turn
                    event()
                    continue
                yield event

        def _stream(message: str, *_a: Any, **_kw: Any):
            self.prompts.append(message)
            return _play(queue.popleft() if queue else _says("Nothing more to do this cycle."))

        self.client.stream = MagicMock(side_effect=_stream)
        self.items: list[dict] = []

    async def _answer(self) -> None:
        """Answer each approval as soon as the chat runner waits on it."""
        for _ in range(4000):
            for request_id, fut in list(self.session._approval_futures.items()):
                if not fut.done() and request_id in self.answers:
                    fut.set_result(self.answers.pop(request_id))
            if not self.answers:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"never asked: {sorted(self.answers)}")

    def run(self) -> "_Cycle":
        from personalclaw.config.loader import AppConfig
        from personalclaw.gateway import GatewayOrchestrator

        cfg = AppConfig()
        with patch.object(cfg, "load_credentials", return_value={}):
            orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
        orch.dashboard_state = self.state
        nudge = MagicMock(id="N1", session_name=self.key, message="Run the next cycle.")
        nudge.stop_sentinel_path, nudge.cycle_count = "", 0

        def _item(_state: Any, **kw: Any) -> str:
            self.items.append(kw)
            return "i-1"

        async def _go() -> None:
            with (
                patch("personalclaw.gateway.autonudge_enabled", return_value=True),
                patch("personalclaw.gateway.AutoNudgeService", _Nudges),
                patch("personalclaw.inbox.emit_attention_item", _item),
            ):
                await orch._init_autonudge()
                if orch.loop_watchdog is not None:
                    await orch.loop_watchdog.stop()
                self.svc = _Nudges.last
                assert self.svc is not None
                self.svc.rows["N1"] = _Row("N1", self.key)
                orch.loop_watchdog = W.LoopWatchdog(self.state, self.svc)
                answering = asyncio.create_task(self._answer()) if self.answers else None
                with patch("personalclaw.triggers.nudge._INSTANCE", self.svc):
                    assert await self.svc.on_fire(nudge) is True
                    await asyncio.wait_for(self.session.task, timeout=60)
                    await asyncio.sleep(0)
                if answering is not None:
                    await asyncio.wait_for(answering, timeout=5)

        asyncio.run(_go())
        return self

    def published(self, event: str) -> list[dict]:
        return [c.args[2] for c in self.hub.publish.call_args_list if c.args[1] == event]

    def reprompts(self) -> list[str]:
        """What the worker was told after its turn: the cycle's nudges past its first."""
        nudges = [m["content"] for m in self.session.messages if m.get("role") == "nudge"]
        return [n for n in nudges if "ended the turn without writing" in n]


# ── her Deny of the finding's write ──────────────────────────────────────────────────────────


def test_a_finding_she_declined_is_not_asked_for_again(tmp_path):
    loop = _attended_goal_loop()
    cycle = _Cycle(
        tmp_path,
        loop,
        [[_writes_its_finding(loop.id), *_says("You declined that write, so I wrote nothing.")]],
        {"901": "rejected"},
    ).run()

    assert len(cycle.prompts) == 1, f"the worker was asked again: {cycle.prompts[1:]}"
    assert cycle.reprompts() == []
    assert cycle.published("reprompt") == []
    assert cycle.client.reject_tool.await_count == 1
    assert loop_files.get_findings(loop.id) == [], "the write she declined happened anyway"


def test_the_cycle_ends_saying_she_declined_it(tmp_path):
    loop = _attended_goal_loop()
    cycle = _Cycle(
        tmp_path,
        loop,
        [[_writes_its_finding(loop.id), *_says("You declined that write, so I wrote nothing.")]],
        {"901": "rejected"},
    ).run()

    said = "Cycle 1 ended without its finding: you declined write_file (findings/cycle_001.json)."
    # The run's record: a cycle her decision ended, which is neither a completed cycle nor a fault.
    (row,) = loop_journal.ledger(loop.id, kinds={loop_journal.STEP_SKIPPED})
    assert (row["actor"], row["cycle"], row["reason"]) == ("user", 1, said)
    assert row["declined"] == ["write_file (findings/cycle_001.json)"]
    assert loop_files.cycles_completed(loop.id) == 0
    assert loop_journal.ledger(loop.id, kinds={loop_journal.STEP_FAILED}) == []
    # The loop waits for her, saying so, and its page lists the cycle once the wait is over too.
    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    question = loop_files.pending_question(loop.id)
    assert question["question"] == f"{said} {waits_for_you()}"
    assert question["declined"] is True and question["why"] == manager.DECLINED_WHY
    view = store.get_redacted(loop.id)
    assert [(d["cycle"], d["reason"]) for d in view["declined"]] == [(1, said)]
    assert cycle.published("declined") == [{"loop_id": loop.id, "task_id": "", "reason": said}]
    (item,) = [i for i in cycle.items if i.get("source") == "loop"]  # beside its approval's own
    assert item["title"] == "Loop waiting — you declined one of its steps"
    assert said in item["body"] and item["refs"]["loop"] == loop.id


def test_its_workers_stop_cycling_until_she_answers(tmp_path):
    loop = _attended_goal_loop()
    cycle = _Cycle(
        tmp_path,
        loop,
        [[_writes_its_finding(loop.id), *_says("You declined that write.")]],
        {"901": "rejected"},
    ).run()

    row = cycle.svc.rows.get("N1")
    assert row is not None, "the worker's nudge loop is kept, for her Resume"
    assert row.active is False, "its next cycle would ask her for the same write again"


def test_a_deny_of_another_step_ends_the_cycle_as_hers_too(tmp_path):
    loop = _attended_goal_loop()
    count = _ask("902", "bash", {"command": "wc -l migrations/*.sql"})
    cycle = _Cycle(
        tmp_path,
        loop,
        [[count, *_says("You declined the count, so I stopped here.")]],
        {"902": "rejected"},
    ).run()

    assert cycle.reprompts() == [] and len(cycle.prompts) == 1
    said = "Cycle 1 ended without its finding: you declined bash (wc -l migrations/*.sql)."
    assert loop_files.pending_question(loop.id)["question"] == f"{said} {waits_for_you()}"
    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value


def test_a_cycle_that_wrote_its_finding_after_her_deny_goes_on(tmp_path):
    """She declined the findings log but allowed the finding: the cycle did its work."""
    loop = _attended_goal_loop()
    d = loop_files.loop_dir(loop.id)
    log = _ask("903", "write_file", {"path": f"{d}/FINDINGS.md", "content": "# Findings"})
    (d / "findings").mkdir(parents=True, exist_ok=True)

    def _writes_the_finding() -> None:
        (d / "findings" / "cycle_001.json").write_text('{"cycle": 1, "summary": "two files"}')

    script = [log, _writes_the_finding, *_says("Wrote the finding; you declined the log.")]
    cycle = _Cycle(tmp_path, loop, [script], {"903": "rejected"}).run()

    assert len(cycle.prompts) == 1
    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    assert loop_journal.ledger(loop.id, kinds={loop_journal.STEP_SKIPPED}) == []
    assert cycle.svc.rows["N1"].active is True


# ── no Deny: the re-prompt stands ────────────────────────────────────────────────────────────


def test_an_undenied_missing_finding_is_still_asked_for(tmp_path):
    loop = _attended_goal_loop()
    cycle = _Cycle(tmp_path, loop, [_says("Here is my plan for this cycle.")], {}).run()

    assert len(cycle.reprompts()) == 3, "one turn, then three asks for the finding it owes"
    asks = cycle.published("reprompt")
    assert [(a["attempt"], a["left"]) for a in asks] == [(1, 2), (2, 1), (3, 0)]
    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    assert loop_files.pending_question(loop.id) is None
    assert cycle.published("declined") == []


# ── what the chat runner keeps of her Deny ───────────────────────────────────────────────────


def _one_turn(tmp_path: Path, events: list[LLMEvent], answer: str = "", window: float = 0.0):
    """One chat turn on the real chat runner, its approval answered *answer* (none: it waits out
    a *window* of seconds); returns the session."""
    from test_dashboard_approval import _set_stream

    from personalclaw.dashboard.chat import run_chat

    state, client = _scripted_runtime(tmp_path)
    if window:
        state.approval_window_secs = lambda: window  # type: ignore[method-assign]
    session = _ChatSession("chat-1-declines")
    _set_stream(client, events)

    async def _go() -> None:
        async def _answer() -> None:
            for _ in range(4000):
                for fut in list(session._approval_futures.values()):
                    if not fut.done():
                        fut.set_result(answer)
                        return
                await asyncio.sleep(0.005)

        answering = asyncio.create_task(_answer()) if answer else None
        await run_chat(state, session, "write it")
        if answering is not None:
            await answering

    asyncio.run(_go())
    return session


def test_the_chat_runner_keeps_her_deny_and_what_it_refused_with_it(tmp_path):
    """Her Deny, and the request sent with it that her Deny refused too, are kept for the turn."""
    first = _ask("905", "write_file", {"path": "/home/user/a.json", "content": "{}"})
    second = _ask("906", "write_file", {"path": "/home/user/b.md", "content": "# B"})
    session = _one_turn(tmp_path, [first, second, *_says("ok")], answer="rejected")
    assert session._last_turn_declined == [
        {"tool": "write_file", "names": ["/home/user/a.json", "{}"]},
        {"tool": "write_file", "names": ["/home/user/b.md", "# B"]},
    ]


@pytest.mark.parametrize("ended", ["expired", "cancelled"])
def test_an_approval_nobody_answered_is_not_her_deny(tmp_path, ended):
    """Only her Deny is kept: an approval that ran out of time, or a turn being stopped, is told to
    the agent as what it was, and a loop's re-prompt sees no Deny of hers in it."""
    call = _ask("904", "write_file", {"path": "/home/user/x.json", "content": "{}"})
    if ended == "expired":
        session = _one_turn(tmp_path, [call, *_says("ok")], window=0.05)
    else:
        session = _one_turn(tmp_path, [call, *_says("ok")], answer="cancelled")
    assert session._last_turn_declined == []


def test_a_new_turn_starts_with_nothing_declined(tmp_path):
    session = _one_turn(tmp_path, _says("hello"))
    session._last_turn_declined = [{"tool": "write_file", "names": []}]
    from test_dashboard_approval import _set_stream

    from personalclaw.dashboard.chat import run_chat

    state, client = _scripted_runtime(tmp_path)
    _set_stream(client, _says("again"))
    asyncio.run(run_chat(state, session, "and again"))
    assert session._last_turn_declined == []


# ── her Resume answers what the loop waited on ───────────────────────────────────────────────


class _NoNudges:
    def list_all(self) -> list:
        return []

    def get_by_session(self, _name: str) -> None:
        return None


class _Worker:
    def __init__(self, key: str) -> None:
        self.key, self.running, self.messages = key, False, []
        self._suppress_autonudge_rearm = False


def _poll(loop: Loop) -> None:
    state = MagicMock()
    state._sessions = {session_key(loop.id): _Worker(session_key(loop.id))}
    state.waiting_on_owner.return_value = False
    asyncio.run(W.LoopWatchdog(state, _NoNudges())._poll_once())


def test_a_resumed_loop_does_not_stop_again_on_the_question_it_waited_on():
    loop = _attended_goal_loop()
    said = "Cycle 1 ended without its finding: you declined write_file (findings/cycle_001.json)."
    loop_files.write_question(loop.id, f"{said} {waits_for_you()}")
    store.update_status(loop.id, LoopStatus.NEEDS_INPUT)

    store.update_status(loop.id, LoopStatus.RUNNING)  # her Resume
    _poll(loop)

    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    assert loop_files.pending_question(loop.id) is None


def test_a_question_its_worker_asks_while_it_runs_still_pauses_it():
    loop = _attended_goal_loop()
    folder = loop_files.loop_dir(loop.id)
    assert folder is not None
    # A worker writes its own question, with no time of its own.
    (folder / "questions.json").write_text('{"question": "Which feed should I count?"}')

    _poll(loop)

    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value


# ── a loop planner's pass she ended with a Deny ──────────────────────────────────────────────


ARTIFACT = pw.ARTIFACT_SENTINEL


def _declined(*names: str) -> tuple[dict, ...]:
    return ({"tool": "write_file", "names": list(names)},)


def _planning_loop() -> Loop:
    loop = store.create(Loop(id="", name="Fix the feed titles", kind="code", task="fix it"))
    store.update_status(loop.id, LoopStatus.PLANNING)
    step = PlanStep(id="step-0", kind="problem_framing", title="Frame it", objective="o")
    loop_files.write_plan_session(PlanSession(project_id=loop.id, steps=[step]))
    return loop


def _scripted(monkeypatch, *passes: R.PlannerPass) -> list[str]:
    briefs: list[str] = []
    queue = list(passes)

    async def _fake_run_pass(state, svc, lp, wt, *, brief, sentinel, timeout_secs=None):
        briefs.append(brief)
        return queue.pop(0)

    monkeypatch.setattr(pw, "_run_pass", _fake_run_pass)
    return briefs


def test_a_planner_pass_she_declined_is_not_retried_or_rerun(monkeypatch):
    loop = _planning_loop()
    # As the pass returns it: what it names in the loop's folder, by its place there.
    declined = R.PlannerPass(ended=R.DECLINED, limit_secs=600, declined=_declined(ARTIFACT))
    good = R.PlannerPass(text='{"markdown": "Framed."}', ended=R.WROTE, limit_secs=600)
    briefs = _scripted(monkeypatch, declined, good)

    assert asyncio.run(pw.run_step_pass(object(), object(), loop.id, "step-0")) is None

    assert len(briefs) == 1, "the retry told the planner to write what she declined"
    session = loop_files.read_plan_session(loop.id)
    step = session.steps[0]
    assert step.status == StepStatus.PENDING.value
    assert step.error == (
        "The planner did not write step_artifact.json: you declined write_file "
        "(step_artifact.json)."
    )
    # A poll or a restart's re-kick runs nothing: only her Retry does.
    assert session.paused == {"by": pw.DECLINED_PAUSE}
    assert asyncio.run(pw.advance_plan(object(), object(), loop.id)) == "paused"
    assert len(briefs) == 1
    pw.clear_design_error(loop.id)  # her Retry
    assert asyncio.run(pw.advance_plan(object(), object(), loop.id)) == "produced"
    assert "previous attempt" not in briefs[1]


def test_a_design_pass_she_declined_says_so_and_waits_for_her_retry(monkeypatch):
    loop = store.create(Loop(id="", name="Fix the feed titles", kind="code", task="fix it"))
    store.update_status(loop.id, LoopStatus.PLANNING)
    _scripted(monkeypatch, R.PlannerPass(ended=R.DECLINED, declined=_declined(pw.STEPS_SENTINEL)))

    assert asyncio.run(pw.run_design_pass(object(), object(), loop.id)) is None

    error = loop_files.read_plan_session(loop.id).design_error
    assert error == (
        "The planner did not write plan_steps.json: you declined write_file (plan_steps.json). "
        "Retry planning when you want it to try again."
    )
    assert "more concrete" not in error


@pytest.mark.asyncio
async def test_the_planner_pass_ends_at_her_deny_before_its_next_cycle(monkeypatch):
    """The pass reads its own session's Deny the moment the planner's turn is over, rather than
    leaving its nudge loop to send the same brief again a minute later."""
    from types import SimpleNamespace

    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    session = SimpleNamespace(
        _trust=False, _extra_tool_roots=[], _last_turn_refusal=None, running=False
    )
    fired: list[str] = []
    removed: list[str] = []

    class _Svc:
        async def add(self, **_kw):
            return None

        def get_by_session(self, _key):
            if not fired:  # its first turn has run, and she declined its write
                fired.append("turn")
                session._last_turn_declined = [_declined("/x/loops/a1/step_artifact.json")[0]]
            return SimpleNamespace(id="N1", active=True)

        async def remove(self, row_id):
            removed.append(row_id)

    state = SimpleNamespace(
        get_or_create_session=lambda **_kw: session, push_sessions_update=lambda: None
    )
    result = await R.run_planner_pass(
        state,
        _Svc(),
        session_key="loop-plan-a1b2c3d4",
        agent_name="planner",
        workspace_dir="",
        files_dir="/x/loops/a1",
        sentinel="step_artifact.json",
        brief="plan",
        app="loops",
        timeout_secs=5,
    )
    assert result.ended == R.DECLINED
    assert result.declined == _declined(ARTIFACT), "named in the loop's folder by its place there"
    assert fired == ["turn"] and removed == ["N1"], "its nudge loop would send the brief again"


@pytest.mark.asyncio
async def test_a_deny_from_an_earlier_pass_does_not_end_the_next_one(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(R, "PLANNER_POLL_SECS", 0.01)
    session = SimpleNamespace(
        _trust=False,
        _extra_tool_roots=[],
        _last_turn_refusal=None,
        _last_turn_declined=[{"tool": "write_file", "names": ["/x/plan_steps.json"]}],
        running=False,
    )

    class _Svc:
        async def add(self, **_kw):
            return None

        def get_by_session(self, _key):
            return SimpleNamespace(id="N1", active=True)

        async def remove(self, _row_id):
            return None

    state = SimpleNamespace(
        get_or_create_session=lambda **_kw: session, push_sessions_update=lambda: None
    )
    result = await R.run_planner_pass(
        state,
        _Svc(),
        session_key="loop-plan-a1b2c3d4",
        agent_name="planner",
        workspace_dir="",
        files_dir="",
        sentinel="step_artifact.json",
        brief="plan",
        app="loops",
        timeout_secs=0.2,
    )
    assert result.ended == R.TIMED_OUT, "a Deny of an earlier pass ended this one"
