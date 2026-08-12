"""Install-kind detection + the shared self-update primitives (contracts C1/C2).

Four fixtures — one per InstallKind — pin the resolution order:
env (container/desktop) wins first, then a .git working tree => git, else pip.
Each test isolates the two env vars the classifier reads (monkeypatch.delenv)
so it never inherits the runner's real environment.

The module under test moved out of ``dashboard/handlers/updates_kind.py`` into the
core package in DIST-13, so the CLI can reach the same decision the dashboard makes
without importing an HTTP handler.
"""

from __future__ import annotations

import aiohttp
import pytest

from personalclaw import self_update as uk
from personalclaw.self_update import detect_install_kind


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)


def test_container_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Even with a git tree present, the container env marker takes precedence.
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "container"


def test_desktop_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
    assert detect_install_kind() == "desktop"


def test_env_kind_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "  Container ")
    assert detect_install_kind() == "container"


def test_unknown_env_kind_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # A junk value is ignored — resolution falls through to git/pip probing.
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "banana")
    assert detect_install_kind() == "pip"


def test_git_when_project_dir_has_dot_git(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_worktree_dot_git_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # In a git worktree/submodule, .git is a FILE pointing at the real gitdir.
    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_when_dot_git_in_monorepo_parent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Monorepo layout: the project dir is nested one level under the repo root
    # (which carries .git). The parent probe catches it.
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "PersonalClaw"
    nested.mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(nested))
    assert detect_install_kind() == "git"


def test_pip_when_no_env_no_git(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # A project dir with NO .git (e.g. an unpacked source dir) is not "git".
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "pip"


def test_pip_when_nothing_set() -> None:
    # No env markers, no project dir -> a plain wheel/uv/pipx install.
    assert detect_install_kind() == "pip"


def test_install_kind_literal_values() -> None:
    # Guard the contract's value set (C1 / C2 wire shape).
    assert uk._ENV_KINDS == {"container", "desktop"}


# ── T4.2: tag-driven check + C2 payload ─────────────────────────────────────


def test_normalize_version_strips_leading_v() -> None:
    assert uk.normalize_version("v0.1.3") == "0.1.3"
    assert uk.normalize_version("0.1.3") == "0.1.3"
    assert uk.normalize_version("  v1.2.0 ") == "1.2.0"


def test_version_tuple_orders_numerically() -> None:
    assert uk.version_tuple("v0.2.0") > uk.version_tuple("0.1.9")
    assert uk.version_tuple("0.1.10") > uk.version_tuple("0.1.9")
    assert uk.version_tuple("garbage") == (0,)


def test_cache_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    uk.write_release_cache({"tag": "v0.1.3", "etag": 'W/"abc"'})
    got = uk.read_release_cache()
    assert got["tag"] == "v0.1.3"
    assert got["etag"] == 'W/"abc"'


def test_read_cache_missing_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert uk.read_release_cache() == {}


@pytest.mark.asyncio
async def test_build_status_update_available(monkeypatch) -> None:
    async def _fake_release() -> dict:
        return {"tag": "v0.2.0", "name": "0.2.0", "body": "notes"}

    monkeypatch.setattr(uk, "fetch_latest_release", _fake_release)
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    status = await uk.build_update_status("0.1.0")
    assert status["kind"] == "container"
    assert status["current"] == "0.1.0"
    assert status["latest"] == "0.2.0"
    assert status["update_available"] is True
    assert status["apply_method"] == "instructions"
    assert status["instructions"]  # container carries pull+up commands
    assert status["commits_behind"] is None


@pytest.mark.asyncio
async def test_build_status_up_to_date_pip(monkeypatch) -> None:
    async def _fake_release() -> dict:
        return {"tag": "v0.1.0", "name": "0.1.0", "body": ""}

    monkeypatch.setattr(uk, "fetch_latest_release", _fake_release)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    status = await uk.build_update_status("0.1.0")
    assert status["kind"] == "pip"
    assert status["update_available"] is False
    assert status["apply_method"] == "pip_upgrade"
    assert status["instructions"] == []


@pytest.mark.asyncio
async def test_build_status_offline_no_tag(monkeypatch) -> None:
    async def _empty_release() -> dict:
        return {}

    monkeypatch.setattr(uk, "fetch_latest_release", _empty_release)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    status = await uk.build_update_status("0.1.0")
    # No latest known -> never claims an update is available (offline-tolerant).
    assert status["latest"] == ""
    assert status["update_available"] is False


@pytest.mark.asyncio
async def test_fetch_latest_release_offline_returns_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    uk.write_release_cache({"tag": "v0.1.2", "etag": 'W/"x"'})

    class _BoomSession:
        def __init__(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    got = await uk.fetch_latest_release()
    assert got["tag"] == "v0.1.2"  # degraded to the cached view, no raise


# ── C2 wire-shape conformance (Tier-S once clients read it) ──────────────────


@pytest.mark.asyncio
async def test_c2_wire_shape_conformance(monkeypatch) -> None:
    """build_update_status emits exactly the C2 contract keys (+ additive extras),
    with the per-kind apply_method / commits_behind / instructions semantics the
    plan pins. Locks the Tier-S wire shape against silent drift."""

    async def _rel() -> dict:
        return {"tag": "v0.2.0", "name": "0.2.0", "body": "notes"}

    monkeypatch.setattr(uk, "fetch_latest_release", _rel)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)

    required = {
        "kind",
        "current",
        "latest",
        "update_available",
        "commits_behind",
        "apply_method",
        "instructions",
    }

    # container: apply_method=instructions, commits_behind=null, instructions non-empty
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    c = await uk.build_update_status("0.1.0")
    assert required <= set(c)
    assert c["apply_method"] == "instructions"
    assert c["commits_behind"] is None
    assert isinstance(c["instructions"], list) and c["instructions"]

    # desktop: apply_method=desktop_delegate
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
    d = await uk.build_update_status("0.1.0")
    assert d["apply_method"] == "desktop_delegate"

    # pip: apply_method=pip_upgrade, commits_behind=null, instructions=[]
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    p = await uk.build_update_status("0.1.0")
    assert p["apply_method"] == "pip_upgrade"
    assert p["commits_behind"] is None
    assert p["instructions"] == []
    # current/latest are normalized (no leading v)
    assert p["current"] == "0.1.0"
    assert p["latest"] == "0.2.0"
    assert p["update_available"] is True


# ── DIST-13: the default branch is resolved, not guessed ────────────────────


class _GitScript:
    """Fake ``_run_git`` driven by a per-subcommand script; records every call."""

    def __init__(self, **replies: tuple[int, str]) -> None:
        self.calls: list[list[str]] = []
        self._replies = replies

    def __call__(self, args, *, cwd, timeout):  # type: ignore[no-untyped-def]
        import subprocess

        self.calls.append(list(args))
        rc, out = self._replies.get(args[0], (1, ""))
        return subprocess.CompletedProcess(["git", *args], rc, out, "")


def test_default_branch_prefers_the_checked_out_branch(monkeypatch) -> None:
    # Updating means "advance the branch I am on" — a contributor on a feature
    # branch must not be reset onto another one.
    git = _GitScript(**{"rev-parse": (0, "feature-foo\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "feature-foo"
    assert [c[0] for c in git.calls] == ["rev-parse"]  # no further probes needed


def test_default_branch_detached_head_reads_the_remote_head(monkeypatch) -> None:
    # A checkout parked on a release tag reports "HEAD"; origin/HEAD is the answer,
    # and it is a LOCAL ref, so this stays offline-safe.
    git = _GitScript(**{"rev-parse": (0, "HEAD\n"), "symbolic-ref": (0, "origin/main\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "main"


def test_default_branch_falls_back_to_remote_show(monkeypatch) -> None:
    # No refs/remotes/origin/HEAD (older clone, or a hand-added remote): ask the
    # remote. Last among the probes because it needs the network.
    git = _GitScript(
        **{
            "rev-parse": (0, "\n"),
            "symbolic-ref": (1, ""),
            "remote": (0, "* remote origin\n  HEAD branch: trunk\n  Fetch URL: x\n"),
        }
    )
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "trunk"


def test_default_branch_ignores_an_unknown_remote_head(monkeypatch) -> None:
    # A remote with no branches reports "HEAD branch: (unknown)" — not a branch name.
    git = _GitScript(
        **{
            "rev-parse": (0, "HEAD\n"),
            "symbolic-ref": (1, ""),
            "remote": (0, "  HEAD branch: (unknown)\n"),
        }
    )
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == uk.DEFAULT_BRANCH_FALLBACK


def test_default_branch_last_resort_is_this_repo_s_real_default(monkeypatch) -> None:
    """Every probe fails ⇒ the literal fallback, and it must NAME A REAL BRANCH.

    The CLI hardcoded ``mainline`` — a branch this repository has never had — so a
    detached-HEAD update fetched an unresolvable ref and failed confusingly.
    """
    git = _GitScript()  # every subcommand returns rc=1
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "main"
    assert uk.DEFAULT_BRANCH_FALLBACK == "main"


def test_no_module_hardcodes_a_branch_this_repo_does_not_have() -> None:
    """Regression rail for the `mainline` default (DIST-13).

    Cheap and exact: the string must not reappear in either updater surface, in a
    fallback or a comment that a later edit could copy back into code.
    """
    from pathlib import Path

    import personalclaw

    root = Path(personalclaw.__file__).parent
    for name in ("self_update.py", "cli_server.py", "gateway.py"):
        assert "mainline" not in (root / name).read_text(encoding="utf-8"), name


def test_run_git_reports_a_timeout_as_a_failure_not_an_exception(monkeypatch) -> None:
    """A timeout is an ordinary updater failure: non-zero + a reason, never a raise.

    Callers report `stderr` and stop; making them wrap every probe in try/except is
    how a timeout ends up swallowed instead.
    """
    import subprocess

    def _boom(*a, **k):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", _boom)
    res = uk._run_git(["status"], cwd="/x", timeout=1)
    assert res.returncode == 124
    assert "timed out" in res.stderr


def test_run_git_reports_a_missing_git_binary(monkeypatch) -> None:
    import subprocess

    def _missing(*a, **k):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _missing)
    res = uk._run_git(["status"], cwd="/x", timeout=1)
    assert res.returncode == 127
    assert "cannot run git" in res.stderr


def test_tracked_changes_excludes_untracked_entries(monkeypatch) -> None:
    # Untracked files survive `reset --hard`, so warning about them would train the
    # reader to click through the warning that matters.
    git = _GitScript(**{"status": (0, " M a.py\n?? scratch.txt\nA  b.py\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.git_tracked_changes("/x") == [" M a.py", "A  b.py"]


def test_upgrade_spec_pins_a_known_tag_and_falls_back_unpinned() -> None:
    assert uk.upgrade_spec("v0.1.4") == "personalclaw==0.1.4"
    assert uk.upgrade_spec("") == "personalclaw"


def test_git_root_finds_the_worktree_that_carries_dot_git(tmp_path) -> None:
    nested = tmp_path / "PersonalClaw"
    nested.mkdir()
    (tmp_path / ".git").mkdir()
    # Git runs at the repo root even when the project dir is the nested package.
    assert uk.git_root(str(nested)) == str(tmp_path)
    assert uk.git_root("") == ""
