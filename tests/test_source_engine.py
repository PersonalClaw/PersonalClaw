"""WATCHED-SOURCES WS-2 — WatchedSource store + SourceEngine poll loop.

Covers the atom's done_when: the knowledge.db migration adds the source tables + the
item source_id/guid columns with UNIQUE(source_id, guid); the engine polls a fixture
source on schedule and writes+enqueues new items; a kill-mid-poll + restart yields no
duplicate and no lost item (cursor + seen-set atomicity + recover_pending); the SOURCE
egress profile exists; and SourcesConfig round-trips.

Isolation: every store binds a tmp_path db AND we set PERSONALCLAW_HOME so nothing can
reach the real home (stores bind config_dir at import — the env var is the robust lever).
"""

import json
import os

import pytest

from personalclaw.knowledge.source_engine import POLL_CEILING_SECS, SourceEngine
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.base import (
    KnowledgeItem,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


# ── a poll-capable fixture provider (§1.1 shape) ───────────────────────────────


class FixtureSourceProvider(KnowledgeSourceProvider):
    """A minimal poll-capable provider the engine can drive without a network.

    ``script`` is a list of poll outcomes (items, next-cursor); each poll pops the next.
    ``polls`` records the cursor it was handed, so a test can assert the engine persisted
    and replayed the cursor. A crash is simulated by the STORE side (partial write), not
    here — this provider just re-emits its script, which is exactly an at-least-once feed.
    """

    def __init__(self, script):
        self._script = list(script)
        self.polls: list[str] = []

    @property
    def name(self) -> str:
        return "watched-fixture"

    @property
    def display_name(self) -> str:
        return "Watched Fixture"

    async def list_sources(self) -> list[KnowledgeSource]:
        return []

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        return []

    async def get_item(self, item_id: str):
        return None

    async def poll(self, source_id: str, cursor: str = "") -> SourcePollResult:
        self.polls.append(cursor)
        if not self._script:
            return SourcePollResult(items=[], cursor=cursor)
        items, next_cursor = self._script.pop(0)
        return SourcePollResult(items=list(items), cursor=next_cursor)


class _FakeQueue:
    """Stands in for KnowledgeIngestQueue: records enqueues + provides recover_pending
    that re-enqueues items the store left in a pending processing_status (the real
    queue's contract)."""

    def __init__(self, store):
        self._store = store
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)

    def recover_pending(self) -> int:
        rows = self._store.db.execute(
            "SELECT id FROM items WHERE processing_status IN ('queued', 'processing')"
        ).fetchall()
        for r in rows:
            self.enqueue(r["id"])
        return len(rows)


def _cfg(**over):
    """A plain SourcesConfig-shaped object; overrides let a test force a floor/cap. Uses a
    tiny floor so scheduling is testable without wall-clock waits."""
    from personalclaw.config.loader import SourcesConfig

    base = dict(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
        daily_request_budget=288,
    )
    base.update(over)
    return SourcesConfig(**base)


def _engine(store, provider, **cfg_over):
    queue = _FakeQueue(store)
    eng = SourceEngine(
        store,
        queue,
        providers_lister=lambda: [provider],
        config_loader=lambda: _cfg(**cfg_over),
        now_fn=lambda: 1_000_000.0,
    )
    return eng, queue


# ── migration ──────────────────────────────────────────────────────────────────


def test_migration_adds_source_tables_and_columns(store):
    tables = {
        r[0]
        for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"sources", "source_cursors", "source_seen"} <= tables
    cols = {r[1] for r in store.db.execute("PRAGMA table_info(items)").fetchall()}
    assert "source_id" in cols and "guid" in cols


def test_unique_source_guid_index_exists_and_binds(store, tmp_path):
    # The partial UNIQUE index rejects a second item for one (source_id, guid) — the
    # never-pruned persist gate on top of the seen-set.
    idx = {
        r[0]
        for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_items_source_guid" in idx


def test_reopen_is_idempotent_and_keeps_source_columns(tmp_path):
    path = str(tmp_path / "k.db")
    s1 = KnowledgeStore(path)
    sid = s1.create_source(name="s", provider="watched-fixture", kind="feed")
    s1.db.close()
    s2 = KnowledgeStore(path)  # reopen → _migrate + _migrate_sources run again
    cols = {r[1] for r in s2.db.execute("PRAGMA table_info(items)").fetchall()}
    # Source columns survive the reopen (the legacy chunk-model DROP keys on chunk_index
    # ALONE now, so it can't clobber the reclaimed source_id/guid).
    assert "source_id" in cols and "guid" in cols
    assert s2.get_source(sid) is not None


# ── create_typed_item source path (novelty gate) ────────────────────────────────


def test_create_typed_item_source_dedups_by_guid(store):
    sid = store.create_source(name="s", provider="watched-fixture", kind="feed")
    a = store.create_typed_item(
        item_type="bookmark", title="t", source_id=sid, guid="g1", provider="watched-fixture"
    )
    b = store.create_typed_item(
        item_type="bookmark", title="t again", source_id=sid, guid="g1", provider="watched-fixture"
    )
    assert a is not None and b is None  # second sighting deduped
    seen = store.db.execute(
        "SELECT COUNT(*) FROM source_seen WHERE source_id = ?", (sid,)
    ).fetchone()[0]
    assert seen == 1
    items = store.db.execute("SELECT COUNT(*) FROM items WHERE source_id = ?", (sid,)).fetchone()[0]
    assert items == 1


def test_native_create_still_returns_id_and_leaves_source_null(store):
    iid = store.create_typed_item(item_type="note", title="native", content="x")
    assert iid is not None
    row = store.db.execute("SELECT source_id, guid FROM items WHERE id = ?", (iid,)).fetchone()
    assert row["source_id"] is None and row["guid"] is None


# ── engine polls, writes, enqueues, advances cursor ─────────────────────────────


@pytest.mark.asyncio
async def test_engine_polls_fixture_writes_and_enqueues(store):
    sid = store.create_source(name="s", provider="watched-fixture", kind="feed")
    provider = FixtureSourceProvider(
        [([SourceItem(guid="g1", title="One"), SourceItem(guid="g2", title="Two")], "cursor-1")]
    )
    eng, queue = _engine(store, provider)
    n = await eng.poll_source(store.get_source(sid), _cfg())
    assert n == 2
    assert len(queue.enqueued) == 2
    # The cursor was persisted.
    assert store.get_source_cursor(sid) == "cursor-1"
    # Rollups updated.
    src = store.get_source(sid)
    assert src["last_new_count"] == 2 and src["health_status"] == "ok"


@pytest.mark.asyncio
async def test_engine_replays_persisted_cursor_next_poll(store):
    sid = store.create_source(name="s", provider="watched-fixture", kind="feed")
    provider = FixtureSourceProvider(
        [
            ([SourceItem(guid="g1", title="One")], "cursor-1"),
            ([SourceItem(guid="g2", title="Two")], "cursor-2"),
        ]
    )
    eng, _ = _engine(store, provider)
    await eng.poll_source(store.get_source(sid), _cfg())
    await eng.poll_source(store.get_source(sid), _cfg())
    # Second poll was handed the cursor the first poll persisted.
    assert provider.polls == ["", "cursor-1"]
    assert store.get_source_cursor(sid) == "cursor-2"


@pytest.mark.asyncio
async def test_provider_error_keeps_cursor_and_degrades_health(store):
    sid = store.create_source(name="s", provider="watched-fixture", kind="feed")

    class _ErrProvider(FixtureSourceProvider):
        async def poll(self, source_id, cursor=""):
            return SourcePollResult(items=[], cursor="", error="rate limited")

    eng, queue = _engine(store, _ErrProvider([]))
    n = await eng.poll_source(store.get_source(sid), _cfg())
    assert n == 0 and queue.enqueued == []
    assert store.get_source(sid)["health_status"] == "degraded"


@pytest.mark.asyncio
async def test_tick_only_polls_due_sources_and_returns_capped_sleep(store):
    store.create_source(name="s", provider="watched-fixture", kind="feed", poll_interval_secs=1)
    provider = FixtureSourceProvider([([SourceItem(guid="g1", title="One")], "c1")])
    eng, queue = _engine(store, provider, poll_interval_default_secs=1, network_floor_secs=0)
    # First tick: never-polled → due now → polls.
    sleep_for = await eng.tick()
    assert len(queue.enqueued) == 1
    assert 0.0 <= sleep_for <= POLL_CEILING_SECS


@pytest.mark.asyncio
async def test_tick_skips_when_disabled(store):
    store.create_source(name="s", provider="watched-fixture", kind="feed")
    provider = FixtureSourceProvider([([SourceItem(guid="g1", title="One")], "c1")])
    eng, queue = _engine(store, provider, enabled=False)
    sleep_for = await eng.tick()
    assert queue.enqueued == [] and sleep_for == POLL_CEILING_SECS


# ── crash-safety: kill mid-poll + restart → no dup, no loss (SC#4) ──────────────


@pytest.mark.asyncio
async def test_kill_mid_poll_then_restart_no_dup_no_loss(store):
    """Simulate a crash BETWEEN item-persist and cursor-persist: the items are durable
    (each committed with its seen-row) but the cursor never advanced. On restart the
    provider re-yields the same items (at-least-once poll); the seen-set drops every one
    (exactly-once persist) — so the item count is unchanged and nothing is lost."""
    sid = store.create_source(name="s", provider="watched-fixture", kind="feed")

    # Phase 1: write both items directly (as poll_source would) but DO NOT record the
    # cursor — this is the exact mid-poll crash window.
    for guid, title in (("g1", "One"), ("g2", "Two")):
        iid = store.create_typed_item(
            item_type="bookmark",
            title=title,
            source_id=sid,
            guid=guid,
            provider="watched-fixture",
            extra={"processing_status": "queued"},
        )
        assert iid is not None
    assert store.get_source_cursor(sid) == ""  # cursor never advanced (the crash)
    items_before = store.db.execute(
        "SELECT COUNT(*) FROM items WHERE source_id = ?", (sid,)
    ).fetchone()[0]
    assert items_before == 2

    # Phase 2: restart. recover_pending re-enqueues the written-but-unprocessed items, and
    # the next poll re-yields the same guids — the seen-set dedups them entirely.
    provider = FixtureSourceProvider(
        [([SourceItem(guid="g1", title="One"), SourceItem(guid="g2", title="Two")], "cursor-1")]
    )
    eng, queue = _engine(store, provider)
    recovered = eng.recover_pending()
    assert recovered == 2  # no loss: both pending items resume ingestion

    n = await eng.poll_source(store.get_source(sid), _cfg())
    assert n == 0  # no dup: the re-yielded items were all already seen
    items_after = store.db.execute(
        "SELECT COUNT(*) FROM items WHERE source_id = ?", (sid,)
    ).fetchone()[0]
    assert items_after == 2  # still exactly two
    # And the cursor advances now that the poll completed.
    assert store.get_source_cursor(sid) == "cursor-1"


# ── SOURCE egress profile ───────────────────────────────────────────────────────


def test_source_egress_profile_exists():
    from personalclaw.net.policy import SOURCE, get_policy

    assert SOURCE.name == "source"
    # STRICT posture: public hosts only, IP pinned, redirect re-check inherited.
    assert SOURCE.allow_private is False and SOURCE.pin_resolved_ip is True
    # Registered so get_policy("source") resolves it (not the STRICT fallback).
    assert get_policy("source") is SOURCE


def test_engine_egress_policy_layers_source_profile(store):
    provider = FixtureSourceProvider([])
    eng, _ = _engine(store, provider)
    pol = eng.egress_policy()
    assert pol.name == "source" and pol.allow_private is False


# ── SourcesConfig round-trip ─────────────────────────────────────────────────────


def test_sources_config_roundtrips(tmp_path, monkeypatch):
    from unittest.mock import patch

    from personalclaw.config.loader import AppConfig

    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=p):
        cfg = AppConfig()
        cfg.sources.enabled = False
        cfg.sources.poll_interval_default_secs = 7200
        cfg.sources.network_floor_secs = 1800
        cfg.sources.max_sources = 42
        cfg.sources.max_items_per_poll = 25
        cfg.sources.daily_request_budget = 500
        cfg.save()

        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw["sources"]["enabled"] is False
        assert raw["sources"]["poll_interval_default_secs"] == 7200
        assert raw["sources"]["max_sources"] == 42

        reloaded = AppConfig.load()
        assert reloaded.sources.enabled is False
        assert reloaded.sources.network_floor_secs == 1800
        assert reloaded.sources.max_items_per_poll == 25
        assert reloaded.sources.daily_request_budget == 500


def test_sources_editable_config_keys_present():
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    for key in (
        "sources.enabled",
        "sources.poll_interval_default_secs",
        "sources.network_floor_secs",
        "sources.max_sources",
        "sources.max_items_per_poll",
        "sources.daily_request_budget",
    ):
        assert key in _EDITABLE_CONFIG


def test_sources_config_in_to_dict():
    from personalclaw.config.loader import AppConfig

    assert "sources" in AppConfig().to_dict()
    assert os.environ.get("PERSONALCLAW_HOME")  # isolation sentinel
