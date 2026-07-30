"""Inbox as the attention store (INBOX-NOTIFICATIONS-UNIFICATION T2.1/T2.2).

Two risks drive these tests.

**Old items must keep working.** The inbox began as a channel-message surface, and items
written by every previous release are on disk right now. `item_kind`/`refs`/`SEEN` are
additive, but "additive" is a claim to verify: a `from_dict` that dropped an unknown key,
or an id whose `ts` no longer parses, would corrupt sorting and retention silently.

**One event must produce one notification.** `emit_attention_item` exists precisely so a
caller can't do `store.add()` and `state.notify()` separately and drift them apart. The
dedup path matters as much: a watchdog that re-observes the same waiting loop every tick
must not stack a hundred rows or re-notify.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from personalclaw import notification_kinds as nk
from personalclaw.inbox import (
    NON_CHANNEL_KINDS,
    Classification,
    InboxItem,
    InboxStore,
    ItemKind,
    ItemStatus,
    emit_attention_item,
    make_item_id,
)


@pytest.fixture()
def store(tmp_path):
    return InboxStore(path=tmp_path / "inbox_items.json")


@pytest.fixture()
def state():
    st = MagicMock()
    st.notify = MagicMock()
    return st


def _channel_item(**over):
    base = dict(
        id="C123_1700000000.123",
        channel="C123",
        channel_name="general",
        thread_ts=None,
        message="hello",
        sender_id="U1",
        sender_name="Ana",
    )
    base.update(over)
    return InboxItem(**base)


# ── T2.1: additive extension ────────────────────────────────────────────


def test_new_fields_have_back_compat_defaults():
    item = _channel_item()
    assert item.item_kind == ItemKind.MESSAGE.value
    assert item.refs == {}


def test_from_dict_loads_an_item_written_before_these_fields_existed():
    """The exact shape a previous release persisted — no item_kind, no refs."""
    old = {
        "id": "C1_1700000000.5",
        "channel": "C1",
        "channel_name": "general",
        "thread_ts": None,
        "message": "hi",
        "sender_id": "U1",
        "sender_name": "Ana",
        "status": "pending",
    }
    item = InboxItem.from_dict(old)
    assert item.item_kind == ItemKind.MESSAGE.value, "old items must read as messages"
    assert item.refs == {}
    assert item.ts == "1700000000.5"


def test_from_dict_ignores_a_field_this_build_does_not_know():
    """Forward tolerance: an item written by a NEWER build must still load."""
    d = _channel_item().to_dict()
    d["some_future_field"] = "x"
    assert InboxItem.from_dict(d).id == d["id"]


def test_refs_default_is_not_shared_between_items():
    """A mutable default shared across instances would cross-link every item."""
    a, b = _channel_item(id="C1_1.0"), _channel_item(id="C1_2.0")
    a.refs["loop"] = "L1"
    assert b.refs == {}


def test_new_fields_round_trip_through_disk(store):
    item = _channel_item(item_kind=ItemKind.NEEDS_INPUT.value, refs={"loop": "L9"})
    store.add(item)
    store.save()
    reloaded = InboxStore(path=store._path)
    reloaded.load()
    got = reloaded.items[item.id]
    assert got.item_kind == ItemKind.NEEDS_INPUT.value
    assert got.refs == {"loop": "L9"}


def test_seen_is_a_real_status():
    assert ItemStatus.SEEN.value == "seen"
    assert ItemStatus("seen") is ItemStatus.SEEN


def test_sent_status_survives_the_extension():
    """SENT predates the lifecycle and means something the others don't."""
    assert ItemStatus.SENT.value == "sent"


# ── T2.1: the id contract ───────────────────────────────────────────────


def test_generated_id_keeps_the_trailing_timestamp_contract():
    """`InboxItem.ts` rsplits on the last underscore; sorting/retention read it."""
    item_id = make_item_id(ItemKind.NEEDS_INPUT.value, now=1700000000.5)
    item = _channel_item(id=item_id)
    assert item.ts == "1700000000.500000"
    assert float(item.ts) == pytest.approx(1700000000.5)


def test_generated_id_starts_with_its_kind():
    assert make_item_id("proposal").startswith("proposal_")


def test_generated_ids_are_unique_within_the_same_instant():
    """`{channel}_{ts}` got uniqueness from channel message ids; these need the uuid."""
    ids = {make_item_id("needs_input", now=1700000000.0) for _ in range(200)}
    assert len(ids) == 200


def test_generated_id_has_exactly_the_expected_shape():
    parts = make_item_id("needs_input", now=1700000000.0).split("_")
    assert parts[0] == "needs"  # kind itself contains an underscore…
    assert parts[1] == "input"  # …which is fine: only the LAST segment is the ts
    assert len(parts[2]) == 8
    assert float(parts[3]) == 1700000000.0


def test_non_channel_kinds_are_enumerated():
    assert ItemKind.NEEDS_INPUT.value in NON_CHANNEL_KINDS
    assert ItemKind.MESSAGE.value not in NON_CHANNEL_KINDS
    assert ItemKind.MENTION.value not in NON_CHANNEL_KINDS


# ── T2.2: emit_attention_item ───────────────────────────────────────────


def test_emit_creates_a_pending_item_and_notifies_once(store, state):
    item_id = emit_attention_item(
        state,
        source="loop",
        kind="needs_input",
        title="Loop needs your input",
        body="my loop",
        refs={"loop": "L1"},
        store=store,
    )
    assert item_id
    item = store.items[item_id]
    assert item.status == ItemStatus.PENDING
    assert item.item_kind == ItemKind.NEEDS_INPUT.value
    assert item.refs["loop"] == "L1"
    assert state.notify.call_count == 1, "exactly one notification per event"


def test_emit_notifies_with_the_resolvable_wire_kind(store, state):
    """The wire value must map back to the registered pair, or the rule is lost."""
    emit_attention_item(state, source="loop", kind="needs_input", title="T", body="B", store=store)
    wire_kind = state.notify.call_args[0][0]
    assert nk.kind_for_legacy(wire_kind).key == "loop/needs_input"


def test_emit_links_the_notification_to_its_item(store, state):
    item_id = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", refs={"loop": "L1"}, store=store
    )
    meta = state.notify.call_args.kwargs["meta"]
    assert meta["inbox_item"] == item_id
    assert meta["item_kind"] == ItemKind.NEEDS_INPUT.value
    assert meta["loop"] == "L1"


def test_emit_persists_immediately(store, state):
    """A crash between add() and flush() would lose a standing request."""
    item_id = emit_attention_item(state, source="loop", kind="needs_input", title="T", store=store)
    on_disk = json.loads(store._path.read_text())
    assert [i["id"] for i in on_disk["items"]] == [item_id]


def test_emitted_item_cannot_be_replied_to(store, state):
    """No channel behind it — a Send button here would be a dead control."""
    item_id = emit_attention_item(state, source="loop", kind="needs_input", title="T", store=store)
    assert store.items[item_id].can_reply is False


def test_emitted_item_is_classified_as_needing_a_reply(store, state):
    """It must not be filtered out as noise by the classification-based views."""
    item_id = emit_attention_item(state, source="loop", kind="needs_input", title="T", store=store)
    assert store.items[item_id].classification == Classification.NEEDS_REPLY.value


def test_item_kind_defaults_to_the_notification_kind(store, state):
    item_id = emit_attention_item(state, source="skills", kind="proposal", title="T", store=store)
    assert store.items[item_id].item_kind == "proposal"


def test_item_kind_can_differ_from_the_notification_kind(store, state):
    item_id = emit_attention_item(
        state,
        source="loop",
        kind="needs_input",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="T",
        store=store,
    )
    assert store.items[item_id].item_kind == ItemKind.AGENT_REQUEST.value


def test_body_falls_back_to_the_title(store, state):
    """An item with an empty message renders as a blank row."""
    item_id = emit_attention_item(
        state, source="loop", kind="needs_input", title="Only", store=store
    )
    assert store.items[item_id].message == "Only"


# ── T2.2: dedup ─────────────────────────────────────────────────────────


def test_dedup_returns_the_same_item_and_does_not_renotify(store, state):
    """A watchdog re-observing the same wait must not stack rows or re-interrupt."""
    first = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="loop:L1:wait"
    )
    second = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="loop:L1:wait"
    )
    assert first == second
    assert len(store.items) == 1
    assert state.notify.call_count == 1, "the user was already told"


def test_dedup_is_scoped_to_its_key(store, state):
    emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="loop:L1:wait"
    )
    emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="loop:L2:wait"
    )
    assert len(store.items) == 2


def test_no_dedup_key_means_every_emission_is_distinct(store, state):
    for _ in range(3):
        emit_attention_item(state, source="loop", kind="needs_input", title="T", store=store)
    assert len(store.items) == 3
    assert state.notify.call_count == 3


@pytest.mark.parametrize("resolved", [ItemStatus.HANDLED, ItemStatus.DISMISSED])
def test_a_resolved_item_does_not_suppress_a_new_one(store, state, resolved):
    """Once the user has dealt with it, a later re-emission is genuinely new."""
    first = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="k"
    )
    store.items[first].status = resolved
    second = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="k"
    )
    assert second != first
    assert state.notify.call_count == 2


def test_a_seen_item_still_suppresses(store, state):
    """SEEN means surfaced-but-unresolved; re-notifying would be nagging."""
    first = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="k"
    )
    store.items[first].status = ItemStatus.SEEN
    assert (
        emit_attention_item(
            state, source="loop", kind="needs_input", title="T", store=store, dedup_key="k"
        )
        == first
    )
    assert state.notify.call_count == 1


def test_dedup_picks_the_newest_open_match(store, state):
    """Two open items with one key (possible before dedup existed) → newest wins."""
    older = _channel_item(id="needs_input_aaaaaaaa_100.0", created_at=100.0)
    older.refs = {"dedup_key": "k"}
    newer = _channel_item(id="needs_input_bbbbbbbb_200.0", created_at=200.0)
    newer.refs = {"dedup_key": "k"}
    store.add(older)
    store.add(newer)
    got = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="k"
    )
    assert got == newer.id


def test_dedup_key_is_recorded_on_the_item(store, state):
    item_id = emit_attention_item(
        state, source="loop", kind="needs_input", title="T", store=store, dedup_key="k"
    )
    assert store.items[item_id].refs["dedup_key"] == "k"


# ── T2.2: failure isolation ─────────────────────────────────────────────


def test_a_store_write_failure_still_delivers_the_notification(store, state):
    """Failing to persist must not ALSO lose the user's only signal."""
    with patch.object(store, "add", side_effect=OSError("disk full")):
        item_id = emit_attention_item(
            state, source="loop", kind="needs_input", title="T", store=store
        )
    assert item_id == ""
    assert state.notify.call_count == 1


def test_a_notify_failure_still_persists_the_item(store, state):
    """The durable record is the more important half — it survives alone."""
    state.notify.side_effect = RuntimeError("no clients")
    item_id = emit_attention_item(state, source="loop", kind="needs_input", title="T", store=store)
    assert item_id in store.items


def test_no_state_still_creates_the_item(store):
    """Called before the dashboard exists (early startup) — the item still lands."""
    item_id = emit_attention_item(None, source="loop", kind="needs_input", title="T", store=store)
    assert item_id in store.items


# ── T2.2: the loop watchdog wiring ──────────────────────────────────────


def test_watchdog_attention_events_are_all_registered_kinds():
    from personalclaw.loop.watchdog import LoopWatchdog

    for event, kind in LoopWatchdog._ATTENTION_EVENTS.items():
        assert nk.kind_for_legacy(kind).kind != nk.GENERIC_KIND, f"{event} → {kind} unregistered"


def test_watchdog_attention_events_are_a_subset_of_its_notify_events():
    """An attention event with no title would notify with an empty string."""
    from personalclaw.loop.watchdog import LoopWatchdog

    assert set(LoopWatchdog._ATTENTION_EVENTS) <= set(LoopWatchdog._NOTIFY_EVENTS)


def test_watchdog_outcome_events_are_not_attention_events():
    """complete/failed/stage_advance already happened — an inbox row would be busywork."""
    from personalclaw.loop.watchdog import LoopWatchdog

    for event in ("complete", "failed", "stage_advance", "judge_blind", "ship_blocked"):
        assert event not in LoopWatchdog._ATTENTION_EVENTS


# ── T2.3: API — kind filtering, chips, mark-SEEN ─────────────────────────


def _api_request(store, *, query=None, body=None):
    """A request whose app['state'] carries a pre-loaded inbox store."""
    st = MagicMock()
    st._inbox_svc = None
    st._inbox_state = MagicMock()
    st._inbox_store = store
    st.broadcast_ws = MagicMock()
    req = MagicMock()
    req.app = {"state": st}
    req.query = query or {}
    if body is not None:
        from unittest.mock import AsyncMock

        req.json = AsyncMock(return_value=body)
    return req, st


def _seed(store):
    for kind, status, ts in [
        (ItemKind.MESSAGE.value, ItemStatus.PENDING.value, 100.0),
        (ItemKind.MESSAGE.value, ItemStatus.HANDLED.value, 101.0),
        (ItemKind.NEEDS_INPUT.value, ItemStatus.PENDING.value, 102.0),
        (ItemKind.NEEDS_INPUT.value, ItemStatus.SEEN.value, 103.0),
        (ItemKind.PROPOSAL.value, ItemStatus.DISMISSED.value, 104.0),
    ]:
        item = _channel_item(id=f"{kind}_x_{ts}", item_kind=kind, created_at=ts)
        item.status = status
        store.add(item)
    store.save()


async def _payload(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_list_without_a_filter_returns_everything(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store)
    assert len(await _payload(await h.api_inbox_list(req))) == 5


@pytest.mark.asyncio
async def test_list_filters_by_kind(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store, query={"kind": "needs_input"})
    got = await _payload(await h.api_inbox_list(req))
    assert {i["item_kind"] for i in got} == {"needs_input"}


@pytest.mark.asyncio
async def test_list_accepts_several_kinds(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store, query={"kind": "needs_input,proposal"})
    got = await _payload(await h.api_inbox_list(req))
    assert {i["item_kind"] for i in got} == {"needs_input", "proposal"}


@pytest.mark.asyncio
async def test_an_unknown_kind_filters_to_nothing(store):
    """Returning everything for a typo would read as "the filter is broken"."""
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store, query={"kind": "no-such-kind"})
    assert await _payload(await h.api_inbox_list(req)) == []


@pytest.mark.asyncio
async def test_an_item_with_no_kind_counts_as_a_message(store):
    """Items on disk from before this field existed must still match the message chip."""
    from personalclaw.dashboard import handlers_inbox as h

    legacy = _channel_item(id="C1_50.0", created_at=50.0)
    legacy.item_kind = ""  # what a tolerant read of a pre-field item yields
    store.add(legacy)
    store.save()
    req, _ = _api_request(store, query={"kind": "message"})
    assert len(await _payload(await h.api_inbox_list(req))) == 1


@pytest.mark.asyncio
async def test_pending_endpoint_also_filters(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store, query={"kind": "needs_input"})
    got = await _payload(await h.api_inbox_pending(req))
    # Only the PENDING needs_input — the SEEN one is not "needs attention now".
    assert len(got) == 1 and got[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_kinds_endpoint_counts_open_as_pending_plus_seen(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store)
    rows = {r["kind"]: r for r in (await _payload(await h.api_inbox_kinds(req)))["kinds"]}
    assert rows["needs_input"] == {
        "kind": "needs_input",
        "total": 2,
        "open": 2,
        "channel": False,
    }
    assert rows["message"]["open"] == 1, "the HANDLED message is not open"
    assert rows["proposal"]["open"] == 0, "a dismissed proposal is resolved"


@pytest.mark.asyncio
async def test_kinds_endpoint_omits_kinds_with_no_items(store):
    """A chip for an empty kind is a dead control."""
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store)
    kinds = {r["kind"] for r in (await _payload(await h.api_inbox_kinds(req)))["kinds"]}
    assert "email" not in kinds and "digest" not in kinds


@pytest.mark.asyncio
async def test_kinds_endpoint_flags_channel_backed_kinds(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store)
    rows = {r["kind"]: r for r in (await _payload(await h.api_inbox_kinds(req)))["kinds"]}
    assert rows["message"]["channel"] is True
    assert rows["needs_input"]["channel"] is False


@pytest.mark.asyncio
async def test_seen_advances_only_pending_items(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, st = _api_request(store, body={})
    result = await _payload(await h.api_inbox_seen(req))
    assert result["seen"] == 2, "exactly the two PENDING items advanced"
    # Three SEEN afterwards, not two: the seed already contains one SEEN needs_input, and
    # it is left alone rather than re-counted.
    statuses = sorted(i.status for i in store.items.values())
    assert statuses == ["dismissed", "handled", "seen", "seen", "seen"]
    assert st.broadcast_ws.call_count == 2, "only the items that actually changed broadcast"


@pytest.mark.asyncio
async def test_seen_never_drags_a_resolved_item_backwards(store):
    """HANDLED → SEEN would resurrect it in every unresolved view."""
    from personalclaw.dashboard import handlers_inbox as h

    item = _channel_item(id="message_x_1.0")
    item.status = ItemStatus.HANDLED.value
    store.add(item)
    req, _ = _api_request(store, body={})
    await h.api_inbox_seen(req)
    assert store.items[item.id].status == ItemStatus.HANDLED.value


@pytest.mark.asyncio
async def test_seen_is_idempotent(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store, body={})
    first = await _payload(await h.api_inbox_seen(req))
    second = await _payload(await h.api_inbox_seen(req))
    assert first["seen"] == 2 and second["seen"] == 0


@pytest.mark.asyncio
async def test_seen_can_target_specific_ids(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    target = next(i for i in store.items.values() if i.status == ItemStatus.PENDING.value)
    req, _ = _api_request(store, body={"ids": [target.id]})
    assert (await _payload(await h.api_inbox_seen(req)))["seen"] == 1
    assert store.items[target.id].status == ItemStatus.SEEN.value


@pytest.mark.asyncio
async def test_seen_can_target_a_kind(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store, body={"kind": "needs_input"})
    assert (await _payload(await h.api_inbox_seen(req)))["seen"] == 1
    msg = next(
        i for i in store.items.values() if i.item_kind == "message" and i.created_at == 100.0
    )
    assert msg.status == ItemStatus.PENDING.value, "another kind must be untouched"


@pytest.mark.asyncio
async def test_seen_persists_to_disk(store):
    from personalclaw.dashboard import handlers_inbox as h

    _seed(store)
    req, _ = _api_request(store, body={})
    await h.api_inbox_seen(req)
    reloaded = InboxStore(path=store._path)
    reloaded.load()
    # 3 = the two just advanced + the one the seed already had SEEN.
    assert sum(1 for i in reloaded.items.values() if i.status == "seen") == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["not-json", ["a"], {"ids": "x"}])
async def test_seen_rejects_a_malformed_body(store, bad):
    from personalclaw.dashboard import handlers_inbox as h

    if bad == "not-json":
        req, _ = _api_request(store)
        from unittest.mock import AsyncMock

        req.json = AsyncMock(side_effect=ValueError("bad"))
    else:
        req, _ = _api_request(store, body=bad)
    assert (await h.api_inbox_seen(req)).status == 400
