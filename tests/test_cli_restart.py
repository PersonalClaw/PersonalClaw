"""E11-P1: service-aware `personalclaw restart`.

Covers the four branches of cli_server._restart:
  - a managed service present → restart_service() True → no foreground spawn
  - no service + a running foreground gateway → _stop then spawn
  - no service + nothing running (_stop exits) → SystemExit swallowed, still spawn
  - the service/platform restart() functions dispatch correctly
"""

from __future__ import annotations

from unittest.mock import patch

from personalclaw import cli_server


def test_restart_uses_service_when_present():
    """restart_service() True → service owns the lifecycle; no spawn."""
    with (
        patch.object(cli_server.service_controller, "restart_service", return_value=True) as rs,
        patch.object(cli_server, "_spawn_detached_gateway") as spawn,
        patch.object(cli_server, "_stop") as stop,
    ):
        cli_server._restart(7777)
    rs.assert_called_once()
    spawn.assert_not_called()
    stop.assert_not_called()


def test_restart_foreground_stops_then_spawns():
    """No service + running gateway → _stop then spawn a fresh one."""
    with (
        patch.object(cli_server.service_controller, "restart_service", return_value=False),
        patch.object(cli_server, "_stop") as stop,
        patch.object(cli_server, "_spawn_detached_gateway") as spawn,
    ):
        cli_server._restart(7777)
    stop.assert_called_once_with(7777)
    spawn.assert_called_once_with(7777)


def test_restart_swallows_stop_systemexit_and_still_spawns():
    """_stop exits nonzero when nothing is running → swallowed, gateway still spawned."""
    with (
        patch.object(cli_server.service_controller, "restart_service", return_value=False),
        patch.object(cli_server, "_stop", side_effect=SystemExit(1)) as stop,
        patch.object(cli_server, "_spawn_detached_gateway") as spawn,
    ):
        cli_server._restart(7777)  # must not raise
    stop.assert_called_once_with(7777)
    spawn.assert_called_once_with(7777)


def test_spawn_detached_gateway_launches_personalclaw_gateway():
    """The detached spawn invokes `python -m personalclaw gateway --port` in a new session."""
    with patch.object(cli_server.subprocess, "Popen") as popen, patch("builtins.open"):
        cli_server._spawn_detached_gateway(7777)
    popen.assert_called_once()
    argv = popen.call_args.args[0]
    assert argv[1:] == ["-m", "personalclaw", "gateway", "--port", "7777"]
    assert popen.call_args.kwargs.get("start_new_session") is True


# ── platform restart dispatch (mirror stop_service's branch shape) ───────────


def test_restart_service_systemd_active():
    from personalclaw.service import controller
    from personalclaw.service.common import Platform

    with (
        patch.object(controller, "current_platform", return_value=Platform.SYSTEMD),
        patch.object(controller.linux, "is_active", return_value=True),
        patch.object(controller.linux, "restart") as restart,
    ):
        assert controller.restart_service() is True
    restart.assert_called_once()


def test_restart_service_none_when_inactive():
    from personalclaw.service import controller
    from personalclaw.service.common import Platform

    with (
        patch.object(controller, "current_platform", return_value=Platform.SYSTEMD),
        patch.object(controller.linux, "is_active", return_value=False),
        patch.object(controller.linux, "restart") as restart,
    ):
        assert controller.restart_service() is False
    restart.assert_not_called()
