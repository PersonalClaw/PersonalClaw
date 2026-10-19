"""``python -m tools.ci run-job`` and ``run`` (tools/ci/jobs.py): a catalogue job run here.

Each test plants a git repository and runs jobs built in memory, whose steps are small Python
programs, so nothing here installs or runs the real catalogue's commands.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.ci import catalogue, jobs, run, verdict
from tools.ci.catalogue import Catalogue, Env, Job, Step

pytestmark = pytest.mark.security

PY = sys.executable
REPO = catalogue.REPO

#: Who a planted step's own git commits as.
_IDENTITY = {
    "GIT_AUTHOR_NAME": "Planted",
    "GIT_AUTHOR_EMAIL": "planted@example.invalid",
    "GIT_COMMITTER_NAME": "Planted",
    "GIT_COMMITTER_EMAIL": "planted@example.invalid",
}

#: Variables a platform's process start adds to the environment it is given (macOS's text
#: encoding), which no runner sets.
_PLATFORM_ADDED = frozenset({"__CF_USER_TEXT_ENCODING"})


@pytest.fixture(autouse=True)
def _the_user_database_names_a_planted_home(tmp_path, monkeypatch):
    """The user database names a home under the test's own folder, never made, so the runner's
    real-home check (``run.real_home``) never looks at the real one."""
    entry = pwd.getpwuid

    def planted(uid: int):
        fields = list(entry(uid))
        fields[5] = str(tmp_path / "the-real-home")
        return pwd.struct_passwd(fields)

    monkeypatch.setattr(pwd, "getpwuid", planted)


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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    # Ignored as this tree ignores them: the reports, and run-job's own work folder.
    (root / ".gitignore").write_text("reports/\n/.local/\n")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (root / "tests" / name).write_text("def test_it():\n    pass\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    return root


def _host(root: Path, **kw) -> jobs.Host:
    kw.setdefault("family", "ubuntu-")
    kw.setdefault("path", os.environ.get("PATH", os.defpath))
    return jobs.Host(root=root, **kw)


def _job(job_id: str, *programs: str, **kw) -> Job:
    """A job whose steps each run one Python program; ``argv`` steps may be given whole."""
    steps = kw.pop("steps", None) or tuple(Step((PY, "-c", p)) for p in programs)
    fields = dict(
        id=job_id,
        tiers=("change",),
        inputs=(":(glob)**",),
        external_inputs=(),
        toolchain=(),
        cache=(),
        security="ordinary",
        timeout_min=5,
        command=steps,
    )
    fields.update(kw)
    return Job(**fields)  # type: ignore[arg-type]


def _run(repo: Path, job: Job, tmp_path: Path, shard: int | None = None, **kw) -> jobs.Outcome:
    host = kw.pop("host", None) or _host(repo)
    options = jobs.Options(**kw)
    return jobs.run_one(Catalogue((job,)), job, shard, host, options, tmp_path / "work")


def _record(outcome: jobs.Outcome) -> dict:
    assert outcome.record is not None, outcome.reasons
    return json.loads(outcome.record.read_text())


def _dump_env(out: Path) -> str:
    """A program that writes the environment it was given to *out*."""
    return f"import json, os; json.dump(dict(os.environ), open({str(out)!r}, 'w'))"


def _stubs(tmp_path: Path, *names: str) -> Path:
    """A folder of programs, each exiting 0, standing in for a toolchain's installs."""
    folder = tmp_path / "stubs"
    folder.mkdir(exist_ok=True)
    for name in names:
        program = folder / name
        program.write_text("#!/bin/sh\nexit 0\n")
        program.chmod(0o755)
    return folder


def _partner(repo: Path) -> str:
    """A partner checkout at ``.ci-apps`` with one commit, which the planted tree ignores as
    this tree's own ignore file does; its head."""
    with (repo / ".gitignore").open("a") as ignore:
        ignore.write("/.ci-apps/\n")
    _git(repo, "commit", "-qam", "ignore the partner checkout")
    partner = repo / ".ci-apps"
    partner.mkdir()
    (partner / "README").write_text("the apps\n")
    _git(partner, "init", "-q", "-b", "main")
    _git(partner, "add", "-A")
    _git(partner, "commit", "-q", "-m", "the apps")
    return _git(partner, "rev-parse", "HEAD")


# ── one job ──────────────────────────────────────────────────────────────────────────────────


def test_a_passing_job_leaves_a_success_record_naming_its_head(repo, tmp_path):
    outcome = _run(repo, _job("ok", "pass", "print('second')"), tmp_path)
    assert outcome.code == run.OK, outcome.reasons
    record = _record(outcome)
    assert record["key"] == "ok"
    assert record["result"] == verdict.SUCCESS
    assert record["head_sha"] == _git(repo, "rev-parse", "HEAD")
    # A passing job had no failed test to diagnose, so it carries no rerun.
    assert record["rerun"] is None and record["quarantined"] == []
    assert [s["exit"] for s in record["steps"]] == [0, 0]
    assert record["steps"][1]["argv"] == [PY, "-c", "print('second')"]
    [read] = verdict.from_job_records([outcome.record.read_text()])
    assert read.result == verdict.SUCCESS


def test_a_failing_step_is_red_and_the_steps_after_it_do_not_run(repo, tmp_path):
    marker = tmp_path / "ran"
    job = _job("bad", "raise SystemExit(3)", f"open({str(marker)!r}, 'w')")
    outcome = _run(repo, job, tmp_path)
    assert outcome.code == run.RED
    assert outcome.reasons == ["step 1: the job exited 3"]
    assert not marker.exists()
    record = _record(outcome)
    assert record["result"] == verdict.FAILURE
    assert [s["exit"] for s in record["steps"]] == [3]


def test_a_step_gets_the_workflow_env_then_the_jobs_then_its_own_and_none_of_the_callers(
    repo, tmp_path, monkeypatch
):
    monkeypatch.setenv("CALLERS_OWN", "leaked")
    out = tmp_path / "env.json"
    step = Step(
        (PY, "-c", f"import json, os; json.dump(dict(os.environ), open({str(out)!r}, 'w'))"),
        env=Env({"BOTH": "step", "STEP_ONLY": "1"}),
    )
    job = _job("env", steps=(step,), env=Env({"BOTH": "job", "JOB_ONLY": "1"}))
    assert _run(repo, job, tmp_path).code == run.OK
    seen = json.loads(out.read_text())
    assert "CALLERS_OWN" not in seen
    assert seen["BOTH"] == "step" and seen["JOB_ONLY"] == "1" and seen["STEP_ONLY"] == "1"
    assert seen["UV_PYTHON_PREFERENCE"] == jobs.WORKFLOW_ENV["UV_PYTHON_PREFERENCE"]
    assert seen["HOME"].startswith(str(tmp_path / "work"))


def test_every_step_gets_the_runners_ci_variable_and_never_github_actions(
    repo, tmp_path, monkeypatch
):
    # Every step of a GitHub Actions job has CI=true; the e2e job's Playwright config reads it
    # for its JSON report, which the job's next step reads. GITHUB_ACTIONS says "on GitHub".
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    out = tmp_path / "env.json"
    assert _run(repo, _job("ci", _dump_env(out)), tmp_path).code == run.OK
    seen = json.loads(out.read_text())
    assert seen["CI"] == "true"
    assert "GITHUB_ACTIONS" not in seen


def test_a_steps_env_is_the_runners_the_workflows_the_jobs_and_its_own_and_nothing_else(
    repo, tmp_path
):
    out = tmp_path / "env.json"
    step = Step((PY, "-c", _dump_env(out)), env=Env({"STEP_ONLY": "1"}))
    job = _job("env", steps=(step,), env=Env({"JOB_ONLY": "1"}))
    assert _run(repo, job, tmp_path).code == run.OK
    seen = set(json.loads(out.read_text())) - _PLATFORM_ADDED
    isolation = set(run.job_env(run.Scratch(tmp_path / "unused"), path=""))
    assert seen == isolation | set(jobs.RUNNER_ENV) | set(jobs.WORKFLOW_ENV) | {
        "JOB_ONLY",
        "STEP_ONLY",
    }


def test_the_jobs_time_is_its_cap_less_a_minute(repo, tmp_path, monkeypatch):
    given: list[float] = []

    def fake_run_job(argv, *, timeout, **kw) -> run.Result:
        given.append(timeout)
        return run.Result(run.OK, 0, ())

    monkeypatch.setattr(run, "run_job", fake_run_job)
    assert _run(repo, _job("cap", "pass", timeout_min=5), tmp_path).code == run.OK
    [timeout] = given
    assert 5 * 60 - 60 - 5 < timeout <= 5 * 60 - 60
    # The loader's smallest cap leaves a local run time after the minute it keeps back.
    assert catalogue.MIN_TIMEOUT_MIN * 60 > jobs.MARGIN_S


def test_a_toolchains_caches_managed_pythons_and_venv_are_under_the_work_folder(repo, tmp_path):
    stubs = _stubs(tmp_path, "uv", "npm", "npx", "node")
    out = tmp_path / "env.json"
    toolchain = ("uv-sync", "node", "npm-ci", "chromium")
    job = _job("tools", _dump_env(out), toolchain=toolchain, python="3.12.15")
    host = _host(repo, path=f"{stubs}{os.pathsep}{os.environ['PATH']}")
    outcome = _run(repo, job, tmp_path, host=host)
    assert outcome.code == run.OK, outcome.reasons
    installs = [s["argv"] for s in _record(outcome)["steps"][:3]]
    assert installs == [list(jobs.INSTALL_ARGV[t]) for t in ("uv-sync", "npm-ci", "chromium")]
    seen = json.loads(out.read_text())
    cache = repo / jobs.WORK / "cache"
    for name in (
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "UV_PROJECT_ENVIRONMENT",
        "npm_config_cache",
        "PLAYWRIGHT_BROWSERS_PATH",
    ):
        assert Path(seen[name]).is_relative_to(cache), (name, seen[name])
    assert seen["UV_PYTHON"] == "3.12.15"


@pytest.mark.skipif(shutil.which("uv") is None, reason="runs uv venv, which needs uv on PATH")
def test_a_uv_venv_step_runs_again_over_the_venv_an_earlier_run_left(repo, tmp_path):
    # client's `uv venv .venv-client`: CI's checkout is new each run, a local one is not.
    with (repo / ".gitignore").open("a") as ignore:
        ignore.write(".venv-planted/\n")
    _git(repo, "commit", "-qam", "ignore the planted venv")
    system_python = {"UV_PYTHON": PY, "UV_PYTHON_PREFERENCE": "only-system", "UV_OFFLINE": "1"}
    step = Step(("uv", "venv", "-q", ".venv-planted"), env=Env(system_python))
    job = _job("venv", steps=(step,), toolchain=("uv",))
    first = _run(repo, job, tmp_path / "first")
    assert first.code == run.OK, first.reasons
    again = _run(repo, job, tmp_path / "again")
    assert again.code == run.OK, again.reasons


def test_runner_temp_is_the_jobs_own_temporary_folder(repo, tmp_path):
    out = tmp_path / "where"
    step = Step(
        (PY, "-c", f"import sys; open({str(out)!r}, 'w').write(sys.argv[1])", "{runner_temp}")
    )
    assert _run(repo, _job("tmp", steps=(step,)), tmp_path).code == run.OK
    where = Path(out.read_text())
    assert where == tmp_path / "work" / "jobs" / "tmp" / "tmp"
    assert where.is_dir()


def test_a_job_on_a_dirty_checkout_runs_but_leaves_no_record(repo, tmp_path):
    (repo / "tests" / "test_a.py").write_text("def test_it():\n    assert True\n")
    outcome = _run(repo, _job("dirty", "pass"), tmp_path)
    assert outcome.record is None
    assert outcome.code == run.PARTIAL
    assert "no record: the checkout was not clean" in outcome.reasons[-1]


def test_a_step_that_dirties_the_checkout_leaves_no_record(repo, tmp_path):
    touch = f"open({str(repo / 'new.txt')!r}, 'w')"
    outcome = _run(repo, _job("dirties", touch), tmp_path)
    assert outcome.record is None and outcome.code == run.PARTIAL


def test_a_step_that_commits_leaves_no_record(repo, tmp_path):
    step = Step(("git", "commit", "-q", "--allow-empty", "-m", "moved"), env=Env(_IDENTITY))
    outcome = _run(repo, _job("moves", steps=(step,)), tmp_path)
    assert _git(repo, "status", "--porcelain") == "", "only the head moved"
    assert outcome.record is None and outcome.code == run.PARTIAL, outcome.reasons


def test_a_step_that_cleans_a_dirty_checkout_leaves_no_record(repo, tmp_path):
    (repo / "tests" / "test_a.py").write_text("def test_it():\n    assert True\n")
    outcome = _run(repo, _job("cleans", steps=(Step(("git", "checkout", "--", ".")),)), tmp_path)
    assert _git(repo, "status", "--porcelain") == "", "the step left the checkout clean"
    assert outcome.record is None and outcome.code == run.PARTIAL, outcome.reasons


def test_a_job_whose_partner_checkout_moves_while_it_runs_leaves_no_record(repo, tmp_path):
    _partner(repo)
    step = Step(
        ("git", "-C", ".ci-apps", "commit", "-q", "--allow-empty", "-m", "moved"),
        env=Env(_IDENTITY),
    )
    job = _job("partner", steps=(step,), external_inputs=("apps-partner",))
    outcome = _run(repo, job, tmp_path)
    assert outcome.record is None and outcome.code == run.PARTIAL, outcome.reasons
    assert "partner" in outcome.reasons[-1]


def test_what_the_merge_tiers_jobs_leave_in_the_checkout_keeps_it_clean(tmp_path):
    # This tree's own ignore files, planted: the partner checkout apps-contract reads, and the
    # reports e2e writes under CI, do not make the checkout dirty, or no job of the merge tier
    # after them would leave a record.
    root = tmp_path / "tree"
    for rel in (".gitignore", "web/.gitignore"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text((REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "this tree's ignore files")
    host = _host(root)
    (root / "web" / "planted.ts").write_text("export {}\n")
    assert not jobs._state(host)[1], "the control: a file nothing ignores is seen"
    (root / "web" / "planted.ts").unlink()
    (root / ".ci-apps").mkdir()
    _git(root / ".ci-apps", "init", "-q", "-b", "main")
    (root / "web" / "e2e-report.json").write_text("{}")
    (root / "web" / "blob-report").mkdir()
    (root / "web" / "blob-report" / "report-1.zip").write_bytes(b"blob")
    assert jobs._state(host)[1], _git(root, "status", "--porcelain")


def test_a_job_that_writes_no_junit_it_declares_is_red(repo, tmp_path):
    outcome = _run(repo, _job("nojunit", "pass", junit=("reports/junit.xml",)), tmp_path)
    assert outcome.code == run.RED
    assert outcome.reasons == ["wrote no junit at reports/junit.xml in this run"]
    assert _record(outcome)["result"] == verdict.FAILURE


def test_a_junit_left_by_an_earlier_run_does_not_count(repo, tmp_path):
    report = repo / "reports" / "junit.xml"
    report.parent.mkdir()
    report.write_text("<testsuites/>")
    old = time.time() - 3600
    os.utime(report, (old, old))
    outcome = _run(repo, _job("stale", "pass", junit=("reports/junit.xml",)), tmp_path)
    assert outcome.code == run.RED
    assert "wrote no junit" in outcome.reasons[0]


_QUARANTINED = """<testsuites><testsuite>
<testcase classname="tests.test_a" name="test_flaky">
  <properties><property name="quarantine_issue" value="4000"/></properties>
  <skipped type="pytest.xfail" message="quarantined"/>
</testcase>
<testcase classname="tests.test_a" name="test_plain"/>
<testcase classname="tests.test_a" name="test_quarantined_and_passed">
  <properties><property name="quarantine_issue" value="4001"/></properties>
</testcase>
<testcase classname="tests.test_a" name="test_skipped_and_not_quarantined">
  <skipped type="pytest.skip" message="not on this platform"/>
</testcase>
</testsuite></testsuites>"""


def _writes_junit(text: str) -> str:
    return (
        "import os; os.makedirs('reports', exist_ok=True);"
        f" open('reports/junit.xml', 'w').write({text!r})"
    )


def test_the_record_names_each_quarantined_failure_its_junit_holds(repo, tmp_path):
    job = _job("q", _writes_junit(_QUARANTINED), junit=("reports/junit.xml",))
    outcome = _run(repo, job, tmp_path)
    assert outcome.code == run.OK, outcome.reasons
    # Not the quarantined test that passed, nor the skipped one the quarantine does not mark.
    assert _record(outcome)["quarantined"] == ["tests.test_a::test_flaky"]


def test_a_junit_that_is_not_xml_is_red(repo, tmp_path):
    job = _job("garbled", _writes_junit("not a report"), junit=("reports/junit.xml",))
    outcome = _run(repo, job, tmp_path)
    assert outcome.code == run.RED
    assert outcome.reasons[0].startswith("its junit reports/junit.xml is unreadable"), outcome
    assert _record(outcome)["result"] == verdict.FAILURE


def test_a_pytest_step_out_of_time_lists_its_live_group_before_it_is_stopped(repo, tmp_path, capfd):
    listed: list[int] = []

    def lister(pgid: int) -> list[str]:
        listed.append(pgid)
        return [f"{pgid} python"]

    step = Step((PY, "-c", "import time; time.sleep(60)", "pytest"))
    host = _host(repo, lister=lister)
    outcome = _run(repo, _job("hang", steps=(step,)), tmp_path, host=host, deadline_s=2, grace=0.5)
    assert outcome.code == run.RED
    assert outcome.reasons == ["step 1: the job timed out after 2s"]
    assert len(listed) == 1
    err = capfd.readouterr().err
    assert "possible xdist hang" in err and f"{listed[0]} python" in err


def test_a_step_that_is_not_pytest_out_of_time_lists_nothing(repo, tmp_path, capfd):
    listed: list[int] = []
    step = Step((PY, "-c", "import time; time.sleep(60)"))
    host = _host(repo, lister=lambda pgid: listed.append(pgid) or [])
    outcome = _run(repo, _job("slow", steps=(step,)), tmp_path, host=host, deadline_s=1, grace=0.5)
    [reason] = outcome.reasons
    assert reason.startswith("step 1: the job timed out after "), reason
    assert listed == [] and "possible xdist hang" not in capfd.readouterr().err


def test_a_step_that_starts_late_still_names_the_jobs_budget(repo, tmp_path):
    """The planted delay: the first step takes a second of the job's 5, so the hanging step starts
    with under 4 s left. Its reason names the 5 s the job was given, never what was left of them
    as it started, which read as "after 3.98731s" (or "1.99999s", when a step began a moment
    after the deadline was taken)."""
    job = _job("late", "import time; time.sleep(1)", "import time; time.sleep(60)")
    outcome = _run(repo, job, tmp_path, deadline_s=5, grace=0.2)
    assert outcome.code == run.RED
    assert outcome.reasons == ["step 2: the job timed out after 5s"]


def test_run_job_names_the_budget_it_is_given_not_the_time_it_waited(tmp_path):
    scratch = run.Scratch.create(tmp_path / "scratch")
    result = run.run_job(
        [PY, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        scratch=scratch,
        timeout=1.0,
        path=os.environ.get("PATH", os.defpath),
        grace=0.2,
        budget=7,
    )
    assert result.code == run.RED
    assert result.violations == ("the job timed out after 7s",)


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf"), True])
def test_a_budget_that_is_not_a_positive_number_of_seconds_is_refused_before_anything_runs(
    tmp_path, budget
):
    marker = tmp_path / "ran"
    with pytest.raises(ValueError, match="budget"):
        run.run_job(
            [PY, "-c", f"open({str(marker)!r}, 'w')"],
            cwd=tmp_path,
            scratch=run.Scratch.create(tmp_path / "scratch"),
            timeout=5.0,
            path=os.environ.get("PATH", os.defpath),
            budget=budget,
        )
    assert not marker.exists()


def test_a_placeholder_with_no_value_is_refused_before_the_step_runs(repo, tmp_path):
    marker = tmp_path / "ran"
    step = Step((PY, "-c", f"open({str(marker)!r}, 'w')", "{nope}"))
    with pytest.raises(jobs.Unreadable, match="no value for the placeholder {nope}"):
        _run(repo, _job("unfilled", steps=(step,)), tmp_path)
    assert not marker.exists()


def test_the_jobs_time_is_shared_by_its_steps(repo, tmp_path):
    job = _job("slow", "import time; time.sleep(1.5)", "import time; time.sleep(1.5)")
    outcome = _run(repo, job, tmp_path, deadline_s=2, grace=0.2)
    assert outcome.code == run.RED
    assert outcome.reasons[0].startswith("step 2: the job timed out after ")


#: What keeps a job from running on this machine in either mode, and how its reason names it.
_CANNOT_RUN_IN_EITHER_MODE = [
    pytest.param(
        {"container": "img:1", "script": "x.sh", "command": ()},
        "it runs in the container",
        id="container",
    ),
    pytest.param({"toolchain": ("tmux",)}, "its toolchain needs tmux", id="tmux"),
    pytest.param({"toolchain": ("uv-sync",)}, "its toolchain needs uv", id="uv-sync"),
    pytest.param(
        {"external_inputs": ("apps-partner",)}, "there is no checkout at .ci-apps", id="partner"
    ),
]


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        pytest.param({"platform": "macos-26"}, "it runs on macos-26", id="platform"),
        *_CANNOT_RUN_IN_EITHER_MODE,
    ],
)
def test_a_job_this_machine_cannot_run_is_partial_and_named(repo, tmp_path, kw, why):
    host = _host(repo, path=str(tmp_path / "empty-path"))
    outcome = _run(repo, _job("elsewhere", "pass", **kw), tmp_path, host=host)
    assert outcome.code == run.PARTIAL and not outcome.ran and outcome.record is None
    assert any(why in reason for reason in outcome.reasons), outcome.reasons


@pytest.mark.parametrize("platform", ["ubuntu-24.04", "macos-26"], ids=["on", "off"])
@pytest.mark.parametrize(("kw", "why"), _CANNOT_RUN_IN_EITHER_MODE)
def test_a_working_tree_run_of_a_job_this_machine_cannot_run_is_partial_and_named(
    repo, tmp_path, kw, why, platform
):
    """Only the platform rule gives way in a working-tree run: a container, a missing toolchain
    program and a missing partner checkout still keep the job from running, on its platform or
    off it. Partial, named, none of its steps run, and no record. git is on PATH, so a rule that
    gave way would let the job run and pass, not stop it on a missing git."""
    marker = tmp_path / "ran"
    kw = {"command": (Step((PY, "-c", f"open({str(marker)!r}, 'w')")),), **kw}
    host = _host(repo, path=str(_git_only(tmp_path)))
    job = _job("elsewhere", platform=platform, **kw)
    outcome = _run(repo, job, tmp_path, host=host, working_tree=True)
    assert outcome.code == run.PARTIAL and not outcome.ran and outcome.record is None
    assert outcome.off_platform is None
    assert any(why in reason for reason in outcome.reasons), outcome.reasons
    assert all(reason.startswith("cannot run here: ") for reason in outcome.reasons)
    assert not marker.exists()
    assert not (tmp_path / "work").exists()


def test_no_job_runs_on_a_machine_of_neither_platform_family(repo, tmp_path):
    outcome = _run(repo, _job("anywhere", "pass"), tmp_path, host=_host(repo, family=""))
    assert outcome.code == run.PARTIAL and not outcome.ran
    assert outcome.reasons == [
        "cannot run here: it runs on ubuntu-24.04, and this machine runs neither*"
    ]


@pytest.mark.parametrize(
    ("given", "ran"),
    [
        (["-n", "4"], ["-n", "2"]),
        (["-n4"], ["-n2"]),
        (["--numprocesses", "4"], ["--numprocesses", "2"]),
        (["--numprocesses=4"], ["--numprocesses=2"]),
    ],
)
def test_a_local_worker_count_replaces_every_spelling_of_pytest_n_and_the_record_says_so(
    repo, tmp_path, given, ran
):
    out = tmp_path / "argv"
    program = f"import json, sys; json.dump(sys.argv[1:], open({str(out)!r}, 'w'))"
    step = Step((PY, "-c", program, "pytest", *given, "-q"))
    assert catalogue.pytest_workers(step) == ["4"], "the loader reads this spelling as workers"
    outcome = _run(repo, _job("w", steps=(step,), workers=4), tmp_path, workers=2)
    assert outcome.code == run.OK, outcome.reasons
    assert json.loads(out.read_text()) == ["pytest", *ran, "-q"]
    assert _record(outcome)["workers"] == 2


def test_a_local_worker_count_is_refused_on_github(repo, tmp_path):
    with pytest.raises(jobs.Unreadable, match="local override"):
        _run(repo, _job("w", "pass"), tmp_path, host=_host(repo, on_github=True), workers=2)


def test_workers_env_carries_the_jobs_worker_count(repo, tmp_path):
    out = tmp_path / "count"
    program = f"import os; open({str(out)!r}, 'w').write(os.environ['TOOL_WORKERS'])"
    job = _job("we", program, workers=3, workers_env="TOOL_WORKERS")
    assert _run(repo, job, tmp_path).code == run.OK
    assert out.read_text() == "3"


def test_workers_env_carries_this_machines_core_count_for_auto(repo, tmp_path):
    out = tmp_path / "count"
    program = f"import os; open({str(out)!r}, 'w').write(os.environ['TOOL_WORKERS'])"
    job = _job("we", program, workers="auto", workers_env="TOOL_WORKERS")
    assert _run(repo, job, tmp_path).code == run.OK
    assert out.read_text() == str(os.cpu_count() or 1)


def test_a_sharded_job_run_locally_uses_the_offline_plan_and_says_so(repo, tmp_path):
    out = tmp_path / "plan"
    program = f"import shutil, sys; shutil.copy(sys.argv[1], {str(out)!r})"
    step = Step(
        (PY, "-c", program, "{runner_temp}/shard-plan/plan.json", "--leg", "suite", "{shard}/2")
    )
    outcome = _run(repo, _job("sh", steps=(step,), shards=2), tmp_path, shard=1)
    assert outcome.key == "sh (1/2)"
    assert outcome.code == run.OK, outcome.reasons
    assert _record(outcome)["plan"] == "offline"
    plan = json.loads(out.read_text())["legs"]["suite"]
    assert plan["n"] == 2
    assert sorted(f for bucket in plan["buckets"] for f in bucket) == [
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
    ]


def test_a_sharded_job_on_github_takes_cis_plan_and_writes_no_offline_one(
    repo, tmp_path, monkeypatch
):
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    step = Step((PY, "-c", "pass", "{shard}"))
    host = _host(repo, on_github=True)
    outcome = _run(repo, _job("sh", steps=(step,), shards=2), tmp_path, shard=1, host=host)
    assert _record(outcome)["plan"] == "ci"
    assert not (runner_temp / "shard-plan").exists()


# ── a tier ───────────────────────────────────────────────────────────────────────────────────


def _tier(repo: Path, tmp_path: Path, *tier_jobs: Job, **kw) -> tuple[int, list, verdict.Verdict]:
    host = kw.pop("host", None) or _host(repo)
    options = kw.pop("options", None) or jobs.Options()
    return jobs.run_tier(
        Catalogue(tuple(tier_jobs)), "change", host, options, tmp_path / "work", **kw
    )


def test_a_tier_whose_jobs_all_pass_here_is_ok_and_green(repo, tmp_path):
    code, outcomes, judged = _tier(repo, tmp_path, _job("a", "pass"), _job("b", "pass"))
    assert code == run.OK and judged.green
    assert [o.key for o in outcomes] == ["a", "b"]


def test_a_tier_with_a_job_that_cannot_run_here_is_partial_not_red(repo, tmp_path):
    code, outcomes, judged = _tier(
        repo, tmp_path, _job("a", "pass"), _job("mac", "pass", platform="macos-26")
    )
    assert code == run.PARTIAL
    assert not judged.green
    assert [r.key for r in judged.reasons] == ["mac"]
    assert [o.key for o in outcomes if not o.ran] == ["mac"]


def test_a_tier_with_a_red_job_is_red_and_runs_the_rest(repo, tmp_path):
    code, outcomes, _ = _tier(
        repo, tmp_path, _job("bad", "raise SystemExit(1)"), _job("after", "pass")
    )
    assert code == run.RED
    assert [o.code for o in outcomes] == [run.RED, run.OK]


def test_fail_fast_runs_nothing_after_the_first_red(repo, tmp_path):
    code, outcomes, _ = _tier(
        repo, tmp_path, _job("bad", "raise SystemExit(1)"), _job("after", "pass"), fail_fast=True
    )
    assert code == run.RED
    assert outcomes[1].ran is False and outcomes[1].reasons == ["not run: --fail-fast"]


#: A step that sends its runner SIGINT, as a terminal's Ctrl-C would, and waits to be stopped.
_INTERRUPTS_ITS_RUNNER = (
    "import os, signal, time; os.kill(os.getppid(), signal.SIGINT); time.sleep(60)"
)


def test_an_interrupted_job_is_cancelled_and_the_tier_runs_nothing_after_it(repo, tmp_path):
    marker = tmp_path / "ran"
    code, outcomes, judged = _tier(
        repo,
        tmp_path,
        _job("stopped", _INTERRUPTS_ITS_RUNNER),
        _job("after", f"open({str(marker)!r}, 'w')"),
        options=jobs.Options(grace=0.5),
    )
    assert code == run.PARTIAL
    stopped, after = outcomes
    assert stopped.code == run.PARTIAL and stopped.reasons == ["step 1: interrupted by SIGINT"]
    assert _record(stopped)["result"] == verdict.CANCELLED
    assert after.ran is False and after.reasons == ["not run: interrupted"]
    assert not marker.exists()
    assert not judged.green


#: _INTERRUPTS_ITS_RUNNER, after its git asks for a credential, which makes the job red.
_ASKS_THEN_INTERRUPTS = (
    "import os, signal, subprocess, time;"
    " subprocess.run(['git', 'credential', 'fill'], input='protocol=https\\nhost=example.invalid"
    "\\n\\n', text=True, capture_output=True);"
    " os.kill(os.getppid(), signal.SIGINT); time.sleep(60)"
)


@pytest.mark.parametrize("working_tree", [False, True], ids=["committed tree", "working tree"])
def test_an_interrupted_job_stops_the_tier_even_when_its_git_made_it_red(
    repo, tmp_path, working_tree
):
    marker = tmp_path / "ran"
    code, outcomes, _ = _tier(
        repo,
        tmp_path,
        _job("asked", _ASKS_THEN_INTERRUPTS),
        _job("after", f"open({str(marker)!r}, 'w')"),
        options=jobs.Options(grace=0.5, working_tree=working_tree),
    )
    asked, after = outcomes
    assert asked.code == run.RED, asked.reasons
    assert "step 1: interrupted by SIGINT" in asked.reasons
    assert any("the tripwire" in reason for reason in asked.reasons), asked.reasons
    assert after.ran is False and after.reasons == ["not run: interrupted"]
    assert not marker.exists()
    assert code == run.RED


def test_each_record_of_a_tier_names_only_the_partner_revisions_its_job_reads(repo, tmp_path):
    sha = _partner(repo)
    code, outcomes, judged = _tier(
        repo,
        tmp_path,
        _job("plain", "pass"),
        _job("partner", "pass", external_inputs=("apps-partner",)),
    )
    assert code == run.OK and judged.green, (outcomes, judged)
    plain, partner = (_record(o) for o in outcomes)
    assert plain["external_revisions"] == {}
    assert partner["external_revisions"] == {"apps-partner": sha}
    alone = _run(repo, _job("plain", "pass"), tmp_path / "alone")
    assert _record(alone)["external_revisions"] == plain["external_revisions"]


def test_a_job_env_naming_a_runner_variable_is_refused_before_its_installs_run(repo, tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    marker = tmp_path / "installed"
    (stubs / "uv").write_text(f'#!/bin/sh\necho "$*" >> "{marker}"\n')
    (stubs / "uv").chmod(0o755)
    host = _host(repo, path=f"{stubs}{os.pathsep}{os.environ['PATH']}")
    job = _job("owned", "pass", toolchain=("uv-sync",), env=Env({"HOME": str(tmp_path)}))
    with pytest.raises(jobs.Unreadable, match="owned: its env sets HOME, which the runner owns"):
        _run(repo, job, tmp_path, host=host)
    assert not marker.exists(), "an install ran for a job that was refused"


def test_a_partner_that_moves_during_a_tier_makes_it_red_though_every_job_passed(repo, tmp_path):
    # The first job reads no partner, so its own record stands; the second reads a partner
    # head the tier did not start on, so its record is not the candidate's.
    before = _partner(repo)
    moves = Step(
        ("git", "-C", ".ci-apps", "commit", "-q", "--allow-empty", "-m", "moved"),
        env=Env(_IDENTITY),
    )
    code, outcomes, judged = _tier(
        repo,
        tmp_path,
        _job("moves", steps=(moves,)),
        _job("reads", "pass", external_inputs=("apps-partner",)),
    )
    assert [o.code for o in outcomes] == [run.OK, run.OK], [o.reasons for o in outcomes]
    assert _record(outcomes[1])["external_revisions"]["apps-partner"] != before
    assert [(r.key, r.kind) for r in judged.reasons] == [("reads", "stale partner")]
    assert code == run.RED


def test_a_tier_with_no_job_is_input_it_cannot_run(repo, tmp_path):
    with pytest.raises(jobs.Unreadable, match="the weekly tier has no job"):
        jobs.run_tier(
            Catalogue((_job("a", "pass"),)), "weekly", _host(repo), jobs.Options(), tmp_path / "w"
        )


def test_a_job_env_naming_a_runner_variable_is_refused_before_any_job_runs(repo, tmp_path):
    marker = tmp_path / "ran"
    first = _job("first", f"open({str(marker)!r}, 'w')")
    owned = _job("owned", "pass", env=Env({"HOME": str(tmp_path)}))
    with pytest.raises(jobs.Unreadable, match="owned: its env sets HOME, which the runner owns"):
        _tier(repo, tmp_path, first, owned)
    assert not marker.exists()


def test_every_shard_of_a_sharded_job_runs_and_is_planned(repo, tmp_path):
    step = Step((PY, "-c", "pass", "{shard}"))
    code, outcomes, judged = _tier(repo, tmp_path, _job("s", steps=(step,), shards=2))
    assert code == run.OK and judged.green
    assert [o.key for o in outcomes] == ["s (1/2)", "s (2/2)"]


def test_jobs_run_after_the_jobs_their_after_names():
    first, second = _job("second", "pass", after=("first",)), _job("first", "pass")
    assert [j.id for j in jobs.ordered([first, second])] == ["first", "second"]


def test_an_after_cycle_is_refused():
    a, b = _job("a", "pass", after=("b",)), _job("b", "pass", after=("a",))
    with pytest.raises(jobs.Unreadable, match="cycle"):
        jobs.ordered([a, b])


def test_changed_runs_every_job_when_a_path_is_in_no_jobs_inputs(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "elsewhere.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a change no input covers")
    tests_only = _job("t", "pass", inputs=("tests/",))
    code, outcomes, _ = jobs.run_tier(
        Catalogue((tests_only,)),
        "change",
        _host(repo),
        jobs.Options(base=base),
        tmp_path / "work",
        changed=True,
    )
    assert [o.key for o in outcomes] == ["t"] and code == run.OK


def test_changed_leaves_out_a_job_whose_inputs_the_diff_does_not_touch(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "test_a.py").write_text("def test_it():\n    assert 1\n")
    _git(repo, "commit", "-q", "-am", "a test change")
    tests_job = _job("t", "pass", inputs=("tests/",))
    docs_job = _job("d", "pass", inputs=("docs/",))
    code, outcomes, judged = jobs.run_tier(
        Catalogue((tests_job, docs_job)),
        "change",
        _host(repo),
        jobs.Options(base=base),
        tmp_path / "work",
        changed=True,
    )
    assert [o.key for o in outcomes] == ["t"] and code == run.OK and judged.green


def test_changed_runs_the_job_a_file_moved_out_of(repo, tmp_path):
    """A move is two changes: the job whose inputs lost the file runs, and so does the job whose
    inputs gained it. A diff that reads the move as a rename names only where the file went."""
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "notes").mkdir()
    _git(repo, "mv", "tests/test_a.py", "notes/test_a.py")
    _git(repo, "commit", "-q", "-m", "a test moved into the notes")
    tests_job = _job("t", "pass", inputs=("tests/",))
    docs_job = _job("d", "pass", inputs=("notes/",))
    code, outcomes, judged = jobs.run_tier(
        Catalogue((tests_job, docs_job)),
        "change",
        _host(repo),
        jobs.Options(base=base),
        tmp_path / "work",
        changed=True,
    )
    assert [o.key for o in outcomes] == ["t", "d"] and code == run.OK and judged.green


def test_changed_reads_a_path_git_would_quote_as_the_path_itself(repo, tmp_path, capsys):
    """git quotes a path with a byte outside printable ASCII unless it is asked for NUL-separated
    names; a quoted name is no path in the repository, so it would plan the whole tier."""
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "notes").mkdir()
    (repo / "notes" / "naïve.md").write_text("prose\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a note with an accent in its name")
    tests_job = _job("t", "pass", inputs=("tests/",))
    docs_job = _job("d", "pass", inputs=("notes/",))
    code, outcomes, _ = jobs.run_tier(
        Catalogue((tests_job, docs_job)),
        "change",
        _host(repo),
        jobs.Options(base=base),
        tmp_path / "work",
        changed=True,
    )
    assert [o.key for o in outcomes] == ["d"] and code == run.OK, capsys.readouterr().err


def test_changed_plans_with_the_overrides_it_is_given(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "test_a.py").write_text("def test_it():\n    assert 3\n")
    _git(repo, "commit", "-q", "-am", "a test change")
    tests_job = _job("t", "pass", inputs=("tests/",))
    docs_job = _job("d", "pass", inputs=("docs/",))
    whole = catalogue.Override(("tests/",), (), True, "a planted override of the whole tier")
    code, outcomes, _ = jobs.run_tier(
        Catalogue((tests_job, docs_job)),
        "change",
        _host(repo),
        jobs.Options(base=base),
        tmp_path / "work",
        changed=True,
        overrides=(whole,),
    )
    assert [o.key for o in outcomes] == ["t", "d"] and code == run.OK


def test_changed_never_leaves_out_a_security_job(repo, tmp_path):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "test_a.py").write_text("def test_it():\n    assert 2\n")
    _git(repo, "commit", "-q", "-am", "a test change")
    tests_job = _job("t", "pass", inputs=("tests/",))
    docs_job = _job("d", "pass", inputs=("docs/",))
    secure = _job("s", "pass", inputs=("docs/",), security="security")
    code, outcomes, _ = jobs.run_tier(
        Catalogue((tests_job, docs_job, secure)),
        "change",
        _host(repo),
        jobs.Options(base=base),
        tmp_path / "work",
        changed=True,
    )
    assert [o.key for o in outcomes] == ["t", "s"] and code == run.OK


# ── the command line ─────────────────────────────────────────────────────────────────────────

#: The platform this machine's jobs run on, so a planted job could run here.
_HERE = "macos-26" if sys.platform == "darwin" else "ubuntu-24.04"

#: A catalogue whose own jobs, plain and sharded, could run on this machine, so a row that
#: refuses one of them is refused by the rule it names, not by the platform.
_PLANTED = f"""
[tier.merge]
jobs = ["rails", "python-suite", "python-suite-macos"]

[[job]]
id = "rails"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
timeout_min = 5
command = [{{ argv = ["python3", "-c", "pass"] }}]

[[job]]
id = "python-suite"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = []
cache = []
security = "security"
timeout_min = 5
command = [{{ argv = ["python3", "-c", "pass"] }}]

[[job]]
id = "python-suite-macos"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = []
cache = []
security = "security"
platform = "macos-26"
timeout_min = 5
command = [{{ argv = ["python3", "-c", "pass"] }}]

[[job]]
id = "plain"
tiers = ["change"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
platform = "{_HERE}"
timeout_min = 5
command = [{{ argv = ["python3", "-c", "pass"] }}]

[[job]]
id = "sharded"
tiers = ["change"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
platform = "{_HERE}"
timeout_min = 5
shards = 4
command = [{{ argv = ["python3", "-c", "pass", "{{shard}}"] }}]
"""

_SHARDS = "sharded runs in 4 shards: give --shard i/4"


@pytest.mark.parametrize(
    ("argv", "why"),
    [
        (["run-job", "no-such-job"], "'no-such-job' is no job of the catalogue"),
        (["run-job", "plain", "--shard", "1/2"], "plain is not sharded, so it takes no --shard"),
        (["run-job", "sharded"], _SHARDS),
        (["run-job", "sharded", "--shard", "5/4"], _SHARDS),
        (["run-job", "sharded", "--shard", "0/4"], _SHARDS),
        (["run-job", "sharded", "--shard", "1/3"], _SHARDS),
        (["run-job", "plain", "--workers", "0"], "--workers is a whole number from 1"),
        (["run-job", "sharded", "--shard", "1/4", "--shard", "2/4"], "--shard: given twice"),
        (["run-job", "plain", "--workers", "2", "--workers", "8"], "--workers: given twice"),
        (["run-job", "plain", "--base", "HEAD", "--base", "HEAD~1"], "--base: given twice"),
        (["run", "nightly"], "'nightly' is not a tier"),
        (["run", "weekly"], "the weekly tier has no job"),
        (["run", "change", "--changed"], "areas.toml"),
        (["run-job"], "the following arguments are required: job"),
    ],
)
def test_input_it_cannot_run_exits_partial_and_says_why(argv, why, tmp_path, capsys):
    from tools.ci.__main__ import main

    planted = tmp_path / "catalogue.toml"
    planted.write_text(_PLANTED, encoding="utf-8")
    catalogue.load(_PLANTED, "planted")  # the planted catalogue loads
    out = tmp_path / "out"
    assert main([*argv, "--catalogue", str(planted), "--out", str(out)]) == run.PARTIAL
    assert why in capsys.readouterr().err
    assert not out.exists(), "nothing ran"


def test_run_changed_reads_the_overrides_beside_the_catalogue(tmp_path, capsys):
    from tools.ci.__main__ import main

    planted = tmp_path / "catalogue.toml"
    planted.write_text(_PLANTED, encoding="utf-8")
    (tmp_path / "areas.toml").write_text(
        '[[override]]\npaths = ["docs/"]\njobs = ["static"]\nreason = "a job it lacks"\n'
    )
    out = tmp_path / "out"
    argv = ["run", "change", "--changed", "--catalogue", str(planted), "--out", str(out)]
    assert main(argv) == run.PARTIAL
    assert "names static, which is no job of the catalogue" in capsys.readouterr().err
    assert not out.exists(), "nothing ran"


@pytest.mark.parametrize("command", [["run-job", "plain"], ["run", "change"]])
@pytest.mark.parametrize("entry", [None, ""], ids=["no entry", "an empty home"])
def test_an_account_whose_user_database_names_no_home_runs_no_job_and_exits_partial(
    command, entry, tmp_path, monkeypatch, capsys
):
    """The runner can't tell where the real home is, so no job can run here: that is said in one
    line and exits 2, before any step is announced or any scratch folder made."""
    from tools.ci.__main__ import main

    def no_entry(uid: int):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    if entry is None:
        monkeypatch.setattr(pwd, "getpwuid", no_entry)
    else:
        named = pwd.getpwuid

        def empty(uid: int):
            fields = list(named(uid))
            fields[5] = entry
            return pwd.struct_passwd(fields)

        monkeypatch.setattr(pwd, "getpwuid", empty)
    monkeypatch.chdir(tmp_path)  # a runner that took a relative home as it stands looks here
    planted = tmp_path / "catalogue.toml"
    planted.write_text(_PLANTED, encoding="utf-8")
    out = tmp_path / "out"
    assert main([*command, "--catalogue", str(planted), "--out", str(out)]) == run.PARTIAL
    err = capsys.readouterr().err
    why = "has no entry in the user database" if entry is None else "names '' as user id"
    assert f"{command[0]}: " in err and why in err and "runs no job" in err, err
    assert "step 1" not in err, err
    assert not out.exists(), "nothing ran"


def test_a_repeated_catalogue_or_out_is_refused(tmp_path, capsys):
    from tools.ci.__main__ import main

    planted = tmp_path / "catalogue.toml"
    planted.write_text(_PLANTED, encoding="utf-8")
    twice = ["--catalogue", str(planted), "--catalogue", str(planted)]
    assert main(["run-job", "plain", *twice, "--out", str(tmp_path / "o")]) == run.PARTIAL
    assert "--catalogue: given twice" in capsys.readouterr().err
    out = ["--out", str(tmp_path / "a"), "--out", str(tmp_path / "b")]
    assert main(["run", "change", "--catalogue", str(planted), *out]) == run.PARTIAL
    assert "--out: given twice" in capsys.readouterr().err


def test_run_job_takes_no_changed_option(capsys):
    from tools.ci.__main__ import main

    assert main(["run-job", "rails", "--changed"]) == run.PARTIAL
    assert "unrecognized arguments: --changed" in capsys.readouterr().err


def test_make_check_runs_the_change_tier_on_the_working_tree_and_make_gate_the_merge_tier():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipes = dict(re.findall(r"^(check|gate):\n\t(.+)$", makefile, flags=re.MULTILINE))
    assert recipes == {
        "check": "$(PYTHON) -m tools.ci run change --working-tree",
        "gate": "$(PYTHON) -m tools.ci run merge",
    }


# ── a working-tree run: the same steps, no record, the steps' result ─────────────────────────


def _git_only(tmp_path: Path) -> Path:
    """A PATH folder holding git and nothing else, so a toolchain program is missing from it
    on any machine while the runner can still read the checkout."""
    folder = tmp_path / "git-only"
    folder.mkdir(exist_ok=True)
    found = shutil.which("git")
    assert found, "the test needs git"
    link = folder / "git"
    if not link.exists():
        link.symlink_to(found)
    return folder


def _dirty(repo: Path) -> None:
    """A planted uncommitted change: one modified file and one untracked file."""
    (repo / "tests" / "test_a.py").write_text("def test_it():\n    assert True\n")
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    pass\n")


def test_a_working_tree_run_on_a_dirty_tree_passes_and_writes_no_record(repo, tmp_path):
    _dirty(repo)
    outcome = _run(repo, _job("wt", "pass"), tmp_path, working_tree=True)
    assert outcome.code == run.OK, outcome.reasons
    assert outcome.record is None
    assert not (tmp_path / "work" / "records").exists()
    assert any("working tree" in r for r in outcome.reasons)


def test_a_working_tree_run_runs_the_uncommitted_change(repo, tmp_path):
    _dirty(repo)
    seen = tmp_path / "seen"
    reader = f"open({str(seen)!r}, 'w').write(open('tests/test_a.py').read())"
    assert _run(repo, _job("wt", reader), tmp_path, working_tree=True).code == run.OK
    assert "assert True" in seen.read_text()


def test_a_working_tree_run_with_a_red_step_is_red(repo, tmp_path):
    _dirty(repo)
    outcome = _run(repo, _job("wt", "raise SystemExit(4)"), tmp_path, working_tree=True)
    assert outcome.code == run.RED and outcome.record is None
    assert "step 1: the job exited 4" in outcome.reasons


def test_a_working_tree_run_of_a_job_missing_a_toolchain_program_is_partial(repo, tmp_path):
    host = _host(repo, path=str(tmp_path / "empty-path"))
    job = _job("needs-tmux", "pass", toolchain=("tmux",))
    outcome = _run(repo, job, tmp_path, host=host, working_tree=True)
    assert outcome.code == run.PARTIAL and not outcome.ran
    assert outcome.reasons == ["cannot run here: its toolchain needs tmux, which is not on PATH"]


@pytest.mark.parametrize(("program", "code"), [("pass", run.OK), ("raise SystemExit(3)", run.RED)])
def test_a_working_tree_run_runs_a_job_of_another_platform_and_exits_by_its_steps(
    repo, tmp_path, program, code
):
    _dirty(repo)
    job = _job("mac", program, platform="macos-26")
    outcome = _run(repo, job, tmp_path, working_tree=True)
    assert outcome.ran and outcome.code == code
    assert outcome.off_platform == "macos-26"
    assert any("off its platform" in r and "never CI's evidence" in r for r in outcome.reasons)


@pytest.mark.parametrize("platform", ["ubuntu-24.04", "macos-26"], ids=["on", "off"])
def test_a_working_tree_run_never_writes_a_record_on_its_platform_or_off_it(
    repo, tmp_path, platform
):
    """Clean tree, passing steps: the one case a committed-tree run would record. Still none."""
    outcome = _run(repo, _job("wt", "pass", platform=platform), tmp_path, working_tree=True)
    assert outcome.code == run.OK, outcome.reasons
    assert outcome.record is None
    assert not (tmp_path / "work" / "records").exists()


def test_the_committed_tree_mode_still_refuses_a_job_of_another_platform(repo, tmp_path):
    marker = tmp_path / "ran"
    job = _job("mac", f"open({str(marker)!r}, 'w')", platform="macos-26")
    outcome = _run(repo, job, tmp_path)
    assert outcome.code == run.PARTIAL and not outcome.ran and outcome.record is None
    assert outcome.off_platform is None
    assert not marker.exists(), "an off-platform job ran in the mode that records"
    assert outcome.reasons == [
        "cannot run here: it runs on macos-26, and this machine runs ubuntu-*"
    ]


def test_the_summary_names_each_job_that_ran_off_its_platform(repo, tmp_path, capsys):
    options = jobs.Options(working_tree=True)
    code, outcomes, _ = _tier(
        repo,
        tmp_path,
        _job("here", "pass"),
        _job("mac", "pass", platform="macos-26"),
        options=options,
    )
    assert code == run.OK
    jobs._say(outcomes)
    out = capsys.readouterr().out
    assert "ran off their platform family: mac (macos-26 in CI)" in out
    assert "here (" not in out
    assert "A local result is never CI's evidence" in out


def test_a_working_tree_run_still_reds_a_missing_junit(repo, tmp_path):
    _dirty(repo)
    job = _job("wt", "pass", junit=("reports/junit.xml",))
    outcome = _run(repo, job, tmp_path, working_tree=True)
    assert outcome.code == run.RED
    assert "wrote no junit at reports/junit.xml in this run" in outcome.reasons


def test_a_working_tree_run_is_refused_on_github(repo, tmp_path):
    with pytest.raises(jobs.Unreadable, match="--working-tree is a local mode"):
        _run(
            repo, _job("wt", "pass"), tmp_path, host=_host(repo, on_github=True), working_tree=True
        )


def test_a_working_tree_tier_is_ok_on_a_dirty_tree_when_every_job_passes(repo, tmp_path):
    _dirty(repo)
    options = jobs.Options(working_tree=True)
    code, outcomes, _ = _tier(repo, tmp_path, _job("a", "pass"), _job("b", "pass"), options=options)
    assert code == run.OK
    assert all(o.record is None for o in outcomes)


def test_a_working_tree_tier_is_red_on_a_red_job_and_partial_only_for_one_that_cannot_run(
    repo, tmp_path
):
    _dirty(repo)
    options = jobs.Options(working_tree=True)
    red, _, _ = _tier(
        repo,
        tmp_path / "r",
        _job("bad", "raise SystemExit(1)"),
        _job("ok", "pass"),
        options=options,
    )
    assert red == run.RED
    partial, _, _ = _tier(
        repo,
        tmp_path / "p",
        _job("ok", "pass"),
        _job("needs-tmux", "pass", toolchain=("tmux",)),
        host=_host(repo, path=str(_git_only(tmp_path))),
        options=options,
    )
    assert partial == run.PARTIAL
    # A red and one that cannot run here: red wins, as in the committed-tree mode, so a red job
    # is never reported as one that could not run.
    both, outcomes, _ = _tier(
        repo,
        tmp_path / "b",
        _job("needs-tmux", "pass", toolchain=("tmux",)),
        _job("bad", "raise SystemExit(1)"),
        host=_host(repo, path=str(_git_only(tmp_path))),
        options=options,
    )
    assert [o.code for o in outcomes] == [run.PARTIAL, run.RED]
    assert both == run.RED


def test_without_the_mode_a_dirty_tree_still_leaves_no_record_and_is_partial(repo, tmp_path):
    """The control: the committed-tree mode keeps refusing to call a dirty run evidence."""
    _dirty(repo)
    code, outcomes, _ = _tier(repo, tmp_path, _job("a", "pass"))
    assert code == run.PARTIAL and outcomes[0].record is None


def test_changed_on_a_working_tree_sees_uncommitted_and_untracked_paths(repo, tmp_path):
    """A working-tree plan reads what the tree holds: a modified file and an untracked one, and
    no file git ignores (``reports/`` is ignored, so the job reading it is left out)."""
    base = _git(repo, "rev-parse", "HEAD")
    with (repo / "pyproject.toml").open("a") as project_file:
        project_file.write("# an uncommitted change\n")
    (repo / "notes").mkdir()
    (repo / "notes" / "new.md").write_text("an untracked page\n")
    (repo / "reports").mkdir()
    (repo / "reports" / "junit.xml").write_text("<testsuite/>\n")
    assert _git(repo, "check-ignore", "reports/junit.xml") == "reports/junit.xml"
    planned = (
        _job("n", "pass", inputs=("notes/",)),
        _job("p", "pass", inputs=("pyproject.toml",)),
        _job("r", "pass", inputs=("reports/",)),
        _job("t", "pass", inputs=("tests/",)),
    )
    code, outcomes, _ = jobs.run_tier(
        Catalogue(planned),
        "change",
        _host(repo),
        jobs.Options(base=base, working_tree=True),
        tmp_path / "work",
        changed=True,
    )
    assert [o.key for o in outcomes] == ["n", "p"] and code == run.OK


def _given_twice() -> list:
    """Each option of the two verbs, as ``(verb, option)``."""
    shared = ("--catalogue", "--base", "--workers", "--out", "--working-tree")
    rows = [(verb, option) for verb in ("run-job", "run") for option in shared]
    rows += [("run-job", "--shard"), ("run", "--changed"), ("run", "--fail-fast")]
    return [pytest.param(verb, option, id=f"{verb} {option}") for verb, option in rows]


@pytest.mark.parametrize(("verb", "option"), _given_twice())
def test_every_option_of_the_two_verbs_given_twice_is_refused(verb, option, tmp_path, capsys):
    """Refused as the line is read, never last-wins. The catalogue named is not there, so a line
    the parser took would stop on reading it, not on the repeat, and no job could run."""
    from tools.ci.__main__ import main

    values = {
        "--catalogue": str(tmp_path / "no-catalogue.toml"),
        "--base": "HEAD",
        "--workers": "2",
        "--out": str(tmp_path / "out"),
        "--shard": "1/4",
    }
    once = [option, values[option]] if option in values else [option]
    rest = [
        word for name in ("--catalogue", "--out") if name != option for word in (name, values[name])
    ]
    target = {"run-job": "sharded", "run": "change"}[verb]
    assert main([verb, target, *once, *once, *rest]) == run.PARTIAL
    assert f"argument {option}: given twice" in capsys.readouterr().err
    assert not (tmp_path / "out").exists(), "nothing ran"


@pytest.mark.parametrize(
    "argv",
    [
        ["run-job", "plain", "--working"],
        ["run-job", "plain", "--wor", "2"],
        ["run-job", "sharded", "--sha", "1/4"],
        ["run", "change", "--work", "2"],
        ["run", "change", "--change"],
        ["run", "change", "--fail"],
    ],
    ids=" ".join,
)
def test_no_prefix_of_an_option_stands_in_for_it(argv, tmp_path, capsys):
    """Each option is spelled in full: ``--working`` is not ``--working-tree``, so a later option
    can never change what a short spelling meant. The catalogue named is not there, so a line
    the parser took would stop on reading it, and no job could run."""
    from tools.ci.__main__ import main

    out = tmp_path / "out"
    safe = ["--catalogue", str(tmp_path / "no-catalogue.toml"), "--out", str(out)]
    assert main([*argv, *safe]) == run.PARTIAL
    assert f"unrecognized arguments: {' '.join(argv[2:])}" in capsys.readouterr().err
    assert not out.exists(), "nothing ran"


def test_a_working_tree_run_of_a_job_of_another_platform_missing_a_program_runs_nothing(
    repo, tmp_path
):
    """Off its platform it runs only when every toolchain program is here; else it is "cannot
    run here", naming the program, and none of its steps runs."""
    marker = tmp_path / "ran"
    job = _job("mac", f"open({str(marker)!r}, 'w')", platform="macos-26", toolchain=("tmux",))
    host = _host(repo, path=str(_git_only(tmp_path)))
    outcome = _run(repo, job, tmp_path, host=host, working_tree=True)
    assert outcome.code == run.PARTIAL and not outcome.ran and outcome.record is None
    assert outcome.reasons == ["cannot run here: its toolchain needs tmux, which is not on PATH"]
    assert not marker.exists()


def test_an_interrupted_working_tree_run_is_partial_and_runs_nothing_after_it(repo, tmp_path):
    _dirty(repo)
    marker = tmp_path / "ran"
    code, outcomes, judged = _tier(
        repo,
        tmp_path,
        _job("stopped", _INTERRUPTS_ITS_RUNNER),
        _job("after", f"open({str(marker)!r}, 'w')"),
        options=jobs.Options(grace=0.5, working_tree=True),
    )
    assert code == run.PARTIAL
    stopped, after = outcomes
    assert stopped.code == run.PARTIAL and stopped.interrupted and stopped.record is None
    assert after.ran is False and after.reasons == ["not run: interrupted"]
    assert not marker.exists()
    assert not judged.green
    assert not (tmp_path / "work" / "records").exists()


def test_a_working_tree_tier_judges_nothing_green(repo, tmp_path):
    """Every job passed on a clean tree, the one case a committed-tree tier calls green: the
    working-tree tier exits by its steps and still judges nothing."""
    options = jobs.Options(working_tree=True)
    code, outcomes, judged = _tier(repo, tmp_path, _job("a", "pass"), options=options)
    assert code == run.OK and not judged.green
    assert [r.kind for r in judged.reasons] == ["working tree"]
    assert not (tmp_path / "work" / "records").exists()


def test_changed_in_the_committed_tree_mode_reads_no_untracked_path(repo, tmp_path):
    """The control for the working-tree plan: an untracked file is no change to the committed
    tree, so the plan is the one a change of nothing makes (every job)."""
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "notes").mkdir()
    (repo / "notes" / "new.md").write_text("an untracked page\n")
    notes = _job("n", "pass", inputs=("notes/",))
    tests_job = _job("t", "pass", inputs=("tests/",))
    _, outcomes, _ = jobs.run_tier(
        Catalogue((notes, tests_job)),
        "change",
        _host(repo),
        jobs.Options(base=base),
        tmp_path / "work",
        changed=True,
    )
    assert [o.key for o in outcomes] == ["n", "t"]


# ── the two verbs with --working-tree, from the command line, on a planted checkout ──────────

_THERE = "ubuntu-24.04" if sys.platform == "darwin" else "macos-26"

#: The planted catalogue, with a job of the other platform family in the change tier, and a red
#: job and one needing a program in a tier ``run change`` does not run.
_PLANTED_WT = _PLANTED + f"""
[[job]]
id = "there"
tiers = ["change"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
platform = "{_THERE}"
timeout_min = 5
command = [{{ argv = ["python3", "-c", "pass"] }}]

[[job]]
id = "red"
tiers = ["breadth"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = []
cache = []
security = "ordinary"
platform = "{_HERE}"
timeout_min = 5
command = [{{ argv = ["python3", "-c", "raise SystemExit(3)"] }}]

[[job]]
id = "needs-tmux"
tiers = ["breadth"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["tmux"]
cache = []
security = "ordinary"
platform = "{_THERE}"
timeout_min = 5
command = [{{ argv = ["python3", "-c", "pass"] }}]
"""


@pytest.fixture
def planted_cli(repo, tmp_path, monkeypatch):
    """The command line's root is the planted checkout, dirty, and its catalogue the planted
    one; the run's folder is under the test's own."""
    catalogue.load(_PLANTED_WT, "planted")  # the planted catalogue loads
    monkeypatch.setattr(catalogue, "REPO", repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _dirty(repo)
    planted = tmp_path / "catalogue.toml"
    planted.write_text(_PLANTED_WT, encoding="utf-8")
    return ["--catalogue", str(planted), "--out", str(tmp_path / "out")]


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["run-job", "plain"], run.OK),
        (["run-job", "there"], run.OK),
        (["run-job", "red"], run.RED),
        (["run", "change"], run.OK),
    ],
)
def test_the_verbs_exit_by_the_steps_on_a_dirty_tree_and_write_no_record(
    argv, code, planted_cli, tmp_path, capsys
):
    from tools.ci.__main__ import main

    assert main([*argv, "--working-tree", *planted_cli]) == code
    out = capsys.readouterr().out
    assert not (tmp_path / "out" / "records").exists()
    assert "(record " not in out and "### Verdict" not in out


def test_run_on_the_working_tree_names_the_off_platform_job_and_claims_no_evidence(
    planted_cli, capsys
):
    from tools.ci.__main__ import main

    assert main(["run", "change", "--working-tree", *planted_cli]) == run.OK
    out = capsys.readouterr().out
    assert f"ran off their platform family: there ({_THERE} in CI)" in out
    assert "A local result is never CI's evidence" in out
    assert "no record was written, so this run is no evidence for a commit" in out
    assert out.rstrip().splitlines()[-1] == "ok"


def test_run_job_on_the_working_tree_missing_a_program_exits_partial_naming_it(
    planted_cli, tmp_path, monkeypatch, capsys
):
    from tools.ci.__main__ import main

    monkeypatch.setenv("PATH", str(_git_only(tmp_path)))
    assert main(["run-job", "needs-tmux", "--working-tree", *planted_cli]) == run.PARTIAL
    out = capsys.readouterr().out
    assert "its toolchain needs tmux, which is not on PATH" in out
    assert "not proven here: needs-tmux" in out


@pytest.mark.parametrize(
    "verb",
    [["run-job", "plain"], ["run-job", "there"], ["run-job", "needs-tmux"], ["run", "change"]],
)
def test_the_working_tree_mode_is_refused_under_github_actions(
    verb, planted_cli, tmp_path, monkeypatch, capsys
):
    """Refused as the input is read, before any job is planned: even a job that could not run
    here anyway (needs-tmux, with no tmux on PATH) is refused for the mode, not skipped."""
    from tools.ci.__main__ import main

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("PATH", str(_git_only(tmp_path)))
    assert main([*verb, "--working-tree", *planted_cli]) == run.PARTIAL
    err = capsys.readouterr().err
    assert "--working-tree is a local mode" in err and "step 1" not in err, err
    assert not (tmp_path / "out").exists(), "nothing ran"
