"""Data-event triggers (#38) — memory-event pattern matching + store + auto-disable."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.event_triggers import (
    CONTENT_MATCH,
    MEMORY_KEY_PATTERN,
    MEMORY_UPDATE,
    EventTrigger,
    EventTriggerEngine,
    EventTriggerStore,
    matches,
)


# ── pure matching ──


def test_memory_update_matches_any():
    t = EventTrigger(id="t", pattern=MEMORY_UPDATE)
    assert matches(t, event_type="create", key="anything", value="v")


def test_key_pattern_glob():
    t = EventTrigger(id="t", pattern=MEMORY_KEY_PATTERN, key_glob="project.acme.*")
    assert matches(t, event_type="create", key="project.acme.deadline", value="v")
    assert not matches(t, event_type="create", key="project.other.x", value="v")


def test_content_match_regex():
    t = EventTrigger(id="t", pattern=CONTENT_MATCH, content_re=r"\bdeadline\b")
    assert matches(t, event_type="update", key="k", value="the deadline is friday")
    assert not matches(t, event_type="update", key="k", value="no match here")


def test_content_match_bad_regex_falls_back_to_substring():
    t = EventTrigger(id="t", pattern=CONTENT_MATCH, content_re="[unclosed")
    assert matches(t, event_type="update", key="k", value="has [unclosed bracket")


def test_disabled_never_matches():
    t = EventTrigger(id="t", pattern=MEMORY_UPDATE, enabled=False)
    assert not matches(t, event_type="create", key="k", value="v")


def test_exhausted_max_fires_never_matches():
    t = EventTrigger(id="t", pattern=MEMORY_UPDATE, max_fires=2, fire_count=2)
    assert not matches(t, event_type="create", key="k", value="v")


# ── store + auto-disable ──


@pytest.fixture
def store(tmp_path):
    return EventTriggerStore(tmp_path / "event_triggers.json")


def test_store_crud(store):
    store.upsert(EventTrigger(id="a", pattern=MEMORY_UPDATE))
    assert len(store.load()) == 1
    store.upsert(EventTrigger(id="a", pattern=CONTENT_MATCH, content_re="x"))  # replace
    assert store.load()[0].pattern == CONTENT_MATCH
    assert store.delete("a") is True
    assert store.load() == []


def test_record_fire_auto_disables_at_max(store):
    store.upsert(EventTrigger(id="oneshot", pattern=MEMORY_UPDATE, max_fires=1))
    store.record_fire("oneshot", now=100.0)
    t = store.load()[0]
    assert t.fire_count == 1 and t.enabled is False  # exhausted → self-retired


def test_record_fire_unlimited_stays_enabled(store):
    store.upsert(EventTrigger(id="forever", pattern=MEMORY_UPDATE, max_fires=0))
    store.record_fire("forever", now=1.0)
    store.record_fire("forever", now=2.0)
    t = store.load()[0]
    assert t.fire_count == 2 and t.enabled is True


# ── engine: fire + debounce + rate cap ──


def test_engine_fires_action(store, monkeypatch):
    fired = []

    class _StubProvider:
        async def execute(self, cfg, ctx, timeout=30):
            fired.append((cfg, ctx.payload))

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda n: _StubProvider())
    store.upsert(EventTrigger(id="t", pattern=MEMORY_KEY_PATTERN, key_glob="x.*", action_provider="notify", action_config={"title": "hi"}, debounce_secs=0))
    eng = EventTriggerEngine(store=store)

    async def go():
        eng.on_memory_event(event_type="create", key="x.y", value="v", now=10.0)
        await asyncio.sleep(0.05)  # let the scheduled task run
    asyncio.run(go())
    assert fired and fired[0][1]["key"] == "x.y"
    # fire recorded
    assert store.load()[0].fire_count == 1


def test_engine_debounce_suppresses_rapid_refire(store, monkeypatch):
    n = {"count": 0}

    class _Stub:
        async def execute(self, cfg, ctx, timeout=30):
            n["count"] += 1

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda _n: _Stub())
    store.upsert(EventTrigger(id="t", pattern=MEMORY_UPDATE, debounce_secs=30))
    eng = EventTriggerEngine(store=store)

    async def go():
        eng.on_memory_event(event_type="create", key="k", value="v", now=10.0)
        eng.on_memory_event(event_type="create", key="k", value="v", now=11.0)  # within debounce
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert n["count"] == 1  # second suppressed
