"""DURABILITY-AND-SYNC §4 / DAS-6c-ii-j — sync config round-trip + the service due-job.

The sync knobs (sync_enabled/sync_transport/sync_stale_after_secs) survive a save/load cycle
(they're in load()'s explicit mapping, not just asdict), and run_sync_job is a self-guarding
due-job: idle unless enabled AND a registered transport is named, scheduled by the staleness
window, never raising.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from personalclaw.config.loader import AppConfig, DurabilityConfig
from personalclaw.durability import service
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "config.json"
    with patch("personalclaw.config.loader.config_path", return_value=p):
        yield p


class TestConfigRoundTrip:
    def test_sync_fields_default_off_and_closed(self):
        d = DurabilityConfig()
        assert d.sync_enabled is False and d.sync_transport == "" and d.sync_stale_after_secs == 900

    def test_sync_fields_survive_save_load(self, cfg_file):
        cfg = AppConfig.load()
        cfg.durability.sync_enabled = True
        cfg.durability.sync_transport = "git-sync"
        cfg.durability.sync_stale_after_secs = 1800
        cfg.save()
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert raw["durability"]["sync_transport"] == "git-sync"  # asdict serialized it
        # And it comes BACK from load()'s explicit mapping (not silently reverted).
        again = AppConfig.load()
        assert again.durability.sync_enabled is True
        assert again.durability.sync_transport == "git-sync"
        assert again.durability.sync_stale_after_secs == 1800

    def test_sync_enabled_is_fail_closed_on_garbage(self, cfg_file):
        cfg_file.write_text(
            json.dumps({"durability": {"sync_enabled": "not-a-bool"}}), encoding="utf-8"
        )
        # A non-bool must read False (fail-closed): a sync surface must never self-enable.
        assert AppConfig.load().durability.sync_enabled is False


class _Cfg:
    def __init__(self, enabled=False, transport="", stale=900):
        self.sync_enabled = enabled
        self.sync_transport = transport
        self.sync_stale_after_secs = stale
        self.restore_drills = False


class FakeTransport(SyncTransportProvider):
    name = "fake"

    def __init__(self):
        self.objects = {}

    def push(self, o):
        for x in o:
            self.objects.setdefault(x.key, x.data)
        return PushResult(pushed=len(o), outcome="delivered")

    def list_remote(self, prefix=""):
        return [RemoteRef(key=k) for k in self.objects if k.startswith(prefix)]

    def pull(self, refs):
        return [
            SyncObject(key=r.key, data=self.objects[r.key]) for r in refs if r.key in self.objects
        ]

    def cas_registry(self, e, d):
        return True

    def test(self):
        return ConnectionResult(ok=True)


class TestSyncJobGuards:
    def test_disabled_is_skipped(self, monkeypatch):
        monkeypatch.setattr(service, "_cfg", lambda: _Cfg(enabled=False))
        r = service.run_sync_job()
        assert r.skipped == "sync disabled" and r.ok

    def test_no_transport_configured_is_skipped(self, monkeypatch):
        monkeypatch.setattr(service, "_cfg", lambda: _Cfg(enabled=True, transport=""))
        assert "no sync transport" in service.run_sync_job().skipped

    def test_unregistered_transport_is_skipped(self, monkeypatch):
        monkeypatch.setattr(service, "_cfg", lambda: _Cfg(enabled=True, transport="ghost"))
        assert "not installed" in service.run_sync_job().skipped


class TestSyncJobRuns:
    def test_configured_sync_runs_a_cycle(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "tasks").mkdir(parents=True)
        (home / "tasks" / "t1.json").write_text('{"id":"t1"}', encoding="utf-8")
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        monkeypatch.setattr(service, "_cfg", lambda: _Cfg(enabled=True, transport="fake"))

        tr = FakeTransport()
        from personalclaw.sync_transports import registry

        registry.register_transport(tr)
        try:
            r = service.run_sync_job()
        finally:
            registry.unregister_transport("fake")
        assert r.ok and not r.skipped
        assert r.extra.get("seq_published") == 1  # published this machine's first seq


class TestDueSchedule:
    """Schedule-only tests: EVERY real job is stubbed so run_due_jobs touches no real home
    (the destructive-test rule — never snapshot ~/.personalclaw). We assert only which jobs
    the scheduler DECIDES to run, given the clock and config."""

    @pytest.fixture(autouse=True)
    def _no_real_jobs(self, monkeypatch):
        # Neutralize the export/snapshot/drill branches so only the sync decision is exercised.
        monkeypatch.setattr(
            service, "run_incremental_export", lambda: service.JobResult("export", skipped="stub")
        )
        monkeypatch.setattr(
            service,
            "run_nightly_snapshot",
            lambda **k: service.JobResult("snapshot", skipped="stub"),
        )
        monkeypatch.setattr(
            service, "run_restore_drill", lambda **k: service.JobResult("drill", skipped="stub")
        )
        monkeypatch.setattr(service, "save_state", lambda s: None)

    def test_sync_runs_only_when_stale(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            service, "_cfg", lambda: _Cfg(enabled=True, transport="fake", stale=900)
        )
        monkeypatch.setattr(
            service, "run_sync_job", lambda: calls.append(1) or service.JobResult("sync")
        )
        monkeypatch.setattr(service, "load_state", lambda: {"last_sync": 1000.0})
        # Not yet stale (100s < 900s window) → no sync.
        service.run_due_jobs(now=1100.0)
        assert calls == []
        # Past the window (1000s elapsed) → sync runs.
        service.run_due_jobs(now=2000.0)
        assert calls == [1]

    def test_disabled_never_schedules_sync(self, monkeypatch):
        calls = []
        monkeypatch.setattr(service, "_cfg", lambda: _Cfg(enabled=False))
        monkeypatch.setattr(service, "run_sync_job", lambda: calls.append(1))
        monkeypatch.setattr(
            service,
            "load_state",
            lambda: {"last_export": 1e12, "last_snapshot": 1e12, "last_drill": 1e12},
        )
        service.run_due_jobs(now=1e12)
        assert calls == []  # sync_enabled=False short-circuits before the due check


class TestStatus:
    def test_status_reports_sync(self, monkeypatch):
        monkeypatch.setattr(
            service, "_cfg", lambda: _Cfg(enabled=True, transport="git-sync", stale=600)
        )
        monkeypatch.setattr(service, "load_state", lambda: {})
        st = service.status()
        assert st["sync"]["enabled"] is True and st["sync"]["transport"] == "git-sync"
