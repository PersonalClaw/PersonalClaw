"""DURABILITY-AND-SYNC §4.1 / DAS-6c-ii-f — the transport-driven push half of the cycle.

Publish a local export as this machine's next seq and announce it via a CAS registry bump,
composing registry.bump + outbox + transport.push/cas_registry. The push obligation is durable
first (survives a crash), a non-delivered push does NOT announce the seq, and a lost CAS race
re-pulls + re-bumps + retries (idempotent object keys make that free).
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.durability.outbox import (
    OUTCOME_TRANSIENT,
    STATUS_DELIVERED,
    STATUS_PENDING,
    Outbox,
    entry_id,
)
from personalclaw.durability.push_engine import publish_export
from personalclaw.durability.registry import Registry, shard_prefix
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)


class FakeTransport(SyncTransportProvider):
    name = "fake"

    def __init__(self, push_outcome="delivered", cas_returns=None):
        self.objects: dict[str, bytes] = {}
        self.registry_bytes: bytes | None = None
        self._push_outcome = push_outcome
        self._cas_returns = list(cas_returns) if cas_returns is not None else None
        self.cas_calls: list = []

    def push(self, objects):
        if self._push_outcome == "delivered":
            for o in objects:
                self.objects.setdefault(o.key, o.data)
            return PushResult(pushed=len(objects), outcome="delivered")
        return PushResult(pushed=0, outcome=self._push_outcome, detail="simulated")

    def list_remote(self, prefix: str = ""):
        return [RemoteRef(key=k) for k in self.objects if k.startswith(prefix)]

    def pull(self, refs):  # pragma: no cover
        return [SyncObject(key=r.key, data=self.objects[r.key]) for r in refs]

    def cas_registry(self, expected_sha, data):
        self.cas_calls.append(expected_sha)
        if self._cas_returns is not None:
            ok = self._cas_returns.pop(0) if self._cas_returns else False
        else:
            ok = True
        if ok:
            self.registry_bytes = data
        return ok

    def test(self):  # pragma: no cover
        return ConnectionResult(ok=True)


def _export_dir(tmp_path) -> Path:
    d = tmp_path / "export"
    (d / "tasks").mkdir(parents=True)
    (d / "tasks" / "entities.jsonl").write_text('{"id":"a","data":{}}\n', encoding="utf-8")
    (d / "manifest.json").write_text('{"schema_version":1}', encoding="utf-8")
    return d


class TestPublish:
    def test_publishes_objects_and_commits_registry(self, tmp_path):
        tr = FakeTransport()
        reg = Registry()
        ob = Outbox(tmp_path / "sync")
        report = publish_export(
            tr, _export_dir(tmp_path), reg, ob, self_id="me", manifest_sha="sha1", now="t"
        )
        assert report.seq == 1 and report.registry_committed
        # Objects landed under this machine's seq prefix.
        prefix = shard_prefix("me", 1)
        assert any(k.startswith(prefix) for k in tr.objects)
        # The outbox entry is delivered; the registry knows our seq.
        assert ob.get(entry_id("fake", 1)).status == STATUS_DELIVERED
        assert Registry.loads(tr.registry_bytes).seq_of("me") == 1

    def test_obligation_is_recorded_before_push(self, tmp_path):
        # Even a transient push leaves a durable pending outbox entry to retry.
        tr = FakeTransport(push_outcome="transient")
        reg = Registry()
        ob = Outbox(tmp_path / "sync")
        report = publish_export(
            tr, _export_dir(tmp_path), reg, ob, self_id="me", manifest_sha="s", now="t"
        )
        assert not report.registry_committed  # a failed push must NOT announce the seq
        assert ob.get(entry_id("fake", 1)).status == STATUS_PENDING
        assert tr.registry_bytes is None  # registry untouched

    def test_transient_push_does_not_announce_seq(self, tmp_path):
        tr = FakeTransport(push_outcome="transient")
        report = publish_export(
            tr,
            _export_dir(tmp_path),
            Registry(),
            Outbox(tmp_path / "s"),
            self_id="me",
            manifest_sha="s",
            now="t",
        )
        assert report.push_outcome == OUTCOME_TRANSIENT and report.cas_attempts == 0

    def test_seq_is_monotonic_across_publishes(self, tmp_path):
        tr = FakeTransport()
        reg = Registry()
        ob = Outbox(tmp_path / "sync")
        export = _export_dir(tmp_path)  # same export dir, published twice
        publish_export(tr, export, reg, ob, self_id="me", manifest_sha="s1", now="t1")
        r2 = publish_export(tr, export, reg, ob, self_id="me", manifest_sha="s2", now="t2")
        assert r2.seq == 2 and Registry.loads(tr.registry_bytes).seq_of("me") == 2


class TestCasRetry:
    def test_lost_race_then_win_reload_and_retry(self, tmp_path):
        # First CAS loses, second wins. The reloader returns a registry where a peer moved.
        tr = FakeTransport(cas_returns=[False, True])
        reg = Registry()

        def reload():
            remote = Registry()
            remote.bump("peer", manifest_sha="p", now="t")  # a peer published while we raced
            return remote

        report = publish_export(
            tr,
            _export_dir(tmp_path),
            reg,
            Outbox(tmp_path / "s"),
            self_id="me",
            manifest_sha="s",
            now="t",
            reload_registry=reload,
        )
        assert report.registry_committed and report.cas_attempts == 2
        committed = Registry.loads(tr.registry_bytes)
        # Both our seq and the peer's survived the merge-on-retry.
        assert committed.seq_of("me") == 1 and committed.seq_of("peer") == 1

    def test_no_reloader_gives_up_on_cas_miss(self, tmp_path):
        tr = FakeTransport(cas_returns=[False])
        report = publish_export(
            tr,
            _export_dir(tmp_path),
            Registry(),
            Outbox(tmp_path / "s"),
            self_id="me",
            manifest_sha="s",
            now="t",
        )
        assert not report.registry_committed and "no reloader" in report.detail

    def test_persistent_race_gives_up_bounded(self, tmp_path):
        tr = FakeTransport(cas_returns=[False, False, False, False, False, False])

        def reload():
            return Registry()

        report = publish_export(
            tr,
            _export_dir(tmp_path),
            Registry(),
            Outbox(tmp_path / "s"),
            self_id="me",
            manifest_sha="s",
            now="t",
            reload_registry=reload,
        )
        assert not report.registry_committed
        assert report.cas_attempts == 5  # bounded, not infinite
