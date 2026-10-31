"""``run-job``'s diagnostic rerun (tools/ci/jobs.py): a failed test is run once more, alone, in a
fresh process, only to classify it; the job stays red whatever the rerun shows.

Each case plants a small pytest suite in a git repository in ``tmp_path`` and runs it through
``jobs.run_one``, the step path ``run-job`` takes, so the first run and the rerun are real pytest
processes. Each planted test appends a line to a call log outside the repository, so how many
times it ran is counted, not inferred.

Security-marked: the rerun must never turn a red green, and never run a rail or a security test.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import nested_pytest
import pytest

from tools.ci import jobs, run, verdict
from tools.ci.catalogue import Catalogue, Job, Step

pytestmark = pytest.mark.security

PY = sys.executable
TESTS = Path(__file__).resolve().parent
REPORT = "reports/junit.xml"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout.strip()


def _calls(log: Path) -> str:
    """The lines a planted test adds to its call log: its name, once per call."""
    return f"open({str(log)!r}, 'a').write(__import__('inspect').stack()[0].function + '\\n')\n"


def _suite(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A repository holding the planted tests; its call log; and the flaky test's flag file."""
    root = tmp_path / "repo"
    (root / "tests" / "rails").mkdir(parents=True)
    log, seen = tmp_path / "calls.log", tmp_path / "seen"
    (root / ".gitignore").write_text("reports/\n/.local/\n__pycache__/\n.pytest_cache/\n")
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
        'addopts = ["--timeout=60"]\n'
        'markers = ["security: never rerun"]\n'
    )
    call = _calls(log)
    (root / "tests" / "test_flaky.py").write_text(
        "import os\n\n"
        "def test_fails_then_passes():\n"
        f"    {call}"
        f"    if not os.path.exists({str(seen)!r}):\n"
        f"        open({str(seen)!r}, 'w').close()\n"
        "        raise AssertionError('first call in this machine')\n"
    )
    (root / "tests" / "test_broken.py").write_text(
        "import pytest\n\n"
        "class TestBroken:\n"
        "    @pytest.mark.parametrize('v', ['a b', 'c'])\n"
        "    def test_fails_twice(self, v):\n"
        f"        {call}"
        "        assert v == 'never'\n\n"
        "def test_passes():\n"
        f"    {call}"
    )
    (root / "tests" / "test_guarded.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.security\n"
        "def test_security_fails():\n"
        f"    {call}"
        "    assert False\n"
    )
    (root / "tests" / "rails" / "test_rail.py").write_text(
        f"def test_rail_fails():\n    {call}    assert False\n"
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    return root, log, seen


def _job(*paths: str, **kw) -> Job:
    argv = (
        *nested_pytest.argv(".", python=PY),
        "-p",
        "no:cacheprovider",
        "-q",
        *paths,
        f"--junitxml={REPORT}",
    )
    fields = dict(
        id="suite",
        tiers=("change",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="ordinary",
        timeout_min=5,
        command=(Step(argv),),
        junit=(REPORT,),
    )
    fields.update(kw)
    return Job(**fields)  # type: ignore[arg-type]


def _host(root: Path) -> jobs.Host:
    return jobs.Host(root=root, family="ubuntu-", path=os.environ.get("PATH", os.defpath))


def _run(root: Path, job: Job, tmp_path: Path, options: jobs.Options | None = None) -> jobs.Outcome:
    options = options or jobs.Options()
    return jobs.run_one(Catalogue((job,)), job, None, _host(root), options, tmp_path / "work")


def _commit(root: Path, files: dict[str, str], message: str) -> None:
    """Add *files* (path relative to the repository: text) to the planted repository, committed,
    so the job runs on a clean checkout and leaves its record."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _failed_in_report(root: Path) -> list[str]:
    """The cases the job's own report holds as failed: the rerun's input, had it run."""
    import xml.etree.ElementTree as ET

    return [
        f"{case.get('classname')}::{case.get('name')}"
        for case in ET.parse(root / REPORT).getroot().iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]


def _record(outcome: jobs.Outcome) -> dict:
    assert outcome.record is not None, outcome.reasons
    return json.loads(outcome.record.read_text())


def _by_test(record: dict) -> dict[str, dict]:
    assert record["rerun"] is not None, record
    return {entry["test"]: entry for entry in record["rerun"]["tests"]}


def _junit_property(root: Path, name: str) -> dict[str, str | None]:
    """Each test case of the job's own report, and its ``rerun_result`` property."""
    import xml.etree.ElementTree as ET

    out: dict[str, str | None] = {}
    for case in ET.parse(root / REPORT).getroot().iter("testcase"):
        props = {p.get("name"): p.get("value") for p in case.iter("property")}
        out[f"{case.get('classname')}::{case.get('name')}"] = props.get(name)
    return out


FLAKY = "tests/test_flaky.py::test_fails_then_passes"
TWICE_A = "tests/test_broken.py::TestBroken::test_fails_twice[a b]"
TWICE_C = "tests/test_broken.py::TestBroken::test_fails_twice[c]"
GUARDED = "tests/test_guarded.py::test_security_fails"
RAIL = "tests/rails/test_rail.py::test_rail_fails"


def test_a_test_that_fails_then_passes_is_a_flake_and_the_job_stays_red(tmp_path):
    root, log, _ = _suite(tmp_path)
    outcome = _run(root, _job("tests/test_flaky.py"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    assert _by_test(record)[FLAKY]["rerun_result"] == "flake"
    # Once in the job's run, once alone in the rerun.
    assert log.read_text().splitlines() == ["test_fails_then_passes"] * 2
    [read] = verdict.from_job_records([outcome.record.read_text()])
    assert read.result == verdict.FAILURE
    assert _junit_property(root, "rerun_result") == {
        "tests.test_flaky::test_fails_then_passes": "flake"
    }


def test_a_test_that_fails_twice_is_a_repeated_failure_and_the_job_stays_red(tmp_path):
    root, log, _ = _suite(tmp_path)
    argv = (
        *nested_pytest.argv(".", python=PY),
        "-n",
        "2",
        "--dist",
        "worksteal",
        "-q",
        "tests/test_broken.py",
    )
    outcome = _run(root, _job(command=(Step((*argv, f"--junitxml={REPORT}")),)), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    rerun = _by_test(record)
    # Both cases of the parametrized test, one with a space in its id; never the test that passed.
    assert set(rerun) == {TWICE_A, TWICE_C}
    assert {t: e["rerun_result"] for t, e in rerun.items()} == {
        TWICE_A: "repeated failure",
        TWICE_C: "repeated failure",
    }
    assert sorted(log.read_text().splitlines()) == ["test_fails_twice"] * 4 + ["test_passes"]
    # The step ran two workers; the rerun runs alone, with no cache a later run could read, and
    # names nothing of the step's selection but the two failed ids.
    rerun_argv = record["rerun"]["argv"]
    assert rerun_argv[3:7] == ["-n", "0", "-p", "no:cacheprovider"], rerun_argv
    assert "2" not in rerun_argv and "worksteal" not in rerun_argv, rerun_argv
    assert sorted(rerun_argv[-2:]) == [TWICE_A, TWICE_C], rerun_argv
    assert _junit_property(root, "rerun_result") == {
        "tests.test_broken.TestBroken::test_fails_twice[a b]": "repeated failure",
        "tests.test_broken.TestBroken::test_fails_twice[c]": "repeated failure",
        "tests.test_broken::test_passes": None,
    }


def test_a_security_marked_failure_and_a_rail_are_never_rerun_and_stay_red(tmp_path):
    root, log, _ = _suite(tmp_path)
    job = _job("tests/test_guarded.py", "tests/rails/test_rail.py", "tests/test_flaky.py")
    outcome = _run(root, job, tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    rerun = _by_test(record)
    assert rerun[GUARDED]["rerun_result"] is None
    assert "security" in rerun[GUARDED]["why"]
    assert rerun[RAIL]["rerun_result"] is None
    assert "rail" in rerun[RAIL]["why"]
    # The control beside them: the flaky test in the same job was rerun.
    assert rerun[FLAKY]["rerun_result"] == "flake"
    calls = log.read_text().splitlines()
    assert calls.count("test_security_fails") == 1
    assert calls.count("test_rail_fails") == 1
    assert calls.count("test_fails_then_passes") == 2
    props = _junit_property(root, "rerun_result")
    assert props["tests.test_guarded::test_security_fails"] is None
    assert props["tests.rails.test_rail::test_rail_fails"] is None


def test_a_job_of_only_a_rail_failing_runs_no_rerun_process(tmp_path):
    root, log, _ = _suite(tmp_path)
    outcome = _run(root, _job("tests/rails/test_rail.py"), tmp_path)
    assert outcome.code == run.RED
    record = _record(outcome)
    assert record["rerun"]["argv"] is None and record["rerun"]["exit"] is None
    assert log.read_text().splitlines() == ["test_rail_fails"]


def test_a_passing_job_has_no_rerun(tmp_path):
    root, log, _ = _suite(tmp_path)
    job = _job("tests/test_broken.py::test_passes")
    outcome = _run(root, job, tmp_path)
    assert outcome.code == run.OK, outcome.reasons
    assert _record(outcome)["rerun"] is None
    assert log.read_text().splitlines() == ["test_passes"]


def test_a_step_that_fails_for_another_reason_than_a_failed_test_is_not_rerun(tmp_path):
    """pytest's exit 1 is a failed test; any other failure (a usage error, a missing file, a
    plain program) has no test to diagnose."""
    root, log, _ = _suite(tmp_path)
    rows = [
        ("a usage error", _job("tests/test_flaky.py", "--no-such-option")),
        ("a plain program", _job(command=(Step((PY, "-c", "raise SystemExit(1)")),))),
    ]
    for name, job in rows:
        outcome = _run(root, job, tmp_path / name.replace(" ", "-"))
        assert outcome.code == run.RED, name
        assert _record(outcome)["rerun"] is None, name
    assert not log.exists() or "test_fails_then_passes" not in log.read_text()


def test_the_rerun_says_each_classification_as_a_warning(tmp_path, capsys):
    root, _, _ = _suite(tmp_path)
    _run(root, _job("tests/test_flaky.py", "tests/test_guarded.py"), tmp_path)
    out = capsys.readouterr().out
    assert f"::warning title=flake::{FLAKY}" in out
    assert "never rerun" in out and GUARDED in out


# ── the record's rerun field ──────────────────────────────────────────────────────────────────

HEAD = "a" * 40


def _written(**kw) -> str:
    kw.setdefault("key", "suite")
    kw.setdefault("result", verdict.FAILURE)
    kw.setdefault("identity", verdict.Identity(HEAD))
    kw.setdefault("steps", [(["pytest"], 1, 1.0)])
    return verdict.job_record(**kw)


_GOOD = {
    "argv": ["pytest", "-n", "0"],
    "exit": 0,
    "tests": [{"test": FLAKY, "rerun_result": "flake", "why": "passed alone"}],
}


def test_a_record_carries_the_rerun_and_reads_back_red():
    text = _written(rerun=_GOOD)
    assert json.loads(text)["rerun"] == _GOOD
    [read] = verdict.from_job_records([text])
    assert read.result == verdict.FAILURE


@pytest.mark.parametrize(
    ("rerun", "why"),
    [
        (True, "rerun must be null or an object"),
        ({**_GOOD, "extra": 1}, "rerun must hold exactly"),
        ({**_GOOD, "tests": []}, "rerun names no test"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": "passed", "why": "x"}]}, "flake"),
        ({**_GOOD, "tests": [{"test": "", "rerun_result": None, "why": "x"}]}, "a test id"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": None}]}, "each rerun test holds"),
        ({**_GOOD, "exit": True}, "rerun exit"),
        ({**_GOOD, "exit": 1.5}, "rerun exit"),
        ({**_GOOD, "argv": []}, "rerun argv"),
        ({**_GOOD, "argv": ["pytest", 0]}, "rerun argv"),
        ({k: v for k, v in _GOOD.items() if k != "exit"}, "rerun must hold exactly"),
        # A classification with no rerun process behind it.
        ({**_GOOD, "argv": None, "exit": None}, "is classified, and no rerun ran"),
        ({**_GOOD, "exit": None}, "both null or both set"),
        ({**_GOOD, "argv": None}, "both null or both set"),
        ({**_GOOD, "tests": [_GOOD["tests"][0], _GOOD["tests"][0]]}, "twice"),
        ({**_GOOD, "tests": [FLAKY]}, "each rerun test holds"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": "flake", "why": ""}]}, "why must"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": "Flake", "why": "x"}]}, "flake"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": True, "why": "x"}]}, "flake"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": "", "why": "x"}]}, "flake"),
        # A word of another JSON type is refused by name, never a crash.
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": [], "why": "x"}]}, "flake"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": {}, "why": "x"}]}, "flake"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": ["flake"], "why": "x"}]}, "flake"),
        # An entry of any other shape or type.
        (
            {**_GOOD, "tests": [{"test": FLAKY, "rerun_result": "flake", "why": "x", "extra": 1}]},
            "each rerun test holds",
        ),
        ({**_GOOD, "tests": [{"test": 1, "rerun_result": "flake", "why": "x"}]}, "a test id"),
        ({**_GOOD, "tests": [{"test": FLAKY, "rerun_result": "flake", "why": 1}]}, "why must"),
    ],
)
def test_a_rerun_field_it_cannot_read_is_refused(rerun, why):
    with pytest.raises(verdict.Refused, match=why):
        _written(rerun=rerun)


@pytest.mark.parametrize(
    ("result", "code"),
    [(verdict.SUCCESS, 0), (verdict.SKIPPED, 0), (verdict.CANCELLED, 1)],
)
def test_only_a_failure_carries_a_rerun(result, code):
    """run-job reruns only a red step's failed tests, so a record of any other result that names
    a rerun is not one it wrote."""
    with pytest.raises(verdict.Refused, match=f"only a failure has a rerun, and this {result}"):
        _written(result=result, steps=[(["pytest"], code, 1.0)], rerun=_GOOD)


# ── the telemetry rerun_result field ──────────────────────────────────────────────────────────


def _zipped(report: bytes, member: str = "junit.xml") -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, report)
    return buffer.getvalue()


def test_the_report_the_rerun_marks_gives_telemetry_its_rerun_result(tmp_path):
    from tools.ci import telemetry

    root, _, _ = _suite(tmp_path)
    _run(
        root, _job("tests/test_flaky.py", "tests/test_broken.py", "tests/test_guarded.py"), tmp_path
    )
    cases, _, _ = telemetry.report_cases("junit-x", _zipped((root / REPORT).read_bytes()))
    got = {test: (first, rerun) for test, _, first, _, rerun, _ in cases}
    assert got == {
        "tests.test_flaky::test_fails_then_passes": ("failed", "flake"),
        "tests.test_broken.TestBroken::test_fails_twice[a b]": ("failed", "repeated failure"),
        "tests.test_broken.TestBroken::test_fails_twice[c]": ("failed", "repeated failure"),
        "tests.test_broken::test_passes": ("passed", None),
        # Never rerun, so never classified: its line's rerun_result is null.
        "tests.test_guarded::test_security_fails": ("failed", None),
    }


def test_telemetry_and_the_record_spell_the_two_words_alike():
    from tools.ci import telemetry

    assert telemetry.RERUN_RESULTS == verdict.RERUN_RESULTS
    assert telemetry.FLAKE == verdict.FLAKE
    assert telemetry.RERUN_PROPERTY == jobs.RERUN_PROPERTY


@pytest.mark.parametrize(
    ("case", "why"),
    [
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result" value="passed"/></properties>'
            '<failure message="x"/></testcase>',
            "a rerun_result of 'passed'",
        ),
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result" value="flake"/></properties></testcase>',
            "its first result is passed",
        ),
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result" value="repeated failure"/></properties>'
            '<skipped message="x"/></testcase>',
            "its first result is skipped",
        ),
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result" value=""/></properties>'
            '<failure message="x"/></testcase>',
            "a rerun_result of ''",
        ),
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result"/></properties>'
            '<failure message="x"/></testcase>',
            "a rerun_result of None",
        ),
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result" value="Flake"/></properties>'
            '<failure message="x"/></testcase>',
            "a rerun_result of 'Flake'",
        ),
        # Two words on one case, in either order: neither is believed over the other.
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result" value="repeated failure"/>'
            '<property name="rerun_result" value="flake"/></properties>'
            '<failure message="x"/></testcase>',
            "more than one rerun_result",
        ),
        (
            '<testcase classname="t" name="a"><properties>'
            '<property name="rerun_result" value="flake"/>'
            '<property name="rerun_result" value="repeated failure"/></properties>'
            '<failure message="x"/></testcase>',
            "more than one rerun_result",
        ),
    ],
)
def test_a_rerun_result_telemetry_cannot_believe_fails_the_collection(case, why):
    from tools.ci import telemetry

    report = f'<?xml version="1.0"?><testsuites><testsuite>{case}</testsuite></testsuites>'
    with pytest.raises(telemetry.CollectError, match=why):
        telemetry.report_cases("junit-x", _zipped(report.encode()))


def test_the_rerun_keeps_the_steps_time_budget(tmp_path):
    """The same per-test budget as the first attempt: the step's own --timeout goes with it."""
    root, _, _ = _suite(tmp_path)
    argv = (
        *nested_pytest.argv(".", python=PY),
        "-p",
        "no:cacheprovider",
        "--timeout=45",
        "-q",
    )
    job = _job(command=(Step((*argv, "tests/test_flaky.py", f"--junitxml={REPORT}")),))
    record = _record(_run(root, job, tmp_path))
    assert "--timeout=45" in record["rerun"]["argv"], record["rerun"]["argv"]
    assert jobs._rerun_args(["uv", "run", "pytest", "--timeout", "9", "-p", "no:x", "-q"]) == [
        "--timeout",
        "9",
        "-p",
        "no:x",
    ]


def test_a_pytest_exit_1_with_no_failed_case_in_its_report_is_red_with_no_rerun(tmp_path):
    """pytest can exit 1 with every case passing (a hook that fails the session). There is no
    failed test to diagnose, so no rerun runs, and the job is red as its step was."""
    root, log, _ = _suite(tmp_path)
    (root / "tests" / "conftest.py").write_text(
        "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 1\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "a session that fails with every test passing")
    outcome = _run(root, _job("tests/test_broken.py::test_passes"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE and record["rerun"] is None
    assert log.read_text().splitlines() == ["test_passes"]


def test_a_pytest_step_that_ends_on_another_exit_than_1_is_not_rerun(tmp_path):
    """Only exit 1 means tests ran and some failed. A session that ends otherwise (here 3, an
    internal error, with a failed case already in its report) has nothing the rerun can trust,
    so it is red with no rerun, and the failed test runs once."""
    root, log, _ = _suite(tmp_path)
    (root / "tests" / "conftest.py").write_text(
        "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 3\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "a session that ends on an internal error")
    outcome = _run(root, _job("tests/test_flaky.py"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert outcome.reasons[0] == "step 1: the job exited 3"
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE and record["rerun"] is None
    assert log.read_text().splitlines() == ["test_fails_then_passes"]


# ── a step that failed for a fault of its own, and a report the rerun cannot trust ───────────

#: Each runs after pytest wrote its report (``pytest_unconfigure``), so the report is fresh and
#: names the failed test: only the step's own fault keeps the rerun from running.
_STEP_FAULTS = {
    "a whole-step timeout": (
        "import time\n\ndef pytest_unconfigure(config):\n    time.sleep(120)\n",
        "the job timed out after",
        run.RED,
    ),
    "a signal to the runner": (
        "import os, signal, time\n\n"
        "def pytest_unconfigure(config):\n"
        "    os.kill(os.getppid(), signal.SIGINT)\n"
        "    time.sleep(60)\n",
        "interrupted by SIGINT",
        run.PARTIAL,
    ),
    "the credential tripwire": (
        "import subprocess\n\n"
        "def pytest_unconfigure(config):\n"
        "    subprocess.run(['git', 'credential', 'fill'],"
        " input='protocol=https\\nhost=example.invalid\\n\\n', text=True, capture_output=True)\n",
        "the tripwire",
        run.RED,
    ),
}


@pytest.mark.parametrize("fault", sorted(_STEP_FAULTS))
def test_a_step_that_failed_for_a_timeout_a_signal_or_the_tripwire_is_not_rerun(tmp_path, fault):
    root, log, _ = _suite(tmp_path)
    conftest, said, code = _STEP_FAULTS[fault]
    _commit(root, {"tests/conftest.py": conftest}, fault)
    options = jobs.Options(deadline_s=10, grace=0.5)
    outcome = _run(root, _job("tests/test_flaky.py"), tmp_path, options)
    assert outcome.code == code, outcome.reasons
    assert any(said in reason for reason in outcome.reasons), outcome.reasons
    # The report was there, fresh, naming the failed test; the fault alone stopped the rerun.
    assert _failed_in_report(root) == ["tests.test_flaky::test_fails_then_passes"]
    assert _record(outcome)["rerun"] is None
    assert log.read_text().splitlines() == ["test_fails_then_passes"]
    assert not any(reason.startswith("step 1: rerun") for reason in outcome.reasons)


#: Each leaves a plain exit 1 (a failed test, nothing else wrong), with a report the rerun must
#: not read failed tests from.
_UNTRUSTED_REPORTS = {
    "a malformed report": (
        f"def pytest_unconfigure(config):\n"
        f"    open({REPORT!r}, 'w').write('<testsuites><testsuite><testcase')\n"
    ),
    "a missing report": (
        f"import os\n\ndef pytest_unconfigure(config):\n    os.remove({REPORT!r})\n"
    ),
    # The same report, naming the failed test, only older than the job.
    "a report older than the job": (
        f"import os\n\ndef pytest_unconfigure(config):\n    os.utime({REPORT!r}, (0, 0))\n"
    ),
}


@pytest.mark.parametrize("report", sorted(_UNTRUSTED_REPORTS))
def test_a_report_the_rerun_cannot_trust_gets_no_rerun(tmp_path, report):
    root, log, _ = _suite(tmp_path)
    _commit(root, {"tests/conftest.py": _UNTRUSTED_REPORTS[report]}, report)
    outcome = _run(root, _job("tests/test_flaky.py"), tmp_path)
    assert outcome.code == run.RED
    # A failed test and nothing else, so the step reached the rerun's reading of its report.
    assert outcome.reasons == ["step 1: the job exited 1"], outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE and record["rerun"] is None
    assert log.read_text().splitlines() == ["test_fails_then_passes"]


# ── the rerun's time and a per-test timeout ───────────────────────────────────────────────────


def test_the_rerun_is_stopped_at_the_end_of_the_jobs_time(tmp_path, capsys):
    """The rerun runs inside what is left of the job's time: one that would run on is stopped
    there, says first which of its processes were alive, classifies nothing, and the job stays
    red. Its per-test budget (25s) is longer than the job's time (12s), so only the job's time
    can stop it this soon."""
    root, log, _ = _suite(tmp_path)
    flag = tmp_path / "hang-seen"
    _commit(
        root,
        {
            "tests/test_hangs.py": (
                "import os, time\n\n"
                "def test_fails_then_hangs():\n"
                f"    {_calls(log)}"
                f"    if not os.path.exists({str(flag)!r}):\n"
                f"        open({str(flag)!r}, 'w').close()\n"
                "        raise AssertionError('first call')\n"
                "    time.sleep(300)\n"
            )
        },
        "a test that hangs on its rerun",
    )
    argv = (
        *nested_pytest.argv(".", python=PY),
        "-p",
        "no:cacheprovider",
        "--timeout=25",
        "-q",
    )
    job = _job(command=(Step((*argv, "tests/test_hangs.py", f"--junitxml={REPORT}")),))
    began = time.monotonic()
    outcome = _run(root, job, tmp_path, jobs.Options(deadline_s=12, grace=0.5))
    took = time.monotonic() - began
    assert outcome.code == run.RED, outcome.reasons
    stopped = [r for r in outcome.reasons if r.startswith("step 1: rerun: the job timed out after")]
    assert stopped, outcome.reasons
    assert float(stopped[0].rsplit(" ", 1)[1].rstrip("s")) <= 12
    assert took < 22, took
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    [entry] = record["rerun"]["tests"]
    assert entry["test"] == "tests/test_hangs.py::test_fails_then_hangs"
    assert entry["rerun_result"] is None and "reported nothing" in entry["why"]
    assert log.read_text().splitlines() == ["test_fails_then_hangs"] * 2
    # The first run ended on its own, so the one hang report is the rerun's.
    assert capsys.readouterr().err.count("possible xdist hang") == 1


def test_no_rerun_starts_once_the_jobs_time_is_spent(tmp_path):
    root, log, _ = _suite(tmp_path)
    (root / REPORT).parent.mkdir()
    (root / REPORT).write_text(
        '<?xml version="1.0"?><testsuites><testsuite><testcase classname="tests.test_flaky"'
        ' name="test_fails_then_passes"><failure message="x"/></testcase></testsuite></testsuites>'
    )
    rerun, reasons, stopped = jobs.diagnose(
        [
            *nested_pytest.argv(".", python=PY),
            "tests/test_flaky.py",
            f"--junitxml={REPORT}",
        ],
        cwd=root,
        env={},
        host=_host(root),
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
    assert not log.exists()
    # The record reads it back, red.
    [read] = verdict.from_job_records([_written(rerun=rerun)])
    assert read.result == verdict.FAILURE


def test_a_test_stopped_by_its_own_per_test_timeout_is_a_failed_test_and_is_rerun(tmp_path):
    """A per-test timeout fails that test and the session goes on (exit 1): it is rerun like any
    failed test. Here it passes alone, a flake, and the job stays red."""
    root, log, _ = _suite(tmp_path)
    flag = tmp_path / "slow-seen"
    _commit(
        root,
        {
            "tests/test_slow.py": (
                "import os, time\n\n"
                "def test_times_out_then_passes():\n"
                f"    {_calls(log)}"
                f"    if not os.path.exists({str(flag)!r}):\n"
                f"        open({str(flag)!r}, 'w').close()\n"
                "        time.sleep(60)\n"
            )
        },
        "a test that runs past its per-test timeout once",
    )
    argv = (
        *nested_pytest.argv(".", python=PY),
        "-p",
        "no:cacheprovider",
        "--timeout=2",
        "-q",
    )
    job = _job(command=(Step((*argv, "tests/test_slow.py", f"--junitxml={REPORT}")),))
    outcome = _run(root, job, tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert outcome.reasons == ["step 1: the job exited 1"], outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    assert "--timeout=2" in record["rerun"]["argv"]
    [entry] = record["rerun"]["tests"]
    assert entry == {
        "test": "tests/test_slow.py::test_times_out_then_passes",
        "rerun_result": "flake",
        "why": "passed rerun alone in a new process",
    }
    assert log.read_text().splitlines() == ["test_times_out_then_passes"] * 2


# ── the weekly report reads the rerun's word ──────────────────────────────────────────────────


def test_the_weekly_report_counts_a_flake_and_not_a_repeated_failure(tmp_path):
    """From the job's marked report, through the collector's reading of it, to the report's flake
    figure: the flake is a first failure a rerun passed, the repeated failure is not."""
    from tools.ci import report, telemetry

    root, _, _ = _suite(tmp_path)
    _run(root, _job("tests/test_flaky.py", "tests/test_broken.py"), tmp_path)
    cases, _, _ = telemetry.report_cases("junit-x", _zipped((root / REPORT).read_bytes()))
    head = dict.fromkeys(telemetry.RUN_FIELDS)
    head.update(
        kind="run",
        schema=telemetry.SCHEMA,
        version=telemetry.VERSION,
        run_id=7,
        attempt=1,
        workflow="ci.yml",
        event="push",
        branch="main",
        created_at="2026-09-01T09:00:00Z",
        jobs=[],
    )
    lines = [head]
    for test, seconds, first, _, rerun, _ in cases:
        line = dict.fromkeys(telemetry.TEST_FIELDS)
        line.update(
            kind="test",
            run_id=7,
            attempt=1,
            workflow="ci.yml",
            report="junit-x",
            test=test,
            seconds=seconds,
            first_result=first,
            rerun_result=rerun,
        )
        lines.append(line)
    folder = tmp_path / "history"
    folder.mkdir()
    (folder / "history-7-1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    tests = report.read(folder).tests
    passed = {test: f.passed_on_rerun for test, f in tests.items() if f.first_failures}
    assert passed == {
        "tests.test_flaky::test_fails_then_passes": 1,
        "tests.test_broken.TestBroken::test_fails_twice[a b]": 0,
        "tests.test_broken.TestBroken::test_fails_twice[c]": 0,
    }


def test_the_weekly_report_reads_the_classification_from_the_job_record(tmp_path):
    """From the job's record, as the collector reads the record's artifact into its line of the
    history, to the report's figure of what the reruns found: one flake and two repeated
    failures, each in the job that found it."""
    from tools.ci import report, telemetry

    root, _, _ = _suite(tmp_path)
    outcome = _run(root, _job("tests/test_flaky.py", "tests/test_broken.py"), tmp_path)
    name = verdict.record_artifact("suite", run_id=7, attempt=1)
    record = outcome.record.read_text().replace('"run_id": null', '"run_id": 7')
    line = telemetry.record_line(name, _zipped(record.encode(), "suite.json"), 7, 1)
    head = dict.fromkeys(telemetry.RUN_FIELDS)
    head.update(
        kind="run",
        schema=telemetry.SCHEMA,
        version=telemetry.VERSION,
        run_id=7,
        attempt=1,
        workflow="ci.yml",
        event="push",
        branch="main",
        created_at="2026-09-01T09:00:00Z",
        jobs=[],
    )
    folder = tmp_path / "history"
    folder.mkdir()
    (folder / "history-7-1.jsonl").write_text(telemetry.history_text([head, line]))
    diagnosed = {
        row["test"]: (row["flakes"], row["repeated_failures"], row["jobs"])
        for row in report.figures(report.read(folder), [], [])["diagnosed"]
    }
    assert diagnosed == {
        FLAKY: (1, 0, ["suite"]),
        TWICE_A: (0, 1, ["suite"]),
        TWICE_C: (0, 1, ["suite"]),
    }


# ── the merge verdict reads the record's classification ───────────────────────────────────────
#
# The results stage judges a run's uploaded records with ``verdict --records`` (#3915): an
# unquarantined flaky failure reads red, and the same failure under a valid quarantine entry reads
# green, each listed with how it stands. The controller's merge-verdict check run, which posts
# that reading, is #3910's.

RUN_ID = 31415


def _on_github(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> jobs.Host:
    """A GitHub runner's host: the record names this run and its attempt."""
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    monkeypatch.setenv("GITHUB_RUN_ID", str(RUN_ID))
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    # A run of no change (a dispatch's): on a pull request's run, given no changed list, the
    # verdict waives no quarantined failure, so the runner's own event must not decide these.
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    return jobs.Host(
        root=root, family="ubuntu-", path=os.environ.get("PATH", os.defpath), on_github=True
    )


def _results_stage(
    tmp_path: Path, root: Path, outcome: jobs.Outcome, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    """``verdict --records`` over the job's record as download-artifact lays out its upload."""
    assert outcome.record is not None, outcome.reasons
    downloaded = tmp_path / "downloaded"
    folder = downloaded / verdict.record_artifact("suite", run_id=RUN_ID, attempt=1)
    folder.mkdir(parents=True)
    (folder / outcome.record.name).write_text(outcome.record.read_text())
    (tmp_path / "plan.json").write_text('["suite"]')
    (tmp_path / "needs.json").write_text("{}")
    head = _git(root, "rev-parse", "HEAD")
    capsys.readouterr()
    code = verdict.main(
        [
            "--plan", str(tmp_path / "plan.json"), "--needs", str(tmp_path / "needs.json"),
            "--records", str(downloaded), "--run-id", str(RUN_ID),
            "--head", head, "--run-head", head,
        ]
    )  # fmt: skip
    return code, capsys.readouterr().out


def _quarantine(root: Path, nodeid: str, *, days: int = 10, assignee: bool = True) -> None:
    """The suite's own quarantine (tests/quarantine.py) loaded in the planted tree, with one entry
    for *nodeid* that expires *days* from today, committed."""
    expires = datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=days)
    _commit(
        root,
        {
            "tests/conftest.py": (
                "import sys\n"
                "from pathlib import Path\n\n"
                f"sys.path.insert(0, {str(TESTS)!r})\n"
                "import quarantine\n\n\n"
                "def pytest_configure(config):\n"
                "    quarantine.configure(config, Path(__file__).resolve().parents[1])\n"
            ),
            "tests/quarantine.toml": (
                "[[entry]]\n"
                f'nodeid = "{nodeid}"\n'
                "issue = 4000\n"
                + ('assignee = "someone"\n' if assignee else "")
                + f"expires = {expires.isoformat()}\n"
                'reason = "fails on its first call in a process"\n'
            ),
        },
        "a quarantine entry",
    )


def test_a_merge_verdict_over_an_unquarantined_flaky_failure_reads_red_and_lists_it(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    root, log, _ = _suite(tmp_path)
    host = _on_github(root, tmp_path, monkeypatch)
    job = _job("tests/test_flaky.py")
    outcome = jobs.run_one(Catalogue((job,)), job, None, host, jobs.Options(), tmp_path / "work")
    assert outcome.code == run.RED, outcome.reasons
    assert log.read_text().splitlines() == ["test_fails_then_passes"] * 2
    code, out = _results_stage(tmp_path, root, outcome, capsys)
    assert code == 1, out
    assert "- `suite`: failure (attempt 1)" in out
    assert f"- `suite` attempt 1: `{FLAKY}`: flake (passed rerun alone in a new process)" in out


def test_the_same_failure_under_a_valid_quarantine_entry_reads_green_and_is_listed(
    tmp_path, monkeypatch, capsys
):
    """The suite's own quarantine (tests/quarantine.py) loaded in the planted tree, with one entry
    for the flaky test: the test still runs, its failure is recorded, and the job passes."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    root, log, _ = _suite(tmp_path)
    _quarantine(root, FLAKY)
    host = _on_github(root, tmp_path, monkeypatch)
    job = _job("tests/test_flaky.py")
    outcome = jobs.run_one(Catalogue((job,)), job, None, host, jobs.Options(), tmp_path / "work")
    assert outcome.code == run.OK, outcome.reasons
    # It ran once, failed under its entry, and was never rerun.
    assert log.read_text().splitlines() == ["test_fails_then_passes"]
    record = _record(outcome)
    assert record["result"] == verdict.SUCCESS and record["rerun"] is None
    assert record["quarantined"] == ["tests.test_flaky::test_fails_then_passes"]
    code, out = _results_stage(tmp_path, root, outcome, capsys)
    assert code == 0, out
    assert out.startswith("### Verdict: green\n")
    assert "- `suite` attempt 1: `tests.test_flaky::test_fails_then_passes`: quarantined" in out


@pytest.mark.parametrize(
    ("nodeid", "days", "assignee"),
    [
        pytest.param(FLAKY, 0, True, id="expired"),
        pytest.param(FLAKY, 10, False, id="malformed"),
        pytest.param("tests/test_broken.py::test_passes", 10, True, id="for-another-test"),
    ],
)
def test_a_quarantine_entry_that_waives_nothing_leaves_the_flaky_failure_red(
    nodeid, days, assignee, tmp_path, monkeypatch, capsys
):
    """Only a valid entry for the failed test waives it: one that has expired or breaks a rule
    stops the session before any test runs, and one for another test leaves this failure a
    failure. Each job reads red, and its verdict too, with no failure listed as quarantined."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    root, _, _ = _suite(tmp_path)
    _quarantine(root, nodeid, days=days, assignee=assignee)
    host = _on_github(root, tmp_path, monkeypatch)
    job = _job("tests/test_flaky.py", "tests/test_broken.py::test_passes")
    outcome = jobs.run_one(Catalogue((job,)), job, None, host, jobs.Options(), tmp_path / "work")
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE and record["quarantined"] == []
    code, out = _results_stage(tmp_path, root, outcome, capsys)
    assert code == 1, out
    assert "- `suite`: failure (attempt 1)" in out
    assert ": quarantined" not in out
    if nodeid != FLAKY:
        assert f"- `suite` attempt 1: `{FLAKY}`: flake" in out


def test_a_local_tier_run_lists_the_failed_tests_with_its_verdict(tmp_path):
    root, _, _ = _suite(tmp_path)
    job = _job("tests/test_flaky.py", "tests/test_broken.py")
    code, _, judged = jobs.run_tier(
        Catalogue((job,)), "change", _host(root), jobs.Options(), tmp_path / "work"
    )
    assert code == run.RED and not judged.green
    assert [(t.key, t.test, t.standing) for t in judged.tests] == [
        ("suite", TWICE_A, verdict.REPEATED_FAILURE),
        ("suite", TWICE_C, verdict.REPEATED_FAILURE),
        ("suite", FLAKY, verdict.FLAKE),
    ]


# ── which test a failed case names ────────────────────────────────────────────────────────────


def _package(root: Path, files: dict[str, str]) -> None:
    """A package under the repository with a pytest config of its own, as the client package
    is: pytest names its cases from that folder, its rootdir, and not from the step's."""
    _commit(
        root,
        {"sub/pyproject.toml": "[tool.pytest.ini_options]\n", "sub/tests/__init__.py": "", **files},
        "a package with its own pytest root",
    )


def test_a_flaky_failure_in_a_package_with_its_own_pytest_root_is_rerun(tmp_path):
    """Its case's classname is relative to the package (``tests.test_client``), so the rerun
    finds the test under the step's own path, not at the repository's root."""
    root, log, _ = _suite(tmp_path)
    flag = tmp_path / "client-seen"
    _package(
        root,
        {
            "sub/tests/test_client.py": (
                "import os\n\n"
                "def test_client_fails_then_passes():\n"
                f"    {_calls(log)}"
                f"    if not os.path.exists({str(flag)!r}):\n"
                f"        open({str(flag)!r}, 'w').close()\n"
                "        raise AssertionError('first call')\n"
            )
        },
    )
    outcome = _run(root, _job("sub/tests"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    client = "sub/tests/test_client.py::test_client_fails_then_passes"
    assert record["rerun"]["tests"] == [
        {"test": client, "rerun_result": "flake", "why": "passed rerun alone in a new process"}
    ]
    assert record["rerun"]["argv"][-1] == client
    assert log.read_text().splitlines() == ["test_client_fails_then_passes"] * 2
    assert _junit_property(root, "rerun_result") == {
        "tests.test_client::test_client_fails_then_passes": "flake"
    }


def test_a_package_failure_never_reruns_its_namesake_at_the_repository_root(tmp_path, capsys):
    """The package's test always fails; a test of the same path and name passes at the root.
    The rerun runs the package's, so the failure is a repeated one, never a flake."""
    root, log, _ = _suite(tmp_path)
    _package(
        root,
        {
            "sub/tests/test_same.py": (
                f"def test_same():\n    {_calls(log)}    raise AssertionError('always')\n"
            ),
            "tests/test_same.py": (
                f"def test_same():\n    open({str(log)!r}, 'a').write('the root namesake\\n')\n"
            ),
        },
    )
    outcome = _run(root, _job("sub/tests"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["rerun"]["tests"] == [
        {
            "test": "sub/tests/test_same.py::test_same",
            "rerun_result": "repeated failure",
            "why": "failed again, alone",
        }
    ]
    assert log.read_text().splitlines() == ["test_same"] * 2
    assert "title=flake" not in capsys.readouterr().out


def _diagnose(root: Path, tmp_path: Path, paths: Sequence[str], cases: str) -> tuple:
    """Run :func:`jobs.diagnose` on a step of *paths* whose report holds *cases*, as pytest
    wrote it in this job, with the job's time still ahead."""
    (root / REPORT).parent.mkdir(exist_ok=True)
    (root / REPORT).write_text(
        f'<?xml version="1.0"?><testsuites><testsuite>{cases}</testsuite></testsuites>'
    )
    return jobs.diagnose(
        [*nested_pytest.argv(".", python=PY), *paths, f"--junitxml={REPORT}"],
        cwd=root,
        env={},
        host=_host(root),
        scratch=run.Scratch.create(tmp_path / "scratch"),
        grace=0.5,
        deadline=time.monotonic() + 60,
        started=time.time() - 60,
    )


_NOT_ONE = "names no one test file of the step's own, so it is not rerun"


@pytest.mark.parametrize(
    ("paths", "classname", "files"),
    [
        # No file anywhere.
        (["tests"], "tests.test_gone", {}),
        # A file of that name at the root, outside the step's own path.
        (
            ["sub/tests"],
            "tests.test_flaky",
            {"sub/tests/test_other.py": "def test_x():\n    pass\n"},
        ),
        # Two files the classname could name under the step's path: either could be the one.
        (
            ["tests"],
            "tests.test_twin",
            {"tests/test_twin.py": "", "tests/tests/test_twin.py": ""},
        ),
    ],
    ids=["no file", "a file outside the step's paths", "two files"],
)
def test_a_case_that_names_no_one_file_of_the_steps_own_is_recorded_and_not_rerun(
    tmp_path, paths, classname, files
):
    root, log, _ = _suite(tmp_path)
    if files:
        _commit(root, files, "the files a classname may name")
    rerun, reasons, stopped = _diagnose(
        root,
        tmp_path,
        paths,
        f'<testcase classname="{classname}" name="test_x"><failure/></testcase>',
    )
    label = f"{classname}::test_x"
    assert rerun == {
        "argv": None,
        "exit": None,
        "tests": [{"test": label, "rerun_result": None, "why": _NOT_ONE}],
    }
    assert reasons == [] and stopped is False
    assert not log.exists()
    [read] = verdict.from_job_records([_written(rerun=rerun)])
    assert read.result == verdict.FAILURE


def test_a_case_that_names_no_file_is_recorded_beside_one_that_is_rerun(tmp_path):
    root, log, seen = _suite(tmp_path)
    seen.touch()  # its first call was the step's
    rerun, _, _ = _diagnose(
        root,
        tmp_path,
        ["tests"],
        '<testcase classname="tests.test_gone" name="test_x"><failure/></testcase>'
        '<testcase classname="tests.test_flaky" name="test_fails_then_passes">'
        "<failure/></testcase>",
    )
    assert [(e["test"], e["rerun_result"]) for e in rerun["tests"]] == [
        ("tests.test_gone::test_x", None),
        (FLAKY, "flake"),
    ]
    assert rerun["argv"][-1] == FLAKY


def test_a_step_that_names_no_path_has_a_case_looked_up_from_its_folder(tmp_path):
    """A step that names no file or folder collects from its own (its config's testpaths), so
    that folder is where its failed case is looked up."""
    root, log, seen = _suite(tmp_path)
    seen.touch()  # its first call was the step's
    cases = '<testcase classname="tests.test_flaky" name="test_fails_then_passes"><failure/>'
    rerun, _, _ = _diagnose(root, tmp_path, [], cases + "</testcase>")
    assert [(e["test"], e["rerun_result"]) for e in rerun["tests"]] == [(FLAKY, "flake")]
    assert log.read_text().splitlines() == ["test_fails_then_passes"]


def test_a_step_that_ran_no_workers_is_rerun_with_no_worker_option(tmp_path):
    """A step that names no worker count ran in one process already, and its pytest may have no
    xdist (the client package's has none): the rerun names no ``-n`` that pytest would refuse."""
    root, log, _ = _suite(tmp_path)
    argv = (
        *nested_pytest.argv(".", python=PY),
        "-p",
        "no:cacheprovider",
        "-p",
        "no:xdist",
        "-q",
    )
    job = _job(command=(Step((*argv, "tests/test_flaky.py", f"--junitxml={REPORT}")),))
    record = _record(_run(root, job, tmp_path))
    assert _by_test(record)[FLAKY]["rerun_result"] == "flake", record["rerun"]
    assert "-n" not in record["rerun"]["argv"], record["rerun"]["argv"]
    assert log.read_text().splitlines() == ["test_fails_then_passes"] * 2


def test_a_failed_call_and_a_teardown_error_are_one_test_rerun_once(tmp_path):
    """pytest writes a call that failed and a teardown that errored as two cases of one id: the
    rerun runs it once, and both cases carry its word."""
    root, log, _ = _suite(tmp_path)
    _commit(
        root,
        {
            "tests/test_teardown.py": (
                "import pytest\n\n"
                "@pytest.fixture\n"
                "def bad():\n"
                "    yield\n"
                "    raise RuntimeError('teardown')\n\n"
                "def test_fails_and_tears_down_badly(bad):\n"
                f"    {_calls(log)}"
                "    assert False\n"
            )
        },
        "a test whose call fails and whose teardown errors",
    )
    outcome = _run(root, _job("tests/test_teardown.py"), tmp_path)
    assert len(_failed_in_report(root)) == 2
    test = "tests/test_teardown.py::test_fails_and_tears_down_badly"
    assert _record(outcome)["rerun"]["tests"] == [
        {"test": test, "rerun_result": "repeated failure", "why": "failed again, alone"}
    ]
    assert log.read_text().splitlines() == ["test_fails_and_tears_down_badly"] * 2
    import xml.etree.ElementTree as ET

    words = [
        [p.get("value") for p in case.iter("property")]
        for case in ET.parse(root / REPORT).getroot().iter("testcase")
    ]
    assert words == [["repeated failure"], ["repeated failure"]]


def test_a_setup_error_is_rerun_and_the_collector_reads_its_word(tmp_path):
    """A test whose fixture errors is a failed test (an error case): rerun, a flake when its
    setup then works, and the collector takes the word on its error case."""
    from tools.ci import telemetry

    root, log, _ = _suite(tmp_path)
    flag = tmp_path / "setup-seen"
    _commit(
        root,
        {
            "tests/test_setup_error.py": (
                "import os, pytest\n\n"
                "@pytest.fixture\n"
                "def once():\n"
                f"    if not os.path.exists({str(flag)!r}):\n"
                f"        open({str(flag)!r}, 'w').close()\n"
                "        raise RuntimeError('setup')\n\n"
                "def test_errors_then_passes(once):\n"
                f"    {_calls(log)}"
            )
        },
        "a fixture that errors once",
    )
    outcome = _run(root, _job("tests/test_setup_error.py"), tmp_path)
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry["rerun_result"] == "flake", entry
    assert log.read_text().splitlines() == ["test_errors_then_passes"]
    cases, _, _ = telemetry.report_cases("junit-x", _zipped((root / REPORT).read_bytes()))
    assert [(first, word) for _, _, first, _, word, _ in cases] == [("error", "flake")]


def test_a_test_that_skips_on_the_rerun_is_not_classified(tmp_path):
    root, log, _ = _suite(tmp_path)
    flag = tmp_path / "skip-seen"
    _commit(
        root,
        {
            "tests/test_skips.py": (
                "import os, pytest\n\n"
                "def test_fails_then_skips():\n"
                f"    if not os.path.exists({str(flag)!r}):\n"
                f"        open({str(flag)!r}, 'w').close()\n"
                "        raise AssertionError('first call')\n"
                "    pytest.skip('alone')\n"
            )
        },
        "a test that skips when run alone",
    )
    [entry] = _record(_run(root, _job("tests/test_skips.py"), tmp_path))["rerun"]["tests"]
    assert entry == {
        "test": "tests/test_skips.py::test_fails_then_skips",
        "rerun_result": None,
        "why": "skipped on the rerun, so not classified",
    }
    assert _junit_property(root, "rerun_result") == {
        "tests.test_skips::test_fails_then_skips": None
    }


def test_a_job_whose_only_failure_is_security_marked_says_so_and_never_runs_it_again(tmp_path):
    """The rerun is a process that deselects the security-marked test (exit 5, none selected):
    it collects the module and never runs the test, and the record says why."""
    root, log, _ = _suite(tmp_path)
    outcome = _run(root, _job("tests/test_guarded.py"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    record = _record(outcome)
    assert record["rerun"]["exit"] == 5
    assert record["rerun"]["tests"] == [
        {
            "test": GUARDED,
            "rerun_result": None,
            "why": "security-marked: the rerun deselects it, so it is never rerun",
        }
    ]
    assert log.read_text().splitlines() == ["test_security_fails"]


# ── a rerun whose report or process the classification cannot trust ──────────────────────────

#: Each conftest rewrites only the rerun's own report (its path is the rerun's scratch file),
#: after pytest wrote it, so the rerun's pytest exits as it would have.
_RERUN_REPORTS = {
    "a rerun report that is not XML": (
        "def pytest_unconfigure(config):\n"
        "    path = str(config.option.xmlpath or '')\n"
        "    if 'rerun-' in path:\n"
        "        open(path, 'w').write('<testsuites><testcase')\n",
        "rerun: its report is unreadable",
    ),
    "a rerun report that is gone": (
        "import os\n\n"
        "def pytest_unconfigure(config):\n"
        "    path = str(config.option.xmlpath or '')\n"
        "    if 'rerun-' in path:\n"
        "        os.remove(path)\n",
        "rerun: it wrote no report",
    ),
    # As a rerun whose pytest took another rootdir would name its case.
    "a rerun report that names its case otherwise": (
        "def pytest_unconfigure(config):\n"
        "    path = str(config.option.xmlpath or '')\n"
        "    if 'rerun-' in path:\n"
        "        text = open(path).read()\n"
        "        open(path, 'w').write(text.replace('\"tests.test_flaky\"', '\"test_flaky\"'))\n",
        "rerun: its report names test_flaky::test_fails_then_passes",
    ),
}


@pytest.mark.parametrize("report", sorted(_RERUN_REPORTS))
def test_a_rerun_report_it_cannot_read_classifies_nothing(tmp_path, report):
    root, log, _ = _suite(tmp_path)
    conftest, said = _RERUN_REPORTS[report]
    _commit(root, {"tests/conftest.py": conftest}, report)
    outcome = _run(root, _job("tests/test_flaky.py"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert any(r.startswith(f"step 1: {said}") for r in outcome.reasons), outcome.reasons
    record = _record(outcome)
    assert record["rerun"]["exit"] == 0
    assert record["rerun"]["tests"] == [
        {"test": FLAKY, "rerun_result": None, "why": "the rerun reported nothing for it (exit 0)"}
    ]
    assert log.read_text().splitlines() == ["test_fails_then_passes"] * 2
    assert _junit_property(root, "rerun_result") == {
        "tests.test_flaky::test_fails_then_passes": None
    }


def test_a_signal_during_the_rerun_marks_the_job_interrupted_and_red(tmp_path):
    """After an interrupted job nothing more runs: a SIGINT that lands during the rerun says so,
    and the job is red as its step was."""
    root, _, _ = _suite(tmp_path)
    flag = tmp_path / "signal-seen"
    _commit(
        root,
        {
            "tests/test_signal.py": (
                "import os, signal, time\n\n"
                "def test_fails_then_signals():\n"
                f"    if not os.path.exists({str(flag)!r}):\n"
                f"        open({str(flag)!r}, 'w').close()\n"
                "        raise AssertionError('first call')\n"
                "    os.kill(os.getppid(), signal.SIGINT)\n"
                "    time.sleep(30)\n"
            )
        },
        "a test that signals the runner on its rerun",
    )
    options = jobs.Options(deadline_s=40, grace=0.5)
    outcome = _run(root, _job("tests/test_signal.py"), tmp_path, options)
    assert outcome.code == run.RED, outcome.reasons
    assert outcome.interrupted is True, outcome.reasons
    assert "step 1: rerun: interrupted by SIGINT" in outcome.reasons, outcome.reasons
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    [entry] = record["rerun"]["tests"]
    assert entry["rerun_result"] is None and "reported nothing" in entry["why"], entry


def test_a_test_that_times_out_twice_is_a_repeated_failure(tmp_path):
    """The rerun has the first attempt's per-test budget (``--timeout=2``), never a longer one:
    a test that outruns it twice is a repeated failure, well inside the job's 20s."""
    root, log, _ = _suite(tmp_path)
    _commit(
        root,
        {
            "tests/test_hang.py": (
                f"import time\n\ndef test_always_hangs():\n    {_calls(log)}    time.sleep(60)\n"
            )
        },
        "a test that always outruns its per-test budget",
    )
    argv = (
        *nested_pytest.argv(".", python=PY),
        "-p",
        "no:cacheprovider",
        "--timeout=2",
        "-q",
    )
    job = _job(command=(Step((*argv, "tests/test_hang.py", f"--junitxml={REPORT}")),))
    began = time.monotonic()
    outcome = _run(root, job, tmp_path, jobs.Options(deadline_s=20, grace=0.5))
    assert outcome.code == run.RED, outcome.reasons
    assert outcome.reasons == ["step 1: the job exited 1"], outcome.reasons
    assert time.monotonic() - began < 15
    [entry] = _record(outcome)["rerun"]["tests"]
    assert entry == {
        "test": "tests/test_hang.py::test_always_hangs",
        "rerun_result": "repeated failure",
        "why": "failed again, alone",
    }
    assert log.read_text().splitlines() == ["test_always_hangs"] * 2


# ── the step's own outputs ────────────────────────────────────────────────────────────────────


def test_the_rerun_leaves_the_steps_report_folder_as_the_step_left_it(tmp_path):
    """A session report the suite writes beside its JUnit (as the real-home guard writes
    reports/real-home-guard.txt) is the job's, not the rerun's: what the rerun wrote there is
    moved to the job's scratch folder, and the step's files are put back."""
    root, _, _ = _suite(tmp_path)
    _commit(
        root,
        {
            "tests/conftest.py": (
                "import os\n\n"
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    os.makedirs('reports', exist_ok=True)\n"
                "    n = str(len(session.items))\n"
                "    open('reports/session.txt', 'w').write(n)\n"
                "    open(f'reports/session-{n}.txt', 'w').write(n)\n"
            )
        },
        "a session report beside the JUnit",
    )
    outcome = _run(root, _job("tests/test_flaky.py", "tests/test_broken.py"), tmp_path)
    record = _record(outcome)
    assert len(record["rerun"]["argv"]) > 0
    # Four tests ran in the job, three in the rerun.
    assert (root / "reports" / "session.txt").read_text() == "4"
    assert sorted(p.name for p in (root / "reports").iterdir()) == [
        "junit.xml",
        "session-4.txt",
        "session.txt",
    ]
    moved = sorted((p.name, p.read_text()) for p in (tmp_path / "work").rglob("session*.txt"))
    assert moved == [("session-3.txt", "3"), ("session.txt", "3")]
    # The job's report still carries the rerun's words.
    assert _junit_property(root, "rerun_result")["tests.test_flaky::test_fails_then_passes"] == (
        "flake"
    )


# ── how the step's argv is read ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "path"),
    [
        (["pytest", "--junitxml=a.xml"], "a.xml"),
        (["pytest", "--junitxml", "a.xml", "-q"], "a.xml"),
        (["pytest", "--junit-xml=a.xml"], "a.xml"),
        (["pytest", "--junit-xml", "a.xml"], "a.xml"),
        (["pytest", "-q"], None),
        (["pytest", "--junitxml"], None),
    ],
)
def test_the_steps_report_is_read_in_each_spelling_pytest_takes(argv, path):
    assert jobs._junit_arg(argv) == path


def test_a_report_folder_that_cannot_be_kept_aside_gets_no_rerun(tmp_path, monkeypatch):
    """The rerun runs only once the step's report folder is kept aside, so it can be put back."""
    root, log, _ = _suite(tmp_path)

    def refuse(folder: Path, into: Path) -> dict:
        raise PermissionError(13, "refused", str(folder))

    monkeypatch.setattr(jobs, "_keep", refuse)
    cases = '<testcase classname="tests.test_flaky" name="test_fails_then_passes"><failure/>'
    rerun, reasons, stopped = _diagnose(root, tmp_path, ["tests"], cases + "</testcase>")
    why = "the step's report folder could not be kept aside, so it is not rerun"
    assert rerun == {
        "argv": None,
        "exit": None,
        "tests": [{"test": FLAKY, "rerun_result": None, "why": why}],
    }
    assert len(reasons) == 1, reasons
    assert reasons[0].startswith("rerun: the step's report folder could not be kept aside"), reasons
    assert stopped is False and not log.exists()


def test_a_report_folder_that_cannot_be_put_back_is_a_reason(tmp_path, monkeypatch):
    root, log, seen = _suite(tmp_path)
    seen.touch()

    def refuse(*args: object) -> list:
        raise PermissionError(13, "refused")

    monkeypatch.setattr(jobs, "_put_back", refuse)
    cases = '<testcase classname="tests.test_flaky" name="test_fails_then_passes"><failure/>'
    rerun, reasons, _ = _diagnose(root, tmp_path, ["tests"], cases + "</testcase>")
    assert [e["rerun_result"] for e in rerun["tests"]] == ["flake"]
    assert len(reasons) == 1, reasons
    assert reasons[0].startswith("rerun: the step's report folder could not be put back"), reasons


def test_a_test_with_any_failed_case_in_a_report_failed(tmp_path):
    """One test can be two cases of one report (its call and its teardown): any failed case
    fails it, and it passed only when every case passed."""
    report = tmp_path / "rerun.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase classname="t" name="a"/><testcase classname="t" name="a"><error/></testcase>'
        '<testcase classname="t" name="b"><skipped/></testcase><testcase classname="t" name="b"/>'
        '<testcase classname="t" name="c"/>'
        "</testsuite></testsuites>"
    )
    assert jobs._results(report) == {
        ("t", "a"): "failed",
        ("t", "b"): "skipped",
        ("t", "c"): "passed",
    }


def test_a_pytest_steps_failed_tests_keep_the_job_red_and_its_other_steps_run_on(tmp_path):
    """A step whose one fault is failed tests its own report names lets the job's other steps
    run, inside the same time: the job is red, and its record holds the one red step, its
    rerun, and the steps after it."""
    root, _, _ = _suite(tmp_path)
    after = tmp_path / "after"
    later = Step((PY, "-c", f"open({str(after)!r}, 'w').close()"))
    flaky = _job("tests/test_flaky.py")
    outcome = _run(root, _job(command=(*flaky.command, later)), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert after.exists()
    record = _record(outcome)
    assert [s["exit"] for s in record["steps"]] == [1, 0]
    assert _by_test(record)[FLAKY]["rerun_result"] == "flake"


def test_a_step_that_failed_for_another_reason_stops_the_job(tmp_path):
    """A usage error is no failed test: the steps after it never run."""
    root, _, _ = _suite(tmp_path)
    after = tmp_path / "after"
    later = Step((PY, "-c", f"open({str(after)!r}, 'w').close()"))
    usage = _job("tests/test_flaky.py", "--no-such-option")
    outcome = _run(root, _job(command=(*usage.command, later)), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert not after.exists()
    assert _record(outcome)["rerun"] is None


def test_a_job_whose_time_runs_out_before_a_planned_step_says_so_in_its_record(
    tmp_path, monkeypatch
):
    """The flake's step is diagnosed and the job runs on; its time runs out after the next step
    and before the third begins, so the record names one planned step that never started, and
    the flake rule passes no flake of it. The clock run-job reads jumps past the job's time as
    the second step ends, so no step is cut short: the third simply never starts."""
    import time as real_time
    import types

    root, _, _ = _suite(tmp_path)
    clock = {"offset": 0.0}
    monkeypatch.setattr(
        jobs,
        "time",
        types.SimpleNamespace(
            monotonic=lambda: real_time.monotonic() + clock["offset"],
            time=real_time.time,
            strftime=real_time.strftime,
        ),
    )
    real_run_job = jobs.run.run_job

    def run_job(argv, **kw):
        result = real_run_job(argv, **kw)
        if list(argv)[-1:] == ["second"]:
            clock["offset"] = 10_000.0
        return result

    monkeypatch.setattr(jobs.run, "run_job", run_job)
    flaky = _job("tests/test_flaky.py")
    second = Step((PY, "-c", "pass", "second"))
    third = Step((PY, "-c", "pass", "third"))
    outcome = _run(root, _job(command=(*flaky.command, second, third)), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert any("ran out before step 3" in r for r in outcome.reasons), outcome.reasons
    record = _record(outcome)
    assert record["schema"] == "personalclaw.ci.job-record/4"
    assert [s["exit"] for s in record["steps"]] == [1, 0]
    assert record["unstarted_steps"] == 1
    assert _by_test(record)[FLAKY]["rerun_result"] == "flake"
    [read] = verdict.from_job_records([outcome.record.read_text()])
    assert read.unstarted_steps == 1


def test_a_job_that_starts_every_planned_step_says_none_never_started(tmp_path):
    root, _, _ = _suite(tmp_path)
    outcome = _run(root, _job("tests/test_flaky.py"), tmp_path)
    assert outcome.code == run.RED, outcome.reasons
    assert _record(outcome)["unstarted_steps"] == 0
