"""A sync run that fails reads as failed: on the Backups card, in the Doctor and in a notification.

A folder sync set up with the default encryption fails every run until a passphrase is stored,
because the cycle refuses to send plaintext to storage the user chose to encrypt. That refusal is
right, and it stays. What was wrong is everything around it: the run was recorded only as the
schedule stamp, so the Backups page read "Last sync just now · Shards leaving this machine
encrypted" while nothing had left the machine; the only place that said why was the gateway log;
the Doctor said nothing; and no note was raised.

The transport here is an in-memory folder named like the shipped folder transport, so the
encryption table encrypts it by default, and nothing is written outside the test's own home.
"""

from __future__ import annotations

import json
import time

import pytest

from personalclaw.config.credentials import save_credential
from personalclaw.durability import service
from personalclaw.durability.crypto import PASSPHRASE_CREDENTIAL, is_ciphertext, is_routing_key
from personalclaw.resilience import doctor
from personalclaw.resilience.doctor import DoctorContext, Tier
from personalclaw.sync_transports import registry
from personalclaw.sync_transports.base import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)

#: An invented passphrase: the tests assert it never appears in anything the gateway says.
PASSPHRASE = "invented-sync-passphrase-for-a-test-7c41"


class MemoryFolder(SyncTransportProvider):
    """An insert-only folder in memory, under the shipped folder transport's name."""

    name = "dir-sync"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def push(self, objects):
        for obj in objects:
            self.objects.setdefault(obj.key, obj.data)
        return PushResult(pushed=len(objects), outcome="delivered")

    def list_remote(self, prefix=""):
        return [RemoteRef(key=k) for k in self.objects if k.startswith(prefix)]

    def pull(self, refs):
        return [
            SyncObject(key=r.key, data=self.objects[r.key]) for r in refs if r.key in self.objects
        ]

    def cas_registry(self, expected, data):
        self.objects["registry.json"] = data
        return True

    def test(self):
        return ConnectionResult(ok=True)


class _Cfg:
    def __init__(self, *, enabled: bool = True, transport: str = "dir-sync") -> None:
        self.sync_enabled = enabled
        self.sync_transport = transport
        self.sync_stale_after_secs = 900
        self.sync_encrypt = "auto"
        self.restore_drills = False
        self.time_travel = False


class _Notes:
    """A `DashboardState.notify`-shaped recorder."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str, dict]] = []

    def __call__(self, kind, title, body, *, meta=None, **_):
        self.seen.append((kind, title, body, dict(meta or {})))

    def titled(self, title: str) -> list[tuple[str, str, str, dict]]:
        return [n for n in self.seen if n[1] == title]


@pytest.fixture
def folder(monkeypatch, unset_env):
    """Sync on, through the folder transport, with every non-sync job stubbed out."""
    # Registered before the test: `save_credential` mirrors into os.environ behind monkeypatch's
    # back.
    unset_env(PASSPHRASE_CREDENTIAL)
    monkeypatch.setattr(service, "_cfg", lambda: _Cfg())
    for job, name in (
        ("run_incremental_export", "export"),
        ("run_history_commit", "history"),
        ("run_nightly_snapshot", "snapshot"),
    ):
        monkeypatch.setattr(
            service, job, lambda *a, _n=name, **k: service.JobResult(_n, skipped="stub")
        )
    transport = MemoryFolder()
    registry.register_transport(transport)
    try:
        yield transport
    finally:
        registry.unregister_transport("dir-sync")


def _sync() -> dict:
    return service.status()["sync"]


def test_a_run_that_fails_for_want_of_a_passphrase_reads_as_failed(folder):
    started = time.time()
    service.run_due_jobs(now=started, notifier=_Notes())

    sync = _sync()
    assert sync["last_run"] == started, "the run happened, so it is the last run"
    assert sync["ok"] is False, "a failed run must not read as a sync"
    assert sync["last_success"] == 0
    problem = sync["problem"]
    assert problem["code"] == "passphrase"
    # Plain words: what happened and why, not the log line with its credential variable.
    assert "passphrase" in problem["message"].lower()
    assert "nothing was synced" in problem["message"].lower()
    assert PASSPHRASE_CREDENTIAL not in problem["message"]
    assert not problem["message"].lower().startswith("error")
    assert problem["remedy"], "a failure says what to do about it"
    assert problem["failures"] == 1 and problem["since"] == started
    # The refusal itself is unchanged: nothing reached the folder.
    assert folder.objects == {}


def test_the_card_can_tell_a_passphrase_is_needed_before_the_first_run(folder):
    sync = _sync()
    assert sync["last_run"] == 0 and sync["ok"] is None, "no run yet is neither a pass nor a fail"
    assert sync["encrypted"] is True
    assert sync["passphrase_stored"] is False
    assert sync["passphrase_credential"] == PASSPHRASE_CREDENTIAL

    save_credential(PASSPHRASE_CREDENTIAL, PASSPHRASE)

    assert _sync()["passphrase_stored"] is True
    assert PASSPHRASE not in json.dumps(service.status()), "presence only, never the value"


def test_a_failing_sync_is_announced_once_and_its_recovery_once(folder):
    notes = _Notes()
    t0 = time.time()
    service.run_due_jobs(now=t0, notifier=notes)
    service.run_due_jobs(now=t0 + 1000, notifier=notes)  # past the 900 s window: it runs again

    failed = notes.titled("Sync failed")
    assert len(failed) == 1, "one note per failing streak, not one every fifteen minutes"
    kind, _title, body, meta = failed[0]
    assert kind == "warning"
    assert "passphrase" in body.lower()
    assert meta.get("statusUrl") == "#/settings/durability"
    assert _sync()["problem"]["failures"] == 2
    assert _sync()["problem"]["since"] == t0

    save_credential(PASSPHRASE_CREDENTIAL, PASSPHRASE)
    service.run_due_jobs(now=t0 + 2000, notifier=notes)

    sync = _sync()
    assert sync["ok"] is True and sync["problem"] is None
    assert sync["last_success"] == t0 + 2000
    recovered = notes.titled("Sync is working again")
    assert len(recovered) == 1 and recovered[0][0] == "info"
    assert notes.titled("Sync failed") == failed, "a recovery is not another failure"
    shards = [v for k, v in folder.objects.items() if not is_routing_key(k)]
    assert shards and all(is_ciphertext(v) for v in shards), "the shards left encrypted"
    assert all(PASSPHRASE not in f"{n[1]} {n[2]} {n[3]}" for n in notes.seen)


def test_a_new_reason_is_announced_again(folder, monkeypatch):
    notes = _Notes()
    t0 = time.time()
    service.run_due_jobs(now=t0, notifier=notes)

    def unreachable(*_a, **_k):
        raise OSError("the folder is not mounted")

    save_credential(PASSPHRASE_CREDENTIAL, PASSPHRASE)
    monkeypatch.setattr(folder, "list_remote", unreachable)
    service.run_due_jobs(now=t0 + 1000, notifier=notes)

    failed = notes.titled("Sync failed")
    assert len(failed) == 2, "a different reason is news"
    problem = _sync()["problem"]
    assert problem["code"] == "pull"
    assert "the folder is not mounted" in problem["message"]
    assert problem["failures"] == 2, "still one streak: the sync has not worked since t0"


def test_a_transport_error_is_quoted_without_the_login_it_carries(folder, monkeypatch):
    """The transport's own error is quoted so the reason is specific, and an address in it that
    carries a login is shown with the login masked: the card, the note and the Doctor all say it."""
    token = "invented-storage-token-51b9"

    def refused(*_a, **_k):
        raise OSError(f"cannot list https://owner:{token}@storage.example.com/sync: 403 Forbidden")

    save_credential(PASSPHRASE_CREDENTIAL, PASSPHRASE)
    monkeypatch.setattr(folder, "list_remote", refused)
    notes = _Notes()
    service.run_due_jobs(now=time.time(), notifier=notes)

    message = _sync()["problem"]["message"]
    assert "403 Forbidden" in message and "storage.example.com" in message
    assert token not in json.dumps(service.status())
    assert all(token not in n[2] for n in notes.seen)


def test_a_skipped_attempt_is_not_a_sync(folder, monkeypatch):
    monkeypatch.setattr(service, "_cfg", lambda: _Cfg(transport="not-installed"))
    t0 = time.time()
    service.run_due_jobs(now=t0, notifier=_Notes())

    sync = _sync()
    assert sync["last_run"] == 0, "nothing ran, so there is no last sync to show"
    assert sync["ok"] is None
    assert "not installed" in sync["skipped"]
    assert sync["due"] is False, "the skip still holds the schedule to the staleness window"


# ── the Doctor ────────────────────────────────────────────────────────────────


def test_the_sync_probe_is_a_durability_capability_probe() -> None:
    probe = {p.id: p for p in doctor.all_probes()}.get("durability.sync")
    assert probe is not None, "the probe must be registered, not just defined"
    assert probe.capability == "durability"
    assert probe.tier is Tier.CAPABILITY


@pytest.mark.asyncio
async def test_the_doctor_says_a_sync_cannot_run_before_it_has_failed(folder):
    res = await doctor._probe_sync(DoctorContext())
    assert res.ok is False
    assert "passphrase" in res.detail.lower()
    assert "Settings → Backups → Sync" in res.remedy


@pytest.mark.asyncio
async def test_the_doctor_reports_a_sync_that_keeps_failing(folder, monkeypatch):
    t0 = time.time()
    save_credential(PASSPHRASE_CREDENTIAL, PASSPHRASE)

    def unreachable(*_a, **_k):
        raise OSError("the folder is not mounted")

    monkeypatch.setattr(folder, "list_remote", unreachable)
    service.run_due_jobs(now=t0, notifier=_Notes())
    service.run_due_jobs(now=t0 + 1000, notifier=_Notes())

    res = await doctor._probe_sync(DoctorContext())
    assert res.ok is False
    assert "the folder is not mounted" in res.detail
    assert "The last 2 runs all failed." in res.detail
    assert res.remedy
    assert res.evidence["failures"] == 2


@pytest.mark.asyncio
async def test_the_doctor_passes_a_sync_that_works_and_one_that_is_off(folder, monkeypatch):
    save_credential(PASSPHRASE_CREDENTIAL, PASSPHRASE)
    service.run_due_jobs(now=time.time(), notifier=_Notes())
    res = await doctor._probe_sync(DoctorContext())
    assert res.ok is True, res.detail
    assert "last synced" in res.detail

    monkeypatch.setattr(service, "_cfg", lambda: _Cfg(enabled=False))
    res = await doctor._probe_sync(DoctorContext())
    assert res.ok is True and res.detail == "sync is off"
