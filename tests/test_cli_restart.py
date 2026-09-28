"""E11-P1: service-aware `personalclaw restart`.

Covers the branches of cli_server._restart:
  - a managed service present → restart_service() True → no foreground spawn
  - no service + a running foreground gateway → _stop, then spawn on the port it had
  - no service + a running gateway `_stop` cannot stop → no spawn: never a second gateway
  - no service + nothing running → no _stop, spawn on the resolved port
  - the service/platform restart() functions dispatch correctly
(The container, whose runtime owns the gateway, is in test_container_runtime_owns_the_gateway.)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from personalclaw import cli_server, gateway_base


@pytest.fixture(autouse=True)
def _not_a_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)


def test_restart_uses_service_when_present():
    """restart_service() True → service owns the lifecycle; no spawn."""
    with (
        patch.object(cli_server.service_controller, "restart_service", return_value=True) as rs,
        patch.object(cli_server, "_spawn_detached_gateway") as spawn,
        patch.object(cli_server, "_stop") as stop,
    ):
        cli_server._restart(None)
    rs.assert_called_once()
    spawn.assert_not_called()
    stop.assert_not_called()


def test_restart_foreground_stops_then_spawns_on_the_port_it_had():
    """No service + running gateway → _stop, then a fresh one on the recorded port."""
    with (
        patch.object(cli_server.service_controller, "restart_service", return_value=False),
        patch.object(
            gateway_base, "live_gateway", return_value=gateway_base.LiveGateway(7777, 4242)
        ),
        patch.object(cli_server, "_stop") as stop,
        patch.object(cli_server, "_spawn_detached_gateway") as spawn,
    ):
        cli_server._restart(None)
    stop.assert_called_once_with(None)
    spawn.assert_called_once_with(7777)


def test_restart_does_not_spawn_when_stop_could_not_stop_the_gateway():
    """_stop exits nonzero while the gateway still runs → the restart ends there. Spawning
    anyway put a second gateway beside the first, on the same home."""
    with (
        patch.object(cli_server.service_controller, "restart_service", return_value=False),
        patch.object(
            gateway_base, "live_gateway", return_value=gateway_base.LiveGateway(7777, 4242)
        ),
        patch.object(cli_server, "_stop", side_effect=SystemExit(1)) as stop,
        patch.object(cli_server, "_spawn_detached_gateway") as spawn,
    ):
        with pytest.raises(SystemExit):
            cli_server._restart(None)
    stop.assert_called_once_with(None)
    spawn.assert_not_called()


def test_restart_with_nothing_running_spawns_without_a_stop():
    with (
        patch.object(cli_server.service_controller, "restart_service", return_value=False),
        patch.object(gateway_base, "live_gateway", return_value=None),
        patch.object(cli_server, "_stop") as stop,
        patch.object(cli_server, "_spawn_detached_gateway") as spawn,
    ):
        cli_server._restart(7777)
    stop.assert_not_called()
    spawn.assert_called_once_with(7777)


def test_spawn_detached_gateway_launches_personalclaw_gateway():
    """The detached spawn invokes `python -m personalclaw gateway --port` in a new session.

    Only the spawn is patched. The restart log and the audit row land in the per-test home
    conftest gives every test; patching `builtins.open` for the whole process also handed the
    security log a mock file to append its row to."""
    with patch.object(cli_server.subprocess, "Popen") as popen:
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
