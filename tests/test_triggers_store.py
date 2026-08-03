"""`triggers.json` — the one trigger store (§1 / §6 step 2 — S87).

§1: "One store: `~/.personalclaw/triggers.json` (fcntl + atomic write …). Parsed with **never-throw
structural validation** (AUTO-R15): typed issue records + closest-match resolution … an
agent-authored near-miss must never become a silently-dead trigger."

**Why this was buildable when S83/S86 recorded the store as blocked.** Those sessions were
right that
the store and the SERVICE are separate concerns, and wrong to treat them as one unit — the service
needs the store, not the reverse. Everything the store depends on was measured as shipped first:
`Trigger.to_dict()`/`parse_trigger()` round-trip losslessly, `parse_trigger` already never
raises and
already offers closest-match resolution, `migrate_crons()` already consumes a raw `crons.json`, and
`ScheduleService` already ships the fcntl+atomic+mtime triad §1 asks for.

The load-bearing tests are the three §1 properties: a broken row never disappears, a write never
truncates, and a concurrent writer is never silently overwritten. Plus the migration one, driven
against a store shaped like the owner's real file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from personalclaw.triggers.models import CLOCK_KINDS, SPEC_KEYS, Trigger
from personalclaw.triggers.store import (
    STORE_VERSION,
    LoadedTrigger,
    TriggerStore,
    health,
)


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _trigger(tid="t1", **over):
    base = dict(
        id=tid,
        name=f"T-{tid}",
        kind="clock",
        enabled=True,
        spec={"kind": "sequence", "expr": "3600", "at": "09:00"},
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
    )
    base.update(over)
    return Trigger(**base)


# ── an absent or damaged store degrades, never raises ──


def test_a_missing_store_loads_as_empty(store):
    assert store.exists() is False
    assert store.load() == []


def test_a_corrupt_store_loads_as_empty_and_is_left_on_disk(store):
    """The gateway must still boot with a damaged triggers file — a boot failure takes every other
    subsystem with it. And the file is NOT rewritten: silently repairing it destroys the
    evidence the
    user needs to see what happened."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json")
    assert store.load() == []
    assert store.path.read_text() == "{not json"


def test_a_non_dict_row_is_skipped_not_fatal(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"version": 1, "triggers": ["nope", 7, None, {"id": "ok"}]}))
    rows = store.load()
    assert len(rows) == 1


def test_a_bare_list_payload_is_accepted(store):
    """Tolerant read: a hand-edited file that dropped the envelope still loads."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps([_trigger().to_dict()]))
    assert len(store.load()) == 1


# ── §1 property 1: a broken row never disappears ──


def test_a_broken_row_is_KEPT_visible_and_inert(store):
    """🔴 The load-bearing decision. A store that dropped invalid rows would make an agent-authored
    typo indistinguishable from a trigger the user never created — R15's "silently-dead
    trigger", and
    worse, because the user cannot fix what they cannot see."""
    store.save_all([_trigger("good")])
    raw = json.loads(store.path.read_text())
    raw["triggers"].append({"id": "bad", "name": "Broken", "kind": "clok", "spec": {}})
    store.path.write_text(json.dumps(raw))

    rows = store.load()
    assert {r.trigger.id for r in rows} == {"good", "bad"}
    bad = next(r for r in rows if r.trigger.id == "bad")
    assert bad.ok is False
    assert bad.errors
    # Inert, not absent: `parse_trigger`'s own rule.
    assert bad.trigger.enabled is False


def test_a_broken_row_carries_the_closest_match_hint(store):
    """R15 requires closest-match resolution rendered as a chip. The store surfaces what
    `parse_trigger` already computes rather than re-deriving it."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "x", "name": "n", "kind": "clok"}]})
    )
    row = store.load()[0]
    assert "clock" in [i.closest for i in row.issues if i.closest]


def test_warnings_and_errors_are_separable(store):
    """A surface renders them differently: a warning is a chip, an error is why the row is off."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    {"id": "x", "name": "n", "kind": "clock", "spec": {"kind": "cron"}, "wat": 1}
                ],
            }
        )
    )
    row = store.load()[0]
    assert row.warnings
    assert all(i.severity != "error" for i in row.warnings)


def test_list_triggers_can_exclude_the_broken_ones(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    _trigger("good").to_dict(),
                    {"id": "bad", "name": "b", "kind": "nope"},
                ],
            }
        )
    )
    assert len(store.list_triggers(include_broken=True)) == 2
    assert [t.id for t in store.list_triggers(include_broken=False)] == ["good"]


def test_list_triggers_filters_by_kind(store):
    store.save_all([_trigger("a"), _trigger("b", kind="manual", spec={})])
    assert [t.id for t in store.list_triggers(kind="clock")] == ["a"]


# ── §1 property 2: a write never truncates ──


def test_a_save_writes_a_versioned_envelope(store):
    store.save_all([_trigger()])
    payload = json.loads(store.path.read_text())
    assert payload["version"] == STORE_VERSION
    assert len(payload["triggers"]) == 1
    assert payload["saved_at"] > 0


def test_no_tmp_file_survives_a_write(store):
    """tmp→rename: a leftover `.json.tmp` means the rename did not happen, and the next reader would
    see a half-written store."""
    store.save_all([_trigger()])
    assert not list(store.path.parent.glob("*.json.tmp"))


def test_a_round_trip_preserves_the_trigger(store):
    original = _trigger("t9", spec={"kind": "cron", "expr": "0 9 * * *"})
    store.save_all([original])
    revived = store.load()[0].trigger
    assert revived.to_dict() == original.to_dict()


def test_saving_an_empty_list_empties_the_store_without_deleting_it(store):
    store.save_all([_trigger()])
    assert store.save_all([]) == 0
    assert store.exists() is True
    assert store.load() == []


# ── §1 property 3: a concurrent writer is never silently overwritten ──


def test_upsert_re_reads_so_another_process_is_not_clobbered(tmp_path):
    """🔴 §6's carried-over gotcha: "MCP tools mutate the store from a separate process". A mutation
    built on a cached view would silently delete a trigger created in chat seconds ago."""
    mine = TriggerStore(base_dir=tmp_path)
    theirs = TriggerStore(base_dir=tmp_path)

    mine.save_all([_trigger("a")])
    mine.load()  # this instance now holds a stale view
    theirs.upsert(_trigger("from-chat"))  # the other process writes
    mine.upsert(_trigger("b"))  # and this one writes from its stale view

    ids = {t.id for t in mine.list_triggers()}
    assert ids == {"a", "from-chat", "b"}


def test_delete_re_reads_too(tmp_path):
    """Deleting from a stale view would resurrect every row another process added."""
    mine = TriggerStore(base_dir=tmp_path)
    theirs = TriggerStore(base_dir=tmp_path)
    mine.save_all([_trigger("a"), _trigger("b")])
    mine.load()
    theirs.upsert(_trigger("from-chat"))
    assert mine.delete("a") is True
    assert {t.id for t in mine.list_triggers()} == {"b", "from-chat"}


def test_upsert_replaces_rather_than_duplicating(store):
    store.upsert(_trigger("t1", name="first"))
    store.upsert(_trigger("t1", name="second"))
    rows = store.load()
    assert len(rows) == 1
    assert rows[0].trigger.name == "second"


def test_delete_reports_whether_it_removed_anything(store):
    store.save_all([_trigger("a")])
    assert store.delete("a") is True
    assert store.delete("a") is False


def test_the_mtime_contract_detects_another_writer(tmp_path):
    """§6: "mtime `_sync` within the ≤30s poll remains the propagation contract"."""
    mine = TriggerStore(base_dir=tmp_path)
    theirs = TriggerStore(base_dir=tmp_path)
    mine.save_all([_trigger("a")])
    mine.load()
    assert mine.changed_on_disk() is False
    time.sleep(0.01)
    theirs.upsert(_trigger("b"))
    assert mine.changed_on_disk() is True


def test_the_lock_file_is_separate_from_the_store(store):
    """Locking `triggers.json` itself would break: the atomic write replaces it by rename, which
    invalidates a lock held on the old inode."""
    store.save_all([_trigger()])
    assert (store.path.parent / ".triggers.lock").exists()
    assert store.path.name == "triggers.json"


# ── enable/disable ──


def test_set_enabled_toggles_a_healthy_row(store):
    store.save_all([_trigger("a")])
    assert store.set_enabled("a", False).enabled is False
    assert store.load()[0].trigger.enabled is False
    assert store.set_enabled("a", True).enabled is True


def test_set_enabled_REFUSES_to_enable_a_broken_row(store):
    """🔴 `parse_trigger` disabled it because the service cannot dispatch it. Flipping the flag would
    put an undispatchable trigger in the active set — pretending to work is worse than being visibly
    broken."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "bad", "name": "b", "kind": "clok"}]})
    )
    assert store.set_enabled("bad", True) is None
    assert store.load()[0].trigger.enabled is False


def test_set_enabled_can_still_DISABLE_a_broken_row(store):
    """Turning something off is always safe, and a user cleaning up should not be blocked."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "bad", "name": "b", "kind": "clok"}]})
    )
    assert store.set_enabled("bad", False) is not None


def test_set_enabled_on_a_missing_id_is_None(store):
    assert store.set_enabled("nope", True) is None


def test_get_returns_the_loaded_pair_or_None(store):
    store.save_all([_trigger("a")])
    found = store.get("a")
    assert isinstance(found, LoadedTrigger) and found.ok
    assert store.get("nope") is None


# ── the cron migration (§6 step 2) ──


def _crons(*jobs):
    return {"version": 1, "jobs": list(jobs)}


def _cron_job(jid, kind, **sched):
    return {
        "id": jid,
        "name": f"J-{jid}",
        "enabled": True,
        "schedule": {"kind": kind, **sched},
        "action": {"provider": "run-prompt", "config": {"message": "go"}},
    }


def test_the_migration_imports_and_KEEPS_the_old_file(store):
    """§6: "old file read-only one release". `personalclaw automation verify-migration` needs both
    sides to diff, and deleting the source makes that command impossible at the one moment anyone
    would run it."""
    (store.path.parent).mkdir(parents=True, exist_ok=True)
    source = store.path.parent / "crons.json"
    source.write_text(json.dumps(_crons(_cron_job("j1", "cron", cron_expr="0 9 * * *"))))
    report = store.migrate_from_crons()
    assert report["written"] == 1
    assert report["source_kept"] is True
    assert source.exists()


def test_an_INTERVAL_cron_survives_the_migration(store):
    """🔴 THE defect this session found. `migrate.convert_job` emits `{kind: "interval",
    interval_secs}` for a legacy `every` cron — deliberately, since `at` would turn a recurring job
    into a one-shot ("the single most destructive possible mistranslation", per its own docstring).
    But `CLOCK_KINDS` never gained the member, so every migrated interval cron parsed with
    `unknown clock kind 'interval'` and landed `enabled=False`: silently retired by the migration
    whose whole job was preserving it.

    Measured against the owner's real store: 4 jobs, 1 of them `every`.
    """
    store.path.parent.mkdir(parents=True, exist_ok=True)
    (store.path.parent / "crons.json").write_text(
        json.dumps(_crons(_cron_job("j-every", "every", every_secs=3600)))
    )
    report = store.migrate_from_crons()
    assert report["written"] == 1
    assert report["unparseable"] == []
    row = store.get("j-every")
    assert row is not None and row.ok
    assert row.trigger.spec["kind"] == "interval"
    assert row.trigger.spec["interval_secs"] == 3600


def test_the_interval_kind_and_its_key_are_both_declared():
    """Paired: without `interval_secs` in `SPEC_KEYS` a migrated row warns on the very number that
    defines when it fires."""
    assert "interval" in CLOCK_KINDS
    assert "interval_secs" in SPEC_KEYS["clock"]


def test_every_legacy_clock_kind_migrates_and_parses(store):
    """The whole legacy vocabulary, in one pass — the shape of the owner's real store."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    (store.path.parent / "crons.json").write_text(
        json.dumps(
            _crons(
                _cron_job("j-cron", "cron", cron_expr="0 9 * * *"),
                _cron_job("j-every", "every", every_secs=1800),
                _cron_job("j-at", "at", at_ts=1893456000),
            )
        )
    )
    report = store.migrate_from_crons()
    assert report["unparseable"] == []
    assert report["written"] == 3
    assert all(r.ok for r in store.load())


def test_the_migration_is_idempotent(store):
    """Running it twice must not duplicate rows — it upserts by id."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    (store.path.parent / "crons.json").write_text(
        json.dumps(_crons(_cron_job("j1", "cron", cron_expr="0 9 * * *")))
    )
    store.migrate_from_crons()
    store.migrate_from_crons()
    assert len(store.load()) == 1


def test_the_migration_preserves_triggers_authored_directly(store):
    """It upserts rather than replacing the store, so a trigger written straight to `triggers.json`
    survives a later migration pass."""
    store.save_all([_trigger("hand-written")])
    store.path.parent.mkdir(parents=True, exist_ok=True)
    (store.path.parent / "crons.json").write_text(
        json.dumps(_crons(_cron_job("j1", "cron", cron_expr="0 9 * * *")))
    )
    store.migrate_from_crons()
    assert {t.id for t in store.list_triggers()} == {"hand-written", "j1"}


def test_no_crons_file_is_a_clean_no_op(store):
    report = store.migrate_from_crons()
    assert report["written"] == 0
    assert report["lossless"] is True


def test_an_unreadable_crons_file_reports_rather_than_raising(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    (store.path.parent / "crons.json").write_text("{not json")
    report = store.migrate_from_crons()
    assert report["written"] == 0
    assert report["lossless"] is False


def test_a_converted_row_the_entity_refuses_is_RECORDED_not_dropped(store, monkeypatch):
    """🔴 How this session found the `interval` bug: `written` was 0 while `converted` said 1, and
    nothing said why. A count that silently disagrees with reality is the worst outcome in the one
    path whose job is not losing the user's automations."""
    from personalclaw.triggers import store as store_mod

    class _Converted:
        trigger = {"id": "j-bad", "name": "n", "kind": "clock", "spec": {"kind": "wat"}}

    class _Report:
        converted = [_Converted()]
        refused: list = []

        def to_dict(self):
            return {"converted": 1, "refused": 0, "lossless": True}

    monkeypatch.setattr(store_mod, "parse_trigger", store_mod.parse_trigger)
    monkeypatch.setattr("personalclaw.triggers.migrate.migrate_crons", lambda _store: _Report())
    store.path.parent.mkdir(parents=True, exist_ok=True)
    (store.path.parent / "crons.json").write_text(json.dumps(_crons()))
    report = store.migrate_from_crons()
    assert report["written"] == 0
    assert report["unparseable"] and report["unparseable"][0]["id"] == "j-bad"


# ── health ──


def test_health_NAMES_the_broken_ids(store):
    """ "3 triggers have problems" sends the user hunting; naming them is the difference between a
    report and a chore. Same rule `InboxView.unrenderable` follows."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": [
                    _trigger("good").to_dict(),
                    {"id": "bad1", "name": "b", "kind": "nope"},
                ],
            }
        )
    )
    report = health(store)
    assert report["total"] == 2
    assert report["broken"] == 1
    assert report["broken_ids"] == ["bad1"]
    assert report["by_kind"]


def test_health_on_a_missing_store_is_honest(store):
    report = health(store)
    assert report["exists"] is False
    assert report["total"] == 0
    assert Path(report["path"]).name == "triggers.json"
