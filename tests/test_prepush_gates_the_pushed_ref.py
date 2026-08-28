"""The pre-push gate must refuse a tree that is not the ref being pushed.

**Measured gap (2026-08-27).** ``scripts/run_prepush.sh`` read the ref ranges
githooks(5) puts on stdin and used ``local_sha`` for exactly one purpose: computing
``range="$remote_sha..$local_sha"`` to decide *whether* the frontend half
(``needs_gate``) and the Python half (``needs_lint``) should run at all. It then ran
black/isort/flake8 and ``npm ci`` → build → ``scripts/render_smoke.mjs`` against
``git rev-parse --show-toplevel`` — the **working tree**.
``git grep -c "rev-parse HEAD" scripts/run_prepush.sh`` returned **0**: nothing
asserted the tree was the thing being pushed.

That is a live hazard, not a theoretical one, because this repo is worked with many
``git worktree``s at once. ``git push origin some-branch`` from a checkout sitting on
``main`` scoped the gate by ``some-branch``'s diff and then validated ``main``'s tree
— green, and proof of nothing about what shipped. Batching (``git push origin br1 br2
br3``) to pay the ~20-minute ``npm ci`` + render-smoke cost once instead of three
times is exactly the shape that produces it: one hook run, three unvalidated
branches, ``main``'s tree gated three times.

**These tests drive the real script**, with synthesized stdin ref lines. Re-deriving
the ref-line parsing in Python would assert nothing about the shipped file — the file
is the gate.

**Why a sandbox repo.** The script resolves everything through
``git rev-parse --show-toplevel``, so the only way to control both ``HEAD`` and the
diff ranges is to point it at a repo built for the purpose:
:func:`sandbox` makes one with two commits and a byte-identical copy of the shipped
script, asserted identical. That is also what makes these legs portable — an earlier
version read *this* repo's history and errored in CI, because ``actions/checkout``
clones shallow (``fetch-depth: 1``) and ``HEAD~1`` does not exist there.

**Cheap by construction, not by mocking.** The guard sits inside the ref loop, before
the range is computed, so every refusing leg exits before either expensive half is
reached. The non-refusing legs feed a ref line whose range is empty, so both halves
are correctly skipped. The sandbox holds no ``web/`` or ``src/personalclaw`` path
either, so nothing there can pull in ``npm ci``. Nothing is stubbed.

**Vacuity.** :func:`test_the_refusal_is_attributable_to_the_guard` reruns the exact
stdin of the negative leg against a copy of the script whose *own* condition is
neutered to ``if false``, and asserts that copy exits 0. So the negative leg's red is
attributable to the guard rather than to any other non-zero exit the script can
produce, and the substitution is asserted to have applied — a rename of that line
reds this test instead of silently making it vacuous.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_prepush.sh"

ZERO = "0" * 40

#: The guard's own condition, verbatim. The vacuity leg neuters this exact line; if it
#: is ever reworded, that leg reds rather than quietly measuring nothing.
GUARD_CONDITION = 'if [ "$pushed_commit" != "$head_commit" ]; then'

REFUSAL = "refusing to gate a tree that is not what you are pushing"
#: What the script prints when it reached the END of the ref loop with neither half
#: owed — the only cheap exit-0 path, and therefore a non-refusing leg's proof that it
#: got *past* the guard rather than never reaching it.
REACHED_THE_END = "no frontend changes outgoing"

#: Identity plus two neutralizers: the developer's own template hooks must not fire in
#: the sandbox, and a configured signing key must not make these commits interactive.
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


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


class Sandbox:
    """A throwaway repo carrying the shipped script, plus its two commit shas."""

    def __init__(self, root: Path, head: str, parent: str) -> None:
        self.root = root
        self.script = root / "scripts" / "run_prepush.sh"
        self.head = head
        #: A real commit that is not ``HEAD``, so the negative legs are not vacuous.
        self.parent = parent


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path / "sandbox"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "run_prepush.sh")
    assert (
        root / "scripts" / "run_prepush.sh"
    ).read_bytes() == SCRIPT.read_bytes(), "the sandbox copy is not the shipped script"

    _git("init", "-q", "-b", "main", cwd=root)
    # The FIRST commit carries the script, so no later range can name it. It is one of
    # the paths that owns the frontend half, and a range touching it would drag `npm ci`
    # into a test. Every subsequent commit only touches `notes.txt`, which owns nothing.
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "first", cwd=root)
    (root / "notes.txt").write_text("two\n", encoding="utf-8")
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-am", "second", cwd=root)

    head = _git("rev-parse", "HEAD", cwd=root)
    parent = _git("rev-parse", "HEAD~1", cwd=root)
    assert head != parent, "the sandbox has no second commit"
    return Sandbox(root, head, parent)


def _run(stdin: str, *, cwd: Path, script: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the real gate with `stdin` as its githooks(5) ref lines."""
    return subprocess.run(
        ["sh", str(script or (cwd / "scripts" / "run_prepush.sh"))],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _assert_refused(result: subprocess.CompletedProcess[str]) -> None:
    """A refusal is a non-zero exit *taken by the guard*, before anything else ran.

    The empty stdout is the load-bearing half. Found while falsifying: with the guard
    softened to print-and-continue, a ref line whose ``remote_sha`` is all-zeroes fell
    through to the merge-base branch, gated unconditionally, and then exited non-zero
    because black had no ``src/personalclaw`` to check in the sandbox. Asserting only
    ``returncode != 0`` and the message therefore read as green against a guard that had
    been turned into a warning — exactly the false green this file exists to close. The
    guard writes to stderr and exits before either half announces itself on stdout, so an
    empty stdout is what separates "refused" from "warned, then failed for another
    reason".
    """
    assert result.returncode != 0, f"the guard did not fire: stdout={result.stdout!r}"
    assert REFUSAL in result.stderr
    assert result.stdout == "", (
        "the script kept going past the guard, so this non-zero exit is not the refusal: "
        f"stdout={result.stdout!r}"
    )


def test_the_shipped_script_still_carries_the_guard():
    """A floor for every leg below: the thing under test must be present."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert GUARD_CONDITION in source, (
        "the tree/ref guard's condition is gone from scripts/run_prepush.sh — the legs "
        "below would be measuring some other exit path. Restore it, do not weaken them."
    )


def test_pushing_this_worktrees_head_is_not_refused(sandbox: Sandbox):
    """The positive leg: the tree IS the ref, so the guard stands aside.

    ``remote_sha == local_sha == HEAD`` is an empty range, so both halves are owed
    nothing and the script takes its cheap exit — which is also how this leg proves it
    ran past the guard instead of never reaching it.
    """
    result = _run(
        f"refs/heads/some-branch {sandbox.head} refs/heads/some-branch {sandbox.head}\n",
        cwd=sandbox.root,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REFUSAL not in result.stderr
    assert (
        REACHED_THE_END in result.stdout
    ), f"the script did not reach the end of the ref loop: stdout={result.stdout!r}"


def test_the_real_repo_does_not_refuse_its_own_head():
    """The same positive leg, in place, against the checkout this file ships in.

    The sandbox proves the logic; this proves the shipped file behaves that way where it
    actually runs. ``HEAD`` exists even in CI's shallow clone, and ``HEAD..HEAD`` is an
    empty range, so this stays cheap.
    """
    head = _git("rev-parse", "HEAD", cwd=REPO_ROOT)
    result = _run(
        f"refs/heads/some-branch {head} refs/heads/some-branch {head}\n",
        cwd=REPO_ROOT,
        script=SCRIPT,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REFUSAL not in result.stderr


def test_pushing_a_ref_this_worktree_does_not_have_checked_out_is_refused(sandbox: Sandbox):
    """The negative leg: non-zero exit, and the message names BOTH shas.

    Naming both is the whole remedy — "wrong tree" without the two commits leaves the
    reader guessing which worktree to go to. ``HEAD`` is read off the checkout by the
    script itself, never from stdin, which is why the ref line can carry the same sha on
    both sides: that keeps the range empty, so the guard-free copy the vacuity leg runs
    on this *identical* stdin also takes the cheap exit.
    """
    result = _run(
        f"refs/heads/other-branch {sandbox.parent} refs/heads/other-branch {sandbox.parent}\n",
        cwd=sandbox.root,
    )
    _assert_refused(result)
    assert sandbox.parent in result.stderr, "the refusal does not name the pushed commit"
    assert sandbox.head in result.stderr, "the refusal does not name the checkout's HEAD"
    assert "git worktree list" in result.stderr, "the refusal does not name the fix"


def test_a_new_remote_branch_is_guarded_too(sandbox: Sandbox):
    """The most common first push — ``remote_sha`` all-zeroes — is not a hole.

    The guard runs before the ``remote_sha == ZERO`` merge-base branch, so a brand-new
    branch pushed from the wrong worktree is refused on the same terms.
    """
    result = _run(
        f"refs/heads/brand-new {sandbox.parent} refs/heads/brand-new {ZERO}\n",
        cwd=sandbox.root,
    )
    _assert_refused(result)


def test_a_branch_deletion_is_not_refused(sandbox: Sandbox):
    """The deletion leg: an all-zeroes ``local_sha`` names no tree to check.

    ``git push origin --delete some-branch`` ships no commits, so there is nothing for
    the guard to compare and a refusal here would be a false one.
    """
    result = _run(f"(delete) {ZERO} refs/heads/some-branch {sandbox.head}\n", cwd=sandbox.root)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REFUSAL not in result.stderr


def test_the_refusal_is_attributable_to_the_guard(sandbox: Sandbox):
    """Vacuity: the same stdin passes once the guard's own condition is neutered.

    Without this, the negative leg could be reading any of the script's other non-zero
    exits. A guard whose test cannot fail is not a guard.
    """
    source = sandbox.script.read_text(encoding="utf-8")
    assert source.count(GUARD_CONDITION) == 1, "the guard condition is not a unique line"
    neutered = sandbox.root / "scripts" / "run_prepush_without_the_guard.sh"
    neutered.write_text(source.replace(GUARD_CONDITION, "if false; then"), encoding="utf-8")
    assert "if false; then" in neutered.read_text(encoding="utf-8"), "the neuter did not apply"

    stdin = f"refs/heads/other-branch {sandbox.parent} refs/heads/other-branch {sandbox.parent}\n"
    _assert_refused(_run(stdin, cwd=sandbox.root))

    result = _run(stdin, cwd=sandbox.root, script=neutered)
    assert result.returncode == 0, (
        "the guard-free copy still failed, so the negative leg above is not measuring "
        f"the guard: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert REFUSAL not in result.stderr
    assert REACHED_THE_END in result.stdout, (
        "the guard-free copy exited 0 without reaching the end of the ref loop, so this "
        f"leg is not comparing the same path: stdout={result.stdout!r}"
    )
