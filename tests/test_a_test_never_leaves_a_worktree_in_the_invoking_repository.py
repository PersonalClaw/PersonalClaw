"""A test that leaves a worktree registered in the repository the tests run from fails the session.

A worktree added to the invoking repository is registered in its ``.git/worktrees``, and after
pytest removes the test's temporary folder the developer's clone keeps a dangling entry until
``git worktree prune`` (``test_a_head_that_only_adds_a_toolchain_word_passes``, before #5686).
``tests/worktree_guard.py`` compares the repository's worktrees at the session's start and finish
and names each new one: a failure for one this session's tests left, a warning for any other.

Each case runs a session of its own with the guard on a scratch repository it takes as the
invoking one, and with a machine temporary folder of its own (TMPDIR), and every git call runs
with none of the variables a git hook exports to point git elsewhere, so no plant ever touches
this checkout.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import conftest
import nested_pytest
import pytest
import worktree_guard

_TESTS_DIR = Path(__file__).resolve().parent

#: The nested session's plugin: the guard on WT_INVOKING, each of its reads of the worktrees
#: counted in WT_CALLS, and with WT_EXIT a status of its own set as the session finishes, before
#: the guard's.
_PLUGIN = """
import os
from pathlib import Path

import worktree_guard

_registered = worktree_guard.registered


def _counted(*args, **kwargs):
    with open(os.environ["WT_CALLS"], "a", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}\\n")
    return _registered(*args, **kwargs)


worktree_guard.registered = _counted


def pytest_configure(config):
    config.pluginmanager.register(
        worktree_guard.Plugin(Path(os.environ["WT_INVOKING"])), "worktree-guard"
    )


def pytest_sessionfinish(session):
    if os.environ.get("WT_EXIT"):
        session.exitstatus = int(os.environ["WT_EXIT"])
"""

#: The one test of the session: it adds a worktree the way the case names, to a branch of its own.
_PLANT = """
import os
import subprocess
import tempfile


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def test_plant(tmp_path):
    how, invoking = os.environ["WT_HOW"], os.environ["WT_INVOKING"]
    if how == "the invoking repository":
        _git("-C", invoking, "worktree", "add", "-b", "plant", str(tmp_path / "wt"))
    elif how == "added and removed":
        _git("-C", invoking, "worktree", "add", "-b", "plant", str(tmp_path / "wt"))
        _git("-C", invoking, "worktree", "remove", str(tmp_path / "wt"))
    elif how == "the machine's temporary folder":
        _git("-C", invoking, "worktree", "add", "-b", "plant", tempfile.mkdtemp() + "/wt")
    elif how == "another session's folder":
        other = os.path.join(tempfile.gettempdir(), "pytest-of-someone", "pytest-99")
        os.makedirs(other)
        _git("-C", invoking, "worktree", "add", "-b", "plant", os.path.join(other, "wt"))
    elif how == "a session's folder deeper down":
        job = ("personalclaw-run-x", "jobs", "python-suite-1", "tmp")
        other = os.path.join(tempfile.gettempdir(), *job, "pytest-of-someone", "pytest-0")
        os.makedirs(other)
        _git("-C", invoking, "worktree", "add", "-b", "plant", os.path.join(other, "wt"))
    elif how == "the repository goes":
        gone = os.path.join(invoking, ".git-gone")
        os.rename(os.path.join(invoking, ".git"), gone)
        with open(os.path.join(invoking, ".git"), "w") as gitfile:
            gitfile.write("gitdir: " + os.path.join(gone, "nowhere") + "\\n")
    elif how == "anywhere else":
        _git("-C", invoking, "worktree", "add", "-b", "plant", os.environ["WT_ELSEWHERE"])
    elif how == "a repository of its own":
        own = tmp_path / "own"
        _git("clone", "-q", invoking, str(own))
        _git("-C", str(own), "worktree", "add", "-b", "plant", str(tmp_path / "wt"))
    elif how == "a --shared clone":
        shared = tmp_path / "shared"
        _git("clone", "-q", "--shared", invoking, str(shared))
        _git("-C", str(shared), "worktree", "add", "-b", "plant", str(tmp_path / "wt"))
    else:
        raise AssertionError(how)
"""

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}

_LEFT = "a test left a worktree in the invoking repository"
_NAMED = "worktrees registered in the invoking repository while the session ran"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        env={**worktree_guard.git_env(), **_GIT_ENV},
    ).stdout


def _scratch_repository(where: Path) -> Path:
    where.mkdir()
    _git("-C", str(where), "init", "-q", "-b", "main")
    _git("-C", str(where), "commit", "-q", "--allow-empty", "-m", "one")
    return where


def _session(
    tmp_path: Path,
    how: str,
    *,
    linked: bool = False,
    args: tuple[str, ...] = (),
    exit_with: int | None = None,
    terminal: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """A session of its own over the plant *how*, on a scratch repository taken as the invoking
    one, or a linked worktree of it when *linked*, with a machine temporary folder of its own
    (TMPDIR) beside its base temporary folder."""
    invoking = _scratch_repository(tmp_path / "invoking")
    if linked:
        _git("-C", str(invoking), "worktree", "add", "-q", "-b", "linked", str(tmp_path / "linked"))
        invoking = tmp_path / "linked"
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.ini").write_text("[pytest]\n")
    (project / "wt_guard_plugin.py").write_text(textwrap.dedent(_PLUGIN))
    (project / "test_plant.py").write_text(textwrap.dedent(_PLANT))
    elsewhere = tmp_path / "elsewhere"
    env = {
        k: v
        for k, v in worktree_guard.git_env().items()
        if not k.startswith(("PYTEST_", "PERSONALCLAW_"))
    }
    env.update(_GIT_ENV)
    env["PYTHONPATH"] = os.pathsep.join([str(project), str(_TESTS_DIR)])
    env.update(WT_HOW=how, WT_INVOKING=str(invoking), WT_ELSEWHERE=str(elsewhere))
    env["WT_CALLS"] = str(tmp_path / "calls")
    if exit_with is not None:
        env["WT_EXIT"] = str(exit_with)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    env["TMPDIR"] = str(tmpdir)  # the session's machine temporary folder
    proc = subprocess.run(
        [*nested_pytest.argv(tmp_path), "-o", "addopts=", "-p", "no:cacheprovider", *args]
        + ["-p", "wt_guard_plugin", *(["-q", "--color=no"] if terminal else []), "test_plant.py"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    return proc, invoking, elsewhere


def _new(invoking: Path, tmp_path: Path) -> list[str]:
    """The worktrees *invoking*'s clone registers beyond its own two at most."""
    own = {os.path.realpath(invoking), os.path.realpath(tmp_path / "invoking")}
    return sorted(worktree_guard.registered(invoking) - own)


def _reads(tmp_path: Path) -> int:
    return len((tmp_path / "calls").read_text(encoding="utf-8").splitlines())


def test_this_session_guards_the_repository_it_runs_from(request):
    """🔴 Red before: nothing compared the invoking repository's worktrees at all."""
    guard = request.config.pluginmanager.get_plugin("worktree-guard")
    assert isinstance(guard, worktree_guard.Plugin), guard
    assert guard.repo == conftest._INVOKING_REPO_ROOT


@pytest.mark.parametrize(
    ("how", "linked", "args"),
    [
        pytest.param("the invoking repository", False, (), id="under the session's basetemp"),
        pytest.param("the machine's temporary folder", False, (), id="under tempfile.gettempdir()"),
        pytest.param("the invoking repository", True, (), id="from inside a linked worktree"),
        pytest.param("the invoking repository", False, ("-n", "2"), id="under xdist"),
    ],
)
def test_a_worktree_this_sessions_tests_left_fails_the_session_named(tmp_path, how, linked, args):
    """🔴 Red with a guard that fails only under the session's basetemp, under
    tempfile.gettempdir(): a test's ``mkdtemp`` is outside the session's basetemp, and its
    worktree was let be. Each case leaves one worktree registered in the invoking repository, a
    linked worktree's included, under this session's base temporary folder or the machine's
    temporary folder, and the session fails once, naming it with the command that removes it,
    though its test passed. The controller alone reads the worktrees, at the start and the
    finish, under xdist too: no worker reads them."""
    proc, invoking, _ = _session(tmp_path, how, linked=linked, args=args)
    out = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.TESTS_FAILED, out[-3000:]
    assert "1 passed" in out, out[-3000:]  # the test itself passed: the session failed it
    assert out.count(_LEFT) == 1, out[-3000:]
    (left,) = _new(invoking, tmp_path)
    assert left in out and f"worktree remove --force {left}" in out, out[-3000:]
    assert _reads(tmp_path) == 2, "the controller reads the worktrees twice, and no worker does"


@pytest.mark.parametrize(
    "how",
    [
        pytest.param("anywhere else", id="outside both temporary folders"),
        pytest.param("another session's folder", id="under another pytest session's folder"),
        pytest.param(
            "a session's folder deeper down",
            id="under a pytest session's folder below another folder",
        ),
    ],
)
def test_a_new_worktree_this_sessions_tests_did_not_leave_is_named_in_a_warning(tmp_path, how):
    """🔴 Red with a guard that fails every new entry under tempfile.gettempdir(), under another
    session's folder: one under ``<TMPDIR>/pytest-of-<user>`` failed the session, though that
    folder is a nested session's or one in another checkout of the same clone; and with one that
    looks only at the top level there, under a local tools/ci job's, whose session folder sits
    under its scratch folder. A worktree registered in the invoking repository while the session
    ran, outside this session's basetemp and outside the machine's temporary folder less every
    pytest session's folder at any depth, is not a test's as far as the session can tell: it
    passes, and a warning names it."""
    proc, invoking, _ = _session(tmp_path, how)
    out = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.OK, out[-3000:]
    assert _NAMED in out, out[-3000:]
    (new,) = _new(invoking, tmp_path)
    assert new in out, out[-3000:]
    assert _LEFT not in out, out[-3000:]


@pytest.mark.parametrize(
    "how",
    ["added and removed", "a repository of its own", "a --shared clone"],
)
def test_a_worktree_the_invoking_repository_does_not_keep_passes_quietly(tmp_path, how):
    proc, invoking, _ = _session(tmp_path, how)
    out = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.OK, out[-3000:]
    assert _LEFT not in out and _NAMED not in out, out[-3000:]
    assert worktree_guard.registered(invoking) - {os.path.realpath(invoking)} == set()


@pytest.mark.parametrize(
    ("exit_with", "expected"),
    [
        pytest.param(2, 2, id="interrupted"),
        pytest.param(3, 3, id="an internal error"),
        pytest.param(4, 4, id="a usage error"),
        pytest.param(5, 1, id="no tests collected: failed"),
    ],
)
def test_a_session_that_ended_otherwise_keeps_its_status_and_still_names_the_worktree(
    tmp_path, exit_with, expected
):
    """🔴 Red with a guard that sets a failed status over any other: an interrupted, broken or
    misused session (here its status set as it finishes) that also left a worktree read as a
    failed test, and CI reran it. It keeps its status, and the worktree is named; one that
    collected no test fails, as one that passed does."""
    proc, invoking, _ = _session(tmp_path, "the invoking repository", exit_with=exit_with)
    out = proc.stdout + proc.stderr
    assert proc.returncode == expected, out[-3000:]
    (left,) = _new(invoking, tmp_path)
    assert _LEFT in out and left in out, out[-3000:]


def test_a_git_hooks_variables_never_point_a_plant_at_another_repository(tmp_path, monkeypatch):
    """🔴 Red with git calls that keep a hook's variables: run from a git hook, which exports
    ``GIT_DIR``, the scratch repository's ``init`` and ``commit`` and the plant's ``worktree add``
    reached the hook's repository, and the guard's read listed that repository's worktrees, not
    the invoking one's. Every git call of the guard and of these cases runs without the
    variables that move git off the repository ``-C`` names: red on a ``registered`` that keeps
    them, and on helpers that keep them."""
    decoy = _scratch_repository(tmp_path / "decoy")
    before = (_git("-C", str(decoy), "rev-list", "--all"), _git("-C", str(decoy), "branch"))
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    (tmp_path / "case").mkdir()
    proc, invoking, _ = _session(tmp_path / "case", "the invoking repository")
    assert proc.returncode == pytest.ExitCode.TESTS_FAILED, (proc.stdout + proc.stderr)[-3000:]
    (left,) = _new(invoking, tmp_path / "case")
    assert left.startswith(
        os.path.realpath(tmp_path / "case" / nested_pytest.FOLDER) + os.sep
    ), left
    assert os.path.realpath(invoking) in worktree_guard.registered(invoking)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    after = (_git("-C", str(decoy), "rev-list", "--all"), _git("-C", str(decoy), "branch"))
    assert after == before, "the decoy repository was written"
    assert worktree_guard.registered(decoy) == {os.path.realpath(decoy)}


def test_a_session_that_ran_no_test_leaves_its_basetemp_as_it_was(tmp_path):
    """The guard reads the base temporary folder only if the session made one: asking for it
    (``getbasetemp()``) would make it, and with ``--basetemp`` empty it, at the end of a session
    that ran no test."""
    marker = tmp_path / nested_pytest.FOLDER / "marker"
    marker.parent.mkdir()
    marker.write_text("kept\n")
    proc, _, _ = _session(tmp_path, "the invoking repository", args=("--collect-only",))
    assert proc.returncode == pytest.ExitCode.OK, (proc.stdout + proc.stderr)[-3000:]
    assert marker.read_text() == "kept\n"


def test_with_no_terminal_the_worktree_is_named_on_stderr(tmp_path):
    """With the terminal plugin off (``-p no:terminal``) the session still fails, and the banner
    and the remove command go to stderr."""
    proc, invoking, _ = _session(
        tmp_path, "the invoking repository", args=("-p", "no:terminal"), terminal=False
    )
    assert proc.returncode == pytest.ExitCode.TESTS_FAILED, (proc.stdout + proc.stderr)[-3000:]
    (left,) = _new(invoking, tmp_path)
    assert f"== {_LEFT} ==" in proc.stderr, proc.stderr[-3000:]
    assert f"worktree remove --force {left}" in proc.stderr, proc.stderr[-3000:]
    assert _reads(tmp_path) == 2


def test_a_repository_git_can_no_longer_read_as_the_session_finishes_is_named(tmp_path):
    """A repository that git can read at the start and not at the finish (here its ``.git``
    gone during the test) leaves the session its own result, with one warning that names it and
    gives git's reason."""
    proc, invoking, _ = _session(tmp_path, "the repository goes")
    out = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.OK, out[-3000:]
    (warned,) = [line for line in out.splitlines() if "as the session finished" in line]
    assert str(invoking) in warned and "fatal:" in warned, out[-3000:]


def test_a_repository_git_cannot_read_arms_nothing_and_says_why(tmp_path):
    """A ``.git`` git cannot read (here a link to a folder that is gone) arms nothing: the session
    keeps its own result, and one warning gives git's reason, so the guard is never off unseen."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.ini").write_text("[pytest]\n")
    (project / "wt_guard_plugin.py").write_text(textwrap.dedent(_PLUGIN))
    (project / "test_plant.py").write_text("def test_nothing():\n    pass\n")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text(f"gitdir: {tmp_path / 'gone'}\n")
    env = {
        k: v
        for k, v in worktree_guard.git_env().items()
        if not k.startswith(("PYTEST_", "PERSONALCLAW_"))
    }
    env.update(PYTHONPATH=os.pathsep.join([str(project), str(_TESTS_DIR)]))
    env.update(WT_INVOKING=str(broken), WT_CALLS=str(tmp_path / "calls"))
    proc = subprocess.run(
        [*nested_pytest.argv(tmp_path), "-o", "addopts=", "-p", "no:cacheprovider"]
        + ["-p", "wt_guard_plugin", "-q", "--color=no", "test_plant.py"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.OK, out[-3000:]
    # git's own reason, in its own words: "not a git repository" here, "gitfile does not point to
    # a valid repository" on CI's git.
    (warned,) = [line for line in out.splitlines() if "the worktree guard is off" in line]
    assert str(broken) in warned and "fatal:" in warned, out
