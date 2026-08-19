"""The working directory an ACP CLI is spawned with on the CONCURRENT (shared-connection)
path.

``SessionManager._open_acp_concurrent`` resolved its fallback cwd as
``default_workspace_dir()`` and passed the result on unchanged. That helper returns ``""``
when it finds no safe root; ``AcpProcess`` does ``Path(work_dir)``; and ``Path("")`` is
``Path(".")``. So a session opened with no explicit cwd on a machine with no resolvable
workspace spawned its CLI in whatever directory the GATEWAY happened to be running in — a
repo checkout, ``/``, or whatever a service manager set. Same family as G39's real-home
escape (#1729): a containment decision falling through to an ambient value.

These rails assert the value that reaches the PROCESS, not a resolver's return:

* the ``cwd=`` kwarg ``pool.open_session`` receives, for each resolution outcome — with a
  vacuity floor (a resolvable workspace MUST arrive, or "nothing arrived" would pass every
  containment assertion here);
* and, as the proof that makes the refusal load-bearing, the ``cwd=`` a bare ``AcpProcess``
  puts on the sandbox handle's ``exec`` (i.e. on ``create_subprocess_exec``) when its
  work_dir is empty.

No test here spawns a real CLI, and every one pins BOTH ``PERSONALCLAW_HOME`` and
``PERSONALCLAW_WORKSPACE`` under ``tmp_path``: nothing may touch the real home.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.acp.errors import AcpWorkspaceUnresolved

_RUNTIME = "acp:test-cli"
_KIND = {"provider_kind": _RUNTIME}


class _SpyPool:
    """Records every ``open_session`` call. Spawns nothing, ever."""

    def __init__(self, provider=None, boom: Exception | None = None) -> None:
        self.opened: list[dict] = []
        self._provider = provider
        self._boom = boom

    async def open_session(self, runtime_id, **kw):
        self.opened.append({"runtime_id": runtime_id, **kw})
        if self._boom is not None:
            raise self._boom
        return self._provider


def _isolate(monkeypatch, tmp_path) -> Path:
    """Pin both homes under tmp and return the workspace path (NOT created)."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    return ws


def _make_sm():
    from personalclaw.session import SessionManager

    cfg = MagicMock()
    cfg.default_agent = ""
    cfg.model = "auto"
    cfg.session.pool_size = 0
    cfg.session.pool_agent = ""
    cfg.session.pool_ttl_secs = 0
    return SessionManager(cfg)


def _no_workspace(monkeypatch) -> None:
    """What ``default_workspace_dir()`` returns when no safe root resolves: ``""``.

    Patched at ``session``'s module binding (it imports the name, not the module), which is
    the binding the code under test reads.
    """
    import personalclaw.session as session_mod

    monkeypatch.setattr(session_mod, "default_workspace_dir", lambda: "")


@pytest.fixture
def wire(monkeypatch):
    """Open the double gate onto a spy pool for runtime ``acp:test-cli``."""
    from personalclaw.acp import connection_pool as cp
    from personalclaw.llm import acp_session_provider, registry

    def _wire(pool):
        entry = MagicMock()
        entry.options = {"command": ["test-cli", "--acp"], "dialect": "test"}
        reg = MagicMock()
        reg.get_entry.return_value = entry
        monkeypatch.setattr(registry, "get_default_registry", lambda: reg)
        monkeypatch.setattr(acp_session_provider, "concurrent_sessions_enabled", lambda d: True)
        cp.set_acp_pool(pool)
        return pool

    yield _wire
    cp.set_acp_pool(None)


@pytest.mark.asyncio
async def test_resolvable_workspace_reaches_open_session_as_a_path(monkeypatch, tmp_path, wire):
    """VACUITY FLOOR for every assertion below: a resolvable workspace DOES arrive.

    Also pins the shape: the fallback branch used to hand ``open_session`` a bare ``str``
    while the explicit branch handed it a ``Path``.
    """
    ws = _isolate(monkeypatch, tmp_path)
    ws.mkdir(parents=True)
    sm = _make_sm()
    prov = MagicMock()
    pool = wire(_SpyPool(prov))

    got = await sm._open_acp_concurrent("dashboard:s1", None, "gpu-dev", None, None, dict(_KIND))

    assert got is prov
    assert len(pool.opened) == 1, "gate never opened — the containment rails would be vacuous"
    cwd = pool.opened[0]["cwd"]
    assert isinstance(cwd, Path), f"open_session got {type(cwd).__name__}, not Path"
    assert cwd == Path(os.path.realpath(ws))


@pytest.mark.asyncio
async def test_unresolvable_workspace_refuses_instead_of_spawning_anywhere(
    monkeypatch, tmp_path, wire
):
    """No workspace ⇒ no spawn. The old code passed ``""`` through to the process."""
    _isolate(monkeypatch, tmp_path)
    sm = _make_sm()
    _no_workspace(monkeypatch)
    pool = wire(_SpyPool(MagicMock()))

    raised: BaseException | None = None
    try:
        await sm._open_acp_concurrent("dashboard:s1", None, "gpu-dev", None, None, dict(_KIND))
    except AcpWorkspaceUnresolved as exc:
        raised = exc

    # Value first, so the red NAMES the directory that would have been used. Ordering the
    # raise check first would hide it, and it also reds for a refactor that returns None
    # (silently degrading to the one-session path) instead of raising.
    assert pool.opened == [], f"a CLI was opened in {pool.opened[0]['cwd']!r}"
    assert raised is not None, "an unresolvable workspace opened a session silently"
    # The user has to be able to act on it: the message names the knob, not just the fault.
    assert "PERSONALCLAW_WORKSPACE" in str(raised)


@pytest.mark.asyncio
async def test_explicit_session_cwd_still_wins_when_no_default_resolves(
    monkeypatch, tmp_path, wire
):
    """The refusal is scoped to the AMBIENT fallback — a bound session is unaffected."""
    _isolate(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    sm = _make_sm()
    _no_workspace(monkeypatch)
    pool = wire(_SpyPool(MagicMock()))

    await sm._open_acp_concurrent("dashboard:s1", None, None, None, str(proj), dict(_KIND))

    assert len(pool.opened) == 1
    assert pool.opened[0]["cwd"] == proj


@pytest.mark.asyncio
async def test_a_genuine_pool_failure_still_degrades_to_the_one_session_path(
    monkeypatch, tmp_path, wire
):
    """Narrowing the swallow must not cost the fallback contract.

    Every OTHER failure here still returns ``None`` so the caller cold-starts. Only the
    containment refusal escapes — falling back cannot help it, because the one-session path
    resolves the SAME workspace and would spawn in the same wrong place.
    """
    ws = _isolate(monkeypatch, tmp_path)
    ws.mkdir(parents=True)
    sm = _make_sm()
    pool = wire(_SpyPool(boom=RuntimeError("agent refused the connection")))

    got = await sm._open_acp_concurrent("dashboard:s1", None, "gpu-dev", None, None, dict(_KIND))

    assert got is None
    assert len(pool.opened) == 1  # it really did try


@pytest.mark.asyncio
async def test_an_empty_work_dir_would_spawn_in_the_gateways_own_cwd(monkeypatch, tmp_path):
    """WHY the refusal above must refuse — measured at the spawn kwargs, no real CLI.

    ``AcpProcess`` does ``Path(work_dir)``, so the ``""`` the old fallback produced and
    ``Path("")`` are the same value by the time it is stored. The ``cwd`` handed to
    ``create_subprocess_exec`` is then ``"."`` — resolved by the OS against the PARENT's
    directory, i.e. wherever the gateway happens to be running.
    """
    _isolate(monkeypatch, tmp_path)
    import personalclaw.session as session_mod
    from personalclaw.acp import transport as tr
    from personalclaw.sandbox_providers import none as none_provider

    assert Path("") == Path("."), "the equivalence this test rests on"

    # PID-tracking files are irrelevant here and write under the config home; stub them out
    # so this test cannot write anywhere at all.
    monkeypatch.setattr(session_mod, "_track_pid", lambda *a, **k: None)
    monkeypatch.setattr(session_mod, "_track_session_pid", lambda *a, **k: None)
    monkeypatch.setattr(session_mod, "_track_child_pids", lambda *a, **k: None)

    seen: list[dict] = []

    class _StubProc:
        pid = 999999  # no such process: no descendant scan, no real pid touched
        stderr = None
        returncode = None

    async def _capture(self, **kw):
        seen.append(dict(kw))
        return _StubProc()

    monkeypatch.setattr(none_provider._NoneHandle, "exec", _capture)

    ambient = tmp_path / "ambient"
    ambient.mkdir()
    monkeypatch.chdir(ambient)

    await tr.AcpProcess(command=["true"], work_dir="").spawn()
    assert len(seen) == 1, "capture seam never fired — the assertion below would be vacuous"
    assert seen[0]["cwd"] == ".", f"expected the ambient marker, got {seen[0]['cwd']!r}"
    assert Path(seen[0]["cwd"]).resolve() == ambient.resolve()

    # Contrast: a real work_dir arrives absolute. Same seam, so the assertion above is
    # measuring the empty case and not a broken harness.
    bound = tmp_path / "bound"
    await tr.AcpProcess(command=["true"], work_dir=bound).spawn()
    assert len(seen) == 2
    assert seen[1]["cwd"] == str(bound)
