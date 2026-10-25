"""A ``--basetemp`` the run names is a test's own to the suite's docker and git guards.

🔴 The defect. The docker guard (``tests/container_runtime.py``) and the git guard
(``personalclaw.sdk.testing.GitGuard``) let a test's own stand-in through when it sits in a
temporary folder: ``tempfile.gettempdir()`` or ``/tmp``, where pytest's own basetemp is. Breadth's
random-order orders each name a basetemp under ``/var/tmp`` (#5568), so every ``tmp_path`` was
outside them, and a stand-in ``docker`` or a credential helper a test wrote there read as the
machine's own: the 0.3.0 rehearsal's breadth (run 40246653348) refused them in
``test_container_runtime_census.py``, ``test_no_test_git_reaches_the_machines_credential_helper.py``
and ``test_a_git_token_signs_in_and_is_kept_nowhere.py``, on both orders.

``tests/conftest.py`` now counts a basetemp the run names as a test's own, in both guards, unless
it is the filesystem's root or in or above the machine's home or this repository: then the session
ends at configure. This file runs a session of its own under one in ``/var/tmp``, outside every
temporary folder, where a test's stand-ins must be let through, and one outside its basetemp, under
the home or in the repository must still be refused; and it holds each basetemp the session must
refuse.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import conftest
import container_runtime
import git_helper_guard
import nested_pytest
import pytest

_TESTS_DIR = Path(__file__).resolve().parent

_STAND_INS = """
import os

import container_runtime
import git_helper_guard


def _docker(folder):
    docker = folder / "bin" / "docker"
    docker.parent.mkdir(parents=True)
    docker.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    docker.chmod(0o755)
    return {"PATH": str(docker.parent)}


def test_a_stand_in_a_test_wrote_under_its_basetemp_is_its_own(tmp_path):
    assert container_runtime.GUARD.refusal(["docker", "version"], _docker(tmp_path)) == ""
    helper = tmp_path / "helper.sh"
    named = tmp_path / "global.gitconfig"
    named.write_text(f"[credential]\\n\\thelper = {helper}\\n", encoding="utf-8")
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(named)}
    assert git_helper_guard.GUARD.refusal(["git", "fetch"], env, cwd=tmp_path) == ""


def test_one_outside_its_basetemp_is_still_refused():
    from pathlib import Path

    elsewhere = Path(os.environ["STAND_IN_ELSEWHERE"])
    assert "this machine's own" in container_runtime.GUARD.refusal(
        ["docker", "version"], _docker(elsewhere)
    )


def test_one_under_the_home_or_in_the_repository_is_still_refused(tmp_path):
    # By path alone: nothing is written there.
    for where in ("STAND_IN_UNDER_HOME", "STAND_IN_IN_REPOSITORY"):
        docker = os.path.join(os.environ[where], "bin", "docker")
        assert "this machine's own" in container_runtime.GUARD.refusal([docker, "version"], {})
    helper = os.path.join(os.environ["STAND_IN_UNDER_HOME"], "helper.sh")
    named = tmp_path / "global.gitconfig"
    named.write_text(f"[credential]\\n\\thelper = {helper}\\n", encoding="utf-8")
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(named)}
    assert git_helper_guard.GUARD.refusal(["git", "fetch"], env, cwd=tmp_path) != ""
"""


def _within(path: str, root: str) -> bool:
    real, top = os.path.realpath(path), os.path.realpath(root)
    return real == top or real.startswith(top + os.sep)


def test_a_stand_in_under_a_named_basetemp_outside_the_temporary_folders_is_the_tests_own(
    tmp_path: Path,
) -> None:
    top = Path(tempfile.mkdtemp(prefix="pclaw-named-basetemp-", dir="/var/tmp"))
    try:
        assert not any(_within(str(top), t) for t in (tempfile.gettempdir(), "/tmp")), top
        (tmp_path / "test_stand_ins.py").write_text(textwrap.dedent(_STAND_INS))
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))
        }
        env.pop(container_runtime.OPT_IN, None)
        env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
            os.pathsep
        )
        env["STAND_IN_ELSEWHERE"] = str(top / "elsewhere")
        never = "pclaw-a-stand-in-never-written"
        env["STAND_IN_UNDER_HOME"] = os.path.join(git_helper_guard.REAL_HOME, never)
        env["STAND_IN_IN_REPOSITORY"] = str(_TESTS_DIR.parent / never)
        proc = subprocess.run(
            # The helper's basetemp, under `top`: outside the temporary folders, as the test needs.
            [*nested_pytest.argv(top), "-o", "addopts=", "-p", "no:cacheprovider"]
            + ["-p", "conftest", "-q", "--color=no", "test_stand_ins.py"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert "3 passed" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-2000:]
    finally:
        shutil.rmtree(top, ignore_errors=True)


class _Named:
    """A run's configuration that names *basetemp*, as ``pytest_configure`` reads it."""

    def __init__(self, basetemp: str) -> None:
        self.basetemp = basetemp

    def getoption(self, name: str) -> str:
        assert name == "basetemp"
        return self.basetemp


def _home_and_repository() -> list[tuple[str, str]]:
    home = git_helper_guard.REAL_HOME
    repo = os.path.realpath(_TESTS_DIR.parent)
    return [
        ("the home", home),
        ("under the home", os.path.join(home, "pclaw-tests")),
        ("above the home", os.path.dirname(home)),
        ("the filesystem's root", os.sep),
        ("the repository", repo),
        ("under the repository", os.path.join(repo, ".local", "basetemp")),
        ("above the repository", os.path.dirname(repo)),
    ]


@pytest.mark.parametrize("what, basetemp", _home_and_repository(), ids=lambda v: str(v))
def test_a_basetemp_in_or_above_the_home_or_the_repository_ends_the_session(
    what: str, basetemp: str
) -> None:
    """Called as ``pytest_configure`` calls it, never as a run's own ``--basetemp``: pytest empties
    a named basetemp the first time it is used, so a check that let one through would empty it."""
    before = (container_runtime.GUARD.own, git_helper_guard.GUARD.own)
    with pytest.raises(pytest.UsageError, match="would count it as a test's own"):
        conftest._a_basetemp_the_run_names_is_a_tests_own(_Named(basetemp))
    assert (container_runtime.GUARD.own, git_helper_guard.GUARD.own) == before, what


def test_one_outside_them_is_added_to_both_guards(monkeypatch) -> None:
    for guard in (container_runtime.GUARD, git_helper_guard.GUARD):
        monkeypatch.setattr(guard, "own", guard.own)
    named = os.path.join("/var/tmp", "pytest-random-order-01")
    conftest._a_basetemp_the_run_names_is_a_tests_own(_Named(named))
    root = os.path.realpath(named)
    assert root in container_runtime.GUARD.own and root in git_helper_guard.GUARD.own


def test_a_workers_basetemp_in_a_tmpdir_under_the_home_is_left_as_it_is(monkeypatch) -> None:
    """CI's layout: run-job's ``TMPDIR`` is under the runner's home, and xdist names each worker's
    basetemp under it (``.../tmp/pytest-of-runner/pytest-0/popen-gw0``). The guards own the
    temporary folders already, so the session configures and they are unchanged. Run here by
    ``tempfile`` alone: nothing is made under the home (PR #5573's first push refused it)."""
    under_home = os.path.join(git_helper_guard.REAL_HOME, "run-job", "jobs", "rails-2-3", "tmp")
    monkeypatch.setattr(tempfile, "tempdir", under_home)
    before = (container_runtime.GUARD.own, git_helper_guard.GUARD.own)
    worker = os.path.join(under_home, "pytest-of-runner", "pytest-0", "popen-gw0")
    conftest._a_basetemp_the_run_names_is_a_tests_own(_Named(worker))
    assert (container_runtime.GUARD.own, git_helper_guard.GUARD.own) == before
