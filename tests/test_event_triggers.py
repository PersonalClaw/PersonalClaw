"""Data-event triggers (#38) — memory-event pattern matching + store + auto-disable."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.event_triggers import (
    CONTENT_MATCH,
    MEMORY_KEY_PATTERN,
    MEMORY_UPDATE,
    SOURCE_INBOX,
    SOURCE_MEMORY,
    EventTrigger,
    EventTriggerEngine,
    EventTriggerStore,
    matches,
)

# ── pure matching ──


def test_memory_update_matches_any():
    t = EventTrigger(id="t", pattern=MEMORY_UPDATE)
    assert matches(t, source=SOURCE_MEMORY, event_type="create", key="anything", value="v")


def test_key_pattern_glob():
    t = EventTrigger(id="t", pattern=MEMORY_KEY_PATTERN, key_glob="project.acme.*")
    assert matches(
        t, source=SOURCE_MEMORY, event_type="create", key="project.acme.deadline", value="v"
    )
    assert not matches(
        t, source=SOURCE_MEMORY, event_type="create", key="project.other.x", value="v"
    )


def test_content_match_regex():
    t = EventTrigger(id="t", pattern=CONTENT_MATCH, content_re=r"\bdeadline\b")
    assert matches(
        t, source=SOURCE_MEMORY, event_type="update", key="k", value="the deadline is friday"
    )
    assert not matches(t, source=SOURCE_MEMORY, event_type="update", key="k", value="no match here")


def test_content_match_bad_regex_falls_back_to_substring():
    t = EventTrigger(id="t", pattern=CONTENT_MATCH, content_re="[unclosed")
    assert matches(
        t, source=SOURCE_MEMORY, event_type="update", key="k", value="has [unclosed bracket"
    )


def test_disabled_never_matches():
    t = EventTrigger(id="t", pattern=MEMORY_UPDATE, enabled=False)
    assert not matches(t, source=SOURCE_MEMORY, event_type="create", key="k", value="v")


def test_exhausted_max_fires_never_matches():
    t = EventTrigger(id="t", pattern=MEMORY_UPDATE, max_fires=2, fire_count=2)
    assert not matches(t, source=SOURCE_MEMORY, event_type="create", key="k", value="v")


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

    monkeypatch.setattr(
        "personalclaw.action_providers.get_action_provider", lambda n: _StubProvider()
    )
    store.upsert(
        EventTrigger(
            id="t",
            pattern=MEMORY_KEY_PATTERN,
            key_glob="x.*",
            action_provider="notify",
            action_config={"title": "hi"},
            debounce_secs=0,
        )
    )
    eng = EventTriggerEngine(store=store)

    async def go():
        eng.on_event(source=SOURCE_MEMORY, event_type="create", key="x.y", value="v", now=10.0)
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
        eng.on_event(source=SOURCE_MEMORY, event_type="create", key="k", value="v", now=10.0)
        eng.on_event(
            source=SOURCE_MEMORY, event_type="create", key="k", value="v", now=11.0
        )  # within debounce
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert n["count"] == 1  # second suppressed


# ── EIAT-1: source scoping + inbox patterns ──


def test_memory_trigger_never_fires_on_inbox_event():
    """A memory trigger is invisible to an inbox event — the source gate, not the pattern."""
    t = EventTrigger(id="t", pattern=MEMORY_UPDATE, source=SOURCE_MEMORY)
    assert matches(t, source=SOURCE_MEMORY, event_type="create", key="k", value="v")
    assert not matches(t, source=SOURCE_INBOX, event_type="message_received", key="k", value="v")


def test_inbox_message_matches_any_inbox_event_only():
    from personalclaw.event_triggers import INBOX_MESSAGE

    t = EventTrigger(id="t", pattern=INBOX_MESSAGE, source=SOURCE_INBOX)
    assert matches(t, source=SOURCE_INBOX, event_type="message_received", key="k", value="hi")
    # ...but never on a memory event, even though InboxMessage otherwise matches anything.
    assert not matches(t, source=SOURCE_MEMORY, event_type="create", key="k", value="hi")


def test_inbox_sender_glob_reads_meta():
    from personalclaw.event_triggers import INBOX_SENDER

    t = EventTrigger(id="t", pattern=INBOX_SENDER, source=SOURCE_INBOX, sender_glob="boss@*")
    assert matches(
        t,
        source=SOURCE_INBOX,
        event_type="message_received",
        key="k",
        value="v",
        meta={"sender": "boss@corp.test"},
    )
    assert not matches(
        t,
        source=SOURCE_INBOX,
        event_type="message_received",
        key="k",
        value="v",
        meta={"sender": "spam@corp.test"},
    )
    # No meta → nothing to match a sender against → no fire.
    assert not matches(t, source=SOURCE_INBOX, event_type="message_received", key="k", value="v")


def test_inbox_address_glob_reads_meta():
    from personalclaw.event_triggers import INBOX_ADDRESS

    t = EventTrigger(id="t", pattern=INBOX_ADDRESS, source=SOURCE_INBOX, address_glob="C_ALERTS*")
    assert matches(
        t,
        source=SOURCE_INBOX,
        event_type="message_received",
        key="k",
        value="v",
        meta={"address": "C_ALERTS_42"},
    )
    assert not matches(
        t,
        source=SOURCE_INBOX,
        event_type="message_received",
        key="k",
        value="v",
        meta={"address": "C_RANDOM"},
    )


def test_inbox_sender_without_glob_never_matches():
    """An InboxSender with no glob matches nothing (the store/handlers reject creating one)."""
    from personalclaw.event_triggers import INBOX_SENDER

    t = EventTrigger(id="t", pattern=INBOX_SENDER, source=SOURCE_INBOX, sender_glob="")
    assert not matches(
        t,
        source=SOURCE_INBOX,
        event_type="message_received",
        key="k",
        value="v",
        meta={"sender": "anyone@corp.test"},
    )


def test_from_dict_infers_source_for_legacy_memory_spec():
    """A spec persisted before EIAT-1 (no ``source`` key) keeps memory semantics."""
    t = EventTrigger.from_dict({"id": "legacy", "pattern": MEMORY_UPDATE})
    assert t.source == SOURCE_MEMORY
    assert matches(t, source=SOURCE_MEMORY, event_type="create", key="k", value="v")
    assert not matches(t, source=SOURCE_INBOX, event_type="message_received", key="k", value="v")


def test_to_dict_round_trip_preserves_inbox_fields():
    from personalclaw.event_triggers import INBOX_SENDER

    t = EventTrigger(
        id="t", pattern=INBOX_SENDER, source=SOURCE_INBOX, sender_glob="a*", address_glob="C*"
    )
    back = EventTrigger.from_dict(t.to_dict())
    assert back.source == SOURCE_INBOX
    assert back.sender_glob == "a*"
    assert back.address_glob == "C*"


def test_engine_scopes_fire_by_source(store, monkeypatch):
    """The live engine fires only the trigger whose source matches the event."""
    fired: list[str] = []

    class _Stub:
        async def execute(self, cfg, ctx, timeout=30):
            fired.append(ctx.payload["trigger_id"])

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda _n: _Stub())
    store.upsert(
        EventTrigger(id="mem", pattern=MEMORY_UPDATE, source=SOURCE_MEMORY, debounce_secs=0)
    )
    from personalclaw.event_triggers import INBOX_MESSAGE

    store.upsert(
        EventTrigger(id="inb", pattern=INBOX_MESSAGE, source=SOURCE_INBOX, debounce_secs=0)
    )
    eng = EventTriggerEngine(store=store)

    async def go():
        eng.on_event(
            source=SOURCE_INBOX,
            event_type="message_received",
            key="m1",
            value="hi",
            now=10.0,
            meta={"sender": "a@b.test"},
        )
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert fired == ["inb"]  # the memory trigger did NOT fire on an inbox event
