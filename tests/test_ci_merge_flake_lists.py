"""merge.yml gives the verdict's flake rule its two lists on a pull request's run and a queue
commit's (0.4.0 A1: the queue takes the pull request's flake rule), and only there: the change's
paths (``changes`` writes them, ``ci-changes``) and the run's jobs on the attempt (``results``
lists them). Each step's own shell runs here against a planted test merge or queue commit and
stand-ins for ``gh`` and main's verdict.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.ci import verdict

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "merge.yml").read_text())
CHANGED_STEP = "the pull request's changed paths, for the flake rule"
VERDICT_STEP = "the verdict over the plan's keys and the run's records"


def _step(job: str, name: str) -> dict[str, Any]:
    [step] = [s for s in WORKFLOW["jobs"][job]["steps"] if s.get("name") == name]
    return step


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
def test_merge(tmp_path: Path) -> tuple[Path, str]:
    """A one-deep checkout of a pull request's test merge, as actions/checkout leaves it, with the
    origin it came from: main changed a file, and the pull request edited one and renamed one."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    (src / "docs").mkdir()
    (src / "docs" / "a.md").write_text("a\n")
    (src / "tests").mkdir()
    (src / "tests" / "test_old.py").write_text("def test_x():\n    pass\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "base")
    _git(src, "switch", "-q", "-c", "pr")
    (src / "docs" / "a.md").write_text("a, edited\n")
    _git(src, "mv", "tests/test_old.py", "tests/test_new.py")
    _git(src, "commit", "-q", "-am", "the pull request")
    head = _git(src, "rev-parse", "HEAD")
    _git(src, "switch", "-q", "main")
    (src / "README").write_text("main moved on\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "main moves on")
    _git(src, "switch", "-q", "-c", "merge")
    _git(src, "merge", "-q", "--no-ff", "-m", "the test merge", "pr")
    _git(src, "switch", "-q", "main")
    _git(src, "config", "uploadpack.allowAnySHA1InWant", "true")
    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--branch", "merge", f"file://{src}", str(work)],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return work, head


@pytest.fixture
def queue_commit(tmp_path: Path) -> tuple[Path, str]:
    """A one-deep checkout of a queue commit: the pull request's change squashed as one commit on
    the commit the queue built it on, which main has moved past since the pull request branched."""
    src = tmp_path / "queue-src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    (src / "docs").mkdir()
    (src / "docs" / "a.md").write_text("a\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "base")
    (src / "README").write_text("main moved on\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "the entry ahead")
    built_on = _git(src, "rev-parse", "HEAD")
    _git(src, "switch", "-q", "-c", "queue")
    (src / "docs" / "a.md").write_text("a, edited\n")
    _git(src, "commit", "-q", "-am", "the entry, squashed (#12)")
    _git(src, "switch", "-q", "main")
    _git(src, "config", "uploadpack.allowAnySHA1InWant", "true")
    work = tmp_path / "queue-work"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--branch", "queue", f"file://{src}", str(work)],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return work, built_on


def _run_changed(
    work: Path, tmp: Path, pr_head: str, *, event: str = "pull_request", queue_base: str = ""
) -> tuple[subprocess.CompletedProcess[str], Path]:
    out = tmp / "github-output"
    out.write_text("")
    runner = tmp / "runner"
    runner.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        PR_HEAD=pr_head,
        QUEUE_BASE=queue_base,
        GITHUB_EVENT_NAME=event,
        RUNNER_TEMP=str(runner),
        GITHUB_OUTPUT=str(out),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    done = subprocess.run(
        ["bash", "-c", _step("changes", CHANGED_STEP)["run"]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done, out


def test_the_changed_paths_are_the_test_merges_own_and_a_rename_is_both(
    test_merge: tuple[Path, str], tmp_path: Path
) -> None:
    work, head = test_merge
    done, out = _run_changed(work, tmp_path, head)
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text() == "ok=true\n"
    listed = (tmp_path / "runner" / "changed-paths" / "changed.z").read_text()
    # main's own change since the pull request branched is not the pull request's.
    assert verdict.read_changed(listed) == {
        "docs/a.md",
        "tests/test_old.py",
        "tests/test_new.py",
    }


def test_a_checkout_that_is_not_the_pull_requests_test_merge_writes_no_list(
    test_merge: tuple[Path, str], tmp_path: Path
) -> None:
    work, _head = test_merge
    done, out = _run_changed(work, tmp_path, "b" * 40)
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text() == ""
    assert "passes no flake" in done.stdout
    assert (
        not (tmp_path / "runner" / "changed-paths" / "changed.z").exists()
        or not (tmp_path / "runner" / "changed-paths" / "changed.z").read_text()
    )


def test_a_queue_commits_changed_paths_are_its_entrys_own(
    queue_commit: tuple[Path, str], tmp_path: Path
) -> None:
    """A queue commit's one parent is the commit the queue built it on: its change against that
    is the entry's, and what the entry ahead brought is not."""
    work, built_on = queue_commit
    done, out = _run_changed(work, tmp_path, "", event="merge_group", queue_base=built_on)
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text() == "ok=true\n"
    listed = (tmp_path / "runner" / "changed-paths" / "changed.z").read_text()
    assert verdict.read_changed(listed) == {"docs/a.md"}


def test_a_queue_commit_not_on_its_queue_base_writes_no_list(
    queue_commit: tuple[Path, str], tmp_path: Path
) -> None:
    work, _built_on = queue_commit
    done, out = _run_changed(work, tmp_path, "", event="merge_group", queue_base="b" * 40)
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text() == ""
    assert "not this entry's queue commit" in done.stdout


def test_a_test_merge_read_as_a_queue_commit_writes_no_list(
    test_merge: tuple[Path, str], tmp_path: Path
) -> None:
    """A checkout with two parents is no queue commit, whatever its event says."""
    work, head = test_merge
    done, out = _run_changed(work, tmp_path, head, event="merge_group", queue_base=head)
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text() == ""


def test_the_lists_are_written_and_read_on_a_pull_request_or_a_queue_commit_only() -> None:
    changes = WORKFLOW["jobs"]["changes"]["steps"]
    results = WORKFLOW["jobs"]["results"]["steps"]
    [write] = [s for s in changes if s.get("name") == CHANGED_STEP]
    [upload] = [s for s in changes if s.get("name") == "the changed paths, as ci-changes"]
    [download] = [s for s in results if s.get("name") == "the pull request's changed paths"]
    assert write["if"] == (
        "${{ github.event_name == 'pull_request' || github.event_name == 'merge_group' }}"
    )
    assert write["env"]["QUEUE_BASE"] == "${{ github.event.merge_group.base_sha }}"
    assert upload["if"] == "${{ steps.changed.outputs.ok == 'true' }}"
    assert "github.event_name == 'pull_request'" in download["if"]
    assert "github.event_name == 'merge_group'" in download["if"]
    # Downloaded by pattern, so a run whose changes job wrote no list downloads nothing and fails
    # nothing; and neither step masks an exit status.
    assert upload["with"]["name"] == download["with"]["pattern"] == "ci-changes"
    assert download["with"]["merge-multiple"] is True and "name" not in download["with"]
    assert "continue-on-error" not in upload and "continue-on-error" not in download


def test_every_stage_job_runs_its_entry_in_the_step_the_rule_reads() -> None:
    """The flake rule passes a failed job only when it failed in its run-job line alone, by that
    step's name, and skips its own results job by name."""
    for job in ("build", "test", "test-2", "test-3"):
        names = [s.get("name") for s in WORKFLOW["jobs"][job]["steps"]]
        assert verdict.RUN_JOB_STEP in names, job
        assert WORKFLOW["jobs"][job]["name"] == "${{ matrix.key }}"
    assert "results" in WORKFLOW["jobs"] and "name" not in WORKFLOW["jobs"]["results"]
    assert verdict.RESULTS_JOB == "results"


# ── the results job's verdict step, with stand-ins ───────────────────────────────────────────


def _bin(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.parametrize(
    ("event", "with_list", "jobs_ok", "passed"),
    [
        pytest.param("pull_request", True, True, True, id="a pull request with both lists"),
        pytest.param("pull_request", False, True, False, id="no changed list"),
        pytest.param("pull_request", True, False, False, id="the jobs could not be listed"),
        pytest.param("push", True, True, False, id="a push"),
        pytest.param("merge_group", True, True, True, id="a queue commit with both lists"),
        pytest.param("merge_group", False, True, False, id="a queue commit with no list"),
    ],
)
def test_the_verdict_is_given_the_two_lists_only_on_a_pull_request_with_both(
    tmp_path: Path, event: str, with_list: bool, jobs_ok: bool, passed: bool
) -> None:
    runner = tmp_path / "runner"
    (runner / "records").mkdir(parents=True)
    if with_list:
        (runner / "changes").mkdir()
        (runner / "changes" / "changed.z").write_text("docs/a.md\0")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # gh: main's verdict is a stand-in that prints its arguments; the jobs listing answers or not.
    _bin(
        bin_dir,
        "gh",
        'case "$*" in\n'
        "  *contents/tools/ci/verdict.py*)\n"
        "    printf 'import sys\\nprint(\"args:\", *sys.argv[1:])\\n';;\n"
        f'  *attempts*) {"echo []" if jobs_ok else "exit 1"};;\n'
        "  *) exit 1;;\n"
        "esac\n",
    )
    python = shutil.which("python3")
    assert python
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(runner),
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_RUN_ID": "4242",
        "GITHUB_RUN_ATTEMPT": "1",
        "GH_TOKEN": "",
        "NEEDS": "{}",
        "KEYS": "[]",
        "KEY_PARTNERS": "{}",
        "APPS_PARTNER": "c" * 40,
        "HEAD_SHA": "a" * 40,
        "JUDGE": "",
    }
    done = subprocess.run(
        ["bash", "-c", _step("results", VERDICT_STEP)["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    [args] = [line for line in done.stdout.splitlines() if line.startswith("args:")]
    given = "--changed" in args.split() and "--jobs" in args.split()
    assert given is passed, args
    assert ("--changed" in args) is passed and ("--jobs" in args) is passed
