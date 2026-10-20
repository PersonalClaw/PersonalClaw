"""An archive or an export of the user's data is readable by its owner alone from its first byte.

A snapshot carries the audit log's signing key and every memory. It was written to a temp file the
tar module opened itself, at the umask's mode, and tightened to 0600 only once it was whole and
renamed: a 404 MB temp file sat ``-rw-r--r--`` for the minute it took. The temp name doubled
its suffix too (``<name>.tar.tar.gz.tmp``). A ``backup export`` folder, the manifest beside a
snapshot, a memory export and a project archive written outside the home were 0644 for good.

Each test runs under umask 022, the usual one, and looks at the file WHILE it is being written:
the snapshot from inside the tar module's ``add``, the whole-file writers at the moment their temp
file is renamed into place. The home is the per-test one the suite's isolation gives every test.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.config import loader as config_loader


@pytest.fixture(autouse=True)
def _umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def home(tmp_path_factory) -> Path:
    """The test's own home (the suite's isolation fixture made it), with a key and a memory."""
    home = config_loader.config_dir()
    assert str(home).startswith(str(tmp_path_factory.getbasetemp())), home
    (home / "sel_hmac.key").write_bytes(b"\x00\x01\x02\x03")
    (home / "workspace").mkdir(exist_ok=True)
    (home / "workspace" / "notes.md").write_text("Buy seeds for the garden.\n")
    (home / "notifications.jsonl").write_text('{"ts": "2026-01-01T00:00:00Z", "msg": "hello"}\n')
    return home


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.fixture
def renames(monkeypatch) -> list[tuple[str, Path, int]]:
    """``(temp name, destination, temp mode)`` for every temp file renamed into place."""
    seen: list[tuple[str, Path, int]] = []
    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        source = Path(src)
        if source.is_file():
            seen.append((source.name, Path(dst), _mode(source)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return seen


# ── the snapshot ─────────────────────────────────────────────────────────────


def _snapshot_while_watching(out: Path, monkeypatch) -> tuple[Path, list[tuple[str, int]]]:
    """Take a snapshot into ``out``; return it and ``(name, mode)`` of each temp file in ``out``
    at every member the tar module adds."""
    from personalclaw.snapshot import snapshot_main

    seen: list[tuple[str, int]] = []
    real_add = tarfile.TarFile.add

    def add(self, name, *args, **kwargs):
        if out.is_dir():
            seen.extend((p.name, _mode(p)) for p in out.iterdir() if p.name.endswith(".tmp"))
        return real_add(self, name, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", add)
    assert snapshot_main([str(out)]) == 0
    archives = sorted(out.glob("personalclaw-snapshot-*.tar.gz"))
    assert len(archives) == 1, archives
    return archives[0], seen


@pytest.mark.parametrize("where", ["a folder the user named", "the home's snapshots folder"])
def test_a_snapshot_is_0600_while_it_is_written(home, tmp_path, monkeypatch, where):
    out = tmp_path / "chosen" / "backups" if where.startswith("a folder") else home / "snapshots"

    archive, seen = _snapshot_while_watching(out, monkeypatch)

    assert seen, "no temp file was seen while the archive was written"
    loose = {(name, oct(mode)) for name, mode in seen if mode != 0o600}
    assert not loose, f"the snapshot was readable by others while it was written: {loose}"
    assert _mode(archive) == 0o600
    assert not list(out.glob("*.tmp")), "the temp file was left behind"


def test_a_snapshot_temp_file_is_named_for_its_archive(home, tmp_path, monkeypatch):
    archive, seen = _snapshot_while_watching(tmp_path / "out", monkeypatch)

    names = {name for name, _ in seen}
    assert names, "no temp file was seen while the archive was written"
    for name in names:
        assert name.startswith(archive.name + "."), name
        assert ".tar.tar" not in name, name


def test_the_folders_a_snapshot_makes_are_0700_and_its_manifest_0600(home, tmp_path, monkeypatch):
    from personalclaw.durability.archive import sidecar_path

    out = tmp_path / "chosen" / "backups"
    archive, _ = _snapshot_while_watching(out, monkeypatch)

    assert _mode(out) == 0o700
    assert _mode(out.parent) == 0o700
    assert _mode(sidecar_path(archive)) == 0o600


def test_an_existing_folder_the_user_named_keeps_its_mode(home, tmp_path, monkeypatch):
    out = tmp_path / "shared"
    out.mkdir()
    out.chmod(0o750)

    archive, _ = _snapshot_while_watching(out, monkeypatch)

    assert _mode(out) == 0o750, "a folder the user made is theirs"
    assert _mode(archive) == 0o600


def test_a_snapshot_extracts_private(home, tmp_path, monkeypatch):
    """A member's mode is what any extraction makes: a restore's staging copy, the files a
    restore puts back, or a ``tar -x`` by hand."""
    archive, _ = _snapshot_while_watching(tmp_path / "out", monkeypatch)

    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
    assert any(m.name.endswith("/sel_hmac.key") for m in members)
    loose = {(m.name, oct(m.mode)) for m in members if m.mode != (0o700 if m.isdir() else 0o600)}
    assert not loose, f"members a restore or a tar -x would make readable by others: {loose}"


def test_a_restore_stages_the_archive_privately(home, tmp_path, monkeypatch):
    from personalclaw import snapshot

    archive, _ = _snapshot_while_watching(tmp_path / "out", monkeypatch)
    staged: dict[str, int] = {}
    real_merge = snapshot._do_merge

    def merge(snap, pc, components):
        staged[snap.parent.name] = _mode(snap.parent)
        staged.update({p.relative_to(snap).as_posix(): _mode(p) for p in snap.rglob("*")})
        return real_merge(snap, pc, components)

    monkeypatch.setattr(snapshot, "_do_merge", merge)
    result = snapshot.restore_merge(archive, None)

    assert result["ok"], result
    assert "sel_hmac.key" in staged
    loose = {(rel, oct(mode)) for rel, mode in staged.items() if mode & 0o077}
    assert not loose, f"a restore staged these readable by others: {loose}"


def test_a_restore_writes_the_audit_key_back_0600_from_its_first_byte(
    home, tmp_path, monkeypatch, renames
):
    """Copied and then tightened, the key sat at the umask's mode until the copy was done."""
    from personalclaw import snapshot

    archive, _ = _snapshot_while_watching(tmp_path / "out", monkeypatch)
    (home / "sel_hmac.key").unlink()

    assert snapshot.restore_merge(archive, ["security"])["ok"]

    assert (home / "sel_hmac.key").read_bytes() == b"\x00\x01\x02\x03"
    written = [mode for _, dst, mode in renames if dst == home / "sel_hmac.key"]
    assert written == [0o600], f"the key was not written whole from a 0600 temp file: {written}"
    assert _mode(home / "sel_hmac.key") == 0o600


# ── the exports ──────────────────────────────────────────────────────────────


def _assert_private_tree(root: Path) -> None:
    loose = {
        (p.relative_to(root).as_posix(), oct(_mode(p)))
        for p in [root, *root.rglob("*")]
        if _mode(p) != (0o700 if p.is_dir() else 0o600)
    }
    assert not loose, f"readable by others: {loose}"


def test_a_shard_export_is_0600_while_it_is_written(home, tmp_path, renames):
    from personalclaw.durability import shards

    out = tmp_path / "records" / "shards"
    args = argparse.Namespace(backup_command="export", out_dir=str(out), incremental=False)

    assert shards.backup_cmd(args) == 0

    written = [(name, dst, mode) for name, dst, mode in renames if out in dst.parents]
    assert any(dst.name == "manifest.json" for _, dst, _ in written), written
    assert any(dst.suffix == ".jsonl" for _, dst, _ in written), written
    loose = {(dst.name, oct(mode)) for _, dst, mode in written if mode != 0o600}
    assert not loose, f"a shard was readable by others while it was written: {loose}"
    _assert_private_tree(out)
    assert _mode(out.parent) == 0o700


def test_a_memory_export_file_is_0600_while_it_is_written(home, tmp_path, renames):
    from personalclaw.cli_commands import _memory_cmd

    out = tmp_path / "exported" / "memory.json"

    _memory_cmd(argparse.Namespace(mem_action="export", output=str(out)))

    assert set(json.loads(out.read_text())) == {"semantic", "episodic", "events"}
    written = [(name, mode) for name, dst, mode in renames if dst == out]
    assert written, "the export was not renamed into place from a temp file"
    assert {mode for _, mode in written} == {0o600}, written
    assert _mode(out) == 0o600
    assert _mode(out.parent) == 0o700


def test_a_project_export_is_0600_while_it_is_written(home, tmp_path, renames, monkeypatch):
    from personalclaw import cli_project

    plan = SimpleNamespace(
        entries=[], artifact_count=0, run_count=0, skipped=[], secrets_present=[]
    )
    monkeypatch.setattr(
        cli_project, "_resolve_project", lambda ident: SimpleNamespace(id="p1", name="Garden")
    )
    monkeypatch.setattr(cli_project, "_gather", lambda pid: ([], []))
    monkeypatch.setattr(
        cli_project.pa, "export_project_archive", lambda *a, **k: (b"PK\x05\x06" + b"\0" * 18, plan)
    )
    out = tmp_path / "handoff" / "garden.zip"

    assert (
        cli_project._export(argparse.Namespace(project="Garden", passphrase="", output=str(out)))
        == 0
    )

    written = [(name, mode) for name, dst, mode in renames if dst == out]
    assert written, "the archive was written in place, not renamed from a private temp file"
    assert {mode for _, mode in written} == {0o600}, written
    assert _mode(out) == 0o600


# ── the writer they share ────────────────────────────────────────────────────


def test_the_private_writer_is_0600_from_its_first_byte(tmp_path):
    from personalclaw.atomic_write import private_file

    target = tmp_path / "new" / "deeper" / "archive.bin"
    with private_file(target) as fh:
        temps = list(target.parent.glob("*.tmp"))
        assert len(temps) == 1, temps
        assert _mode(temps[0]) == 0o600, "the temp file existed at a wider mode"
        assert temps[0].name.startswith("archive.bin."), temps[0].name
        assert not target.exists(), "the file appeared under its name before it was whole"
        fh.write(b"payload")

    assert target.read_bytes() == b"payload"
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700
    assert _mode(target.parent.parent) == 0o700
    assert not list(target.parent.glob("*.tmp"))


def test_a_failed_private_write_leaves_the_old_file_and_no_temp(tmp_path):
    from personalclaw.atomic_write import private_file

    target = tmp_path / "archive.bin"
    target.write_bytes(b"before")

    with pytest.raises(RuntimeError), private_file(target) as fh:
        fh.write(b"half")
        raise RuntimeError("the disk filled")

    assert target.read_bytes() == b"before"
    assert not list(tmp_path.glob("*.tmp"))


def test_ordinary_text_round_trips_through_the_private_writer(tmp_path):
    from personalclaw.atomic_write import write_private_file

    target = tmp_path / "manifest.json"
    write_private_file(target, '{"note": "café ☕"}\n')

    assert json.loads(target.read_text(encoding="utf-8")) == {"note": "café ☕"}
    assert _mode(target) == 0o600
