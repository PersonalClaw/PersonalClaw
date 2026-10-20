"""In a container the container runtime runs the gateway, and `service`, `stop`, `restart` say so.

The gateway is the published image's own main process. `personalclaw service install` there
said "❌ … only supported on Linux (systemd) and macOS (launchd). On other platforms run
`personalclaw gateway` directly or wrap it in tmux/screen yourself": an error mark, and advice to
start a second gateway by hand beside the one already running. `personalclaw stop` failed for
want of `lsof`, and `restart` then started a second gateway anyway. Inside a container none of
them can do their job, because the container runtime on the host is the gateway's process
manager: each now says so, names the host command, and changes nothing.

Every call that would reach a real service manager is trapped here (``platform_calls``). That is
the assertion that nothing changed, and it is also what makes these tests safe to run against the
code before the fix, where a container was not recognised and this host's own launchd or systemd
would have been the target.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from personalclaw import cli_server, container_host, gateway_base
from personalclaw.service import controller, linux, macos
from personalclaw.service.common import Platform, current_platform


@pytest.fixture
def in_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """The published image, started by the README's `docker run`."""
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    monkeypatch.delenv(container_host.STARTED_BY_ENV, raising=False)


@pytest.fixture
def platform_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record, instead of making, every call that acts on a real service or process."""
    calls: list[str] = []

    def record(name: str, result: object = None):
        def call(*_args: object, **_kwargs: object) -> object:
            calls.append(name)
            return result

        return call

    for module, prefix in ((linux, "linux"), (macos, "macos")):
        for fn, result in (
            ("install", {}),
            ("uninstall", None),
            ("installed_environment", {}),
            ("status", ""),
            ("is_active", False),
            ("stop", None),
            ("restart", None),
        ):
            monkeypatch.setattr(module, fn, record(f"{prefix}.{fn}", result))
    monkeypatch.setattr(controller.tmux_substrate, "kill_server", record("tmux.kill_server"))

    def no_lookup(*args: object, **_kwargs: object) -> str:
        calls.append(f"subprocess.check_output {args[0] if args else ''}")
        raise FileNotFoundError("no lookup program in this test")

    monkeypatch.setattr(subprocess, "check_output", no_lookup)
    monkeypatch.setattr(cli_server.os, "kill", record("os.kill"))
    monkeypatch.setattr(cli_server, "_spawn_detached_gateway", record("spawn_gateway"))
    return calls


def _record_a_live_gateway(port: int = 10000, pid: int = 7) -> None:
    """This home's runtime record, as a gateway writes it once it listens."""
    gateway_base._runtime_path().write_text(json.dumps({"port": port, "pid": pid}), "utf-8")


def test_the_container_is_its_own_platform_whatever_is_on_path(in_the_image, monkeypatch) -> None:
    monkeypatch.setattr("personalclaw.service.common.shutil.which", lambda _name: "/usr/bin/x")
    assert current_platform() is Platform.CONTAINER


# ── personalclaw service ───────────────────────────────────────────────────────────────────


def test_service_install_says_the_runtime_keeps_it_running_and_changes_nothing(
    in_the_image, platform_calls, capsys
) -> None:
    assert controller.install_service() == 0
    out, err = capsys.readouterr()
    assert "the container runtime keeps it running" in out
    assert "`--restart unless-stopped`" in out
    assert "nothing was changed" in out
    assert "tmux" not in out + err and "screen" not in out + err
    assert out.isascii(), "plain words: no status emoji on a message that reports no failure"
    assert err == ""
    assert platform_calls == []


def test_with_compose_it_names_the_compose_files_restart_policy(
    in_the_image, platform_calls, capsys, monkeypatch
) -> None:
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")
    assert controller.install_service() == 0
    assert "`restart: unless-stopped`" in capsys.readouterr().out
    assert platform_calls == []


def test_service_uninstall_has_nothing_to_remove(in_the_image, platform_calls, capsys) -> None:
    assert controller.uninstall_service() == 0
    out, err = capsys.readouterr()
    assert "There is no service to remove here, and nothing was changed." in out
    assert err == ""
    assert platform_calls == []


def test_service_status_is_whether_the_gateway_runs(
    in_the_image, platform_calls, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(gateway_base, "live_gateway", lambda: gateway_base.LiveGateway(10000, 7))
    assert controller.service_status() == 0
    assert "The gateway is running, on port 10000." in capsys.readouterr().out
    monkeypatch.setattr(gateway_base, "live_gateway", lambda: None)
    assert controller.service_status() == 1
    assert "The gateway is not running." in capsys.readouterr().out
    assert platform_calls == []


# ── personalclaw stop / restart ────────────────────────────────────────────────────────────


def test_stop_in_a_container_signals_nothing_and_names_the_host_command(
    in_the_image, platform_calls, capsys
) -> None:
    """This home's gateway is recorded, so the container rule is the only refusal."""
    _record_a_live_gateway()
    with pytest.raises(SystemExit) as exc:
        cli_server._stop(None)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "the container runtime starts and stops it. Nothing was stopped." in err
    assert "\n    docker stop personalclaw" in err
    assert platform_calls == []


def test_restart_in_a_container_starts_no_second_gateway(
    in_the_image, platform_calls, capsys
) -> None:
    _record_a_live_gateway()
    with pytest.raises(SystemExit) as exc:
        cli_server._restart(None)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Nothing was restarted." in err
    assert "\n    docker restart personalclaw" in err
    assert platform_calls == []


def test_with_compose_stop_and_restart_name_the_compose_commands(
    in_the_image, platform_calls, capsys, monkeypatch
) -> None:
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")
    for command, verb in ((cli_server._stop, "stop"), (cli_server._restart, "restart")):
        with pytest.raises(SystemExit):
            command(None)
        assert f"docker compose -f deploy/compose/compose.yaml {verb} personalclaw-gateway" in (
            capsys.readouterr().err
        )
    assert platform_calls == []
