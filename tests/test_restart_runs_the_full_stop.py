"""The dashboard's Restart stops the gateway the way every stop does, THEN starts the new image.

`updates._graceful_reexec` — the Restart button, an applied update, a staged auto-update — saved
chat history, closed sessions, stopped app processes and called `os.execve` from inside the request
handler. Every other step of a stop was skipped, and with it everything those steps hold: time
travel's debouncer keeps the commits of the last few seconds of edits (a write schedules its commit
10 s out) and writes them only when the durability service stops — an `on_cleanup` hook of the
dashboard runner, which the gateway's `_shutdown()` runs and an in-place `execve` never reaches.
So the setting you changed just before pressing Restart had no history.

A restart is now the ONE stop path with a different last step: `_graceful_reexec` asks for it
(`restart_request`), and the gateway's `_finish()` — the same code a Ctrl-C runs — stops everything
and then starts the requested image instead of exiting.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest
from aiohttp import web

from personalclaw import restart_request, shutdown_event
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.handlers import updates
from personalclaw.durability import history_debounce
from personalclaw.durability import state_history as sh
from personalclaw.durability.service import DurabilityService
from personalclaw.gateway import GatewayOrchestrator


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    """A private home; no restart request, no shutdown signal and no debouncer leaking out —
    all three are process-wide."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(restart_request, "_pending", None)
    shutdown_event.clear()
    history_debounce.uninstall(flush=False)
    yield
    history_debounce.uninstall(flush=False)
    shutdown_event.clear()


class _NewImage(Exception):
    """Raised where `os.execve` would have replaced this process."""


def _orchestrator() -> GatewayOrchestrator:
    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        return GatewayOrchestrator(cfg)


async def _runner_that_stops_durability(tmp_path) -> web.AppRunner:
    """A dashboard runner whose cleanup stops the durability service — the hook the real one has."""
    app = web.Application()
    svc = DurabilityService(tick_secs=3600)

    async def _durability_shutdown(_app: web.Application) -> None:
        svc.stop()

    app.on_cleanup.append(_durability_shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    return runner


@pytest.mark.asyncio
async def test_pending_history_is_committed_before_the_restarted_image_starts(tmp_path):
    """🔴 Red on main: the new image started with the edit's commit still pending (0 commits)."""
    assert (
        sh.git_available()
    ), "time travel needs git; this host must have it for the test to mean anything"
    debouncer = history_debounce.install(home=tmp_path, start=False)
    assert debouncer is not None
    from personalclaw.atomic_write import atomic_write

    atomic_write(tmp_path / "config.json", '{"dashboard": {"user_name": "Restart probe"}}\n')
    config_root = sh.root_by_id("config", home=tmp_path)
    assert debouncer.pending_roots() == ("config",), "the edit must be pending, as it is for 10 s"
    assert sh.commit_count(config_root, home=tmp_path) == 0

    orch = _orchestrator()
    orch._dashboard_runner = await _runner_that_stops_durability(tmp_path)
    commits_when_the_new_image_started: list[int] = []

    def _execve(path, argv, env):  # noqa: ANN001
        commits_when_the_new_image_started.append(sh.commit_count(config_root, home=tmp_path))
        raise _NewImage(argv)

    async def _close_all() -> None:
        return None

    state = types.SimpleNamespace(
        push_update_progress=lambda *a, **k: None,
        sessions=types.SimpleNamespace(close_all=_close_all),
    )
    with (
        patch("os.execve", _execve),
        patch("os._exit", side_effect=AssertionError("a restart must not exit")),
        patch("personalclaw.session.cleanup_orphaned_sessions"),
    ):
        try:
            await updates._graceful_reexec(state, auth_mode="local_token")
        except _NewImage:
            pass  # main: the handler replaced the process itself, mid-request
        if not commits_when_the_new_image_started:
            # The restart was handed to the gateway's own stop, as it must be.
            assert shutdown_event.is_set(), "a restart must ask the gateway to stop"
            with pytest.raises(_NewImage):
                await orch._finish()

    assert commits_when_the_new_image_started == [1], (
        "the restarted image started before the edit made just before Restart was committed "
        f"to history (commits then: {commits_when_the_new_image_started})"
    )


@pytest.mark.asyncio
async def test_the_restarted_image_is_this_gateway_with_its_auth_mode_pinned():
    """The image is `-m personalclaw` with the same arguments, and the running gateway's auth mode
    rides into it, so a Restart never changes whether sign-in is required."""
    started: list[tuple[str, list[str], dict[str, str]]] = []

    def _execve(path, argv, env):  # noqa: ANN001
        started.append((path, list(argv), dict(env)))
        raise _NewImage

    orch = _orchestrator()
    restart_request.request_restart(auth_mode="none")
    with (
        patch("os.execve", _execve),
        patch("os._exit", side_effect=AssertionError("a restart must not exit")),
        patch("personalclaw.session.cleanup_orphaned_sessions"),
        pytest.raises(_NewImage),
    ):
        await orch._finish()
    path, argv, env = started[0]
    assert path == sys.executable
    assert argv[:3] == [sys.executable, "-m", "personalclaw"]
    assert argv[3:] == sys.argv[1:]
    assert env["PERSONALCLAW_AUTH_MODE"] == "none"


@pytest.mark.parametrize("kind", ["pip", "git"])
@pytest.mark.asyncio
async def test_no_update_starts_while_the_gateway_stops_to_restart(kind, tmp_path, monkeypatch):
    """A Restart returns to its caller now, and the gateway takes a moment to stop. An Update
    pressed in that moment must not start: it would pull, install and build against the tree the
    new image starts from while the gateway stops under it. When the handler exec'd the new image
    itself, no apply could ever see the moment."""
    import json
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(updates, "_apply_in_flight", False)
    monkeypatch.setattr(updates.self_update, "detect_install_kind", lambda: kind)
    monkeypatch.setattr(updates.self_update, "source_checkout", lambda: str(tmp_path))
    monkeypatch.setattr(updates.self_update, "resolve_target", AsyncMock(return_value=""))
    monkeypatch.setattr(updates.self_update, "resolve_wheel_target", AsyncMock(return_value=""))
    spawned = AsyncMock(side_effect=AssertionError("nothing may be installed while restarting"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawned)
    state = types.SimpleNamespace(
        push_update_progress=lambda *a, **k: None,
        push_refresh=lambda *a, **k: None,
        _background_tasks=set(),
    )
    await updates._graceful_reexec(state)
    assert restart_request.pending() is not None, "the Restart must be waiting on the stop"

    app = web.Application()
    app["state"] = state
    app["auth_cfg"] = None
    request = MagicMock()
    request.app = app
    resp = await updates.api_update_apply(request)

    assert resp.status == 409, "an Update started while the gateway was stopping to restart"
    assert json.loads(resp.body)["error"] == (
        "PersonalClaw is restarting. Try again once it is back."
    )
    assert not state._background_tasks
    spawned.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stop_asked_for_during_a_restart_stops():
    """Ctrl-C (or a service manager's SIGTERM) while a restart is shutting down: a stop that came
    back as a new process — with the same PID — is not a stop."""
    orch = _orchestrator()
    restart_request.request_restart()
    restart_request.request_stop()
    assert restart_request.pending() is None
    exited: list[int] = []
    with (
        patch("os.execve", side_effect=AssertionError("a stop must not restart")),
        patch("os._exit", side_effect=lambda code: exited.append(code)),
        patch("personalclaw.session.cleanup_orphaned_sessions"),
    ):
        await orch._finish()
    assert exited == [0]
