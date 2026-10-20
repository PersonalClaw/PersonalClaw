"""A gateway opens the dashboard in a browser only for the person who started it at a terminal.

Every start of the installed background service opened the dashboard in the machine's default
browser and signed that browser in for 30 days, unasked: its install, each login or boot, every
KeepAlive or ``Restart=`` restart, and ``personalclaw restart`` acting on it. The launchd plist and
the systemd unit ran ``personalclaw gateway`` with no ``--no-open``, and the gateway opened the
browser whenever ``dashboard.auto_open_browser`` was on and a browser could be reached, which under
launchd in a desktop session it always can.

The behaviour the code must have:

* A gateway that no person started at a terminal opens no browser and makes no sign-in link,
  whatever its command line says, so a service file an earlier release installed, which says
  nothing about the browser, opens none either. Its banner gives the address and names
  ``personalclaw token``.
* The plist, the systemd unit and a detached ``personalclaw restart`` start the gateway with
  ``--no-open``, and so does a restart of a gateway (the dashboard's Restart, an applied update),
  whose new image keeps the terminal of the start before it.
* ``personalclaw service install`` opens nothing and makes no link: it names ``personalclaw token``.
* A gateway started at a terminal opens the dashboard, as it always has.

Driven through the real start path (``GatewayOrchestrator.run``), the real plist and unit writers
and install, the real CLI parser and the real restart request, with launchctl, sudo, systemctl and
the browser faked. Whether a process was started at a terminal is read in child processes started
the way a service manager starts one (a session of its own, with no terminal) and in a terminal of
the test's own making.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from test_a_sign_in_link_never_reaches_a_log import _start_gateway, _startup_links

from personalclaw import cli_server, restart_request, self_update
from personalclaw.cli import _resolve_gateway_args, build_parser
from personalclaw.dashboard import session_store
from personalclaw.service import controller
from personalclaw.service import linux as svc_linux
from personalclaw.service import macos as svc_macos
from personalclaw.service.common import Platform

_BIN = "/home/user/.local/bin/personalclaw"

#: What the plist and the unit an earlier release installed start, left as they are by an upgrade:
#: only ``personalclaw service install`` writes a service file.
_AN_EARLIER_RELEASES_COMMAND = ["gateway"]


def _parsed(argv: list[str]) -> dict:
    """What the gateway starts with, given *argv* (a command line less its program), read by the
    CLI's own parser."""
    args = build_parser().parse_args(argv)
    assert args.command == "gateway"
    return _resolve_gateway_args(args)


def _plist_command() -> list[str]:
    with patch("personalclaw.service.common.shutil.which", return_value=_BIN):
        document = plistlib.loads(svc_macos.render_plist().encode("utf-8"))
    program, *argv = document["ProgramArguments"]
    assert program == _BIN
    return argv


def _unit_command(unit: str) -> list[str]:
    (exec_start,) = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
    program, *argv = shlex.split(exec_start.removeprefix("ExecStart="))
    assert program == _BIN
    return argv


def _render_unit(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("USER", "user")
    group = MagicMock(returncode=0, stdout="user\n", stderr="")
    with (
        patch("personalclaw.service.common.shutil.which", return_value=_BIN),
        patch("personalclaw.service.linux.subprocess.run", return_value=group),
    ):
        return svc_linux.render_unit()


# ── the service files ──────────────────────────────────────────────────────────────────────────


def test_the_launchd_plist_starts_the_gateway_with_no_open() -> None:
    argv = _plist_command()
    assert argv == ["gateway", "--no-open"], argv
    assert _parsed(argv)["no_open"] is True


def test_the_systemd_unit_starts_the_gateway_with_no_open(monkeypatch) -> None:
    argv = _unit_command(_render_unit(monkeypatch))
    assert argv == ["gateway", "--no-open"], argv
    assert _parsed(argv)["no_open"] is True


@pytest.mark.parametrize("service_file", ["plist", "unit"])
def test_the_service_files_start_opens_nothing_even_given_a_terminal(
    service_file, monkeypatch, capsys
) -> None:
    """The command line holds by itself: a service manager set up to give the job a terminal
    still starts a gateway that opens nothing and makes no link."""
    argv = _plist_command() if service_file == "plist" else _unit_command(_render_unit(monkeypatch))
    opened: list[str] = []
    _start_gateway(no_open=_parsed(argv)["no_open"], opened=opened, started_at_a_terminal=True)
    capsys.readouterr()

    assert opened == [], f"the {service_file}'s start opened the default browser"
    assert _startup_links() == {}, f"the {service_file}'s start made a sign-in link"


# ── a start nobody made at a terminal ──────────────────────────────────────────────────────────


def test_a_service_an_earlier_release_installed_opens_no_browser(capsys) -> None:
    """🔑 The regression. launchd starts the gateway of a plist an earlier release wrote: no
    terminal, a browser to hand and ``dashboard.auto_open_browser`` on, as it is by default.
    Nothing opens, and no sign-in link is made."""
    no_open = _parsed(_AN_EARLIER_RELEASES_COMMAND)["no_open"]
    assert no_open is False, "the earlier release's command line says nothing about the browser"
    opened: list[str] = []
    _start_gateway(no_open=no_open, opened=opened)
    out = capsys.readouterr().out

    assert opened == [], "a service start opened the default browser"
    assert _startup_links() == {}, "a service start made a sign-in link"
    assert "Opening it in the default browser" not in out, out
    assert "token=" not in out, out
    assert "personalclaw token" in out, "the banner says how to sign in"
    assert "http://127.0.0.1:19703" in out, "the banner gives the address"


def test_restarts_of_a_service_open_nothing(capsys) -> None:
    """A KeepAlive or ``Restart=`` restart is the same start again, as is a login or a boot:
    none of them opens a browser, and no sign-in link piles up."""
    opened: list[str] = []
    for _ in range(3):
        _start_gateway(no_open=False, opened=opened)
    capsys.readouterr()

    assert opened == []
    assert session_store.load_session_records() == {}, "a restart made a sign-in"


def test_a_gateway_started_at_a_terminal_opens_the_dashboard_as_before(capsys) -> None:
    """The control arm: the foreground start a person makes in a shell, auto-open on and a
    browser to hand, opens the dashboard once, signed in."""
    opened: list[str] = []
    _start_gateway(no_open=False, opened=opened, started_at_a_terminal=True)
    capsys.readouterr()

    assert len(opened) == 1 and "?token=" in opened[0], opened
    assert len(_startup_links()) == 1


def test_no_open_still_keeps_a_terminal_start_from_opening(capsys) -> None:
    opened: list[str] = []
    _start_gateway(no_open=True, opened=opened, started_at_a_terminal=True)
    capsys.readouterr()

    assert opened == []


# ── restarts ───────────────────────────────────────────────────────────────────────────────────


def _relaunched_with(monkeypatch: pytest.MonkeyPatch, *args: str) -> list[str]:
    """What a restart (the dashboard's Restart, an applied update) starts a gateway started with
    *args* with, less this install's own CLI."""
    monkeypatch.setattr("sys.argv", [_BIN, *args])
    return list(restart_request.relaunch_argv()[len(self_update.cli_argv()) :])


def test_a_restart_of_a_gateway_started_at_a_terminal_opens_no_browser(monkeypatch) -> None:
    """The new image keeps the old one's terminal, so without ``--no-open`` it opened the
    dashboard again, in a new sign-in, at every Restart and every applied update."""
    tail = _relaunched_with(monkeypatch, "gateway", "--port", "19703")
    assert tail == ["gateway", "--port", "19703", "--no-open"], tail
    assert _parsed(tail)["no_open"] is True


def test_a_restart_keeps_a_no_open_it_was_started_with_once(monkeypatch) -> None:
    tail = _relaunched_with(monkeypatch, "gateway", "--no-open", "--port", "auto")
    assert tail == ["gateway", "--no-open", "--port", "auto"], tail


def test_a_detached_restart_starts_its_gateway_with_no_open_and_no_terminal() -> None:
    """``personalclaw restart`` with no service installed: a gateway in a session of its own."""
    with patch.object(cli_server.subprocess, "Popen") as popen:
        cli_server._spawn_detached_gateway(19703)
    argv = popen.call_args.args[0]
    assert argv[len(self_update.cli_argv()) :] == ["gateway", "--port", "19703", "--no-open"]
    assert popen.call_args.kwargs.get("start_new_session") is True
    assert popen.call_args.kwargs.get("stdin") is subprocess.DEVNULL


# ── personalclaw service install ───────────────────────────────────────────────────────────────


@pytest.fixture
def no_browser_may_open():
    """Any browser open fails the test, wherever it comes from."""
    with patch("webbrowser.open", side_effect=AssertionError("a browser was opened")) as opened:
        yield opened


def test_service_install_on_macos_opens_nothing_and_names_the_way_to_sign_in(
    tmp_path, monkeypatch, capsys, no_browser_may_open
) -> None:
    """The real install, into the test's own Library, with a launchctl that succeeds: the plist
    it writes starts the gateway with ``--no-open``, and the install itself opens no browser and
    makes no link."""
    library = tmp_path / "Library"
    monkeypatch.setattr(svc_macos, "PLIST_DIR", library / "LaunchAgents")
    monkeypatch.setattr(svc_macos, "PLIST_PATH", library / "LaunchAgents" / "agent.plist")
    monkeypatch.setattr(svc_macos, "LOG_DIR", library / "Logs" / "PersonalClaw")
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", library / "Logs" / "PersonalClaw" / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", library / "Logs" / "PersonalClaw" / "gateway.err")
    launchctl = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with (
        patch("personalclaw.service.controller.current_platform", return_value=Platform.LAUNCHD),
        patch("personalclaw.service.common.shutil.which", return_value=_BIN),
        patch("personalclaw.service.macos.subprocess.run", launchctl),
    ):
        assert controller.install_service() == 0
    out = capsys.readouterr().out

    asked = [call.args[0] for call in launchctl.call_args_list]
    assert ["launchctl", "load", "-w", str(svc_macos.PLIST_PATH)] in asked
    with svc_macos.PLIST_PATH.open("rb") as written:
        assert plistlib.load(written)["ProgramArguments"] == [_BIN, "gateway", "--no-open"]
    assert "Sign in: personalclaw token" in out, out
    assert "token=" not in out, out
    assert session_store.load_session_records() == {}, "the install made a sign-in"


def test_service_install_on_linux_opens_nothing_and_names_the_way_to_sign_in(
    monkeypatch, capsys, no_browser_may_open
) -> None:
    """The real install with sudo and systemctl faked: the unit it hands ``sudo install`` starts
    the gateway with ``--no-open``, and the install opens no browser and makes no link."""
    monkeypatch.setenv("USER", "user")
    units: list[str] = []

    def _run(cmd, **_kwargs):  # noqa: ANN001, ANN202
        if cmd[:2] == ["sudo", "install"]:
            units.append(Path(cmd[-2]).read_text(encoding="utf-8"))
        return MagicMock(returncode=0, stdout="user\n" if cmd[0] == "id" else "", stderr="")

    with (
        patch("personalclaw.service.controller.current_platform", return_value=Platform.SYSTEMD),
        patch("personalclaw.service.common.shutil.which", return_value=_BIN),
        patch("personalclaw.service.linux.subprocess.run", side_effect=_run),
    ):
        assert controller.install_service() == 0
    out = capsys.readouterr().out

    (unit,) = units
    assert _unit_command(unit) == ["gateway", "--no-open"]
    assert "Sign in: personalclaw token" in out, out
    assert "token=" not in out, out
    assert session_store.load_session_records() == {}, "the install made a sign-in"


# ── whether a process was started at a terminal ───────────────────────────────────────────────

_ASK = "from personalclaw.env import started_at_a_terminal; print(started_at_a_terminal())"


def _asked_in_a_child(tmp_path: Path, *, stdin: int, prelude: str = "") -> str:
    """What ``started_at_a_terminal`` answers in a child started in a session of its own, as
    launchd, systemd and a detached restart start a gateway, its stdin *stdin*."""
    home = tmp_path / "user"
    home.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "PERSONALCLAW_HOME": str(tmp_path / "home"),
    }
    done = subprocess.run(
        [sys.executable, "-c", prelude + _ASK],
        stdin=stdin,
        capture_output=True,
        text=True,
        env=env,
        start_new_session=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_a_process_a_service_manager_starts_was_not_started_at_a_terminal(tmp_path) -> None:
    assert _asked_in_a_child(tmp_path, stdin=subprocess.DEVNULL) == "False"


def test_a_process_in_a_terminals_session_was_started_at_a_terminal(tmp_path) -> None:
    """The control arm: the same child, given a terminal of the test's own as its controlling
    terminal, the way a shell's terminal is a command's."""
    primary, secondary = os.openpty()
    try:
        answer = _asked_in_a_child(
            tmp_path,
            stdin=secondary,
            prelude="import fcntl, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); ",
        )
    finally:
        os.close(secondary)
        os.close(primary)
    assert answer == "True"
