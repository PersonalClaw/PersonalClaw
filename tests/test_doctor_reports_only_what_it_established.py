"""Every doctor line must say what the check MEASURED — never what it expects to be true.

Three reports, one defect (#2907, #2908, #2922). `personalclaw doctor` is the surface a
user reaches when something is already wrong, so a line that states a conclusion the check
never established is worse than a missing line: it ends the investigation at the wrong
place.

The three instances, as observed on `origin/main`:

1. **#2907 — `git repo: ⚠️  not a git repo` for a valid checkout.** The check was
   `(proj / ".git").is_dir()`. In a linked worktree or a submodule `.git` is a regular
   FILE holding a `gitdir:` pointer, so `is_dir()` is false and the else branch fired on
   the layout this project's own dev flow uses. What the check established was "no `.git`
   DIRECTORY here"; what it printed was "not a git repo". The same false branch also
   covered a case it never looked at — `proj` being a SUBDIRECTORY of a checkout, where
   there is no marker here and git is still inside a work tree.

2. **#2908 — one requirement, two numbers.** The warning interpolated
   `_MIN_NODE_VERSION` (`needs Node 18+`) and the Fix line beneath it hardcoded
   `install Node.js >= 16`, at both of the two sites. A user reading the pair cannot tell
   which number the software actually enforces, and the literal is free to drift again.

3. **#2922 — `credentials: 🔐 .env 0600` on an install with no `.env`.** 0600 is the mode
   the fallback PROMISES. The CLI row printed that literal for every non-keychain install
   without stat-ing anything, and the `security.credential_backend` probe rendered
   `mode or '0600'`, so a fresh home reported credentials stored at a measured-sounding
   mode, in a file that did not exist, at a path it printed. The defect had two shapes:
   the fabricated claim on an ABSENT file, and the wrong mode on a PRESENT one (a `.env`
   at 0640 also read as `0600`).

The rails below are written as properties of the OUTPUT rather than as string equality,
because the fix is not "three new strings" — it is that a claim must be licensed by a
measurement:

* a row may name a `.env` mode only if that file exists and its mode was read;
* every Node minimum the Dependencies block declares must be the one constant;
* the git row may report a verdict only when something established one, and must have a
  distinguishable "could not determine" state for when nothing did.

⚠️  `_doctor()` touches the agent config under `agents_dir()`, which used to be frozen at import
from the real home — and its MCP section REWRITES `personalclaw.json` when it finds a
stale binary path. Every test here that drives `_doctor()` repoints
`cli_doctor.agents_dir` at `tmp_path` first, so no test can write the operator's home.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.config import loader

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    # A repo created for this test must not inherit the developer's hooks/templates.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )


# ── #2907: the git row ───────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one commit (so `git worktree add` has a base)."""
    root = tmp_path / "main-checkout"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    (root / "f.txt").write_text("v1\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "init", cwd=root)
    return root


def _row(proj: Path) -> str:
    from personalclaw.cli_doctor import _git_work_tree_row

    return _git_work_tree_row(proj)


def test_a_linked_worktree_is_not_reported_as_not_a_git_repo(repo: Path, tmp_path: Path) -> None:
    """THE BUG. A real `git worktree add` checkout, whose `.git` is a pointer FILE."""
    wt = tmp_path / "linked-worktree"
    _git("worktree", "add", "-q", "-b", "side", str(wt), cwd=repo)

    marker = wt / ".git"
    # The fixture is only meaningful if it reproduces the shape the bug needs: assert the
    # pointer-FILE layout directly, so this test can never pass against a `.git` directory.
    assert marker.is_file(), "a linked worktree's .git must be the gitdir pointer file"
    assert not marker.is_dir()
    assert marker.read_text(encoding="utf-8").startswith("gitdir:")
    assert (
        _git("rev-parse", "--is-inside-work-tree", cwd=wt).stdout.strip() == "true"
    ), "ground truth: git itself calls this a work tree"

    row = _row(wt)
    assert "not a git" not in row, f"a valid linked worktree reported as no repo: {row!r}"
    assert row.startswith("✅"), row
    assert "worktree" in row, f"the row should name the shape it found: {row!r}"


def test_a_subdirectory_of_a_checkout_is_not_reported_as_not_a_git_repo(repo: Path) -> None:
    """The other case the old false branch swallowed: no marker HERE, still in a work tree."""
    sub = repo / "nested" / "deeper"
    sub.mkdir(parents=True)
    assert not (sub / ".git").exists(), "the fixture has no marker of its own"

    row = _row(sub)
    assert "not a git" not in row, row
    assert row.startswith("✅"), row


def test_an_ordinary_checkout_still_reports_a_plain_tick(repo: Path) -> None:
    row = _row(repo)
    assert row.startswith("✅"), row
    assert "⚠️" not in row and "⏭" not in row


def test_a_plain_directory_is_reported_as_not_a_work_tree(tmp_path: Path) -> None:
    """The negative must survive the fix — the row is still a warning when git says no."""
    plain = tmp_path / "plain"
    plain.mkdir()

    row = _row(plain)
    assert row.startswith("⚠️"), row
    assert "not a git" in row


def test_the_row_says_it_cannot_tell_when_nothing_could_establish_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third state. No marker to read and no git to ask is not a verdict either way.

    A doctor that answers "could not determine" is correct here; one that picks the
    negative is guessing, and it is the guess that produced #2907.
    """
    import personalclaw.cli_doctor as cd

    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(cd.shutil, "which", lambda _b: None)

    row = _row(plain)
    assert row.startswith("⏭"), row
    assert "not a git" not in row, "an unanswered question must not be rendered as a verdict"
    assert "cannot tell" in row


def test_the_marker_alone_answers_when_git_cannot_be_asked(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gitdir pointer is observable without git, so an absent git must not erase it."""
    import personalclaw.cli_doctor as cd

    wt = tmp_path / "linked-worktree"
    _git("worktree", "add", "-q", "-b", "side2", str(wt), cwd=repo)
    monkeypatch.setattr(cd.shutil, "which", lambda _b: None)

    row = _row(wt)
    assert row.startswith("✅"), row
    assert "worktree" in row


def test_the_doctor_actually_prints_the_row_it_computes() -> None:
    """A helper nobody calls is an inert control — assert the CALL SITE, as SH-1 does."""
    import ast
    import inspect

    import personalclaw.cli_doctor as cd

    tree = ast.parse(Path(inspect.getsourcefile(cd) or "").read_text(encoding="utf-8"))
    doctor = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_doctor"
    )
    called = {
        n.func.id
        for n in ast.walk(doctor)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_git_work_tree_row" in called


# ── #2908: the Node minimum ──────────────────────────────────────────────────


def _dependencies_block(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    node_version: str | None,
) -> str:
    """Drive the real `_doctor()` and return its Dependencies section.

    `node_version` is what `node -v` answers; `None` removes node from PATH entirely,
    which is the second of the two sites that render a minimum.
    """
    import personalclaw.cli_doctor as cd

    def _which(binary: str) -> str | None:
        if binary == "node" and node_version is None:
            return None
        return f"/usr/local/bin/{binary}"

    def _run(cmd, *a, **kw):  # noqa: ANN001 - subprocess.run's signature
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
        stdout = node_version if argv[:2] == ["node", "-v"] else "Python 3.13.14"
        return subprocess.CompletedProcess(argv, 0, stdout=f"{stdout}\n", stderr="")

    # agents_dir() resolves the ACTIVE home, and the MCP section REWRITES
    # personalclaw.json when it sees a stale command path. Repoint it at tmp_path.
    monkeypatch.setattr(cd, "agents_dir", lambda: tmp_path / "agents")
    with (
        patch.object(cd.shutil, "which", side_effect=_which),
        patch("subprocess.run", side_effect=_run),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch.object(cd, "is_local_bind", return_value=True),
    ):
        try:
            cd._doctor()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    assert "Dependencies\n" in out, out
    return out.split("Dependencies\n", 1)[1].split("\nProject", 1)[0]


def _declared_minimums(block: str) -> list[int]:
    """Every Node minimum the block states, in either of the two phrasings."""
    return [int(n) for n in re.findall(r"(?:needs Node |Node\.js >= )(\d+)", block)]


@pytest.mark.parametrize("node_version", ["v16.20.2", None], ids=["too-old", "not-found"])
def test_the_node_warning_and_its_fix_line_name_one_minimum(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    node_version: str | None,
) -> None:
    """THE BUG: `needs Node 18+` over `Fix: install Node.js >= 16` — one requirement, two
    numbers. Asserted as a property of the whole block so a future edit cannot reintroduce
    the drift at only one of the two sites."""
    from personalclaw.cli_doctor import _MIN_NODE_VERSION

    block = _dependencies_block(capsys, monkeypatch, tmp_path, node_version=node_version)
    claims = _declared_minimums(block)

    # Vacuity floor: this rail is only meaningful if it actually found both claims.
    assert len(claims) >= 2, f"expected a warning AND a Fix line to state a minimum:\n{block}"
    assert set(claims) == {_MIN_NODE_VERSION}, (
        f"the node rows declare {sorted(set(claims))} as the minimum, but the code enforces "
        f"{_MIN_NODE_VERSION} — a user cannot tell which number is real:\n{block}"
    )


def test_an_unparsable_node_version_is_not_reported_as_a_tick(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same family, same function: the `except` branch printed `node: ✅` after the version
    parse FAILED, so the one thing the check exists to establish was the one thing it had
    not established."""
    block = _dependencies_block(capsys, monkeypatch, tmp_path, node_version="not-a-version")
    node_row = next(line for line in block.splitlines() if line.strip().startswith("node:"))

    assert "✅" not in node_row, f"a tick claims the version was checked: {node_row!r}"
    assert "unknown" in node_row, node_row


# ── #2922: the credentials row and probe ─────────────────────────────────────


@pytest.fixture
def cred_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated credential home. Never the real one — and it starts with no `.env`."""
    cfg = tmp_path / "cred-home"
    cfg.mkdir()
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.delenv("PERSONALCLAW_CREDENTIAL_BACKEND", raising=False)
    return cfg


def _mode_claims(text: str) -> list[str]:
    """Every octal mode the text attributes to `.env`."""
    return re.findall(r"\.env(?:\s+at)?(?:\s+mode)?\s+(\d{4})", text)


def test_the_credentials_row_claims_no_mode_for_a_file_that_is_not_there(
    cred_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE BUG: a fresh install was told its credentials were stored in `.env` at 0600."""
    from personalclaw.cli_doctor import _doctor_credentials

    env = loader.env_path()
    assert not env.exists(), "the whole point of the fixture: there is no credential file"

    issues = _doctor_credentials()
    out = capsys.readouterr().out

    assert not _mode_claims(
        out
    ), f"the row names a mode for a file that does not exist ({env}): {out!r}"
    assert "none stored yet" in out, out
    assert issues == [], "nothing is wrong on a fresh install — it is just empty"


def test_the_credentials_row_reports_the_mode_it_measured_not_the_promised_floor(
    cred_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second shape of the same defect: 0600 was printed for a file at 0640.

    This is the falsification that a mere "say nothing when absent" fix would survive.
    """
    from personalclaw.cli_doctor import _doctor_credentials

    env = loader.env_path()
    env.write_text("SOME_TOKEN=x\n", encoding="utf-8")
    env.chmod(0o640)
    assert stat.S_IMODE(env.stat().st_mode) == 0o640

    issues = _doctor_credentials()
    out = capsys.readouterr().out

    assert _mode_claims(out) == ["0640"], f"the row must report the mode it read: {out!r}"
    # A measured-but-loose mode only became VISIBLE on this surface with the fix, and a bare
    # `.env 0640` reads as fine — so the sentence the probe already had belongs here too.
    assert "group/world readable" in out, out
    # Legibility, not a gate: the next credential read repairs the mode, and doctor must not
    # start exiting 1 on installs it used to pass.
    assert issues == []


@pytest.mark.asyncio
async def test_the_probe_does_not_report_stored_credentials_with_no_file(
    cred_home: Path,
) -> None:
    """The dashboard half of #2922: `detail` asserted storage; only the evidence was honest."""
    from personalclaw.resilience.doctor import DoctorContext, all_probes

    probe = {p.id: p for p in all_probes()}["security.credential_backend"]
    result = await probe.run(DoctorContext(home=cred_home))

    assert not loader.env_path().exists()
    assert result.ok is True, "an empty store is not a fault"
    assert not _mode_claims(result.detail), result.detail
    assert "stored in .env at mode" not in result.detail, result.detail
    assert (
        result.evidence["env_exists"] is False
    ), "the evidence must state the file's absence, not leave it to be inferred from env_mode"
