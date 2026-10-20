"""A new snapshot never replaces the only verified one, a run says what it removed, and the drill
line follows the file it checked.

A snapshot taken from Run now late in the day removed that day's earlier snapshot, the one a restore
drill had passed on: the daily tier keeps the newest snapshot of each day. The run reported "kept 1,
pruned 1" and nothing else, and the archive kept saying "Last restore drill passed" over the name of
the file it had just deleted, until the next drill.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.durability import retention, service

DAY = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _snap(directory: Path, when: datetime, *, size: int = 100) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"personalclaw-snapshot-{when:%Y%m%dT%H%M%SZ}.tar.gz"
    path.write_bytes(b"x" * size)
    return path


def _drill(name: str, *, ok: bool) -> service.JobResult:
    detail = f"{name}: 6 database(s) verified" if ok else f"{name}: integrity_check said bad"
    return service.JobResult(
        "restore_drill", ok=ok, detail=detail, extra={"snapshot": name, "databases_checked": 6}
    )


def _earlier_today() -> datetime:
    """Today's first second (UTC): the same day as a snapshot taken now, and older than it."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now - midnight < timedelta(seconds=2):
        time.sleep(2)  # a snapshot taken in midnight's own second would share its name
    return midnight


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    (h / "config.json").write_text("{}")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    # The workspace reads its own variable first; pin it inside the scratch folder too.
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    return h


# ── the plan ─────────────────────────────────────────────────────────────────


def test_a_same_day_snapshot_after_a_verified_one_keeps_the_verified_one(tmp_path):
    morning = _snap(tmp_path, DAY.replace(hour=7, minute=25, second=55))
    evening = _snap(tmp_path, DAY.replace(hour=23, minute=26, second=10))

    plan = retention.plan_retention(retention.list_snapshots(tmp_path), verified=morning.name)

    assert [s.path for s in plan.keep] == [evening, morning]
    assert plan.prune == []
    assert plan.held is not None and plan.held.path == morning


def test_unverified_the_day_keeps_only_its_newest_and_says_why(tmp_path):
    """The control for the test above: the same two files, nothing verified, the rule it bends."""
    morning = _snap(tmp_path, DAY.replace(hour=7, minute=25, second=55))
    evening = _snap(tmp_path, DAY.replace(hour=23, minute=26, second=10))

    plan = retention.plan_retention(retention.list_snapshots(tmp_path), verified=None)

    assert [s.path for s in plan.keep] == [evening]
    assert plan.reasons == {morning.name: retention.SAME_DAY}
    assert plan.held is None


def test_the_hold_is_one_snapshot_and_moves_to_a_newer_verified_one(tmp_path):
    first = _snap(tmp_path, DAY.replace(hour=1))
    second = _snap(tmp_path, DAY.replace(hour=9))
    third = _snap(tmp_path, DAY.replace(hour=17))
    snaps = retention.list_snapshots(tmp_path)

    held = retention.plan_retention(snaps, verified=second.name)
    assert [s.path for s in held.keep] == [third, second]
    assert [s.path for s in held.prune] == [first], "only the verified one is held, not every one"

    moved = retention.plan_retention(snaps, verified=third.name)
    assert [s.path for s in moved.keep] == [third]
    assert moved.held is None, "once the newest is verified, the older one goes"


def test_with_every_tier_at_zero_nothing_is_held_and_each_removal_says_so(tmp_path):
    verified = _snap(tmp_path, DAY.replace(hour=7))
    _snap(tmp_path, DAY.replace(hour=23))

    plan = retention.plan_retention(
        retention.list_snapshots(tmp_path), verified=verified.name, daily=0, weekly=0, monthly=0
    )

    assert plan.keep == [] and plan.held is None, "a budget of none trades it for nothing"
    assert set(plan.reasons.values()) == {retention.NONE_KEPT}


def test_each_removed_snapshot_names_the_tier_that_replaced_it(tmp_path):
    _snap(tmp_path, datetime(2026, 7, 28, 23, tzinfo=timezone.utc))
    same_day = _snap(tmp_path, datetime(2026, 7, 28, 1, tzinfo=timezone.utc))
    _snap(tmp_path, datetime(2026, 7, 24, 12, tzinfo=timezone.utc))
    same_week = _snap(tmp_path, datetime(2026, 7, 22, 12, tzinfo=timezone.utc))
    _snap(tmp_path, datetime(2026, 6, 20, 12, tzinfo=timezone.utc))
    same_month = _snap(tmp_path, datetime(2026, 6, 2, 12, tzinfo=timezone.utc))
    aged = _snap(tmp_path, datetime(2026, 4, 10, 12, tzinfo=timezone.utc))

    plan = retention.plan_retention(
        retention.list_snapshots(tmp_path), verified=None, daily=1, weekly=2, monthly=2
    )

    assert plan.reasons == {
        same_day.name: retention.SAME_DAY,
        same_week.name: retention.SAME_WEEK,
        same_month.name: retention.SAME_MONTH,
        aged.name: retention.AGED_OUT,
    }


# ── the verified record ──────────────────────────────────────────────────────


def test_a_failed_drill_on_a_newer_snapshot_does_not_erase_the_verified_one(home):
    service.persist_job_result("drill", _drill("snap-a.tar.gz", ok=True), at=1_800_000_000.0)
    service.persist_job_result("drill", _drill("snap-b.tar.gz", ok=False), at=1_800_000_100.0)

    assert service.last_drill()["archive"] == "snap-b.tar.gz"
    assert service.last_drill()["ok"] is False
    verified = service.last_verified()
    assert verified["archive"] == "snap-a.tar.gz"
    assert verified["at"] == 1_800_000_000.0
    assert verified["databases_checked"] == 6


def test_a_pass_recorded_before_passes_had_their_own_record_is_still_the_verified_one(home):
    """The backfill: a home whose last drill passed before this record existed."""
    service.save_state(
        {
            "last_drill": 1_700_000_000.0,
            "last_drill_ok": True,
            "last_drill_archive": "snap-a.tar.gz",
            "last_drill_detail": "snap-a.tar.gz: 6 database(s) verified",
            "last_drill_databases": 6,
        }
    )
    assert service.last_verified()["archive"] == "snap-a.tar.gz"

    service.persist_job_result("drill", _drill("snap-b.tar.gz", ok=False), at=1_700_000_100.0)
    assert service.last_verified()["archive"] == "snap-a.tar.gz", "the failure erased the pass"


def test_a_home_with_no_passed_drill_has_no_verified_snapshot(home):
    assert service.last_verified()["archive"] == ""
    service.persist_job_result("drill", _drill("snap-b.tar.gz", ok=False))
    assert service.last_verified()["archive"] == ""


# ── a snapshot run ───────────────────────────────────────────────────────────


def test_a_manual_snapshot_after_a_verified_one_keeps_it_and_says_so(home):
    verified = _snap(home / "snapshots", _earlier_today())
    service.persist_job_result("drill", _drill(verified.name, ok=True))

    result = service.run_nightly_snapshot()

    assert result.ok, result.detail
    assert verified.exists(), "the snapshot the drill passed on was removed"
    assert result.extra["held"] == verified.name
    taken = result.extra["taken"]
    assert taken and taken != verified.name
    assert result.detail.startswith(f"Created {taken} (")
    assert "No snapshot was removed." in result.detail
    assert f"Kept {verified.name} as well: {retention.HELD}." in result.detail
    # The run record the Backups page shows under the job's last run.
    service.persist_job_result("snapshot", result)
    assert service.status()["snapshot"]["detail"] == result.detail


def test_the_result_names_each_removed_snapshot_and_why(home, monkeypatch):
    earlier = _snap(home / "snapshots", _earlier_today())
    audits: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        service, "_audit", lambda event, resources, **kw: audits.append((event, resources, kw))
    )

    result = service.run_nightly_snapshot()

    assert result.ok, result.detail
    assert not earlier.exists()
    assert earlier.name in result.detail, "the run did not say which snapshot it removed"
    assert f"Removed {earlier.name}, {retention.SAME_DAY}." in result.detail
    assert "Kept" not in result.detail, "nothing was verified, so nothing is held"
    # The audit row carries the sentence, and every name with its reason beside it.
    event, resources, kw = audits[-1]
    assert event == "durability_snapshot"
    assert resources == result.detail
    assert kw["metadata"]["reasons"] == {earlier.name: retention.SAME_DAY}
    assert kw["metadata"]["taken"] == result.extra["taken"]


def test_the_cli_keeps_the_newest_verified_snapshot_beyond_its_count(home, tmp_path, capsys):
    from personalclaw.snapshot import snapshot_main

    out = tmp_path / "out"
    oldest = _snap(out, datetime(2026, 1, 1, tzinfo=timezone.utc))
    middle = _snap(out, datetime(2026, 1, 2, tzinfo=timezone.utc))
    newest_fake = _snap(out, datetime(2026, 1, 3, tzinfo=timezone.utc))
    hand_named = out / "personalclaw-snapshot-hand-copied.tar.gz"
    hand_named.write_bytes(b"x")
    service.persist_job_result("drill", _drill(oldest.name, ok=True))

    assert snapshot_main([str(out), "--keep", "2"]) == 0

    assert oldest.exists(), "the --keep count removed the snapshot the drill passed on"
    assert newest_fake.exists() and not middle.exists()
    assert hand_named.exists(), "a name that is no snapshot time is never pruned"
    printed = capsys.readouterr().out
    assert f"Pruned: {middle.name} (older than the 2 newest)" in printed
    assert f"Kept {oldest.name} as well: {retention.HELD}." in printed


# ── the archive the Backups page reads ───────────────────────────────────────


def _archive_app() -> web.Application:
    from personalclaw.dashboard.handlers import durability as mod

    app = web.Application()
    app.router.add_get("/api/durability/archive", mod.api_durability_archive)
    return app


async def _archive() -> dict:
    async with TestClient(TestServer(_archive_app())) as client:
        resp = await client.get("/api/durability/archive")
        assert resp.status == 200
        return await resp.json()


@pytest.mark.asyncio
async def test_the_drill_line_follows_the_file_it_verified(home):
    snaps = home / "snapshots"
    gone = "personalclaw-snapshot-20261002T072555Z.tar.gz"
    left = _snap(snaps, DAY.replace(hour=23, minute=26, second=10))
    service.persist_job_result("drill", _drill(gone, ok=True))

    body = await _archive()
    assert (
        body["last_drill"].get("on_disk") is False
    ), "a pass on a file that is gone reads as current"
    assert [a["validate"] for a in body["archives"]] == [None], f"{left.name} was never verified"

    _snap(snaps, DAY.replace(hour=7, minute=25, second=55))
    body = await _archive()
    assert body["last_drill"]["on_disk"] is True
    verdicts = {a["name"]: a["validate"] for a in body["archives"]}
    assert verdicts[gone]["ok"] is True and verdicts[left.name] is None


@pytest.mark.asyncio
async def test_a_record_that_names_no_file_cannot_say_whether_it_is_on_disk(home):
    service.save_state({"last_drill": 1_700_000_000.0})
    body = await _archive()
    assert body["last_drill"]["on_disk"] is None


@pytest.mark.asyncio
async def test_the_archive_marks_the_held_snapshot_and_keeps_its_pass_after_a_failed_drill(home):
    snaps = home / "snapshots"
    verified = _snap(snaps, DAY.replace(hour=7))
    newer = _snap(snaps, DAY.replace(hour=12))
    service.persist_job_result("drill", _drill(verified.name, ok=True), at=1_800_000_000.0)
    service.persist_job_result("drill", _drill(newer.name, ok=False), at=1_800_000_100.0)

    body = await _archive()

    rows = {a["name"]: a for a in body["archives"]}
    assert rows[verified.name]["validate"] is not None, "the pass was lost to the later failure"
    assert rows[verified.name]["validate"]["ok"] is True
    assert rows[verified.name]["retained"] is True and rows[verified.name]["held"] is True
    assert rows[newer.name]["validate"]["ok"] is False and rows[newer.name]["held"] is False
    assert body["would_prune"] == [], "the preview must hold what the next run holds"
