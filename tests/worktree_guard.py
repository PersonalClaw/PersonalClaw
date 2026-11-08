"""A test never leaves a worktree registered in the repository the tests run from.

A worktree a test adds to the invoking repository is registered in that repository's
``.git/worktrees``: once pytest removes the test's temporary folder, the developer's clone keeps
a dangling entry until ``git worktree prune``.
``test_a_head_that_only_adds_a_toolchain_word_passes`` did that before #5686 laid its head in a
``--shared`` clone, and nothing stopped the next test.

:class:`Plugin` reads ``git worktree list --porcelain -z`` of the invoking repository as the
session starts and as it finishes, in the controller alone (an xdist worker's session is part of
it; a run with no xdist is its own controller). A worktree registered there since the start is a
test's when its path is under this session's base temporary folder, or under the machine's
temporary folder (``tempfile.gettempdir()``, where a test's ``mkdtemp`` lands) but outside every
pytest session's folder at any depth there (``pytest-of-<user>``; a local tools/ci job's sits
under its scratch folder): a nested session's, or one in another checkout of the same clone,
belongs to that session, not to this one's tests. Each of those fails a session that would
otherwise pass, named with how to remove it; a session already failed, interrupted or broken
keeps its own status. Any other new entry is named in a warning, not failed: another checkout of
the same clone can add worktrees to it while a suite runs. A ``--basetemp`` anywhere under the
machine's temporary folder outside a ``pytest-of-*`` folder, which ``conftest.py`` allows, cannot
be told apart from a test's ``mkdtemp`` there, so name such a basetemp elsewhere (under
``/var/tmp``, as breadth and bisect-pollution do).
Entries are compared by their real path, so one listed at the start, a stale prunable one
included, is never new. From inside a linked worktree the repository lists every worktree of its
clone, as it does from the main one. git runs with none of the variables that move it off the
repository ``-C`` names (:data:`GIT_LOCATION_VARS`), some of which a git hook exports. A
repository git cannot read arms nothing, and says why when it has a ``.git``, at the start and at
the finish.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

#: The variables that move git off the repository ``-C`` names, or off what it reads there; a git
#: hook exports some of them, and a git call that keeps them can read, or write, the hook's.
GIT_LOCATION_VARS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
    }
)

#: The first part of every pytest session's folder under the machine's temporary folder.
_SESSIONS = "pytest-of-"


def git_env() -> dict[str, str]:
    """This process's environment less :data:`GIT_LOCATION_VARS`."""
    return {name: value for name, value in os.environ.items() if name not in GIT_LOCATION_VARS}


def registered(repo: Path, why: list[str] | None = None) -> frozenset[str] | None:
    """The real path of every worktree *repo* registers, its own included; ``None`` when git
    cannot list them (not a repository, or no git), with git's reason appended to *why*."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain", "-z"],
            capture_output=True,
            timeout=30,
            env=git_env(),
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        if why is not None:
            why.append(f"{type(exc).__name__}: {exc}")
        return None
    if done.returncode != 0:
        if why is not None:
            why.append(" ".join(os.fsdecode(done.stderr).split()) or f"exit {done.returncode}")
        return None
    return frozenset(
        os.path.realpath(os.fsdecode(field[len(b"worktree ") :]))
        for field in done.stdout.split(b"\0")
        if field.startswith(b"worktree ")
    )


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def a_tests(path: str, own: str | None, machine: str) -> bool:
    """Whether a new worktree at *path* is this session's tests': under its base temporary
    folder *own*, or under the machine's temporary folder *machine* outside every pytest
    session's folder at any depth there (``pytest-of-<user>``; a local tools/ci job's sits under
    its scratch folder)."""
    if own is not None and _within(path, own):
        return True
    if not _within(path, machine):
        return False
    return not any(
        part.startswith(_SESSIONS) for part in os.path.relpath(path, machine).split(os.sep)
    )


class Plugin:
    """The guard for one invoking repository, *repo*."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.before: frozenset[str] | None = None
        #: New entries this session's tests left: the session fails.
        self.left: list[str] = []
        #: New entries anywhere else: named, not failed.
        self.elsewhere: list[str] = []

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if hasattr(session.config, "workerinput"):
            return
        why: list[str] = []
        self.before = registered(self.repo, why)
        if self.before is None and (Path(self.repo) / ".git").exists():
            _write(
                session,
                [
                    f"warning: the worktree guard is off: git could not list the worktrees of"
                    f" {self.repo}: {' '.join(why)}"
                ],
            )

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.before is None:
            return
        why: list[str] = []
        after = registered(self.repo, why)
        if after is None:
            _write(
                session,
                [
                    f"warning: the worktree guard could not list the worktrees of {self.repo} as"
                    f" the session finished: {' '.join(why)}"
                ],
            )
            return
        # The base temporary folder if this session made one: getbasetemp() would make it, and
        # with --basetemp wipe it, for a session that ran no test.
        own = getattr(getattr(session.config, "_tmp_path_factory", None), "_basetemp", None)
        own = None if own is None else os.path.realpath(own)
        machine = os.path.realpath(tempfile.gettempdir())
        new = sorted(after - self.before)
        self.left = [path for path in new if a_tests(path, own, machine)]
        self.elsewhere = [path for path in new if path not in self.left]
        if self.left and session.exitstatus in (
            pytest.ExitCode.OK,
            pytest.ExitCode.NO_TESTS_COLLECTED,
        ):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        self._report(session)

    def _report(self, session: pytest.Session) -> None:
        repo = shlex.quote(str(self.repo))
        if self.elsewhere:
            _write(
                session,
                [
                    f"warning: {len(self.elsewhere)} new worktree(s) of {self.repo}, outside this"
                    " session's base temporary folder, and outside the machine's temporary folder"
                    " or in a pytest session's folder there: not its tests', as far as it can"
                    " tell, so named, not failed:",
                    *(f"  {path}" for path in self.elsewhere),
                ],
                title="worktrees registered in the invoking repository while the session ran",
            )
        if self.left:
            _write(
                session,
                [
                    f"{len(self.left)} worktree(s) under this session's base temporary folder or"
                    f" the machine's temporary folder are registered in {self.repo}, and the"
                    " clone keeps each after pytest removes its folder. Add a test's worktree to"
                    " a repository of its own (`git init`, or a `--shared` clone). Remove these"
                    " with:",
                    *(
                        f"  git -C {repo} worktree remove --force {shlex.quote(path)}"
                        for path in self.left
                    ),
                    "A branch a test made with `-b` stays: `git -C"
                    f" {repo} branch -D <name>` removes it.",
                ],
                title="a test left a worktree in the invoking repository",
                red=True,
            )


def _write(
    session: pytest.Session, lines: list[str], *, title: str | None = None, red: bool = False
) -> None:
    """*lines* on the terminal, under *title*, or on stderr where there is no terminal reporter
    (``-p no:terminal``), so a session that ends without its summary still names them."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        print(*([f"== {title} =="] if title else []), *lines, sep="\n", file=sys.stderr)
        return
    if title:
        reporter.write_sep("=" if red else "-", title, red=red, yellow=not red)
    for line in lines:
        reporter.write_line(line)
