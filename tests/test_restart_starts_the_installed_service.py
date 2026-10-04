"""``stop``, ``restart`` and ``status`` follow the service installed for this home, running or not.

``personalclaw stop`` stops a launchd service by unloading it: the plist stays in
``~/Library/LaunchAgents``, and launchd loads it again at the next login. ``restart`` then asked
launchd whether the service runs, heard no, and started a gateway of its own, detached: no
KeepAlive, no start at login, and the service the user installed never came back. systemd had the
same gap, with ``systemctl is-active``.

The behaviour the code must have: an installed service file is how this home's gateway runs,
whether or not its service manager runs it now. ``restart`` starts it (stopping a gateway that was
started outside it first, so the two do not fight over the home and the port); ``stop`` says what
will really bring it back; ``status`` names it. A service installed for ANOTHER home is that
home's: none of the three touches it.

launchctl, systemctl and sudo are scripts in the test's own folder, first and alone on PATH, that
record what they were asked and change nothing else. The real service managers are never reached.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

import pytest

from personalclaw import cli_server, gateway_base
from personalclaw.service import controller
from personalclaw.service import linux as svc_linux
from personalclaw.service import macos as svc_macos
from personalclaw.service.common import Platform


class _FakeManager:
    """A service manager's command line, as a script on PATH: what it was asked, and whether
    it runs the service now."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.calls = folder / "calls.log"
        self.running = folder / "running"

    def asked(self) -> list[str]:
        if not self.calls.exists():
            return []
        return self.calls.read_text(encoding="utf-8").splitlines()

    def write(self, name: str, body: str) -> None:
        script = self.folder / name
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"{name} $*\" >> {shlex.quote(str(self.calls))}\n" + body,
            encoding="utf-8",
        )
        script.chmod(0o755)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A HOME of the test's own, whose default PersonalClaw home is the one the CLI uses."""
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
    return user_home


@pytest.fixture
def launchctl(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    """A Mac whose launchd is a script: ``list`` answers from a flag file, ``load`` sets it,
    ``unload`` clears it. The plist and the logs are in the test's own Library."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = _FakeManager(bin_dir)
    running = shlex.quote(str(fake.running))
    fake.write(
        "launchctl",
        f'case "$1" in\n'
        f"  list)\n"
        f"    if [ -f {running} ]; then\n"
        '      printf \'{\\n\\t"PID" = 4242;\\n\\t"Label" = "io.personalclaw.gateway";\\n};\\n\'\n'
        "      exit 0\n"
        "    fi\n"
        "    echo 'Could not find service \"io.personalclaw.gateway\" in domain for port' >&2\n"
        "    exit 113 ;;\n"
        f"  load) : > {running} ;;\n"
        f"  unload) /bin/rm -f {running} ;;\n"
        "esac\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    library = home / "Library"
    monkeypatch.setattr(svc_macos, "PLIST_DIR", library / "LaunchAgents")
    monkeypatch.setattr(
        svc_macos, "PLIST_PATH", library / "LaunchAgents" / "io.personalclaw.gateway.plist"
    )
    monkeypatch.setattr(svc_macos, "LOG_DIR", library / "Logs" / "PersonalClaw")
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", library / "Logs" / "PersonalClaw" / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", library / "Logs" / "PersonalClaw" / "gateway.err")
    for module in (controller, cli_server):
        monkeypatch.setattr(module, "current_platform", lambda: Platform.LAUNCHD)
    return fake


def _install_plist(carried: dict[str, str] | None = None) -> None:
    """The plist ``service install`` writes, for the home *carried* names (this one by default)."""
    svc_macos.PLIST_DIR.mkdir(parents=True, exist_ok=True)
    svc_macos.PLIST_PATH.write_text(svc_macos.render_plist(carried), encoding="utf-8")


@pytest.fixture
def no_detached_gateway(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record a detached gateway start, instead of starting one."""
    started: list[int] = []
    monkeypatch.setattr(cli_server, "_spawn_detached_gateway", started.append)
    return started


def test_restart_of_an_installed_service_launchd_does_not_run_loads_it(
    launchctl: _FakeManager, no_detached_gateway: list[int], capsys
) -> None:
    """🔑 After ``stop``, ``restart`` used to start a gateway outside the service instead."""
    _install_plist()

    cli_server._restart(None)

    assert f"launchctl load -w {svc_macos.PLIST_PATH}" in launchctl.asked(), launchctl.asked()
    assert launchctl.running.exists(), "launchd runs the service again"
    assert no_detached_gateway == [], "no gateway is started outside the service"
    out = capsys.readouterr().out
    assert "io.personalclaw.gateway" in out, out


def test_restart_first_stops_a_gateway_started_outside_the_service(
    launchctl: _FakeManager, no_detached_gateway: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gateway a detached restart started holds the home and the port: it is stopped, and
    only then is the service loaded, or the service's gateway could not bind and launchd would
    start it again and again."""
    _install_plist()
    order: list[str] = []
    monkeypatch.setattr(gateway_base, "live_gateway", lambda: gateway_base.LiveGateway(19703, 3939))
    monkeypatch.setattr(cli_server, "_stop", lambda port: order.append(f"stop {port}"))

    cli_server._restart(None)

    order.extend(line for line in launchctl.asked() if line.startswith("launchctl load"))
    assert order == ["stop None", f"launchctl load -w {svc_macos.PLIST_PATH}"], order
    assert no_detached_gateway == []


def test_restart_of_a_running_service_reloads_it(
    launchctl: _FakeManager, no_detached_gateway: list[int]
) -> None:
    _install_plist()
    launchctl.running.touch()

    cli_server._restart(None)

    asked = [line for line in launchctl.asked() if not line.startswith("launchctl list")]
    assert asked == [
        f"launchctl unload -w {svc_macos.PLIST_PATH}",
        f"launchctl load -w {svc_macos.PLIST_PATH}",
    ], asked
    assert no_detached_gateway == []


def test_stop_says_what_brings_the_service_back(launchctl: _FakeManager, capsys) -> None:
    """Unloaded, the service starts again at the next login, or with ``restart``: the message
    says both, and names the job."""
    _install_plist()
    launchctl.running.touch()

    cli_server._stop(None)

    assert f"launchctl unload {svc_macos.PLIST_PATH}" in launchctl.asked()
    out = capsys.readouterr().out
    assert "io.personalclaw.gateway" in out, out
    assert "next log in" in out, out
    assert "personalclaw restart" in out, out


def test_a_service_of_another_home_is_not_this_homes_to_stop_or_restart(
    launchctl: _FakeManager,
    no_detached_gateway: list[int],
    tmp_path: Path,
) -> None:
    """The plist runs another home: restarting this one leaves that service alone."""
    _install_plist({"PERSONALCLAW_HOME": str(tmp_path / "another-home")})
    launchctl.running.touch()

    cli_server._restart(None)

    assert [a for a in launchctl.asked() if not a.startswith("launchctl list")] == []
    assert launchctl.running.exists()
    assert len(no_detached_gateway) == 1, "this home has no service: a gateway of its own"


def test_status_names_the_service_and_the_command_that_starts_it(
    launchctl: _FakeManager, capsys
) -> None:
    """No gateway of this home is running (it keeps no record of one): the status says so, with
    the command that starts the service installed for this home."""
    _install_plist()

    cli_server._status(argparse.Namespace(port=None))

    out = capsys.readouterr().out
    assert "io.personalclaw.gateway" in out, out
    assert "personalclaw restart" in out, out
    assert "personalclaw gateway" not in out, out


# ── systemd: the same rule ─────────────────────────────────────────────────────────────────


@pytest.fixture
def systemctl(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    """A Linux host whose systemctl and sudo are scripts. ``is-active`` answers inactive."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = _FakeManager(bin_dir)
    fake.write("systemctl", 'case "$1" in\n  is-active) echo inactive; exit 3 ;;\nesac\nexit 0\n')
    fake.write("sudo", "exit 0\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("USER", "ada")
    unit = tmp_path / "etc" / "personalclaw.service"
    unit.parent.mkdir()
    monkeypatch.setattr(svc_linux, "UNIT_PATH", unit)
    unit.write_text(svc_linux.render_unit(), encoding="utf-8")
    for module in (controller, cli_server):
        monkeypatch.setattr(module, "current_platform", lambda: Platform.SYSTEMD)
    return fake


def test_restart_of_an_installed_systemd_unit_that_is_stopped_starts_it(
    systemctl: _FakeManager, no_detached_gateway: list[int]
) -> None:
    cli_server._restart(None)

    assert "sudo systemctl restart personalclaw.service" in systemctl.asked(), systemctl.asked()
    assert no_detached_gateway == []
