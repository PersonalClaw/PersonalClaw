"""Claim leases — the flock-guarded on-disk half of the pure claim logic.

The pure decision (`containers.claim`/`release`) is covered in `test_workflows_containers`.
Here the load-bearing properties are the ones only the FILE + flock give:

* a claim survives the process that took it (the file, not the lock, is the lease);
* an EXPIRED claim on disk is treated as free — a crashed holder frees the work after its
  TTL without an admin step;
* a second holder is refused while the first still holds, and contention (the flock already
  held) is a distinct, non-crashing outcome;
* only the holder may release.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.workflows import leases


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch) -> Path:
    """Patch the config dir the leases module resolves through. `config_dir` is imported
    by value in both leases and concurrency, so patch it at the leaves that read it."""
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.concurrency.config_dir", lambda: tmp_path)
    return tmp_path


class TestRoundTrip:
    def test_acquire_writes_a_readable_lease(self) -> None:
        granted, reason = leases.acquire_claim("run-a", "worker-1", ttl=300)
        assert granted is not None and reason == ""
        assert granted.holder == "worker-1"
        # The FILE is the lease: it survives this call and reads back identically.
        read = leases.read_claim("run-a")
        assert read is not None and read.holder == "worker-1"
        assert read.expires_at == granted.expires_at

    def test_absent_claim_reads_as_none(self) -> None:
        assert leases.read_claim("never-claimed") is None

    def test_a_corrupt_lease_reads_as_none_not_a_crash(self) -> None:
        path = leases._lease_path("run-x")
        path.write_text("{not json", encoding="utf-8")
        assert leases.read_claim("run-x") is None

    def test_release_unlinks_the_file(self) -> None:
        leases.acquire_claim("run-b", "worker-1")
        remaining, reason = leases.release_claim("run-b", "worker-1")
        assert remaining is None and reason == ""
        assert leases.read_claim("run-b") is None
        assert not leases._lease_path("run-b").exists()


class TestExclusion:
    def test_a_second_holder_is_refused_while_the_first_holds(self) -> None:
        first, _ = leases.acquire_claim("run-c", "worker-1", ttl=300)
        assert first is not None
        second, reason = leases.acquire_claim("run-c", "worker-2", ttl=300)
        assert second is None
        assert "worker-1" in reason
        # The first holder's lease is untouched by the refused attempt.
        assert leases.read_claim("run-c").holder == "worker-1"

    def test_the_same_holder_renews_rather_than_being_refused(self) -> None:
        first, _ = leases.acquire_claim("run-d", "worker-1", ttl=300)
        again, reason = leases.acquire_claim("run-d", "worker-1", ttl=300)
        assert again is not None and reason == ""
        assert again.renewals == first.renewals + 1

    def test_only_the_holder_may_release(self) -> None:
        leases.acquire_claim("run-e", "worker-1")
        remaining, reason = leases.release_claim("run-e", "worker-2")
        assert remaining is not None  # foreign release refused
        assert "worker-1" in reason
        assert leases.read_claim("run-e").holder == "worker-1"


class TestExpiry:
    def test_an_expired_lease_is_reclaimable_by_anyone(self, monkeypatch) -> None:
        """A crashed holder leaves a lease that expires; after the TTL, the work is free —
        this is what makes the board truthful across a kill without a cleanup process."""
        import personalclaw.workflows.leases as L

        clock = [1000.0]
        monkeypatch.setattr(L.time, "time", lambda: clock[0])
        first, _ = L.acquire_claim("run-f", "worker-1", ttl=10)
        assert first is not None
        # Before expiry, a different worker is refused.
        assert L.acquire_claim("run-f", "worker-2", ttl=10)[0] is None
        # After the TTL, the stale lease is treated as free and re-granted.
        clock[0] = 1011.0
        second, reason = L.acquire_claim("run-f", "worker-2", ttl=10)
        assert second is not None and reason == ""
        assert second.holder == "worker-2"


class TestContention:
    def test_a_held_flock_yields_contended_not_a_crash(self, monkeypatch) -> None:
        """When the flock is already held by another live process, single_flight yields
        False. That is a normal, non-crashing outcome the board renders, not an error."""
        import contextlib

        import personalclaw.workflows.leases as L

        @contextlib.contextmanager
        def _busy(_key):
            yield False

        monkeypatch.setattr(L, "single_flight", _busy)
        granted, reason = L.acquire_claim("run-g", "worker-1")
        assert granted is None and reason == "contended"
        released, reason2 = L.release_claim("run-g", "worker-1")
        assert reason2 == "contended"
