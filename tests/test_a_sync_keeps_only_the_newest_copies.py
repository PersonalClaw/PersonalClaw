"""A sync keeps this machine's newest copy of its records in the store, not one copy per cycle.

Every cycle exported the whole home and published it as a new seq, ~7 MB each in a real home, and
nothing ever removed one: every fifteen minutes another copy, about 20 GB a month, into a folder
picked because it syncs itself. Now a copy is sent only when the records changed, a peer reads only
a machine's newest copy, and a copy a newer one replaced is removed once the newer one has stood
for fifteen minutes — so a machine that started reading it can finish.

Driven end to end through ``run_sync_cycle`` against an in-memory store, with real times.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personalclaw.durability.outbox import Outbox
from personalclaw.durability.published import KEEP_PREVIOUS_SECS
from personalclaw.durability.registry import REGISTRY_KEY, Registry, seq_of_key, shard_prefix
from personalclaw.durability.shards import import_shards
from personalclaw.durability.sync_cycle import read_registry, run_sync_cycle
from personalclaw.sync_transports.base import (
    ConnectionResult,
    KeysRefused,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


class Store(SyncTransportProvider):
    """One in-memory store machines share, which removes objects when asked."""

    name = "store"
    removes_old_copies = True

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.removed: list[str] = []

    def push(self, objects: list[SyncObject]) -> PushResult:
        for o in objects:
            self.objects.setdefault(o.key, o.data)  # insert-only, idempotent
        return PushResult(pushed=len(objects), outcome="delivered")

    def list_remote(self, prefix: str = "") -> list[RemoteRef]:
        return [RemoteRef(key=k) for k in sorted(self.objects) if k.startswith(prefix)]

    def pull(self, refs: list[RemoteRef]) -> list[SyncObject]:
        return [
            SyncObject(key=r.key, data=self.objects[r.key]) for r in refs if r.key in self.objects
        ]

    def cas_registry(self, expected_sha, data) -> bool:
        self.objects[REGISTRY_KEY] = data
        return True

    def remove(self, keys: list[str]) -> int:
        gone = 0
        for key in keys:
            if self.objects.pop(key, None) is not None:
                gone += 1
                self.removed.append(key)
        return gone

    def test(self) -> ConnectionResult:  # pragma: no cover
        return ConnectionResult(ok=True)

    def copies(self, machine: str) -> set[int]:
        """The seqs of *machine*'s copies whose manifest is in the store."""
        return {
            seq
            for key in self.objects
            if key.endswith("/manifest.json") and (seq := seq_of_key(machine, key)) is not None
        }


class KeepingStore(Store):
    """A store whose transport keeps every copy: the cycle must never ask it to remove one."""

    removes_old_copies = False

    def remove(self, keys: list[str]) -> int:  # pragma: no cover — reaching it is the failure
        raise AssertionError("asked a transport that keeps every copy to remove some")


def _task(home: Path, tid: str, title: str) -> None:
    d = home / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps({"id": tid, "title": title}), encoding="utf-8")


def _title(home: Path, tid: str) -> str | None:
    path = home / "tasks" / f"{tid}.json"
    return json.loads(path.read_text(encoding="utf-8"))["title"] if path.exists() else None


def _sel(home: Path, caller: str, n: int) -> None:
    """Append a security-log row written by *caller*."""
    row = {
        "event_id": f"e{n}",
        "timestamp": _at(n),
        "event_type": "api_access",
        "caller_identity": caller,
        "operation": "durability_sync",
        "outcome": "allowed",
    }
    with (home / "security_events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _cycle(store, home: Path, me: str, minutes: float):
    report = run_sync_cycle(store, home, self_id=me, now=_at(minutes))
    assert report.ok, report.error
    return report


class TestACopyIsSentOnlyWhenTheRecordsChanged:
    def test_an_unchanged_home_sends_no_new_copy(self, tmp_path):
        store, a = Store(), tmp_path / "A"
        _task(a, "t1", "hello")
        assert _cycle(store, a, "A", 0).seq_published == 1

        again = _cycle(store, a, "A", 15)

        assert (again.seq_published, again.unchanged_since) == (0, 1)
        assert "nothing new to send, seq 1 stands" in again.detail
        assert store.copies("A") == {1} and read_registry(store).seq_of("A") == 1

    def test_a_change_sends_one(self, tmp_path):
        store, a = Store(), tmp_path / "A"
        _task(a, "t1", "hello")
        _cycle(store, a, "A", 0)
        _task(a, "t1", "hello again")

        assert _cycle(store, a, "A", 15).seq_published == 2

    def test_the_security_log_never_sends_a_copy_on_its_own(self, tmp_path):
        """The security log is this machine's account of what happened on it, and something
        appends to it every minute — the automations' heartbeat, every backup and sync run.
        Counted, every export was new and every sync sent a whole copy. Its rows ride the next
        copy that is sent."""
        store, a = Store(), tmp_path / "A"
        _task(a, "t1", "hello")
        _sel(a, "dashboard:owner", 1)
        _cycle(store, a, "A", 0)

        _sel(a, "autonomy:action.spawn_turn", 2)
        _sel(a, "durability:service", 3)
        assert _cycle(store, a, "A", 15).unchanged_since == 1

        _task(a, "t1", "hello again")
        assert _cycle(store, a, "A", 30).seq_published == 2

    def test_an_automations_run_times_never_send_a_copy_and_an_edit_of_it_does(self, tmp_path):
        """An automation's run times, next fire and switch change in this home each time it runs,
        and never reach another home (``triggers.store.what_it_is``): the heartbeat runs every
        minute, and each sync sent a whole copy for it. An edit of what it is sends one."""
        store, a = Store(), tmp_path / "A"
        a.mkdir(parents=True)

        def automations(name: str, runs: int) -> None:
            row = {
                "id": "trg1",
                "name": name,
                "enabled": runs % 2 == 0,
                "run_count": runs,
                "last_fired_at": _at(runs),
                "next_fire_at": _at(runs + 1),
            }
            body = {"version": 1, "saved_at": runs, "triggers": [row]}
            (a / "triggers.json").write_text(json.dumps(body), encoding="utf-8")

        automations("Heartbeat", 1)
        _cycle(store, a, "A", 0)

        automations("Heartbeat", 2)
        assert _cycle(store, a, "A", 15).unchanged_since == 1

        automations("Morning heartbeat", 3)
        assert _cycle(store, a, "A", 30).seq_published == 2

    def test_a_store_that_lacks_this_machines_newest_copy_is_sent_one(self, tmp_path):
        """A store that does not hold the copy this machine last sent — another folder, a
        transport just switched to, a folder someone cleared — is sent one, changed or not."""
        a = tmp_path / "A"
        _task(a, "t1", "hello")
        first = Store()
        _cycle(first, a, "A", 0)

        assert _cycle(Store(), a, "A", 15).seq_published == 1  # a new store

        for key in [k for k in first.objects if k.startswith(shard_prefix("A", 1))]:
            del first.objects[key]  # cleared, though its registry still names seq 1
        assert _cycle(first, a, "A", 30).seq_published == 2


class TestACopyANewerOneReplacedIsRemoved:
    def test_it_stays_until_the_newer_one_has_stood_fifteen_minutes(self, tmp_path):
        store, a = Store(), tmp_path / "A"
        _task(a, "t1", "one")
        _cycle(store, a, "A", 0)
        _task(a, "t1", "two")
        assert _cycle(store, a, "A", 15).seq_published == 2
        assert store.copies("A") == {1, 2}  # a machine may be reading seq 1 right now

        assert _cycle(store, a, "A", 29).copies_removed == []
        assert store.copies("A") == {1, 2}

        last = _cycle(store, a, "A", 15 + KEEP_PREVIOUS_SECS / 60)

        assert last.copies_removed == [1] and "removed 1 older copy" in last.detail
        assert store.copies("A") == {2}
        assert not [k for k in store.objects if k.startswith(shard_prefix("A", 1))]

    def test_the_copy_before_the_previous_goes_when_a_newer_one_lands(self, tmp_path):
        store, a = Store(), tmp_path / "A"
        for n, minutes in enumerate((0, 20, 40), start=1):
            _task(a, "t1", f"edit {n}")
            report = _cycle(store, a, "A", minutes)
        assert report.seq_published == 3
        assert store.copies("A") == {2, 3}

    def test_only_this_machines_own_copies_are_removed(self, tmp_path):
        store = Store()
        b = tmp_path / "B"
        _task(b, "tb", "from B")
        _cycle(store, b, "B", 0)
        theirs = {k: v for k, v in store.objects.items() if k != REGISTRY_KEY}
        a = tmp_path / "A"
        for n, minutes in enumerate((1, 21, 41, 61), start=1):
            _task(a, "ta", f"edit {n}")
            _cycle(store, a, "A", minutes)

        assert store.copies("A") == {3, 4}  # 3 was replaced just now, so it stays a while
        assert {k: store.objects[k] for k in theirs} == theirs  # B's copy, untouched
        assert all(seq_of_key("A", k) is not None for k in store.removed)

    def test_a_transport_that_keeps_every_copy_is_never_asked_to_remove_one(self, tmp_path):
        store, a = KeepingStore(), tmp_path / "A"
        for n, minutes in enumerate((0, 20, 40), start=1):
            _task(a, "t1", f"edit {n}")
            _cycle(store, a, "A", minutes)
        assert _cycle(store, a, "A", 60).unchanged_since == 3
        assert store.copies("A") == {1, 2, 3}

    def test_a_removal_that_fails_does_not_fail_the_sync(self, tmp_path):
        class Busy(Store):
            refuse = True

            def remove(self, keys):
                if self.refuse:
                    raise OSError("the sync folder is busy")
                return super().remove(keys)

        store, a = Busy(), tmp_path / "A"
        _task(a, "t1", "one")
        _cycle(store, a, "A", 0)
        _task(a, "t1", "two")
        _cycle(store, a, "A", 15)

        failed = _cycle(store, a, "A", 31)
        assert "older copies not removed (the sync folder is busy)" in failed.detail
        assert store.copies("A") == {1, 2}

        store.refuse = False
        assert _cycle(store, a, "A", 46).copies_removed == [1]

    def test_a_key_the_transport_will_not_remove_is_named_in_the_report(self, tmp_path):
        """Only a link someone put in this machine's own folder of the store is refused there: the
        report names it, as it names any refused key, and the copy stays."""

        class Linked(Store):
            def remove(self, keys):
                raise KeysRefused("won't remove a key", {keys[0]: "is a link in the folder"})

        store, a = Linked(), tmp_path / "A"
        _task(a, "t1", "one")
        _cycle(store, a, "A", 0)
        _task(a, "t1", "two")
        _cycle(store, a, "A", 15)

        report = _cycle(store, a, "A", 31)

        assert report.refused == {shard_prefix("A", 1) + "manifest.json": "is a link in the folder"}
        assert "older copies not removed" in report.detail and store.copies("A") == {1, 2}

    def test_the_outbox_keeps_no_entry_for_a_copy_the_store_no_longer_has(self, tmp_path):
        store, a = Store(), tmp_path / "A"
        for n, minutes in enumerate((0, 20, 40, 60), start=1):
            _task(a, "t1", f"edit {n}")
            _cycle(store, a, "A", minutes)
        _cycle(store, a, "A", 80)
        assert store.copies("A") == {4}
        assert [e.seq for e in Outbox(a / "sync").all_entries()] == [4]


class TestAPublishThatDidNotLand:
    def test_it_reads_as_a_failed_push(self, tmp_path):
        """A push the store did not take (a folder whose drive is unplugged answers ``transient``)
        sent nothing, and the run said so: it read as a sync that went through."""

        class Unplugged(Store):
            def push(self, objects):
                return PushResult(outcome="transient", detail="The sync folder isn't there.")

        a = tmp_path / "A"
        _task(a, "t1", "hello")
        report = run_sync_cycle(Unplugged(), a, self_id="A", now=_at(0))

        assert (report.ok, report.failure) == (False, "push")
        assert report.error == "push: The sync folder isn't there."

    def test_a_registry_that_never_takes_the_seq_reads_as_a_failed_push(self, tmp_path):
        class Locked(Store):
            def cas_registry(self, expected_sha, data):
                return False

        a = tmp_path / "A"
        _task(a, "t1", "hello")
        report = run_sync_cycle(Locked(), a, self_id="A", now=_at(0))

        assert (report.ok, report.failure) == (False, "push")
        assert report.error == (
            "push: the shared registry changed under each of 5 tries to name this copy"
        )

    def test_what_it_left_is_cleared_before_its_seq_is_sent_again(self, tmp_path):
        """A failed publish's objects stay under a seq no peer reads, and the next publish takes
        the same number. Insert-only, its push kept them beside its own export, and a peer read
        one copy made of two."""

        class HalfWay(Store):
            broken = True

            def push(self, objects):
                if self.broken:
                    for o in objects[: len(objects) // 2]:
                        self.objects.setdefault(o.key, o.data)
                    return PushResult(outcome="transient", detail="the disk filled up")
                return super().push(objects)

        store, a, b = HalfWay(), tmp_path / "A", tmp_path / "B"
        _task(a, "t1", "before")
        assert run_sync_cycle(store, a, self_id="A", now=_at(0)).failure == "push"
        assert [k for k in store.objects if k.startswith(shard_prefix("A", 1))]

        store.broken = False
        _task(a, "t1", "after")
        assert _cycle(store, a, "A", 15).seq_published == 1

        landed = tmp_path / "landed"
        for key, data in store.objects.items():
            if key.startswith(shard_prefix("A", 1)):
                target = landed / key[len(shard_prefix("A", 1)) :]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        import_shards(landed)  # one whole export: validates, nothing left from the first try
        _cycle(store, b, "B", 16)
        assert _title(b, "t1") == "after"


class TestAMachineThatWasAwayCatchesUpFromTheNewestCopy:
    def test_it_reads_one_copy_and_takes_every_change_and_delete(self, tmp_path):
        store, a, b = Store(), tmp_path / "A", tmp_path / "B"
        _task(a, "t1", "to delete")
        _task(a, "t2", "to edit")
        _cycle(store, a, "A", 0)
        _cycle(store, b, "B", 1)
        _cycle(store, a, "A", 2)  # the two are in step
        assert (_title(b, "t1"), _title(b, "t2")) == ("to delete", "to edit")

        # B is away while A deletes one task, edits another, makes a third: many copies.
        (a / "tasks" / "t1.json").unlink()
        _cycle(store, a, "A", 20)
        _task(a, "t2", "edited while B was away")
        _cycle(store, a, "A", 40)
        _task(a, "t3", "made while B was away")
        _cycle(store, a, "A", 60)
        _cycle(store, a, "A", 80)
        assert store.copies("A") == {4}  # only the newest is left

        read: list[str] = []
        real_list = store.list_remote
        store.list_remote = lambda prefix="": read.append(prefix) or real_list(prefix)
        back = _cycle(store, b, "B", 600)

        assert shard_prefix("A", 4) in read and shard_prefix("A", 2) not in read
        assert (_title(b, "t1"), _title(b, "t2"), _title(b, "t3")) == (
            None,
            "edited while B was away",
            "made while B was away",
        )
        assert back.conflicts == 0

    def test_an_edit_made_on_a_version_taken_from_here_is_taken_without_a_conflict(self, tmp_path):
        """B took A's edit and edited the record again before A read B. A reads only B's newest
        copy — the one that held A's version is gone — and B's copy says it took that version,
        so B's edit is an edit, not a divergence for review."""
        store, a, b = Store(), tmp_path / "A", tmp_path / "B"
        _task(a, "t1", "made on A")
        _cycle(store, a, "A", 0)
        _cycle(store, b, "B", 1)
        _cycle(store, a, "A", 2)  # A reads B's copy: the two agree on "made on A"

        _task(a, "t1", "edited on A")
        _cycle(store, a, "A", 20)
        _cycle(store, b, "B", 21)  # B takes A's edit, and its copy holds it
        assert _title(b, "t1") == "edited on A"
        _task(b, "t1", "edited on B")
        _cycle(store, b, "B", 40)
        _cycle(store, b, "B", 60)  # B's copy that held A's version is removed
        assert store.copies("B") == {3}

        report = _cycle(store, a, "A", 61)

        assert _title(a, "t1") == "edited on B"
        assert report.conflicts == 0


def test_a_copy_lands_with_the_time_it_landed_in_the_registry(tmp_path):
    store, a = Store(), tmp_path / "A"
    _task(a, "t1", "hello")
    _cycle(store, a, "A", 0)
    entry = Registry.loads(store.objects[REGISTRY_KEY]).machines["A"]
    assert (entry.seq, entry.last_export_at) == (1, _at(0))


@pytest.mark.parametrize("now", ["", "now", "t1"])
def test_without_a_time_that_reads_as_one_nothing_is_removed(tmp_path, now):
    store, a = Store(), tmp_path / "A"
    for n in range(3):
        _task(a, "t1", f"edit {n}")
        report = run_sync_cycle(store, a, self_id="A", now=now)
        assert report.ok
    assert store.copies("A") == {1, 2, 3} and store.removed == []
