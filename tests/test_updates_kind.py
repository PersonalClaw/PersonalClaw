"""Install-kind detection (plan 34 S4 T4.1, contract C1).

Four fixtures — one per InstallKind — pin the resolution order:
env (container/desktop) wins first, then a .git working tree => git, else pip.
Each test isolates the two env vars the classifier reads (monkeypatch.delenv)
so it never inherits the runner's real environment.
"""

from __future__ import annotations

import aiohttp
import pytest

from personalclaw.dashboard.handlers import updates_kind
from personalclaw.dashboard.handlers import updates_kind as uk
from personalclaw.dashboard.handlers.updates_kind import detect_install_kind


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
    assert updates_kind._ENV_KINDS == {"container", "desktop"}


# ── T4.2: tag-driven check + C2 payload ─────────────────────────────────────


def test_normalize_version_strips_leading_v() -> None:
    assert uk._normalize_version("v0.1.3") == "0.1.3"
    assert uk._normalize_version("0.1.3") == "0.1.3"
    assert uk._normalize_version("  v1.2.0 ") == "1.2.0"


def test_version_tuple_orders_numerically() -> None:
    assert uk._version_tuple("v0.2.0") > uk._version_tuple("0.1.9")
    assert uk._version_tuple("0.1.10") > uk._version_tuple("0.1.9")
    assert uk._version_tuple("garbage") == (0,)


def test_cache_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    uk._write_cache({"tag": "v0.1.3", "etag": 'W/"abc"'})
    got = uk._read_cache()
    assert got["tag"] == "v0.1.3"
    assert got["etag"] == 'W/"abc"'


def test_read_cache_missing_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert uk._read_cache() == {}


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
    uk._write_cache({"tag": "v0.1.2", "etag": 'W/"x"'})

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
