"""The snapshot is the backup a restore reads; the shards are a copy of the records, and say so.

🔴 The hourly shard export, `personalclaw backup export` and every sync copied each folder store —
skills, cron scripts, uploads, the workspace, installed apps — as blobs named by their content,
with no path. Nothing could put a file back where it was, `import_shards` listed a folder the
exporter never wrote them to, and nothing read the list. So a user who trusted the export, or the
sync, to hold their skills held no skill anywhere but on the machine; and each sync cycle uploaded
every file of every folder again.

A folder of files stays out of the shards now, and every backup surface says which copy a restore
reads: Settings → Backups, `personalclaw backup export` and `validate`, the CLI reference, the
setting's help and `import_shards` itself. The snapshot holds the folders, with every file at its
path, and a restore of it brings them back — asserted here too, since it is the claim the surfaces
now make.

And `personalclaw backup export OUT_DIR` cleared OUT_DIR for a full export by deleting the folder
whole, whatever it held: pointed at a folder of the user's, it deleted that folder. It now refuses a
folder that holds anything but an earlier export, and replaces only what an export wrote.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.durability import shards
from personalclaw.durability.shards import NOT_A_BACKUP, export_shards, import_shards

_ROOT = Path(__file__).resolve().parents[1]

#: One file of each kind of folder a user keeps, at a path a restore must put it back at.
FOLDER_FILES = {
    "skills/weekly-review/SKILL.md": "---\nname: weekly-review\n---\nSum up the week.\n",
    "crons/nightly.sh": "#!/bin/sh\necho nightly\n",
    "uploads/u-1/report.pdf": "%PDF-1.4 a report",
    "workspace/notes/plan.md": "# The plan\n",
    "apps/clone-voice/data/notes.txt": "the app's own state",
}


def _home(tmp_path: Path, monkeypatch, name: str = "home") -> Path:
    home = tmp_path / name
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    (home / "tasks").mkdir()
    (home / "tasks" / "t1.json").write_text(json.dumps({"id": "t1", "title": "a task"}))
    for rel, text in FOLDER_FILES.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text, encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _every_file(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


@pytest.mark.parametrize("for_sync", [False, True], ids=["the hourly export", "a sync"])
def test_the_shards_carry_no_folder_of_files(tmp_path, monkeypatch, for_sync):
    home = _home(tmp_path, monkeypatch)
    out = tmp_path / "shards"
    export_shards(home, out, for_sync=for_sync)
    carried = _every_file(out)
    assert "tasks/entities.jsonl" in carried, "the records are still exported"
    blobs = sorted(rel for rel in carried if "/blobs/" in rel)
    assert blobs == [], f"folder files copied into the shards: {blobs}"
    for rel, text in FOLDER_FILES.items():
        assert not any(text.encode() in data for data in carried.values()), rel
    assert not set(import_shards(out).rows) & {e.id for e in inv.INVENTORY if e.kind == "tree"}


def test_the_copies_an_earlier_export_made_of_a_folder_are_removed(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    out = tmp_path / "shards"
    export_shards(home, out)
    stale = out / "skills" / "blobs" / "ab" / ("ab" + "0" * 62)
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"a skill an earlier build copied")
    export_shards(home, out, entries=["tasks"])
    assert not (out / "skills").exists()


def test_a_restore_of_the_snapshot_brings_every_folder_back(tmp_path, monkeypatch):
    """The claim every surface now makes: the snapshot holds the folders, at their paths."""
    from personalclaw.snapshot import restore_main, snapshot_main

    _home(tmp_path, monkeypatch, "old")
    assert snapshot_main([str(tmp_path / "snaps")]) in (0, None)
    (tarball,) = sorted((tmp_path / "snaps").glob("personalclaw-snapshot-*.tar.gz"))
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(fresh))
    assert restore_main([str(tarball), "--mode", "replace", "--force"]) == 0
    for rel, text in FOLDER_FILES.items():
        assert (fresh / rel).read_text(encoding="utf-8") == text, rel


# ── `personalclaw backup export OUT_DIR` never deletes what is not its own ─────────────────────


def _backup(command: str, directory: Path) -> int:
    key = "out_dir" if command == "export" else "shard_dir"
    return shards.backup_cmd(
        Namespace(backup_command=command, incremental=False, **{key: str(directory)})
    )


def test_a_full_export_refuses_a_folder_that_holds_something_else(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    mine = tmp_path / "Documents"
    mine.mkdir()
    (mine / "taxes.pdf").write_bytes(b"not a shard")
    assert _backup("export", mine) == 1
    assert (mine / "taxes.pdf").read_bytes() == b"not a shard"
    assert sorted(p.name for p in mine.iterdir()) == ["taxes.pdf"]
    assert "holds files and no shard export" in capsys.readouterr().err


def test_a_full_export_replaces_only_what_an_export_wrote(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    out = tmp_path / "review"
    assert _backup("export", out) == 0
    # The shards are kept in git to diff them, the use they are made for, with a note beside.
    (out / ".git").mkdir()
    (out / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (out / "NOTES.md").write_text("what changed this week\n")
    (home / "tasks" / "t1.json").unlink()
    assert _backup("export", out) == 0
    assert (out / ".git" / "HEAD").read_text() == "ref: refs/heads/main\n"
    assert (out / "NOTES.md").read_text() == "what changed this week\n"
    assert import_shards(out).rows.get("tasks") == [], "the earlier export's shards are replaced"
    assert _backup("validate", out) == 0


def test_an_empty_folder_takes_a_full_export(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    out = tmp_path / "empty"
    out.mkdir()
    assert _backup("export", out) == 0
    assert (out / "manifest.json").is_file()


# ── every surface says which copy a restore reads ──────────────────────────────────────────────


def test_the_backup_command_says_what_the_shards_are(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    out = tmp_path / "shards"
    assert _backup("export", out) == 0
    assert NOT_A_BACKUP in capsys.readouterr().out
    assert _backup("validate", out) == 0
    assert NOT_A_BACKUP in capsys.readouterr().out
    assert "The backup is a snapshot" in NOT_A_BACKUP and "skills" in NOT_A_BACKUP


def test_the_cli_reference_says_the_snapshot_is_the_backup():
    doc = (_ROOT / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    export_row = next(line for line in doc.splitlines() if "personalclaw backup export" in line)
    snapshot_row = next(line for line in doc.splitlines() if "`personalclaw snapshot [" in line)
    assert "It is not a backup" in export_row
    assert "The backup is `personalclaw snapshot`" in export_row
    assert "the backup a restore reads" in snapshot_row and "skills" in snapshot_row
    assert "A backup nobody has verified" not in doc, "validate no longer calls the shards a backup"


def test_the_setting_says_what_a_restore_brings_back():
    from personalclaw.config.loader import DurabilityConfig

    help_text = DurabilityConfig.__dataclass_fields__["auto_backup"].metadata["help"]
    assert "what a restore brings back" in help_text
    assert "nothing restores from it" in help_text


def test_import_shards_says_it_holds_no_folder():
    import inspect

    assert "never a folder store" in (shards.import_shards.__doc__ or "")
    source = inspect.getsource(shards.import_shards)
    assert 'shard_dir / "blobs"' not in source, "nothing lists what the shards do not hold"
    assert not hasattr(shards.ImportResult(), "blobs")
