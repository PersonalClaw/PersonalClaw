"""A key the sync transport refuses is named in the sync report, and nothing of it comes in.

A transport whose remote is a folder on this machine refuses a key that leads out of that folder,
a link someone put there, say (``sync_transports.base.KeysRefused``). The cycle had no way to hear
it: a refusal could only be dropped, which reads as an object the remote no longer has, or raised
like any failure, which stopped the whole cycle on one peer's change and named nothing.

Now a peer's change a key of it is refused in is refused whole, the way a change naming a path
outside its export is, and the cursor moves past it; a refused registry, salt or push fails the
cycle with the transport's own words, and the push isn't chased again. The report names the keys
either way, so the sync job says what was refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.durability import service
from personalclaw.durability.crypto import SALT_KEY
from personalclaw.durability.cursor import Cursor
from personalclaw.durability.outbox import STATUS_GIVEN_UP, Outbox
from personalclaw.durability.registry import REGISTRY_KEY, shard_prefix
from personalclaw.durability.sync_cycle import run_sync_cycle
from personalclaw.sdk.sync import KeysRefused, is_path_in_store, is_safe_relative_path
from tests.test_durability_sync_cycle import SharedStore

#: The transport's own why for a key, as it would give it.
WHY = "leads out of the folder through a link"
#: The transport's own sentence, which a failed cycle's error carries.
SAYS = "The transport won't reach a key that leads out of its folder. Take the link out."


class Refusing(SharedStore):
    """The store two machines share, with *refused* keys the transport won't reach in *where*
    (``list_remote``, ``pull`` or ``push``), refused as a folder transport refuses them."""

    def __init__(self, shared: SharedStore, refused: dict[str, str], where: str) -> None:
        super().__init__()
        self.objects = shared.objects
        self.refused = refused
        self.where = where

    def _refuse(self, where: str, keys: list[str]) -> None:
        hit = {key: why for key, why in self.refused.items() if key in keys}
        if where == self.where and hit:
            raise KeysRefused(SAYS, hit)

    def list_remote(self, prefix: str = ""):
        self._refuse("list_remote", [key for key in self.refused if key.startswith(prefix)])
        return super().list_remote(prefix)

    def pull(self, refs):
        self._refuse("pull", [ref.key for ref in refs])
        return super().pull(refs)

    def push(self, objects):
        self._refuse("push", [obj.key for obj in objects])
        return super().push(objects)


def _task(home: Path, tid: str) -> None:
    (home / "tasks").mkdir(parents=True, exist_ok=True)
    (home / "tasks" / f"{tid}.json").write_text(json.dumps({"id": tid, "title": tid}))


def _b_publishes(tmp_path: Path) -> SharedStore:
    """Machine B's first sync, published at seq 1: one task."""
    b = tmp_path / "B"
    _task(b, "from-b")
    shared = SharedStore()
    assert run_sync_cycle(shared, b, self_id="B", now="t").ok
    assert f"{shard_prefix('B', 1)}manifest.json" in shared.objects
    return shared


def _a_syncs(transport, tmp_path: Path, **kwargs):
    a = tmp_path / "A"
    a.mkdir(exist_ok=True)
    return a, run_sync_cycle(transport, a, self_id="A", now="t2", **kwargs)


# ── the rule a transport holds its keys to ─────────────────────────────────────────────────


def test_a_transport_holds_its_keys_to_the_rule_the_sync_holds_a_peers_paths_to(tmp_path):
    """``personalclaw.sdk.sync`` publishes the sync's own rule (``record_ids``), so a transport
    whose remote is a folder refuses what the cycle would: a key that climbs out, and one that
    leads out through a link someone put in the folder, while one that stays inside passes."""
    from personalclaw import record_ids

    assert is_path_in_store is record_ids.is_path_in_store
    assert is_safe_relative_path is record_ids.is_safe_relative_path
    folder, elsewhere = tmp_path / "folder", tmp_path / "elsewhere"
    (folder / "machines" / "b").mkdir(parents=True)
    elsewhere.mkdir()
    (folder / "machines" / "c").symlink_to(elsewhere, target_is_directory=True)
    (folder / "machines" / "b" / "x.jsonl").symlink_to(tmp_path / "outside.txt")

    assert is_path_in_store(folder, "machines/b/seq-0001/entities.jsonl")
    assert not is_safe_relative_path("../outside.txt")
    assert not is_path_in_store(folder, "../outside.txt")
    assert is_safe_relative_path("machines/c/seq-0001/x.jsonl"), "the name alone is plain"
    assert not is_path_in_store(folder, "machines/c/seq-0001/x.jsonl"), "a folder on the way"
    assert not is_path_in_store(folder, "machines/b/x.jsonl"), "the key itself leads out"
    assert is_path_in_store(
        folder, "machines/b/x.jsonl", follow_last=False
    ), "a removal takes a link at the key as itself"


# ── a peer's change ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("where", ["list_remote", "pull"])
def test_a_change_with_a_key_the_transport_refuses_is_refused_whole(tmp_path, where):
    shared = _b_publishes(tmp_path)
    key = f"{shard_prefix('B', 1)}manifest.json"
    a, report = _a_syncs(Refusing(shared, {key: WHY}, where), tmp_path)
    assert report.ok, f"one peer's change stopped the whole cycle: {report.detail}"
    assert not (a / "tasks" / "from-b.json").exists(), "the rest of the change came in"
    assert report.refused == {key: WHY}
    assert "refused 1 path(s)" in report.detail and f"{key} ({WHY})" in report.detail
    assert Cursor(a / "sync").seq_of("B") == 1, "the change is moved past, never pulled again"


def test_the_control_a_change_nothing_refuses_comes_in(tmp_path):
    shared = _b_publishes(tmp_path)
    a, report = _a_syncs(Refusing(shared, {}, "pull"), tmp_path)
    assert report.ok and not report.refused
    assert (a / "tasks" / "from-b.json").exists()


# ── the registry and the salt, without which nothing syncs ─────────────────────────────────


def test_a_refused_registry_fails_the_cycle_and_is_named(tmp_path):
    shared = _b_publishes(tmp_path)
    a, report = _a_syncs(Refusing(shared, {REGISTRY_KEY: WHY}, "pull"), tmp_path)
    assert not report.ok
    assert report.error == f"pull: {SAYS}"
    assert report.refused == {REGISTRY_KEY: WHY}
    assert not (a / "tasks" / "from-b.json").exists()


def test_a_refused_salt_fails_the_cycle_and_is_named(tmp_path, monkeypatch):
    from personalclaw.durability import crypto

    monkeypatch.setattr(crypto, "load_passphrase", lambda: "a passphrase of the test's own")
    shared = _b_publishes(tmp_path)
    _, report = _a_syncs(Refusing(shared, {SALT_KEY: WHY}, "list_remote"), tmp_path, encrypt="on")
    assert not report.ok
    assert report.error == f"pull: {SAYS}"
    assert report.refused == {SALT_KEY: WHY}


# ── this machine's push ─────────────────────────────────────────────────────────────────────


def test_a_refused_push_fails_the_cycle_is_named_and_is_not_chased(tmp_path):
    shared = _b_publishes(tmp_path)
    key = f"{shard_prefix('A', 1)}manifest.json"
    a, report = _a_syncs(Refusing(shared, {key: WHY}, "push"), tmp_path)
    assert not report.ok
    assert report.error == f"push: {SAYS}"
    assert report.refused == {key: WHY}
    assert (a / "tasks" / "from-b.json").exists(), "the pull before it stands"
    assert (
        "A" not in json.loads(shared.objects[REGISTRY_KEY])["machines"]
    ), "a seq whose objects never landed was announced"
    (entry,) = Outbox(a / "sync").all_entries()
    assert (entry.status, entry.detail) == (
        STATUS_GIVEN_UP,
        SAYS,
    ), "the outbox would chase a push the transport refuses every time"


# ── the sync job: its detail, its audit, its outcome ────────────────────────────────────────


class _Cfg:
    sync_enabled = True
    sync_transport = "shared"
    sync_encrypt = "off"
    sync_stale_after_secs = 900
    restore_drills = False
    time_travel = False


def test_the_sync_job_says_what_the_transport_refused(tmp_path, monkeypatch):
    from personalclaw.sync_transports import registry

    shared = _b_publishes(tmp_path)
    key = f"{shard_prefix('B', 1)}manifest.json"
    a = tmp_path / "A"
    a.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(a))
    monkeypatch.setattr(service, "_cfg", lambda: _Cfg())
    audited: list[tuple[str, str, str]] = []

    def audit(event: str, resources: str, *, outcome: str = "allowed") -> None:
        audited.append((event, resources, outcome))

    monkeypatch.setattr(service, "_audit", audit)
    registry.register_transport(Refusing(shared, {key: WHY}, "pull"))
    try:
        result = service.run_sync_job()
    finally:
        registry.unregister_transport("shared")
    assert not result.ok, "a refusal is the one thing in the report its owner must look at"
    assert result.extra["refused"] == {key: WHY}
    assert f"{key} ({WHY})" in result.detail
    assert audited == [("durability_sync", result.detail, "denied")]
