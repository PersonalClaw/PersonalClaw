"""A Verifiable loop runs its check where its worker wrote, and says what came of every check.

A goal loop with no bound workspace has its worker's shell start in the workspace root, so a check
like ``test -f RELEASE_NOTES.md`` is written against that folder. The supervisor ran the same check
with no folder at all, so it ran wherever the gateway process happened to be started (its owner's
home, a checkout): the file was never there, the check failed every cycle, nothing was logged
(a failing check is not an error) and nothing was written on the loop. The loop could never finish
and its page said nothing about why.

What the code must do, and what these tests hold it to:

* the check runs in the loop's work folder (``effective_dir`` — what the loop's page shows as
  "Files the worker writes land in"), and the worker's brief names that folder;
* a check that passes (and, for a goal with several sub-goals, a judge that agrees) completes the
  loop as done, with one notification;
* every cycle's check, and the judge's answer when it is asked, is a verdict on the loop's ledger,
  which the loop's page reads;
* a check that cannot run, or a judge that gives no answer, is said in words on that verdict, and
  the loop keeps going rather than finishing on it.

The worker is scripted: each test writes what a worker's turn would leave behind (its file, then
its finding), and drives the real watchdog poll over a real loop row. No model is called: the judge
is a stub that answers what the test says it answers.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus, effective_dir

CHECK = "test -f RELEASE_NOTES.md && wc -l RELEASE_NOTES.md"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _homes(monkeypatch, tmp_path):
    """Destructive (loop rows, finding files, a workspace), so every store this reaches is in
    tmp_path: the loop store at both of its bindings, and the workspace root through its own
    documented override. The process is moved to a folder that is NOT the work folder, which is
    where a gateway started from its owner's home would be."""
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "workspace"))
    elsewhere = tmp_path / "where-the-gateway-was-started"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert loop_files.config_dir() == tmp_path, "the store redirect did not take — refusing to run"
    return tmp_path


class _FakeSession:
    def __init__(self, key):
        self.key = key
        self.running = False
        self._trust = True
        self.messages = []


class _FakeState:
    def __init__(self):
        self._sessions = {}
        self.notes = []
        from personalclaw.dashboard.sse import SseRegistry

        self._sse = SseRegistry()

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *kinds):
        pass

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body, meta or {}))


class _FakeNudge:
    def __init__(self, lid, session_name):
        self.id, self.session_name, self.active, self.cycle_count = lid, session_name, True, 0


class _FakeSvc:
    def __init__(self):
        self._loops = {}

    async def add(self, *, session_name, message, idle_secs, max_cycles, stop_sentinel_path, **_):
        lp = _FakeNudge(f"N{len(self._loops) + 1}", session_name)
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)

    def list_all(self):
        return list(self._loops.values())

    async def update(self, loop_id, **kw):
        pass

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


class _Watchdog(W.LoopWatchdog):
    """The real watchdog; it also keeps the events it publishes, so a test can read them."""

    def __init__(self):
        super().__init__(_FakeState(), _FakeSvc())
        self.events: list[tuple[str, object]] = []

    def _publish(self, loop_id, event, data=None):
        self.events.append((event, data))
        super()._publish(loop_id, event, data)


def _verifiable(*, sub_goals=(), command=CHECK, **over):
    base = dict(
        id="",
        name="Release notes",
        kind="goal",
        task="Draft the release notes from the changelog",
        kind_config={
            "goal_type": "verifiable",
            "verify_command": command,
            "sub_goals": list(sub_goals),
        },
        idle_secs=120,
        max_cycles=30,
        attended=False,
    )
    base.update(over)
    loop = store.create(Loop(**base))
    store.update_status(loop.id, LoopStatus.RUNNING)
    return store.get(loop.id)


def _worker_folder() -> Path:
    """Where an unbound worker's relative writes land: its shell starts in the workspace root
    (``config.loader.default_workspace_dir``). Resolved independently of the supervisor's own
    choice, so the test does not restate the code under test."""
    from personalclaw.config.loader import workspace_root

    return workspace_root()


def _scripted_turn(loop, cycle, *, writes_the_file, new_findings=1, summary=""):
    """What one worker turn leaves behind: maybe the deliverable, then the cycle's finding."""
    if writes_the_file:
        (_worker_folder() / "RELEASE_NOTES.md").write_text(
            "# 0.9.0\n\n## Features\n- feeds\n\n## Fixes\n- digest\n", encoding="utf-8"
        )
    finding = {
        "cycle": cycle,
        "summary": summary or f"cycle {cycle} drafted the notes",
        "new_findings_count": new_findings,
    }
    (loop_files.loop_dir(loop.id) / "findings" / f"cycle_{cycle:03d}.json").write_text(
        json.dumps(finding), encoding="utf-8"
    )


def _start(wd, loop):
    wd._state._sessions[manager.session_key(loop.id)] = _FakeSession(manager.session_key(loop.id))
    _run(wd._poll_once())  # first sight seeds liveness


def _judge_answers(monkeypatch, *answers):
    """The judge, scripted: each call answers the next of *answers*; the prompts it was shown
    are kept, in order."""
    import personalclaw.loop.gates as gates

    shown: list[str] = []
    queue = list(answers)

    async def _judge(prompt, *, loop_id):
        shown.append(prompt)
        return queue.pop(0) if queue else ""

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    return shown


# ── the folder ──


def test_a_passing_check_in_the_work_folder_completes_the_loop_with_one_notification():
    loop = _verifiable()
    wd = _Watchdog()
    _start(wd, loop)
    _scripted_turn(loop, 1, writes_the_file=True)
    before = len(wd._state.notes)
    _run(wd._poll_once())

    done = store.get(loop.id)
    assert (
        done.status == LoopStatus.COMPLETE.value
    ), f"the check never passed: it did not run in {effective_dir(loop)}, where the worker wrote"
    assert done.stop_reason == "done"
    titles = [title for (_kind, title, _body, _meta) in wd._state.notes[before:]]
    assert titles == ["Loop complete"], titles


def test_the_brief_names_the_folder_the_check_runs_in():
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    loop = _verifiable()
    brief = kinds.get("goal").build_brief(loop)
    assert f"`{CHECK}` in `{effective_dir(loop)}`" in brief

    general = store.create(
        Loop(
            id="",
            name="g",
            kind="general",
            task="make it pass",
            kind_config={"verify_command": "true"},
        )
    )
    assert f"`true` in `{effective_dir(general)}`" in kinds.get("general").build_brief(general)


def test_a_code_loop_without_stages_checks_its_own_folder():
    """A code loop with no bound workspace works from its own folder (the brief sends it there);
    its completion check ran in the gateway's folder instead, where its tests are not."""
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    code = store.create(
        Loop(
            id="",
            name="c",
            kind="code",
            task="write slugify",
            kind_config={"verify_command": "test -f slugify.py"},
        )
    )
    (loop_files.loop_dir(code.id) / "slugify.py").write_text("def slugify(s): ...\n")
    published: list[str] = []
    ctx = SimpleNamespace(publish=lambda lid, event, data=None: published.append(event))
    assert _run(kinds.get("code")._no_stage_done(code, [{"cycle": 1}], ctx)) is True
    assert published == []


# ── every check is on the loop ──


def test_every_cycles_check_and_the_judges_answer_are_recorded_on_the_loop(monkeypatch):
    shown = _judge_answers(monkeypatch, "FAIL", "PASS")
    loop = _verifiable(sub_goals=["Features section", "Fixes section"])
    wd = _Watchdog()
    _start(wd, loop)

    _scripted_turn(loop, 1, writes_the_file=False)  # nothing written yet: the check fails
    _run(wd._poll_once())
    _scripted_turn(loop, 2, writes_the_file=True)  # the check passes; the judge says not yet
    _run(wd._poll_once())
    _scripted_turn(loop, 3, writes_the_file=True)  # the judge agrees: done
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.COMPLETE.value
    verdicts = loop_files.get_verdicts(loop.id)
    assert [v["cycle"] for v in verdicts] == [1, 2, 3]
    where = effective_dir(loop)
    first, second, third = verdicts
    assert first["check"]["outcome"] == "failed" and first["check"]["exit_code"] == 1
    assert first["check"]["dir"] == where and "judge" not in first and first["done"] is False
    assert "failed" in first["done_reason"] and where in first["done_reason"]
    assert second["check"]["outcome"] == "passed" and second["judge"]["outcome"] == "fail"
    assert second["done"] is False
    assert third["judge"]["outcome"] == "pass" and third["done"] is True
    # The judge is shown what the check printed: it judges the result, not only the narration.
    assert len(shown) == 2 and all("RELEASE_NOTES.md" in p for p in shown)
    # The loop's page reads them off the loop's own view.
    assert [v["cycle"] for v in store.get_redacted(loop.id)["verdicts"]] == [1, 2, 3]
    # Each one was told to the page as it landed.
    assert [d["cycle"] for (e, d) in wd.events if e == "cycle_verdict"] == [1, 2, 3]


# ── what cannot run is said ──


def test_a_check_that_cannot_run_is_said_in_words_and_the_loop_keeps_going():
    loop = _verifiable(command="/nonexistent/pc-fixture-check --all")
    wd = _Watchdog()
    _start(wd, loop)
    _scripted_turn(loop, 1, writes_the_file=True)
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    (verdict,) = loop_files.get_verdicts(loop.id)
    assert verdict["check"]["outcome"] == "not_run"
    assert "not installed" in verdict["check"]["not_run"]
    assert verdict["cannot_judge"].startswith("The check could not run in ")
    assert "runs it again" in verdict["cannot_judge"]
    assert any(e == "judge_error" for (e, _d) in wd.events)


def test_a_refused_check_is_said_and_pauses_the_loop_instead_of_promising_another_run():
    """A check the shell denylist refuses is recorded with the rule, and the loop pauses with it
    as its question; its verdict does not also say the loop keeps going, since it does not."""
    from personalclaw.config.loader import config_dir

    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"denied_commands": ["pcfixture-notes-gate"]}}), encoding="utf-8"
    )
    loop = _verifiable(command="pcfixture-notes-gate RELEASE_NOTES.md")
    wd = _Watchdog()
    _start(wd, loop)
    _scripted_turn(loop, 1, writes_the_file=True)
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    (verdict,) = loop_files.get_verdicts(loop.id)
    assert verdict["check"]["outcome"] == "refused"
    assert "the shell denylist refused it" in verdict["done_reason"]
    assert "cannot_judge" not in verdict
    assert "Change the check command" in (loop_files.pending_question(loop.id) or {})["question"]
    assert not any(e == "judge_error" for (e, _d) in wd.events)


def test_a_judge_that_gives_no_answer_is_said_not_skipped(monkeypatch):
    _judge_answers(monkeypatch, "")  # the judge's model could not be reached
    loop = _verifiable(sub_goals=["Features section", "Fixes section"])
    wd = _Watchdog()
    _start(wd, loop)
    _scripted_turn(loop, 1, writes_the_file=True)
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    (verdict,) = loop_files.get_verdicts(loop.id)
    assert verdict["check"]["outcome"] == "passed"
    assert verdict["judge"]["outcome"] == "no_answer"
    assert "the judge gave no answer" in verdict["cannot_judge"]
    assert "Reasoning" in verdict["cannot_judge"]  # names the model setting to look at


def test_an_open_ended_judge_that_gives_no_verdict_is_said_not_skipped(monkeypatch):
    """The open-ended loop's judge is the same kind of check: when it gives nothing, the loop
    says so on the cycle instead of leaving it blank."""
    from personalclaw.loop import judge as judge_mod
    from personalclaw.loop import supervisor
    from personalclaw.workflows.supervisor_policy import policy_for_kind

    async def _no_verdict(*_a, **_k):
        return None

    monkeypatch.setattr(judge_mod, "assess_cycle", _no_verdict)
    loop = store.create(
        Loop(
            id="",
            name="o",
            kind="goal",
            task="survey the field",
            kind_config={"goal_type": "open_ended", "judge_calibrated": True},
        )
    )
    findings = [{"cycle": 1, "summary": "read three papers"}]
    policy = policy_for_kind(loop.kind, loop.kind_config)
    assert _run(supervisor.done_signal(loop, findings, policy)) is None
    (verdict,) = loop_files.get_verdicts(loop.id)
    assert verdict["cycle"] == 1 and verdict["done"] is False
    assert verdict["cannot_judge"].startswith("The judge gave no verdict for this cycle")
    assert "marginal_value" not in verdict, "a cycle nobody scored must not carry a score"


# ── one notification ──


def test_a_completion_with_nothing_to_graduate_is_not_second_guessed(monkeypatch, tmp_path):
    """The reproduce pass guards graduating a loop's document deliverable. A verifiable goal has
    none, so it guarded nothing there, and a disagreeing judge sent a second notification saying
    an output was not graduated when there was no output."""
    from personalclaw.loop import instrument

    asked = []

    async def _disagrees(loop):
        asked.append(loop.id)
        return False

    monkeypatch.setattr(instrument, "reproduce_confirm", _disagrees)
    bound = tmp_path / "repo"
    bound.mkdir()
    (bound / "RELEASE_NOTES.md").write_text("# notes\n")
    loop = _verifiable(workspace_dir=str(bound))
    wd = _Watchdog()
    _start(wd, loop)
    _scripted_turn(loop, 1, writes_the_file=False)
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.COMPLETE.value
    assert [t for (_k, t, _b, _m) in wd._state.notes] == ["Loop complete"]
    assert asked == []


# ── the stall rule still applies ──


def test_a_verifiable_loop_that_reports_nothing_new_stalls_before_its_cap():
    """Pins the existing stall rule on this kind: a check that keeps failing while the worker
    reports nothing new ends in a stall at the window, not at the cycle cap."""
    loop = _verifiable()
    wd = _Watchdog()
    _start(wd, loop)
    window = W.DEFAULT_STAGNATION_WINDOW
    for cycle in range(1, window + 1):
        _scripted_turn(loop, cycle, writes_the_file=False, new_findings=0, summary="no change")
        _run(wd._poll_once())
    assert store.get(loop.id).status == LoopStatus.STAGNANT.value
    assert len(loop_files.get_verdicts(loop.id)) == window


# ── the check's own report ──


@pytest.mark.parametrize(
    ("command", "passed", "exit_code", "printed"),
    [
        ("printf 'ok: 12 passed\\n'", True, 0, "ok: 12 passed"),
        ("printf 'boom\\n' >&2; exit 3", False, 3, "boom"),
    ],
)
def test_the_check_reports_its_exit_code_and_what_it_printed(command, passed, exit_code, printed):
    from personalclaw.loop.gates import CheckReport, run_verify_command

    report = CheckReport()
    assert _run(run_verify_command(command, os.getcwd(), report=report)) is passed
    assert report.exit_code == exit_code and printed in report.output and report.not_run == ""


def test_what_the_check_printed_is_kept_to_its_end():
    from personalclaw.loop.gates import CHECK_OUTPUT_TAIL, CheckReport, run_verify_command

    report = CheckReport()
    command = "i=0; while [ $i -lt 4000 ]; do echo line-$i; i=$((i+1)); done; echo summary-line"
    assert _run(run_verify_command(command, os.getcwd(), report=report)) is True
    assert report.output.endswith("summary-line") and "line-0\n" not in report.output
    assert len(report.output) <= CHECK_OUTPUT_TAIL


@pytest.mark.parametrize(
    ("command", "words"),
    [
        ("/nonexistent/pc-fixture-check", "a program it runs is not installed here (exit 127)"),
        ("", "no check command is set"),
    ],
)
def test_a_check_that_cannot_run_says_why(command, words):
    from personalclaw.loop.gates import CheckReport, run_verify_command

    report = CheckReport()
    assert _run(run_verify_command(command, os.getcwd(), report=report)) is None
    assert words in report.not_run


def test_a_check_the_shell_denylist_refuses_says_which_rule():
    from personalclaw.config.loader import config_dir
    from personalclaw.loop.gates import CheckReport, run_verify_command

    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"denied_commands": ["pcfixture-notes-gate"]}}), encoding="utf-8"
    )
    report = CheckReport()
    assert _run(run_verify_command("pcfixture-notes-gate", os.getcwd(), report=report)) is None
    assert report.not_run.startswith("the shell denylist refused it (the command matches ")
    assert "a pattern added to the shell denylist" in report.not_run
    # Ordinary checks still run beside it.
    assert _run(run_verify_command("true", os.getcwd())) is True


def test_a_check_in_a_missing_folder_says_so(tmp_path):
    from personalclaw.loop.gates import CheckReport, run_verify_command

    report = CheckReport()
    gone = tmp_path / "deleted-workspace"
    assert _run(run_verify_command("true", str(gone), report=report)) is None
    assert report.not_run == f"its folder {gone} does not exist"


def test_a_check_that_outlives_its_bound_says_so(monkeypatch):
    import personalclaw.loop.gates as gates

    monkeypatch.setattr(gates, "VERIFY_TIMEOUT_SECS", 1)
    report = gates.CheckReport()
    assert _run(gates.run_verify_command("sleep 20", os.getcwd(), report=report)) is None
    assert report.not_run == "it was still running after 1 second, so it was stopped"
