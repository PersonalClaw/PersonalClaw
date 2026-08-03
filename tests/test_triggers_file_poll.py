"""The file-watch poll runtime — what actually FIRES a `file` trigger (§3 / crit 2 — S93).

S83 shipped `file_watch.py` (globs, hashing, delta) and stopped there because there was no store to
enumerate `file` triggers from. S87 shipped the store; S92 made file triggers creatable in chat.
Measured before writing: `file_watch.changed_files` had **zero live callers**, and the tick clock
(`service.due_ids`) only surfaces triggers with a `next_fire_at` — a `file` trigger has none. So a
chat-created "when a file in ~/notes changes…" automation was present and inert: creatable, never
fired. These tests pin the runtime that closes that, and its disjointness from `ScheduleService`
(the property that makes booting it beside the cron loop safe).
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import file_poll as P
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path):
    return tmp_path


@pytest.fixture
def watched(tmp_path):
    d = tmp_path / "watched"
    d.mkdir()
    return d


def _store(home, *triggers):
    store = TriggerStore(base_dir=home)
    for t in triggers:
        store.upsert(t)
    return store


def _file_trigger(watched, tid="file:notes", **spec_over):
    spec = {"paths": [f"{watched}/**"]}
    spec.update(spec_over)
    return Trigger(
        id=tid,
        name="Notes",
        kind="file",
        enabled=True,
        spec=spec,
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
    )


# ── 🔴 the seeding contract: a fresh watch fires NOTHING ──


def test_the_first_poll_seeds_and_fires_nothing(home, watched):
    """🔴 A freshly enabled watch reporting every existing file as new would run the automation over
    the whole directory the first time. `WatchState.seeded` exists to prevent it; the runtime must
    honour it."""
    (watched / "existing.md").write_text("already here")
    store = _store(home, _file_trigger(watched))
    assert P.poll_all(store, base_dir=home) == []


def test_a_new_file_after_seeding_fires_once(home, watched):
    store = _store(home, _file_trigger(watched))
    P.poll_all(store, base_dir=home)  # seed
    (watched / "new.md").write_text("hello")
    fires = P.poll_all(store, base_dir=home)
    assert len(fires) == 1
    assert fires[0]["trigger_id"] == "file:notes"
    assert any("new.md" in c for c in fires[0]["changed"])


def test_no_change_fires_nothing(home, watched):
    store = _store(home, _file_trigger(watched))
    P.poll_all(store, base_dir=home)
    (watched / "a.md").write_text("x")
    P.poll_all(store, base_dir=home)  # consume the add
    assert P.poll_all(store, base_dir=home) == []  # nothing new


def test_a_content_change_fires_again(home, watched):
    store = _store(home, _file_trigger(watched))
    (watched / "a.md").write_text("v1")
    P.poll_all(store, base_dir=home)  # seed WITH the file present
    (watched / "a.md").write_text("v2")
    assert len(P.poll_all(store, base_dir=home)) == 1


# ── state persistence ──


def test_state_persists_to_a_sidecar_not_the_trigger(home, watched):
    """🔴 WatchState is high-churn runtime data. Writing it back onto the trigger would rewrite
    triggers.json every poll and race every unrelated edit — the reason leases are sidecars too."""
    store = _store(home, _file_trigger(watched))
    P.poll_all(store, base_dir=home)
    sidecar = P._state_path("file:notes", home)
    assert sidecar.exists()
    assert sidecar.parent.name == "trigger-watch"
    # The trigger's own spec is untouched by polling.
    assert store.get("file:notes").trigger.spec == {"paths": [f"{watched}/**"]}


def test_the_sidecar_name_is_filesystem_safe(home):
    """The trigger id carries a `:` — legal on posix, broken on Windows and ugly in a listing."""
    assert ":" not in P._state_path("file:my-notes", home).name
    assert P._state_path("file:my-notes", home).name == "file-my-notes.json"


def test_the_seed_survives_a_restart(home, watched):
    """🔴 If the seed were memory-only, every gateway restart would re-fire the whole directory."""
    (watched / "a.md").write_text("x")
    store = _store(home, _file_trigger(watched))
    P.poll_all(store, base_dir=home)  # seed and persist
    # A brand-new store object (as after a restart) reads the persisted seed.
    fresh = TriggerStore(base_dir=home)
    assert P.poll_all(fresh, base_dir=home) == []


def test_a_corrupt_sidecar_degrades_to_unseeded_not_a_crash(home, watched):
    """A truncated state file must re-seed (firing nothing), never crash the poll for other
    triggers."""
    (watched / "a.md").write_text("x")
    P.save_state("file:notes", P.WatchState(hashes={"stale": "x"}, seeded=True), base_dir=home)
    P._state_path("file:notes", home).write_text("{not json")
    store = _store(home, _file_trigger(watched))
    assert P.poll_all(store, base_dir=home) == []  # re-seeds, fires nothing


# ── which triggers are polled ──


def test_only_file_triggers_are_polled(home, watched):
    store = _store(
        home,
        _file_trigger(watched),
        Trigger(
            id="clock:x",
            name="C",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"provider": "run-prompt", "config": {}},
        ),
    )
    assert [t.id for t in P.file_triggers(store)] == ["file:notes"]


def test_a_paused_file_trigger_is_not_polled(home, watched):
    """🔴 Pausing a watch must stop the filesystem work, or "paused" is a lie the user pays for on
    every poll."""
    store = _store(home, _file_trigger(watched))
    store.set_enabled("file:notes", False)
    assert P.file_triggers(store) == []


def test_a_broken_row_is_not_polled(home, watched):
    import json

    store = TriggerStore(base_dir=home)
    store.path.write_text(
        json.dumps({"version": 1, "triggers": [{"id": "file:x", "name": "X", "kind": "file"}]})
    )
    # A row with no spec/paths must not blow up the poll.
    assert P.poll_all(store, base_dir=home) == []


def test_a_file_trigger_with_no_paths_is_skipped_not_a_cwd_scan(home):
    """A pathless file trigger should have been refused at creation; if one exists, it must not
    fall back to scanning the working directory."""
    store = _store(home, Trigger(id="file:bad", name="B", kind="file", enabled=True, spec={}))
    assert P.poll_one(store.get("file:bad").trigger, base_dir=home) is None


# ── isolation: one bad watch never strands the rest ──


def test_one_failing_watch_does_not_stop_the_others(home, watched, monkeypatch):
    """🔴 A poll loop that died on one bad watch would silently retire every other file automation
    the user has."""
    good = _file_trigger(watched, tid="file:good")
    bad = _file_trigger(watched, tid="file:bad")
    store = _store(home, good, bad)
    P.poll_all(store, base_dir=home)  # seed both
    (watched / "new.md").write_text("hi")

    real_poll_one = P.poll_one
    calls = []

    def flaky(trigger, *, base_dir=None):
        calls.append(trigger.id)
        if trigger.id == "file:bad":
            raise RuntimeError("boom")
        return real_poll_one(trigger, base_dir=base_dir)

    monkeypatch.setattr(P, "poll_one", flaky)
    fires = P.poll_all(store, base_dir=home)
    assert {t.id for t in P.file_triggers(store)} == {"file:good", "file:bad"}
    assert len(calls) == 2  # both attempted
    assert any(f["trigger_id"] == "file:good" for f in fires)  # the good one still fired


# ── the dedup hint rides the payload ──


def test_the_dedup_mode_is_carried_on_the_fire_payload(home, watched):
    """A content-dedup automation that summarizes only real edits needs to know how the change was
    decided."""
    store = _store(home, _file_trigger(watched, dedup="content"))
    P.poll_all(store, base_dir=home)
    (watched / "a.md").write_text("hi")
    fires = P.poll_all(store, base_dir=home)
    assert fires[0]["dedup"] == "content"


# ── 🔴 the disjointness that makes the boot cutover safe ──


def test_file_triggers_are_disjoint_from_the_clock_tick(home, watched):
    """🔴 THE property that lets this loop boot beside ScheduleService without double-firing: a
    `file` trigger never becomes due through the clock tick, because it has no `next_fire_at`."""
    from personalclaw.triggers import service as S

    store = _store(home, _file_trigger(watched))
    triggers = [r.trigger for r in store.load()]
    assert S.due_ids(triggers, now=1_800_000_000.0) == []
