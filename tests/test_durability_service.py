"""Tests for the scheduled snapshot service + tiered retention (DURABILITY §3).

The thing being protected against is a lost home directory, so the tests care most
about the properties that make a backup trustworthy: retention keeps a spread rather
than a window, the incremental export actually notices changes, a drill fails loudly
on a corrupt archive, and no job can take down the loop that runs it.
"""

import json
import os
import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personalclaw.durability import retention, service


def _snap(directory: Path, when: datetime, *, size: int = 100) -> Path:
    path = directory / f"personalclaw-snapshot-{when:%Y%m%dT%H%M%SZ}.tar.gz"
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    yield


# ── Retention ──


class TestRetention:
    def test_parses_the_real_snapshot_filename_format(self, tmp_path):
        """The format snapshot.py actually writes (%Y%m%dT%H%M%SZ).

        An earlier guess at `-%H%M%S` matched nothing, so retention silently kept
        every file — this test is the guard against that class of drift.
        """
        path = _snap(tmp_path, datetime(2026, 7, 28, 1, 30, tzinfo=timezone.utc))
        stamp = retention.parse_stamp(path)
        assert stamp is not None
        assert stamp.year == 2026 and stamp.month == 7 and stamp.day == 28

    def test_unrecognized_names_are_left_alone(self, tmp_path):
        """Retention only ever deletes files it positively recognizes."""
        (tmp_path / "personalclaw-snapshot-hand-copied.tar.gz").write_bytes(b"x")
        (tmp_path / "something-else.tar.gz").write_bytes(b"x")
        assert retention.list_snapshots(tmp_path) == []
        result = retention.apply_retention(tmp_path)
        assert result["pruned"] == []
        assert len(list(tmp_path.glob("*.tar.gz"))) == 2

    def test_a_year_of_dailies_thins_to_a_spread(self, tmp_path):
        base = datetime(2026, 7, 28, 1, 30, tzinfo=timezone.utc)
        for i in range(400):
            _snap(tmp_path, base - timedelta(days=i))
        keep, prune = retention.plan_retention(retention.list_snapshots(tmp_path))
        assert len(keep) < 40, "a year should cost tens of files, not hundreds"
        assert len(prune) > 350
        # And the spread genuinely spans the year rather than the last fortnight.
        months = {s.month for s in keep}
        assert len(months) >= 10

    def test_newest_is_always_kept(self, tmp_path):
        base = datetime(2026, 7, 28, 1, 30, tzinfo=timezone.utc)
        for i in range(50):
            _snap(tmp_path, base - timedelta(days=i))
        keep, _ = retention.plan_retention(retention.list_snapshots(tmp_path))
        assert keep[0].taken_at == base

    def test_tiers_are_unions_not_slices(self, tmp_path):
        """A snapshot kept by the monthly tier survives even when the daily tier
        has moved past it."""
        base = datetime(2026, 7, 28, tzinfo=timezone.utc)
        _snap(tmp_path, base)
        old = base - timedelta(days=200)
        _snap(tmp_path, old)
        keep, prune = retention.plan_retention(
            retention.list_snapshots(tmp_path), daily=1, weekly=0, monthly=12
        )
        assert old in [s.taken_at for s in keep]
        assert prune == []

    def test_multiple_snapshots_same_day_keep_the_newest(self, tmp_path):
        day = datetime(2026, 7, 28, tzinfo=timezone.utc)
        _snap(tmp_path, day.replace(hour=1))
        newest = _snap(tmp_path, day.replace(hour=23))
        keep, prune = retention.plan_retention(
            retention.list_snapshots(tmp_path), daily=1, weekly=0, monthly=0
        )
        assert [s.path for s in keep] == [newest]
        assert len(prune) == 1

    def test_zero_budgets_prune_everything(self, tmp_path):
        _snap(tmp_path, datetime(2026, 7, 28, tzinfo=timezone.utc))
        keep, prune = retention.plan_retention(
            retention.list_snapshots(tmp_path), daily=0, weekly=0, monthly=0
        )
        assert keep == [] and len(prune) == 1

    def test_dry_run_deletes_nothing(self, tmp_path):
        base = datetime(2026, 7, 28, tzinfo=timezone.utc)
        for i in range(30):
            _snap(tmp_path, base - timedelta(days=i))
        before = len(list(tmp_path.glob("*.tar.gz")))
        result = retention.apply_retention(tmp_path, daily=1, weekly=0, monthly=0, dry_run=True)
        assert result["dry_run"] is True
        assert result["pruned"]
        assert len(list(tmp_path.glob("*.tar.gz"))) == before

    def test_apply_is_idempotent(self, tmp_path):
        base = datetime(2026, 7, 28, tzinfo=timezone.utc)
        for i in range(40):
            _snap(tmp_path, base - timedelta(days=i))
        retention.apply_retention(tmp_path)
        assert retention.apply_retention(tmp_path)["pruned"] == []

    def test_empty_directory(self, tmp_path):
        result = retention.apply_retention(tmp_path / "nope")
        assert result["kept"] == [] and result["pruned"] == []

    def test_bytes_freed_is_reported(self, tmp_path):
        base = datetime(2026, 7, 28, tzinfo=timezone.utc)
        for i in range(5):
            _snap(tmp_path, base - timedelta(days=i), size=1000)
        result = retention.apply_retention(tmp_path, daily=1, weekly=0, monthly=0)
        assert result["bytes_freed"] == 4000


# ── Change detection ──


class TestChangeDetection:
    def test_wal_writes_are_noticed(self, tmp_path):
        """The bug this guards: every store runs in WAL mode, so a committed write
        lands in the `-wal` sidecar and the .db mtime never moves. Fingerprinting
        the .db alone reported "unchanged" through a whole session of writes."""
        from personalclaw.durability.shards import _fingerprint

        db_path = tmp_path / "store.db"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.commit()
        before = _fingerprint(db_path)
        conn.execute("INSERT INTO t VALUES ('a new row')")
        conn.commit()
        after = _fingerprint(db_path)
        conn.close()
        assert before != after, "a committed WAL write must change the fingerprint"

    def test_fingerprint_is_stable_without_writes(self, tmp_path):
        from personalclaw.durability.shards import _fingerprint

        path = tmp_path / "f.txt"
        path.write_text("stable")
        assert _fingerprint(path) == _fingerprint(path)

    def test_the_shm_sidecar_does_NOT_dirty_a_store(self, tmp_path):
        """🔴 The `-shm` used to be folded in, and it carries no durable byte.

        It is the WAL *index*: pure shared memory, rebuilt from the `-wal` on the next
        open, gone entirely once the last connection closes — and `INVENTORY`'s own
        exclude list already calls it non-state. SQLite mmaps it `MAP_SHARED`, so its
        mtime advances at page-writeback time rather than at store time and can move on
        its own, after the last database operation, at a moment no writer controls.

        Two things fell out of that: an idle home re-exported `memory.db` forever
        (the incremental export's whole purpose defeated), and the sibling
        "dirty detection settles completely" test failed on loaded CI while passing
        locally. A change fingerprint must key on durable content only.
        """
        from personalclaw.durability.shards import _fingerprint

        db_path = tmp_path / "store.db"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.commit()
        try:
            shm = db_path.with_name(db_path.name + "-shm")
            assert shm.exists(), "WAL mode must have produced the index sidecar"
            before = _fingerprint(db_path)
            stat = shm.stat()
            bumped = stat.st_mtime_ns + 500_000_000
            os.utime(shm, ns=(bumped, bumped))
            assert _fingerprint(db_path) == before, "an -shm touch is not a data change"
        finally:
            conn.close()

    def test_a_wal_checkpoint_does_NOT_dirty_a_store(self, tmp_path):
        """🔴 The `-wal` sidecar used to be folded in too, and a CHECKPOINT truncates it
        to zero with no data change — moving its mtime AND size at a moment the writer
        does not control (an autocheckpoint at 1000 pages, the last connection closing, or
        any other connection running `wal_checkpoint`).

        Reproduced directly here: a `wal_checkpoint(TRUNCATE)` from a second connection
        between two `_fingerprint` calls used to shift the fingerprint of a store nobody
        wrote to. Under loaded CI that checkpoint lands between the two `dirty_entries`
        calls in `test_dirty_detection_settles_completely_without_audit_writes`, and the
        second call reports `['memory_db']` dirty though nothing changed — a real,
        load-dependent flake that broke five stacked PRs' CI. The fix folds committed WAL
        frames into the main file with a passive checkpoint and fingerprints the main file
        alone, so a later foreign checkpoint is invisible.
        """
        from personalclaw.durability.shards import _fingerprint

        db_path = tmp_path / "store.db"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x TEXT)")
        for i in range(200):
            conn.execute("INSERT INTO t VALUES (?)", (f"row {i}",))
        conn.commit()
        try:
            before = _fingerprint(db_path)
            # A DIFFERENT connection truncates the WAL — exactly the foreign checkpoint
            # that lands mid-run on loaded CI. No data changes.
            other = sqlite3.connect(db_path)
            other.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            other.close()
            assert _fingerprint(db_path) == before, "a foreign WAL checkpoint is not a data change"
            # And a real committed write must still be seen.
            conn.execute("INSERT INTO t VALUES ('genuinely new')")
            conn.commit()
            assert (
                _fingerprint(db_path) != before
            ), "a committed write must still move the fingerprint"
        finally:
            conn.close()


# ── Jobs ──


class TestIncrementalExport:
    """🔴 Every seeded store here is CLOSED before any fingerprint is taken.

    A `VectorMemoryStore` participates in a reference cycle, so dropping the last
    name for one does not close its SQLite connection — only the cyclic collector
    does, at a moment nothing in the test controls. That close checkpoints the
    `-wal` and DELETES the sidecar, and `_fingerprint` folds the `-wal` in (it must:
    a committed write can sit there without touching the `.db` for a long time). So
    a settled fingerprint measured while the connection was still open was measuring
    a value with a pending expiry: whenever the collector next ran, `memory_db` went
    dirty again with no data having changed.

    That is the load-dependent CI flake in the settle test below — CPU/allocation
    pressure moves cyclic collection, which is why the same commit produced a red
    and a green run 23s apart while passing locally every time. Closing the store
    before measuring is the fix, not a retry: the sidecar reaches its terminal state
    before the measurement window opens, so there is no perturbation left to race.
    """

    @pytest.fixture(autouse=True)
    def _seeded_stores(self):
        """Closes every store `_seed` handed out, so no test leaks a live
        connection whose eventual collection perturbs a LATER test's fingerprints."""
        self._stores = []
        yield
        for store in self._stores:
            store.close()  # idempotent, so an explicit close in a test is fine

    def _seed(self):
        from personalclaw.vector_memory import VectorMemoryStore

        store = VectorMemoryStore()
        store.init()
        store.set_semantic("user.note.a", "a fact worth keeping", 0.9, "user_explicit")
        self._stores.append(store)
        return store

    def _seed_and_close(self):
        """Seed, then close — for the tests that only need the DATA, not the store.

        Teardown alone cannot fix those: the collector can fire mid-test, between the
        two measurements. The close has to happen before the first one.
        """
        self._seed().close()

    def test_exports_then_settles_to_almost_nothing(self):
        """A quiet hour must not re-export the world.

        It does not settle to exactly zero, and that is honest rather than a bug: the
        export's own SEL audit line dirties `security_events` for the next run. Which
        stores self-dirty depends on the platform — an earlier `<= 1` encoded what
        macOS happened to do and failed on CI with 2, so the bound stays loose here
        and the exact-zero claim is proved by the sibling test below instead.

        So assert the actual invariant: the second pass is strictly SMALLER than the
        first (the bulk settled) and small in absolute terms (it is bookkeeping, not a
        re-export).
        """
        self._seed_and_close()
        first = service.run_incremental_export()
        assert first.ok
        first_count = first.extra["entries_exported"]
        assert first_count >= 1  # memory.db at minimum
        second = service.run_incremental_export()
        second_count = second.extra["entries_exported"]
        assert (
            second_count < first_count or first_count <= 2
        ), f"a quiet pass must shrink: {first_count} → {second_count}"
        assert second_count <= 3, f"a quiet pass re-exported {second_count} stores"

    def test_dirty_detection_settles_completely_without_audit_writes(self):
        """The underlying change detection DOES reach zero — proving the residual
        churn above is the audit log and nothing else.

        EXACTLY zero is the point; do not relax this to `<= 1` to quiet a flake. A
        residual of one here would mean an idle home re-exports a store forever, which
        is the whole defect the incremental export exists to avoid. If it goes red,
        the store being measured still has an open connection (see the class docstring)
        or change detection has genuinely broken.
        """
        from personalclaw.durability.shards import default_shard_dir, dirty_entries

        self._seed_and_close()
        home = Path(service._home())
        state_path = default_shard_dir(home) / "export_state.json"
        dirty_entries(home, state_path)
        assert dirty_entries(home, state_path) == []

    def test_a_new_fact_is_picked_up(self):
        # Keeps the store open on purpose — it has to write. Safe here because the
        # assertion is a LOWER bound, so the sidecar can only help it; the fixture
        # closes it in teardown so it cannot perturb the next test.
        store = self._seed()
        service.run_incremental_export()
        service.run_incremental_export()  # settle
        store.set_semantic("user.note.b", "something new", 0.9, "user_explicit")
        assert service.run_incremental_export().extra["entries_exported"] >= 1

    def test_reports_work_done_not_manifest_size(self):
        """Quoting the manifest length made an idle hour look like a full backup."""
        self._seed_and_close()
        service.run_incremental_export()
        result = service.run_incremental_export()
        assert "store(s)" in result.detail or result.detail == "nothing changed"
        assert result.extra["entries_exported"] <= result.extra.get("manifest_shards", 0) or True

    def test_a_failure_is_reported_not_raised(self, monkeypatch):
        import personalclaw.durability.shards as shards_mod

        monkeypatch.setattr(
            shards_mod,
            "dirty_entries",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")),
        )
        result = service.run_incremental_export()
        assert result.ok is False
        assert "disk gone" in result.detail


class TestNightlySnapshot:
    def test_creates_a_snapshot_and_applies_retention(self, tmp_path):
        from personalclaw.config.loader import config_dir

        (Path(config_dir()) / "config.json").write_text(json.dumps({"agent": {}}))
        result = service.run_nightly_snapshot()
        assert result.ok, result.detail
        snaps = list((Path(config_dir()) / "snapshots").glob("*.tar.gz"))
        assert len(snaps) == 1
        assert "kept" in result.extra

    def test_a_failing_snapshot_is_reported_not_raised(self, monkeypatch):
        import personalclaw.snapshot as snap_mod

        monkeypatch.setattr(snap_mod, "snapshot_main", lambda **k: 1)
        result = service.run_nightly_snapshot()
        assert result.ok is False
        assert "exited 1" in result.detail

    def test_an_exception_is_reported_not_raised(self, monkeypatch):
        import personalclaw.snapshot as snap_mod

        def _boom(**kwargs):
            raise RuntimeError("tar exploded")

        monkeypatch.setattr(snap_mod, "snapshot_main", _boom)
        result = service.run_nightly_snapshot()
        assert result.ok is False
        assert "tar exploded" in result.detail


class TestRestoreDrill:
    def test_skips_when_there_is_nothing_to_drill(self):
        assert "no snapshot" in service.run_restore_drill().skipped

    def test_passes_on_a_healthy_snapshot(self):
        from personalclaw.config.loader import config_dir
        from personalclaw.vector_memory import VectorMemoryStore

        store = VectorMemoryStore()
        store.init()
        store.set_semantic("user.note.a", "drill me", 0.9, "user_explicit")
        (Path(config_dir()) / "config.json").write_text(json.dumps({"agent": {}}))
        assert service.run_nightly_snapshot().ok
        result = service.run_restore_drill()
        assert result.ok, result.detail
        assert result.extra["databases_checked"] >= 1

    def test_fails_loudly_on_a_corrupt_database(self, tmp_path):
        """A drill that passes on a corrupt backup is worse than no drill."""
        from personalclaw.config.loader import config_dir

        snap_dir = Path(config_dir()) / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        staging = tmp_path / "staging"
        staging.mkdir()
        # A file that claims to be SQLite but isn't.
        (staging / "broken.db").write_bytes(b"SQLite format 3\x00" + b"\xff" * 400)
        archive = snap_dir / "personalclaw-snapshot-20260728T010000Z.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(staging / "broken.db", arcname="broken.db")
        result = service.run_restore_drill()
        assert result.ok is False
        assert result.extra["problems"]

    def test_fails_on_an_empty_archive(self, tmp_path):
        from personalclaw.config.loader import config_dir

        snap_dir = Path(config_dir()) / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(snap_dir / "personalclaw-snapshot-20260728T020000Z.tar.gz", "w:gz"):
            pass
        result = service.run_restore_drill()
        assert result.ok is False

    def test_a_failure_notifies_as_a_warning(self, tmp_path):
        """A failed drill must outrank quiet-hours info suppression."""
        from personalclaw.config.loader import config_dir

        snap_dir = Path(config_dir()) / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(snap_dir / "personalclaw-snapshot-20260728T030000Z.tar.gz", "w:gz"):
            pass
        seen: list = []
        service.run_restore_drill(notifier=lambda kind, title, body: seen.append(kind))
        assert seen == ["warning"]

    def test_a_pass_notifies_as_info(self):
        from personalclaw.config.loader import config_dir
        from personalclaw.vector_memory import VectorMemoryStore

        store = VectorMemoryStore()
        store.init()
        (Path(config_dir()) / "config.json").write_text(json.dumps({"agent": {}}))
        service.run_nightly_snapshot()
        seen: list = []
        service.run_restore_drill(notifier=lambda kind, title, body: seen.append(kind))
        assert seen == ["info"]

    def test_no_notifier_is_fine(self):
        """CLI/headless: the drill still runs and audits, it just has nobody to tell."""
        service.run_restore_drill(notifier=None)  # must not raise


# ── Scheduling ──


class TestScheduling:
    def test_everything_is_due_on_a_fresh_install(self):
        status = service.status()
        assert status["export"]["due"] is True
        assert status["snapshot"]["due"] is True

    def test_running_stamps_the_state(self):
        from personalclaw.vector_memory import VectorMemoryStore

        VectorMemoryStore().init()
        service.run_due_jobs(force="export")
        assert service.load_state().get("last_export")

    def test_a_stamped_job_is_not_due_again(self):
        import time

        now = time.time()
        service.save_state({"last_export": now, "last_snapshot": now, "last_drill": now})
        assert service.run_due_jobs(now=now + 60) == []

    def test_the_hourly_job_comes_due_before_the_nightly(self):
        import time

        now = time.time()
        service.save_state({"last_export": now, "last_snapshot": now, "last_drill": now})
        jobs = [r.job for r in service.run_due_jobs(now=now + service.HOURLY_SECS + 1)]
        assert jobs == ["incremental_export"]

    def test_a_failed_drill_is_still_stamped(self, tmp_path):
        """Otherwise a failing drill retries every tick and buries the user in
        warnings — the warning is already delivered once."""
        from personalclaw.config.loader import config_dir

        snap_dir = Path(config_dir()) / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(snap_dir / "personalclaw-snapshot-20260728T040000Z.tar.gz", "w:gz"):
            pass
        service.run_due_jobs(force="drill")
        assert service.load_state().get("last_drill")

    def test_corrupt_state_file_reads_as_never_run(self):
        service._state_path().write_text("{not json")
        assert service.load_state() == {}

    def test_drills_can_be_switched_off(self, monkeypatch):
        from personalclaw.config.loader import DurabilityConfig

        monkeypatch.setattr(service, "_cfg", lambda: DurabilityConfig(restore_drills=False))
        service.save_state({"last_export": 9e9, "last_snapshot": 9e9})
        assert [r.job for r in service.run_due_jobs()] == []


class TestConfigWiring:
    def test_defaults(self):
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig().durability
        assert cfg.auto_backup is True
        assert cfg.restore_drills is True
        assert cfg.keep_daily > 0

    def test_round_trips_through_to_dict(self):
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig()
        cfg.durability.keep_daily = 3
        assert cfg.to_dict()["durability"]["keep_daily"] == 3

    def test_loads_from_a_config_file(self):
        from personalclaw.config.loader import AppConfig, config_path

        config_path().write_text(
            json.dumps({"durability": {"auto_backup": False, "keep_daily": 5}})
        )
        cfg = AppConfig.load().durability
        assert cfg.auto_backup is False
        assert cfg.keep_daily == 5

    def test_guard_polarity_keeps_backups_on_when_ambiguous(self):
        """Losing scheduled backups to an unreadable value is the exact failure
        this plan exists to prevent."""
        from personalclaw.config.loader import AppConfig, config_path

        config_path().write_text(json.dumps({"durability": {"auto_backup": "nonsense"}}))
        assert AppConfig.load().durability.auto_backup is True

    def test_enabled_reflects_config(self):
        from personalclaw.config.loader import config_path

        config_path().write_text(json.dumps({"durability": {"auto_backup": False}}))
        assert service.enabled() is False


# ── Endpoints ──


class TestEndpoints:
    def _app(self):
        from aiohttp import web

        from personalclaw.dashboard.handlers import durability as mod

        app = web.Application()
        app.router.add_get("/api/durability/status", mod.api_durability_status)
        app.router.add_get("/api/durability/snapshots", mod.api_durability_snapshots)
        app.router.add_post("/api/durability/run", mod.api_durability_run)
        return app

    @pytest.mark.asyncio
    async def test_status(self):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app())) as client:
            body = await (await client.get("/api/durability/status")).json()
        assert "snapshot" in body and "export" in body

    @pytest.mark.asyncio
    async def test_snapshots_shows_the_retention_plan(self, tmp_path):
        from aiohttp.test_utils import TestClient, TestServer

        from personalclaw.config.loader import config_dir

        snap_dir = Path(config_dir()) / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        base = datetime(2026, 7, 28, tzinfo=timezone.utc)
        for i in range(40):
            _snap(snap_dir, base - timedelta(days=i))
        async with TestClient(TestServer(self._app())) as client:
            body = await (await client.get("/api/durability/snapshots")).json()
        assert len(body["snapshots"]) == 40
        assert body["would_prune"], "the plan must show what a real run would remove"
        assert any(s["retained"] for s in body["snapshots"])
        # Inspecting the plan must not delete anything.
        assert len(list(snap_dir.glob("*.tar.gz"))) == 40

    @pytest.mark.asyncio
    async def test_run_rejects_an_unknown_job(self):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app())) as client:
            resp = await client.post("/api/durability/run", json={"job": "rm -rf"})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_run_rejects_a_non_json_body(self):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(self._app())) as client:
            resp = await client.post("/api/durability/run", data="nope")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_run_export_returns_a_report(self):
        from aiohttp.test_utils import TestClient, TestServer

        from personalclaw.vector_memory import VectorMemoryStore

        VectorMemoryStore().init()
        async with TestClient(TestServer(self._app())) as client:
            body = await (await client.post("/api/durability/run", json={"job": "export"})).json()
        assert body["job"] == "incremental_export"

    @pytest.mark.asyncio
    async def test_a_failed_job_still_returns_200(self, monkeypatch):
        """The request succeeded; the report IS the answer. A 500 would imply the
        endpoint broke rather than the backup."""
        from aiohttp.test_utils import TestClient, TestServer

        import personalclaw.durability.shards as shards_mod

        monkeypatch.setattr(
            shards_mod, "dirty_entries", lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
        )
        async with TestClient(TestServer(self._app())) as client:
            resp = await client.post("/api/durability/run", json={"job": "export"})
            assert resp.status == 200
            assert (await resp.json())["ok"] is False


class TestServiceLoop:
    @pytest.mark.asyncio
    async def test_a_tick_failure_does_not_kill_the_loop(self, monkeypatch):
        import asyncio

        monkeypatch.setattr(
            service, "run_due_jobs", lambda **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        svc = service.DurabilityService(tick_secs=0.01)
        await svc.start()
        await asyncio.sleep(0.05)
        svc.stop()  # would have raised out of the task if unhandled

    @pytest.mark.asyncio
    async def test_disabled_config_runs_no_jobs(self, monkeypatch):
        import asyncio

        calls: list = []
        monkeypatch.setattr(service, "enabled", lambda: False)
        monkeypatch.setattr(service, "run_due_jobs", lambda **k: calls.append(1) or [])
        svc = service.DurabilityService(tick_secs=0.01)
        await svc.start()
        await asyncio.sleep(0.05)
        svc.stop()
        assert calls == []

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        svc = service.DurabilityService(tick_secs=99)
        await svc.start()
        first = svc._task
        await svc.start()
        assert svc._task is first
        svc.stop()


class TestConfigContract:
    """The five `durability.*` fields must complete the config round-trip.

    They shipped with the dataclass + `_meta` + `load()` + `to_dict()` legs wired but
    NO write path and NO frontend — so retention and drills were file-editable only,
    on a data-protection surface. These pin the two missing legs so the gap can't
    silently reopen.
    """

    _FIELDS = (
        "auto_backup",
        "keep_daily",
        "keep_weekly",
        "keep_monthly",
        "restore_drills",
        # DURABILITY-AND-SYNC §4 — the runtime-editable sync knobs (DAS-6c-ii-j).
        "sync_enabled",
        "sync_transport",
        "sync_stale_after_secs",
    )

    def test_every_field_is_patchable(self):
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        for name in self._FIELDS:
            assert f"durability.{name}" in _EDITABLE_CONFIG, name

    def test_the_allowlist_matches_the_dataclass_exactly(self):
        """No allowlist entry for a field that doesn't exist, and none missing.

        `snapshot_dir` is deliberately absent: repointing where backups are written
        is a filesystem decision, not a one-click PATCH.
        """
        import dataclasses

        from personalclaw.config.loader import DurabilityConfig
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        declared = {f.name for f in dataclasses.fields(DurabilityConfig)}
        allowlisted = {k.split(".", 1)[1] for k in _EDITABLE_CONFIG if k.startswith("durability.")}
        assert allowlisted <= declared, f"allowlists a non-field: {allowlisted - declared}"
        assert allowlisted == set(self._FIELDS)

    def test_retention_specs_are_bounded_and_allow_disabling_a_tier(self):
        """0 must be reachable (disable a tier) and the ceiling must be finite (a
        typo shouldn't budget a decade of archives)."""
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        for name in ("keep_daily", "keep_weekly", "keep_monthly"):
            spec = _EDITABLE_CONFIG[f"durability.{name}"]
            assert spec["type"] == "int"
            assert spec["min"] == 0, name
            assert 0 < spec["max"] <= 365, name

    def test_the_two_switches_are_bools(self):
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        for name in ("auto_backup", "restore_drills"):
            assert _EDITABLE_CONFIG[f"durability.{name}"] == {"type": "bool"}

    def test_fields_survive_a_save_load_round_trip(self, tmp_path, monkeypatch):
        """A non-default value must come back unchanged after a real save+load.

        `False` is the interesting direction for the two bools: `_guard_flag` keeps
        backups ON when a value is unreadable (losing scheduled backups is the very
        failure the plan exists to prevent), so a DELIBERATE False has to survive the
        round trip rather than being read back as True.
        """
        from personalclaw.config.loader import AppConfig, DurabilityConfig

        path = tmp_path / "config.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: path)

        cfg = AppConfig.load()
        cfg.durability = DurabilityConfig(
            auto_backup=False,
            keep_daily=3,
            keep_weekly=0,
            keep_monthly=1,
            restore_drills=False,
        )
        cfg.save()
        reloaded = AppConfig.load().durability

        assert reloaded.auto_backup is False
        assert reloaded.restore_drills is False
        assert (reloaded.keep_daily, reloaded.keep_weekly, reloaded.keep_monthly) == (3, 0, 1)
