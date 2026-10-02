"""A trigger's run record says when the run started and finished, and how it went, and the
trigger's own counters move with it.

What was wrong, driven on the shipped fire path:

* A scheduled fire's row was written at record time as `started_at = finished_at = now`, so every
  fire read `duration_ms: 0` — a 6.8-minute digest included — and its start read as late as its
  finish. A Run now of the same trigger recorded its real duration, because the Run button had a
  recorder of its own.
* A fire no admission granted (a watched file, a watched page, a chain, a quiet session) left the
  trigger's `run_count` at 0 and `last_fired_at` empty while `last_success_at` moved, so its panel
  read "never run" beside the runs its history listed. And no fire ever set `last_run_id`, the row
  "Open as chat" opens: only a Run now did.
* An action that delivered its no-model floor (a digest with no synthesis) was recorded as a plain
  `success`.

Every run now goes through one recorder (`triggers.run_record.record_run`) and every fire source
counts its fire through one function (`run_record.count_fire`).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

import personalclaw.action_providers as AP
from personalclaw.action_providers.base import ActionResult
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import loop as L
from personalclaw.triggers import service as SVC
from personalclaw.triggers import wakeup as WK
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0

#: How long the slow action takes. Long enough that a zero-duration row cannot pass, short enough
#: to keep the file quick.
SLOW_SECS = 0.3


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


class _Slow:
    """An action that takes a while, as a digest waiting on a model does."""

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.calls = 0

    async def execute(self, config, ctx, timeout=30):
        self.calls += 1
        await asyncio.sleep(SLOW_SECS)
        if self.raises:
            raise RuntimeError("the action broke")
        return ActionResult(success=True, stdout="did the thing")


@pytest.fixture
def slow(monkeypatch):
    provider = _Slow()
    monkeypatch.setattr(AP, "get_action_provider", lambda name: provider)
    return provider


def _trigger(tid: str, kind: str, **over: Any) -> Trigger:
    fields: dict[str, Any] = {
        "id": tid,
        "name": tid,
        "kind": kind,
        "enabled": True,
        "spec": {},
        "capabilities": {"providers": ["notify"]},
        "workflow": {"inline": {"provider": "notify", "config": {}}},
    }
    fields.update(over)
    return Trigger(**fields)


def _orch(**attrs: Any) -> GatewayOrchestrator:
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = None
    for name, value in attrs.items():
        setattr(orch, name, value)
    return orch


def _rows(home: Path, tid: str) -> list[dict[str, Any]]:
    rows, _total = asyncio.run(ScheduleRunStore(home).list_for_job(tid, 0, 20))
    return rows


def _live(home: Path, tid: str) -> Trigger:
    row = TriggerStore(base_dir=home).get(tid)
    assert row is not None
    return row.trigger


def _assert_timed(row: dict[str, Any]) -> None:
    """The row says the run took as long as its action did, and agrees with itself."""
    took = row["finished_at"] - row["started_at"]
    assert took >= SLOW_SECS, f"started {row['started_at']} finished {row['finished_at']}"
    assert row["duration_ms"] >= SLOW_SECS * 1000
    assert abs(row["duration_ms"] - took * 1000) <= 1


def _clock_runner(monkeypatch, orch: GatewayOrchestrator):
    """The gateway's own clock runner, captured from `_clock_loop` rather than copied."""
    captured: dict[str, Any] = {}

    async def _capture(store, *, runner, **_kw):
        captured["runner"] = runner

    monkeypatch.setattr(L, "run_forever", _capture)
    asyncio.run(orch._clock_loop())
    return captured["runner"]


def _manager(*keys: str):
    from personalclaw.session import SessionManager, _Session

    class _Provider:
        async def shutdown(self):
            return None

    manager = SessionManager.__new__(SessionManager)
    manager._sessions = {key: _Session(provider=_Provider()) for key in keys}
    return manager


# ── a scheduled fire ─────────────────────────────────────────────────────────────


def test_a_scheduled_fire_of_a_slow_action_records_when_it_started_and_finished(
    home, slow, monkeypatch
):
    store = TriggerStore(base_dir=home)
    store.upsert(
        _trigger(
            "clock:slow",
            "clock",
            spec={"kind": "interval", "interval_secs": 3600},
            next_fire_at=SVC.to_iso(NOW - 1),
        )
    )
    manager = _manager()
    orch = _orch(sessions=manager)
    runner = _clock_runner(monkeypatch, orch)

    asyncio.run(L.tick_once(store, runner=runner, sessions=manager, base_dir=home, now=NOW))

    assert slow.calls == 1, "the tick never reached the action"
    (row,) = _rows(home, "clock:slow")
    assert row["status"] == "success"
    _assert_timed(row)
    trigger = _live(home, "clock:slow")
    # The grant counted the fire, and the record moved its stamps beside it.
    assert trigger.run_count == 1
    assert trigger.last_fired_at == SVC.to_iso(NOW)
    assert SVC.to_epoch(trigger.last_success_at) >= row["finished_at"] - 1
    assert trigger.last_run_id == row["run_id"]


def test_a_fire_whose_action_raises_records_how_long_it_ran_before_it_failed(
    home, slow, monkeypatch
):
    slow.raises = True
    TriggerStore(base_dir=home).upsert(_trigger("clock:raises", "clock"))

    asyncio.run(_orch()._fire_store_trigger(_live(home, "clock:raises"), {}))

    (row,) = _rows(home, "clock:raises")
    assert row["status"] == "failure"
    assert "the action broke" in row["error"]
    _assert_timed(row)
    trigger = _live(home, "clock:raises")
    assert trigger.last_failure_at and not trigger.last_success_at
    assert trigger.last_run_id == row["run_id"]


# ── fires no admission granted ───────────────────────────────────────────────────


def test_a_watched_files_fire_moves_its_triggers_counters_with_its_record(home, slow):
    from personalclaw.triggers import file_poll

    watched = home / "watched"
    watched.mkdir()
    store = TriggerStore(base_dir=home)
    store.upsert(_trigger("file:notes", "file", spec={"paths": [f"{watched}/**"]}))
    assert file_poll.poll_all(store, base_dir=home) == []  # the seed fires nothing
    (watched / "new.md").write_text("hello")
    before = time.time()
    (payload,) = file_poll.poll_all(store, base_dir=home)

    asyncio.run(_orch()._fire_file_trigger(payload))

    (row,) = _rows(home, "file:notes")
    _assert_timed(row)
    trigger = _live(home, "file:notes")
    assert trigger.run_count == 1, "a file fire left its trigger reading as never fired"
    assert before - 1 <= SVC.to_epoch(trigger.last_fired_at) <= row["started_at"] + 1
    assert trigger.last_success_at
    assert trigger.last_run_id == row["run_id"]


def test_a_watched_page_counts_its_fire_when_it_finds_new_items(home):
    import types

    from personalclaw.triggers import web_poll

    store = TriggerStore(base_dir=home)
    store.upsert(
        _trigger(
            "web_watch:feed",
            "web_watch",
            spec={"url": "https://example.com/feed", "poll_interval": 300},
        )
    )
    feed = {"body": "<rss><item><guid>post-1</guid></item></rss>"}

    def fetch(url):
        body = feed["body"].encode()
        return types.SimpleNamespace(status=200, body=body, url=url, headers={}, truncated=False)

    class _Knowledge:
        def create_typed_item(self, **kwargs):
            return "item-1"

    seeded, _ = web_poll.poll_all(
        store, now=NOW, base_dir=home, fetcher=fetch, knowledge_store=_Knowledge()
    )
    assert seeded == []
    assert _live(home, "web_watch:feed").run_count == 0, "the seeding pass is not a fire"
    feed["body"] = feed["body"].replace("</rss>", "<item><guid>post-2</guid></item></rss>")
    payloads, _ = web_poll.poll_all(
        store, now=NOW + 3600, base_dir=home, fetcher=fetch, knowledge_store=_Knowledge()
    )

    assert len(payloads) == 1
    trigger = _live(home, "web_watch:feed")
    assert trigger.run_count == 1
    assert trigger.last_fired_at == SVC.to_iso(NOW + 3600)


def test_a_chained_fire_moves_its_triggers_counters_with_its_record(home, slow):
    store = TriggerStore(base_dir=home)
    store.upsert(_trigger("clock:source", "clock"))
    store.upsert(
        _trigger("run_completed:after", "run_completed", spec={"source_trigger": "clock:source"})
    )

    asyncio.run(_orch()._fire_chained_triggers(source_trigger="clock:source", payload={}))

    (row,) = _rows(home, "run_completed:after")
    _assert_timed(row)
    trigger = _live(home, "run_completed:after")
    assert trigger.run_count == 1
    assert trigger.last_fired_at
    assert trigger.last_run_id == row["run_id"]


def test_a_chain_counts_the_trigger_it_fires_and_not_the_one_it_refuses(home, slow):
    """One completed run, two triggers waiting on it. The one already on the chain's path would
    loop, so it is refused: its history says why and it is not counted, since it did not fire. The
    other fires, and its count and its run's record move together."""
    from personalclaw.triggers import chain

    store = TriggerStore(base_dir=home)
    store.upsert(_trigger("clock:source", "clock"))
    for tid in ("run_completed:after", "run_completed:again"):
        store.upsert(_trigger(tid, "run_completed", spec={"source_trigger": "clock:source"}))
    payload = {chain.DEPTH_KEY: 1, chain.PATH_KEY: ["run_completed:again", "clock:source"]}

    asyncio.run(_orch()._fire_chained_triggers(source_trigger="clock:source", payload=payload))

    (refusal,) = _rows(home, "run_completed:again")
    assert refusal["status"] == "skipped_gate"
    assert "chain cycle" in refusal["error"]
    refused = _live(home, "run_completed:again")
    assert refused.run_count == 0 and refused.last_fired_at == ""
    assert slow.calls == 1, "only the chain that was not refused ran"
    (row,) = _rows(home, "run_completed:after")
    _assert_timed(row)
    fired = _live(home, "run_completed:after")
    assert fired.run_count == 1 and fired.last_fired_at
    assert fired.last_run_id == row["run_id"]


def test_a_quiet_sessions_fire_moves_its_triggers_counters_with_its_record(home, slow, monkeypatch):
    monkeypatch.setattr("personalclaw.triggers.nudge._INSTANCE", None)
    store = TriggerStore(base_dir=home)
    store.upsert(_trigger("idle:standup", "idle", spec={"idle_secs": 60, "first_idle_secs": 0}))
    manager = _manager(WK.session_key_for("idle:standup"))
    orch = _orch(sessions=manager)
    runner = _clock_runner(monkeypatch, orch)

    # The first tick arms the quiet period; the second, a period later, fires.
    asyncio.run(L.tick_once(store, runner=runner, sessions=manager, base_dir=home, now=NOW))
    assert _rows(home, "idle:standup") == []
    asyncio.run(L.tick_once(store, runner=runner, sessions=manager, base_dir=home, now=NOW + 61))

    (row,) = _rows(home, "idle:standup")
    _assert_timed(row)
    trigger = _live(home, "idle:standup")
    assert trigger.run_count == 1
    assert trigger.last_fired_at == SVC.to_iso(NOW + 61)
    assert trigger.last_run_id == row["run_id"]


# ── a run by hand ────────────────────────────────────────────────────────────────


def test_a_run_now_records_its_duration_and_spends_no_fire(home, slow, monkeypatch):
    """The Run button goes through the same recorder, tagged as the hand run it is: its row and
    stamps move, and the fire meters `max_fires` spends do not."""
    from personalclaw.dashboard.handlers import trigger_runs

    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: home)
    TriggerStore(base_dir=home).upsert(_trigger("clock:by-hand", "clock"))

    ran, note = asyncio.run(
        trigger_runs._dispatch_store_action(_live(home, "clock:by-hand"), {}, event="manual.run")
    )

    assert ran is True, note
    (row,) = _rows(home, "clock:by-hand")
    assert row["trigger"] == "manual"
    _assert_timed(row)
    trigger = _live(home, "clock:by-hand")
    assert trigger.last_run_id == row["run_id"]
    assert trigger.last_success_at
    assert trigger.run_count == 0 and trigger.last_fired_at == ""


# ── a degraded run ───────────────────────────────────────────────────────────────


def test_a_digest_with_no_synthesis_records_that_it_was_degraded(home, monkeypatch):
    """The morning digest's background model answered nothing: the digest still arrives with its
    items (its no-model floor), and its run is recorded as degraded, saying so, rather than as a
    plain success."""
    from personalclaw import llm_helpers
    from personalclaw.action_providers import services as svc
    from personalclaw.action_providers.source_digest_provider import (
        SOURCE_DIGEST_JOB_NAME,
        reconcile_source_digest_cron,
    )
    from personalclaw.knowledge import source_digest as sd
    from personalclaw.knowledge.source_streams import SOURCE_ITEM_INGESTED, SourceEventSpool
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
    from personalclaw.triggers.history import schedule_run_to_record
    from personalclaw.triggers.models import Outcome

    knowledge = KnowledgeStore(db_path=str(knowledge_db_path()))
    source_id = knowledge.create_source(name="Release notes", provider="fixture", kind="feed")
    item_id = knowledge.create_typed_item(
        item_type="bookmark", title="Release 2.0", content="stable", source_id=source_id
    )
    SourceEventSpool().emit(SOURCE_ITEM_INGESTED, {"item_id": item_id, "source_id": source_id})

    class _State:
        notes: list[Any] = []

        def notify(self, *args, **kwargs):
            self.notes.append((args, kwargs))

        def push_refresh(self, *kinds):
            return None

    state = _State()
    monkeypatch.setattr(svc, "_services", svc.ActionServices(state=state))
    asked: list[str] = []

    async def _nothing(prompt, use_case="", **kwargs):
        asked.append(use_case)
        await asyncio.sleep(SLOW_SECS)
        return ""

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _nothing)
    store = TriggerStore(base_dir=home)
    reconcile_source_digest_cron(store)

    asyncio.run(_orch()._fire_store_trigger(_live(home, SOURCE_DIGEST_JOB_NAME), {}))

    assert asked == ["background"], "the digest never asked its background model"
    digests = knowledge.db.execute(
        "SELECT content FROM items WHERE provider = ?", (sd.DIGEST_PROVIDER,)
    ).fetchall()
    assert [d[0] for d in digests] == [sd.UNSYNTHESISED_BODY], "the floor still delivers"
    (row,) = _rows(home, SOURCE_DIGEST_JOB_NAME)
    assert row["status"] == "degraded"
    assert "synthesis was unavailable" in row["summary"]
    _assert_timed(row)
    assert schedule_run_to_record(row).outcome == Outcome.DEGRADED.value
    trigger = _live(home, SOURCE_DIGEST_JOB_NAME)
    assert trigger.last_success_at and not trigger.last_failure_at
    assert "synthesis was unavailable" in trigger.last_error_summary
    assert trigger.health_status == "ok", "a degraded run is not a failure of the automation"
