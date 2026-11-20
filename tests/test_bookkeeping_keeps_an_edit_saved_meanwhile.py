"""The scheduler's and a lifecycle hook's own bookkeeping keeps an edit saved while it worked.

🔴 The defect (Backlog: "Scheduler and lifecycle-trigger bookkeeping write whole rows from an
earlier copy"). Two background writers stored a whole record from a copy they read earlier:

- The scheduler's tick loads every armable automation once, then walks the due ones. For each it
  wrote the whole row from that start-of-tick copy: the next fire time, then the grant's fire
  count, and the grant awaits in between (the fire-rate count, the gate walk with its calendar
  duty). An edit saved during those awaits, to the automation being admitted or to any later due
  one in the same tick (its schedule, its action, a switch-off), was written over without a word.
- A lifecycle hook's run sets its `last_run`, `last_status` and `run_count`, and the hook store then
  saved its whole list. A change another writer made to the running hook meanwhile (the chat's tool
  server, the CLI) was replaced by this store's copy: a hook switched off during its run came back
  on.

Now each writes what it changed onto the record as stored, under the file's lock: a field it
changed is written only where nobody changed that field since it read the row, its fire counts are
added to the stored count, and a row deleted meanwhile is not written back
(`TriggerStore.write_changes`, `record_files.Kept.moves`). Each case saves its edit through a
second store on the same file, at the await the tick or the hook's run is held at, as another
process would.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from personalclaw.triggers import service as svc
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0
#: Due a minute before NOW: due, and not so late that the slot is missed and reviewed.
DUE = "2027-01-15T07:59:00+00:00"
A, B = "clock:a-morning-brief", "clock:b-inbox-digest"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))


def _due(store: TriggerStore, tid: str, *, every: int = 3600) -> None:
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": every},
            next_fire_at=DUE,
            capabilities={"providers": ["run-prompt"]},
            workflow={"inline": {"provider": "run-prompt", "config": {}}},
        )
    )


def _while_admitting(monkeypatch: pytest.MonkeyPatch, who: str, edit: Any) -> None:
    """Hold the tick at the grant's first await for *who* (its fire-rate count), and save *edit*
    there through a second store on the same file, as the chat's tool server or the CLI would."""
    real = svc._fires_in_window

    async def held(trigger: Any, **kw: Any) -> int:
        if trigger.id == who:
            edit()
        return await real(trigger, **kw)

    monkeypatch.setattr(svc, "_fires_in_window", held)


def _tick(store: TriggerStore, tmp_path: Path) -> svc.TickResult:
    return asyncio.run(svc.tick(store, now=NOW, base_dir=tmp_path, persist=True))


def _stored(tmp_path: Path, tid: str) -> Trigger | None:
    row = TriggerStore(base_dir=tmp_path).get(tid)
    return row.trigger if row is not None else None


def _edit(tmp_path: Path, tid: str, **fields: Any) -> None:
    other = TriggerStore(base_dir=tmp_path)
    row = other.get(tid)
    assert row is not None
    for name, value in fields.items():
        setattr(row.trigger, name, value)
    other.upsert(row.trigger)


def test_an_edit_to_a_later_automation_saved_during_the_tick_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: B's reschedule wrote its start-of-tick copy, and the edit was gone."""
    store = TriggerStore(base_dir=tmp_path)
    _due(store, A)
    _due(store, B)
    _while_admitting(
        monkeypatch,
        A,
        lambda: _edit(
            tmp_path,
            B,
            name="Inbox digest, weekdays",
            spec={"kind": "interval", "interval_secs": 7200},
            enabled=False,
        ),
    )

    _tick(store, tmp_path)

    b = _stored(tmp_path, B)
    assert b is not None
    assert (b.name, b.spec["interval_secs"], b.enabled) == ("Inbox digest, weekdays", 7200, False)
    assert b.next_fire_at and b.next_fire_at != DUE, "the tick's own reschedule did not land"


def test_an_edit_to_the_automation_being_admitted_survives_its_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: the grant's fire count wrote A's copy from before its own gate walk, so A
    switched off during the walk came back on."""
    store = TriggerStore(base_dir=tmp_path)
    _due(store, A)
    _while_admitting(
        monkeypatch, A, lambda: _edit(tmp_path, A, name="Brief, renamed", enabled=False)
    )

    _tick(store, tmp_path)

    a = _stored(tmp_path, A)
    assert a is not None
    assert (a.name, a.enabled) == ("Brief, renamed", False), "the grant wrote over the edit"
    assert a.run_count == 1 and a.last_fired_at, "the grant's own count did not land"


def test_a_fire_counted_by_another_writer_meanwhile_is_still_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers' fire counts both count: the grant adds its one to the count as stored."""
    from personalclaw.triggers.run_record import note_fire

    store = TriggerStore(base_dir=tmp_path)
    _due(store, A)
    _while_admitting(
        monkeypatch, A, lambda: note_fire(TriggerStore(base_dir=tmp_path), A, at=NOW - 5)
    )

    _tick(store, tmp_path)

    a = _stored(tmp_path, A)
    assert a is not None and a.run_count == 2


def test_a_reschedule_an_edit_saved_meanwhile_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An edit that set B's next time itself wins over the tick's reschedule from the old copy."""
    store = TriggerStore(base_dir=tmp_path)
    _due(store, A)
    _due(store, B)
    later = "2027-02-01T09:00:00+00:00"
    _while_admitting(monkeypatch, A, lambda: _edit(tmp_path, B, next_fire_at=later))

    _tick(store, tmp_path)

    b = _stored(tmp_path, B)
    assert b is not None and b.next_fire_at == later


def test_an_automation_deleted_during_the_tick_is_not_written_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TriggerStore(base_dir=tmp_path)
    _due(store, A)
    _due(store, B)
    _while_admitting(monkeypatch, A, lambda: TriggerStore(base_dir=tmp_path).delete(B))

    _tick(store, tmp_path)

    assert _stored(tmp_path, B) is None, "the tick made a deleted automation again"


def test_a_row_written_in_place_keeps_its_place(tmp_path: Path) -> None:
    """A bookkeeping write changes a row where it is: the file's order is the owner's."""
    store = TriggerStore(base_dir=tmp_path)
    _due(store, A)
    _due(store, B)
    first = store.get(A)
    assert first is not None
    base = first.trigger.to_dict()
    first.trigger.run_count = 4
    store.write_changes(first.trigger, base=base)
    assert [t.id for t in TriggerStore(base_dir=tmp_path).list_triggers()] == [A, B]


# ── a lifecycle hook ─────────────────────────────────────────────────────────────────────────────


def test_a_hook_switched_off_while_it_runs_stays_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: the hook store saved its whole list after the run, and the hook it ran came back
    on with its own copy."""
    import personalclaw.action_providers as providers
    from personalclaw.action_providers.base import ActionResult
    from personalclaw.hooks import HOOK_EVENT_STOP, ScriptHook, ScriptHookStore

    started, release = asyncio.Event(), asyncio.Event()

    class _Waits:
        async def execute(self, config: Any, ctx: Any, timeout: int = 30) -> ActionResult:
            started.set()
            await release.wait()
            return ActionResult(success=True, stdout="linted", exit_code=0)

    monkeypatch.setattr(providers, "get_action_provider", lambda name: _Waits())
    store = ScriptHookStore(config_dir=tmp_path)
    store.create(
        ScriptHook(
            id="lint",
            name="Lint after a turn",
            event=HOOK_EVENT_STOP,
            provider="bash",
            provider_config={"command": "make lint"},
            enabled=True,
            capabilities={"providers": ["bash"]},
        ).to_dict()
    )

    async def _run() -> None:
        fire = asyncio.create_task(store.fire(HOOK_EVENT_STOP))
        await asyncio.wait_for(started.wait(), 10)
        ScriptHookStore(config_dir=tmp_path).update("lint", {"enabled": False})
        release.set()
        await fire

    asyncio.run(_run())

    stored = ScriptHookStore(config_dir=tmp_path).get("lint")
    assert stored is not None
    assert stored.enabled is False, "the hook switched off during its run came back on"
    assert stored.run_count == 1 and stored.last_run > 0, "the run's own bookkeeping did not land"
