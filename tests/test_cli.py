"""Tests for CLI module."""

import argparse
import json
import subprocess
import sys
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.cli_commands import _cron
from personalclaw.cli_doctor import _doctor


class TestDoctor:
    def test_doctor_with_agent(self, tmp_path):
        agent_file = tmp_path / "personalclaw.json"
        agent_file.write_text("{}")
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        with (
            patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
            patch("personalclaw.cli_doctor.AGENTS_DIR", tmp_path),
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


class TestSetupWorkspaceDir:
    """Tests for _setup_workspace_dir prompt default and label logic."""

    def test_uses_saved_path_as_default(self, tmp_path, monkeypatch):
        ws_file = tmp_path / "workspace_dir"
        ws_file.write_text("/custom/workspace\n")
        custom_dir = tmp_path / "custom"
        monkeypatch.setattr(
            "personalclaw.cli_setup._workspace_dir_file", lambda: ws_file
        )
        with patch("builtins.input", return_value=str(custom_dir)) as mock_input:
            from personalclaw.cli_setup import _setup_workspace_dir

            _setup_workspace_dir()
        prompt = mock_input.call_args[0][0]
        assert "/custom/workspace" in prompt

    def test_shows_configured_label_when_saved(self, tmp_path, monkeypatch, capsys):
        ws_file = tmp_path / "workspace_dir"
        ws_file.write_text("/custom/workspace\n")
        custom_dir = tmp_path / "custom"
        monkeypatch.setattr(
            "personalclaw.cli_setup._workspace_dir_file", lambda: ws_file
        )
        with patch("builtins.input", return_value=str(custom_dir)):
            from personalclaw.cli_setup import _setup_workspace_dir

            _setup_workspace_dir()
        output = capsys.readouterr().out
        assert "Configured:" in output

    def test_shows_default_label_when_no_saved(self, tmp_path, monkeypatch, capsys):
        ws_file = tmp_path / "no_such_file"
        custom_dir = tmp_path / "ws"
        monkeypatch.setattr(
            "personalclaw.cli_setup._workspace_dir_file", lambda: ws_file
        )
        with patch("builtins.input", return_value=str(custom_dir)):
            from personalclaw.cli_setup import _setup_workspace_dir

            _setup_workspace_dir()
        output = capsys.readouterr().out
        assert "Default:" in output


class TestCronCli:
    def test_cron_add_with_channel(self, tmp_path):
        with patch("personalclaw.cli_commands.ScheduleService") as mock_svc_cls, \
             patch("personalclaw.cli_commands.sel"):
            mock_svc = mock_svc_cls.return_value
            mock_job = MagicMock()
            mock_job.id = "abc"
            mock_job.name = "test"
            mock_job.schedule.kind = "every"
            mock_job.schedule.every_secs = 300
            mock_job.schedule.cron_expr = None
            mock_job.schedule.at_ts = None
            mock_svc.add_job.return_value = mock_job
            args = argparse.Namespace(
                cron_action="add",
                name="ops",
                message="check",
                every=300,
                cron_expr=None,
                channel="C0AP77JJSN6",
                approval_mode="",
            )
            _cron(args)
            from personalclaw.schedule import make_agent_action
            mock_svc.add_job.assert_called_once_with(
                name="ops", action=make_agent_action(message="check", approval_mode=""),
                every_secs=300, channel="C0AP77JJSN6",
            )

    def test_cron_add_with_cron_expr_and_channel(self, tmp_path):
        with patch("personalclaw.cli_commands.ScheduleService") as mock_svc_cls, \
             patch("personalclaw.cli_commands.sel"):
            mock_svc = mock_svc_cls.return_value
            mock_job = MagicMock()
            mock_job.id = "def"
            mock_job.name = "daily"
            mock_job.schedule.kind = "cron"
            mock_job.schedule.every_secs = None
            mock_job.schedule.cron_expr = "0 9 * * 1-5"
            mock_job.schedule.at_ts = None
            mock_svc.add_job.return_value = mock_job
            args = argparse.Namespace(
                cron_action="add",
                name="daily",
                message="brief",
                every=None,
                cron_expr="0 9 * * 1-5",
                channel="C0APAPQ5GSY",
                approval_mode="",
            )
            _cron(args)
            from personalclaw.schedule import make_agent_action
            mock_svc.add_job.assert_called_once_with(
                name="daily", action=make_agent_action(message="brief", approval_mode=""),
                cron_expr="0 9 * * 1-5", channel="C0APAPQ5GSY",
            )

    def test_cron_add_with_approval_mode(self, tmp_path):
        with patch("personalclaw.cli_commands.ScheduleService") as mock_svc_cls, \
             patch("personalclaw.cli_commands.sel") as mock_sel:
            mock_svc = mock_svc_cls.return_value
            mock_job = MagicMock()
            mock_job.id = "ghi"
            mock_job.name = "auto-job"
            mock_job.schedule.kind = "every"
            mock_job.schedule.every_secs = 600
            mock_job.schedule.cron_expr = None
            mock_job.schedule.at_ts = None
            mock_svc.add_job.return_value = mock_job
            args = argparse.Namespace(
                cron_action="add",
                name="auto-job",
                message="run unattended",
                every=600,
                cron_expr=None,
                channel=None,
                approval_mode="auto",
            )
            _cron(args)
            from personalclaw.schedule import make_agent_action
            mock_svc.add_job.assert_called_once_with(
                name="auto-job", action=make_agent_action(message="run unattended", approval_mode="auto"),
                every_secs=600, channel=None,
            )
            mock_sel.return_value.log_api_access.assert_called_once_with(
                caller="cli", operation="cron.add",
                outcome="allowed", source="cli",
                resources="job_id=ghi approval_mode=auto",
            )

    def test_cron_update_approval_mode(self, tmp_path):
        with patch("personalclaw.cli_commands.ScheduleService") as mock_svc_cls, \
             patch("personalclaw.cli_commands.sel") as mock_sel:
            mock_svc = mock_svc_cls.return_value
            mock_job = MagicMock()
            mock_job.id = "abc123"
            mock_job.name = "existing"
            mock_svc.update_job.return_value = mock_job
            args = argparse.Namespace(
                cron_action="update",
                job_id="abc123",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel=None,
                approval_mode="auto",
            )
            _cron(args)
            mock_svc.update_job.assert_called_once_with("abc123", approval_mode="auto")
            mock_sel.return_value.log_api_access.assert_called_once_with(
                caller="cli", operation="cron.update",
                outcome="allowed", source="cli",
                resources="job_id=abc123 fields=approval_mode",
            )

    def test_cron_update_whitespace_channel_skipped(self, tmp_path, capsys):
        with patch("personalclaw.cli_commands.ScheduleService") as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.update_job.return_value = None
            args = argparse.Namespace(
                cron_action="update",
                job_id="job1",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel="   ",
                approval_mode=None,
            )
            _cron(args)
            out = capsys.readouterr().out
            assert "at least one field" in out

    def test_cron_update_every_and_cron_exclusive(self, tmp_path, capsys):
        with patch("personalclaw.cli_commands.ScheduleService"):
            args = argparse.Namespace(
                cron_action="update",
                job_id="job1",
                name=None,
                message=None,
                every_secs=300,
                cron_expr="0 9 * * *",
                channel=None,
                approval_mode=None,
            )
            _cron(args)
            out = capsys.readouterr().out
            assert "not both" in out

    def test_cron_update_not_found(self, tmp_path, capsys):
        with patch("personalclaw.cli_commands.ScheduleService") as mock_svc_cls, \
             patch("personalclaw.cli_commands.sel") as mock_sel:
            mock_svc = mock_svc_cls.return_value
            mock_svc.update_job.return_value = None
            args = argparse.Namespace(
                cron_action="update",
                job_id="nonexist",
                name=None,
                message=None,
                every_secs=None,
                cron_expr=None,
                channel=None,
                approval_mode="auto",
            )
            _cron(args)
            assert "nonexist" in capsys.readouterr().out
            mock_sel.return_value.log_api_access.assert_called_once_with(
                caller="cli", operation="cron.update",
                outcome="not_found", source="cli",
                resources="job_id=nonexist reason=not_found",
            )


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
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")
        monkeypatch.setattr("personalclaw.cli_setup.config_path", lambda: cfg_file)

        from personalclaw.cli_setup import _setup_timezone

        with patch("builtins.input", return_value="Invalid/Timezone"):
            with patch("personalclaw.cli_setup._detect_system_timezone", return_value=""):
                _setup_timezone()

        data = json.loads(cfg_file.read_text())
        assert "timezone" not in data
        output = capsys.readouterr().out
        assert "Unknown timezone" in output

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

        _setup_timezone()

        # File should be unchanged
        assert cfg_file.read_text() == "not json {{{"
        output = capsys.readouterr().out
        assert "Could not read" in output


class TestLogout:
    """Tests for _logout CLI function."""

    def test_logout_success(self, tmp_path, monkeypatch):
        """Successful logout prints success message."""
        secret_file = tmp_path / ".local_secret"
        secret_file.write_text("test-secret")
        monkeypatch.setattr("personalclaw.cli_server.config_dir", lambda: tmp_path)

        from personalclaw.cli_server import _logout

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _logout(7777)  # Should not raise

    def test_logout_gateway_not_running(self, tmp_path, monkeypatch):
        """Missing secret file means gateway not running."""
        monkeypatch.setattr("personalclaw.cli_server.config_dir", lambda: tmp_path)

        from personalclaw.cli_server import _logout

        try:
            _logout(7777)
            assert False, "should have exited"
        except SystemExit as e:
            assert e.code == 1

    def test_logout_http_error(self, tmp_path, monkeypatch):
        """HTTP error from gateway is handled."""
        secret_file = tmp_path / ".local_secret"
        secret_file.write_text("test-secret")
        monkeypatch.setattr("personalclaw.cli_server.config_dir", lambda: tmp_path)

        from personalclaw.cli_server import _logout

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(None, 403, "Forbidden", {}, None),
        ):
            try:
                _logout(7777)
                assert False, "should have exited"
            except SystemExit as e:
                assert e.code == 1

    def test_logout_connection_error(self, tmp_path, monkeypatch):
        """Connection error means gateway not running."""
        secret_file = tmp_path / ".personalclaw" / ".local_secret"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text("test-secret")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        from personalclaw.cli_server import _logout

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            try:
                _logout(7777)
                assert False, "should have exited"
            except SystemExit as e:
                assert e.code == 1

    def test_logout_error_response(self, tmp_path, monkeypatch):
        """Error response from gateway is handled."""
        secret_file = tmp_path / ".personalclaw" / ".local_secret"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text("test-secret")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        from personalclaw.cli_server import _logout

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": false, "error": "test error"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            try:
                _logout(7777)
                assert False, "should have exited"
            except SystemExit as e:
                assert e.code == 1


class TestStatus:
    """Tests for _status() HTTP error handling."""

    def _make_args(self, port=7777):
        return argparse.Namespace(port=port)

    def test_status_auth_required(self, capsys):
        """401/403 should report gateway as running with token auth."""
        from personalclaw.cli_server import _status

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://127.0.0.1:7777/api/status", 403, "Forbidden", {}, None
            ),
        ):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "token auth" in out

    def test_status_other_http_error(self, capsys):
        """Non-auth HTTP errors should report gateway as running with code."""
        from personalclaw.cli_server import _status

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://127.0.0.1:7777/api/status", 500, "Internal Server Error", {}, None
            ),
        ):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "HTTP 500" in out

    def test_status_connection_refused(self, capsys):
        """Connection refused should report gateway as not running."""
        from personalclaw.cli_server import _status

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "not running" in out

    def test_status_success(self, capsys):
        """200 OK should display stats."""
        from personalclaw.cli_server import _status

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"uptime": "1h 0m", "sessions": 2, "messages": 10,
             "tool_calls": 5, "subagents": 0, "crons": 1, "lessons": 3}
        ).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "1h 0m" in out
        assert "Sessions" in out or "sessions" in out.lower()

    def test_status_unexpected_exception(self, capsys):
        """Non-network exceptions should report gateway as running with unexpected response."""
        from personalclaw.cli_server import _status

        with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
            _status(self._make_args())
        out = capsys.readouterr().out
        assert "running" in out
        assert "unexpected response" in out


class TestIsPersonalclawProcess:
    """Tests for _is_personalclaw_process helper."""

    def test_returns_true_for_personalclaw(self):
        from personalclaw.cli_server import _is_personalclaw_process

        with patch("subprocess.check_output", return_value="python3 -m personalclaw.dashboard\n"):
            assert _is_personalclaw_process(1234) is True

    def test_returns_true_for_personalclaw_binary(self):
        from personalclaw.cli_server import _is_personalclaw_process

        with patch("subprocess.check_output", return_value="/usr/bin/personalclaw start\n"):
            assert _is_personalclaw_process(1234) is True

    def test_returns_false_for_unrelated(self):
        from personalclaw.cli_server import _is_personalclaw_process

        with patch("subprocess.check_output", return_value="nginx: worker process\n"):
            assert _is_personalclaw_process(1234) is False

    def test_returns_false_for_broad_match(self):
        """Editing a personalclaw file should NOT match — only gateway entry points."""
        from personalclaw.cli_server import _is_personalclaw_process

        with patch("subprocess.check_output", return_value="vim /tmp/personalclaw-notes.txt\n"):
            assert _is_personalclaw_process(1234) is False

    def test_returns_false_on_process_exit(self):
        from personalclaw.cli_server import _is_personalclaw_process

        with patch("subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "ps")):
            assert _is_personalclaw_process(1234) is False

    def test_raises_on_missing_ps(self):
        from personalclaw.cli_server import _is_personalclaw_process

        with patch("subprocess.check_output", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                _is_personalclaw_process(1234)


class TestStop:
    """Tests for _stop CLI function."""

    def _mock_sel(self):
        mock = MagicMock()
        return patch("personalclaw.cli_commands.sel", return_value=mock)

    @pytest.fixture(autouse=True)
    def _no_service(self):
        # ``_stop`` short-circuits via ``service_controller.stop_service()``
        # when a systemd/launchd service is active on the host. Force the
        # SIGTERM-by-port path so tests don't flake based on whether the
        # test host happens to have ``personalclaw.service`` installed.
        with patch(
            "personalclaw.cli_server.service_controller.stop_service", return_value=False
        ):
            yield

    def test_lsof_not_found(self, capsys):
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=FileNotFoundError
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "lsof" in capsys.readouterr().out

    def test_no_process_on_port(self, capsys):
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "lsof")
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "No PersonalClaw gateway" in capsys.readouterr().out

    def test_no_personalclaw_process(self, capsys):
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=[
                "1234\n",  # lsof returns a PID
                "nginx: worker\n",  # ps shows non-personalclaw
            ]
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "No PersonalClaw gateway" in capsys.readouterr().out

    def test_ps_not_found(self, capsys):
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=[
                "1234\n",  # lsof returns a PID
                FileNotFoundError,  # ps not found
            ]
        ):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "ps" in capsys.readouterr().out

    def test_successful_stop(self, capsys):
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=[
                "1234\n",  # lsof
                "python3 -m personalclaw.dashboard\n",  # ps
            ]
        ), patch("os.kill"), patch("time.sleep"):
            _stop(7777)
        assert "SIGTERM" in capsys.readouterr().out

    def test_permission_denied(self, capsys):
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=[
                "1234\n",
                "python3 -m personalclaw.dashboard\n",
            ]
        ), patch("os.kill", side_effect=PermissionError):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "No permission" in capsys.readouterr().out

    def test_process_already_exited(self, capsys):
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=[
                "1234\n",
                "python3 -m personalclaw.dashboard\n",
            ]
        ), patch("os.kill", side_effect=ProcessLookupError):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        assert "already exited" in capsys.readouterr().out

    def test_partial_permission_denied(self, capsys):
        """One PID succeeds, another is denied — reports both."""
        from personalclaw.cli_server import _stop

        def kill_side_effect(pid, sig):
            if pid == 5678:
                raise PermissionError

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=[
                "1234\n5678\n",
                "python3 -m personalclaw.dashboard\n",  # ps for 1234
                "python3 -m personalclaw.dashboard\n",  # ps for 5678
            ]
        ), patch("os.kill", side_effect=kill_side_effect), patch("time.sleep"):
            with pytest.raises(SystemExit) as exc:
                _stop(7777)
            assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "SIGTERM" in out
        assert "No permission" in out

    def test_lsof_with_warnings(self, capsys):
        """lsof sometimes emits warnings mixed with PIDs — non-digit lines are filtered."""
        from personalclaw.cli_server import _stop

        with self._mock_sel(), patch(
            "subprocess.check_output", side_effect=[
                "1234\nlsof: WARNING: can't stat() ...\n",
                "python3 -m personalclaw.dashboard\n",
            ]
        ), patch("os.kill"), patch("time.sleep"):
            _stop(7777)
        assert "SIGTERM" in capsys.readouterr().out


class TestResolveClientPort:
    """Tests for `resolve_client_port` — the port-resolution order used by
    `personalclaw token` / `status` / `logout` / `stop` to find the gateway.

    Resolution order (see cli.resolve_client_port):
      1. explicit --port CLI arg (cli_port != None)
      2. PERSONALCLAW_PORT env var
      3. port parsed from dashboard.url in config
      4. default 10000
    """

    def test_cli_flag_wins(self, monkeypatch, tmp_path):
        """An explicit --port flag must override env and config."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.setenv("PERSONALCLAW_PORT", "9999")
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:8888"
        with patch("personalclaw.cli_server.AppConfig.load", return_value=mock_cfg):
            assert resolve_client_port(12345) == 12345

    def test_env_var_used_when_no_cli(self, monkeypatch):
        """PERSONALCLAW_PORT env var wins over config when no --port passed."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.setenv("PERSONALCLAW_PORT", "6777")
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:8888"
        with patch("personalclaw.cli_server.AppConfig.load", return_value=mock_cfg):
            assert resolve_client_port(None) == 6777

    def test_invalid_env_var_falls_through_to_config(self, monkeypatch):
        """A garbage PERSONALCLAW_PORT must not crash; the helper falls through."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.setenv("PERSONALCLAW_PORT", "not-a-number")
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:7778"
        with patch("personalclaw.cli_server.AppConfig.load", return_value=mock_cfg):
            assert resolve_client_port(None) == 7778

    def test_config_url_used_when_no_cli_no_env(self, monkeypatch):
        """The port in dashboard.url must be honoured when env is unset."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://localhost:7778"
        with patch("personalclaw.cli_server.AppConfig.load", return_value=mock_cfg):
            assert resolve_client_port(None) == 7778

    def test_config_url_hostname_only_falls_through_to_default(self, monkeypatch):
        """A dashboard.url without an explicit port must fall through to 10000."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = "http://my.host.example"
        with patch("personalclaw.cli_server.AppConfig.load", return_value=mock_cfg):
            # parse_dashboard_url returns _DEFAULT_PORT when no port in URL,
            # which is the same as the final fallback — either way we land on 10000.
            assert resolve_client_port(None) == 10000

    def test_empty_config_falls_through_to_default(self, monkeypatch):
        """No env, empty dashboard.url → 10000."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
        mock_cfg = MagicMock()
        mock_cfg.dashboard.url = ""
        with patch("personalclaw.cli_server.AppConfig.load", return_value=mock_cfg):
            assert resolve_client_port(None) == 10000

    def test_config_load_failure_falls_through_to_default(self, monkeypatch):
        """If config loading raises, the helper must still return a usable port."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
        with patch("personalclaw.cli_server.AppConfig.load", side_effect=RuntimeError("boom")):
            assert resolve_client_port(None) == 10000

    def test_cli_flag_zero_is_respected(self, monkeypatch):
        """Port 0 is weird but valid; it must not be coerced to None/default."""
        from personalclaw.cli_server import resolve_client_port

        monkeypatch.setenv("PERSONALCLAW_PORT", "9999")
        # cli_port=0 is explicit; the helper uses 'is not None' not truthiness.
        assert resolve_client_port(0) == 0


class TestDoctorStaleProjectDir:
    """Tests for doctor stale project_dir detection."""

    def test_doctor_detects_stale_project_dir(self, tmp_path, capsys):
        proj_file = tmp_path / "project_dir"
        proj_file.write_text("/nonexistent/deleted\n")
        agent_file = tmp_path / "personalclaw.json"
        agent_data = {
            "tools": ["@personalclaw-core", "@personalclaw-schedule"],
            "allowedTools": ["@personalclaw-core", "@personalclaw-schedule"],
            "mcpServers": {
                "personalclaw-core": {"command": "/usr/local/bin/personalclaw", "args": ["mcp-core"]},
                "personalclaw-schedule": {"command": "/usr/local/bin/personalclaw", "args": ["mcp-schedule"]},
            },
        }
        agent_file.write_text(json.dumps(agent_data))
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        with (
            patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
            patch("personalclaw.cli_doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen"),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.cli_doctor.config_dir", return_value=tmp_path),
            patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": ""}, clear=False),
        ):
            with pytest.raises(SystemExit):
                _doctor()
        out = capsys.readouterr().out
        assert "stale" in out
        assert "project dir: ⚠️  not set" not in out  # should NOT show fallback message


class TestDoctorMcpCmdFixed:
    """Tests for doctor auto-fixing stale MCP binary paths."""

    def test_doctor_fixes_stale_mcp_path(self, tmp_path, capsys):
        agent_file = tmp_path / "personalclaw.json"
        # personalclaw-schedule has valid path, personalclaw-core has stale path
        valid_bin = tmp_path / "personalclaw"
        valid_bin.write_text("#!/bin/sh")
        valid_bin.chmod(0o755)
        agent_data = {
            "tools": ["@personalclaw-core", "@personalclaw-schedule"],
            "allowedTools": ["@personalclaw-core", "@personalclaw-schedule"],
            "mcpServers": {
                "personalclaw-core": {"command": "/nonexistent/personalclaw", "args": ["mcp-core"]},
                "personalclaw-schedule": {"command": str(valid_bin), "args": ["mcp-schedule"]},
            },
        }
        agent_file.write_text(json.dumps(agent_data))
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")

        def which_side_effect(b):
            if b == "personalclaw":
                return "/usr/bin/personalclaw"
            return f"/usr/local/bin/{b}"

        with (
            patch("personalclaw.cli_doctor.shutil.which", side_effect=which_side_effect),
            patch("personalclaw.cli_doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen"),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.dict("os.environ", {"PERSONALCLAW_PROJECT_DIR": ""}, clear=False),
            # STT defaults to enabled; keep it disabled here so the unrelated
            # "no STT model selected" issue doesn't trigger a non-zero exit.
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": False},
            ),
            patch("personalclaw.stt.registry.active_stt", return_value=None),
        ):
            _doctor()
        out = capsys.readouterr().out
        assert "fixed stale path" in out
        assert "Auto-fixed stale binary" in out
        # Verify it did NOT print the tools/allowedTools message
        assert "Auto-fixed tools/allowedTools" not in out


class TestDoctorStt:
    """Tests for doctor Speech-to-Text section.

    STT now resolves through the typed registry: enabled lives in
    use_case_settings/stt.json (read via load_use_case_settings) and the
    active model in active_models.json (read via active_stt).
    """

    def _agent_file(self, tmp_path):
        agent_file = tmp_path / "personalclaw.json"
        agent_data = {
            "tools": ["@personalclaw-core", "@personalclaw-schedule"],
            "allowedTools": ["@personalclaw-core", "@personalclaw-schedule"],
            "mcpServers": {
                "personalclaw-core": {"command": "/usr/local/bin/personalclaw", "args": ["mcp-core"]},
                "personalclaw-schedule": {"command": "/usr/local/bin/personalclaw", "args": ["mcp-schedule"]},
            },
        }
        agent_file.write_text(json.dumps(agent_data))

    def test_doctor_stt_enabled_with_model(self, tmp_path, capsys):
        self._agent_file(tmp_path)
        mock_run = MagicMock(returncode=0, stdout="personalclaw-cli 1.0.0", stderr="")
        provider = MagicMock()
        provider.name = "faster_whisper"
        with (
            patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
            patch("personalclaw.cli_doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.cli_doctor.ensure_ffmpeg_in_path"),
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
            patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
            patch("personalclaw.cli_doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.cli_doctor.ensure_ffmpeg_in_path"),
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
            patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
            patch("personalclaw.cli_doctor.AGENTS_DIR", tmp_path),
            patch("subprocess.run", return_value=mock_run),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
            patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
            patch("personalclaw.cli_doctor.ensure_ffmpeg_in_path"),
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

    def test_project_dir_file_uses_config_dir(self, tmp_path, monkeypatch):
        """_project_dir_file() returns path under config_dir(), not hardcoded home."""
        monkeypatch.setattr("personalclaw.cli.config_dir", lambda: tmp_path)

        from personalclaw.cli import _project_dir_file

        assert _project_dir_file() == tmp_path / "project_dir"

    def test_detect_project_dir_reads_from_config_dir(self, tmp_path, monkeypatch):
        """_detect_project_dir reads saved path from config_dir()/project_dir."""
        proj = tmp_path / "my_project"
        proj.mkdir()
        (proj / "agents").mkdir()
        (proj / "skills").mkdir()

        config_home = tmp_path / "custom_config"
        config_home.mkdir()
        (config_home / "project_dir").write_text(str(proj) + "\n")

        monkeypatch.setattr("personalclaw.cli.config_dir", lambda: config_home)
        monkeypatch.chdir(tmp_path)  # CWD has no project markers

        from personalclaw.cli import _detect_project_dir

        assert _detect_project_dir() == str(proj)

    def test_logout_reads_secret_from_config_dir(self, tmp_path, monkeypatch):
        """_logout reads .local_secret from config_dir(), not ~/.personalclaw."""
        secret_file = tmp_path / ".local_secret"
        secret_file.write_text("test-secret")
        monkeypatch.setattr("personalclaw.cli_server.config_dir", lambda: tmp_path)

        from personalclaw.cli_server import _logout

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _logout(7777)

    def test_setup_slack_tokens_writes_to_config_dir(self, tmp_path, monkeypatch):
        """_setup_slack_tokens writes .env to config_dir(), not ~/.personalclaw."""
        monkeypatch.setattr("personalclaw.cli_setup.env_path", lambda: tmp_path / ".env")

        from personalclaw.cli_setup import _setup_slack_tokens

        # Simulate user providing all tokens
        inputs = iter(["y", "xapp-test", "xoxb-test", "U12345"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        _setup_slack_tokens()
        assert (tmp_path / ".env").exists()
        content = (tmp_path / ".env").read_text()
        assert "xapp-test" in content
