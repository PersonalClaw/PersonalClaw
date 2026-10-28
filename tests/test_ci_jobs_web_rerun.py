"""``run-job``'s diagnostic rerun for the web runners (tools/ci/jobs.py): a failed Vitest test is
run once more, alone, in a new process; a Playwright test that its own one retry passed is run
once more, alone (one worker, no retry); and the job stays red whatever either shows.

The runners are stand-ins on the job's ``PATH``: an ``npm`` that answers ``npm run test:web`` as
Vitest does under CI (a JUnit report at ``reports/junit-web-unit.xml``, exit 1 on a failure), and
an ``npx`` that answers ``npx vitest run`` and ``npx playwright test``. Each records its argv, its
environment and the tests it ran, so what the rerun asked for is read, not inferred. The planted
suite is a file the stand-ins read: each test's result on each of its calls, so a test that fails
on its first call and passes on its second is real. The rerun's ``--testNamePattern`` is applied
as Vitest applies it, a regular expression searched in each test's full name, and a test of a
named file that it leaves out is reported skipped, as Vitest reports it. A fault the plant names
(another exit, a hang, a signal, a report that is missing, stale or malformed) is played by the
call it names: the step, the rerun, or Playwright.

Security-marked: the rerun must never turn a red green.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

from tools.ci import jobs, run, verdict
from tools.ci.catalogue import Catalogue, Env, Job, Step

pytestmark = pytest.mark.security

FLAKY_NAME = "panel > fails once, then passes"
#: Each planted Vitest test: its file under web/, its full name, and its result on each call (the
#: last one again once they are spent).
VITEST_CASES = (
    ("src/flaky.test.ts", FLAKY_NAME, ["failed", "passed"]),
    # The failed test's own title in another suite of its file, passing: the rerun of the failed
    # test is filtered to its full name, so this one never runs again.
    ("src/flaky.test.ts", "toolbar > fails once, then passes", ["passed"]),
    ("src/broken.test.ts", "broken > fails every time", ["failed"]),
    ("src/broken.test.ts", "broken > passes", ["passed"]),
    ("src/slow.test.ts", "slow > times out every time", ["timeout"]),
)

FLAKY = f"web/src/flaky.test.ts::{FLAKY_NAME}"
BROKEN = "web/src/broken.test.ts::broken > fails every time"
SLOW = "web/src/slow.test.ts::slow > times out every time"

_FAKE = r'''#!__PYTHON__
"""A stand-in for __TOOL__: it plays the planted suite, and logs each call."""
import json, os, re, signal, sys, time
from pathlib import Path
from xml.sax.saxutils import quoteattr

STATE = Path(__STATE__)
ROOT = Path(__ROOT__)
PLANT = json.loads((STATE / "plant.json").read_text())
BODY = {
    "passed": "",
    "failed": "<failure message='expected true'/>",
    "timeout": "<failure message='Test timed out in 20000ms.'/>",
    "skipped": "<skipped/>",
}
args = sys.argv[1:]
ran = []


def outcome(test, results):
    counts = STATE / "counts.json"
    seen = json.loads(counts.read_text()) if counts.exists() else {}
    n = seen.get(test, 0)
    seen[test] = n + 1
    counts.write_text(json.dumps(seen))
    return results[min(n, len(results) - 1)]


def junit(files=(), pattern=None):
    rows, failed = [], False
    for file, name, results in PLANT["vitest"]:
        if files and file not in files:
            continue
        if pattern is not None and re.search(pattern, name) is None:
            got = "skipped"
        else:
            got = outcome(f"{file}::{name}", results)
            if got != "skipped":
                ran.append(name)
        failed = failed or got in ("failed", "timeout")
        attrs = f"classname={quoteattr(file)} name={quoteattr(name)}"
        rows.append(f"<testcase {attrs}>{BODY[got]}</testcase>")
    body = "".join(rows)
    return f"<testsuites><testsuite name='vitest'>{body}</testsuite></testsuites>", failed


def write(path, text, fault):
    text = fault.get("report", text)
    if text is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if fault.get("old"):
        old = time.time() - 3600
        os.utime(path, (old, old))


def finish(fault, failed):
    with (STATE / "calls.jsonl").open("a") as f:
        f.write(json.dumps({"tool": "__TOOL__", "argv": args, "cwd": os.getcwd(), "ran": ran,
            "workers": os.environ.get("VITEST_MAX_WORKERS"), "ci": os.environ.get("CI"),
            "json": os.environ.get("PLAYWRIGHT_JSON_OUTPUT_NAME"),
            "blob": os.environ.get("PLAYWRIGHT_BLOB_OUTPUT_DIR")}) + "\n")
    if fault.get("signal"):
        os.kill(os.getppid(), signal.SIGINT)
    if "on_term" in fault:
        # Stopped, it ends on this exit, as a runner that catches SIGTERM does.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(fault["on_term"]))
    if fault.get("sleep"):
        time.sleep(fault["sleep"])
    sys.exit(fault.get("exit", 1 if failed else 0))


faults = PLANT["faults"]
if args[:2] == ["run", "test:web"]:
    # Where Vitest's config writes it, from wherever the step runs: the repository's reports/.
    text, failed = junit()
    write(ROOT / "reports" / "junit-web-unit.xml", text, faults.get("step", {}))
    finish(faults.get("step", {}), failed)
if args[:2] == ["vitest", "run"]:
    out = next(a.split("=", 1)[1] for a in args if a.startswith("--outputFile="))
    files = [a for a in args[2:] if not a.startswith("-")]
    pattern = next((a.split("=", 1)[1] for a in args if a.startswith("--testNamePattern=")), None)
    text, failed = junit(files, pattern)
    fault = faults.get("rerun", {})
    write(Path(out), text, fault)
    if fault.get("beside"):
        # As Vitest's config reporter would under CI: the step's own report path, and a file
        # beside it.
        Path("..", "reports", "junit-web-unit.xml").write_text(text)
        Path("..", "reports", "beside.txt").write_text("the rerun's")
    finish(fault, failed)
if args[:2] == ["playwright", "test"] and "--workers=1" in args:
    # The alone rerun: only the tests at the places it names, in the projects it names, each with
    # its planted result alone; chromium brings its `setup` dependency, as Playwright does.
    fault = faults.get("alone", {})
    places = {a for a in args[2:] if not a.startswith("-")}
    projects = {a.split("=", 1)[1] for a in args if a.startswith("--project=")}
    suites, failed = [], False
    if "chromium" in projects:
        setup = {"title": "authenticate", "file": "auth.setup.ts", "line": 1, "tests": [
            {"projectName": "setup", "status": "expected", "results": [{"status": "passed"}]}]}
        suites.append({"title": "auth.setup.ts", "file": "auth.setup.ts", "specs": [setup]})
    for suite in PLANT["playwright"]["suites"] + PLANT.get("beside_in_rerun", []):
        specs = []
        for spec in suite["specs"]:
            beside = suite in PLANT.get("beside_in_rerun", [])
            if not beside and f"e2e/{spec['file']}:{spec.get('line')}" not in places:
                continue
            tests = []
            for t in spec["tests"]:
                if not beside and t["projectName"] not in projects:
                    continue
                got = PLANT.get("alone", {}).get(spec["title"], "expected")
                failed = failed or got == "unexpected"
                result = {"expected": "passed", "unexpected": "failed"}.get(got, got)
                tests.append({"projectName": t["projectName"], "status": got,
                    "results": [{"status": result, "retry": 0}]})
                ran.append(spec["title"])
            if tests:
                specs.append({**spec, "tests": tests})
        if specs:
            suites.append({**suite, "specs": specs})
    write(Path(os.environ["PLAYWRIGHT_JSON_OUTPUT_NAME"]), json.dumps({"suites": suites}), fault)
    if fault.get("beside"):
        # As a config reporter would: the step's own report path, from web/.
        Path("e2e-report.json").write_text("the rerun's")
    finish(fault, failed)
if args[:2] == ["playwright", "test"]:
    fault = faults.get("playwright", {})
    write(Path(os.environ["PLAYWRIGHT_JSON_OUTPUT_NAME"]), json.dumps(PLANT["playwright"]), fault)
    finish(fault, True)
sys.exit(f"__TOOL__: not planted: {args}")
'''


def _playwright_report(*tests: tuple) -> dict:
    """A Playwright JSON report: each test's file, title, status and its results' statuses, and
    its project (``chromium`` unless a fifth item names another). Each spec is on its own line,
    numbered from 1 in the order given."""
    suites = []
    for line, (file, title, status, results, *project) in enumerate(tests, 1):
        suites.append(
            {
                "title": file,
                "file": file,
                "specs": [
                    {
                        "title": title,
                        "file": file,
                        "line": line,
                        "tests": [
                            {
                                "projectName": project[0] if project else "chromium",
                                "status": status,
                                "results": [
                                    {"status": r, "retry": n} for n, r in enumerate(results)
                                ],
                            }
                        ],
                    }
                ],
                "suites": [],
            }
        )
    return {"suites": suites}


def _xml(*cases: tuple[str, str, str]) -> str:
    """A JUnit report as Vitest writes it: each case's classname, name and result."""
    body = {"passed": "", "failed": "<failure message='x'/>", "skipped": "<skipped/>"}
    rows = "".join(
        f"<testcase classname={quoteattr(file)} name={quoteattr(name)}>{body[got]}</testcase>"
        for file, name, got in cases
    )
    return f"<testsuites><testsuite name='vitest'>{rows}</testsuite></testsuites>"


def _git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True)


def _plant(
    tmp_path: Path,
    vitest=VITEST_CASES,
    playwright: dict | None = None,
    faults: dict | None = None,
    alone: dict | None = None,
    beside_in_rerun: list | None = None,
) -> tuple[Path, str]:
    """A repository with a ``web/`` folder holding the planted test files (and one test file
    outside it, at the root), and a ``bin/`` of stand-in runners that play *vitest* and
    *playwright* with *faults*: *alone* gives each Playwright test's status, by its title, when
    the alone rerun runs it (``expected`` unless named), and *beside_in_rerun* the suites that
    rerun reports beside the tests it was asked for. Returns the root and the PATH."""
    root = tmp_path / "repo"
    (root / "web" / "e2e").mkdir(parents=True)
    for file, _, _ in (*VITEST_CASES, *vitest):
        if file.startswith("src/"):
            (root / "web" / file).parent.mkdir(parents=True, exist_ok=True)
            (root / "web" / file).write_text("// planted\n")
    (root / "web" / "e2e" / "a.spec.ts").write_text("// planted\n")
    (root / "outside.test.ts").write_text("// planted outside web/\n")
    (root / ".gitignore").write_text("reports/\n/.local/\nweb/e2e-report.json\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    plant = {
        "vitest": vitest,
        "playwright": playwright or {"suites": []},
        "faults": faults or {},
        "alone": alone or {},
        "beside_in_rerun": beside_in_rerun or [],
    }
    (state / "plant.json").write_text(json.dumps(plant))
    for tool in ("npm", "npx"):
        script = bin_dir / tool
        script.write_text(
            _FAKE.replace("__PYTHON__", sys.executable)
            .replace("__TOOL__", tool)
            .replace("__STATE__", repr(str(state)))
            .replace("__ROOT__", repr(str(root)))
        )
        script.chmod(0o755)
    return root, f"{bin_dir}{os.pathsep}{os.environ.get('PATH', os.defpath)}"


def _calls(tmp_path: Path) -> list[dict]:
    log = tmp_path / "state" / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _vitest_calls(tmp_path: Path) -> list[dict]:
    return [c for c in _calls(tmp_path) if c["argv"][:2] == ["vitest", "run"]]


def _vitest_job(**kw) -> Job:
    fields = dict(
        id="web-unit",
        tiers=("change",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="ordinary",
        timeout_min=5,
        workers="auto",
        workers_env="VITEST_MAX_WORKERS",
        command=(Step(jobs.VITEST_STEP),),
        junit=(jobs.VITEST_JUNIT,),
    )
    fields.update(kw)
    return Job(**fields)  # type: ignore[arg-type]


def _playwright_job() -> Job:
    step = Step(
        ("npx", "playwright", "test", "--grep-invert", "@visual"),
        workdir="web",
        env=Env({"PLAYWRIGHT_JSON_OUTPUT_NAME": "e2e-report.json"}),
    )
    return Job(  # type: ignore[call-arg]
        id="e2e",
        tiers=("merge",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="ordinary",
        timeout_min=5,
        command=(step,),
    )


def _run(
    root: Path, path: str, job: Job, tmp_path: Path, options: jobs.Options | None = None
) -> jobs.Outcome:
    host = jobs.Host(root=root, family="ubuntu-", path=path)
    options = options or jobs.Options()
    return jobs.run_one(Catalogue((job,)), job, None, host, options, tmp_path / "work")


def _record(outcome: jobs.Outcome) -> dict:
    assert outcome.record is not None, outcome.reasons
    return json.loads(outcome.record.read_text())


def _by_test(record: dict) -> dict[str, dict]:
    assert record["rerun"] is not None, record
    return {e["test"]: e for e in record["rerun"]["tests"]}


def _marks(root: Path) -> dict[str, str | None]:
    """Each case of the job's own Vitest report, and its ``rerun_result`` property."""
    props = {}
    for case in ET.parse(root / jobs.VITEST_JUNIT).getroot().iter("testcase"):
        values = {p.get("name"): p.get("value") for p in case.iter("property")}
        props[case.get("name")] = values.get(jobs.RERUN_PROPERTY)
    return props


def _red_with_no_rerun(outcome: jobs.Outcome, tmp_path: Path) -> None:
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE and record["rerun"] is None, record
    assert _vitest_calls(tmp_path) == []


# ── Vitest: a failed test is rerun once, alone, and the job stays red ──────────────────────────


def test_a_vitest_failure_is_rerun_once_alone_and_the_job_stays_red(tmp_path):
    root, path = _plant(tmp_path)
    outcome = _run(root, path, _vitest_job(), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert outcome.reasons == ["step 1: the job exited 1"], outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    rerun = _by_test(record)
    assert {t: e["rerun_result"] for t, e in rerun.items()} == {
        FLAKY: verdict.FLAKE,
        BROKEN: verdict.REPEATED_FAILURE,
        SLOW: verdict.REPEATED_FAILURE,
    }
    assert rerun[FLAKY]["why"] == "passed rerun alone in a new process"
    [first, again] = _calls(tmp_path)
    assert first["argv"] == ["run", "test:web"]
    # One rerun, in the web folder, on one worker, of the failed tests' files only, filtered to
    # the failed tests' full names: nothing that passed runs again, the same title in another
    # suite included.
    assert again["tool"] == "npx" and again["argv"][:2] == ["vitest", "run"]
    assert Path(again["cwd"]).resolve() == (root / "web").resolve()
    assert "--maxWorkers=1" in again["argv"] and again["workers"] == "1"
    files = sorted(a for a in again["argv"][2:] if not a.startswith("-"))
    assert files == ["src/broken.test.ts", "src/flaky.test.ts", "src/slow.test.ts"]
    assert again["ran"] == [FLAKY_NAME, "broken > fails every time", "slow > times out every time"]
    # The first run's per-test budget stands: nothing in the rerun changes a time or a retry.
    assert not any(
        a.startswith(("--testTimeout", "--hookTimeout", "--retry", "--repeats"))
        for a in again["argv"]
    )
    assert record["rerun"]["argv"][:3] == ["npx", "vitest", "run"]


def test_a_vitest_steps_failed_tests_let_the_jobs_next_steps_run(tmp_path):
    """As web-unit's design detector and its desktop and mobile tests run after its Vitest step:
    a flake there leaves the job red, and every step after it still runs."""
    after = tmp_path / "after"
    root, path = _plant(tmp_path, vitest=(VITEST_CASES[0], VITEST_CASES[1]))
    later = Step((sys.executable, "-c", f"open({str(after)!r}, 'w').close()"))
    job = _vitest_job()
    outcome = _run(root, path, dataclasses.replace(job, command=(*job.command, later)), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert after.exists()
    record = _record(outcome)
    assert [s["exit"] for s in record["steps"]] == [1, 0]
    assert _by_test(record)[FLAKY]["rerun_result"] == verdict.FLAKE


def test_a_vitest_job_whose_only_failure_is_a_flake_stays_red(tmp_path):
    root, path = _plant(tmp_path, vitest=(VITEST_CASES[0], VITEST_CASES[1]))
    outcome = _run(root, path, _vitest_job(), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    assert record["rerun"]["tests"] == [
        {"test": FLAKY, "rerun_result": verdict.FLAKE, "why": "passed rerun alone in a new process"}
    ]
    [read] = verdict.from_job_records([outcome.record.read_text()])
    assert read.result == verdict.FAILURE


def test_the_vitest_report_carries_each_classification_and_is_the_steps_own(tmp_path):
    root, path = _plant(tmp_path)
    _run(root, path, _vitest_job(), tmp_path)
    assert _marks(root) == {
        FLAKY_NAME: verdict.FLAKE,
        "toolbar > fails once, then passes": None,
        "broken > fails every time": verdict.REPEATED_FAILURE,
        "broken > passes": None,
        "slow > times out every time": verdict.REPEATED_FAILURE,
    }


def test_the_vitest_rerun_says_each_classification_as_a_warning(tmp_path, capsys):
    root, path = _plant(tmp_path)
    _run(root, path, _vitest_job(), tmp_path)
    out = capsys.readouterr().out
    assert f"::warning title=flake::{FLAKY} (passed rerun alone in a new process)" in out
    assert f"::warning title=repeated failure::{BROKEN} (failed again, alone)" in out


def test_a_passing_vitest_job_runs_no_rerun(tmp_path):
    root, path = _plant(tmp_path, vitest=(VITEST_CASES[1], VITEST_CASES[3]))
    outcome = _run(root, path, _vitest_job(), tmp_path)
    assert outcome.code == run.OK, outcome.reasons
    assert _record(outcome)["rerun"] is None
    assert [c["tool"] for c in _calls(tmp_path)] == ["npm"]


TRICKY = r"s > renders (empty) [a] $1 a+b? {x} | y / z \ w ^ *."


def test_a_vitest_title_with_regular_expression_characters_is_rerun_as_itself(tmp_path):
    """The rerun's name filter matches each failed name literally: a title full of regular
    expression characters still selects itself, and only itself."""
    cases = (
        ("src/flaky.test.ts", TRICKY, ["failed", "passed"]),
        ("src/flaky.test.ts", f"{TRICKY} again", ["passed"]),
        ("src/flaky.test.ts", f"outer > {TRICKY}", ["passed"]),
    )
    root, path = _plant(tmp_path, vitest=cases)
    outcome = _run(root, path, _vitest_job(), tmp_path)
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry == {
        "test": f"web/src/flaky.test.ts::{TRICKY}",
        "rerun_result": verdict.FLAKE,
        "why": "passed rerun alone in a new process",
    }
    [again] = _vitest_calls(tmp_path)
    assert again["ran"] == [TRICKY]


def test_a_vitest_test_failed_twice_in_the_report_is_one_test_rerun_once(tmp_path):
    failed = ("src/flaky.test.ts", FLAKY_NAME, "failed")
    report = _xml(failed, failed)
    root, path = _plant(tmp_path, vitest=(VITEST_CASES[0],), faults={"step": {"report": report}})
    outcome = _run(root, path, _vitest_job(), tmp_path)
    assert [e["test"] for e in _record(outcome)["rerun"]["tests"]] == [FLAKY]
    [again] = _vitest_calls(tmp_path)
    assert again["ran"] == [FLAKY_NAME]


# ── Vitest: which failed case is rerun ─────────────────────────────────────────────────────────


def test_a_vitest_case_whose_file_is_gone_is_not_rerun(tmp_path):
    root, path = _plant(tmp_path)
    (root / "web" / "src" / "slow.test.ts").unlink()
    _git(root, "commit", "-qam", "the slow file is gone")
    outcome = _run(root, path, _vitest_job(), tmp_path)
    rerun = _by_test(_record(outcome))
    assert rerun[SLOW] == {"test": SLOW, "rerun_result": None, "why": jobs.NOT_ONE_FILE}
    [again] = _vitest_calls(tmp_path)
    assert "src/slow.test.ts" not in again["argv"]


@pytest.mark.parametrize(
    ("classname", "name"),
    [
        ("../outside.test.ts", "outside > fails"),
        ("/etc/hosts", "absolute > fails"),
        ("vitest unhandled errors", "Error: boom"),
        ("", FLAKY_NAME),
        ("src/flaky.test.ts", ""),
    ],
)
def test_a_vitest_case_that_names_no_test_file_under_web_is_never_rerun(tmp_path, classname, name):
    root, path = _plant(tmp_path, vitest=((classname, name, ["failed", "passed"]),))
    outcome = _run(root, path, _vitest_job(), tmp_path)
    assert outcome.code == run.RED
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry["rerun_result"] is None and entry["why"] == jobs.NOT_ONE_FILE, entry
    assert entry["test"] == f"web/{classname}::{name}"
    assert _vitest_calls(tmp_path) == []


# ── Vitest: a step the rerun cannot trust ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fault",
    [
        {"exit": 2},
        {"exit": 130},
        {"old": True},
        {"report": None},
        {"report": "not xml <"},
        {"report": ""},
        {"report": "<testsuites><testsuite><testcase"},
        {"report": _xml(("src/flaky.test.ts", FLAKY_NAME, "passed"))},
    ],
    ids=[
        "exit 2",
        "exit 130",
        "a report older than the job",
        "no report",
        "not XML",
        "empty",
        "truncated",
        "no failed case",
    ],
)
def test_a_vitest_step_the_rerun_cannot_trust_is_red_with_no_rerun(tmp_path, fault):
    """Only a step that exits 1, and nothing else, with a fresh report naming a failed test is
    rerun; anything else is red as it stands."""
    root, path = _plant(tmp_path, faults={"step": fault})
    outcome = _run(root, path, _vitest_job(), tmp_path)
    _red_with_no_rerun(outcome, tmp_path)
    assert not any("rerun" in r for r in outcome.reasons), outcome.reasons


@pytest.mark.parametrize(
    ("fault", "said"),
    [
        ({"report": None}, "wrote no junit at reports/junit-web-unit.xml in this run"),
        ({"old": True}, "wrote no junit at reports/junit-web-unit.xml in this run"),
        (
            {"report": "<testsuites><testsuite"},
            "its junit reports/junit-web-unit.xml is unreadable",
        ),
    ],
    ids=["no report", "a report older than the job", "a malformed report"],
)
def test_a_passing_vitest_step_whose_report_cannot_be_read_is_red(tmp_path, fault, said):
    """A Vitest step that exits 0 is green only with its report written in this run and read:
    a missing, stale or malformed one is never read as no failures."""
    root, path = _plant(tmp_path, vitest=(VITEST_CASES[1],), faults={"step": fault})
    outcome = _run(root, path, _vitest_job(), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert any(said in r for r in outcome.reasons), outcome.reasons
    assert _record(outcome)["result"] == verdict.FAILURE
    assert _vitest_calls(tmp_path) == []


def test_a_vitest_step_stopped_by_the_jobs_time_is_red_with_no_rerun(tmp_path):
    root, path = _plant(tmp_path, faults={"step": {"sleep": 60}})
    outcome = _run(root, path, _vitest_job(), tmp_path, jobs.Options(deadline_s=3, grace=0.5))
    _red_with_no_rerun(outcome, tmp_path)
    assert outcome.reasons[0].startswith("step 1: the job timed out after"), outcome.reasons


def test_a_vitest_step_run_from_another_folder_is_not_rerun(tmp_path):
    root, path = _plant(tmp_path)
    job = _vitest_job(command=(Step(jobs.VITEST_STEP, workdir="web"),), junit=())
    outcome = _run(root, path, job, tmp_path)
    _red_with_no_rerun(outcome, tmp_path)


def test_no_vitest_rerun_starts_once_the_jobs_time_is_spent(tmp_path):
    root, path = _plant(tmp_path)
    report = root / jobs.VITEST_JUNIT
    report.parent.mkdir(parents=True)
    report.write_text(_xml(("src/flaky.test.ts", FLAKY_NAME, "failed")))
    rerun, reasons, stopped = jobs.diagnose_vitest(
        root=root,
        env={},
        host=jobs.Host(root=root, family="ubuntu-", path=path),
        scratch=run.Scratch.create(tmp_path / "scratch"),
        grace=0.5,
        deadline=time.monotonic() - 1,
        started=time.time() - 60,
    )
    assert rerun == {
        "argv": None,
        "exit": None,
        "tests": [
            {"test": FLAKY, "rerun_result": None, "why": "the job's time ran out before the rerun"}
        ],
    }
    assert reasons == [] and stopped is False
    assert _calls(tmp_path) == []


# ── Vitest: a rerun that cannot classify ───────────────────────────────────────────────────────


def _one_rerun(tmp_path: Path, fault: dict, options: jobs.Options | None = None):
    """The flaky test alone, failed in the step, rerun with *fault*: the outcome and its entry."""
    root, path = _plant(tmp_path, vitest=(VITEST_CASES[0],), faults={"rerun": fault})
    outcome = _run(root, path, _vitest_job(), tmp_path, options)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    [entry] = record["rerun"]["tests"]
    assert entry["test"] == FLAKY
    return root, outcome, entry


@pytest.mark.parametrize(
    ("fault", "said", "why"),
    [
        ({"exit": 2}, None, "the rerun reported nothing for it (exit 2)"),
        ({"report": None}, "rerun: it wrote no report", "the rerun reported nothing for it"),
        ({"report": "<testsuites"}, "rerun: its report is unreadable", "reported nothing"),
        ({"report": "<testsuites/>"}, None, "the rerun reported nothing for it (exit 0)"),
        (
            {"report": _xml(("src/flaky.test.ts", FLAKY_NAME, "skipped"))},
            None,
            "skipped on the rerun, so not classified",
        ),
        (
            {"report": _xml(("src/flaky.test.ts", "toolbar > fails once, then passes", "passed"))},
            "which the step's report did not fail, so it classifies nothing",
            "the rerun reported nothing for it",
        ),
        (
            {
                "report": _xml(
                    ("src/flaky.test.ts", FLAKY_NAME, "passed"),
                    ("src/flaky.test.ts", "toolbar > fails once, then passes", "passed"),
                )
            },
            "which the step's report did not fail, so it classifies nothing",
            "the rerun reported nothing for it",
        ),
    ],
    ids=[
        "exit 2",
        "no report",
        "unreadable",
        "no case",
        "skipped",
        "another test only",
        "another test beside it",
    ],
)
def test_a_vitest_rerun_that_cannot_be_read_classifies_nothing(tmp_path, fault, said, why):
    _, outcome, entry = _one_rerun(tmp_path, fault)
    assert entry["rerun_result"] is None and why in entry["why"], entry
    if said is not None:
        assert any(r.startswith("step 1: ") and said in r for r in outcome.reasons), outcome.reasons


def test_a_vitest_rerun_is_stopped_at_the_end_of_the_jobs_time(tmp_path):
    """Stopped, the rerun ends on exit 1 with its report already written, as a runner that
    catches SIGTERM can: its timeout alone keeps that report from classifying anything."""
    began = time.monotonic()
    options = jobs.Options(deadline_s=6, grace=0.5)
    _, outcome, entry = _one_rerun(tmp_path, {"sleep": 120, "on_term": 1}, options)
    assert _record(outcome)["rerun"]["exit"] == 1
    assert time.monotonic() - began < 20
    assert any(r.startswith("step 1: rerun: the job timed out after") for r in outcome.reasons)
    assert entry["rerun_result"] is None and "reported nothing" in entry["why"], entry


def test_a_signal_during_the_vitest_rerun_marks_the_job_interrupted_and_red(tmp_path):
    _, outcome, entry = _one_rerun(
        tmp_path, {"signal": True, "sleep": 30}, jobs.Options(deadline_s=40, grace=0.5)
    )
    assert outcome.interrupted is True, outcome.reasons
    assert "step 1: rerun: interrupted by SIGINT" in outcome.reasons, outcome.reasons
    assert entry["rerun_result"] is None and "reported nothing" in entry["why"], entry


def test_the_vitest_rerun_leaves_the_steps_report_folder_as_the_step_left_it(tmp_path):
    """A rerun that also writes the step's report path and a file beside it (as Vitest's config
    reporter does under CI) leaves the step's own report in place, marked, and what it wrote
    there moves to the job's scratch folder."""
    root, _, entry = _one_rerun(tmp_path, {"beside": True})
    assert entry["rerun_result"] == verdict.FLAKE
    [case] = ET.parse(root / jobs.VITEST_JUNIT).getroot().iter("testcase")
    assert case.find("failure") is not None, "the job's report is the rerun's, not the step's"
    assert _marks(root) == {FLAKY_NAME: verdict.FLAKE}
    assert sorted(p.name for p in (root / "reports").iterdir()) == ["junit-web-unit.xml"]
    moved = sorted(p.read_text() for p in (tmp_path / "work").rglob("beside.txt"))
    assert moved == ["the rerun's"]


def test_a_vitest_report_folder_that_cannot_be_kept_aside_gets_no_rerun(tmp_path, monkeypatch):
    def refuse(folder: Path, into: Path) -> dict:
        raise PermissionError(13, "refused", str(folder))

    monkeypatch.setattr(jobs, "_keep", refuse)
    root, path = _plant(tmp_path, vitest=(VITEST_CASES[0],))
    outcome = _run(root, path, _vitest_job(), tmp_path)
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry["rerun_result"] is None and "could not be kept aside" in entry["why"]
    assert any("could not be kept aside" in r for r in outcome.reasons), outcome.reasons
    assert _vitest_calls(tmp_path) == []


def test_a_vitest_report_folder_that_cannot_be_put_back_is_a_reason(tmp_path, monkeypatch):
    def refuse(*args: object) -> list:
        raise PermissionError(13, "refused")

    monkeypatch.setattr(jobs, "_put_back", refuse)
    _, outcome, entry = _one_rerun(tmp_path, {})
    assert entry["rerun_result"] == verdict.FLAKE
    assert any("could not be put back" in r for r in outcome.reasons), outcome.reasons


# ── Playwright: its own one retry classifies, and the job stays red ────────────────────────────


LOADS = "web/e2e/a.spec.ts::loads [chromium]"


def _playwright_calls(tmp_path: Path) -> list[dict]:
    return [c for c in _calls(tmp_path) if c["argv"][:2] == ["playwright", "test"]]


def test_a_flaky_playwright_test_that_passes_alone_is_a_flake_and_the_job_stays_red(tmp_path):
    """Playwright's own retry runs beside the other workers, so a pass there is not a pass alone:
    the flaky test is run once more, alone, and is a flake only because it passed there. A test
    that failed on its retry too is a repeated failure with no rerun, and one that passed in the
    step is never rerun."""
    report = _playwright_report(
        ("a.spec.ts", "loads", "flaky", ["failed", "passed"]),
        ("a.spec.ts", "breaks", "unexpected", ["failed", "failed"]),
        ("a.spec.ts", "hangs", "unexpected", ["timedOut", "timedOut"]),
        ("a.spec.ts", "works", "expected", ["passed"]),
    )
    root, path = _plant(tmp_path, playwright=report)
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    assert {t: e["rerun_result"] for t, e in _by_test(record).items()} == {
        LOADS: verdict.FLAKE,
        "web/e2e/a.spec.ts::breaks [chromium]": verdict.REPEATED_FAILURE,
        "web/e2e/a.spec.ts::hangs [chromium]": verdict.REPEATED_FAILURE,
    }
    assert _by_test(record)[LOADS]["why"] == "passed rerun alone in a new process"
    step, alone = _playwright_calls(tmp_path)
    assert step["argv"] == ["playwright", "test", "--grep-invert", "@visual"]
    assert alone["argv"][:4] == ["playwright", "test", *verdict.E2E_ALONE]
    assert "--project=chromium" in alone["argv"]
    assert [a for a in alone["argv"][2:] if not a.startswith("-")] == ["e2e/a.spec.ts:1"]
    assert alone["ran"] == ["loads"], "only the flaky test is rerun"
    assert alone["json"] != step["json"] and alone["blob"] is not None
    # The record names the alone rerun, its own argv and its own exit.
    assert record["rerun"]["argv"][:5] == ["npx", "playwright", "test", *verdict.E2E_ALONE]
    assert record["rerun"]["exit"] == 0


def test_a_flaky_playwright_test_that_fails_alone_is_a_repeated_failure(tmp_path):
    report = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"]))
    root, path = _plant(tmp_path, playwright=report, alone={"loads": "unexpected"})
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    record = _record(outcome)
    assert record["rerun"]["tests"] == [
        {"test": LOADS, "rerun_result": verdict.REPEATED_FAILURE, "why": "failed again, alone"}
    ]
    assert record["rerun"]["exit"] == 1


def test_a_green_playwright_step_starts_no_rerun(tmp_path):
    report = _playwright_report(("a.spec.ts", "works", "expected", ["passed"]))
    root, path = _plant(tmp_path, playwright=report, faults={"playwright": {"exit": 0}})
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.OK, outcome.reasons
    assert len(_playwright_calls(tmp_path)) == 1
    assert _record(outcome)["rerun"] is None


def test_more_flaky_playwright_tests_than_a_run_passes_are_none_of_them_rerun(tmp_path):
    report = _playwright_report(
        *((f"{n}.spec.ts", "loads", "flaky", ["failed", "passed"]) for n in "abc")
    )
    root, path = _plant(tmp_path, playwright=report)
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    record = _record(outcome)
    assert len(_playwright_calls(tmp_path)) == 1, "no alone rerun"
    assert record["rerun"]["argv"] is None
    assert {e["rerun_result"] for e in record["rerun"]["tests"]} == {None}
    assert all("one of 3 flaky tests" in e["why"] for e in record["rerun"]["tests"])


@pytest.mark.parametrize("project", ["stateful", "stateful-again-1", "app-ui"])
def test_a_flaky_playwright_test_of_a_chained_or_homed_project_is_not_rerun_alone(
    tmp_path, project
):
    report = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"], project))
    root, path = _plant(tmp_path, playwright=report)
    outcome = _run(root, path, _playwright_job(), tmp_path)
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry["rerun_result"] is None
    assert f"its project, {project}, runs only in its chain or its home" in entry["why"]
    assert len(_playwright_calls(tmp_path)) == 1


@pytest.mark.parametrize(
    ("fault", "said"),
    [
        ({"report": None}, "Playwright wrote no rerun report"),
        ({"report": "{not json"}, "its rerun report is unreadable"),
        ({"exit": 2}, None),
    ],
    ids=["no report", "unreadable", "exit 2"],
)
def test_an_alone_rerun_that_cannot_be_read_classifies_nothing(tmp_path, fault, said):
    report = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"]))
    root, path = _plant(tmp_path, playwright=report, faults={"alone": fault})
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry["rerun_result"] is None, entry
    if said:
        assert any(said in r for r in outcome.reasons), outcome.reasons


def test_an_alone_rerun_that_ran_another_test_classifies_nothing(tmp_path):
    """A test the rerun was not asked for, which ran there (not ``setup``, which chromium brings
    as its dependency), means the rerun is not the one asked for: it classifies nothing."""
    report = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"]))
    other = _playwright_report(("b.spec.ts", "other", "expected", ["passed"]))["suites"]
    root, path = _plant(tmp_path, playwright=report, beside_in_rerun=other)
    outcome = _run(root, path, _playwright_job(), tmp_path)
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry["rerun_result"] is None
    assert any("b.spec.ts::other [chromium]" in r for r in outcome.reasons), outcome.reasons


def test_the_alone_rerun_leaves_the_steps_report_as_the_step_wrote_it(tmp_path):
    report = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"]))
    root, path = _plant(tmp_path, playwright=report, faults={"alone": {"beside": True}})
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert json.loads((root / "web" / "e2e-report.json").read_text()) == report
    assert _by_test(_record(outcome))[LOADS]["rerun_result"] == verdict.FLAKE


def test_no_alone_rerun_starts_once_the_jobs_time_is_spent(tmp_path):
    flaky = {"status": "flaky", "projectName": "chromium", "results": _RETRIED}
    report = _spec(flaky)
    report["suites"][0]["specs"][0]["line"] = 3
    found, _ = _classify(tmp_path, report)
    assert found is not None and found["argv"] is None
    [entry] = found["tests"]
    assert entry["why"] == "the job's time ran out before the rerun"


@pytest.mark.parametrize(
    ("results", "why"),
    [
        (["failed"], "failed with no retry run, so not classified"),
        (["failed", "interrupted"], "its retry ended interrupted, so not classified"),
        (["failed", "skipped"], "its retry ended skipped, so not classified"),
    ],
)
def test_a_playwright_failure_whose_retry_did_not_fail_is_not_classified(tmp_path, results, why):
    report = _playwright_report(("a.spec.ts", "breaks", "unexpected", results))
    root, path = _plant(tmp_path, playwright=report)
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry == {
        "test": "web/e2e/a.spec.ts::breaks [chromium]",
        "rerun_result": None,
        "why": why,
    }


def test_a_red_playwright_run_with_no_failed_test_in_its_report_classifies_nothing(tmp_path):
    report = _playwright_report(("a.spec.ts", "works", "expected", ["passed"]))
    root, path = _plant(tmp_path, playwright=report, faults={"playwright": {"exit": 1}})
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    assert _record(outcome)["rerun"] is None


def test_a_playwright_step_stopped_by_the_jobs_time_is_not_classified(tmp_path):
    report = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"]))
    root, path = _plant(tmp_path, playwright=report, faults={"playwright": {"sleep": 60}})
    outcome = _run(root, path, _playwright_job(), tmp_path, jobs.Options(deadline_s=3, grace=0.5))
    assert outcome.code == run.RED
    assert outcome.reasons[0].startswith("step 1: the job timed out after"), outcome.reasons
    assert _record(outcome)["rerun"] is None


def test_a_playwright_report_that_is_missing_classifies_nothing_and_says_so(tmp_path):
    root, path = _plant(tmp_path, faults={"playwright": {"report": None, "exit": 1}})
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    assert _record(outcome)["rerun"] is None
    assert any(
        "Playwright wrote no e2e-report.json, so nothing is classified" in r
        for r in outcome.reasons
    ), outcome.reasons


def test_a_playwright_report_older_than_the_job_classifies_nothing_and_says_so(tmp_path):
    """Playwright can exit 1 with no report written (its config throws at load): a report an
    earlier run left in web/ is never read as this run's."""
    stale = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"]))
    root, path = _plant(tmp_path, faults={"playwright": {"report": None, "exit": 1}})
    (root / "web" / "e2e-report.json").write_text(json.dumps(stale))
    old = time.time() - 3600
    os.utime(root / "web" / "e2e-report.json", (old, old))
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    assert _record(outcome)["rerun"] is None
    assert any(
        "Playwright wrote no e2e-report.json in this run" in r and "older than the job" in r
        for r in outcome.reasons
    ), outcome.reasons


def test_a_malformed_playwright_report_is_a_reason_and_never_a_crash(tmp_path):
    bad = {"suites": [{"title": "a.spec.ts", "specs": [{"title": "t", "tests": ["flaky"]}]}]}
    root, path = _plant(tmp_path, playwright=bad, faults={"playwright": {"exit": 1}})
    outcome = _run(root, path, _playwright_job(), tmp_path)
    assert outcome.code == run.RED
    assert _record(outcome)["rerun"] is None
    assert any("e2e-report.json is unreadable" in r for r in outcome.reasons), outcome.reasons


def _classify(
    tmp_path: Path, report: object, *, left: float = -1.0
) -> tuple[dict | None, list[str]]:
    """The step's classification from *report*, with *left* seconds of the job's time (none, so
    no rerun starts, unless given)."""
    cwd = tmp_path / "web"
    cwd.mkdir(exist_ok=True)
    text = report if isinstance(report, str) else json.dumps(report)
    (cwd / "e2e-report.json").write_text(text)
    found, reasons, _ = jobs.diagnose_playwright(
        ["npx", "playwright", "test"],
        root=tmp_path,
        cwd=cwd,
        env={jobs.PLAYWRIGHT_REPORT_ENV: "e2e-report.json"},
        host=jobs.Host(root=tmp_path, family="ubuntu-", path=os.defpath),
        scratch=run.Scratch.create(tmp_path / "scratch"),
        grace=1.0,
        deadline=time.monotonic() + left,
        started=time.time() - 60,
    )
    return found, reasons


def _spec(*tests: object) -> dict:
    return {
        "suites": [
            {
                "title": "a.spec.ts",
                "file": "a.spec.ts",
                "specs": [{"title": "t", "file": "a.spec.ts", "tests": list(tests)}],
            }
        ]
    }


_RETRIED = [{"status": "failed"}, {"status": "failed"}]


@pytest.mark.parametrize(
    "report",
    [
        "{not json",
        "",
        '{"suites": ' + "[" * 100_000 + "]" * 100_000 + "}",
        "[]",
        "null",
        {"suites": 5},
        {"suites": [5]},
        {"suites": [{"specs": 5}]},
        {"suites": [{"specs": [5]}]},
        {"suites": [{"specs": [{"tests": 5}]}]},
        {"suites": [{"suites": [{"specs": [{"title": 5, "tests": []}]}]}]},
        _spec(5),
        _spec("flaky"),
        _spec({"status": 5, "projectName": "chromium", "results": _RETRIED}),
        _spec({"status": "unexpected", "projectName": 3, "results": _RETRIED}),
        _spec({"status": "unexpected", "projectName": "chromium", "results": 5}),
        _spec({"status": "unexpected", "projectName": "chromium", "results": "ab"}),
        _spec({"status": "unexpected", "projectName": "chromium", "results": {"a": 1, "b": 2}}),
        _spec({"status": "unexpected", "projectName": "chromium", "results": [1, 2]}),
        _spec({"status": "unexpected", "projectName": "chromium", "results": [{}, {}]}),
    ],
    ids=lambda r: (r if isinstance(r, str) else json.dumps(r))[:70],
)
def test_a_playwright_report_not_of_playwrights_shape_is_unreadable(tmp_path, report):
    found, reasons = _classify(tmp_path, report)
    assert found is None, found
    assert len(reasons) == 1 and reasons[0].startswith("rerun: its e2e-report.json is unreadable")


def test_a_playwright_test_named_twice_is_listed_once(tmp_path):
    test = {"status": "flaky", "projectName": "chromium", "results": _RETRIED}
    found, reasons = _classify(tmp_path, _spec(test, test))
    assert found is not None and reasons == []
    assert [t["test"] for t in found["tests"]] == ["web/e2e/a.spec.ts::t [chromium]"]


# ── A browser leg's failed tests leave it red, and its other homes run on ─────────────────────


def _home(blob: Path) -> Step:
    """A leg's later run on a home of its own, as e2e-shard's idle and stateful runs are: here a
    program that leaves its blob, outside the checkout so the job's record is still written."""
    return Step((sys.executable, "-c", f"open({str(blob)!r}, 'w').write('blob')"))


def _leg(*later: Step) -> Job:
    first = _playwright_job()
    return dataclasses.replace(first, command=(*first.command, *later))


FLAKY_LEG = _playwright_report(("a.spec.ts", "loads", "flaky", ["failed", "passed"]))


def test_a_legs_failed_tests_keep_it_red_and_its_other_homes_still_leave_their_blobs(tmp_path):
    """e2e-shard's first run failed on a flake and its run-job stopped, so its idle home never
    ran, and `e2e` and `ui-quality` each went red merging a leg with no blob. A step whose one
    fault is failed tests its own report names lets the job's other steps run on: the job is
    red, its record holds the one red step and its rerun, and every home leaves its blob."""
    idle, stateful = tmp_path / "idle.zip", tmp_path / "stateful.zip"
    root, path = _plant(tmp_path, playwright=FLAKY_LEG)
    outcome = _run(root, path, _leg(_home(idle), _home(stateful)), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert idle.read_text() == "blob" and stateful.read_text() == "blob"
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    assert [s["exit"] for s in record["steps"]] == [1, 0, 0]
    assert _by_test(record)[LOADS]["rerun_result"] == verdict.FLAKE
    [read] = verdict.from_job_records([outcome.record.read_text()])
    assert read.result == verdict.FAILURE and read.step_exits == (1, 0, 0)


@pytest.mark.parametrize(
    "fault, said",
    [
        pytest.param({"exit": 2}, "step 1: the job exited 2", id="another exit"),
        pytest.param({"signal": True}, "step 1: interrupted by SIGINT", id="a signal"),
        pytest.param(
            {"report": None, "exit": 1},
            "step 1: rerun: ",
            id="an exit 1 whose report names no failed test",
        ),
    ],
)
def test_a_leg_whose_step_failed_for_any_other_fault_runs_no_other_home(tmp_path, fault, said):
    """A job's time spent stops it at the next step anyway, so it is not among these."""
    blob = tmp_path / "idle.zip"
    root, path = _plant(tmp_path, playwright=FLAKY_LEG, faults={"playwright": fault})
    outcome = _run(root, path, _leg(_home(blob)), tmp_path)
    assert outcome.code != run.OK, outcome.reasons
    assert any(r.startswith(said) for r in outcome.reasons), outcome.reasons
    assert not blob.exists(), "a step after a fault that is not failed tests ran"


def test_a_red_step_after_a_legs_failed_tests_keeps_it_red_and_stops_it(tmp_path):
    """The record holds one rerun, the first red step's, and a job with two red steps is never a
    flake, so the second stops the job: the home after it never runs."""
    blob = tmp_path / "stateful.zip"
    root, path = _plant(tmp_path, playwright=FLAKY_LEG)
    red = Step((sys.executable, "-c", "raise SystemExit(1)"))
    outcome = _run(root, path, _leg(red, _home(blob)), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert not blob.exists()
    record = _record(outcome)
    assert [s["exit"] for s in record["steps"]] == [1, 1]
    assert _by_test(record)[LOADS]["rerun_result"] == verdict.FLAKE
    assert any(r.startswith("step 2: the job exited 1") for r in outcome.reasons), outcome.reasons
