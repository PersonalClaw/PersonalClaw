"""What sudo, systemctl, launchctl or git printed reaches the owner's terminal masked, and useful.

🔴 ``personalclaw service install`` put what a failing ``sudo``, ``systemctl`` or ``launchctl``
printed into its error whole, and ``personalclaw service status`` printed systemd's status block
as it came: that block ends with the gateway's latest journal lines, which hold whatever a log
line held. ``personalclaw update`` did the same with a failed git step. A proxy login in a URL, a
token in a remote's message or an escape sequence that moves the cursor reached the terminal as
the child wrote it.

Now each is masked the way every view masks, keeps its line breaks for the person reading it and
shows each control character as a visible escape. A failure keeps its end, where the reason and
what to do are.

Nothing here touches the machine's service manager: every ``sudo``/``systemctl``/``launchctl``
call is replaced, and the files are written under ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.service import linux as svc_linux
from personalclaw.service import macos as svc_macos
from personalclaw.service.common import COMMAND_SAID_CHARS

#: A login a child can print: a proxy URL with its password in it.
PASSWORD = "hunter2-proxy-pass"
LOGIN_URL = f"http://ada:{PASSWORD}@proxy.example.com:3128"


def _failed(stderr: str = "", stdout: str = "", code: int = 1) -> MagicMock:
    return MagicMock(returncode=code, stdout=stdout, stderr=stderr)


def _assert_masked(text: str) -> None:
    assert PASSWORD not in text, text
    assert "\x1b" not in text and "\x07" not in text, "a control character reached the terminal"


@pytest.fixture
def unit_host(monkeypatch):
    """``linux.install()`` with the unit write, ``sudo``/``systemctl`` and ``id`` replaced."""
    monkeypatch.setenv("USER", "ada")
    with patch("personalclaw.service.common.shutil.which", return_value="/u/bin/personalclaw"):
        yield


def test_a_unit_write_sudo_refused_says_why_masked(unit_host) -> None:
    said = (
        f"sudo: fetching the password helper through {LOGIN_URL} failed\x1b[2K\n"
        "sudo: a terminal is required to read the password; either use the -S option to read "
        "from standard input or configure an askpass helper"
    )
    with (
        patch.object(svc_linux, "_write_unit_via_sudo", return_value=_failed(stderr=said)),
        patch("personalclaw.service.linux.subprocess.run", return_value=_failed(stdout="staff")),
        pytest.raises(svc_linux.ServiceInstallError) as caught,
    ):
        svc_linux.install()

    message = str(caught.value)
    _assert_masked(message)
    assert "\\x1b[2K" in message, "the escape is shown, not obeyed"
    assert "a terminal is required to read the password" in message
    assert "sudo install said:" in message


def test_a_failed_restart_keeps_the_end_of_what_systemctl_said(unit_host) -> None:
    """systemd prints its reason last. A long output keeps that and drops the start."""
    noise = "\n".join(f"warning {i}: unit file changed on disk" for i in range(200))
    reason = (
        "Job for personalclaw.service failed because the control process exited with error "
        'code. See "systemctl status personalclaw.service" for details.'
    )

    def systemctl(*args, **kwargs):
        if "restart" in args:
            return _failed(stderr=f"{noise}\n{reason}")
        return _failed(code=0, stdout="staff")

    with (
        patch.object(svc_linux, "_write_unit_via_sudo", return_value=_failed(code=0)),
        patch.object(svc_linux, "_systemctl", side_effect=systemctl),
        pytest.raises(svc_linux.ServiceInstallError) as caught,
    ):
        svc_linux.install()

    message = str(caught.value)
    assert reason in message
    assert "warning 0:" not in message
    assert len(message) < COMMAND_SAID_CHARS + 200, len(message)
    assert "journalctl -u personalclaw.service" in message, "what to run next is still there"


def test_systemd_status_masks_the_journal_lines_it_ends_with() -> None:
    block = (
        "● personalclaw.service - PersonalClaw gateway\n"
        "     Active: active (running) since Mon 2026-09-28 09:00:00 UTC\n"
        f"Sep 28 09:00:01 host personalclaw[42]: proxy {LOGIN_URL} refused\x07\n"
    )
    with patch(
        "personalclaw.service.linux.subprocess.run", return_value=_failed(code=0, stdout=block)
    ):
        shown = svc_linux.status()

    _assert_masked(shown)
    assert "Active: active (running)" in shown
    assert len(shown.splitlines()) == 3, "each line stays a line"


@pytest.fixture
def agent_host(tmp_path, monkeypatch):
    """The LaunchAgent paths under tmp_path."""
    plist = tmp_path / "LaunchAgents" / "io.personalclaw.gateway.plist"
    monkeypatch.setattr(svc_macos, "PLIST_DIR", plist.parent)
    monkeypatch.setattr(svc_macos, "PLIST_PATH", plist)
    monkeypatch.setattr(svc_macos, "LOG_DIR", tmp_path / "Logs")
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", tmp_path / "Logs" / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", tmp_path / "Logs" / "gateway.err")
    with patch("personalclaw.service.common.shutil.which", return_value="/u/bin/personalclaw"):
        yield plist


def test_a_failed_launchctl_load_says_why_masked(agent_host) -> None:
    said = f"Load failed: 5: Input/output error\x1b]0;{LOGIN_URL}\x07"
    with (
        patch("personalclaw.service.macos.subprocess.run", return_value=_failed(stderr=said)),
        pytest.raises(svc_macos.ServiceInstallError) as caught,
    ):
        svc_macos.install()

    message = str(caught.value)
    _assert_masked(message)
    assert "Load failed: 5: Input/output error" in message
    assert f"Plist: {agent_host}" in message


def test_launchd_status_masks_what_launchctl_said() -> None:
    listed = f'{{\n\t"PID" = 4242;\n\t"LastExitStatus" = 0;\n\t"Proxy" = "{LOGIN_URL}";\n}};\n'
    with patch(
        "personalclaw.service.macos.subprocess.run", return_value=_failed(code=0, stdout=listed)
    ):
        shown = svc_macos.status()

    _assert_masked(shown)
    assert '"PID" = 4242;' in shown

    missing = f"Could not find service \x1b[31mio.personalclaw.gateway\x1b[0m ({LOGIN_URL})\n"
    with patch(
        "personalclaw.service.macos.subprocess.run", return_value=_failed(code=113, stderr=missing)
    ):
        shown = svc_macos.status()

    _assert_masked(shown)
    assert shown.startswith("personalclaw service is not loaded (Could not find service")
    assert shown.count("\n") == 1, "one line"


@pytest.mark.parametrize("channel", ["nightly", "stable"])
def test_a_failed_update_step_prints_what_git_said_masked(channel, monkeypatch, capsys) -> None:
    from personalclaw import cli_server, self_update

    said = (
        f"fatal: unable to access '{LOGIN_URL}/r.git/': Could not resolve proxy\x1b[1A\n"
        "fatal: the remote end hung up unexpectedly"
    )
    failed = subprocess.CompletedProcess(["git", "fetch"], 128, "", said)
    monkeypatch.setattr(self_update, "resolve_default_branch", lambda _: "main")
    monkeypatch.setattr(self_update, "git_fetch", lambda *_: failed)
    monkeypatch.setattr(self_update, "git_fetch_tags", lambda *_: failed)
    monkeypatch.setattr(self_update, "git_tracked_changes", lambda *_: [])

    async def resolve_target(*_):
        return "v9.9.9"

    monkeypatch.setattr(self_update, "resolve_target", resolve_target)
    monkeypatch.setattr(cli_server, "_already_there", lambda *_: False)

    with pytest.raises(SystemExit):
        if channel == "nightly":
            cli_server._update_git_nightly("/nowhere")
        else:
            cli_server._update_git_release("/nowhere", "stable", "")

    printed = capsys.readouterr().err
    _assert_masked(printed)
    assert "Could not resolve proxy" in printed
    assert "fatal: the remote end hung up unexpectedly" in printed.splitlines()[-1]
