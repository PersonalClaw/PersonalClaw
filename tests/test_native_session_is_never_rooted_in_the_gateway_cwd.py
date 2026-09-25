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


def test_no_usable_workspace_is_a_private_scratch_not_the_process_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: "")
    got = Path(pb._native_session_cwd(None))
    assert got.is_dir() and not any(got.iterdir()), "the fallback is a fresh, empty directory"
    assert got.resolve() != Path(os.getcwd()).resolve()
    assert got.name.startswith("personalclaw-no-workspace-")
    got.rmdir()


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
