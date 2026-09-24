"""``scripts/run_prepush.sh``: on a TOPIC branch, CI is the render-smoke gate — but on
``main``, ``release/*`` and tags the full local chain still runs.

**Why this file exists (owner ruling 2026-09-18).** The render-smoke chain costs ~20 minutes
(the ``npm ci`` note at the top of the script) and an autonomous worker's stream watchdog is
600s, so no worker could push a frontend branch at all. Measured on this repo the day of the
ruling: 72 of 184 remote branches ahead of ``main`` touched ``web/``, and 76 open issues were
labelled ``area:frontend``. A gate that cannot be satisfied is not protection; it is a queue
that quietly stops moving.

**The invariant that must NOT be weakened, and the direction that matters.** The dangerous
change is not "topic branches skip" — that is the ruling. It is the skip *spreading* to a
release ref, which is exactly where the v0.1.0 blank-dashboard regression this rail was built
for actually shipped from. So the legs below pin BOTH directions:

* the skip leg — a topic branch never reaches the chain;
* the vacuity floor — ``main``, ``release/*`` and a tag all still DO reach it, so the skip can
  never quietly become unconditional;
* a source floor — the shipped script still carries the ``release_ref`` test, so the guard
  cannot be deleted while these tests keep passing against something else;
* an attribution leg — a copy with that one test neutered DOES reach the chain on a topic ref,
  proving the skip is caused by the ref rule rather than by some unrelated earlier ``exit 0``.

Nothing here asserts that CI is *configured* to run the chain — that is a different file's
job (``test_ci_tier_enforcement.py``) and claiming it here would be a promise this test cannot
keep.

**These tests DRIVE THE REAL SCRIPT** with synthesized githooks(5) stdin, for the same reason
the sibling ``test_prepush_gates_the_pushed_ref.py`` states: re-deriving the shell in Python
would assert nothing about the shipped gate. npm/node/npx are exit-0 stubs so reaching the
chain is observable without a 20-minute build.
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

#: The guard's own condition, verbatim. The attribution leg neuters this exact line; if it is
#: ever reworded, the source floor reds rather than the attribution leg silently measuring
#: nothing.
GUARD_CONDITION = 'if [ "$release_ref" -eq 0 ]; then'

#: The distinctive middle of the new skip message — this phrase appears on exactly one line of
#: the script, so it cannot be confused with the "no frontend changes" skip or with the
#: toolchain-absent skip.
CI_GATES = "CI is the render-smoke gate"

#: The announcement printed the instant the chain is actually entered — before ``npm ci``. Its
#: presence proves the ref was treated as release-grade; its absence proves it was not.
GATING = "frontend changes outgoing — running the render-smoke gate"

#: The pre-existing skip, for the leg proving a non-frontend topic push is untouched by this
#: change.
NO_FRONTEND = "no frontend changes outgoing"

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
    """A throwaway repo carrying the shipped script and a branch whose only change is a
    ``web/`` file, so ``needs_gate=1`` and the ref rule is the only thing left to decide."""

    def __init__(self, root: Path, head: str, fakebin: Path) -> None:
        self.root = root
        self.script = root / "scripts" / "run_prepush.sh"
        self.head = head
        self.fakebin = fakebin

    def push_line(self, ref: str) -> str:
        """A githooks(5) stdin line pushing this frontend-touching HEAD as ``ref``.

        ``local_sha == HEAD`` so the tree/ref guard stands aside; ``remote_sha`` is
        all-zeroes (a first push) while ``origin/main`` exists, so the script scopes by
        ``merge-base(HEAD, origin/main)..HEAD`` — which contains the ``web/`` file.
        """
        return f"{ref} {self.head} {ref} {ZERO}\n"


def _write_exit0_stub(path: Path, log: Path) -> None:
    # The PATH mirror below may already have symlinked the REAL tool here, and writing through
    # that symlink would edit the tool itself: EPERM where it is root-owned (CI's node toolcache),
    # and a clobbered npm where it is not. Drop the link first so the stub is a fresh file.
    path.unlink(missing_ok=True)
    path.write_text("#!/bin/sh\n" f'printf "%s\\n" "$*" >> "{log}"\n' "exit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _mirror_path_with_stubs(dest: Path, log: Path) -> None:
    """Mirror the real PATH into ``dest``, then add exit-0 npm/node/npx stubs.

    Mirroring rather than curating keeps ``git`` and its helpers resolvable so the script runs
    as it would in a real shell; the stubs make "the chain was entered" observable without
    paying for a real build, and are what stop this test from depending on where node lives.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            contents = list(Path(entry).iterdir())
        except OSError:
            continue
        for tool in contents:
            link = dest / tool.name
            if link.exists() or link.is_symlink():
                continue  # first occurrence on PATH wins, like the shell's own lookup
            try:
                if tool.is_dir() or not os.access(tool, os.X_OK):
                    continue
                link.symlink_to(tool)
            except OSError:
                continue
    for tool in ("npm", "node", "npx"):
        _write_exit0_stub(dest / tool, log)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path / "sandbox"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "run_prepush.sh")
    assert (
        root / "scripts" / "run_prepush.sh"
    ).read_bytes() == SCRIPT.read_bytes(), "the sandbox copy is not the shipped script"

    _git("init", "-q", "-b", "main", cwd=root)
    # The first commit carries the script (itself a FRONTEND_PATH) so no later range names it
    # and drags the chain in for the wrong reason. Nothing here touches PYTHON_PATHS, so the
    # lint half stays unowed.
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "first", cwd=root)
    base = _git("rev-parse", "HEAD", cwd=root)
    _git("update-ref", "refs/remotes/origin/main", base, cwd=root)

    (root / "web").mkdir()
    (root / "web" / "Thing.tsx").write_text("export const Thing = () => null\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "frontend change", cwd=root)
    head = _git("rev-parse", "HEAD", cwd=root)
    assert head != base, "the branch adds no commit"

    fakebin = tmp_path / "fakebin"
    _mirror_path_with_stubs(fakebin, tmp_path / "invocations.log")
    return Sandbox(root, head, fakebin)


def _run(
    stdin: str, *, cwd: Path, fakebin: Path, script: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = str(fakebin)
    return subprocess.run(
        ["sh", str(script or (cwd / "scripts" / "run_prepush.sh"))],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def test_a_topic_branch_lets_ci_gate_the_frontend(sandbox: Sandbox) -> None:
    """The ruling itself: a topic branch touching ``web/`` does not run the chain locally."""
    result = _run(
        sandbox.push_line("refs/heads/improvement-something"),
        cwd=sandbox.root,
        fakebin=sandbox.fakebin,
    )
    assert result.returncode == 0, f"the push was refused: {result.stderr!r}"
    assert CI_GATES in result.stdout, (
        "a topic branch still ran the ~20-minute chain, so the ruling did not take: "
        f"stdout={result.stdout!r}"
    )
    assert GATING not in result.stdout, (
        "the chain was entered on a topic branch: " f"stdout={result.stdout!r}"
    )


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/main",
        "refs/heads/release/0.2",
        "refs/tags/v0.2.0",
    ],
)
def test_release_refs_still_run_the_chain_locally(sandbox: Sandbox, ref: str) -> None:
    """The vacuity floor, and the leg that matters most.

    The dangerous direction is the skip spreading to a release ref — which is precisely where
    the v0.1.0 blank-dashboard regression shipped from. If this ever reds, the gate has become
    unconditional and the rail is gone.
    """
    result = _run(sandbox.push_line(ref), cwd=sandbox.root, fakebin=sandbox.fakebin)
    assert GATING in result.stdout, (
        f"{ref} skipped the render-smoke chain — the local gate is now unconditional and the "
        f"rail this file protects is gone: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert CI_GATES not in result.stdout


def test_a_topic_branch_with_no_frontend_change_is_untouched(sandbox: Sandbox) -> None:
    """The pre-existing scoping still decides first: no ``web/`` diff, no gate owed at all.

    Without this, a bug that set ``needs_gate=1`` unconditionally would be masked by the new
    skip and both would read as "skipped" for indistinguishable reasons.
    """
    # Branch from ``origin/main``, NOT from HEAD: the fixture's ``web/`` commit sits on HEAD,
    # so a branch taken from there would still carry it inside
    # ``merge-base(HEAD, origin/main)..HEAD`` and this test would be measuring the frontend
    # path while claiming to measure the non-frontend one.
    base = _git("rev-parse", "refs/remotes/origin/main", cwd=sandbox.root)
    _git(*_IDENT, "checkout", "-q", "-b", "no-frontend-branch", base, cwd=sandbox.root)
    (sandbox.root / "notes.txt").write_text("two\n", encoding="utf-8")
    _git("add", "-A", cwd=sandbox.root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "no frontend", cwd=sandbox.root)
    head = _git("rev-parse", "HEAD", cwd=sandbox.root)
    assert not _git(
        "diff", "--name-only", f"{base}..{head}", "--", "web", cwd=sandbox.root
    ), "sanity: this branch must add no web/ change, or the test measures the wrong path"
    result = _run(
        f"refs/heads/improvement-something {head} refs/heads/improvement-something {ZERO}\n",
        cwd=sandbox.root,
        fakebin=sandbox.fakebin,
    )
    assert NO_FRONTEND in result.stdout, (
        "a branch with no frontend diff took the new topic-branch path instead of the "
        f"pre-existing scoping path: stdout={result.stdout!r}"
    )
    assert CI_GATES not in result.stdout


def test_the_shipped_script_still_carries_the_release_ref_guard() -> None:
    """Source floor: the guard the other legs attribute the skip to still exists, verbatim.

    Pinning the exact line means a reword reds HERE — loudly and in one place — rather than
    leaving the attribution leg below silently measuring nothing.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert GUARD_CONDITION in source, (
        "scripts/run_prepush.sh no longer carries the release_ref guard this file pins; "
        "if it was deliberately reworded, update GUARD_CONDITION here in the same change"
    )
    assert "refs/heads/main|refs/heads/release/*|refs/tags/*" in source, (
        "the release-ref match is gone or narrowed — release pushes may no longer run the "
        "full local chain"
    )


def test_the_skip_is_attributable_to_the_release_ref_guard(sandbox: Sandbox) -> None:
    """Attribution: neuter that ONE test and the same topic push reaches the chain.

    This is what separates "the ref rule caused the skip" from "something earlier exited 0 and
    the rule is dead code" — the failure mode that would make every other leg here vacuous.
    """
    neutered = sandbox.root / "scripts" / "neutered.sh"
    source = sandbox.script.read_text(encoding="utf-8")
    assert GUARD_CONDITION in source
    neutered.write_text(
        source.replace(GUARD_CONDITION, 'if [ "$release_ref" -eq 99 ]; then'),
        encoding="utf-8",
    )
    result = _run(
        sandbox.push_line("refs/heads/improvement-something"),
        cwd=sandbox.root,
        fakebin=sandbox.fakebin,
        script=neutered,
    )
    assert GATING in result.stdout, (
        "with the release_ref guard neutered the topic push STILL did not reach the chain, so "
        "the skip proved by the other legs is caused by something else and they are vacuous: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
