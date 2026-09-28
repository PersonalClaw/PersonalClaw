"""An automation or a hook from another home arrives by one rule, whichever way it comes.

A row from another home comes in by four paths: a device sync's merge, a sync conflict resolved
with the other machine's version or a drafted merge, a snapshot restore's merge and an archive
import's merge. Each automation (and each lifecycle hook) arrives the same way on all of them:
switched off, with nothing of what happened to it in the other home, and with no grant that home's
owner gave, so switching it on here asks first for what it runs.

Three ways that failed, each measured on the code before this change:

* **The sync took a peer's store only into a home that had none.** ``triggers.json`` is one
  ``json_file`` row, merged ``union_by_id``, so the file this home already had won whole and every
  automation made on the other machine stayed there.
* **Conflict detection hashed the whole file.** Once both homes had run anything — a fire stamp,
  a run count, a switch — all three shas differed and every pull queued the file for review.
* **"Take theirs" wrote the peer's row as it was,** switched on, armed with the peer's next fire,
  and granted by the peer's owner. "Keep mine" wrote back the copy taken when the conflict was
  found, undoing whatever this home had done to the automation since. The snapshot and archive
  merges copied a store in whole into a home with none, switched on and granted.
"""

from __future__ import annotations

import dataclasses
import json
import zipfile
from pathlib import Path

import pytest

from personalclaw.durability import conflict_resolve as resolver
from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.hooks import HOOK_RUNTIME_FIELDS, ScriptHook, ScriptHookStore
from personalclaw.triggers import screen
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.store import RUNTIME_FIELDS, TriggerStore

DIGEST = "clock:morning-digest"
BACKUP = "clock:nightly-backup"

#: What running an automation in a home leaves on its row there.
RAN_HERE = {
    "enabled": True,
    "next_fire_at": "2026-09-02T08:00:00+00:00",
    "last_fired_at": "2026-09-01T08:00:00+00:00",
    "run_count": 3,
    "last_success_at": "2026-09-01T08:00:05+00:00",
}
RAN_THERE = {
    "enabled": True,
    "next_fire_at": "2026-09-02T07:30:00+00:00",
    "last_fired_at": "2026-09-01T07:30:00+00:00",
    "run_count": 11,
    "health_status": "failing",
    "last_error_summary": "the other machine's outage",
}


def _automation(tid: str, name: str, *, expr: str = "0 8 * * *", **extra: object) -> dict:
    row = Trigger(
        id=tid,
        name=name,
        kind="clock",
        spec={"kind": "cron", "expr": expr},
        workflow={"inline": {"provider": "notify", "config": {"title": name}}},
    ).to_dict()
    row.update(extra)
    return row


def _granted(tid: str, name: str, **extra: object) -> dict:
    """An automation whose action runs a shell command, with its owner's yes to it."""
    row = Trigger(
        id=tid,
        name=name,
        kind="clock",
        spec={"kind": "cron", "expr": "0 2 * * *"},
        workflow={"inline": {"provider": "bash", "config": {"command": "/nonexistent/pc-backup"}}},
    ).to_dict()
    row["capabilities"] = {"providers": ["bash"]}
    row.update(extra)
    return row


def _write_store(home: Path, *rows: dict, saved_at: float = 1.0) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "triggers.json").write_text(
        json.dumps({"version": 1, "triggers": list(rows), "saved_at": saved_at}, indent=2)
    )


def _peer(*rows: dict, saved_at: float = 1.0) -> list[dict]:
    """The rows a peer's shard carries for its trigger store: the whole file, as one row."""
    document = {"version": 1, "triggers": list(rows), "saved_at": saved_at}
    return [{"id": "triggers.json", "data": document}]


def _stored(home: Path) -> dict[str, dict]:
    return {t["id"]: t for t in json.loads((home / "triggers.json").read_text())["triggers"]}


def _sync(home: Path, peer_rows: list[dict], **kwargs) -> reconcile.ReconcileResult:
    entry = inv.by_id("triggers")
    assert entry is not None
    return reconcile.reconcile_entry(home, entry, peer_rows, **kwargs)


def _conflict_on_the_schedule(home: Path) -> conflicts_mod.ConflictRecord:
    """One automation both homes agreed on, then each gave a different schedule — the real
    detector's conflict, through two real syncs."""
    queue = conflicts_mod.ConflictQueue(home)
    _write_store(home, _automation(DIGEST, "Morning digest", **RAN_HERE))
    first = _sync(home, _peer(_automation(DIGEST, "Morning digest", **RAN_HERE)), queue=queue)
    ancestors = dict(first.new_ancestors)
    assert ancestors, "the two homes agree on the automation — the fixture is vacuous otherwise"
    _write_store(home, _automation(DIGEST, "Morning digest", expr="0 9 * * *", **RAN_HERE))
    peer = _peer(_automation(DIGEST, "Morning digest", expr="0 7 * * *", **RAN_THERE), saved_at=2.0)
    second = _sync(home, peer, ancestors=ancestors, queue=queue)
    assert second.conflicts == 1, "both homes changed the schedule: that IS a conflict"
    (rec,) = queue.items()
    return rec


# ── the sync's merge ─────────────────────────────────────────────────────────────────────────


def test_a_peers_automation_lands_in_a_home_that_has_its_own(tmp_path):
    """🔴 Red before: the peer's store was one row, and this home's won it whole."""
    _write_store(tmp_path, _automation(DIGEST, "Morning digest", **RAN_HERE))

    result = _sync(tmp_path, _peer(_automation(BACKUP, "Nightly backup", **RAN_THERE)))

    assert result.verdict == "consumed" and result.added == 1
    stored = _stored(tmp_path)
    assert list(stored) == [DIGEST, BACKUP], "this home's order first, then what arrived"
    assert stored[BACKUP]["enabled"] is False, "another home's switch is not this one's"
    assert not set(stored[BACKUP]) & set(RUNTIME_FIELDS) - {"enabled"}
    # CONTROL: this home's own automation is exactly as this home had it.
    assert {k: stored[DIGEST][k] for k in RAN_HERE} == RAN_HERE


def test_a_run_in_either_home_is_not_an_edit_to_review(tmp_path):
    """🔴 Red before: after one agreed sync, a fire in each home made all three shas differ and the
    whole file was queued for review."""
    queue = conflicts_mod.ConflictQueue(tmp_path)
    _write_store(tmp_path, _automation(DIGEST, "Morning digest", enabled=True))
    first = _sync(tmp_path, _peer(_automation(DIGEST, "Morning digest", enabled=True)), queue=queue)
    ancestors = dict(first.new_ancestors)
    assert ancestors, "the two homes agree — without an ancestor no conflict could be detected"

    _write_store(tmp_path, _automation(DIGEST, "Morning digest", **RAN_HERE), saved_at=5.0)
    switched_off_there = {**RAN_THERE, "enabled": False}
    peer = _peer(_automation(DIGEST, "Morning digest", **switched_off_there), saved_at=6.0)
    second = _sync(tmp_path, peer, ancestors=ancestors, queue=queue)

    assert second.conflicts == 0
    assert queue.items() == []


def test_an_edit_in_both_homes_is_still_a_conflict_and_shows_only_what_was_edited(tmp_path):
    """CONTROL for the one above: what a person makes of an automation is still compared, and the
    review holds that — not the run stamps, which are not what either machine is deciding."""
    rec = _conflict_on_the_schedule(tmp_path)

    assert rec.entity_id == DIGEST, "one automation is the conflict, not the whole file"
    assert rec.local_row["spec"]["expr"] == "0 9 * * *"
    assert rec.remote_row["spec"]["expr"] == "0 7 * * *"
    for version in (rec.local_row, rec.remote_row):
        assert not set(version) & set(RUNTIME_FIELDS), sorted(version)
    assert _stored(tmp_path)[DIGEST]["spec"]["expr"] == "0 9 * * *", "this home's is held"


def test_a_pull_that_brings_nothing_leaves_the_file_as_the_store_wrote_it(tmp_path):
    """🔴 Red before: every pull rewrote the file in the exporter's compact form."""
    _write_store(tmp_path, _automation(DIGEST, "Morning digest", **RAN_HERE))
    before = (tmp_path / "triggers.json").read_bytes()

    _sync(tmp_path, _peer(_automation(DIGEST, "Morning digest", **RAN_THERE)))

    assert (tmp_path / "triggers.json").read_bytes() == before


def test_a_store_this_home_cannot_read_is_left_as_it_is(tmp_path):
    """🔴 Red before: an unreadable file read as no store, and the merge wrote the peer's over it."""
    (tmp_path / "triggers.json").write_text("{half a store")

    result = _sync(tmp_path, _peer(_automation(BACKUP, "Nightly backup")))

    assert result.verdict == "payload-bad"
    assert (tmp_path / "triggers.json").read_text() == "{half a store"


def test_a_peers_hook_lands_switched_off_and_without_its_grant(tmp_path):
    """🔴 Red before: the same one-row merge, so a home with its own hooks never took the peer's —
    and a home with none took them switched on, granted, to run on the next prompt."""
    local = ScriptHook(id="h-local", name="Log prompts", provider="notify").to_dict()
    (tmp_path / "hooks.json").write_text(json.dumps({"hooks": [local]}))
    peer_hook = ScriptHook(
        id="h-peer",
        name="Scan every prompt",
        provider="bash",
        provider_config={"command": "/nonexistent/pc-scan"},
        capabilities={"providers": ["bash"]},
        last_run=1_900_000_000.0,
        last_status="ok",
        run_count=40,
    ).to_dict()
    entry = inv.by_id("hooks")
    assert entry is not None

    peer_rows = [{"id": "hooks.json", "data": {"hooks": [peer_hook]}}]
    reconcile.reconcile_entry(tmp_path, entry, peer_rows)

    hooks = {h.id: h for h in ScriptHookStore(tmp_path).list_all()}
    assert set(hooks) == {"h-local", "h-peer"}
    arrived = hooks["h-peer"]
    assert (arrived.enabled, arrived.capabilities, arrived.run_count) == (False, {}, 0)
    assert screen.ungranted_providers(arrived) == ["bash"], "switching it on here asks first"
    assert screen.ungranted_providers(ScriptHook.from_dict(peer_hook)) == [], "CONTROL: it had one"


# ── a conflict's resolution ──────────────────────────────────────────────────────────────────


def test_taking_the_other_machines_automation_brings_it_in_switched_off(tmp_path):
    """🔴 Red before: "take theirs" wrote the peer's row as it was — switched on, armed with the
    peer's next fire and carrying the peer's health."""
    rec = _conflict_on_the_schedule(tmp_path)

    out = resolver.resolve_conflict(tmp_path, rec.id, resolver.CHOICE_TAKE_REMOTE, now="NOW")

    assert out.ok, out.message
    taken = _stored(tmp_path)[DIGEST]
    assert taken["spec"]["expr"] == "0 7 * * *", "the other machine's version"
    assert taken["enabled"] is False
    assert not set(taken) & set(RUNTIME_FIELDS) - {"enabled"}, sorted(taken)
    entry = inv.by_id("triggers")
    assert entry is not None and out.note == entry.arrival and "switched off" in out.note


def test_a_drafted_merge_arrives_by_the_same_rule(tmp_path):
    """The drafted merge is built from the other machine's version, so it comes in the same way —
    whatever the draft carried of a run or a switch."""
    rec = _conflict_on_the_schedule(tmp_path)
    rec.proposal = {**rec.remote_row, "name": "Morning digest (merged)", **RAN_THERE}
    assert conflicts_mod.ConflictQueue(tmp_path).update(rec)

    out = resolver.resolve_conflict(tmp_path, rec.id, resolver.CHOICE_ACCEPT_PROPOSAL)

    assert out.ok, out.message
    taken = _stored(tmp_path)[DIGEST]
    assert taken["name"] == "Morning digest (merged)"
    assert taken["enabled"] is False
    assert not set(taken) & set(RUNTIME_FIELDS) - {"enabled"}


def test_keeping_this_machines_version_writes_nothing(tmp_path):
    """🔴 Red before: it wrote back the copy the record took when the conflict was found, so a fire
    here in between was undone — the run count, and the next fire time with it."""
    rec = _conflict_on_the_schedule(tmp_path)
    later = {**RAN_HERE, "run_count": 4, "next_fire_at": "2026-09-03T09:00:00+00:00"}
    _write_store(tmp_path, _automation(DIGEST, "Morning digest", expr="0 9 * * *", **later))
    before = (tmp_path / "triggers.json").read_bytes()

    out = resolver.resolve_conflict(tmp_path, rec.id, resolver.CHOICE_KEEP_LOCAL)

    assert out.ok and out.written == 0 and out.note == ""
    assert (tmp_path / "triggers.json").read_bytes() == before
    stored = conflicts_mod.ConflictQueue(tmp_path).get(rec.id)
    assert stored is not None and stored.status == conflicts_mod.STATUS_RESOLVED


def test_a_conflict_recorded_over_the_whole_file_is_closed_by_keeping_this_machines(tmp_path):
    """A home that synced before the store was compared automation by automation may hold a
    conflict over the whole file. Writing that version as one automation would break the store,
    so only keeping this machine's closes it — and says so."""
    _write_store(tmp_path, _automation(DIGEST, "Morning digest", **RAN_HERE))
    before = (tmp_path / "triggers.json").read_bytes()
    document = json.loads(before)
    whole = conflicts_mod.ConflictRecord(
        entry_id="triggers",
        entity_id="triggers.json",
        domain=inv.DOMAIN_AUTOMATION,
        surface=conflicts_mod.SURFACE_DURABILITY,
        ancestor_sha="a",
        local_sha="b",
        remote_sha="c",
        local_row={"id": "triggers.json", "data": document},
        remote_row={"id": "triggers.json", "data": {**document, "saved_at": 9.0}},
    )
    queue = conflicts_mod.ConflictQueue(tmp_path)
    assert queue.record(whole)

    refused = resolver.resolve_conflict(tmp_path, whole.id, resolver.CHOICE_TAKE_REMOTE)
    assert not refused.ok and refused.code == "not_a_record"
    assert "keep this machine's version" in refused.message
    assert (tmp_path / "triggers.json").read_bytes() == before

    assert resolver.resolve_conflict(tmp_path, whole.id, resolver.CHOICE_KEEP_LOCAL).ok
    assert (tmp_path / "triggers.json").read_bytes() == before


# ── a snapshot restore's merge and an archive import's merge ─────────────────────────────────


def test_a_merge_restore_into_a_home_with_no_store_brings_each_automation_in_by_the_rule(
    tmp_path, capsys
):
    """🔴 Red before: with no store here, the snapshot's was copied in whole — every automation
    switched on, armed, and granted by the owner of the home the snapshot was taken in."""
    from personalclaw.snapshot import _do_merge

    snap, home = tmp_path / "snap", tmp_path / "home"
    digest = _automation(DIGEST, "Morning digest", **RAN_THERE)
    _write_store(snap, digest, _granted(BACKUP, "Backup"))
    (snap / "config.json").write_text("{}")
    home.mkdir()

    _do_merge(snap, home, None)

    stored = _stored(home)
    assert set(stored) == {DIGEST, BACKUP}
    for row in stored.values():
        assert row["enabled"] is False
        assert not set(row) & set(RUNTIME_FIELDS) - {"enabled"}, sorted(row)
    backup, _issues = parse_trigger(stored[BACKUP])
    assert screen.ungranted_providers(backup) == ["bash"], "switching it on here asks first"


def test_a_merge_restore_brings_hooks_in_switched_off(tmp_path):
    """🔴 Red before: `hooks.json` was copied in whole into a home with none (switched on, granted),
    and merged into one that had its own as the other home wrote it."""
    from personalclaw.snapshot import _do_merge

    peer_hook = ScriptHook(
        id="h-peer", name="Scan", provider="bash", capabilities={"providers": ["bash"]}
    ).to_dict()
    for has_hooks in (False, True):
        snap, home = tmp_path / f"snap-{has_hooks}", tmp_path / f"home-{has_hooks}"
        snap.mkdir()
        home.mkdir()
        (snap / "config.json").write_text("{}")
        (snap / "hooks.json").write_text(json.dumps({"hooks": [peer_hook]}))
        if has_hooks:
            mine = ScriptHook(id="h-mine", name="Mine", provider="notify").to_dict()
            (home / "hooks.json").write_text(json.dumps({"hooks": [mine]}))

        _do_merge(snap, home, None)

        hooks = {h.id: h for h in ScriptHookStore(home).list_all()}
        assert hooks["h-peer"].enabled is False, has_hooks
        assert hooks["h-peer"].capabilities == {}, has_hooks
        assert ("h-mine" in hooks) is has_hooks


@pytest.fixture
def import_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def test_an_archive_import_into_a_home_with_no_store_brings_each_automation_in_by_the_rule(
    import_home, tmp_path
):
    """🔴 Red before: the import copied the archive's store in whole when this home had none."""
    from personalclaw.portability import apply_import_zip

    archive = tmp_path / "export.zip"
    root = "personalclaw-export-20260901T000000Z"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{root}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
        zf.writestr(
            f"{root}/triggers.json",
            json.dumps({"version": 1, "triggers": [_granted(BACKUP, "Backup", **RAN_THERE)]}),
        )
        hook = ScriptHook(id="h-peer", name="Scan", provider="bash").to_dict()
        zf.writestr(f"{root}/hooks.json", json.dumps({"hooks": [hook]}))

    summary = apply_import_zip(archive, mode="merge")

    assert "automations (merged)" in summary["items"]
    (row,) = TriggerStore(import_home).load()
    assert row.trigger.enabled is False and row.trigger.run_count == 0
    assert screen.ungranted_providers(row.trigger) == ["bash"]
    assert [h.enabled for h in ScriptHookStore(import_home).list_all()] == [False]


# ── the rule, classified ─────────────────────────────────────────────────────────────────────


#: What a hook IS — made by a person, and carried to another home.
HOOK_DEFINITION_FIELDS = frozenset(
    {"id", "name", "event", "matcher", "provider", "provider_config", "timeout"}
)


def test_every_hook_field_is_classified_as_one_homes_or_what_the_hook_is():
    """The rail, as `test_trigger_runtime_fields.py` keeps it for a trigger: a field added to
    `ScriptHook` fails here until someone decides whether a hook from another home brings it."""
    fields = {f.name for f in dataclasses.fields(ScriptHook)}
    home = set(HOOK_RUNTIME_FIELDS)
    assert sorted(fields - home - HOOK_DEFINITION_FIELDS) == []
    assert home & HOOK_DEFINITION_FIELDS == set()
    assert sorted((home | HOOK_DEFINITION_FIELDS) - fields) == []
