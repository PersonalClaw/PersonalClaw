"""The security event log rotates by size, keeps its rotated files for its retention, and a
snapshot carries every one of them.

Measured on day 8: `security_events.jsonl` grew ~5 MB an hour and nothing ever rotated it — the
only bound was a prune that DELETED the oldest rows past a 50,000-entry cap. The manual rotate
renamed the log to `security_events.<ts>.bak.jsonl` beside it, a path no inventory entry
claimed, and a settings validation found that archive missing from snapshots. So the retained
trail was either being erased or left out of the backup that is meant to preserve it.
"""

from __future__ import annotations

import json
import os
import tarfile
import time
from pathlib import Path

import pytest

import personalclaw.sel as sel_mod
from personalclaw.sel import SecurityEventLog


@pytest.fixture(autouse=True)
def _fresh_singleton():
    SecurityEventLog._instance = None
    SecurityEventLog._initialized = False
    yield
    SecurityEventLog._instance = None
    SecurityEventLog._initialized = False


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _archives(home: Path) -> list[Path]:
    return sorted((home / "sel_archive").glob("security_events.*.jsonl"))


def test_the_live_log_rotates_into_the_archive_at_its_size_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(sel_mod, "_ROTATE_BYTES", 4096)
    log = SecurityEventLog(base_dir=tmp_path)
    for i in range(60):
        log.log_api_access(caller="u", operation=f"op{i}", outcome="denied", resources="/api/x")

    archives = _archives(tmp_path)
    assert archives, "the live log reached its bound and nothing rotated"
    live = tmp_path / "security_events.jsonl"
    assert live.stat().st_size < 4096 + 1024
    # Nothing was lost to the rotation: every event is in the archive or the live file.
    ops = [r["operation"] for p in [*archives, live] for r in _rows(p)]
    assert [op for op in ops if op.startswith("op")] == [f"op{i}" for i in range(60)]
    # The rotation is itself on the record, in the fresh file.
    rotated = [
        r
        for r in _rows(live) + [r for p in archives for r in _rows(p)]
        if r["operation"] == "sel.rotated"
    ]
    assert rotated and rotated[0]["resources"].startswith("sel_archive/security_events.")


def test_a_rotation_starts_a_fresh_chain_that_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(sel_mod, "_ROTATE_BYTES", 2048)
    log = SecurityEventLog(base_dir=tmp_path)
    for i in range(30):
        log.log_api_access(caller="u", operation=f"op{i}", outcome="ok")
    total, valid = log.verify_integrity()
    assert total and total == valid


def test_retention_expires_old_archives_and_records_the_removal(tmp_path):
    log = SecurityEventLog(base_dir=tmp_path)
    log.log_api_access(caller="u", operation="first", outcome="ok")
    archive = Path(log.rotate(archive=True)["archive_path"])
    old = time.time() - 400 * 86400
    os.utime(archive, (old, old))
    fresh = Path(log.rotate(archive=True)["archive_path"])

    assert log.count_prunable() >= 1
    log.prune()

    assert not archive.exists(), "an archive past retention was kept"
    assert fresh.exists(), "an archive inside retention was removed"
    (row,) = [
        r
        for r in _rows(tmp_path / "security_events.jsonl")
        if r["operation"] == "sel.archive_expired"
    ]
    assert archive.name in row["resources"]


def test_the_archive_ceiling_removes_the_oldest_first(tmp_path, monkeypatch):
    log = SecurityEventLog(base_dir=tmp_path)
    made = []
    for i in range(3):
        log.log_api_access(caller="u", operation=f"batch{i}", outcome="ok", resources="x" * 400)
        path = Path(log.rotate(archive=True)["archive_path"])
        stamp = time.time() - (3 - i) * 60
        os.utime(path, (stamp, stamp))
        made.append(path)
    monkeypatch.setattr(
        sel_mod, "_ARCHIVE_MAX_BYTES", made[-1].stat().st_size + made[-2].stat().st_size
    )

    log.prune()

    assert [p.exists() for p in made] == [False, True, True]


def test_a_failed_archive_keeps_the_live_log(tmp_path, monkeypatch):
    """The old rotate DELETED the log when the rename failed — the audit trail lost to an I/O
    error. A failed archive now leaves every row where it was and says it did not rotate."""
    log = SecurityEventLog(base_dir=tmp_path)
    log.log_api_access(caller="u", operation="keep-me", outcome="ok")

    def _refuse(self):
        raise OSError("read-only archive")

    monkeypatch.setattr(SecurityEventLog, "_archive_live", _refuse)
    result = log.rotate(archive=True)

    assert result["rotated"] is False
    assert [r["operation"] for r in _rows(tmp_path / "security_events.jsonl")] == ["keep-me"]


def test_a_loose_archive_from_an_earlier_rotate_is_adopted(tmp_path):
    loose = tmp_path / "security_events.20260920T101010Z.bak.jsonl"
    loose.write_text('{"operation": "old"}\n')
    SecurityEventLog(base_dir=tmp_path)
    assert not loose.exists()
    assert (tmp_path / "sel_archive" / loose.name).read_text() == '{"operation": "old"}\n'


def test_the_archive_is_declared_state():
    from personalclaw.durability import inventory

    entry = inventory.claim_for("sel_archive/security_events.20260925T000000Z.jsonl")
    assert entry is not None and entry.id == "security_events_archive"
    assert entry.domain == inventory.DOMAIN_SECURITY


def test_a_snapshot_carries_the_rotated_files(tmp_path, monkeypatch):
    """The settings-validation finding, end to end: rotate, snapshot, look inside."""
    from personalclaw.snapshot import snapshot_main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    log = SecurityEventLog(base_dir=home)
    log.log_api_access(caller="u", operation="before-rotation", outcome="ok")
    archive = Path(log.rotate(archive=True)["archive_path"])
    log.log_api_access(caller="u", operation="after-rotation", outcome="ok")

    out = tmp_path / "snaps"
    assert snapshot_main([str(out)]) == 0
    (tarball,) = sorted(out.glob("*.tar.gz"))
    with tarfile.open(tarball) as tar:
        names = tar.getnames()
    assert any(n.endswith(f"sel_archive/{archive.name}") for n in names), names
    assert not any(n.endswith(".rotate.lock") for n in names), names
