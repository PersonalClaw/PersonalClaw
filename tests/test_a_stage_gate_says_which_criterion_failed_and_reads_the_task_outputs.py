"""A Code loop's stage gate judges each exit criterion on the loop's own records, and says which.

Measured on a parallel Code loop: all three of a stage's tasks were done, merged and verified, and
the loop went Blocked — "produced 5+ cycles without clearing its exit criteria" — with nothing
anywhere naming the criterion that failed. The judge had been handed the last four cycle summaries,
cut at 300 characters, so a criterion about "the failing assertion quoted in the task output" was
judged without the task output; it answered one word for the whole stage, and nothing was recorded.
The stall counter then counted the re-check cycles of finished tasks as cycles of a stuck stage.

Now each evaluation records a verdict per criterion — pass, fail or can't tell, each with its
reason — on the loop's ledger, where its page reads it and a Blocked message quotes it; the judge is
shown the stage's tasks and their status and every finding's recorded evidence (cut sensibly, and
told what was cut); and only cycles that did work toward the stage count toward a stall.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import gates, kinds, store, tasks_link
from personalclaw.loop.loop import Loop, LoopStatus

CRITERIA = [
    "Every task of the stage is done",
    "The task output quotes the failing assertion the fix makes pass",
    "The test suite passes",
]
ASSERTION = "AssertionError: assert 'Fish &amp;amp; Chips' == 'Fish &amp; Chips'"


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    yield tmp_path
    code = kinds.get("code")
    code._stall_notified.clear()
    code._stall_progress.clear()


class _Svc:
    def get_by_session(self, _name):
        return None


class _Ctx:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.svc = _Svc()
        self.state = type("S", (), {"_sessions": {}})()
        self.completed: list[tuple[str, str]] = []

    def publish(self, loop_id, event, data=None):
        self.events.append((event, data or {}))

    async def complete(self, loop_id, reason=""):
        self.completed.append((loop_id, reason))


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _stage_loop(*task_titles: str, verification: tuple[str, ...] = ()) -> tuple[Loop, list[str]]:
    plan = [
        {
            "stage": "implementation",
            "title": "Implementation",
            "exit_criteria": CRITERIA,
            "tasks": [{"title": t} for t in task_titles],
        }
    ]
    if verification:
        plan.append(
            {
                "stage": "verification",
                "title": "Verification",
                "exit_criteria": [CRITERIA[2]],
                "tasks": [{"title": t} for t in verification],
            }
        )
    loop = store.create(
        Loop(
            id="",
            name="digest titles",
            kind="code",
            task="stop escaping the digest titles twice",
            plan=plan,
            phase_status={"implementation": "active"},
        )
    )
    tasks_link.provision(loop.id)
    _run(tasks_link.seed_phase_tasks(loop.id))
    loop = store.update_status(loop.id, LoopStatus.RUNNING)
    from personalclaw.tasks import registry

    tasks, _ = _run(
        registry.collect_tasks(task_list_id=tasks_link.phase_list_id(loop, "implementation"))
    )
    by_title = {t.title: t.id for t in tasks}
    return loop, [by_title[t] for t in task_titles]


def _finish(task_id: str) -> None:
    from personalclaw.tasks import registry

    _run(registry.update_task(task_id, provider_name="native", status="done"))


_seq = iter(range(1, 10_000))


def _finding(
    loop_id: str, *, task_id: str = "", summary: str, evidence="", stage="implementation"
) -> None:
    d = loop_files.loop_dir(loop_id) / "findings"
    n = next(_seq)
    name = f"task_{task_id}_{n:03d}.json" if task_id else f"cycle_{n:03d}.json"
    body = {"cycle": n, "stage": stage, "summary": summary, "evidence": evidence}
    if task_id:
        body["task_id"] = task_id
    (d / name).write_text(json.dumps(body))
    loop_files.record_cycle_findings(loop_id)


def _honest_judge(prompt: str) -> str:
    """A judge that answers only from what it is shown, in whichever form it was asked for."""
    asked = re.findall(
        r"^(?:(\d+)\.|-) (.+)$",
        prompt.split("Exit criteria:", 1)[1].split("Evidence", 1)[0],
        flags=re.M,
    )
    tasks_done = "[done]" in prompt and "[open]" not in prompt and "[in_progress]" not in prompt
    proof = {
        CRITERIA[0]: tasks_done,
        CRITERIA[1]: ASSERTION in prompt,
        CRITERIA[2]: "13 passed" in prompt,
    }
    shown = {int(n or i): proof.get(text.strip(), False) for i, (n, text) in enumerate(asked, 1)}
    if '"criteria"' not in prompt:  # the one-word form
        return "PASS" if all(shown.values()) else "FAIL"
    return json.dumps(
        {
            "criteria": [
                {
                    "n": n,
                    "verdict": "pass" if ok else "cant_tell",
                    "reason": "shown in the record" if ok else "not in the record",
                }
                for n, ok in shown.items()
            ]
        }
    )


@pytest.fixture()
def judge(monkeypatch):
    asked: list[str] = []

    async def _judge(prompt, **_kw):
        asked.append(prompt)
        return _honest_judge(prompt)

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    return asked


def _gate(loop: Loop, ctx: _Ctx) -> bool:
    loop = store.get(loop.id)
    findings = loop_files.get_findings(loop.id)
    return _run(kinds.get("code")._stage_gate_passed(loop, 0, findings, ctx))


def _done_stage_with_evidence():
    loop, (escape, digest, tests) = _stage_loop(
        "Escape once in the digest", "Update the digest template", "Run the suite"
    )
    _finding(
        loop.id,
        task_id=escape,
        summary="escape the title once, in the renderer",
        evidence=f"before the fix:\n{ASSERTION}\n1 failed in 0.21s",
    )
    _finding(loop.id, task_id=digest, summary="the template no longer escapes it again")
    _finding(loop.id, task_id=tests, summary="ran the suite", evidence="13 passed in 0.44s")
    for tid in (escape, digest, tests):
        _finish(tid)
    return loop, (escape, digest, tests)


# ── the judge reads the task outputs ──────────────────────────────────────────


def test_a_stage_whose_tasks_are_done_with_evidence_passes_its_gate(judge):
    loop, _tasks = _done_stage_with_evidence()
    ctx = _Ctx()

    assert _gate(loop, ctx) is True, judge[-1][-2000:]

    (prompt,) = judge
    assert ASSERTION in prompt and "13 passed" in prompt, "the judge never saw the task outputs"
    assert "[done] Escape once in the digest" in prompt
    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert gate["passed"] is True
    assert [c["verdict"] for c in gate["criteria"]] == ["pass", "pass", "pass"]


def test_a_failing_gate_records_which_criterion_failed_and_why(monkeypatch):
    loop, _tasks = _done_stage_with_evidence()

    async def _judge(prompt, **_kw):
        return json.dumps(
            {
                "criteria": [
                    {"n": 1, "verdict": "pass", "reason": "all three tasks are done"},
                    {"n": 2, "verdict": "pass", "reason": "the escape task quotes it"},
                    {"n": 3, "verdict": "fail", "reason": "the last run shows 1 failed"},
                ]
            }
        )

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    ctx = _Ctx()

    assert _gate(loop, ctx) is False

    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert gate["passed"] is False and gate["shortfalls"] == ["The test suite passes"]
    assert gate["criteria"][2] == {
        "criterion": "The test suite passes",
        "verdict": "fail",
        "reason": "the last run shows 1 failed",
    }
    assert "Not met: “The test suite passes” (the last run shows 1 failed)." in (
        gate["done_reason"]
    )
    (said,) = [d for e, d in ctx.events if e == "gate_check" and d.get("label") == "exit criteria"]
    assert said["ok"] is False and "The test suite passes" in said["output"]
    # …and the loop's page reads it.
    view = store.get_redacted(loop.id)
    assert any(v.get("gate") == "stage" for v in view["verdicts"])


def test_what_the_judge_could_not_be_shown_is_said_and_answered_cant_tell(judge):
    loop, (escape,) = _stage_loop("Escape once in the digest")
    for n in range(12):  # more than one judge call can be shown
        _finding(loop.id, summary=f"cycle {n} notes", evidence="y" * 2_000)
    long_run = "x" * 20_000 + "\n13 passed"  # longer than one finding may take
    _finding(loop.id, task_id=escape, summary="ran it", evidence=long_run)

    assert _gate(loop, _Ctx()) is False

    (prompt,) = judge
    assert "characters cut" in prompt
    assert "earliest finding" in prompt and "answer cant_tell" in prompt
    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert "cant_tell" in [c["verdict"] for c in gate["criteria"]]
    assert "Can't tell from the record" in gate["done_reason"]


# ── the stall counts only cycles of work ──────────────────────────────────────


def _stall(loop: Loop) -> bool:
    code = kinds.get("code")
    loop = store.get(loop.id)
    findings = loop_files.get_findings(loop.id)
    hit = False
    for _ in range(2):  # the first look only sets the stage's progress baseline
        hit = (
            _run(code._escalate_stall_if_stuck(loop, 0, "implementation", findings, _Ctx())) or hit
        )
    return hit


def test_re_checks_of_finished_tasks_never_read_as_a_stuck_stage():
    loop, tasks = _done_stage_with_evidence()
    for tid in tasks:  # each finished task re-checked, again and again
        _finding(loop.id, task_id=tid, summary="re-check, no changes")
        _finding(loop.id, task_id=tid, summary="re-check, no changes")

    assert _stall(loop) is False
    assert store.get(loop.id).status == LoopStatus.RUNNING.value


def test_a_stage_that_really_is_stuck_says_which_criteria_it_is_stuck_on(monkeypatch):
    loop, (escape,) = _stage_loop("Escape once in the digest")
    for n in range(5):  # five cycles of the stage worker's own work
        _finding(loop.id, summary=f"tried approach {n}")

    async def _judge(prompt, **_kw):
        return json.dumps(
            {"criteria": [{"n": 3, "verdict": "fail", "reason": "the last run shows 1 failed"}]}
        )

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    _gate(loop, _Ctx())

    assert _stall(loop) is True

    blocked = store.get(loop.id)
    assert blocked.status == LoopStatus.BLOCKED.value
    message = blocked.error_message or ""
    assert "Not met: “The test suite passes” (the last run shows 1 failed)" in message
    assert re.search(r"Can't tell from the record: .*Every task of the stage is done", message)


# ── the judge's answer, read ──────────────────────────────────────────────────


def test_each_criterion_gets_its_own_verdict_and_a_missing_one_is_cant_tell():
    raw = (
        'Here you go: {"criteria": [{"n": 2, "verdict": "fail", "reason": "1 failed"}, '
        '{"n": 1, "verdict": "maybe", "reason": "?"}]}'
    )
    verdicts, rendered = gates.criteria_verdicts(raw, CRITERIA)
    assert rendered is True
    assert [v["verdict"] for v in verdicts] == ["cant_tell", "fail", "cant_tell"]
    assert verdicts[1]["reason"] == "1 failed"
    assert verdicts[2]["reason"] == "the judge gave no answer for this criterion"


def test_a_one_word_answer_and_no_answer_are_read_honestly():
    passed, _ = gates.criteria_verdicts("PASS", CRITERIA)
    assert {v["verdict"] for v in passed} == {"pass"}
    failed, rendered = gates.criteria_verdicts("FAIL", CRITERIA)
    assert rendered is True and {v["verdict"] for v in failed} == {"cant_tell"}
    assert "without saying which criterion" in failed[0]["reason"]
    nothing, rendered = gates.criteria_verdicts("", CRITERIA)
    assert rendered is False and nothing[0]["reason"] == "the judge gave no verdict"


# ── the supervisor honours a gate that passes, and a loop whose work is done ends ─────────────


def _verification_task(loop: Loop) -> str:
    from personalclaw.tasks import registry

    list_id = tasks_link.phase_list_id(loop, "verification")
    tasks, _ = _run(registry.collect_tasks(task_list_id=list_id))
    (task,) = tasks
    return task.id


def test_a_loop_whose_work_is_all_done_ends_complete_in_the_cycle_it_is_judged(judge):
    """The worker ran ahead into the next stage and finished it; the supervisor still named the
    first. Once the first stage's gate passes on its evidence, the next is judged at once on its
    own, so a loop whose every task is done ends complete instead of re-checking finished work."""
    loop, (escape, digest, tests) = _stage_loop(
        "Escape once in the digest",
        "Update the digest template",
        "Run the suite",
        verification=("Run the full suite",),
    )
    _finding(loop.id, task_id=escape, summary="escaped once", evidence=f"before:\n{ASSERTION}")
    _finding(loop.id, task_id=digest, summary="the template no longer escapes it")
    _finding(loop.id, task_id=tests, summary="ran the suite", evidence="13 passed in 0.44s")
    full = _verification_task(store.get(loop.id))
    _finding(
        loop.id,
        task_id=full,
        stage="verification",
        summary="ran the full suite and the linter",
        evidence="13 passed in 0.51s\nruff: no new findings",
    )
    for tid in (escape, digest, tests, full):
        _finish(tid)
    ctx = _Ctx()

    done = _run(kinds.get("code").on_new_cycle(store.get(loop.id), _all(loop), ctx))

    assert done is True and ctx.completed == [(loop.id, "all stages complete")]
    after = store.get(loop.id)
    assert after.phase_status == {"implementation": "done", "verification": "done"}
    assert len(judge) == 2, "each stage was judged once, on its own evidence"


def test_a_stage_handed_over_with_no_work_of_its_own_waits_for_it(judge):
    loop, (escape, digest, tests) = _stage_loop(
        "Escape once in the digest",
        "Update the digest template",
        "Run the suite",
        verification=("Run the full suite",),
    )
    _finding(loop.id, task_id=escape, summary="escaped once", evidence=f"before:\n{ASSERTION}")
    _finding(loop.id, task_id=digest, summary="the template no longer escapes it")
    _finding(loop.id, task_id=tests, summary="ran the suite", evidence="13 passed in 0.44s")
    for tid in (escape, digest, tests):
        _finish(tid)
    ctx = _Ctx()

    done = _run(kinds.get("code").on_new_cycle(store.get(loop.id), _all(loop), ctx))

    assert done is False and ctx.completed == []
    after = store.get(loop.id)
    assert after.phase_status == {"implementation": "done", "verification": "active"}
    assert len(judge) == 1, "an untouched stage was judged on the stage before it"


def test_a_stage_still_stuck_after_a_steer_is_escalated_again(monkeypatch):
    loop, (escape,) = _stage_loop("Escape once in the digest")
    _finish(escape)  # its task is done; its gate still fails on the test run

    async def _judge(prompt, **_kw):
        return json.dumps(
            {"criteria": [{"n": 3, "verdict": "fail", "reason": "the last run shows 1 failed"}]}
        )

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    code = kinds.get("code")

    def _cycles(k: int) -> None:
        for n in range(k):
            _finding(loop.id, summary=f"tried approach {n}")
            _run(code.on_new_cycle(store.get(loop.id), _all(loop), _Ctx()))

    # Five cycles of work, and one more: the task resolving is progress the first look credits.
    _cycles(6)
    assert store.get(loop.id).status == LoopStatus.BLOCKED.value
    store.update_status(loop.id, LoopStatus.RUNNING)  # she steered it, and it resumed

    _cycles(4)
    assert store.get(loop.id).status == LoopStatus.RUNNING.value, "escalated before 5 more"
    _cycles(1)
    assert store.get(loop.id).status == LoopStatus.BLOCKED.value, "the loop spun on after a steer"


def _all(loop: Loop) -> list[dict]:
    return loop_files.get_findings(loop.id)


# ── the stage is judged the moment its last task worker is done ───────────────
#
# A task worker writes its finding and ends its turn; it is reaped a poll or two later, once that
# turn is over. The finding's own cycle found it still at work and left the stage unjudged, and
# nothing judged it after that until the stage worker, stood back up, spent a turn re-checking
# finished work. Driven through the watchdog's own poll, on a real repository and worktree.


class _Rows:
    """The nudge loops as the service keeps them: one per worker session."""

    def __init__(self):
        self._loops: dict[str, object] = {}
        self._n = 0

    async def add(self, *, session_name, max_cycles=0, **_kw):
        from types import SimpleNamespace

        self._loops = {k: v for k, v in self._loops.items() if v.session_name != session_name}
        self._n += 1
        row = SimpleNamespace(
            id=f"N{self._n}", session_name=session_name, active=True, cycle_count=0,
            max_cycles=max_cycles, error_count=0,
        )  # fmt: skip
        self._loops[row.id] = row
        return row

    def get_by_session(self, session_name):
        return next((r for r in self._loops.values() if r.session_name == session_name), None)

    def list_all(self):
        return list(self._loops.values())

    async def update(self, row_id, **kw):
        for k, v in kw.items():
            setattr(self._loops[row_id], k, v)

    async def remove(self, row_id):
        self._loops.pop(row_id, None)


class _Turns:
    async def stop_turn(self, key, *, force=False, **_kw):
        return "soft"


class _Dashboard:
    conversation_log = None

    def __init__(self):
        self._sessions: dict = {}
        self.sessions = _Turns()

    def get_or_create_session(self, *, name, **_kw):
        from types import SimpleNamespace

        return self._sessions.setdefault(
            name,
            SimpleNamespace(
                key=name, running=False, messages=[], acp_provider="", acp_provider_agent="",
                reasoning_effort="", acp_mode="", _extra_tool_roots=None, _queue=[],
            ),
        )  # fmt: skip

    def push_sessions_update(self):
        pass

    def loop_sse(self):
        from personalclaw.dashboard.sse import SseRegistry

        return SseRegistry()

    def push_refresh(self, *kinds):
        pass

    def notify(self, *args, **kwargs):
        pass

    def waiting_on_owner(self, key):
        return False


def _git(ws, home, *args):
    import os
    import subprocess

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "user.name=t", "-c", "user.email=t@example.com",
         *args],
        cwd=ws, check=True, capture_output=True, env=env, text=True,
    )  # fmt: skip


@pytest.mark.parametrize("attended", [False, True], ids=["unattended", "attended"])
def test_a_stage_is_judged_when_its_last_task_worker_is_done_and_the_loop_ends(
    tmp_path, monkeypatch, attended
):
    """Unattended, the task's work merges as its worker is reaped. Attended, it waits for its
    owner's merge, and the stage is judged when the work she approved lands."""
    from personalclaw.loop import manager
    from personalclaw.loop import watchdog as W
    from personalclaw.loop import worktree
    from personalclaw.loop.loop import LoopStopReason
    from personalclaw.loop.manager import session_key, task_session_key
    from personalclaw.tasks import registry

    ws = tmp_path / "newsfold"
    ws.mkdir()
    (ws / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n### Fixed\n")
    _git(ws, tmp_path, "init", "-q")
    _git(ws, tmp_path, "config", "user.name", "t")
    _git(ws, tmp_path, "config", "user.email", "t@example.com")
    _git(ws, tmp_path, "add", "CHANGELOG.md")
    _git(ws, tmp_path, "commit", "-q", "-m", "init")
    rows = _Rows()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: rows)
    monkeypatch.setattr("personalclaw.llm.registry.sends_to_this_machine", lambda entry: True)
    asked: list[str] = []

    async def _judge(prompt, **_kw):
        asked.append(prompt)
        ok = "Digest titles are escaped once" in prompt
        verdict = "pass" if ok else "cant_tell"
        return json.dumps({"criteria": [{"n": 1, "verdict": verdict, "reason": "the diff"}]})

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    loop = store.create(
        Loop(
            id="",
            name="digest titles",
            kind="code",
            task="add a Fixed line for the digest titles",
            attended=attended,
            autopilot=True,
            model="ollama:local-model",
            max_cycles=30,
            workspace_dir=str(ws),
            plan=[
                {
                    "stage": "implementation",
                    "title": "Implementation",
                    "exit_criteria": ["CHANGELOG.md has a Fixed line for the digest titles"],
                    "tasks": [{"title": "Add the Fixed line"}],
                }
            ],
            phase_status={"implementation": "active"},
        )
    )
    tasks_link.provision(loop.id)
    _run(tasks_link.seed_phase_tasks(loop.id))
    dash = _Dashboard()
    _run(manager.start(dash, rows, loop.id))
    wd = W.LoopWatchdog(dash, rows)
    wd._swept = True
    _run(wd._poll_once())  # the stage fans out: its one task worker starts in its worktree
    loop = store.get(loop.id)
    (tid,) = loop.kind_config["queued_task_ids"]
    key = task_session_key(loop.id, tid)
    assert rows.get_by_session(key) is not None, "no task worker started"
    stage_worker = rows.get_by_session(session_key(loop.id))
    assert stage_worker.active is False, "the stage worker kept its cycles beside a task worker"

    # Its turn: the edit, the finding, the task marked done, and the turn not yet over.
    wt = worktree.worktree_path(str(ws), tid, loop.tasks_project_id)
    with open(f"{wt}/CHANGELOG.md", "a", encoding="utf-8") as fh:
        fh.write("- Digest titles are escaped once. (#21)\n")
    dash.get_or_create_session(name=key).running = True
    (loop_files.loop_dir(loop.id) / "findings" / f"task_{tid}_001.json").write_text(
        json.dumps(
            {
                "cycle": 1,
                "task_id": tid,
                "summary": "added the Fixed line",
                "evidence": "CHANGELOG.md: - Digest titles are escaped once. (#21)",
            }
        )
    )
    _run(registry.update_task(tid, provider_name="native", status="done"))
    _run(wd._poll_once())  # the finding's cycle: its worker is still at work
    assert asked == [], "the stage was judged while its task worker was still writing"
    assert store.get(loop.id).status == LoopStatus.RUNNING.value

    dash.get_or_create_session(name=key).running = False  # the turn is over
    _run(wd._poll_once())
    if attended:
        waiting = store.get(loop.id)
        assert waiting.status == LoopStatus.NEEDS_INPUT.value, "nothing asked her to merge it"
        assert asked == [], "the stage was judged before its work was in"
        (task,) = loop_files.pending_question(loop.id)["merge"]["tasks"]
        code = kinds.get("code")
        code.approve_merge(loop.id, {tid: task["tip"]})  # what POST /api/loops/{id}/merge does
        loop_files.clear_question(loop.id)
        _run(manager.start(dash, rows, loop.id))
        _run(wd._poll_once())

    after = store.get(loop.id)
    assert after.status == LoopStatus.COMPLETE.value, after.error_message
    assert after.stop_reason == LoopStopReason.DONE.value
    assert len(asked) == 1, "judged once, the moment its last task worker was done"
    assert "Digest titles are escaped once" in (ws / "CHANGELOG.md").read_text(), "not merged"
    assert stage_worker.active is False, "the stage worker was stood back up to re-check it"
    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert gate["passed"] is True
