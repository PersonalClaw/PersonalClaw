"""Unit tests for the ``personalclaw agent`` CLI subcommand group.

Tests cover list output format, create with defaults, create duplicate,
update non-existent, and delete default agent.
"""

import json
import unittest.mock
from pathlib import Path

import pytest

from personalclaw.cli import main
from personalclaw.cli_commands import render_agent_table
from personalclaw.config.loader import AppConfig

_HEADERS = ("NAME", "PROVIDER_AGENT", "DEFAULT_DIR", "MEMORY_STORE")


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

    def test_long_names_keep_every_column_under_its_header(self, tmp_path: Path) -> None:
        """A name longer than the old fixed 20-char NAME column no longer shifts the table (#2949).

        The three agent names shipped by default that overflowed — ``personalclaw-code-planner``
        and ``personalclaw-goal-planner`` (25) and ``personalclaw-template-refiner`` (29) — are
        used verbatim, so the regression is measured against the real overflow, not a synthetic one.
        """
        data = _base_config()
        for name in (
            "personalclaw-code-planner",
            "personalclaw-goal-planner",
            "personalclaw-template-refiner",
        ):
            data["agents"][name] = {
                "provider_agent": "personalclaw",
                "default_dir": "ws",
                "memory_store": "default",
            }
        cfg_path = _write_config(tmp_path, data)

        with unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path):
            table = render_agent_table(AppConfig.load())

        header, *rows = table.splitlines()
        assert "personalclaw-template-refiner" in table
        # Under the old fixed widths the 25- and 29-char names pushed every later column
        # right by 5–9 chars. Slicing each row at the HEADER's offsets must still recover
        # whole cells — a shifted row leaks a neighbour's characters into the slice.
        starts = [header.index(column) for column in _HEADERS]
        bounds = [*zip(starts, [*starts[1:], None])]
        for row in rows:
            cells = [row[begin:end].strip() for begin, end in bounds]
            assert cells[0], f"NAME cell is empty on row {row!r}"
            assert cells[1] in ("", "personalclaw"), f"PROVIDER_AGENT leaked on row {row!r}"
            assert cells[2] in ("", "ws"), f"DEFAULT_DIR leaked on row {row!r}"
            assert cells[3] in ("", "default"), f"MEMORY_STORE leaked on row {row!r}"
        assert any(row.startswith("personalclaw-template-refiner ") for row in rows)

    def test_default_marker_does_not_eat_the_name_budget(self, tmp_path: Path) -> None:
        """The ` *` marker is measured as part of the NAME cell, not carved out of it (#2949)."""
        data = _base_config()
        data["agents"]["personalclaw-template-refiner"] = {
            "provider_agent": "personalclaw",
            "default_dir": "",
            "memory_store": "default",
        }
        data["default_agent"] = "personalclaw-template-refiner"
        cfg_path = _write_config(tmp_path, data)

        with unittest.mock.patch("personalclaw.config.loader.config_path", return_value=cfg_path):
            table = render_agent_table(AppConfig.load())

        header, *rows = table.splitlines()
        marked = next(row for row in rows if " *" in row)
        assert marked.startswith("personalclaw-template-refiner *")
        start = header.index("PROVIDER_AGENT")
        assert marked[start:].startswith("personalclaw")


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
