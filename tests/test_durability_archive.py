"""The archive browser's two data sources: a snapshot's own manifest, and the last drill.

§6 asks the archive list to show per-domain row counts "from the manifest" and the
validate status "from the last drill". Both were unavailable before DAS-10 — the snapshot
manifest carried a hand-written `contents` blob with no domains, and only the drill's
TIMESTAMP was persisted, so a passed drill and a failed one rendered identically.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from personalclaw.durability import archive as arch


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    return h


# ── the manifest sidecar ─────────────────────────────────────────────────────


def _tar_with_manifest(path: Path, manifest: dict) -> None:
    stage = path.parent / "stage"
    (stage / "inner").mkdir(parents=True, exist_ok=True)
    (stage / "inner" / "MANIFEST.json").write_text(json.dumps(manifest))
    (stage / "inner" / "config.json").write_text("{}")
    with tarfile.open(str(path), "w:gz") as tar:
        tar.add(str(stage / "inner"), arcname="inner")


def test_domain_counts_come_from_the_sidecar_when_present(tmp_path):
    tar = tmp_path / "snap.tar.gz"
    _tar_with_manifest(tar, {"version": 3, "domains": {"memory": {"files": 1, "rows": 7}}})
    arch.write_sidecar(tar, {"version": 3, "domains": {"memory": {"files": 1, "rows": 99}}})
    # The sidecar wins — it is the cheap read, and it is written from the same bytes.
    assert arch.domain_counts(tar) == {"memory": {"files": 1, "rows": 99}}


def test_a_missing_sidecar_is_backfilled_from_the_tar_once(tmp_path):
    """An archive taken before v3 gets its sidecar created on first read.

    An idempotent backfill, not a migration: the archive is never rewritten, and reading
    it twice produces the same answer.
    """
    tar = tmp_path / "snap.tar.gz"
    _tar_with_manifest(tar, {"version": 3, "domains": {"work": {"files": 2, "rows": 2}}})
    assert not arch.sidecar_path(tar).exists()

    first = arch.domain_counts(tar)
    assert first == {"work": {"files": 2, "rows": 2}}
    assert arch.sidecar_path(tar).exists(), "the backfill must persist"
    assert arch.domain_counts(tar) == first, "reading again must not change the answer"


def test_an_archive_with_no_manifest_reports_none_not_empty(tmp_path):
    """`None` (nothing recorded) and `{}` (recorded nothing) must stay distinguishable.

    Collapsing them would make an EMPTY backup render identically to an unlabelled one —
    the worst confusion a backup surface can offer.
    """
    tar = tmp_path / "bare.tar.gz"
    stage = tmp_path / "bare"
    stage.mkdir()
    (stage / "x.txt").write_text("hi")
    with tarfile.open(str(tar), "w:gz") as t:
        t.add(str(stage), arcname="bare")
    assert arch.domain_counts(tar) is None

    _tar_with_manifest(tmp_path / "empty.tar.gz", {"version": 3, "domains": {}})
    assert arch.domain_counts(tmp_path / "empty.tar.gz") == {}


def test_a_corrupt_archive_is_listed_rather_than_raising(tmp_path):
    """The browser's job is to SHOW a corrupt archive so the user knows it exists."""
    tar = tmp_path / "corrupt.tar.gz"
    tar.write_bytes(b"this is not a tarball")
    assert arch.domain_counts(tar) is None


def test_retention_removes_the_sidecar_with_its_archive(tmp_path):
    """An orphaned sidecar would outlive its archive and could be mistaken for a
    future snapshot's manifest if a name were ever reused."""
    from datetime import datetime, timedelta, timezone

    from personalclaw.durability import retention

    directory = tmp_path / "snaps"
    directory.mkdir()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    made: list[Path] = []
    for i in range(30):
        stamp = (base - timedelta(days=i)).strftime("%Y%m%dT%H%M%SZ")
        p = directory / f"personalclaw-snapshot-{stamp}.tar.gz"
        p.write_bytes(b"x" * 32)
        arch.write_sidecar(p, {"version": 3, "domains": {}})
        made.append(p)

    plan = retention.apply_retention(directory, daily=2, weekly=1, monthly=1)
    assert plan["pruned"], "the fixture must actually prune something"
    for p in made:
        # A sidecar exists exactly when its archive does.
        assert arch.sidecar_path(p).exists() == p.exists(), p.name


# ── the snapshot manifest's domains block ────────────────────────────────────


def test_snapshot_manifest_records_real_per_domain_row_counts(home, tmp_path):
    """Rows, not files: a `rows` count that just counted files would be a lie for a
    database, and rows are what §6 asks the browser to show."""
    import argparse

    from personalclaw.snapshot import snapshot_main

    conn = sqlite3.connect(str(home / "memory.db"))
    try:
        conn.execute("CREATE TABLE semantic_memory (key TEXT PRIMARY KEY, value_json TEXT)")
        conn.executemany(
            "INSERT INTO semantic_memory (key, value_json) VALUES (?, ?)",
            [(f"k{i}", "{}") for i in range(5)],
        )
        conn.commit()
    finally:
        conn.close()
    (home / "tasks").mkdir()
    for i in range(3):
        (home / "tasks" / f"t{i}.json").write_text(json.dumps({"id": f"t{i}"}))
    (home / "config.json").write_text("{}")

    out = tmp_path / "out"
    code = snapshot_main(
        parsed=argparse.Namespace(output_dir=str(out), keep=5, list_snapshots=False)
    )
    assert code == 0
    tars = sorted(out.glob("personalclaw-snapshot-*.tar.gz"))
    assert len(tars) == 1

    manifest = arch.read_manifest(tars[0])
    assert manifest is not None and manifest["version"] == 3
    domains = manifest["domains"]
    assert domains["memory"]["rows"] == 5, domains
    assert domains["work"]["rows"] == 3, domains
    assert domains["config"]["files"] >= 1, domains
    # The sidecar is written at snapshot time, not lazily on first read.
    assert arch.sidecar_path(tars[0]).exists()


# ── the drill verdict ────────────────────────────────────────────────────────


def test_last_drill_reports_not_run_on_a_fresh_home(home):
    from personalclaw.durability import service

    drill = service.last_drill()
    assert drill["ran"] is False
    assert drill["ok"] is None, "an unrun drill must never look like a pass"


def test_a_stamp_without_a_recorded_outcome_reports_unknown(home):
    """A `last_drill` written before outcomes were recorded reads as UNKNOWN, not as ok.

    The alternative — defaulting the missing field to True — would show green for every
    home that drilled once before this atom landed.
    """
    from personalclaw.durability import service

    service.save_state({"last_drill": 1_700_000_000.0})
    drill = service.last_drill()
    assert drill["ran"] is True
    assert drill["ok"] is None


@pytest.mark.parametrize("ok", [True, False])
def test_a_drill_result_round_trips_through_state(home, ok):
    from personalclaw.durability import service

    result = service.JobResult(
        "restore_drill",
        ok=ok,
        detail="snap-1: 3 database(s) verified" if ok else "snap-1: integrity_check said bad",
        extra={"snapshot": "snap-1.tar.gz", "databases_checked": 3},
    )
    service.persist_drill_result(result, at=1_800_000_000.0)
    drill = service.last_drill()
    assert drill["ran"] is True
    assert drill["ok"] is ok
    assert drill["at"] == 1_800_000_000.0
    assert drill["archive"] == "snap-1.tar.gz"
    assert drill["databases_checked"] == 3
    assert "snap-1" in drill["detail"]


def test_the_scheduled_tick_records_the_verdict_too(home, monkeypatch):
    """Both drill entry points persist the verdict.

    Without this the on-demand "Verify a restore" button and the monthly schedule would
    disagree about what the archive browser shows — `run_due_jobs`' trailing `save_state`
    would clobber whatever a drill had recorded on its own.
    """
    from personalclaw.durability import service

    monkeypatch.setattr(
        service,
        "run_restore_drill",
        lambda **kw: service.JobResult(
            "restore_drill", ok=False, detail="boom", extra={"snapshot": "s.tar.gz"}
        ),
    )
    service.run_due_jobs(force="drill", now=1_900_000_000.0)
    drill = service.last_drill()
    assert drill["ok"] is False
    assert drill["archive"] == "s.tar.gz"
    assert drill["at"] == 1_900_000_000.0
