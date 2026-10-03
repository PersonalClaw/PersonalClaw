"""A snapshot or an export that fails reads as failed, beside what a restore can still bring back.

A scheduled snapshot into a folder this account may not write to failed every night, and the
Backups page kept "Last run" at the previous success: a failed run stamps nothing, so it stays due
and is tried again, and that stamp was the only record there was. Its words were a gateway log line
and a security log row. A person could believe a recent backup existed.

Every run that happened is now recorded (when, whether it worked, the failure's code and the
system's own words, how long the streak has lasted), apart from the schedule stamp. The Backups
page, the Doctor, ``personalclaw doctor`` and the note all read it, the note is raised once when a
run starts failing (again for a new reason) and once when it works again, and the last success and
the newest snapshot a restore can bring back stay beside the failure.

The unwritable folders are real folders made read-only. The full disk is a stand-in that raises
what a write on a full disk raises.
"""

from __future__ import annotations

import errno
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.config.loader import DurabilityConfig, config_dir
from personalclaw.durability import service
from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import DoctorContext, Tier

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes into a read-only folder"
)

#: A time for the records that are not read against the clock.
T0 = 1_800_000_000.0


class _Notes:
    """A `DashboardState.notify`-shaped recorder."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str, dict]] = []

    def __call__(self, kind, title, body, *, meta=None, **_):
        self.seen.append((kind, title, body, dict(meta or {})))

    def titled(self, title: str) -> list[tuple[str, str, str, dict]]:
        return [n for n in self.seen if n[1] == title]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """This test's home, its snapshots written to a folder of its own outside it."""
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    # The export and the snapshot are the jobs here: the memory history and the drill are off.
    monkeypatch.setattr(
        service, "_cfg", lambda: DurabilityConfig(time_travel=False, restore_drills=False)
    )
    root = Path(config_dir())
    (root / "config.json").write_text(json.dumps({"snapshot_dir": str(tmp_path / "backups")}))
    (root / "tasks").mkdir()
    (root / "tasks" / "t-1.json").write_text('{"id": "t-1", "title": "a task worth keeping"}')
    return root


@pytest.fixture
def read_only(tmp_path):
    """Make a folder read-only for the rest of the test (and writable again after it)."""
    made: list[Path] = []

    def _make(folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o500)
        made.append(folder)
        # The control: a write into it really is refused here.
        with pytest.raises(PermissionError):
            (folder / "probe").write_text("x")
        return folder

    yield _make
    for folder in made:
        os.chmod(folder, 0o700)


def _full_disk(*_a, **_k):
    raise OSError(errno.ENOSPC, "No space left on device")


def _status(job: str) -> dict:
    return service.status()[job]


# ── the snapshot ──────────────────────────────────────────────────────────────


@needs_permissions
def test_a_snapshot_into_a_folder_it_may_not_write_reads_as_failed(home, tmp_path, read_only):
    t0 = time.time() - 2 * service.NIGHTLY_SECS
    service.run_due_jobs(now=t0)
    good = _status("snapshot")
    assert good["ok"] is True and good["last_run"] == t0 and good["last_success"] == t0
    newest = good["newest"]["name"]
    assert newest.startswith("personalclaw-snapshot-")

    read_only(tmp_path / "backups")
    t1 = t0 + service.NIGHTLY_SECS + 1
    service.run_due_jobs(now=t1)

    snapshot = _status("snapshot")
    assert snapshot["last_run"] == t1, "the failed run happened, so it is the last run"
    assert snapshot["ok"] is False, "a failed run must not read as a snapshot"
    assert snapshot["last_success"] == t0, "the last snapshot that worked stays beside it"
    assert snapshot["due"] is True, "a failed snapshot is tried again at the next check"
    problem = snapshot["problem"]
    assert problem["code"] == "unwritable"
    message = problem["message"]
    assert message.startswith("The snapshot was not taken")
    assert str(tmp_path / "backups") in message
    assert "permission denied" in message
    assert "Errno" not in message and ".tmp" not in message, "a sentence, not the exception"
    assert problem["remedy"] and problem["failures"] == 1 and problem["since"] == t1
    # What a restore can still bring back is the snapshot that worked.
    assert snapshot["newest"]["name"] == newest


def test_a_full_disk_reads_as_failed_and_says_which_disk(home, tmp_path, monkeypatch):
    import personalclaw.snapshot as snap_mod

    monkeypatch.setattr(snap_mod, "_write_archive", _full_disk)
    service.run_due_jobs(now=T0)

    snapshot = _status("snapshot")
    assert snapshot["ok"] is False and snapshot["last_success"] == 0
    assert snapshot["problem"]["code"] == "disk_full"
    assert snapshot["problem"]["message"] == (
        f"The snapshot was not taken: the disk that holds {tmp_path / 'backups'} is full."
    )
    assert snapshot["newest"] is None, "nothing was ever taken, so nothing can be restored"
    assert service.restorable(snapshot) == (
        f"There is no snapshot in {tmp_path / 'backups'} to restore from."
    )


def test_a_failed_run_now_snapshot_reads_as_failed_and_says_why_in_its_answer(home, monkeypatch):
    import personalclaw.snapshot as snap_mod

    monkeypatch.setattr(snap_mod, "_write_archive", _full_disk)
    result = service.run_nightly_snapshot()
    assert result.ok is False
    # Run now shows `detail` as its answer: the same sentence the page keeps.
    assert result.detail.startswith("The snapshot was not taken: the disk that holds")


# ── the export ────────────────────────────────────────────────────────────────


@needs_permissions
def test_an_export_into_a_folder_it_may_not_write_reads_as_failed(home, read_only):
    t0 = time.time() - 2 * service.HOURLY_SECS
    service.run_due_jobs(now=t0)
    assert _status("export")["ok"] is True

    (home / "tasks" / "t-2.json").write_text('{"id": "t-2", "title": "a second task"}')
    read_only(home / "shards")
    t1 = t0 + service.HOURLY_SECS + 1
    service.run_due_jobs(now=t1)

    export = _status("export")
    assert export["ok"] is False and export["last_run"] == t1
    assert export["last_success"] == t0
    assert export["due"] is True
    assert export["problem"]["code"] == "unwritable"
    assert export["problem"]["message"].startswith(
        f"The export was not written: PersonalClaw may not write to {home / 'shards'}"
    )


def test_a_failed_export_is_tried_again_and_not_read_as_nothing_changed(home, monkeypatch):
    """The change detection recorded what it saw before the export ran, so an export that failed
    part way read "nothing changed" on its next try, and that read as a success: the stores it never
    wrote stayed out of the export, and the page said the export was fine."""
    from personalclaw.durability import shards

    service.run_due_jobs(now=T0)
    (home / "tasks" / "t-2.json").write_text('{"id": "t-2", "title": "a second task"}')

    with monkeypatch.context() as patched:
        patched.setattr(shards, "_write_shard", _full_disk)
        failed = service.run_incremental_export()
    assert failed.ok is False

    again = service.run_incremental_export()
    assert again.ok, again.detail
    assert again.extra["entries_exported"] >= 1, "the store it never wrote is exported now"
    assert "t-2" in (home / "shards" / "tasks" / "entities.jsonl").read_text()


def test_an_export_that_leaves_a_file_out_stays_failed_until_the_file_is_carried(home):
    (home / "tasks" / "t-9.json").write_text("{not json")
    t0 = time.time() - 100
    service.run_due_jobs(now=t0)

    export = _status("export")
    assert export["ok"] is False
    assert export["problem"]["code"] == "left_out"
    assert export["problem"]["message"] == (
        "The export ran, but 1 file could not be exported: tasks/t-9.json (not valid JSON)."
    )

    # Another try in five minutes cannot carry it, so the schedule is kept…
    assert export["due"] is False
    # …and an hour on, the store is read again: still changed, since its file was never carried,
    # and still left out. One streak, not a recovery — though the export's own security log line
    # has changed another store, which that run exports whole.
    t1 = t0 + service.HOURLY_SECS + 1
    service.run_due_jobs(now=t1)
    export = _status("export")
    assert export["ok"] is False and export["problem"]["code"] == "left_out"
    assert export["problem"]["failures"] == 2
    assert export["due"] is False

    (home / "tasks" / "t-9.json").write_text('{"id": "t-9", "title": "mended"}')
    t2 = t1 + service.HOURLY_SECS + 1
    service.run_due_jobs(now=t2)
    export = _status("export")
    assert export["ok"] is True and export["problem"] is None and export["last_success"] == t2


# ── the note ──────────────────────────────────────────────────────────────────


def test_a_failing_snapshot_is_announced_once_and_its_recovery_once(home, monkeypatch):
    import personalclaw.snapshot as snap_mod

    notes = _Notes()
    with monkeypatch.context() as patched:
        patched.setattr(snap_mod, "_write_archive", _full_disk)
        service.run_due_jobs(now=T0, notifier=notes)
        service.run_due_jobs(now=T0 + service.TICK_SECS, notifier=notes)  # tried again

    failed = notes.titled("Snapshot failed")
    assert len(failed) == 1, "one note per failing streak, not one every five minutes"
    kind, _title, body, meta = failed[0]
    assert kind == "warning"
    assert body.startswith("The snapshot was not taken: the disk that holds")
    assert "Free some space" in body
    assert "There is no snapshot in " in body and body.endswith(" to restore from.")
    assert meta.get("statusUrl") == "#/settings/durability"
    assert _status("snapshot")["problem"]["failures"] == 2

    t2 = T0 + 2 * service.TICK_SECS
    service.run_due_jobs(now=t2, notifier=notes)

    snapshot = _status("snapshot")
    assert snapshot["ok"] is True and snapshot["problem"] is None
    assert snapshot["last_success"] == t2
    recovered = notes.titled("Snapshots are working again")
    assert len(recovered) == 1 and recovered[0][0] == "info"
    assert recovered[0][2].startswith("Created personalclaw-snapshot-")
    assert notes.titled("Snapshot failed") == failed, "a recovery is not another failure"


@needs_permissions
def test_a_failure_note_names_the_snapshot_that_can_still_be_restored(home, tmp_path, read_only):
    service.run_due_jobs(now=T0)
    newest = _status("snapshot")["newest"]["name"]

    read_only(tmp_path / "backups")
    notes = _Notes()
    service.run_due_jobs(now=T0 + service.NIGHTLY_SECS + 1, notifier=notes)

    (failed,) = notes.titled("Snapshot failed")
    assert f"The newest snapshot a restore can bring back is {newest}." in failed[2]


def test_a_new_reason_is_announced_again(home, tmp_path, monkeypatch):
    import personalclaw.snapshot as snap_mod

    notes = _Notes()

    def refused(*_a, **_k):
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path / "backups" / "x"))

    with monkeypatch.context() as patched:
        patched.setattr(snap_mod, "_write_archive", refused)
        service.run_due_jobs(now=T0, notifier=notes)
    with monkeypatch.context() as patched:
        patched.setattr(snap_mod, "_write_archive", _full_disk)
        service.run_due_jobs(now=T0 + service.TICK_SECS, notifier=notes)

    assert len(notes.titled("Snapshot failed")) == 2, "a different reason is news"
    problem = _status("snapshot")["problem"]
    assert problem["code"] == "disk_full"
    assert problem["failures"] == 2 and problem["since"] == T0, "still one streak"


def test_a_backup_that_works_is_stamped_and_raises_no_note(home):
    """The control: a run that works records itself, as a success, and says nothing."""
    notes = _Notes()
    now = time.time()
    service.run_due_jobs(now=now, notifier=notes)

    for job in ("export", "snapshot"):
        entry = _status(job)
        assert entry["ok"] is True, job
        assert entry["last_run"] == entry["last_success"] == now, job
        assert entry["problem"] is None, job
        assert entry["due"] is False, job
    assert notes.seen == [], "a working backup is not news"


def test_a_failed_export_notes_once_too(home, monkeypatch):
    from personalclaw.durability import shards

    notes = _Notes()
    with monkeypatch.context() as patched:
        patched.setattr(shards, "_write_shard", _full_disk)
        service.run_due_jobs(now=T0, notifier=notes)
        service.run_due_jobs(now=T0 + service.TICK_SECS, notifier=notes)
    (failed,) = notes.titled("Export failed")
    assert failed[0] == "warning"
    assert failed[2].startswith(
        f"The export was not written: the disk that holds {home / 'shards'}"
    )

    service.run_due_jobs(now=T0 + 2 * service.TICK_SECS, notifier=notes)
    (recovered,) = notes.titled("Export is working again")
    assert recovered[0] == "info"


# ── Run now ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_now_records_a_failure_and_notes_it_through_the_same_path(home, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    import personalclaw.snapshot as snap_mod
    from personalclaw.dashboard.handlers import durability as handlers

    notes = _Notes()
    app = web.Application()
    app["state"] = SimpleNamespace(notify=notes)
    app.router.add_post("/api/durability/run", handlers.api_durability_run)
    monkeypatch.setattr(snap_mod, "_write_archive", _full_disk)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/durability/run", json={"job": "snapshot"})
        body = await resp.json()
    assert resp.status == 200 and body["ok"] is False
    assert body["detail"].startswith("The snapshot was not taken")

    snapshot = _status("snapshot")
    assert snapshot["ok"] is False and snapshot["problem"]["code"] == "disk_full"
    assert len(notes.titled("Snapshot failed")) == 1


# ── one note path, the drill included ────────────────────────────────────────


def test_every_drill_verdict_is_still_news(home):
    """The monthly drill says every verdict, passed or failed: it is the proof a backup restores."""
    notes = _Notes()

    def drill(ok: bool) -> service.JobResult:
        return service.JobResult(
            "restore_drill",
            ok=ok,
            detail="snap.tar.gz: 3 databases verified" if ok else "snap.tar.gz: archive is empty",
            extra={"snapshot": "snap.tar.gz", "databases_checked": 3 if ok else 0},
        )

    service.persist_job_result("drill", drill(False), at=T0, notifier=notes)
    service.persist_job_result("drill", drill(False), at=T0 + 1, notifier=notes)
    service.persist_job_result("drill", drill(True), at=T0 + 2, notifier=notes)
    service.persist_job_result("drill", drill(True), at=T0 + 3, notifier=notes)

    assert [(n[0], n[1]) for n in notes.seen] == [
        ("warning", "Backup restore drill FAILED"),
        ("warning", "Backup restore drill FAILED"),
        ("info", "Backup restore drill passed"),
        ("info", "Backup restore drill passed"),
    ]
    assert notes.seen[0][2] == "snap.tar.gz: archive is empty"
    assert all(n[3].get("statusUrl") == "#/settings/durability" for n in notes.seen)


# ── a record written before runs kept an outcome ─────────────────────────────


def test_a_record_from_before_reads_as_the_success_it_stamped():
    """A schedule stamp was written only by a success: it is the last run known to have worked."""
    service.save_state({"last_export": T0, "last_snapshot": T0 + 1})
    for job, at in (("export", T0), ("snapshot", T0 + 1)):
        entry = _status(job)
        assert entry["last_run"] == at and entry["last_success"] == at, job
        assert entry["ok"] is True and entry["problem"] is None, job


# ── the Doctor, and `personalclaw doctor` ────────────────────────────────────


def test_the_backups_probe_is_a_durability_capability_probe() -> None:
    probe = {p.id: p for p in doctor.all_probes()}.get("durability.backups")
    assert probe is not None, "the probe must be registered, not just defined"
    assert probe.capability == "durability"
    assert probe.tier is Tier.CAPABILITY


@pytest.mark.asyncio
@needs_permissions
async def test_the_doctor_reports_a_failing_snapshot_and_what_can_still_be_restored(
    home, tmp_path, read_only
):
    service.run_due_jobs(now=T0)
    newest = _status("snapshot")["newest"]["name"]
    read_only(tmp_path / "backups")
    service.run_due_jobs(now=T0 + service.NIGHTLY_SECS + 1)
    service.run_due_jobs(now=T0 + service.NIGHTLY_SECS + 1 + service.TICK_SECS)

    res = await doctor._probe_backups(DoctorContext())
    assert res.ok is False
    assert res.detail.startswith("The snapshot was not taken: PersonalClaw may not write to")
    assert "The last 2 runs all failed." in res.detail
    assert f"The newest snapshot a restore can bring back is {newest}." in res.detail
    assert res.remedy.startswith("No automatic fix — make that folder writable")
    assert res.evidence["failing"] == ["snapshot"]


@pytest.mark.asyncio
async def test_the_doctor_passes_backups_that_work_and_says_when_they_are_off(home, monkeypatch):
    res = await doctor._probe_backups(DoctorContext())
    assert res.ok is True and res.detail == "no snapshot has been taken yet"

    service.run_due_jobs()
    res = await doctor._probe_backups(DoctorContext())
    assert res.ok is True, res.detail
    assert res.detail.startswith("last snapshot ")

    monkeypatch.setattr(service, "enabled", lambda: False)
    res = await doctor._probe_backups(DoctorContext())
    assert res.ok is True
    assert res.detail == "automatic backups are off: a snapshot is taken only when you run one"


def test_personalclaw_doctor_prints_what_failed_and_what_to_do(home, monkeypatch, capsys):
    import personalclaw.snapshot as snap_mod
    from personalclaw.cli_doctor import _doctor_backups

    monkeypatch.setattr(snap_mod, "_write_archive", _full_disk)
    service.run_due_jobs(now=T0)

    _doctor_backups()
    out = capsys.readouterr().out
    assert "\nBackups\n" in out
    assert "❌ The snapshot was not taken: the disk that holds" in out
    assert "There is no snapshot in " in out
    assert "\n               No automatic fix — free some space on that disk.\n" in out
