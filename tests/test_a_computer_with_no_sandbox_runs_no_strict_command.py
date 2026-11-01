"""A computer with no sandbox runs no command set to the strict sandbox, and says what fixes it.

Where this computer gives PersonalClaw no sandbox (a Linux host that withholds unprivileged user
namespaces, with no ``sandbox-exec``), every command ran unconfined, whatever level it was set to,
and only the log said so, once. Now a command set to the strict level is refused with a sentence
that names the setting withholding the namespaces and how to change it, and is never run. A command
at the standard level still runs, held by PersonalClaw's own checks, and the chat's Execution
Boundary and the Doctor say so, with the same remedy.

The host's answer is stood in for (``detect_backend`` answers ``"none"``), so these run on every
host; ``tests/test_the_sandbox_where_the_host_withholds_namespaces.py`` reads a real one.
"""

from __future__ import annotations

import pytest

from personalclaw import cli_doctor, sandbox
from personalclaw.dashboard import chat_boundary
from personalclaw.net import policy

pytestmark = pytest.mark.security

COMMANDS = "the commands it runs"


@pytest.fixture
def no_sandbox(monkeypatch, tmp_path):
    """This computer has no sandbox, on Linux, where AppArmor withholds the namespaces."""
    monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": "none")
    monkeypatch.setattr(policy, "no_network_for_commands", lambda session_key=None: "")
    monkeypatch.setattr("sys.platform", "linux")
    setting = tmp_path / "kernel" / "apparmor_restrict_unprivileged_userns"
    setting.parent.mkdir(parents=True)
    setting.write_text("1\n")
    monkeypatch.setattr(sandbox, "_PROC_SYS", tmp_path, raising=False)


def test_a_strict_command_is_not_run(no_sandbox) -> None:
    """🔴 Before: it ran unconfined, as every command did."""
    with pytest.raises(sandbox.SandboxEnforcementUnavailable) as refused:
        sandbox.wrap_argv(["/bin/true"], "strict")

    said = str(refused.value)
    assert said.startswith("This command was not run: it is set to run in the strict sandbox")
    assert "kernel.apparmor_restrict_unprivileged_userns=1" in said, said
    assert "AppArmor profile" in said, said
    assert "keep that after a restart in a file under /etc/sysctl.d" in said, said


def test_a_tool_refuses_it_before_it_starts(no_sandbox) -> None:
    assert sandbox.wrap_refusal("strict") == sandbox.strict_refusal()
    assert sandbox.wrap_refusal("standard") == ""


def test_a_standard_command_runs_with_personalclaws_own_checks(no_sandbox) -> None:
    assert sandbox.wrap_argv(["/bin/true"], "standard") == (["/bin/true"], None)


@pytest.mark.parametrize(
    "setting, value, words",
    [
        ("kernel/unprivileged_userns_clone", "0", "kernel.unprivileged_userns_clone=1"),
        ("user/max_user_namespaces", "0", "user.max_user_namespaces=15000"),
    ],
)
def test_the_remedy_names_the_setting_that_withholds_them(
    no_sandbox, tmp_path, setting, value, words
) -> None:
    (tmp_path / "kernel" / "apparmor_restrict_unprivileged_userns").write_text("0\n")
    (tmp_path / setting).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / setting).write_text(value)

    remedy = sandbox.no_sandbox_remedy()
    assert words in remedy and "after a restart in a file under /etc/sysctl.d" in remedy, remedy


def test_the_boundary_says_the_commands_are_held_only_by_personalclaws_checks(no_sandbox) -> None:
    """🔴 Before: it said they were not sandboxed, and nothing of what would sandbox them."""
    said = chat_boundary._on_this_computer("standard", subject=COMMANDS)

    assert said["confined"] is False and said["label"] == "Not sandboxed"
    assert "held only by PersonalClaw's own checks" in said["said"], said
    assert "kernel.apparmor_restrict_unprivileged_userns=1" in said["said"], said


def test_the_boundary_says_strict_commands_are_not_run(no_sandbox) -> None:
    said = chat_boundary._on_this_computer("strict", subject=COMMANDS)

    assert (said["confined"], said["label"]) == (True, "Not run")
    assert said["said"].startswith("The commands it runs are set to the strict sandbox"), said


def test_the_doctor_says_so_and_names_the_remedy(no_sandbox, capsys) -> None:
    """🔴 Before: the Doctor had no line for the sandbox at all."""
    issues = cli_doctor._doctor_sandbox()

    out = capsys.readouterr().out
    assert "sandbox:" in out and "PersonalClaw's own checks only" in out, out
    assert issues and "kernel.apparmor_restrict_unprivileged_userns=1" in issues[0], issues


def test_the_doctor_names_a_working_sandbox(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": "sandbox-exec")

    assert cli_doctor._doctor_sandbox() == []
    assert "sandbox-exec" in capsys.readouterr().out


def test_a_connector_packs_parse_script_is_not_run(no_sandbox, tmp_path, monkeypatch) -> None:
    """A pack's parse script is third-party code, run at the strict level, so a computer with no
    sandbox refuses it with the same sentence, naming the fix, and starts nothing. 🔴 Before: it
    ran unconfined, held only by its own import fence."""
    import subprocess

    from personalclaw.knowledge_providers import pack_parse

    started: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **_kw: started.append(argv))
    script = tmp_path / "parse.py"
    script.write_text("print('{}')\n", encoding="utf-8")

    with pytest.raises(sandbox.SandboxEnforcementUnavailable) as refused:
        pack_parse.run_parse_script(script, b"<feed/>")

    assert str(refused.value) == sandbox.strict_refusal()
    assert "keep that after a restart in a file under /etc/sysctl.d" in str(refused.value)
    assert started == []
