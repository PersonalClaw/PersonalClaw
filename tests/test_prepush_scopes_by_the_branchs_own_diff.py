"""The pre-push gate must scope its halves by what the BRANCH adds, not by how far
its base has moved.

**Measured gap (2026-09-01).** ``scripts/run_prepush.sh`` decided whether to run the
frontend render-smoke chain and the Python lint half from
``range="$remote_sha..$local_sha"`` — the old remote tip to the outgoing tip. Rebasing
rewrites a branch onto a newer ``main``, so that range spans every commit ``main`` gained
while the branch waited. Measured on a one-commit backend PR that had sat four days:

- ``$remote_sha..$local_sha`` (what the hook used): **37 commits, 96 frontend files**
- ``merge-base(local, origin/main)..local``: **1 commit, 0 frontend files**

So a backend-only push paid the ~20-minute ``npm ci`` → build → render-smoke chain for
somebody else's ``web/`` change — one that had already been gated when it landed. With a
queue of rebased branches, that is the dominant cost of draining it.

**Why the narrower range cannot under-run.** The only commits it drops are ancestors of
``origin/main``: already on ``main``, already gated on their own way in. For a stacked
branch it *over*-runs (the range then includes the parent PR's commits), which is the safe
direction. The fallbacks keep their old behaviour: no ``origin/main`` (a fresh clone that
has not fetched) falls back to the remote range, and no remote range either still gates
unconditionally rather than skipping blind.

**These tests drive the real script** with synthesized githooks(5) ref lines, against a
sandbox repo built to the rebase topology above:

- ``base``      — carries the script and ``notes.txt``
- ``original``  — the branch's tip before the rebase, touching ``notes.txt`` (owns nothing)
- ``main``      — ``main``'s advance, touching ``web/app.ts`` AND
  ``src/personalclaw/thing.py``, so it owns *both* halves and a leg that reads it is
  unmistakable
- ``rebased``   — ``original`` replayed onto ``main``: the outgoing commit, and HEAD

``refs/remotes/origin/main`` is set to ``main`` so the script's ``git merge-base
"$local_sha" origin/main`` resolves without a network.

**What is stubbed, and why that makes the evidence stronger rather than weaker.** The
observable under test is the *scoping decision*, not what either half then does. A first
version let the real tools run and took **18m43s** for 14 tests — it would have added
nineteen minutes to CI to prove a change whose entire purpose is to remove twenty. So
``npm``, ``npx``, ``black``, ``isort`` and ``flake8`` are replaced by stubs on ``PATH``
that append their own name to a log and exit 0. A leg that expects a half to fire then
asserts **the stub was reached** — the chain was genuinely entered — and a leg that
expects a half to be skipped asserts **the log is empty**. That is a sharper pair of
assertions than reading announcements off stdout, and the two directions cannot both be
satisfied by one bug. Nothing about the script itself is stubbed.

The sandbox has no ``.venv``, so the script's ``[ -x .venv/bin/black ]`` branch is not
taken and the stubs on ``PATH`` are what it resolves — asserted by
:func:`test_the_stub_bin_is_what_the_script_resolves`, so a future change that finds a
real toolchain first reds this file instead of silently timing out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_prepush.sh"

ZERO = "0" * 40

#: The scoping line under test, verbatim. The vacuity leg swaps this exact line back to
#: the old remote-range form; if it is ever reworded, that leg reds rather than quietly
#: measuring nothing.
MERGE_BASE_SCOPING = 'if base=$(git merge-base "$local_sha" origin/main 2>/dev/null); then'
OLD_REMOTE_SCOPING = "if false; then  # neutered: fall through to the remote range"

#: Every tool either half shells out to. Stubbed so entering a half is cheap and
#: observable. `git` is deliberately NOT here — the script's own git calls are the thing
#: being measured.
STUBBED_TOOLS = ("npm", "npx", "black", "isort", "flake8")

FRONTEND_SKIPPED = "no frontend changes outgoing"


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


def _commit(root: Path, message: str, files: dict[str, str]) -> str:
    """Write `files` (repo-relative paths → contents) and commit them, returning the sha."""
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", message, cwd=root)
    return _git("rev-parse", "HEAD", cwd=root)


class Rebased:
    """A sandbox repo at the rebase topology, plus the shas the ref lines need."""

    def __init__(self, root: Path, original: str, main: str, rebased: str, stub_bin: Path) -> None:
        self.root = root
        self.script = root / "scripts" / "run_prepush.sh"
        #: The branch's tip BEFORE the rebase — what the remote still points at.
        self.original = original
        #: ``main``'s advance, touching a path in each half's path list.
        self.main = main
        #: The branch replayed onto ``main`` — the outgoing commit, and this tree's HEAD.
        self.rebased = rebased
        self.stub_bin = stub_bin
        self.stub_log = stub_bin / "reached.log"

    def reached(self) -> list[str]:
        """Which stubbed tools the script actually invoked, in order."""
        if not self.stub_log.exists():
            return []
        return [line for line in self.stub_log.read_text(encoding="utf-8").split() if line]


@pytest.fixture
def rebased(tmp_path: Path) -> Rebased:
    root = tmp_path / "sandbox"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "run_prepush.sh")
    assert (
        root / "scripts" / "run_prepush.sh"
    ).read_bytes() == SCRIPT.read_bytes(), "the sandbox copy is not the shipped script"

    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    log = stub_bin / "reached.log"
    for tool in STUBBED_TOOLS:
        stub = stub_bin / tool
        stub.write_text(f'#!/bin/sh\necho "{tool}" >> "{log}"\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)

    _git("init", "-q", "-b", "main", cwd=root)
    # The script lands in the FIRST commit so no later range can name it: it is itself one
    # of the paths that owns the frontend half.
    _commit(root, "base", {"notes.txt": "one\n"})

    _git(*_IDENT, "checkout", "-q", "-b", "feature", cwd=root)
    original = _commit(root, "the branch's own change", {"notes.txt": "mine\n"})

    _git(*_IDENT, "checkout", "-q", "main", cwd=root)
    main = _commit(
        root,
        "main advances, touching both halves",
        {"web/app.ts": "export const x = 1;\n", "src/personalclaw/thing.py": "VALUE = 1\n"},
    )

    # A real rebase, so the topology is not a mock of one.
    _git(*_IDENT, "checkout", "-q", "feature", cwd=root)
    _git(*_IDENT, "rebase", "main", cwd=root)
    rebased_tip = _git("rev-parse", "HEAD", cwd=root)

    _git("update-ref", "refs/remotes/origin/main", main, cwd=root)

    assert rebased_tip not in (original, main), "the rebase did not produce a new commit"
    assert _git("merge-base", rebased_tip, "origin/main", cwd=root) == main, (
        "origin/main is not the rebased branch's merge-base, so this sandbox does not "
        "reproduce the topology under test"
    )
    assert not (root / ".venv").exists(), (
        "the sandbox grew a .venv — the script would resolve its lint tools there instead "
        "of from the stub PATH, and the lint legs below would run the real toolchain"
    )
    return Rebased(root, original, main, rebased_tip, stub_bin)


def _run(
    stdin: str, sandbox: Rebased, *, script: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real gate with `stdin` as its githooks(5) ref lines and stubs on PATH."""
    env = dict(os.environ)
    env["PATH"] = f"{sandbox.stub_bin}:{env.get('PATH', '')}"
    return subprocess.run(
        ["sh", str(script or sandbox.script)],
        cwd=sandbox.root,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def _push_line(sandbox: Rebased) -> str:
    """The ref line git writes for a force-push of the rebased branch."""
    return f"refs/heads/feature {sandbox.rebased} refs/heads/feature {sandbox.original}\n"


def test_the_shipped_script_still_scopes_by_the_merge_base():
    """A floor for every leg below: the thing under test must be present."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count(MERGE_BASE_SCOPING) == 1, (
        "scripts/run_prepush.sh no longer scopes by merge-base with origin/main as a "
        "unique line — the legs below would be measuring some other path. Restore it, do "
        "not weaken them."
    )


def test_the_stub_bin_is_what_the_script_resolves(rebased: Rebased):
    """The stubs must be what the script finds, or every 'fires' leg below is vacuous.

    Forces both halves via the unconditional-gate fallback (no ``origin/main``, no remote
    tip) and asserts each half's tools were reached. If the script ever resolves a real
    toolchain first, this reds here rather than turning the other legs into 20-minute
    timeouts.
    """
    _git("update-ref", "-d", "refs/remotes/origin/main", cwd=rebased.root)
    result = _run(f"refs/heads/feature {rebased.rebased} refs/heads/feature {ZERO}\n", rebased)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    reached = rebased.reached()
    assert "npm" in reached, f"the frontend half did not reach npm: reached={reached}"
    assert "black" in reached, f"the lint half did not reach black: reached={reached}"


def test_a_rebased_backend_branch_does_not_pay_the_frontend_gate(rebased: Rebased):
    """The whole point: main's web change is not the branch's, so neither half is owed.

    ``main``'s advance touches a path owned by *both* halves; the branch's own commit
    touches ``notes.txt``, owned by neither. Under the narrow range the script must take
    its cheap exit without invoking a single tool.
    """
    result = _run(_push_line(rebased), rebased)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert FRONTEND_SKIPPED in result.stdout, f"stdout={result.stdout!r}"
    assert rebased.reached() == [], (
        "the gate invoked tools for a branch that changed neither web/ nor Python — the "
        f"range is still spanning main's advance: reached={rebased.reached()}"
    )


def test_the_skip_is_attributable_to_the_merge_base_range(rebased: Rebased):
    """Vacuity: the SAME stdin must pay both halves once the scoping is reverted.

    Without this leg the test above would pass equally against a sandbox whose topology
    happened to touch nothing. This proves the two scopings genuinely disagree here, which
    is the measured claim.
    """
    source = rebased.script.read_text(encoding="utf-8")
    assert source.count(MERGE_BASE_SCOPING) == 1, "the scoping line is not unique"
    reverted = rebased.root / "scripts" / "run_prepush_old_scoping.sh"
    reverted.write_text(source.replace(MERGE_BASE_SCOPING, OLD_REMOTE_SCOPING), encoding="utf-8")
    assert OLD_REMOTE_SCOPING in reverted.read_text(encoding="utf-8"), "the revert did not apply"

    _run(_push_line(rebased), rebased, script=reverted)
    reached = rebased.reached()
    assert "npm" in reached, (
        "the old scoping did NOT reach the frontend chain on this topology, so the leg "
        f"above is not measuring the fix: reached={reached}"
    )
    assert "black" in reached, (
        "the old scoping did not reach the lint half either, so main's advance is not "
        f"being read as the branch's: reached={reached}"
    )


def test_a_branch_whose_own_diff_touches_the_frontend_still_gates(rebased: Rebased):
    """No under-run: the narrow range must still see the branch's OWN web change."""
    tip = _commit(rebased.root, "my own web change", {"web/mine.ts": "export const mine = 1;\n"})
    line = f"refs/heads/feature {tip} refs/heads/feature {rebased.original}\n"
    _run(line, rebased)
    assert "npm" in rebased.reached(), (
        "a branch that changes web/ itself skipped the render-smoke chain — the scoping "
        f"under-runs: reached={rebased.reached()}"
    )


def test_a_branch_whose_own_diff_touches_python_still_lints(rebased: Rebased):
    """No under-run for the other half: the branch's own Python change is still seen."""
    tip = _commit(rebased.root, "my own python change", {"src/personalclaw/mine.py": "MINE = 1\n"})
    line = f"refs/heads/feature {tip} refs/heads/feature {rebased.original}\n"
    _run(line, rebased)
    reached = rebased.reached()
    assert "black" in reached, (
        "a branch that changes src/personalclaw/ itself skipped the lint half — the "
        f"scoping under-runs: reached={reached}"
    )
    assert "npm" not in reached, (
        "that branch touched no web/ path, so the frontend chain should not have been "
        f"entered: reached={reached}"
    )


def test_a_first_push_without_origin_main_still_gates_unconditionally(rebased: Rebased):
    """The blind-spot fallback: no merge-base AND no remote range means gate everything.

    A fresh clone that has not fetched has no ``origin/main``, and a brand-new branch has
    no remote tip either, so there is nothing to diff against. The script must not read
    that as "nothing to do".
    """
    _git("update-ref", "-d", "refs/remotes/origin/main", cwd=rebased.root)
    line = f"refs/heads/feature {rebased.rebased} refs/heads/feature {ZERO}\n"
    _run(line, rebased)
    reached = rebased.reached()
    assert "npm" in reached and "black" in reached, (
        "with neither a merge-base nor a remote range the gate skipped a half instead of "
        f"running both: reached={reached}"
    )


def test_without_origin_main_a_force_push_falls_back_to_the_remote_range(rebased: Rebased):
    """The middle fallback: no ``origin/main``, but a real remote tip to diff against.

    This is the pre-fix behaviour, kept deliberately for the case where it is the only
    information available — which is what makes the change a narrowing rather than a
    replacement.
    """
    _git("update-ref", "-d", "refs/remotes/origin/main", cwd=rebased.root)
    _run(_push_line(rebased), rebased)
    assert "npm" in rebased.reached(), (
        "the remote-range fallback did not fire on a range that spans main's advance: "
        f"reached={rebased.reached()}"
    )
