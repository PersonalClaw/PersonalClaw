"""Device sync brings another home's automations in the way a snapshot merge does: paused, with
nothing of what happened to them there.

The trigger store syncs as one `json_file` row, the whole `triggers.json`, merged `union_by_id`
(`durability/merge.py`). A home with no trigger store took the peer's row as it was, so every
automation arrived switched on and armed with the peer's `next_fire_at`, run count, health and
alert dedupe: the new machine fired the peer's schedule alongside it, and its first alert of a
failure could be swallowed by the peer's. A snapshot merge already drops those fields
(`triggers.store.RUNTIME_FIELDS`, #3774) and brings each automation in switched off; a sync now
applies the same rule, declared on the inventory entry (`StateEntry.arrives`).
"""

from __future__ import annotations

import json
from pathlib import Path

from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.snapshot import _merge_triggers
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.store import RUNTIME_FIELDS, TriggerStore

#: A value for each runtime field that proves it came from the peer.
PEER_RUNTIME = {
    "next_fire_at": "2026-09-01T10:00:00+00:00",
    "last_run_id": "run-peer-17",
    "run_count": 42,
    "last_success_at": "2026-09-01T09:00:00+00:00",
    "last_failure_at": "2026-08-30T09:00:00+00:00",
    "last_waiting_at": "2026-08-29T09:00:00+00:00",
    "last_fired_at": "2026-09-01T09:00:00+00:00",
    "park_retry_after": 1_900_000_000.0,
    "last_alert_hash": "5f1c0ffee",
    "last_alert_at": 1_800_000_000.0,
    "health_status": "failing",
    "last_error_summary": "the peer's outage",
    "state": "parked",
    "enabled": True,
}


def _peer_trigger() -> dict:
    row = Trigger(
        id="clock:morning-digest",
        name="Morning digest",
        kind="clock",
        spec={"kind": "cron", "expr": "0 8 * * *"},
        workflow={"inline": {"provider": "notify", "config": {"title": "digest"}}},
    ).to_dict()
    row.update(PEER_RUNTIME)
    return row


def _peer_store_row() -> dict:
    """The row a peer's shard carries for its trigger store: the whole file."""
    return {
        "id": "triggers.json",
        "data": {"version": 1, "triggers": [_peer_trigger()], "saved_at": 1_790_000_000.0},
    }


def _synced(home: Path) -> reconcile.ReconcileResult:
    entry = inv.by_id("triggers")
    assert entry is not None
    return reconcile.reconcile_entry(home, entry, [_peer_store_row()])


def test_a_peers_automation_arrives_with_nothing_of_what_happened_there(tmp_path) -> None:
    """🔴 Red on main: every runtime field arrived, and the automation arrived switched on."""
    result = _synced(tmp_path)
    assert result.verdict == "consumed" and result.added == 1
    (arrived,) = json.loads((tmp_path / "triggers.json").read_text())["triggers"]
    carried = {
        name: arrived[name] for name in RUNTIME_FIELDS if name in arrived and name != "enabled"
    }
    assert carried == {}, f"the peer's runtime state came with its automation: {carried}"
    assert arrived["enabled"] is False, "another home's switch is not this one's"
    trigger, _issues = parse_trigger(arrived)
    # Unarmed, never run here, and not parked by the peer's outage.
    assert (trigger.next_fire_at, trigger.run_count, trigger.state) == ("", 0, "active")


def test_what_the_automation_is_arrives_whole(tmp_path) -> None:
    """CONTROL: dropping what happened to it must not drop the automation."""
    _synced(tmp_path)
    loaded = TriggerStore(tmp_path).load()
    assert [t.trigger.name for t in loaded] == ["Morning digest"]
    (only,) = loaded
    assert only.issues == [], "the automation must arrive as one this home can run"
    assert only.trigger.spec == {"kind": "cron", "expr": "0 8 * * *"}
    assert only.trigger.workflow == {
        "inline": {"provider": "notify", "config": {"title": "digest"}}
    }


def test_a_sync_and_a_snapshot_merge_bring_an_automation_in_alike(tmp_path) -> None:
    """One rule for both ways a row from another home arrives, so the two cannot drift apart."""
    _synced(tmp_path / "synced")
    (by_sync,) = json.loads((tmp_path / "synced" / "triggers.json").read_text())["triggers"]
    src, dst = tmp_path / "src.json", tmp_path / "dst.json"
    src.write_text(json.dumps({"triggers": [_peer_trigger()]}))
    dst.write_text(json.dumps({"triggers": []}))
    _merge_triggers(src, dst)
    (by_snapshot,) = json.loads(dst.read_text())["triggers"]
    assert by_sync == by_snapshot


def test_the_arrived_store_is_not_recorded_as_agreed_with_the_peer(tmp_path) -> None:
    """🔴 Red on main: the store written here was the peer's row byte for byte, so it became the
    two homes' common ancestor, and once each ran its automations every pull was a conflict.
    What is written here now is not what the peer holds, so it is not an ancestor."""
    assert _synced(tmp_path).new_ancestors == {}


def test_through_the_peers_own_export(tmp_path) -> None:
    """🔴 Red on main. The same, from the peer's real store through the exporter's shards and the
    importer, the rows a pull hands the reconcile — not a row built by hand."""
    from personalclaw.durability.shards import export_shards, import_shards

    peer, here, shards = tmp_path / "peer", tmp_path / "here", tmp_path / "shards"
    peer.mkdir()
    here.mkdir()
    (peer / "triggers.json").write_text(
        json.dumps({"version": 1, "triggers": [_peer_trigger()], "saved_at": 1_790_000_000.0})
    )
    export_shards(peer, shards, entries=["triggers"])
    rows = import_shards(shards, entries=["triggers"]).rows["triggers"]
    entry = inv.by_id("triggers")
    assert entry is not None
    reconcile.reconcile_entry(here, entry, rows)
    (arrived,) = json.loads((here / "triggers.json").read_text())["triggers"]
    assert arrived["enabled"] is False
    assert not set(arrived) & (set(RUNTIME_FIELDS) - {"enabled"}), sorted(arrived)
    assert arrived["name"] == "Morning digest"


def test_a_store_already_here_keeps_its_own_automations(tmp_path) -> None:
    """CONTROL: the whole-file union keeps this home's store, switches and stamps included."""
    local = Trigger(
        id="clock:local",
        name="Local only",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *"},
    )
    row = local.to_dict() | {"enabled": True, "run_count": 3}
    (tmp_path / "triggers.json").write_text(json.dumps({"version": 1, "triggers": [row]}))
    result = _synced(tmp_path)
    assert result.added == 0
    (kept,) = json.loads((tmp_path / "triggers.json").read_text())["triggers"]
    assert (kept["name"], kept["enabled"], kept["run_count"]) == ("Local only", True, 3)
