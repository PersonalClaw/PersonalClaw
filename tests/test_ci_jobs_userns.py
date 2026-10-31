"""The ``userns`` toolchain word (#3918): its install lifts the user-namespace restriction on a
GitHub-hosted Linux runner, and nowhere else.

GitHub's Ubuntu 24.04 runners set ``kernel.apparmor_restrict_unprivileged_userns=1``, which
withholds the namespaces the sandbox's own launcher needs, so its namespace tests skipped on every
job. A job that names ``userns`` has ``tools/ci/allow_userns.sh`` lift it for the job's own run. A
developer's machine or a self-hosted runner is a machine someone owns, so there the job is one
run-job cannot run (``jobs.cannot_run_here``), and the script itself refuses.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from test_ci_jobs import (  # noqa: F401 - the job tests' autouse fixtures, by name
    _git,
    _host,
    _hosted,
    _job,
    _run,
    _the_runner_s_setup_uv_names_only_what_a_test_plants,
    _the_user_database_names_a_planted_home,
)

from tools.ci import catalogue, jobs, run

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "ci" / "allow_userns.sh"
SETTING = "kernel.apparmor_restrict_unprivileged_userns"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The job tests' planted repository, as ``test_ci_jobs.repo`` plants it."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / ".gitignore").write_text("reports/\n/.local/\n")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (root / "tests" / "test_a.py").write_text("def test_it():\n    pass\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    return root


def _userns_job(job_id: str, *programs: str, **kw):
    return _job(job_id, *programs, toolchain=("userns",), **kw)


def test_userns_is_a_toolchain_word_and_its_install_is_the_script():
    assert "userns" in catalogue.TOOLCHAINS
    assert jobs.INSTALL_ARGV["userns"] == ("sh", "tools/ci/allow_userns.sh")


def test_off_github_a_userns_job_cannot_run_here_and_nothing_of_it_runs(repo, tmp_path):
    marker = tmp_path / "lifted"
    script = repo / "tools" / "ci" / "allow_userns.sh"
    script.parent.mkdir(parents=True)
    script.write_text(f"#!/bin/sh\n: > {str(marker)!r}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the script")
    ran = tmp_path / "ran"
    outcome = _run(repo, _userns_job("namespace-sandbox", f"open({str(ran)!r}, 'w')"), tmp_path)
    assert outcome.code == run.PARTIAL and not outcome.ran and outcome.not_here
    assert outcome.reasons == [f"cannot run here: {jobs.USERNS_ELSEWHERE}"]
    assert not marker.exists() and not ran.exists(), "nothing of the job ran, its install neither"


def test_a_github_hosted_linux_runner_can_run_it(tmp_path, monkeypatch):
    _hosted(monkeypatch)
    assert jobs.cannot_run_here(_userns_job("x", "pass"), _host(tmp_path, on_github=True)) == []


@pytest.mark.parametrize(
    "linux, environment, image",
    [(False, "github-hosted", "macos15"), (True, "self-hosted", "ubuntu24"), (True, None, None)],
    ids=["a hosted mac", "a self-hosted runner", "no runner environment"],
)
def test_any_other_runner_cannot_run_it(tmp_path, monkeypatch, linux, environment, image):
    _hosted(monkeypatch, linux=linux, environment=environment, image=image)
    why = jobs.cannot_run_here(_userns_job("x", "pass"), _host(tmp_path, on_github=True))
    assert why == [jobs.USERNS_ELSEWHERE]


def test_the_install_alone_is_given_the_runner_s_own_words_and_only_on_a_hosted_runner(
    tmp_path, monkeypatch
):
    host = _host(tmp_path, on_github=True)
    _hosted(monkeypatch, environment="self-hosted")
    assert jobs._helper_env(jobs.INSTALL_ARGV["userns"], host) == {}
    _hosted(monkeypatch)
    assert jobs._helper_env(jobs.INSTALL_ARGV["userns"], host) == {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }
    assert jobs._helper_env(("sh", "tools/ci/another.sh"), host) == {}


# ── the script, over faked uname, sysctl and sudo ────────────────────────────────────────────


def _fakes(tmp_path: Path, *, system: str = "Linux", value: str = "1", holds: bool = True):
    """A PATH whose ``uname`` says *system*, whose ``sysctl`` reads and writes the setting in a
    file holding *value* (a write that does not hold leaves it), and whose ``sudo`` runs its
    command, each call logged."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state, calls = tmp_path / "setting", tmp_path / "calls"
    state.write_text(value)
    scripts = {
        "uname": f"echo {system}\n",
        "sysctl": (
            f'echo "sysctl $*" >> {calls}\n'
            f'if [ "$1" = -n ]; then cat {state}; exit 0; fi\n'
            + (f'echo "${{2#*=}}" > {state}\n' if holds else "")
        ),
        "sudo": f'echo "sudo $*" >> {calls}\nexec "$@"\n',
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", state, calls


def _script(path: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT)],
        env={"PATH": path, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


_HOSTED = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}


@pytest.mark.parametrize(
    "system, env",
    [
        ("Linux", {}),
        ("Linux", {"GITHUB_ACTIONS": "true"}),
        ("Linux", {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}),
        ("Darwin", _HOSTED),
    ],
    ids=["no GitHub", "no runner environment", "a self-hosted runner", "not Linux"],
)
def test_the_script_refuses_and_changes_nothing_anywhere_but_a_hosted_linux_runner(
    tmp_path, system, env
):
    path, state, calls = _fakes(tmp_path, system=system)
    done = _script(path, **env)
    assert done.returncode != 0
    assert "not a GitHub-hosted Linux runner" in done.stderr
    assert state.read_text().strip() == "1"
    assert not calls.exists() or "sudo" not in calls.read_text()


def test_on_a_hosted_linux_runner_it_lifts_the_restriction_and_says_so(tmp_path):
    path, state, calls = _fakes(tmp_path)
    done = _script(path, **_HOSTED)
    assert done.returncode == 0, done.stderr
    assert state.read_text().strip() == "0"
    assert f"sudo sysctl -w {SETTING}=0" in calls.read_text()
    assert f"{SETTING} was 1, is 0" in done.stdout


def test_a_restriction_already_off_is_left_as_it_is(tmp_path):
    path, state, calls = _fakes(tmp_path, value="0")
    done = _script(path, **_HOSTED)
    assert done.returncode == 0, done.stderr
    assert "sudo" not in calls.read_text()


def test_a_lift_that_does_not_hold_fails(tmp_path):
    path, state, _ = _fakes(tmp_path, holds=False)
    done = _script(path, **_HOSTED)
    assert done.returncode != 0
    assert state.read_text().strip() == "1"
