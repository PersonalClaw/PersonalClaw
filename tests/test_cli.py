"""Tests for CLI module."""

import argparse
import json
import os
import urllib.error
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fakes import gateway_stand_in, released_port

from personalclaw.cli_commands import _cron, _security
from personalclaw.cli_doctor import _doctor


def _set_up_agent_config(tmp_path):
    """An agent config whose PersonalClaw server starts a program that is there (never run)."""
    command = tmp_path / "bin" / "personalclaw"
    command.parent.mkdir(exist_ok=True)
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o755)
    (tmp_path / "personalclaw.json").write_text(
        json.dumps(
            {
                "tools": ["@personalclaw-core"],
                "allowedTools": ["@personalclaw-core"],
                "mcpServers": {
                    "personalclaw-core": {"command": str(command), "args": ["mcp-core"]}
                },
            }
        )
    )


class TestDoctor:
    def test_doctor_with_agent(self, tmp_path):
        _set_up_agent_config(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        with (
            patch(
                "personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"
            ),
            patch("personalclaw.cli_doctor.agents_dir", lambda: tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
        ):
            _doctor()

    def test_doctor_without_agent(self):
        with (
            patch("personalclaw.cli_doctor.shutil.which", return_value=None),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
        ):
            try:
                _doctor()
            except SystemExit as e:
                assert e.code == 1


class _TtyStdin:
    """A stdin that claims to be a terminal.

    `cli_setup._ask` takes the non-interactive door when `sys.stdin.isatty()` is False
    (`setup` must not die with an EOFError traceback when it cannot prompt).
    pytest's stdin is not a tty, so a test that patches `builtins.input` to drive a prompt
    must also say it is on a terminal — otherwise the guard returns "" and the patched
    answer is never consumed, which reads as the feature being broken.
    """

    def isatty(self) -> bool:
        return True


class TestSetupWorkspaceDir:
    """Tests for _setup_workspace_dir prompt default and label logic."""

    @pytest.fixture(autouse=True)
    def _no_workspace_from_the_environment(self, monkeypatch):
        monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)

    def test_uses_saved_path_as_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.cli_setup.sys.stdin", _TtyStdin())
        ws_file = tmp_path / "workspace_dir"
        ws_file.write_text("/custom/workspace\n")
        custom_dir = tmp_path / "custom"
        monkeypatch.setattr("personalclaw.cli_setup._workspace_dir_file", lambda: ws_file)
        with patch("builtins.input", return_value=str(custom_dir)) as mock_input:
            from personalclaw.cli_setup import _setup_workspace_dir

            _setup_workspace_dir()
        prompt = mock_input.call_args[0][0]
        assert "/custom/workspace" in prompt

    def test_shows_configured_label_when_saved(self, tmp_path, monkeypatch, capsys):
        ws_file = tmp_path / "workspace_dir"
        ws_file.write_text("/custom/workspace\n")
        custom_dir = tmp_path / "custom"
        monkeypatch.setattr("personalclaw.cli_setup._workspace_dir_file", lambda: ws_file)
        with patch("builtins.input", return_value=str(custom_dir)):
            from personalclaw.cli_setup import _setup_workspace_dir

            _setup_workspace_dir()
        output = capsys.readouterr().out
        assert "Configured:" in output

    def test_shows_default_label_when_no_saved(self, tmp_path, monkeypatch, capsys):
        ws_file = tmp_path / "no_such_file"
        custom_dir = tmp_path / "ws"
        monkeypatch.setattr("personalclaw.cli_setup._workspace_dir_file", lambda: ws_file)
        with patch("builtins.input", return_value=str(custom_dir)):
            from personalclaw.cli_setup import _setup_workspace_dir

            _setup_workspace_dir()
        output = capsys.readouterr().out
        assert "Default:" in output

    def test_a_workspace_the_environment_sets_is_shown_not_asked(
        self, tmp_path, monkeypatch, capsys
    ):
        """``PERSONALCLAW_WORKSPACE`` wins over the folder this step saves, so a folder typed here
        would be saved, reported as configured, and never used."""
        from personalclaw.cli_setup import _setup_workspace_dir
        from personalclaw.config.loader import workspace_root

        monkeypatch.setattr("personalclaw.cli_setup.sys.stdin", _TtyStdin())
        ws_file = tmp_path / "workspace_dir"
        monkeypatch.setattr("personalclaw.cli_setup._workspace_dir_file", lambda: ws_file)
        from_env = tmp_path / "from-env"
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(from_env))
        with patch("builtins.input", return_value=str(tmp_path / "typed")) as mock_input:
            assert _setup_workspace_dir() is None
        mock_input.assert_not_called()
        output = capsys.readouterr().out
        assert f"Set by PERSONALCLAW_WORKSPACE: {from_env}" in output
        assert f"✅ Workspace: {from_env}" in output
        assert not ws_file.exists(), "no folder saved that sessions would not run in"
        assert from_env.is_dir() and workspace_root() == from_env

    def test_a_workspace_the_environment_sets_that_cannot_be_made_fails_the_step(
        self, tmp_path, monkeypatch, capsys
    ):
        from personalclaw.cli_setup import _setup_workspace_dir

        blocker = tmp_path / "a-file"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(blocker / "workspace"))
        with patch("builtins.input", side_effect=AssertionError("the step asked")):
            reason = _setup_workspace_dir()
        assert reason and "from PERSONALCLAW_WORKSPACE" in reason
        assert "from PERSONALCLAW_WORKSPACE" in capsys.readouterr().err


class TestSetupAgentRuntime:
    """``setup --provider`` sets ``agent.provider``, the runtime an agent that names none runs on.

    It used to save any name: a model provider's name was saved, reported as set, and read as
    ``native`` everywhere, which changed nothing.
    """

    @pytest.fixture
    def config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        path = tmp_path / "config.json"
        path.write_text("{}", encoding="utf-8")
        return path

    @pytest.mark.parametrize("runtime", ["native", "acp", "acp:claude-code"])
    def test_a_runtime_is_saved(self, config, runtime, capsys):
        from personalclaw.cli_setup import _setup_noninteractive

        assert _setup_noninteractive(provider=runtime) is True
        assert json.loads(config.read_text(encoding="utf-8"))["agent"]["provider"] == runtime
        assert f"✅ Agent runtime set: {runtime}" in capsys.readouterr().out

    @pytest.mark.parametrize("value", ["openai", "Native", "acp:", "acp: "])
    def test_anything_else_is_refused_and_not_saved(self, config, value, capsys):
        from personalclaw.cli_setup import _setup_noninteractive

        assert _setup_noninteractive(provider=value) is False
        assert json.loads(config.read_text(encoding="utf-8")) == {}
        out, err = capsys.readouterr()
        assert f"--provider {value!r} is not an agent runtime" in err
        assert "✅" not in out


class TestCronCli:
    """The `cron` CLI writes the unified TRIGGER STORE.

    🔴 Every test here used to `patch("personalclaw.cli_commands.ScheduleService")` and assert the
    `add_job(...)` CALL SHAPE. They passed the whole time a CLI-created cron DID NOT FIRE: the write
    went to `crons.json`, which the clock engine never reads, so the job stayed inert until the user
    restarted the gateway. A mock-shape assertion cannot see that — it proves only which
    function was called, not that anything got scheduled. These drive the store and assert the row.

    They also had no `config_dir` isolation at all (the mock was the only thing between them and
    the user's real home). The store is a real file, so the fixture now redirects it.

    `yes=True` is the owner's `--yes`: an agent job, and `--approval-mode auto`, need it, and what
    the command says without it is `test_a_grant_is_for_the_action_the_owner_allowed`'s.
    """

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.cli_commands.config_dir", lambda: tmp_path)
        monkeypatch.setattr("personalclaw.cli_commands.sel", MagicMock())
        return tmp_path

    @pytest.fixture
    def fakechat(self, monkeypatch):
        """An installed chat channel. `--channel` names the channel results go to, and the CLI
        builds the installed channels to ask whether it can take the id."""
        from personalclaw.channel_transports.base import ChannelTransportProvider

        class _Chat(ChannelTransportProvider):
            name = property(lambda self: "fakechat")
            display_name = property(lambda self: "FakeChat")

            async def connect(self) -> bool:
                return True

            async def disconnect(self) -> None:
                return None

            async def send(self, message: object) -> bool:
                return True

        monkeypatch.setattr(
            "personalclaw.providers.loader.build_channel_transports", lambda: [_Chat()]
        )
        return "fakechat"

    def _store(self, tmp_path):
        from personalclaw.triggers.store import TriggerStore

        return TriggerStore(base_dir=tmp_path)

    def _only(self, tmp_path):
        rows = self._store(tmp_path).load()
        assert len(rows) == 1, [r.trigger.id for r in rows]
        return rows[0]

    def test_cron_add_writes_an_armed_store_trigger(self, tmp_path):
        """🔴 THE POINT OF THE SESSION: a created cron must be able to fire without a restart.
        `service.due_ids` only surfaces rows carrying a `next_fire_at`."""
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel=None,
                approval_mode="",
                yes=True,
            )
        )
        row = self._only(tmp_path)
        assert row.ok, row.errors
        assert row.trigger.enabled
        assert row.trigger.next_fire_at
        assert row.trigger.spec == {"kind": "interval", "interval_secs": 300}
        assert not (tmp_path / "crons.json").exists(), "nothing may be written to the legacy file"

    def test_cron_add_with_channel(self, tmp_path, fakechat):
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel="fakechat:C0EXAMPLE01",
                approval_mode="",
                yes=True,
            )
        )
        # `delivery` is the store's spelling of the legacy `channel=` kwarg.
        assert self._only(tmp_path).trigger.delivery == "channel:fakechat:C0EXAMPLE01"

    def test_cron_add_with_cron_expr(self, tmp_path, fakechat):
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=None,
                cron_expr="0 9 * * MON-FRI",
                channel="fakechat",
                approval_mode="",
                yes=True,
            )
        )
        row = self._only(tmp_path)
        assert row.trigger.spec == {"kind": "cron", "expr": "0 9 * * MON-FRI"}
        assert row.trigger.delivery == "channel:fakechat"
        assert row.trigger.next_fire_at

    def test_cron_add_with_approval_mode(self, tmp_path):
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel=None,
                approval_mode="auto",
                yes=True,
            )
        )
        inline = (self._only(tmp_path).trigger.workflow or {}).get("inline") or {}
        config = inline.get("config") or {}
        assert config.get("approval_mode") == "auto"
        # `task_template`, NOT `message` — the key `invoke-agent` actually reads.
        assert config.get("task_template") == "check"

    def test_cron_add_is_a_user_creation_not_an_agent_one(self, tmp_path):
        """`created_by="user"`: the agent cap (decision 5d) bounds what the ASSISTANT creates
        unprompted. A human typing the command is the user acting directly, and capping their own
        CLI at the agent limit would aim the rule at the wrong party."""
        _cron(
            argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel=None,
                approval_mode="",
                yes=True,
            )
        )
        assert self._only(tmp_path).trigger.created_by == "user"

    def test_cron_add_without_a_cadence_is_refused(self, tmp_path, capsys):
        """A usage error, so the parser's: the usage on stderr and exit 2, before any handler."""
        from personalclaw.cli import build_parser

        with pytest.raises(SystemExit) as exited:
            build_parser().parse_args(["cron", "add", "ops", "check"])
        assert exited.value.code == 2
        err = capsys.readouterr().err
        assert "usage: personalclaw cron add" in err
        assert "one of the arguments --every --cron is required" in err
        assert self._store(tmp_path).load() == []

    def _seed(self, tmp_path, **over):
        from personalclaw.triggers.models import Trigger

        store = self._store(tmp_path)
        trigger = Trigger(
            id="clock:ops",
            name="ops",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 300},
            workflow={
                "inline": {
                    "provider": "invoke-agent",
                    "config": {"task_template": "check", "agent": "helper", "model": "gpt"},
                }
            },
            # Granted, as `cron add` (`tools.create`) freezes it: resuming an ungranted row is
            # refused (`triggers.grants`).
            capabilities={"providers": ["invoke-agent"]},
        )
        for key, value in over.items():
            setattr(trigger, key, value)
        store.upsert(trigger)
        return store

    def test_cron_update_approval_mode(self, tmp_path):
        self._seed(tmp_path)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel=None,
                approval_mode="auto",
                yes=True,
            )
        )
        config = ((self._only(tmp_path).trigger.workflow or {})["inline"]).get("config") or {}
        assert config.get("approval_mode") == "auto"
        # 🔴 The agent + model the user set at creation must SURVIVE an unrelated edit — the action
        # is read-modify-written, not replaced.
        assert config.get("agent") == "helper"
        assert config.get("model") == "gpt"

    def test_cron_update_default_approval_mode_clears_it(self, tmp_path):
        self._seed(tmp_path)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel=None,
                approval_mode="default",
            )
        )
        config = ((self._only(tmp_path).trigger.workflow or {})["inline"]).get("config") or {}
        # A cleared setting is removed, not stored empty (`triggers.action_edit`).
        assert "approval_mode" not in config

    def test_cron_update_cadence_re_arms(self, tmp_path, monkeypatch):
        """🔴 The cadence changed and the list showed the new time, but
        `next_fire_at` still held the OLD one — so the job would fire on the schedule the user had
        just replaced. `next_fire_at` is engine state the patch allowlist refuses, so the re-arm
        is a separate clear-then-arm."""
        # Host zone pinned: the assertion reads the re-armed instant as UTC (`07:30:00+00:00` for
        # `30 7 * * *`), and an absent `spec.timezone` resolves the machine's zone now (#2520).
        # This test is about the RE-ARM happening at all.
        monkeypatch.setenv("TZ", "UTC")
        store = self._seed(tmp_path, next_fire_at="2026-01-01T09:00:00+00:00")
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr="30 7 * * *",
                channel=None,
                approval_mode=None,
            )
        )
        trigger = store.get("clock:ops").trigger
        assert trigger.spec["expr"] == "30 7 * * *"
        assert trigger.next_fire_at.endswith("07:30:00+00:00")

    def test_cron_update_cadence_preserves_the_quietly_losable_keys(self, tmp_path):
        """`timezone`/`skip_dates`/`strict` survive a cadence change — the contract. A user
        changing `0 9 * * *` to `0 10 * * *` must not lose their holidays."""
        store = self._seed(tmp_path)
        trigger = store.get("clock:ops").trigger
        trigger.spec = {
            **trigger.spec,
            "timezone": "America/New_York",
            "skip_dates": ["2026-12-25"],
        }
        store.upsert(trigger)
        _cron(
            argparse.Namespace(
                cron_action="update",
                job_id="clock:ops",
                name=None,
                message=None,
                every_secs=None,
                cron_expr="0 10 * * *",
                channel=None,
                approval_mode=None,
            )
        )
        spec = store.get("clock:ops").trigger.spec
        assert spec["expr"] == "0 10 * * *"
        assert spec["timezone"] == "America/New_York"
        assert spec["skip_dates"] == ["2026-12-25"]

    def test_cron_update_whitespace_channel_skipped(self, tmp_path, capsys):
        self._seed(tmp_path)
        with pytest.raises(SystemExit) as exited:
            _cron(
                argparse.Namespace(
                    cron_action="update",
                    job_id="clock:ops",
                    name=None,
                    message=None,
                    every_secs=None,
                    cron_expr=None,
                    channel="   ",
                    approval_mode=None,
                )
            )
        assert exited.value.code == 2  # a usage error: nothing to update was given
        assert "Provide at least one field to update" in capsys.readouterr().err

    def test_cron_update_every_and_cron_exclusive(self, tmp_path, capsys):
        """A usage error, so the parser's: exit 2 before the handler can write anything."""
        from personalclaw.cli import build_parser

        self._seed(tmp_path)
        with pytest.raises(SystemExit) as exited:
            build_parser().parse_args(
                ["cron", "update", "clock:ops", "--every", "600", "--cron", "0 9 * * *"]
            )
        assert exited.value.code == 2
        assert "argument --cron: not allowed with argument --every" in capsys.readouterr().err
        # And nothing may have been written on the way to that refusal.
        assert self._only(tmp_path).trigger.spec == {"kind": "interval", "interval_secs": 300}

    def test_cron_update_not_found(self, tmp_path, capsys, fakechat):
        with pytest.raises(SystemExit) as exited:
            _cron(
                argparse.Namespace(
                    cron_action="update",
                    job_id="nope",
                    name=None,
                    message=None,
                    every_secs=None,
                    cron_expr=None,
                    channel="fakechat:C0EXAMPLE01",
                    approval_mode=None,
                )
            )
        assert exited.value.code == 1
        assert "Job not found: nope" in capsys.readouterr().err

    def test_cron_pause_and_resume(self, tmp_path):
        store = self._seed(tmp_path, enabled=True)
        _cron(argparse.Namespace(cron_action="pause", job_id="clock:ops"))
        assert store.get("clock:ops").trigger.enabled is False
        _cron(argparse.Namespace(cron_action="resume", job_id="clock:ops"))
        assert store.get("clock:ops").trigger.enabled is True

    def test_resuming_a_broken_trigger_names_the_parse_error(self, tmp_path, capsys):
        """`set_paused` REFUSES to enable a row that failed to parse and says why, which beats the
        legacy "Job not found" — the row does exist, so that message was wrong as well as unhelpful.
        """
        self._seed(tmp_path, spec={}, enabled=False)  # no spec.kind → invalid clock row
        with pytest.raises(SystemExit) as exited:
            _cron(argparse.Namespace(cron_action="resume", job_id="clock:ops"))
        assert exited.value.code == 1
        assert "parse error" in capsys.readouterr().err
        assert self._store(tmp_path).get("clock:ops").trigger.enabled is False

    def test_cron_remove_deletes_the_row(self, tmp_path):
        store = self._seed(tmp_path)
        _cron(argparse.Namespace(cron_action="remove", job_id="clock:ops"))
        assert store.get("clock:ops") is None

    def test_cron_remove_not_found(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exited:
            _cron(argparse.Namespace(cron_action="remove", job_id="nope"))
        assert exited.value.code == 1
        assert "Job not found: nope" in capsys.readouterr().err

    def test_cron_list_renders_the_stores_rows(self, tmp_path, capsys):
        self._seed(tmp_path)
        _cron(argparse.Namespace(cron_action="list"))
        out = capsys.readouterr().out
        assert "clock:ops" in out
        # 🔴 The message came out BLANK first: read via the shared projection, because
        # `invoke-agent`'s key is `task_template` and `run-prompt`/`notify` differ again.
        assert "check" in out

    def test_cron_list_shows_a_broken_row_rather_than_hiding_it(self, tmp_path, capsys):
        """The legacy list could not represent a broken row at all, and silently omitting a trigger
        the user created is how "where did my automation go" happens."""
        self._seed(tmp_path, spec={})
        _cron(argparse.Namespace(cron_action="list"))
        out = capsys.readouterr().out
        assert "clock:ops" in out
        assert "⚠️" in out

    def test_cron_list_when_empty(self, tmp_path, capsys):
        _cron(argparse.Namespace(cron_action="list"))
        assert "No cron jobs." in capsys.readouterr().out

    def test_the_cli_no_longer_touches_the_legacy_service(self):
        """🔴 The clean break, pinned at the source: a re-added `ScheduleService` write here would
        silently stop firing again, and the symptom (a cron that runs only after a restart) is
        exactly the one that took this long to notice."""
        import inspect

        from personalclaw import cli_commands

        assert "ScheduleService" not in inspect.getsource(cli_commands._cron)


class TestSetupTimezone:
    def test_auto_detect_from_tz_env(self, monkeypatch):
        """TZ env var is checked before /etc/localtime."""
        from personalclaw.cli_setup import _detect_system_timezone

        monkeypatch.setenv("TZ", "Europe/London")
        assert _detect_system_timezone() == "Europe/London"

    def test_auto_detect_tz_env_with_colon(self, monkeypatch):
        """TZ env var with glibc colon prefix is handled."""
        from personalclaw.cli_setup import _detect_system_timezone

        monkeypatch.setenv("TZ", ":America/Chicago")
        assert _detect_system_timezone() == "America/Chicago"

    def test_auto_detect_from_symlink(self, tmp_path, monkeypatch):
        """When /etc/localtime is a symlink, timezone is auto-detected."""
        monkeypatch.setattr("personalclaw.cli_setup.sys.stdin", _TtyStdin())
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)

        from personalclaw.cli_setup import _setup_timezone

        with patch("builtins.input", return_value="") as mock_input:
            with patch(
                "personalclaw.cli_setup._detect_system_timezone",
                return_value="America/Los_Angeles",
            ):
                _setup_timezone()

        prompt = mock_input.call_args[0][0]
        assert "America/Los_Angeles" in prompt
        data = json.loads(cfg_file.read_text())
        assert data["timezone"] == "America/Los_Angeles"

    def test_manual_entry(self, tmp_path, monkeypatch):
        """When no auto-detect, user types timezone manually."""
        monkeypatch.setattr("personalclaw.cli_setup.sys.stdin", _TtyStdin())
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)

        from personalclaw.cli_setup import _setup_timezone

        with patch("builtins.input", return_value="America/New_York"):
            with patch("personalclaw.cli_setup._detect_system_timezone", return_value=""):
                _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert data["timezone"] == "America/New_York"

    def test_skip_on_empty_input(self, tmp_path, monkeypatch):
        """Empty input skips timezone setup."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)

        from personalclaw.cli_setup import _setup_timezone

        with patch("builtins.input", return_value=""):
            with patch("personalclaw.cli_setup._detect_system_timezone", return_value=""):
                _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert "timezone" not in data

    def test_invalid_timezone_rejected(self, tmp_path, monkeypatch, capsys):
        """Invalid timezone is rejected, not saved."""
        monkeypatch.setattr("personalclaw.cli_setup.sys.stdin", _TtyStdin())
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)

        from personalclaw.cli_setup import _setup_timezone

        with patch("builtins.input", return_value="Invalid/Timezone"):
            with patch("personalclaw.cli_setup._detect_system_timezone", return_value=""):
                reason = _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert "timezone" not in data
        # Three answers that are not zones fail the step, so `setup` cannot end on "Done!".
        assert reason == "no IANA timezone in 3 tries, so it was not changed"
        assert "Unknown timezone" in capsys.readouterr().err

    def test_missing_database_is_not_called_an_unknown_timezone(
        self, tmp_path, monkeypatch, capsys
    ):
        """A broken install is actionable once; re-prompting implies the user's name is wrong."""
        from zoneinfo import ZoneInfoNotFoundError

        from personalclaw import timezones as tzmod
        from personalclaw.cli_setup import _setup_timezone

        monkeypatch.setattr("personalclaw.cli_setup.sys.stdin", _TtyStdin())
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)
        monkeypatch.setattr(
            tzmod,
            "ZoneInfo",
            lambda key: (_ for _ in ()).throw(
                ZoneInfoNotFoundError(f"No time zone found with key {key}")
            ),
        )

        with patch("builtins.input", return_value="America/Los_Angeles"):
            with patch("personalclaw.cli_setup._detect_system_timezone", return_value=""):
                reason = _setup_timezone()

        assert "timezone" not in json.loads(cfg_file.read_text())
        out, err = capsys.readouterr()
        assert reason and "tzdata" in reason
        assert "Timezone database unavailable" in err
        assert "Unknown timezone" not in out + err
        assert "tzdata" in err

    def test_keeps_existing_on_enter(self, tmp_path, monkeypatch):
        """Re-running setup with existing timezone keeps it on Enter."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"timezone": "America/Chicago"}))
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)

        from personalclaw.cli_setup import _setup_timezone

        with patch("builtins.input", return_value=""):
            _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert data["timezone"] == "America/Chicago"

    def test_corrupted_config_not_overwritten(self, tmp_path, monkeypatch, capsys):
        """Corrupted config file is not overwritten."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("not json {{{")
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)

        from personalclaw.cli_setup import _setup_timezone

        assert _setup_timezone()

        # File should be unchanged
        assert cfg_file.read_text() == "not json {{{"
        assert "Could not read" in capsys.readouterr().err


@pytest.fixture
def gateway_home(tmp_path, monkeypatch, unset_env):
    """A home, named by ``PERSONALCLAW_HOME``, holding the local secret its gateway writes as it
    starts, with no port named in the environment: what ``logout`` and ``status`` run for."""
    unset_env("PERSONALCLAW_PORT", "HTTP_PROXY", "http_proxy")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    (home / ".local_secret").write_text("test-secret")
    return home


@contextmanager
def _running(home, routes):
    """This home's gateway, on a loopback port of its own, answering *routes*, and the record it
    keeps of that port."""
    with gateway_stand_in(home, routes=routes) as gateway:
        gateway.record()
        yield gateway


class TestLogout:
    """Tests for _logout CLI function, against this home's gateway."""

    def test_logout_success(self, gateway_home, capsys):
        """Successful logout prints success message, having sent the home's secret."""
        from personalclaw.cli_server import _logout

        with _running(gateway_home, {("POST", "/api/logout"): (200, {"ok": True})}) as gateway:
            _logout(None)
        assert "All dashboard sessions revoked" in capsys.readouterr().out
        (sent,) = gateway.asked("/api/logout")
        assert sent.headers["x-local-secret"] == "test-secret"

    def test_logout_gateway_not_running(self, gateway_home, capsys):
        """No gateway of this home running: refused, saying so."""
        from personalclaw.cli_server import _logout

        with pytest.raises(SystemExit) as exited:
            _logout(None)
        assert exited.value.code == 1
        assert "No gateway is running for this home" in capsys.readouterr().err

    def test_logout_http_error(self, gateway_home, capsys):
        """HTTP error from gateway is handled."""
        from personalclaw.cli_server import _logout

        with _running(gateway_home, {("POST", "/api/logout"): (403, {"error": "invalid secret"})}):
            with pytest.raises(SystemExit) as exited:
                _logout(None)
        assert exited.value.code == 1
        assert "Failed to revoke sessions: HTTP 403" in capsys.readouterr().err

    def test_logout_connection_error(self, gateway_home, capsys):
        """Nothing listening where the home's gateway said it listens: it is not running."""
        from personalclaw.cli_server import _logout

        (gateway_home / "gateway.runtime.json").write_text(
            json.dumps({"port": released_port(), "pid": os.getpid()})
        )
        with pytest.raises(SystemExit) as exited:
            _logout(None)
        assert exited.value.code == 1
        assert "No gateway is running for this home" in capsys.readouterr().err

    def test_logout_error_response(self, gateway_home, capsys):
        """Error response from gateway is handled."""
        from personalclaw.cli_server import _logout

        answer = {"ok": False, "error": "test error"}
        with _running(gateway_home, {("POST", "/api/logout"): (200, answer)}):
            with pytest.raises(SystemExit) as exited:
                _logout(None)
        assert exited.value.code == 1
        assert "test error" in capsys.readouterr().err


class TestStatus:
    """Tests for _status() against this home's gateway."""

    def _make_args(self, port=None):
        return argparse.Namespace(port=port)

    def test_status_auth_required(self, gateway_home, capsys):
        """401/403 should report gateway as running with token auth."""
        from personalclaw.cli_server import _status

        with _running(gateway_home, {("GET", "/api/status"): (403, {"error": "sign in"})}):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "token auth" in out

    def test_status_other_http_error(self, gateway_home, capsys):
        """Non-auth HTTP errors should report gateway as running with code."""
        from personalclaw.cli_server import _status

        with _running(gateway_home, {("GET", "/api/status"): (500, {"error": "boom"})}):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "HTTP 500" in out

    def test_status_connection_refused(self, gateway_home, capsys):
        """No gateway of this home running is reported, with the command that starts one."""
        from personalclaw.cli_server import _status

        _status(self._make_args())
        out = capsys.readouterr().out
        assert "No gateway is running for this home" in out
        assert "Start it with: personalclaw" in out

    def test_status_success(self, gateway_home, capsys):
        """200 OK should display stats.

        The payload is the shape `/api/status` really emits — `cron` is a BLOCK and there is
        no `crons`/`messages`/`tool_calls` key (#2903). The previous fixture invented all
        three, so it passed while the surface printed a hardwired `0` for each. The
        wire contract itself is pinned against the live endpoint in
        `test_cli_status_wire_contract.py`; this stays a plain reader unit test.
        """
        from personalclaw.cli_server import _status

        payload = {
            "uptime": "1h 0m",
            "sessions": 2,
            "subagents": 0,
            "cron": {"total": 1, "enabled": 1, "broken": 0},
            "stats": {"total_turns": 7},
            "lessons": 3,
        }
        with _running(gateway_home, {("GET", "/api/status"): (200, payload)}):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "1h 0m" in out
        assert "Sessions" in out or "sessions" in out.lower()
        assert "Cron jobs:   1" in out
        assert "Turns:       7" in out

    def test_status_unexpected_exception(self, gateway_home, capsys):
        """An answer that is not JSON reports the gateway as running with an unexpected
        response."""
        from aiohttp import web

        from personalclaw.cli_server import _status

        async def not_json(_request):
            return web.Response(text="not json")

        with _running(gateway_home, {("GET", "/api/status"): not_json}):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "unexpected response" in out


class TestDoctorProjectDir:
    """The doctor's project row is the project dir the CLI exported, never a saved path."""

    def test_a_saved_project_dir_file_is_not_the_project_dir(self, tmp_path, capsys):
        """Earlier versions of `personalclaw setup` saved the checkout the working directory was
        in, and doctor reported it as this install's project dir from then on, wherever
        PersonalClaw ran from. Nothing reads that file now, so doctor neither reports it nor
        calls it stale."""
        checkout = tmp_path / "PersonalClaw"
        checkout.mkdir()
        (tmp_path / "project_dir").write_text(f"{checkout}\n")
        agent_file = tmp_path / "personalclaw.json"
        agent_data = {
            "tools": ["@personalclaw-core", "@personalclaw-schedule"],
            "allowedTools": ["@personalclaw-core", "@personalclaw-schedule"],
            "mcpServers": {
                "personalclaw-core": {
                    "command": "/usr/local/bin/personalclaw",
                    "args": ["mcp-core"],
                },
                "personalclaw-schedule": {
                    "command": "/usr/local/bin/personalclaw",
                    "args": ["mcp-schedule"],
                },
            },
        }
        agent_file.write_text(json.dumps(agent_data))
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        with (
            patch(
                "personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"
            ),
            patch("personalclaw.cli_doctor.agents_dir", lambda: tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen"),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.cli_doctor.config_dir", return_value=tmp_path),
            patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": ""}, clear=False),
        ):
            try:
                _doctor()
            except SystemExit:
                pass  # other rows may fail on this machine; the project row is the point
        out = capsys.readouterr().out
        assert f"project dir: ✅ {checkout}" not in out
        assert "project dir: ⏹  not set" in out
        assert "project dir: ❌ stale" not in out


class TestDoctorStt:
    """Tests for doctor Speech-to-Text section.

    STT now resolves through the typed registry: enabled lives in
    use_case_settings/stt.json (read via load_use_case_settings) and the
    active model in active_models.json (read via active_stt).
    """

    def _agent_file(self, tmp_path):
        _set_up_agent_config(tmp_path)

    def test_doctor_stt_enabled_with_model(self, tmp_path, capsys):
        self._agent_file(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        provider = MagicMock()
        provider.name = "faster_whisper"
        with (
            patch(
                "personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"
            ),
            patch("personalclaw.cli_doctor.agents_dir", lambda: tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.ffmpeg_binary.find_ffmpeg", return_value="/usr/local/bin/ffmpeg"),
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch(
                "personalclaw.stt.registry.active_stt",
                return_value=(provider, "turbo"),
            ),
        ):
            _doctor()
        out = capsys.readouterr().out
        assert "Speech-to-Text" in out
        assert "model:" in out
        assert "faster_whisper:turbo" in out
        assert "ffmpeg:      ✅" in out

    def test_doctor_stt_enabled_no_model(self, tmp_path, capsys):
        self._agent_file(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        with (
            patch(
                "personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"
            ),
            patch("personalclaw.cli_doctor.agents_dir", lambda: tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.ffmpeg_binary.find_ffmpeg", return_value="/usr/local/bin/ffmpeg"),
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch(
                "personalclaw.stt.registry.active_stt",
                return_value=None,
            ),
        ):
            # STT enabled but no model bound is NOT a failure now: media backends
            # (faster-whisper app, remote providers) are opt-in, so an unconfigured
            # STT is an informational state — the doctor reports it and exits 0.
            _doctor()
        out = capsys.readouterr().out
        assert "Speech-to-Text" in out
        assert "no STT model configured" in out

    def test_doctor_stt_disabled(self, tmp_path, capsys):
        self._agent_file(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        with (
            patch(
                "personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"
            ),
            patch("personalclaw.cli_doctor.agents_dir", lambda: tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.ffmpeg_binary.find_ffmpeg", return_value="/usr/local/bin/ffmpeg"),
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": False},
            ),
            patch(
                "personalclaw.stt.registry.active_stt",
                return_value=None,
            ),
        ):
            _doctor()
        out = capsys.readouterr().out
        assert "Speech-to-Text" in out
        assert "disabled" in out


class TestConfigDirOverride:
    """Tests that CLI functions respect PERSONALCLAW_HOME env var via config_dir()."""

    def test_logout_reads_secret_from_config_dir(self, gateway_home):
        """_logout sends the .local_secret of the home it runs for, not ~/.personalclaw's."""
        from personalclaw.cli_server import _logout

        (gateway_home / ".local_secret").write_text("this-homes-own-secret")
        with _running(gateway_home, {("POST", "/api/logout"): (200, {"ok": True})}) as gateway:
            _logout(None)
        (sent,) = gateway.asked("/api/logout")
        assert sent.headers["x-local-secret"] == "this-homes-own-secret"

    # (removed) test_setup_slack_tokens_writes_to_config_dir — plan 32 moved
    # _setup_slack_tokens out of core into the slack-channel app's cli_setup.py
    # (behind the cli.setup manifest seam). The config-dir/.env write path is now
    # exercised app-side and by tests/test_app_cli.py's setup-runner tests.


class TestSecurityEventsRenderer:
    """#2948: ``personalclaw security events`` must not print an ``error:`` line
    for a successful outcome whose allow reason lives in ``metadata.reason``."""

    def test_prints_reason_not_error_for_a_success_row(self, tmp_path, monkeypatch, capsys):
        from personalclaw.sel import SecurityEventLog

        log = SecurityEventLog(base_dir=tmp_path)
        log.log_api_access(
            caller="local-net:127.0.0.1",
            operation="dashboard.token_auth",
            outcome="ok",
            metadata={"reason": "local-network bypass"},
        )
        monkeypatch.setattr("personalclaw.cli_commands.sel", lambda: log)

        _security(argparse.Namespace(sec_action="events", limit=20))

        out = capsys.readouterr().out
        assert "error:" not in out
        assert "reason: local-network bypass" in out

    def test_still_prints_error_for_a_genuine_failure(self, tmp_path, monkeypatch, capsys):
        from personalclaw.sel import SecurityEventLog

        log = SecurityEventLog(base_dir=tmp_path)
        log.log_api_access(
            caller="127.0.0.1",
            operation="dashboard.token_auth",
            outcome="denied",
            error="wrong secret",
        )
        monkeypatch.setattr("personalclaw.cli_commands.sel", lambda: log)

        _security(argparse.Namespace(sec_action="events", limit=20))

        out = capsys.readouterr().out
        assert "error: wrong secret" in out
        assert "reason:" not in out
