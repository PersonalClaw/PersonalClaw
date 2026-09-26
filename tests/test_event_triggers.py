"""Data-event triggers (#38): the pattern grammar over store rows, the spec helpers, the bus."""

from __future__ import annotations

import pytest

from personalclaw.event_triggers import (
    APP_EVENT,
    CONTENT_MATCH,
    EVENT_VARS,
    INBOX_ADDRESS,
    INBOX_MESSAGE,
    INBOX_SENDER,
    MEMORY_KEY_PATTERN,
    MEMORY_UPDATE,
    SOURCE_APP,
    SOURCE_INBOX,
    SOURCE_MEMORY,
    BusEvent,
    attach,
    detach,
    emit_event,
    event_spec,
    fire_payload,
    matches,
    router_attached,
    with_derived_source,
)
from personalclaw.triggers.models import Trigger, TriggerState


def _row(pattern: str, matcher: str = "", **fields) -> Trigger:
    return Trigger(
        id="event:t", name="t", kind="event", spec=event_spec(pattern, matcher), **fields
    )


def _hit(trigger: Trigger, **event) -> bool:
    base = {"source": SOURCE_MEMORY, "event_type": "create", "key": "k", "value": "v"}
    return matches(trigger, **{**base, **event})


# ── pure matching ──


def test_memory_update_matches_any_memory_write():
    assert _hit(_row(MEMORY_UPDATE), key="anything")


def test_key_pattern_glob():
    t = _row(MEMORY_KEY_PATTERN, "project.acme.*")
    assert _hit(t, key="project.acme.deadline")
    assert not _hit(t, key="project.other.x")


def test_content_match_regex():
    t = _row(CONTENT_MATCH, r"\bdeadline\b")
    assert _hit(t, event_type="update", value="the deadline is friday")
    assert not _hit(t, event_type="update", value="no match here")


def test_content_match_bad_regex_falls_back_to_substring():
    assert _hit(_row(CONTENT_MATCH, "[unclosed"), value="has [unclosed bracket")


def test_content_match_scans_a_bounded_prefix_only():
    from personalclaw.event_triggers import CONTENT_MATCH_SCAN_LIMIT

    late = "x" * CONTENT_MATCH_SCAN_LIMIT + "needle"
    assert not _hit(_row(CONTENT_MATCH, "needle"), value=late)


def test_a_disabled_row_never_matches():
    assert not _hit(_row(MEMORY_UPDATE, enabled=False))


@pytest.mark.parametrize("state", ["parked", "autopaused", "quarantined", "paused", "retired"])
def test_a_row_that_is_not_ACTIVE_never_matches(state):
    """`enabled` and `state` asked as ONE question: checking `enabled` alone is how a parked or
    autopaused trigger keeps firing."""
    assert not _hit(_row(MEMORY_UPDATE, state=state))
    assert _hit(_row(MEMORY_UPDATE, state=TriggerState.ACTIVE.value))


def test_only_an_event_row_matches():
    clock = Trigger(id="clock:x", name="x", kind="clock", spec=event_spec(MEMORY_UPDATE))
    assert not _hit(clock)


# ── EIAT-1: source scoping + inbox patterns ──


def test_a_memory_trigger_never_fires_on_an_inbox_event():
    """A memory trigger is invisible to an inbox event — the source gate, not the pattern."""
    t = _row(MEMORY_UPDATE)
    assert _hit(t)
    assert not _hit(t, source=SOURCE_INBOX, event_type="message_received")


def test_a_row_whose_source_contradicts_its_pattern_matches_nothing():
    """A hand-edited `{source: inbox, pattern: MemoryUpdate}` must not become an inbox catch-all."""
    t = Trigger(
        id="event:t", name="t", kind="event", spec={"source": "inbox", "pattern": MEMORY_UPDATE}
    )
    assert not _hit(t, source=SOURCE_INBOX, event_type="message_received")
    assert not _hit(t)


def test_inbox_message_matches_any_inbox_event_only():
    t = _row(INBOX_MESSAGE)
    assert _hit(t, source=SOURCE_INBOX, event_type="message_received", value="hi")
    assert not _hit(t, value="hi")


def test_inbox_sender_glob_reads_meta():
    t = _row(INBOX_SENDER, "boss@*")
    inbox = {"source": SOURCE_INBOX, "event_type": "message_received"}
    assert _hit(t, **inbox, meta={"sender": "boss@corp.test"})
    assert not _hit(t, **inbox, meta={"sender": "spam@corp.test"})
    # No meta → nothing to match a sender against → no fire.
    assert not _hit(t, **inbox)


def test_inbox_address_glob_reads_meta():
    t = _row(INBOX_ADDRESS, "C_ALERTS*")
    inbox = {"source": SOURCE_INBOX, "event_type": "message_received"}
    assert _hit(t, **inbox, meta={"address": "C_ALERTS_42"})
    assert not _hit(t, **inbox, meta={"address": "C_RANDOM"})


def test_an_app_event_glob_matches_the_namespaced_name():
    t = _row(APP_EVENT, "app:calendar:*")
    app = {"source": SOURCE_APP}
    assert _hit(t, **app, event_type="app:calendar:meeting_soon")
    assert not _hit(t, **app, event_type="app:weather:rain")


def test_an_empty_app_glob_is_the_catch_all():
    assert _hit(_row(APP_EVENT), source=SOURCE_APP, event_type="app:anything:at_all")


# ── the spec helpers ──


def test_event_spec_derives_the_source_and_keeps_only_the_patterns_matcher():
    assert event_spec(INBOX_SENDER, "alice@*") == {
        "source": "inbox",
        "pattern": INBOX_SENDER,
        "sender_glob": "alice@*",
    }
    # A pattern with no matcher carries none, whatever was offered.
    assert event_spec(MEMORY_UPDATE, "ignored") == {"source": "memory", "pattern": MEMORY_UPDATE}
    # An empty matcher is omitted, so validation reports a missing required one.
    assert event_spec(MEMORY_KEY_PATTERN, "") == {"source": "memory", "pattern": MEMORY_KEY_PATTERN}


def test_with_derived_source_fills_the_source_but_never_overrides_one():
    assert with_derived_source({"pattern": MEMORY_UPDATE})["source"] == "memory"
    # A contradiction is kept, so validation refuses it rather than silently correcting it.
    assert with_derived_source({"pattern": MEMORY_UPDATE, "source": "inbox"})["source"] == "inbox"


# ── the bus ──


def test_an_attached_router_receives_every_event_and_nothing_is_spooled(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    seen: list[BusEvent] = []

    def router(event):
        seen.append(event)

    attach(router)
    try:
        assert router_attached()
        emit_event(source=SOURCE_MEMORY, event_type="create", key="k", value="v", now=1.0)
    finally:
        detach(router)
    assert [(e.source, e.key, e.value) for e in seen] == [("memory", "k", "v")]
    assert not (tmp_path / "trigger-spool.jsonl").exists()
    assert not router_attached()


def test_detach_removes_only_the_router_it_names():
    """A stale shutdown must not detach a router a newer gateway in the same process attached."""

    def old(event):
        return None

    def new(event):
        return None

    attach(new)
    try:
        detach(old)
        assert router_attached()
    finally:
        detach(new)
    assert not router_attached()


def test_a_raising_router_never_breaks_the_write_that_emitted():
    def boom(event):
        raise RuntimeError("router down")

    attach(boom)
    try:
        emit_event(source=SOURCE_MEMORY, event_type="create", key="k", value="v", now=1.0)
    finally:
        detach(boom)


# ── what a fire hands the dispatch ──


def test_the_value_is_fenced_at_origin_with_its_provenance():
    event = BusEvent(source="memory", event_type="create", key="project.x", value="hello", now=1.0)
    payload, context = fire_payload("event:t", event)
    assert payload["trigger_id"] == "event:t"
    assert payload["key"] == "project.x"
    assert "source_type=event:memory:create" in payload["value"]
    assert "source_id=project.x" in payload["value"]
    assert "transformation_path=truncate:2000" in payload["value"]
    assert "hello" in payload["value"]
    assert context.startswith("project.x: ") and "hello" in context


def test_a_long_value_is_truncated_to_2000_inside_the_fence():
    event = BusEvent(source="memory", event_type="create", key="k", value="z" * 5000, now=1.0)
    payload, _context = fire_payload("event:t", event)
    assert "z" * 2000 in payload["value"]
    assert "z" * 2001 not in payload["value"]
    assert payload["value"].rstrip().endswith("</untrusted_content>")


def test_text_already_fenced_at_origin_is_not_re_wrapped():
    from personalclaw.security import fence_untrusted

    fenced = fence_untrusted(
        "app text", source="trigger:app:cal:x", source_type="app:cal", source_id="x"
    )
    event = BusEvent(source="app", event_type="app:cal:x", key="x", value=fenced, now=1.0)
    payload, _context = fire_payload("event:t", event)
    assert payload["value"].count("<untrusted_content") == 1
    assert "source_type=app:cal" in payload["value"]


def test_the_advertised_variables_are_exactly_what_the_payload_carries():
    """The create form offers `EVENT_VARS`; each must be something a template can resolve."""
    event = BusEvent(source="inbox", event_type="message_received", key="k", value="v", now=1.0)
    payload, _context = fire_payload("event:t", event)
    offered = {v.lstrip("$") for v in EVENT_VARS} - {"EVENT", "CONTEXT"}
    assert offered <= set(payload), offered - set(payload)
