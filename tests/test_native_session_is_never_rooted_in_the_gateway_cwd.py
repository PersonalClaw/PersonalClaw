"""A native session with no explicit cwd works in the workspace root — never in the gateway's cwd.

Measured on a General loop run unattended (driven 2026-09-25): the loop's worker step — a
native subagent on a project-less run, spawned with no cwd — wrote ``checklist.md`` into the
REPOSITORY CHECKOUT the gateway had been started from. The platform tool provider fell back to
``Path.cwd()``, the gateway process's own working directory; the run page meanwhile said it had
looked for output in the run's directory. Started from ``~`` the file lands in the home directory;
under a service manager, possibly ``/``.

The ACP spawn path already refuses that ambient fallback (``session._acp_spawn_cwd``). The native
factory now defaults to the same validated workspace root — where a new chat, the Terminal and
the Files page open — and, when none is usable, to a private scratch directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw.providers import provider_bridge as pb


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(root))
    return root.resolve()


def test_no_cwd_resolves_to_the_workspace_root(workspace: Path) -> None:
    assert Path(pb._native_session_cwd(None)) == workspace
    assert Path(pb._native_session_cwd("")) == workspace
    assert Path(pb._native_session_cwd(None)) != Path(os.getcwd()).resolve()


def test_an_explicit_cwd_still_wins(workspace: Path, tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    assert pb._native_session_cwd(str(mine)) == str(mine)


def test_no_usable_workspace_is_a_fresh_folder_inside_the_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no usable workspace a session gets a fresh, empty folder of its own, in the home's own
    workspace, where the durability inventory claims it. On `main` it was a
    `personalclaw-no-workspace-*` folder in the system temp folder that nothing removed: one per
    session built in that state, outside the home."""
    import tempfile

    from personalclaw.config.loader import config_dir
    from personalclaw.durability.inventory import claim_for

    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: "")

    first = Path(pb._native_session_cwd(None)).resolve()
    second = Path(pb._native_session_cwd(None)).resolve()

    home = config_dir().resolve()
    for got in (first, second):
        assert got.is_dir() and not any(got.iterdir()), "the fallback is a fresh, empty directory"
        assert got.is_relative_to(home), got
        entry = claim_for(got.relative_to(home).as_posix())
        assert entry is not None and entry.id == "workspace", got
    assert first != second, "each session gets its own"
    assert first != Path(os.getcwd()).resolve()
    assert list(system_temp.iterdir()) == []


class _Model:
    """A resolved inference model — the one thing the factory needs that this test is not about."""

    supports_tools = True
    _model = "m"

    async def complete(self, messages, **_):  # pragma: no cover — never prompted
        raise AssertionError("no turn runs in this test")


def test_the_built_runtime_roots_its_file_tools_there(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the REAL factory. At `origin/main` the platform provider's root is `Path.cwd()`."""
    monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: _Model())
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **k: "m")
    runtime = pb._build_native_runtime(
        use_case="chat", session_key="subagent:abc", agent=None, model_override=None, cwd=None
    )
    platform = runtime._tool_providers[0]
    assert platform.name == "personalclaw-filesystem"
    assert Path(platform._cwd_inst).resolve() == workspace
    assert Path(runtime._cwd).resolve() == workspace
