"""``scripts/run_prepush.sh``: it gates the tree it is pushing, scopes by what that branch adds,
and runs the change tier's pre-push jobs and nothing of its own (#3920).

── 1. The tree/ref guard ──────────────────────────────────────────────────────

The hook's run checks the WORKING TREE (``run change --working-tree``), while the push names
REFS. They are the same thing only when the ref pushed is what this worktree has checked out,
and with many ``git worktree``s they routinely are not: ``git push origin some-branch`` from a
checkout sitting on ``main`` would check ``main``'s tree, go green, and prove nothing about what
shipped. So every outgoing ref must resolve to this worktree's ``HEAD`` or the push is refused,
before anything runs.

── 2. Scope, after a rebase (issue 2269) ──────────────────────────────────────

The run's ``--base`` is the merge base with ``origin/main``: what the branch adds. The range from
the remote's old tip is the same question only while the branch is a fast-forward of its remote;
after a rebase it spans every ``main`` commit in between. The old tip is kept only for a clone
with no ``origin/main``; with neither, every pre-push job runs.

── 3. The run ─────────────────────────────────────────────────────────────────

The hook runs ``python -m tools.ci run change --pre-push`` with ``--working-tree --fail-fast
--workers 2``, and its exit decides the push: red refuses it, partial lets it through. The
contract row in ``tests/rails/test_ci_contract.py`` holds that it runs no check of its own.

**These tests drive the real script** in a sandbox repository whose ``.venv/bin/python`` is a
stub: it records the words it is given and exits as the test says, so no job runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_prepush.sh"

ZERO = "0" * 40

#: The guard's own condition, verbatim. The vacuity leg neuters this exact line; if it is ever
#: reworded, that leg reds rather than quietly measuring nothing.
GUARD_CONDITION = 'if [ "$pushed_commit" != "$head_commit" ]; then'

REFUSAL = "refusing to gate a tree that is not what you are pushing"

#: The words the hook gives tools/ci, after any --changed/--base.
TIER = "-m tools.ci run change --pre-push"
RUN_FLAGS = "--working-tree --fail-fast --workers 2 --admission-wait 60"

#: Identity plus two neutralizers: the developer's own template hooks must not fire in the
#: sandbox, and a configured signing key must not make these commits interactive.
_IDENT = (
    "-c",
    "user.name=Gate Test",
    "-c",
    "user.email=gate@test.invalid",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
)

#: The stub interpreter: it starts (the hook probes it with ``-c``), records every other call's
#: words, and exits with the code in ``exit-code`` beside the sandbox.
_STUB = """#!/bin/sh
here=$(cd "$(dirname "$0")/../.." && pwd)
[ "$1" = "-c" ] && exit 0
printf '%s\\n' "$*" >> "$here/calls.txt"
exit "$(cat "$here/exit-code" 2>/dev/null || echo 0)"
"""


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", message, cwd=root)
    return _git("rev-parse", "HEAD", cwd=root)


class Sandbox:
    """A throwaway repository carrying the shipped script and the stub interpreter."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.script = root / "scripts" / "run_prepush.sh"
        #: The fixture's two commits: ``head`` checked out, ``parent`` as ``origin/main``.
        self.head = ""
        self.parent = ""

    def calls(self) -> list[str]:
        """Each call the hook made of the interpreter, but the probe."""
        path = self.root / "calls.txt"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def exits(self, code: int) -> None:
        (self.root / "exit-code").write_text(str(code), encoding="utf-8")


def _sandbox(tmp_path: Path, name: str = "sandbox") -> Sandbox:
    root = tmp_path / name
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "run_prepush.sh")
    assert (root / "scripts" / "run_prepush.sh").read_bytes() == SCRIPT.read_bytes()
    stub = root / ".venv" / "bin" / "python"
    stub.parent.mkdir(parents=True)
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    (root / ".gitignore").write_text(".venv/\ncalls.txt\nexit-code\n", encoding="utf-8")
    _git("init", "-q", "-b", "main", cwd=root)
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    _commit(root, "first")
    return Sandbox(root)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    """Two commits on main, the second checked out; ``origin/main`` is the first."""
    box = _sandbox(tmp_path)
    box.parent = _git("rev-parse", "HEAD", cwd=box.root)
    (box.root / "notes.txt").write_text("two\n", encoding="utf-8")
    box.head = _commit(box.root, "second")
    _git("update-ref", "refs/remotes/origin/main", box.parent, cwd=box.root)
    return box


def _run(stdin: str, *, cwd: Path, script: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the real gate with `stdin` as its githooks(5) ref lines."""
    return subprocess.run(
        ["sh", str(script or (cwd / "scripts" / "run_prepush.sh"))],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _line(ref: str, local: str, remote: str) -> str:
    return f"{ref} {local} {ref} {remote}\n"


def _assert_refused(result: subprocess.CompletedProcess[str], box: Sandbox) -> None:
    """A refusal is a non-zero exit taken by the guard, before the tier ran."""
    assert result.returncode != 0, f"the guard did not fire: stdout={result.stdout!r}"
    assert REFUSAL in result.stderr
    assert box.calls() == [], "the tier ran before the guard refused"


# ── 1. The guard ─────────────────────────────────────────────────────────────────────────────


def test_the_shipped_script_still_carries_the_guard():
    assert GUARD_CONDITION in SCRIPT.read_text(encoding="utf-8"), (
        "the tree/ref guard's condition is gone from scripts/run_prepush.sh; the legs below "
        "would be measuring some other exit path. Restore it, do not weaken them."
    )


def test_pushing_this_worktrees_head_runs_the_tier(sandbox: Sandbox):
    result = _run(_line("refs/heads/feature", sandbox.head, ZERO), cwd=sandbox.root)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REFUSAL not in result.stderr
    assert sandbox.calls() == [
        f"{TIER} --changed --base {sandbox.parent} {RUN_FLAGS}"
    ], sandbox.calls()


def test_pushing_a_ref_this_worktree_does_not_have_checked_out_is_refused(sandbox: Sandbox):
    """Non-zero, before the tier runs, and the message names both commits and the fix."""
    result = _run(_line("refs/heads/other", sandbox.parent, sandbox.parent), cwd=sandbox.root)
    _assert_refused(result, sandbox)
    assert sandbox.parent in result.stderr, "the refusal does not name the pushed commit"
    assert sandbox.head in result.stderr, "the refusal does not name the checkout's HEAD"
    assert "git worktree list" in result.stderr, "the refusal does not name the fix"


def test_a_new_remote_branch_is_guarded_too(sandbox: Sandbox):
    result = _run(_line("refs/heads/brand-new", sandbox.parent, ZERO), cwd=sandbox.root)
    _assert_refused(result, sandbox)


def test_one_wrong_ref_among_several_refuses_the_push(sandbox: Sandbox):
    stdin = _line("refs/heads/a", sandbox.head, ZERO) + _line("refs/heads/b", sandbox.parent, ZERO)
    _assert_refused(_run(stdin, cwd=sandbox.root), sandbox)


def test_an_annotated_tag_on_head_compares_as_its_commit(sandbox: Sandbox):
    _git(*_IDENT, "tag", "-a", "-m", "a tag", "v9.9.9", cwd=sandbox.root)
    tag = _git("rev-parse", "v9.9.9", cwd=sandbox.root)
    assert tag != sandbox.head, "the tag is not an object of its own"
    result = _run(_line("refs/tags/v9.9.9", tag, ZERO), cwd=sandbox.root)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert len(sandbox.calls()) == 1


def test_a_branch_deletion_runs_nothing(sandbox: Sandbox):
    result = _run(f"(delete) {ZERO} refs/heads/some-branch {sandbox.head}\n", cwd=sandbox.root)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert sandbox.calls() == []
    assert "nothing outgoing" in result.stdout


def test_the_refusal_is_attributable_to_the_guard(sandbox: Sandbox):
    """Vacuity: the same stdin runs the tier once the guard's own condition is neutered."""
    source = sandbox.script.read_text(encoding="utf-8")
    assert source.count(GUARD_CONDITION) == 1, "the guard condition is not a unique line"
    neutered = sandbox.root / "scripts" / "run_prepush_without_the_guard.sh"
    neutered.write_text(source.replace(GUARD_CONDITION, "if false; then"), encoding="utf-8")
    stdin = _line("refs/heads/other", sandbox.parent, sandbox.parent)
    _assert_refused(_run(stdin, cwd=sandbox.root), sandbox)
    result = _run(stdin, cwd=sandbox.root, script=neutered)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert len(sandbox.calls()) == 1, "the guard-free copy did not reach the run"


def test_the_real_repo_refuses_a_ref_that_is_not_its_head():
    """The shipped file, where it runs: a ref that is not this checkout's HEAD (here an object
    id that names no commit at all) is refused at once. The positive leg would run the real
    tier, a static job of about two minutes, so it is the sandbox's."""
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=REPO_ROOT,
        input=_line("refs/heads/some-branch", "1" * 40, ZERO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0 and REFUSAL in result.stderr, result.stderr


# ── 2. Scope ─────────────────────────────────────────────────────────────────────────────────


def test_a_rebased_branch_is_scoped_by_its_merge_base_not_its_old_remote_tip(tmp_path: Path):
    """🔴 issue 2269: the stranded remote tip must not decide the scope."""
    box = _sandbox(tmp_path, "rebased")
    _git(*_IDENT, "checkout", "-q", "-b", "feature", cwd=box.root)
    (box.root / "notes.txt").write_text("branch work\n", encoding="utf-8")
    old_tip = _commit(box.root, "work")
    _git(*_IDENT, "checkout", "-q", "main", cwd=box.root)
    (box.root / "other.txt").write_text("somebody else's\n", encoding="utf-8")
    new_main = _commit(box.root, "main moves on")
    _git("update-ref", "refs/remotes/origin/main", new_main, cwd=box.root)
    _git(*_IDENT, "checkout", "-q", "feature", cwd=box.root)
    _git(*_IDENT, "rebase", "-q", "main", cwd=box.root)
    head = _git("rev-parse", "HEAD", cwd=box.root)
    assert head != old_tip

    result = _run(_line("refs/heads/feature", head, old_tip), cwd=box.root)
    assert result.returncode == 0, result.stderr
    assert box.calls() == [f"{TIER} --changed --base {new_main} {RUN_FLAGS}"], box.calls()


def test_a_commit_main_gained_after_the_branch_point_neither_widens_nor_narrows_the_run(
    tmp_path: Path,
):
    """The base is where the branch left main, whatever main did since: a commit that landed on
    main after it is not in the run's diff (`--base` is not an older commit, which would widen
    it, nor main's tip, which would leave the branch's own work out and narrow it)."""
    box = _sandbox(tmp_path, "branched")
    branch_point = _git("rev-parse", "HEAD", cwd=box.root)
    _git(*_IDENT, "checkout", "-q", "-b", "feature", cwd=box.root)
    (box.root / "mine.txt").write_text("the branch's own work\n", encoding="utf-8")
    head = _commit(box.root, "my work")
    _git(*_IDENT, "checkout", "-q", "main", cwd=box.root)
    (box.root / "theirs.txt").write_text("landed on main later\n", encoding="utf-8")
    later = _commit(box.root, "main moves on")
    _git("update-ref", "refs/remotes/origin/main", later, cwd=box.root)
    _git(*_IDENT, "checkout", "-q", "feature", cwd=box.root)

    for remote in (ZERO, branch_point, later):
        (box.root / "calls.txt").unlink(missing_ok=True)
        result = _run(_line("refs/heads/feature", head, remote), cwd=box.root)
        assert result.returncode == 0, result.stderr
        assert box.calls() == [f"{TIER} --changed --base {branch_point} {RUN_FLAGS}"], (
            remote,
            box.calls(),
        )
    diff = _git("diff", "--name-only", f"{branch_point}..{head}", cwd=box.root).split()
    assert diff == ["mine.txt"], "the base's diff is not the branch's own work"


def test_without_origin_main_the_old_remote_tip_is_the_base(tmp_path: Path):
    """A fork that tracks `upstream` has no `origin/main`: the remote's tip is the best base
    left, exact while the branch is a fast-forward and too wide after a rewrite."""
    box = _sandbox(tmp_path, "fork")
    remote = _git("rev-parse", "HEAD", cwd=box.root)
    (box.root / "notes.txt").write_text("more\n", encoding="utf-8")
    head = _commit(box.root, "more")
    result = _run(_line("refs/heads/feature", head, remote), cwd=box.root)
    assert result.returncode == 0, result.stderr
    assert box.calls() == [f"{TIER} --changed --base {remote} {RUN_FLAGS}"]


def test_no_shared_history_with_main_runs_every_pre_push_job(tmp_path: Path):
    box = _sandbox(tmp_path, "orphan")
    head = _git("rev-parse", "HEAD", cwd=box.root)
    result = _run(_line("refs/heads/feature", head, ZERO), cwd=box.root)
    assert result.returncode == 0, result.stderr
    assert box.calls() == [f"{TIER} {RUN_FLAGS}"], "a push with nothing to scope by was scoped"


def test_a_manual_run_from_a_terminal_runs_every_pre_push_job(sandbox: Sandbox):
    """No ref lines on stdin: a check of this tree, which runs the whole tier's pre-push jobs.
    `script -q` gives the hook a terminal for its stdin."""
    if not shutil.which("script"):
        pytest.skip("no `script` here to give the hook a terminal")
    argv = (
        ["script", "-q", "/dev/null", "sh", str(sandbox.script)]
        if sys.platform == "darwin"
        else ["script", "-q", "-e", "-c", f"sh {sandbox.script}", "/dev/null"]
    )
    done = subprocess.run(argv, cwd=sandbox.root, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert sandbox.calls() == [f"{TIER} {RUN_FLAGS}"]


# ── 3. The run's exit decides the push ───────────────────────────────────────────────────────


def test_the_hook_waits_at_most_a_minute_for_a_job_s_memory():
    """A run waits up to half an hour for a job's memory (tools/ci/admission.py). A push must not:
    on a full machine it would hang past a lane's ten-minute tool limit. So the hook bounds the
    wait, and a job that never gets its memory is not run, the run is partial, and the push goes
    through (the partial leg below)."""
    source = SCRIPT.read_text(encoding="utf-8")
    found = re.search(r"^ADMISSION_WAIT=(\d+)$", source, re.M)
    assert found, "the hook names no ADMISSION_WAIT"
    assert 0 < int(found.group(1)) <= 120, f"the hook may wait {found.group(1)} s for memory"
    assert '--admission-wait "$ADMISSION_WAIT"' in source, "the hook does not pass its bound"


def test_a_red_run_refuses_the_push(sandbox: Sandbox):
    sandbox.exits(1)
    result = _run(_line("refs/heads/feature", sandbox.head, ZERO), cwd=sandbox.root)
    assert result.returncode == 1
    assert "the change tier is red" in result.stderr
    assert len(sandbox.calls()) == 1


def test_a_partial_run_lets_the_push_through_and_says_so(sandbox: Sandbox):
    sandbox.exits(2)
    result = _run(_line("refs/heads/feature", sandbox.head, ZERO), cwd=sandbox.root)
    assert result.returncode == 0, result.stderr
    assert "the run is partial" in result.stdout and "CI runs every job" in result.stdout


def test_the_hook_says_how_long_the_run_took(sandbox: Sandbox):
    result = _run(_line("refs/heads/feature", sandbox.head, ZERO), cwd=sandbox.root)
    assert "pre-push: the run took " in result.stdout


def test_with_no_python_to_run_tools_ci_the_push_goes_through(sandbox: Sandbox, tmp_path):
    """No venv, and the first `python3` on PATH cannot start (a version-manager shim with no
    version set): the hook says so, and CI runs every job."""
    shutil.rmtree(sandbox.root / ".venv")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "python3"
    shim.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    shim.chmod(0o755)
    done = subprocess.run(
        ["sh", str(sandbox.script)],
        cwd=sandbox.root,
        input=_line("refs/heads/feature", sandbox.head, ZERO),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
    )
    assert done.returncode == 0, done.stderr
    assert "no Python 3.12 or newer" in done.stdout
