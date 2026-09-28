"""`personalclaw restore` takes an export archive as well as a snapshot, guarded the same way.

The dashboard refuses a replace from an export archive while it runs, as it refuses one from a
snapshot, and names `personalclaw restore <archive> --mode replace` for it. Before, only a snapshot
could be restored at a terminal: the command read every file as a gzipped tar, so the one way the
refusal offered to replace a home from an export archive failed on the archive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.snapshot import restore_main


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    (h / "tasks").mkdir(parents=True)
    (h / "config.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    (h / "tasks" / "exported.json").write_text(json.dumps({"id": "exported"}), encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    return h


@pytest.fixture
def export(home, tmp_path) -> Path:
    """This home's export archive, as Settings → Import / Export writes it."""
    from personalclaw.portability import create_export_zip

    data, _manifest = create_export_zip()
    path = tmp_path / "personalclaw-export.zip"
    path.write_bytes(data)
    return path


def _gateway(monkeypatch, running: bool) -> None:
    monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: running)


def test_a_replace_from_an_export_archive_runs_at_the_terminal(home, export, monkeypatch, capsys):
    """🔴 Red before: the command opened the archive as a tar and failed."""
    _gateway(monkeypatch, False)
    (home / "tasks" / "exported.json").unlink()
    (home / "tasks" / "later.json").write_text(json.dumps({"id": "later"}), encoding="utf-8")

    assert restore_main([str(export), "--mode", "replace"]) == 0

    assert (home / "tasks" / "exported.json").is_file(), "the archive's state is back"
    assert not (home / "tasks" / "later.json").exists(), "a replace, not a merge"
    (aside,) = home.glob("pre-restore-*")
    assert (aside / "tasks" / "later.json").is_file(), "and what it replaced is kept aside"
    assert f"Previous state saved to: {aside}/" in capsys.readouterr().out


def test_it_is_refused_while_the_gateway_runs(home, export, monkeypatch):
    """The snapshot restore's guard, for an export archive too; `--force` is the terminal's."""
    _gateway(monkeypatch, True)
    (home / "tasks" / "later.json").write_text(json.dumps({"id": "later"}), encoding="utf-8")

    assert restore_main([str(export), "--mode", "replace"]) == 1

    assert (home / "tasks" / "later.json").is_file()
    assert not list(home.glob("pre-restore-*"))


def test_a_populated_home_is_merged_unless_a_replace_is_asked_for(home, export, monkeypatch):
    _gateway(monkeypatch, False)
    (home / "tasks" / "exported.json").unlink()
    (home / "tasks" / "later.json").write_text(json.dumps({"id": "later"}), encoding="utf-8")

    assert restore_main([str(export)]) == 0

    assert (home / "tasks" / "exported.json").is_file()
    assert (home / "tasks" / "later.json").is_file(), "a merge keeps what the home has"
    assert not list(home.glob("pre-restore-*"))


def test_a_dry_run_and_components_write_nothing(home, export, monkeypatch):
    _gateway(monkeypatch, False)
    (home / "tasks" / "exported.json").unlink()

    assert restore_main([str(export), "--mode", "replace", "--dry-run"]) == 0
    assert restore_main([str(export), "--components", "memory"]) == 2, "an archive is whole"

    assert not (home / "tasks" / "exported.json").exists()
    assert not list(home.glob("pre-restore-*"))
