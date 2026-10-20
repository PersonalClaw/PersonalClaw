"""A Code loop's stage gate judges what the stage did, on what it observed itself.

Measured on an Attended Code loop for a bug: its first stage had three tasks (the fix, a new
regression test in a second file, a run of the suite), all three were done and merged, and the
checkout was clean. Its gate's judge answered "can't tell" for "the test is added and passing" on
four cycles in a row, and the loop went Blocked. The judge is told to trust only what the
supervisor observed over what the workers report, and the supervisor had observed one file (the
stage's declared deliverable) and a bare "the command passed". Nothing it was shown could ever
confirm a criterion about a second file, or about one named test. In between, the stage's worker
was asked again every cycle, and re-checked finished work.

What the code must do, and what these tests hold it to:

* the gate observes the stage's work as its owner would review it: every file the loop changed in
  the workspace (the diff, and each changed file as it is now), the checks it ran with what they
  printed (each test named, where the runner can be asked to), and every file the stage's
  deliverable names; a stage that edits two files passes on that, and the loop moves on;
* a worker's report is still only a claim: a criterion the observed record does not show still
  fails, and the gate says which criterion and what it looked at;
* a stage whose work is all done but whose gate cannot tell a criterion pauses for its owner at
  once, rather than standing its worker back up to re-check finished work;
* the goal loops' judges are shown what their checks printed too, every file a deliverable names,
  and never cite a line a check printed as evidence of their own.

Workers are scripted (each test writes what a worker's turn leaves behind) over the real watchdog
poll, a real git repository and a real test run. No model is called: the judge is a stand-in that
answers only from what the supervisor observed, as the gate's prompt tells a judge to.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import gates, kinds, manager, store, tasks_link
from personalclaw.loop import watchdog as W
from personalclaw.loop import worktree
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.loop.manager import session_key, task_session_key

PYTHON = shlex.quote(sys.executable)
#: The project's own test run, as a planner writes it.
PYTEST = f"{PYTHON} -m pytest -q -p no:cacheprovider"
#: A build check that runs no test.
COMPILE = f"{PYTHON} -m compileall -q src"

FIX = "render_title in src/newsfold/digest.py escapes a title once"
TEST = "test_an_ampersand_is_escaped_once is added to tests/test_digest.py and passes"
README = "README.md says that titles are escaped once"

DIGEST = '''import html


def render_title(title: str) -> str:
    """The title as the digest page shows it."""
    return f"<h2>{html.escape(html.escape(title))}</h2>"
'''
FIXED_LINE = 'return f"<h2>{html.escape(title)}</h2>"'
DIGEST_FIXED = DIGEST.replace('return f"<h2>{html.escape(html.escape(title))}</h2>"', FIXED_LINE)
TESTS = """from newsfold.digest import render_title


def test_a_plain_title_is_unchanged() -> None:
    assert render_title("Digest") == "<h2>Digest</h2>"
"""
NEW_TEST = """

def test_an_ampersand_is_escaped_once() -> None:
    assert render_title("Tom & Jerry") == "<h2>Tom &amp; Jerry</h2>"
"""
WRONG_TEST = """

def test_an_ampersand_is_escaped_once() -> None:
    assert render_title("Tom & Jerry") == "<h2>Tom & Jerry</h2>"
"""


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _homes(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    assert loop_files.config_dir() == tmp_path, "the store redirect did not take — refusing to run"
    yield tmp_path
    code = kinds.get("code")
    code._stall_notified.clear()
    code._stall_progress.clear()
    code._stood_down.clear()
    code._no_start_point.clear()


# ── the workspace, a real repository ──────────────────────────────────────────


def _git(ws: Path, *args: str) -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(ws.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    done = subprocess.run(
        ["git", "-c", "credential.helper=", *args],
        cwd=ws, check=True, capture_output=True, env=env, text=True,
    )  # fmt: skip
    return done.stdout


def _newsfold(root: Path) -> Path:
    """A small project with a bug in its digest titles: each is escaped twice."""
    ws = root / "newsfold"
    (ws / "src" / "newsfold").mkdir(parents=True)
    (ws / "tests").mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "newsfold"\nversion = "0.9.0"\n\n'
        '[tool.pytest.ini_options]\npythonpath = ["src"]\ntestpaths = ["tests"]\n'
    )
    (ws / ".gitignore").write_text("__pycache__/\n")
    (ws / "README.md").write_text("# newsfold\n\nTurns feeds into one digest page.\n")
    (ws / "src" / "newsfold" / "__init__.py").write_text("")
    (ws / "src" / "newsfold" / "digest.py").write_text(DIGEST)
    (ws / "tests" / "test_digest.py").write_text(TESTS)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.name", "Ada Example")
    _git(ws, "config", "user.email", "ada@example.com")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "newsfold 0.9.0")
    return ws


# ── the gateway around the loop, as the watchdog drives it ────────────────────


class _Rows:
    """The nudge loops as the service keeps them: one per worker session."""

    def __init__(self):
        self._loops: dict[str, object] = {}
        self._n = 0

    async def add(self, *, session_name, max_cycles=0, message="", **_kw):
        self._loops = {k: v for k, v in self._loops.items() if v.session_name != session_name}
        self._n += 1
        row = SimpleNamespace(
            id=f"N{self._n}", session_name=session_name, active=True, cycle_count=0,
            max_cycles=max_cycles, error_count=0, message=message,
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


def _observed(prompt: str) -> str:
    """What a judge may rule on: the prompt without the workers' findings, which are their own
    account (a block opening ``- cycle N`` and its indented lines)."""
    kept: list[str] = []
    in_finding = False
    for line in prompt.splitlines():
        if re.match(r"^- cycle \S+", line):
            in_finding = True
            continue
        if in_finding and line.startswith("  "):
            continue
        in_finding = False
        kept.append(line)
    return "\n".join(kept)


def _observing_judge(prompt: str) -> str:
    """A judge that rules each criterion only on what the supervisor observed, as the gate's
    prompt tells it to: a worker's word for it is no proof, and what it was not shown it cannot
    tell."""
    asked, evidence = prompt.split("Exit criteria:", 1)[1].split("Evidence", 1)
    criteria = re.findall(r"^(\d+)\. (.+)$", asked, flags=re.M)
    seen = _observed(evidence)
    shows = {
        FIX: FIXED_LINE in seen,
        TEST: "def test_an_ampersand_is_escaped_once" in seen
        and re.search(r"PASSED \S*test_digest\.py::test_an_ampersand_is_escaped_once", seen),
        README: "titles are escaped once" in seen.split("README.md", 1)[-1],
    }
    failed = {TEST: "FAILED tests/test_digest.py::test_an_ampersand_is_escaped_once" in seen}
    answers = []
    for n, text in criteria:
        text = text.strip()
        if failed.get(text):
            answers.append({"n": int(n), "verdict": "fail", "reason": "the test run shows it fail"})
        elif shows.get(text):
            answers.append({"n": int(n), "verdict": "pass", "reason": "the supervisor saw it"})
        else:
            answers.append(
                {
                    "n": int(n),
                    "verdict": "cant_tell",
                    "reason": "nothing the supervisor observed shows it; only a worker says so",
                }
            )
    return json.dumps({"criteria": answers})


@pytest.fixture()
def judge(monkeypatch):
    asked: list[str] = []

    async def _judge(prompt, **_kw):
        asked.append(prompt)
        return _observing_judge(prompt)

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    return asked


def _code_loop(ws: Path, criteria: list[str], *, verify: str, test: str, attended: bool) -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="digest titles",
            kind="code",
            task="fix the double-escaped titles in the digest",
            attended=attended,
            autopilot=True,
            model="ollama:local-model",
            max_cycles=30,
            workspace_dir=str(ws),
            plan=[
                {
                    "stage": "implementation",
                    "title": "Remove the double escape and add its test",
                    "exit_criteria": criteria,
                    "deliverable": "src/newsfold/digest.py",
                    "tasks": [{"title": "Escape the title once"}, {"title": "Add the test"}],
                }
            ],
            phase_status={"implementation": "active"},
            kind_config={"verify_command": verify, "test_command": test, "queued_task_ids": []},
        )
    )
    tasks_link.provision(loop.id)
    _run(tasks_link.seed_phase_tasks(loop.id))
    return store.get(loop.id)


def _task_id(loop: Loop, title: str) -> str:
    from personalclaw.tasks import registry

    tasks, _ = _run(
        registry.collect_tasks(task_list_id=tasks_link.phase_list_id(loop, "implementation"))
    )
    return next(t.id for t in tasks if t.title == title)


def _work(dash, ws: Path, loop: Loop, tid: str, path: str, text: str, finding: dict) -> None:
    """One task worker's turn: its edit in its worktree, its finding, its task marked done, and the
    turn not yet over."""
    from personalclaw.tasks import registry

    wt = Path(worktree.worktree_path(str(ws), tid, loop.tasks_project_id))
    assert wt.is_dir(), f"no worktree was cut for {tid}"
    (wt / path).write_text(text)
    dash.get_or_create_session(name=task_session_key(loop.id, tid)).running = True
    (loop_files.loop_dir(loop.id) / "findings" / f"task_{tid}_001.json").write_text(
        json.dumps({"cycle": 1, "task_id": tid, "stage": "implementation", **finding})
    )
    _run(registry.update_task(tid, provider_name="native", status="done"))


def _drive_to_the_gate(tmp_path, monkeypatch, criteria, *, verify, test, attended, tests_text):
    """A stage of two tasks in two files, each done by its own task worker in its own worktree and
    merged, up to the poll that judges the stage. Returns the loop, the gateway pieces and the
    workspace."""
    ws = _newsfold(tmp_path)
    rows, dash = _Rows(), _Dashboard()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: rows)
    monkeypatch.setattr("personalclaw.llm.registry.sends_to_this_machine", lambda entry: False)
    loop = _code_loop(ws, criteria, verify=verify, test=test, attended=attended)
    _run(manager.start(dash, rows, loop.id))
    wd = W.LoopWatchdog(dash, rows)
    wd._swept = True
    _run(wd._poll_once())  # the stage fans out: one task worker per task, each in its worktree
    loop = store.get(loop.id)
    fix, add = _task_id(loop, "Escape the title once"), _task_id(loop, "Add the test")
    assert {fix, add} <= set(loop.kind_config["queued_task_ids"]), "the stage did not fan out"
    _work(
        dash, ws, loop, fix, "src/newsfold/digest.py", DIGEST_FIXED,
        {"summary": "escaped each title once", "files_touched": ["src/newsfold/digest.py"]},
    )  # fmt: skip
    _work(
        dash, ws, loop, add, "tests/test_digest.py", tests_text,
        {
            "summary": "added test_an_ampersand_is_escaped_once; README.md now says that "
            "titles are escaped once",
            "files_touched": ["tests/test_digest.py", "README.md"],
            "evidence": "PASSED tests/test_digest.py::test_an_ampersand_is_escaped_once",
        },
    )  # fmt: skip
    _run(wd._poll_once())  # the findings' cycle: the workers are still at work
    for tid in (fix, add):
        dash.get_or_create_session(name=task_session_key(loop.id, tid)).running = False
    _run(wd._poll_once())
    if attended:
        waiting = store.get(loop.id)
        assert waiting.status == LoopStatus.NEEDS_INPUT.value, "nothing asked her to merge it"
        tips = {
            t["task_id"]: t["tip"] for t in loop_files.pending_question(loop.id)["merge"]["tasks"]
        }
        kinds.get("code").approve_merge(loop.id, tips)  # what POST /api/loops/{id}/merge does
        loop_files.clear_question(loop.id)
        _run(manager.start(dash, rows, loop.id))
        _run(wd._poll_once())
    return store.get(loop.id), rows, dash, wd, ws


COMMANDS = [
    pytest.param({"verify": PYTEST, "test": ""}, id="its build check runs the tests"),
    pytest.param({"verify": COMPILE, "test": PYTEST}, id="its tests run apart from the build"),
]
MODES = [pytest.param(False, id="unattended"), pytest.param(True, id="attended")]


# ── a stage that edits two files passes on what its gate observed ─────────────


@pytest.mark.parametrize("attended", MODES)
@pytest.mark.parametrize("commands", COMMANDS)
def test_a_stage_that_changed_two_files_passes_on_what_its_gate_observed(
    tmp_path, monkeypatch, judge, commands, attended
):
    loop, _rows, _dash, _wd, ws = _drive_to_the_gate(
        tmp_path, monkeypatch, [FIX, TEST], attended=attended, tests_text=TESTS + NEW_TEST,
        **commands,
    )  # fmt: skip

    assert len(judge) == 1, "the stage was not judged once, when its work was in"
    seen = _observed(judge[0])
    assert "def test_an_ampersand_is_escaped_once" in seen, "the second file's change unseen"
    assert FIXED_LINE in seen
    assert re.search(
        r"PASSED \S*test_digest\.py::test_an_ampersand_is_escaped_once", seen
    ), "no check the supervisor ran named the new test"
    assert loop.status == LoopStatus.COMPLETE.value, loop.error_message
    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert gate["passed"] is True
    assert [c["verdict"] for c in gate["criteria"]] == ["pass", "pass"]
    looked_at = " ".join(gate["observed"])
    assert "src/newsfold/digest.py" in looked_at and "tests/test_digest.py" in looked_at
    assert "Tom &amp; Jerry" in (ws / "tests" / "test_digest.py").read_text(), "not merged"


def test_the_workers_words_and_the_workspace_text_reach_the_judge_as_data(
    tmp_path, monkeypatch, judge
):
    _drive_to_the_gate(
        tmp_path, monkeypatch, [FIX, TEST], verify=PYTEST, test="", attended=False,
        tests_text=TESTS + NEW_TEST,
    )  # fmt: skip
    (prompt,) = judge
    diff_at = prompt.index("+def test_an_ampersand_is_escaped_once")
    claim_at = prompt.index("README.md now says that titles are escaped once")
    for at in (diff_at, claim_at):
        opened = prompt.rfind("<untrusted_content", 0, at)
        assert (
            opened != -1 and prompt.find("</untrusted_content>", opened) > at
        ), "text from the workspace or a worker reached the judge outside a fence"
    assert diff_at < claim_at, "the workers' account came before what the supervisor observed"


# ── what the record does not show still fails, and the loop does not spin ─────


def test_a_criterion_the_record_does_not_show_fails_and_the_loop_pauses_at_once(
    tmp_path, monkeypatch, judge
):
    """A worker says README.md says it; nothing the gate observed shows it. The criterion is not
    passed on its word, and with every task of the stage done there is nothing left for a worker to
    do: the loop pauses for its owner now, naming the criterion and what the gate looked at, rather
    than asking its worker to re-check finished work until the anti-spin pause."""
    loop, rows, _dash, wd, _ws = _drive_to_the_gate(
        tmp_path, monkeypatch, [FIX, TEST, README], verify=PYTEST, test="", attended=False,
        tests_text=TESTS + NEW_TEST,
    )  # fmt: skip

    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert gate["passed"] is False
    assert [c["verdict"] for c in gate["criteria"]] == ["pass", "pass", "cant_tell"]
    assert loop.status == LoopStatus.BLOCKED.value, "a stage with nothing left to do kept cycling"
    said = loop.error_message or ""
    assert README in said and "only a worker says so" in said, said
    assert (
        "src/newsfold/digest.py" in said and "tests/test_digest.py" in said
    ), "the pause does not say what the gate looked at"
    stage_worker = rows.get_by_session(session_key(loop.id))
    assert (
        stage_worker is not None and stage_worker.active is False
    ), "the stage worker was stood back up to re-check finished work"
    _run(wd._poll_once())
    assert len(judge) == 1, "the stage was judged again on the same record"
    assert store.get(loop.id).status == LoopStatus.BLOCKED.value


def test_a_criterion_the_record_shows_unmet_keeps_the_stage_at_work(tmp_path, monkeypatch, judge):
    """The new test fails. That is work for the stage's worker to do, not a question for its
    owner: the stage keeps cycling (the stall bound still holds), and the gate says what failed."""
    loop, rows, _dash, _wd, _ws = _drive_to_the_gate(
        tmp_path, monkeypatch, [FIX, TEST], verify=COMPILE, test=PYTEST, attended=False,
        tests_text=TESTS + WRONG_TEST,
    )  # fmt: skip

    assert loop.status == LoopStatus.RUNNING.value, loop.error_message
    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert [c["verdict"] for c in gate["criteria"]] == ["pass", "fail"]
    stage_worker = rows.get_by_session(session_key(loop.id))
    assert stage_worker is not None and stage_worker.active is True, "nobody is fixing the test"
    told = f"The supervisor's gate last found: Not met: “{TEST}” (the test run shows it fail)."
    assert told in stage_worker.message, "its worker is not told what the gate found"


# ── a workspace git does not track ─────────────────────────────────────────────


class _Ctx:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.svc = SimpleNamespace(get_by_session=lambda _name: None)
        self.state = SimpleNamespace(_sessions={})

    def publish(self, loop_id, event, data=None):
        self.events.append((event, data or {}))

    async def complete(self, loop_id, reason=""):
        pass


def _greenfield(deliverable: str, criteria: list[str]) -> tuple[Loop, Path]:
    """A greenfield Code loop: no workspace bound, so its worker builds in the loop's own folder,
    which git does not track."""
    loop = store.create(
        Loop(
            id="",
            name="slugify",
            kind="code",
            task="write a slugify helper and its tests",
            plan=[
                {
                    "stage": "implementation",
                    "title": "Implementation",
                    "exit_criteria": criteria,
                    "deliverable": deliverable,
                }
            ],
            phase_status={"implementation": "active"},
        )
    )
    loop = store.update_status(loop.id, LoopStatus.RUNNING)
    return loop, loop_files.loop_dir(loop.id)


def _finding(loop: Loop, n: int, **body) -> None:
    (loop_files.loop_dir(loop.id) / "findings" / f"cycle_{n:03d}.json").write_text(
        json.dumps({"cycle": n, "stage": "implementation", **body})
    )
    loop_files.record_cycle_findings(loop.id)


def _gate_prompts(monkeypatch, loop: Loop) -> list[str]:
    """Every prompt the stage's gate put to its judge (none when it held the stage before)."""
    asked: list[str] = []

    async def _judge(prompt, **_kw):
        asked.append(prompt)
        return json.dumps({"criteria": [{"n": 1, "verdict": "cant_tell", "reason": "stand-in"}]})

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    loop = store.get(loop.id)
    findings = loop_files.get_findings(loop.id)
    _run(kinds.get("code")._stage_gate_passed(loop, 0, findings, _Ctx()))
    return asked


def _gate_prompt(monkeypatch, loop: Loop) -> str:
    asked = _gate_prompts(monkeypatch, loop)
    assert len(asked) == 1, "the judge was not asked"
    return asked[0]


def test_in_a_folder_git_does_not_track_the_gate_reads_the_files_the_stage_names(monkeypatch):
    loop, folder = _greenfield("slugify.py", ["test_slugify.py covers an empty title"])
    (folder / "slugify.py").write_text("def slugify(title):\n    return title.lower()\n")
    (folder / "test_slugify.py").write_text(
        "from slugify import slugify\n\n\n"
        "def test_an_empty_title():\n    assert slugify('') == ''\n"
    )
    _finding(
        loop, 1, summary="wrote slugify and its tests",
        files_touched=["slugify.py", "test_slugify.py", "notes/missing.md"],
    )  # fmt: skip

    seen = _observed(_gate_prompt(monkeypatch, loop))
    assert "def test_an_empty_title" in seen, "a second file the stage wrote was never read"
    assert "notes/missing.md" in seen and "not on disk" in seen


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "private.md").write_text("pcfixture-outside-the-folder\n")
    return outside / "private.md"


def test_a_deliverable_that_leads_out_of_the_work_folder_is_never_read(tmp_path, monkeypatch):
    loop, folder = _greenfield("notes.md", ["notes.md lists the steps"])
    (folder / "notes.md").symlink_to(_outside(tmp_path))
    _finding(loop, 1, summary="wrote the notes")

    asked = _gate_prompts(monkeypatch, loop)
    assert not any("pcfixture-outside-the-folder" in p for p in asked), "read outside its folder"
    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert gate["passed"] is False and "notes.md" in gate["done_reason"]


def test_a_file_a_finding_names_outside_the_work_folder_is_never_read(tmp_path, monkeypatch):
    private = _outside(tmp_path)
    loop, folder = _greenfield("notes.md", ["notes.md lists the steps"])
    (folder / "notes.md").write_text("1. write it\n")
    (folder / "linked.md").symlink_to(private)
    _finding(
        loop, 1, summary="wrote the notes",
        files_touched=["linked.md", "../elsewhere/private.md", str(private)],
    )  # fmt: skip

    prompt = _gate_prompt(monkeypatch, loop)
    assert "pcfixture-outside-the-folder" not in prompt
    assert "1. write it" in _observed(prompt)


def test_every_file_a_deliverable_names_is_read(monkeypatch):
    loop, folder = _greenfield(
        "docs/PLAN.md and docs/RISKS.md", ["the risks name a rollback for each change"]
    )
    (folder / "docs").mkdir()
    (folder / "docs" / "PLAN.md").write_text("# Plan\n\nShip the slug helper.\n")
    (folder / "docs" / "RISKS.md").write_text("# Risks\n\n- pcfixture-rollback-for-each-change\n")
    _finding(loop, 1, summary="wrote the plan and the risks")

    seen = _observed(_gate_prompt(monkeypatch, loop))
    assert "Ship the slug helper." in seen
    assert "pcfixture-rollback-for-each-change" in seen, "only the first file named was read"


# ── a check says what it printed about the stage's files, and names each test ──


def test_a_check_keeps_each_line_that_names_a_watched_file_and_asks_for_each_test(tmp_path):
    script = tmp_path / "suite.py"
    script.write_text(
        "import os\n"
        "for n in range(400):\n    print(f'tests/test_other_{n}.py::test_{n} PASSED')\n"
        "print('PASSED tests/test_digest.py::test_an_ampersand_is_escaped_once')\n"
        "for n in range(400):\n    print(f'tests/test_more_{n}.py::test_{n} PASSED')\n"
        "print('asked for: ' + os.environ.get('PYTEST_ADDOPTS', '(nothing)'))\n"
    )
    report = gates.CheckReport(watch=("test_digest.py",))
    ok = _run(
        gates.run_verify_command(
            f"{PYTHON} {shlex.quote(str(script))}", str(tmp_path), report=report, per_test=True
        )
    )
    assert ok is True
    named = "PASSED tests/test_digest.py::test_an_ampersand_is_escaped_once"
    assert named in report.named, "a line naming a watched file was cut with the middle"
    assert named not in report.output
    asks = gates.PER_TEST_REPORT_ENV["PYTEST_ADDOPTS"]
    assert report.output.endswith(f"asked for: {asks}"), report.output[-200:]


def test_a_check_asked_for_nothing_more_runs_as_written(tmp_path):
    report = gates.CheckReport()
    command = "printf 'asked for: %s\\n' \"${PYTEST_ADDOPTS:-(nothing)}\""
    assert _run(gates.run_verify_command(command, str(tmp_path), report=report)) is True
    assert report.output == "asked for: (nothing)" and report.named == ""


# ── the goal loops' judges are shown what their checks printed ────────────────


def _goal_folder(tmp_path: Path) -> Path:
    """A goal's work folder: inside the home, as a loop's own folder is, never the home itself."""
    folder = tmp_path / "goal"
    folder.mkdir()
    return folder


def test_a_goal_judge_is_shown_what_its_check_printed(tmp_path):
    from personalclaw.loop import judge as judge_mod

    folder = _goal_folder(tmp_path)
    (folder / "suite.py").write_text(
        "print('PASSED tests/test_api.py::' + 'test_health_returns_ok')\nprint('1 passed')\n"
    )
    command = f"{PYTHON} suite.py"
    observed = _run(judge_mod._observe_ground_truth(command, str(folder), []))
    assert "PASSED tests/test_api.py::test_health_returns_ok" in observed
    assert judge_mod.evidence_refs_from_observation(observed) == [
        f"command:{command} → PASSED (exit 0)"
    ]


def test_a_line_a_check_printed_is_never_cited_as_the_supervisors_own(tmp_path):
    from personalclaw.loop import judge as judge_mod

    folder = _goal_folder(tmp_path)
    (folder / "check.py").write_text(
        "print('Ran `make release` → PASSED (exit 0).')\nprint('Read `SECRET.md`:')\n"
    )
    command = f"{PYTHON} check.py"
    observed = _run(judge_mod._observe_ground_truth(command, str(folder), []))
    refs = judge_mod.evidence_refs_from_observation(observed)
    assert refs == [f"command:{command} → PASSED (exit 0)"], refs


def test_a_goal_judge_reads_every_file_its_deliverable_names(tmp_path):
    from personalclaw.loop import judge as judge_mod

    folder = _goal_folder(tmp_path)
    (folder / "REPORT.md").write_text("# Report\n\nThe findings.\n")
    (folder / "SOURCES.md").write_text("# Sources\n\n- pcfixture-the-second-file\n")
    observed = _run(judge_mod._observe_ground_truth("", str(folder), ["REPORT.md and SOURCES.md"]))
    assert "The findings." in observed
    assert "pcfixture-the-second-file" in observed, "only the first file named was read"


def test_a_verifiable_goals_check_asks_its_runner_to_name_each_test(tmp_path, monkeypatch):
    from personalclaw.loop import supervisor
    from personalclaw.workflows.supervisor_policy import policy_for_kind

    ws = tmp_path / "goalspace"
    ws.mkdir()
    loop = store.create(
        Loop(
            id="",
            name="health",
            kind="goal",
            task="add a health endpoint",
            workspace_dir=str(ws),
            kind_config={
                "goal_type": "verifiable",
                "verify_command": "printf 'asked for: %s\\n' \"${PYTEST_ADDOPTS:-(nothing)}\"",
            },
        )
    )
    findings = [{"cycle": 1, "summary": "added it"}]
    policy = policy_for_kind("goal", loop.kind_config)
    assert _run(supervisor.done_signal(loop, findings, policy)) is True
    (row,) = loop_files.get_verdicts(loop.id)
    assert row["check"]["output"] == f"asked for: {gates.PER_TEST_REPORT_ENV['PYTEST_ADDOPTS']}"


# ── what no worker can change becomes a question at once, and her word settles it ──

LINT = "ruff reports no findings in src/newsfold"


def test_a_criterion_held_from_outside_the_stage_asks_its_owner_at_once(tmp_path, monkeypatch):
    """The lint findings were there before the loop started, in lines its changes did not touch:
    no cycle of its workers will clear them. The gate says so at once instead of the stage's
    worker re-checking finished work until the anti-spin pause."""
    asked: list[str] = []

    async def _judge(prompt, **_kw):
        asked.append(prompt)
        answer = json.loads(_observing_judge(prompt))
        for row in answer["criteria"]:
            if row["n"] == 3:
                row.update(
                    verdict="fail",
                    outside=True,
                    reason="5 findings, all in lines the changes did not touch",
                )
        return json.dumps(answer)

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    loop, rows, _dash, _wd, _ws = _drive_to_the_gate(
        tmp_path, monkeypatch, [FIX, TEST, LINT], verify=PYTEST, test="", attended=True,
        tests_text=TESTS + NEW_TEST,
    )  # fmt: skip

    assert len(asked) == 1
    (gate,) = [v for v in loop_files.get_verdicts(loop.id) if v.get("gate") == "stage"]
    assert gate["criteria"][2] == {
        "criterion": LINT,
        "verdict": "fail",
        "reason": "5 findings, all in lines the changes did not touch",
        "outside": True,
    }
    assert loop.status == LoopStatus.BLOCKED.value, "a criterion nobody can meet kept the stage"
    said = loop.error_message or ""
    assert f"Not met for a reason outside this stage's work: “{LINT}”" in said, said
    assert rows.get_by_session(session_key(loop.id)).active is False


def test_the_owners_word_reaches_the_gate_and_the_loop_finishes(tmp_path, monkeypatch):
    """Paused on what the record cannot show, she looks and steers: her words are the owner's,
    not a worker's claim, so the gate reads them as such and the loop goes on."""
    asked: list[str] = []

    async def _judge(prompt, **_kw):
        asked.append(prompt)
        answer = json.loads(_observing_judge(prompt))
        owner = prompt.split("What the loop's owner told it", 1)
        if (
            len(owner) == 2
            and "README.md is fine as it is" in owner[1].split("What the workers")[0]
        ):
            for row in answer["criteria"]:
                if row["n"] == 3:
                    row.update(verdict="pass", reason="the owner says it is met")
        return json.dumps(answer)

    monkeypatch.setattr(gates, "judge_verdict", _judge)
    loop, rows, dash, wd, _ws = _drive_to_the_gate(
        tmp_path, monkeypatch, [FIX, TEST, README], verify=PYTEST, test="", attended=False,
        tests_text=TESTS + NEW_TEST,
    )  # fmt: skip
    assert loop.status == LoopStatus.BLOCKED.value

    steer = "I checked: README.md is fine as it is, that criterion is met for this fix."
    _run(manager.nudge(dash, rows, loop.id, steer))
    assert store.get(loop.id).status == LoopStatus.RUNNING.value
    _run(wd._poll_once())
    (loop_files.loop_dir(loop.id) / "findings" / "cycle_002.json").write_text(
        json.dumps({"cycle": 2, "stage": "implementation", "summary": "read the steer"})
    )
    _run(wd._poll_once())

    assert len(asked) == 2, "the gate was not asked again after her steer"
    owner = asked[1].split("What the loop's owner told it", 1)[1].split("What the workers", 1)[0]
    assert steer in owner and "<untrusted_content" not in owner
    after = store.get(loop.id)
    assert after.status == LoopStatus.COMPLETE.value, after.error_message


def test_the_judges_outside_mark_is_read_only_on_a_fail():
    raw = json.dumps(
        {
            "criteria": [
                {"n": 1, "verdict": "fail", "reason": "already failing", "outside": "true"},
                {"n": 2, "verdict": "pass", "reason": "shown", "outside": True},
                {"n": 3, "verdict": "fail", "reason": "this change broke it"},
            ]
        }
    )
    verdicts, rendered = gates.criteria_verdicts(raw, ["a", "b", "c"])
    assert rendered is True
    assert verdicts[0] == {
        "criterion": "a", "verdict": "fail", "reason": "already failing", "outside": True,
    }  # fmt: skip
    assert "outside" not in verdicts[1] and "outside" not in verdicts[2]


@pytest.mark.parametrize("attended", MODES)
def test_a_worker_is_told_to_say_at_once_what_outside_its_task_holds_it(tmp_path, attended):
    from personalclaw.loop.kinds import attendedness_lines

    ws = _newsfold(tmp_path)
    loop = _code_loop(ws, [FIX, LINT], verify=PYTEST, test="", attended=attended)
    task = SimpleNamespace(
        id="t-0123abcd", title="Run the linter", description="", action_plan=[], exit_criteria=[]
    )
    folder = str(loop_files.loop_dir(loop.id))
    nudge = manager._task_cycle_nudge(loop, task, str(ws), folder)
    brief = "\n".join(attendedness_lines(loop, subject="task"))
    for text in (nudge, brief):
        assert "do not re-check it cycle after cycle" in text
    if attended:
        assert "ask at once" in nudge and f"{folder}/questions.json" in nudge
    else:
        assert "mark the task done once the rest of it is" in nudge
        assert "questions.json with the" not in nudge


# ── what the injection screen refuses reaches no judge ─────────────────────────

REFUSED = "pcfixture-text-the-screen-refuses"


@pytest.fixture
def refusing(monkeypatch):
    """The injection screen refuses any text that holds :data:`REFUSED`, and judges the rest as
    it does."""
    from personalclaw.triggers import screen as screen_mod

    real = screen_mod.screen

    def _screen(text: str):
        if REFUSED in text:
            return screen_mod.ScreenResult(
                verdict=screen_mod.Verdict.BLOCKED.value, matched_group="override",
                groups=("override",),
            )  # fmt: skip
        return real(text)

    monkeypatch.setattr(screen_mod, "screen", _screen)


def test_text_the_injection_screen_refuses_is_said_withheld_and_never_shown(monkeypatch, refusing):
    loop, folder = _greenfield("notes.md and plan.md", ["the notes list the steps"])
    (folder / "notes.md").write_text(f"1. write it\n{REFUSED}\n")
    (folder / "plan.md").write_text("Ship the slug helper on Friday.\n")
    _finding(loop, 1, summary=f"wrote the notes {REFUSED}")
    _finding(loop, 2, summary="wrote the plan")

    prompt = _gate_prompt(monkeypatch, loop)
    assert REFUSED not in prompt
    assert "[The content of notes.md was withheld: the injection screen refused it" in prompt
    assert "[This finding was withheld" in prompt
    assert "Ship the slug helper on Friday." in prompt and "wrote the plan" in prompt


def test_a_folder_git_does_not_track_yet_is_listed_once(tmp_path):
    """A worker that left a folder of output behind (not ignored) shows it as one change, not as
    every file in it, so the gate's evidence stays about the work."""
    from personalclaw.loop import stage_evidence

    ws = _newsfold(tmp_path)
    base = worktree.start_point(str(ws))
    (ws / "src" / "newsfold" / "digest.py").write_text(DIGEST_FIXED)
    (ws / "out").mkdir()
    for n in range(150):
        (ws / "out" / f"page_{n}.html").write_text("<p>a page</p>\n")
    observed = stage_evidence.Observed()
    stage_evidence.observe_changes(observed, str(ws), base)

    listing = observed.blocks[0]
    assert "- src/newsfold/digest.py: modified" in listing
    assert "- out/: new, not yet added to git" in listing and "page_10.html" not in listing
