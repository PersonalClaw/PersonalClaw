"""Unit tests for the ``personalclaw agent`` CLI subcommand group.

Tests cover list output format, create with defaults, create duplicate,
update non-existent, and delete default agent.
"""

import json
import unittest.mock
from pathlib import Path

import pytest

from personalclaw.cli import main


def _write_config(tmp_path: Path, data: dict) -> Path:
    """Write a config.json to *tmp_path* and return the path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def _base_config() -> dict:
    """Return a minimal valid config with a default agent."""
    return {
        "agents": {
            "default": {
                "provider_agent": "personalclaw",
                "default_dir": "",
                "memory_store": "default",
            },
        },
        "default_agent": "default",
        "memory_stores": {"default": {}},
    }


class TestAgentList:
    """Test ``personalclaw agent list`` output format."""

    def test_list_output_format(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path),
            unittest.mock.patch("sys.argv", ["personalclaw", "agent", "list"]),
        ):
            main()

        out = capsys.readouterr().out
        # Header row
        assert "NAME" in out
        assert "PROVIDER_AGENT" in out
        assert "DEFAULT_DIR" in out
        assert "MEMORY_STORE" in out
        # Default agent marked with *
        assert "default *" in out or "default*" in out

    def test_list_multiple_agents(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        data = _base_config()
        data["agents"]["oncall"] = {
            "provider_agent": "oncall-agent",
            "default_dir": "oncall-ws",
            "memory_store": "oncall-mem",
        }
        cfg_path = _write_config(tmp_path, data)

        with (
            unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path),
            unittest.mock.patch("sys.argv", ["personalclaw", "agent", "list"]),
        ):
            main()

        out = capsys.readouterr().out
        assert "oncall" in out
        assert "oncall-agent" in out


class TestAgentCreate:
    """Test ``personalclaw agent create``."""

    def test_create_with_defaults(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path),
            unittest.mock.patch(
                "sys.argv",
                ["personalclaw", "agent", "create", "--name", "research"],
            ),
        ):
            main()

        out = capsys.readouterr().out
        assert "Created agent: research" in out

        # Verify persisted to disk
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "research" in saved["agents"]
        assert saved["agents"]["research"]["provider_agent"] == "personalclaw"
        # --default-dir defaults to "" (empty inherits the workspace root)
        assert saved["agents"]["research"]["default_dir"] == ""
        assert saved["agents"]["research"]["memory_store"] == "default"

    def test_create_duplicate_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path),
            unittest.mock.patch(
                "sys.argv",
                ["personalclaw", "agent", "create", "--name", "default"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "already exists" in err


class TestAgentUpdate:
    """Test ``personalclaw agent update``."""

    def test_update_nonexistent_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path),
            unittest.mock.patch(
                "sys.argv",
                ["personalclaw", "agent", "update", "nonexistent", "--provider-agent", "x"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "not found" in err


class TestAgentDelete:
    """Test ``personalclaw agent delete``."""

    def test_delete_default_agent_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = _write_config(tmp_path, _base_config())

        with (
            unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path),
            unittest.mock.patch(
                "sys.argv",
                ["personalclaw", "agent", "delete", "default"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "cannot delete default agent" in err
