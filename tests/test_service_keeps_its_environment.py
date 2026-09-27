"""The service starts the gateway with the environment the gateway needs, and never a secret.

🔴 The launchd plist and the systemd unit carried ``HOME`` and ``PATH`` and nothing else. A user's
``AWS_PROFILE`` was gone, so Amazon Bedrock fell back to the default credential chain and failed
under the service; ``CLAUDE_CONFIG_DIR``, ``CODEX_HOME`` and ``HF_HOME`` were gone, so the agent
CLIs, the import and local models read other directories; ``PERSONALCLAW_HOME`` was gone, so the
service ran on ``~/.personalclaw`` while the CLI used the home the user chose.

The first four tests drive ``install()`` and ``service status`` as they are, with no argument the
fix added, so they fail on the code before it by assertion. The rest hold the contract that
replaced it: what is carried, what is refused and how it is changed.

Nothing here touches the machine's service manager: every ``launchctl``/``systemctl``/``sudo``
call is replaced, and the files are written under ``tmp_path`` or kept in memory.
"""

from __future__ import annotations

import plistlib
import re
import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.service import controller
from personalclaw.service import linux as svc_linux
from personalclaw.service import macos as svc_macos
from personalclaw.service.common import Platform

_ROOT = Path(__file__).resolve().parents[1]

#: What this user's shell sets for the tools the gateway runs.
SHELL = {
    "PERSONALCLAW_HOME": "/Users/ada/pc-home",
    "AWS_PROFILE": "bedrock-dev",
    "AWS_REGION": "us-east-1",
    "CLAUDE_CONFIG_DIR": "/Users/ada/.config/claude",
    "CODEX_HOME": "/Users/ada/.config/codex",
    "HF_HOME": "/Users/ada/models/hf",
    "HTTPS_PROXY": "http://proxy.corp:3128",
}
#: Secrets the same shell exports. None may reach a service file.
SECRETS = {
    "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "AWS_SESSION_TOKEN": "IQoJb3JpZ2luX2VjEXAMPLESESSIONTOKEN0123456789",
    "GITHUB_TOKEN": "ghp_exampleExampleExampleExample0123456789",
}


@pytest.fixture
def shell(monkeypatch):
    """This shell's environment: the variables above set, and none of the secrets."""
    for name in SECRETS:
        monkeypatch.delenv(name, raising=False)
    for name, value in SHELL.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("USER", "ada")
    return monkeypatch


@pytest.fixture
def launchd(tmp_path, monkeypatch):
    """The LaunchAgent paths under tmp_path, and a launchctl that does nothing."""
    plist = tmp_path / "LaunchAgents" / "io.personalclaw.gateway.plist"
    monkeypatch.setattr(svc_macos, "PLIST_DIR", plist.parent)
    monkeypatch.setattr(svc_macos, "PLIST_PATH", plist)
    monkeypatch.setattr(svc_macos, "LOG_DIR", tmp_path / "Logs")
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", tmp_path / "Logs" / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", tmp_path / "Logs" / "gateway.err")
    launchctl = MagicMock(returncode=0, stdout='{\n\t"PID" = 4242;\n};\n', stderr="")
    with (
        patch("personalclaw.service.common.shutil.which", return_value="/u/bin/personalclaw"),
        patch("personalclaw.service.macos.subprocess.run", return_value=launchctl),
    ):
        yield plist


@pytest.fixture
def systemd():
    """``linux.install()`` with ``sudo``, ``systemctl`` and ``id`` replaced: yields the unit
    texts it wrote, in order, instead of writing ``/etc/systemd/system``."""
    written: list[str] = []

    def _write(contents: str):
        written.append(contents)
        return MagicMock(returncode=0, stdout="", stderr="")

    ok = MagicMock(returncode=0, stdout="staff\n", stderr="")
    with (
        patch("personalclaw.service.common.shutil.which", return_value="/u/bin/personalclaw"),
        patch("personalclaw.service.linux._write_unit_via_sudo", side_effect=_write),
        patch("personalclaw.service.linux.subprocess.run", return_value=ok),
    ):
        yield written


def _plist_environment(path: Path) -> dict[str, str]:
    with path.open("rb") as fh:
        return plistlib.load(fh)["EnvironmentVariables"]


def _unit_environment(unit: str) -> dict[str, str]:
    """The variables a unit sets, read the way systemd reads ``Environment=``: whitespace
    separates assignments, double quotes group one, ``\\`` escapes, ``%%`` is a ``%``."""
    env: dict[str, str] = {}
    for line in unit.splitlines():
        if line.startswith("Environment="):
            for assignment in shlex.split(line[len("Environment=") :]):
                name, _, value = assignment.partition("=")
                env[name] = value.replace("%%", "%")
    return env


# ── the defect: the environment the gateway needs ─────────────────────────────────────────────


def test_the_launchd_plist_carries_what_the_gateway_needs(shell, launchd):
    svc_macos.install()

    env = _plist_environment(launchd)
    missing = {k: v for k, v in SHELL.items() if env.get(k) != v}
    assert not missing, f"the plist dropped {sorted(missing)}; it carries {sorted(env)}"
    assert env["HOME"] and env["PATH"]


def test_the_systemd_unit_carries_what_the_gateway_needs(shell, systemd):
    svc_linux.install()

    env = _unit_environment(systemd[-1])
    missing = {k: v for k, v in SHELL.items() if env.get(k) != v}
    assert not missing, f"the unit dropped {sorted(missing)}; it carries {sorted(env)}"
    assert env["USER"] == "ada" and env["HOME"] and env["PATH"]


def test_status_shows_the_environment_the_service_starts_with(shell, launchd, capsys):
    svc_macos.install()
    capsys.readouterr()
    with patch("personalclaw.service.controller.current_platform", return_value=Platform.LAUNCHD):
        controller.service_status()

    out = capsys.readouterr().out
    for name, value in SHELL.items():
        assert f"{name}={value}" in out, f"`service status` does not show {name}"


def test_the_install_says_what_it_carried_and_what_it_left_out(shell, launchd, capsys):
    shell.setenv("AWS_SESSION_TOKEN", SECRETS["AWS_SESSION_TOKEN"])
    with patch("personalclaw.service.controller.current_platform", return_value=Platform.LAUNCHD):
        assert controller.install_service() == 0

    out = capsys.readouterr().out
    assert "Carried from this shell:" in out and "AWS_PROFILE" in out
    assert "Not carried: AWS_SESSION_TOKEN, because it names a secret" in out
    assert SECRETS["AWS_SESSION_TOKEN"] not in out


# ── never a secret ────────────────────────────────────────────────────────────────────────────


def test_no_secret_reaches_either_service_file(shell, launchd, systemd):
    from personalclaw.service.environment import summary

    for name, value in SECRETS.items():
        shell.setenv(name, value)
    shell.setenv("HTTPS_PROXY", "http://ada:hunter2-proxy-pass@proxy.corp:3128")

    carried = svc_macos.install(extra=["GITHUB_TOKEN"])
    svc_linux.install(extra=["GITHUB_TOKEN"])
    plist_text = launchd.read_text(encoding="utf-8")
    unit_text = systemd[-1]

    for secret in (*SECRETS.values(), "hunter2-proxy-pass"):
        assert secret not in plist_text, "a secret reached the plist"
        assert secret not in unit_text, "a secret reached the systemd unit"
    assert sorted(carried.secrets) == sorted(SECRETS), carried.secrets
    assert [name for name, _ in carried.refused] == ["HTTPS_PROXY"], carried.refused
    said = summary(carried)
    secrets_line = [line for line in said if all(name in line for name in SECRETS)]
    assert len(secrets_line) == 1 and "Settings → Secrets" in secrets_line[0], said
    proxy_line = [line for line in said if line.startswith("Not carried: HTTPS_PROXY, because")]
    assert len(proxy_line) == 1 and "its value has a credential in it" in proxy_line[0], said


# ── changing it ───────────────────────────────────────────────────────────────────────────────


def test_env_carries_one_more_and_no_env_leaves_one_out(shell, launchd):
    shell.setenv("NOTES_VAULT_DIR", "/Users/ada/Notes/Garden")

    carried = svc_macos.install(extra=["NOTES_VAULT_DIR"], without=["HF_HOME"])

    env = _plist_environment(launchd)
    assert env["NOTES_VAULT_DIR"] == "/Users/ada/Notes/Garden"
    assert "HF_HOME" not in env
    assert "NOTES_VAULT_DIR" in carried.env


def test_asking_for_a_variable_the_shell_does_not_set_says_so():
    from personalclaw.service.environment import capture

    result = capture({}, extra=["NOT_SET_ANYWHERE"])
    assert result.refused == [
        ("NOT_SET_ANYWHERE", "NOT_SET_ANYWHERE, because this shell does not set it.")
    ]


def test_the_cli_takes_env_and_no_env(monkeypatch):
    from personalclaw import cli, cli_server

    args = cli.build_parser().parse_args(
        ["service", "install", "--env", "A_DIR", "--env", "B_DIR", "--no-env", "HF_HOME"]
    )
    seen = {}

    def _install(**kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(cli_server.service_controller, "install_service", _install)
    monkeypatch.setattr(cli_server, "sel", lambda: MagicMock())
    assert cli_server._service_cmd(args) == 0
    assert list(seen["extra"]) == ["A_DIR", "B_DIR"] and list(seen["without"]) == ["HF_HOME"]


# ── the files hold what they are given ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ['C:\\tools\\a "quoted" dir', "50%off/path", "a b  c", "semi;colon $DOLLAR `tick`"],
)
def test_a_value_reads_back_from_the_unit_exactly(shell, systemd, value):
    shell.setenv("HF_HOME", value)
    svc_linux.install()
    assert _unit_environment(systemd[-1])["HF_HOME"] == value


def test_a_value_reads_back_from_the_plist_exactly(shell, launchd):
    shell.setenv("HF_HOME", "/Users/ada/a&b <models>")
    svc_macos.install()
    assert _plist_environment(launchd)["HF_HOME"] == "/Users/ada/a&b <models>"


# ── the contract is the documented one ────────────────────────────────────────────────────────


def test_no_carried_name_is_a_credential():
    from personalclaw.apps.secret_fields import is_credential_field_name
    from personalclaw.service.environment import carried_names

    assert [n for n in carried_names() if is_credential_field_name(n)] == []


def test_the_cli_reference_lists_exactly_the_carried_names():
    """The table in the CLI reference is the documented contract: the same names, both ways."""
    from personalclaw.service.environment import carried_names

    doc = (_ROOT / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    section = doc.split("## `personalclaw service`", 1)[1].split("\n## ", 1)[0]
    table = section.split("| For | Variables |", 1)[1].split("\n\n", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("|")][1:]  # past |---|
    assert rows, "the table is gone, so this check would pass on nothing"
    documented = {n for row in rows for n in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", row)}
    assert documented == set(carried_names()), (
        f"documented, not carried: {sorted(documented - set(carried_names()))}; "
        f"carried, not documented: {sorted(set(carried_names()) - documented)}"
    )
