"""``scripts/run_prepush.sh``: the render-smoke half must DEGRADE GRACEFULLY when the web
toolchain (npm/node) is absent from PATH — exactly as the Python-lint half already does.

**Why this file exists (directive #30(C), 2026-09-17).** The render-smoke chain — ``npm
ci`` -> ``typecheck:web`` -> vitest -> ``vite build`` -> ``npx playwright`` -> render
smoke — ran UNCONDITIONALLY once a frontend path was outgoing. npm/node are mise-managed
on this machine and are NOT on the default PATH, so a local ``git push`` of any branch
touching a frontend path died on ``npm: command not found``. Because
``scripts/run_prepush.sh`` is itself one of ``FRONTEND_PATHS``, that included every
attempt to push a fix TO the gate — the dominant blocker for web-touching branches. The
Python half already handled its own missing tools (black/isort/flake8) by printing a skip
line and letting the push through; this makes the render-smoke half symmetric.

**The invariant that must NOT be weakened.** CI runs with npm/node present, so it still
runs the full chain. This is *local graceful degradation only*. The two behavioural legs
pin both directions — skip-when-absent
(:func:`test_render_smoke_skips_when_toolchain_absent`) and its vacuity floor
run-when-present (:func:`test_render_smoke_runs_when_toolchain_present`) — plus a source
floor (:func:`test_the_shipped_script_still_carries_the_toolchain_guard`) and an
attribution leg (:func:`test_the_skip_is_attributable_to_the_guard`) so the skip can never
quietly become vacuous.

**These tests DRIVE THE REAL SCRIPT** with synthesized githooks(5) stdin and a PATH they
control, for the same reason the sibling ``test_prepush_gates_the_pushed_ref.py`` states:
re-deriving the shell in Python would assert nothing about the shipped gate. Toolchain
presence is controlled by pointing PATH at a ``fakebin`` that mirrors the real PATH minus
npm/node/npx, so ``command -v npm`` fails deterministically while ``git`` and everything
else the script needs still resolve — independent of where node happens to live on the
host (mac dev vs Linux CI). The present-toolchain leg then drops exit-0 stubs for
npm/node/npx into that same ``fakebin`` and logs their invocations, so it can prove the
chain actually ran rather than merely that the guard let it through.
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

#: The guard's own condition, verbatim. The attribution leg neuters this exact line; if it
#: is ever reworded, the source floor reds rather than the attribution leg quietly measuring
#: nothing.
GUARD_CONDITION = "if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then"

#: The distinctive middle of the skip message. Deliberately not "frontend changes outgoing"
#: (a substring shared with both the GATING announcement and the "no frontend changes"
#: skip) nor "npm/node" alone — this phrase appears on exactly one line of the script.
TOOLCHAIN_SKIP = "web toolchain (npm/node) not found"
#: The announcement printed the instant the render-smoke chain is actually entered — before
#: ``npm ci``. Its presence proves the guard let the chain through; its absence proves the
#: guard skipped before it.
GATING = "frontend changes outgoing — running the render-smoke gate"
#: Printed only after every step of the chain returned 0.
GREEN = "render-smoke gate green"

#: Identity plus two neutralizers, copied from the sibling gate test: the developer's own
#: template hooks must not fire in the sandbox, and a configured signing key must not make
#: these commits interactive.
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
    """A throwaway repo carrying the shipped script, a frontend-owed branch, and the ref
    line that scopes the render-smoke half ON."""

    def __init__(self, root: Path, head: str) -> None:
        self.root = root
        self.script = root / "scripts" / "run_prepush.sh"
        self.head = head

    @property
    def frontend_push(self) -> str:
        """A githooks(5) stdin line for pushing this branch's frontend-touching HEAD.

        ``local_sha == HEAD`` so the tree/ref guard stands aside; ``remote_sha`` is
        all-zeroes (a first push) but ``origin/main`` exists, so the script scopes by
        ``merge-base(HEAD, origin/main)..HEAD`` — which contains the ``web/`` file, so
        ``needs_gate=1`` and the render-smoke half is owed.
        """
        return f"refs/heads/main {self.head} refs/heads/main {ZERO}\n"


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path / "sandbox"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "run_prepush.sh")
    assert (
        root / "scripts" / "run_prepush.sh"
    ).read_bytes() == SCRIPT.read_bytes(), "the sandbox copy is not the shipped script"

    _git("init", "-q", "-b", "main", cwd=root)
    # First commit carries the script (itself a FRONTEND_PATH), so no later range names it
    # — otherwise the range would drag `npm ci` in for the wrong reason. Nothing here
    # touches PYTHON_PATHS, so the lint half stays unowed and black/isort/flake8 (which are
    # deliberately NOT in the fakebin) are never invoked.
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "first", cwd=root)
    base = _git("rev-parse", "HEAD", cwd=root)
    _git("update-ref", "refs/remotes/origin/main", base, cwd=root)

    # A branch whose only change is a `web/` file, so the render-smoke half is owed and the
    # lint half is not.
    _git(*_IDENT, "checkout", "-q", "-b", "feature", cwd=root)
    (root / "web").mkdir()
    (root / "web" / "Thing.tsx").write_text("export const Thing = () => null\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(*_IDENT, "commit", "-q", "--no-gpg-sign", "-m", "frontend change", cwd=root)
    head = _git("rev-parse", "HEAD", cwd=root)
    assert head != base, "the branch adds no commit"
    return Sandbox(root, head)


def _mirror_path_without_toolchain(dest: Path) -> None:
    """Symlink every executable on the current PATH into ``dest``, EXCEPT npm/node/npx.

    Mirroring the whole PATH — rather than curating a handful of tools — keeps ``git`` and
    all of its helpers (e.g. the macOS ``xcrun`` shim) resolvable, so the script runs
    exactly as it would in a real shell. The ONLY thing removed is the web toolchain the
    guard checks for, which makes the "absent" state robust across hosts: it does not
    assume where node lives.
    """
    dest.mkdir(parents=True, exist_ok=True)
    excluded = {"npm", "node", "npx"}
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        directory = Path(entry)
        try:
            contents = list(directory.iterdir())
        except OSError:
            continue
        for tool in contents:
            name = tool.name
            if name in excluded:
                continue
            link = dest / name
            if link.exists() or link.is_symlink():
                continue  # first occurrence on PATH wins, like the shell's own lookup
            try:
                if tool.is_dir() or not os.access(tool, os.X_OK):
                    continue
                link.symlink_to(tool)
            except OSError:
                continue


def _write_exit0_stub(path: Path, log: Path) -> None:
    """A fake tool that records the arguments it was called with and exits 0."""
    path.write_text(
        "#!/bin/sh\n" f'printf "%s\\n" "$*" >> "{log}"\n' "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _env(fakebin: Path) -> dict[str, str]:
    """The real environment with PATH narrowed to ``fakebin`` — the only lever the guard
    reads. Everything else (HOME, GIT_*) is preserved so git behaves normally."""
    env = dict(os.environ)
    env["PATH"] = str(fakebin)
    return env


def _run(
    stdin: str,
    *,
    cwd: Path,
    env: dict[str, str],
    script: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(script or (cwd / "scripts" / "run_prepush.sh"))],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_the_shipped_script_still_carries_the_toolchain_guard():
    """A floor for the legs below: the thing under test must be present in the shipped file."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert GUARD_CONDITION in source, (
        "the web-toolchain guard's condition is gone from scripts/run_prepush.sh — the "
        "legs below would be measuring some other exit path. Restore it, do not weaken them."
    )


def test_render_smoke_skips_when_toolchain_absent(sandbox: Sandbox, tmp_path: Path):
    """🔴 directive #30(C), the whole point. npm/node off PATH ⇒ skip, exit 0, no chain.

    The frontend half is owed (a ``web/`` file is outgoing), yet with the toolchain absent
    the gate must print the skip and exit 0 instead of dying on ``npm: command not found``.
    ``GATING not in stdout`` is the load-bearing half — it proves the chain was never
    entered, not merely that a message was printed before running it anyway. And the exit
    is 0 precisely because npm is genuinely unavailable here: without the guard this same
    run would be non-zero (see the attribution leg).
    """
    fakebin = tmp_path / "fakebin-absent"
    _mirror_path_without_toolchain(fakebin)
    assert shutil.which("npm", path=str(fakebin)) is None, "npm leaked into the fakebin"
    assert shutil.which("node", path=str(fakebin)) is None, "node leaked into the fakebin"
    assert shutil.which("git", path=str(fakebin)), "git must remain resolvable for the gate to run"

    result = _run(sandbox.frontend_push, cwd=sandbox.root, env=_env(fakebin))

    assert result.returncode == 0, (
        "the gate did not degrade gracefully with the toolchain absent: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert TOOLCHAIN_SKIP in result.stdout, f"stdout={result.stdout!r}"
    assert GATING not in result.stdout, (
        "the render-smoke chain was entered despite the toolchain being absent: "
        f"stdout={result.stdout!r}"
    )
    assert GREEN not in result.stdout


def test_render_smoke_runs_when_toolchain_present(sandbox: Sandbox, tmp_path: Path):
    """🪤 The vacuity floor: with npm/node present the FULL chain still runs.

    "Skip when absent" is one reword away from "always skip", which would silently gut the
    gate. So drop exit-0 stubs for npm/node/npx into the fakebin, confirm the guard lets the
    chain through (``GATING``), that it ran to the end (``GREEN``, exit 0), and — the real
    anti-vacuity — that the stubs were actually invoked, including the load-bearing clean
    install (``npm ci``) and ``npm run build``. A gate that announces itself but calls
    nothing would pass the first two assertions and fail these.
    """
    fakebin = tmp_path / "fakebin-present"
    _mirror_path_without_toolchain(fakebin)
    log = tmp_path / "toolchain-invocations.log"
    for name in ("npm", "node", "npx"):
        _write_exit0_stub(fakebin / name, log)
    assert shutil.which("npm", path=str(fakebin)), "the npm stub is not resolvable"
    assert shutil.which("node", path=str(fakebin)), "the node stub is not resolvable"

    result = _run(sandbox.frontend_push, cwd=sandbox.root, env=_env(fakebin))

    assert result.returncode == 0, (
        "the full render-smoke chain did not run green with the toolchain present: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert GATING in result.stdout, f"stdout={result.stdout!r}"
    assert TOOLCHAIN_SKIP not in result.stdout, (
        "the guard skipped even though npm/node were on PATH: " f"stdout={result.stdout!r}"
    )
    assert GREEN in result.stdout, f"stdout={result.stdout!r}"

    invocations = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert any(line.strip() == "ci" for line in invocations), (
        "`npm ci` — the clean-install step the gate exists for — was not invoked: "
        f"{invocations!r}"
    )
    assert any(
        "run build" in line for line in invocations
    ), f"`npm run build` was not invoked: {invocations!r}"


def test_the_skip_is_attributable_to_the_guard(sandbox: Sandbox, tmp_path: Path):
    """Attribution: the same absent-toolchain stdin must NOT skip once the guard is neutered.

    Without this, the skip leg above could be reading any other early exit. Neuter the
    guard's own condition to ``if false`` on a copy of the sandbox script and re-run the
    identical absent-toolchain input: the copy must fall through to the render-smoke
    announcement and then fail on the genuinely-absent ``npm`` — never printing the skip.
    A rename of the guard line reds the source floor above rather than making this vacuous.
    """
    fakebin = tmp_path / "fakebin-neutered"
    _mirror_path_without_toolchain(fakebin)  # npm/node absent, as in the skip leg

    source = sandbox.script.read_text(encoding="utf-8")
    assert source.count(GUARD_CONDITION) == 1, "the guard condition is not a unique line"
    neutered = sandbox.root / "scripts" / "run_prepush_without_the_guard.sh"
    neutered.write_text(source.replace(GUARD_CONDITION, "if false; then"), encoding="utf-8")
    assert "if false; then" in neutered.read_text(encoding="utf-8"), "the neuter did not apply"

    result = _run(sandbox.frontend_push, cwd=sandbox.root, env=_env(fakebin), script=neutered)

    assert TOOLCHAIN_SKIP not in result.stdout, (
        "the guard-free copy still printed the skip, so the skip leg above is not "
        f"attributable to the guard: stdout={result.stdout!r}"
    )
    assert GATING in result.stdout, (
        "the guard-free copy did not fall through to the render-smoke chain: "
        f"stdout={result.stdout!r}"
    )
    assert result.returncode != 0, (
        "the guard-free copy exited 0 with npm genuinely absent — it cannot have tried to "
        f"run the chain: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
