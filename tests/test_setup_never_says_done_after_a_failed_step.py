"""`personalclaw setup` never says "Done!" after a step that failed.

It is the first command a new install runs, and the website one-liner ends on
`exec personalclaw setup`, so its exit status is the installer's. The interactive wizard
printed a failed step and carried on to "Done!" with exit 0:

* a workspace folder it could not create ("❌ Cannot create …", then "Falling back to the
  default", which was not where the workspace stayed when one was already configured);
* an agent config it could not write (a traceback, and the steps after it never ran);
* a default agent it could not add to an unreadable `config.json` (one line on stderr);
* an orchestrator skill that raised ("⚠️ … failed");
* a timezone or dashboard URL it could not save, or three answers that were not zones.

Each step now returns why it failed. The wizard goes on to the next step, then names every
failure with the command that runs that step again, and exits 1. The steps that worked stay
saved, and running `setup` again is safe.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from personalclaw import cli_setup
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import ConfigWriteError


class _Terminal:
    """A stdin that says it is a terminal, so ``_ask`` reads the patched ``input``."""

    def isatty(self) -> bool:
        return True


def _answer(monkeypatch, answers: dict[str, str]) -> None:
    """Answer each prompt whose text contains a key; Enter for every other prompt."""
    monkeypatch.setattr(cli_setup.sys, "stdin", _Terminal())

    def fake_input(prompt: str = "") -> str:
        return next((a for key, a in answers.items() if key in prompt), "")

    monkeypatch.setattr("builtins.input", fake_input)


@pytest.fixture(autouse=True)
def _a_machine_with_a_zone(monkeypatch):
    """The detected zone is fixed, so the timezone step always has something to save."""
    monkeypatch.setattr(cli_setup, "_detect_system_timezone", lambda: "Europe/Berlin")


def _refuse_config_writes(monkeypatch, only: str = "") -> None:
    """Refuse the wizard's `config.json` writes, or only those that set the key ``only``."""
    saving = cli_setup.mutate_config

    def refuse(mutator, *, path=None, **kwargs):
        wanted: dict = {}
        mutator(wanted)
        if only and only not in wanted:
            return saving(mutator, path=path, **kwargs)
        raise ConfigWriteError("another writer holds config.json")

    monkeypatch.setattr(cli_setup, "mutate_config", refuse)


def _a_typed_workspace_that_cannot_exist(monkeypatch, tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    _answer(monkeypatch, {"Workspace path": str(blocker / "workspace")})


def _the_agent_install_raises(monkeypatch, tmp_path: Path) -> None:
    def boom(*, clean: bool = False) -> Path:
        raise PermissionError(13, "Permission denied", str(tmp_path / "agents"))

    monkeypatch.setattr("personalclaw.agent.rebuild_agent_config", boom)


def _the_skills_folder_is_read_only(monkeypatch, tmp_path: Path) -> None:
    def boom():
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(cli_setup, "SkillsLoader", boom)


def _the_timezone_cannot_be_saved(monkeypatch, tmp_path: Path) -> None:
    _refuse_config_writes(monkeypatch)


def _three_answers_that_are_not_zones(monkeypatch, tmp_path: Path) -> None:
    _answer(monkeypatch, {"Timezone": "Mars/Olympus_Mons"})


def _the_channel_check_crashes(monkeypatch, tmp_path: Path) -> None:
    async def boom(_transports):
        raise RuntimeError("a channel app's health check crashed")

    monkeypatch.setattr("personalclaw.channel_transports.configured_channels", boom)


def _the_dashboard_url_cannot_be_saved(monkeypatch, tmp_path: Path) -> None:
    """A remote host with a channel, so the wizard asks for the URL, and its write is refused."""

    async def one_channel(_transports):
        return ["a-channel"]

    monkeypatch.setattr("personalclaw.channel_transports.configured_channels", one_channel)
    monkeypatch.setattr(cli_setup.socket, "gethostbyname", lambda _host: "10.1.2.3")
    _answer(monkeypatch, {"Dashboard URL": "http://box.lan:10000"})
    _refuse_config_writes(monkeypatch, only="dashboard")


@pytest.mark.parametrize(
    ("arrange", "step", "retry", "why"),
    [
        (
            _a_typed_workspace_that_cannot_exist,
            "Workspace directory",
            "personalclaw setup",
            "as the workspace",
        ),
        (
            _the_agent_install_raises,
            "Agent config",
            "personalclaw setup --agent-only",
            "PermissionError: [Errno 13] Permission denied",
        ),
        (
            _the_skills_folder_is_read_only,
            "Orchestrator skill",
            "personalclaw setup --agent-only",
            "Read-only file system",
        ),
        (_the_timezone_cannot_be_saved, "Timezone", "personalclaw setup", "another writer"),
        (_three_answers_that_are_not_zones, "Timezone", "personalclaw setup", "3 tries"),
        (_the_channel_check_crashes, "Dashboard URL", "personalclaw setup", "RuntimeError"),
        (
            _the_dashboard_url_cannot_be_saved,
            "Dashboard URL",
            "personalclaw setup",
            "could not save the dashboard URL: another writer",
        ),
    ],
    ids=lambda v: v.__name__.strip("_") if callable(v) else None,
)
def test_a_failed_step_is_named_with_its_retry_and_the_wizard_exits_1(
    arrange, step, retry, why, monkeypatch, capsys, tmp_path
):
    arrange(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        cli_setup._setup()

    assert exit_info.value.code == 1
    out, err = capsys.readouterr()
    assert "Done!" not in out + err
    summary = err[err.index("Setup did not finish: 1 step failed.") :]
    failure = summary.splitlines()[1]
    assert failure.startswith(f"  ❌ {step}: ") and why in failure, summary
    assert f"Run it again: {retry}" in summary
    # One failed step does not cost the steps after it: the wizard ran to its last section.
    assert "── Custom Domain ──" in out


def test_a_typed_workspace_that_fails_says_where_the_workspace_stays(monkeypatch, capsys, tmp_path):
    """The old line said "Falling back to the default" while an earlier choice stayed in force."""
    configured = tmp_path / "chosen-before"
    configured.mkdir()
    (config_loader.config_dir() / "workspace_dir").write_text(f"{configured}\n", encoding="utf-8")
    _a_typed_workspace_that_cannot_exist(monkeypatch, tmp_path)

    assert cli_setup._setup_workspace_dir()

    out, err = capsys.readouterr()
    assert f"The workspace stays {configured}." in out
    assert "Falling back" not in out + err
    assert "❌ Cannot use" in err
    assert (config_loader.config_dir() / "workspace_dir").read_text(encoding="utf-8").strip() == (
        str(configured)
    )


def test_an_unreadable_config_names_both_steps_it_stops(capsys):
    """The default agent and the timezone both need `config.json`; neither writes over it."""
    config_loader.config_dir()  # the home this writes into: finding where the file is makes nothing
    config_file = config_loader.config_path()
    config_file.write_text("not json {{{", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        cli_setup._setup()

    assert exit_info.value.code == 1
    out, err = capsys.readouterr()
    assert "Done!" not in out + err
    summary = err[err.index("Setup did not finish: 2 steps failed.") :]
    assert "❌ Default agent: could not add the default agent" in summary
    assert "Run it again: personalclaw setup --agent-only" in summary
    assert "❌ Timezone: could not read" in summary
    assert config_file.read_text(encoding="utf-8") == "not json {{{"


def test_agent_only_names_the_failed_step_instead_of_done(monkeypatch, capsys, tmp_path):
    """`personalclaw update` runs `setup --agent-only` and trusts its exit status."""
    _the_skills_folder_is_read_only(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        cli_setup._setup(agent_only=True)

    assert exit_info.value.code == 1
    out, err = capsys.readouterr()
    assert "Done!" not in out + err
    assert "❌ Orchestrator skill: OSError: [Errno 30] Read-only file system" in err
    assert "Run it again: personalclaw setup --agent-only" in err


def test_a_second_run_keeps_what_the_first_saved_and_finishes(monkeypatch, capsys, tmp_path):
    home = config_loader.config_dir()
    chosen = tmp_path / "my-workspace"
    _answer(monkeypatch, {"Workspace path": str(chosen)})
    saving = cli_setup.mutate_config
    _refuse_config_writes(monkeypatch)

    with pytest.raises(SystemExit) as first:
        cli_setup._setup()

    assert first.value.code == 1
    assert "❌ Timezone: could not save the timezone" in capsys.readouterr().err
    # What worked is saved: the typed folder, the agent config, the default agent.
    agent_file = home / "agents" / "personalclaw.json"
    assert (home / "workspace_dir").read_text(encoding="utf-8").strip() == str(chosen)
    assert agent_file.is_file()
    config = json.loads(config_loader.config_path().read_text(encoding="utf-8"))
    assert config["default_agent"] == "default" and "timezone" not in config

    # The retry, pressing Enter at every prompt, with the config writable again.
    monkeypatch.setattr(cli_setup, "mutate_config", saving)
    _answer(monkeypatch, {})
    cli_setup._setup()

    out, err = capsys.readouterr()
    assert "Done! Try: personalclaw doctor && personalclaw gateway" in out
    assert "did not finish" not in err and "❌" not in err
    assert (home / "workspace_dir").read_text(encoding="utf-8").strip() == str(chosen)
    config = json.loads(config_loader.config_path().read_text(encoding="utf-8"))
    assert config["timezone"] == "Europe/Berlin"
    assert list(config["agents"]) == ["default"]

    # And once everything is done, another run changes nothing.
    kept = [config_loader.config_path(), home / "workspace_dir", agent_file]
    before = [path.read_bytes() for path in kept]
    cli_setup._setup()
    assert "Done!" in capsys.readouterr().out
    assert [path.read_bytes() for path in kept] == before


def test_a_failure_on_stderr_lands_after_what_was_printed_before_it(tmp_path):
    """`personalclaw setup 2>&1 | tee setup.log` — both streams in one pipe.

    A piped stdout is buffered in blocks, and stderr is not, so every failure landed ahead of
    the output it follows: the log opened on the refusal and the header came after it. `main`
    now puts stdout on line buffering. The refused `--credential` is the probe: its header is
    on stdout and its refusal on stderr, and it stores nothing.
    """
    user_home = tmp_path / "user"
    user_home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_")}
    env.update(
        PERSONALCLAW_HOME=str(tmp_path / "home"),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        HOME=str(user_home),
        PYTHONIOENCODING="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "personalclaw", "setup", "--credential", "1BAD=x"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout
    log = proc.stdout
    assert log.index("PersonalClaw Setup") < log.index("❌ --credential '1BAD'"), log
