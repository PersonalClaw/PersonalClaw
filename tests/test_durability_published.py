"""The pieces a sync's copies are kept by: the change probe, the retention rule, the record of
what this machine sent, what a copy carries for a peer that reads only the newest, and what the
Backups page is told."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.durability import service
from personalclaw.durability.published import (
    KEEP_PREVIOUS_SECS,
    Published,
    export_digest,
    superseded,
)
from personalclaw.durability.shards import (
    IncompleteExport,
    OutsideTheExport,
    export_shards,
    import_shards,
)
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)

NOW = "2026-10-02T12:00:00+00:00"


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "tasks").mkdir(parents=True)
    (home / "tasks" / "t1.json").write_text('{"id": "t1", "title": "hello"}', encoding="utf-8")
    return home


def _export(home: Path, out: Path, **kw) -> Path:
    export_shards(home, out, for_sync=True, **kw)
    return out


class TestTheChangeProbe:
    def test_two_exports_of_an_unchanged_home_read_the_same(self, tmp_path):
        home = _home(tmp_path)
        first = export_digest(_export(home, tmp_path / "one"))
        second = export_digest(_export(home, tmp_path / "two"))
        manifests = [
            json.loads((tmp_path / d / "manifest.json").read_text()) for d in ("one", "two")
        ]
        assert first and first == second
        # The manifest's own time differs run to run, and does not count.
        assert set(manifests[0]) >= {"generated_at", "shards"}

    def test_a_changed_record_reads_differently(self, tmp_path):
        home = _home(tmp_path)
        before = export_digest(_export(home, tmp_path / "one"))
        (home / "tasks" / "t1.json").write_text('{"id": "t1", "title": "bye"}', encoding="utf-8")
        assert export_digest(_export(home, tmp_path / "two")) != before

    def test_a_database_change_reads_differently(self, tmp_path):
        """A database's rows record a blob by its size alone, so the probe reads the database
        copy too: an embedding rewritten at the same size is a change."""
        import sqlite3

        home = _home(tmp_path)
        db = home / "memory.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE m (id TEXT PRIMARY KEY, emb BLOB)")
            conn.execute("INSERT INTO m VALUES ('a', ?)", (b"\x00" * 8,))
        before = export_digest(_export(home, tmp_path / "one"))
        assert export_digest(_export(home, tmp_path / "same")) == before
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE m SET emb = ? WHERE id = 'a'", (b"\x01" * 8,))
        assert export_digest(_export(home, tmp_path / "two")) != before

    def test_the_security_log_does_not_count(self, tmp_path):
        """Something appends to it every minute (an automation's heartbeat, every backup and sync
        run), whoever writes the row: counted, every export was new."""
        home = _home(tmp_path)
        log = home / "security_events.jsonl"

        def row(n: int, caller: str) -> str:
            return (
                json.dumps(
                    {
                        "event_id": f"e{n}",
                        "timestamp": f"2026-10-02T12:0{n}:00+00:00",
                        "caller_identity": caller,
                        "operation": "x",
                    }
                )
                + "\n"
            )

        log.write_text(row(1, "dashboard:owner"), encoding="utf-8")
        before = export_digest(_export(home, tmp_path / "one"))
        with log.open("a", encoding="utf-8") as fh:
            fh.write(row(2, "durability:service") + row(3, "autonomy:action.spawn_turn"))
            fh.write(row(4, "dashboard:owner"))
        assert export_digest(_export(home, tmp_path / "two")) == before

    def test_a_record_counts_by_what_two_homes_compare_of_it(self, tmp_path):
        """An automation's runs change it in this home alone; what a person made of it counts."""
        home = _home(tmp_path)

        def automations(name: str, runs: int) -> None:
            row = {"id": "trg1", "name": name, "run_count": runs, "last_fired_at": f"t{runs}"}
            body = {"version": 1, "saved_at": runs, "triggers": [row]}
            (home / "triggers.json").write_text(json.dumps(body), encoding="utf-8")

        automations("Heartbeat", 1)
        before = export_digest(_export(home, tmp_path / "one"))
        automations("Heartbeat", 2)
        assert export_digest(_export(home, tmp_path / "two")) == before
        automations("Morning heartbeat", 3)
        assert export_digest(_export(home, tmp_path / "three")) != before

    def test_an_export_whose_manifest_is_unreadable_is_always_new(self, tmp_path):
        assert export_digest(tmp_path) == ""
        assert not Published(seq=1, digest="").stands("", remote_seq=1, remote_has_it=True)


class TestTheRetentionRule:
    @pytest.mark.parametrize(
        ("minutes", "removed"),
        [(0, []), (14, []), (KEEP_PREVIOUS_SECS / 60, [4]), (60, [4])],
    )
    def test_the_copy_before_the_newest_stays_fifteen_minutes(self, minutes, removed):
        from datetime import datetime, timedelta

        landed_5 = datetime.fromisoformat(NOW)
        now = (landed_5 + timedelta(minutes=minutes)).isoformat()
        assert superseded([4, 5], newest=5, landed={5: NOW}, now=now) == removed

    def test_older_copies_go_and_the_newest_never_does(self):
        landed = {2: "2026-10-02T10:00:00+00:00", 3: "2026-10-02T11:00:00+00:00", 4: NOW}
        assert superseded([1, 2, 3, 4], newest=4, landed=landed, now=NOW) == [1, 2]
        assert superseded([4], newest=4, landed=landed, now="2027-01-01T00:00:00+00:00") == []

    def test_a_copy_whose_successor_has_no_known_time_landed_long_ago(self):
        assert superseded([1, 2], newest=2, landed={}, now=NOW) == [1]

    def test_a_seq_above_the_newest_is_never_removed(self):
        """Objects under a seq the registry doesn't name yet are a failed publish's: the next
        publish of that seq clears them, and nothing else touches them."""
        later = "2026-10-02T13:00:00+00:00"
        assert superseded([3, 4, 5], newest=4, landed={4: NOW}, now=later) == [3]

    @pytest.mark.parametrize("now", ["", "now", "2026-13-45"])
    def test_without_a_time_that_parses_nothing_is_removed(self, now):
        assert superseded([1, 2, 3], newest=3, landed={}, now=now) == []


class TestTheRecord:
    def test_it_round_trips(self, tmp_path):
        from personalclaw.config.loader import config_dir

        sync_root = config_dir() / "sync"  # where the cycle keeps it: the home's own, 0600
        rec = Published()
        rec.record(3, "d3", now=NOW)
        rec.save(sync_root)
        back = Published.load(sync_root)
        assert (back.seq, back.digest, back.landed) == (3, "d3", {3: NOW})
        assert (sync_root / "published.json").stat().st_mode & 0o077 == 0

    @pytest.mark.parametrize("body", ["", "{not json", "[1, 2]", '{"seq": "x", "landed": 4}'])
    def test_an_unreadable_record_reads_as_none_sent(self, tmp_path, body):
        (tmp_path / "published.json").write_text(body, encoding="utf-8")
        assert Published.load(tmp_path).seq == 0

    def test_it_stands_only_for_the_same_records_in_a_store_that_holds_them(self):
        rec = Published(seq=4, digest="d")
        assert rec.stands("d", remote_seq=4, remote_has_it=True)
        assert not rec.stands("e", remote_seq=4, remote_has_it=True)  # records changed
        assert not rec.stands("d", remote_seq=0, remote_has_it=False)  # another store
        assert not rec.stands("d", remote_seq=5, remote_has_it=True)  # the store moved on
        assert not rec.stands("d", remote_seq=4, remote_has_it=False)  # the copy is gone

    def test_it_keeps_the_times_of_the_copies_still_in_the_store(self):
        rec = Published(seq=5, digest="d", landed={3: "a", 4: "b", 5: "c"})
        rec.keep_only({4, 5})
        assert rec.landed == {4: "b", 5: "c"}


class TestWhatACopyCarriesForAPeer:
    AGREED = {"peer-b": {"tasks": {"t1": "sha-1"}}, "peer-c": {}}

    def test_the_agreements_round_trip_through_an_export(self, tmp_path):
        out = _export(_home(tmp_path), tmp_path / "out", agreements=self.AGREED)
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["agreements"]["path"] == "agreements.json"
        assert import_shards(out).agreements == self.AGREED

    def test_an_export_without_them_imports_none(self, tmp_path):
        out = _export(_home(tmp_path), tmp_path / "out")
        assert "agreements" not in json.loads((out / "manifest.json").read_text())
        assert import_shards(out).agreements == {}

    def test_agreements_a_manifest_puts_outside_the_export_are_refused(self, tmp_path):
        out = _export(_home(tmp_path), tmp_path / "out", agreements=self.AGREED)
        manifest = json.loads((out / "manifest.json").read_text())
        manifest["agreements"]["path"] = "../elsewhere.json"
        (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(OutsideTheExport):
            import_shards(out)

    def test_agreements_not_there_yet_hold_the_copy(self, tmp_path):
        out = _export(_home(tmp_path), tmp_path / "out", agreements=self.AGREED)
        (out / "agreements.json").unlink()
        with pytest.raises(IncompleteExport) as caught:
            import_shards(out)
        assert caught.value.paths == ["agreements.json"]

    def test_only_plain_strings_of_them_are_taken(self, tmp_path):
        odd = {"peer-b": {"tasks": {"t1": "sha-1", "t2": 7, "": "x"}, "bad": [1]}, "peer-c": 3}
        out = _export(_home(tmp_path), tmp_path / "out", agreements=odd)
        assert import_shards(out).agreements == {"peer-b": {"tasks": {"t1": "sha-1"}}}

    def test_a_missing_shard_is_an_incomplete_export_not_a_broken_one(self, tmp_path):
        out = _export(_home(tmp_path), tmp_path / "out")
        (out / "tasks" / "entities.jsonl").unlink()
        with pytest.raises(IncompleteExport) as caught:
            import_shards(out)
        assert caught.value.paths == ["tasks/entities.jsonl"]
        (out / "manifest.json").unlink()
        with pytest.raises(IncompleteExport):
            import_shards(out)


class _Cfg:
    def __init__(self, transport: str) -> None:
        self.sync_enabled = True
        self.sync_transport = transport
        self.sync_stale_after_secs = 900
        self.sync_encrypt = "auto"
        self.restore_drills = False
        self.time_travel = False


class _Transport(SyncTransportProvider):
    def __init__(self, name: str, removes: bool) -> None:
        self.name = name
        self.removes_old_copies = removes
        self.objects: dict[str, bytes] = {}

    def push(self, objects):
        for o in objects:
            self.objects.setdefault(o.key, o.data)
        return PushResult(pushed=len(objects), outcome="delivered")

    def list_remote(self, prefix=""):
        return [RemoteRef(key=k) for k in self.objects if k.startswith(prefix)]

    def pull(self, refs):
        return [
            SyncObject(key=r.key, data=self.objects[r.key]) for r in refs if r.key in self.objects
        ]

    def cas_registry(self, expected_sha, data):
        self.objects["registry.json"] = data
        return True

    def remove(self, keys):
        return sum(1 for k in keys if self.objects.pop(k, None) is not None)

    def test(self):  # pragma: no cover
        return ConnectionResult(ok=True)


class TestTheBackupsPageIsTold:
    @pytest.mark.parametrize(("removes", "said"), [(True, True), (False, False)])
    def test_whether_the_chosen_transport_removes_old_copies(self, monkeypatch, removes, said):
        from personalclaw.sync_transports import registry

        monkeypatch.setattr(service, "_cfg", lambda: _Cfg("copies-probe"))
        monkeypatch.setattr(service, "load_state", lambda: {})
        registry.register_transport(_Transport("copies-probe", removes))
        try:
            sync = service.status()["sync"]
        finally:
            registry.unregister_transport("copies-probe")
        assert sync["removes_old_copies"] is said
        assert sync["keeps_previous_secs"] == KEEP_PREVIOUS_SECS

    def test_nothing_while_the_chosen_transport_is_not_there(self, monkeypatch):
        monkeypatch.setattr(service, "_cfg", lambda: _Cfg("not-installed"))
        monkeypatch.setattr(service, "load_state", lambda: {})
        assert service.status()["sync"]["removes_old_copies"] is None


def test_a_run_that_could_not_remove_old_copies_is_said_on_the_card(monkeypatch):
    """The card says the store keeps the newest copy. A run whose removal failed (a bucket key
    with no right to delete, a folder that is busy) left the older ones, and the card says that
    too; the next run that removes them clears it."""
    failed = service.JobResult(
        "sync", ok=True, detail="", extra={"removal_failed": "the store refused the removal"}
    )
    state = service.job_stamp_fields("sync", failed, at=100.0, previous={})
    monkeypatch.setattr(service, "_cfg", lambda: _Cfg(""))
    monkeypatch.setattr(service, "load_state", lambda: state)
    assert service.status()["sync"]["removal_failed"] == "the store refused the removal"

    cleared = service.job_stamp_fields(
        "sync",
        service.JobResult("sync", ok=True, extra={"removal_failed": ""}),
        at=200.0,
        previous=state,
    )
    assert cleared["sync_removal_failed"] == ""


def test_a_scheduled_sync_stamps_its_copy_with_the_time(tmp_path, monkeypatch):
    """The service passed no time, so the registry read ``last_export_at: ""`` and nothing could
    tell how long a copy had stood."""
    from datetime import datetime

    from personalclaw.durability.registry import Registry
    from personalclaw.sync_transports import registry

    home = _home(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr(service, "_cfg", lambda: _Cfg("stamp-probe"))
    tr = _Transport("stamp-probe", True)
    registry.register_transport(tr)
    try:
        result = service.run_sync_job()
    finally:
        registry.unregister_transport("stamp-probe")
    assert result.ok, result.detail
    (entry,) = Registry.loads(tr.objects["registry.json"]).machines.values()
    assert datetime.fromisoformat(entry.last_export_at).tzinfo is not None
