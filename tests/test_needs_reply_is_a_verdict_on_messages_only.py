"""The triage verdict "needs reply" belongs to channel messages; nothing else is filed under it.

Measured on a live install: the Inbox's "Needs reply" listed the user's own captured note, three
"Update available for …" notices and a stopped loop's row. Every row is stored with the verdict
``needs_reply`` (high confidence) until something triages it, and only a channel message is ever
triaged. The app-update notices were worse off than the rest: raised with no row kind, they took
the notification's own kind (``update``), which no surface knows, and so read as messages
everywhere — reply machinery included.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from personalclaw.inbox import InboxItem, InboxStore, ItemKind


def _row(item_id: str, kind: str, **over) -> InboxItem:
    base = dict(
        id=f"{item_id}_x_1700000000.0",
        channel="agent",
        channel_name="agent",
        thread_ts=None,
        message=f"row {item_id}",
        sender_id="s",
        sender_name="s",
        item_kind=kind,
    )
    base.update(over)
    return InboxItem(**base)


def test_an_app_update_notice_is_a_system_notice():
    from personalclaw.apps import catalog

    state = MagicMock()
    catalog._emit_app_update(
        state,
        {
            "name": "weather",
            "displayName": "Weather",
            "latestVersion": "2.0.0",
            "installedVersion": "1.0.0",
        },
    )

    store = InboxStore()
    store.load()
    (row,) = [i for i in store.items.values() if "Update available for Weather" in i.message]
    assert row.item_kind == ItemKind.SYSTEM.value, "the notice took a kind no surface knows"
    assert row.can_reply is False


@pytest.mark.asyncio
async def test_the_kinds_census_offers_the_reply_machinery_only_to_channel_kinds(tmp_path):
    from personalclaw.dashboard import handlers_inbox as h

    store = InboxStore(path=tmp_path / "inbox_items.json")
    store.add(_row("m", ItemKind.MESSAGE.value))
    store.add(_row("n", ItemKind.USER_NOTE.value))
    # A row stored before its emitter named a kind: its kind is one this build does not know.
    store.add(_row("u", "update"))
    store.save()
    state = SimpleNamespace(
        _inbox_svc=None, _inbox_state=MagicMock(), _inbox_store=store, broadcast_ws=MagicMock()
    )
    req = MagicMock()
    req.app = {"state": state}
    req.query = {}

    rows = {r["kind"]: r for r in json.loads((await h.api_inbox_kinds(req)).body)["kinds"]}

    assert rows["message"]["channel"] is True
    assert rows["user_note"]["channel"] is False
    assert rows["update"]["channel"] is False, "a kind no source declared was offered the reply box"


@pytest.mark.parametrize(
    ("kind", "classified"),
    [
        (ItemKind.EMAIL.value, True),
        (ItemKind.USER_NOTE.value, False),
        (ItemKind.SYSTEM.value, False),
    ],
)
def test_investigate_hands_the_chat_a_verdict_only_for_a_message(kind, classified):
    from personalclaw.investigate import _resolve_inbox_item

    store = InboxStore()
    item = _row("i", kind, message="Buy stamps on the way home")
    store.add(item)
    state = SimpleNamespace(_inbox_svc=SimpleNamespace(inbox=store))

    snapshot = _resolve_inbox_item(item.id, state).snapshot

    assert ("Classification: needs_reply" in snapshot) is classified, snapshot
    if not classified:
        assert f"Kind: {kind}" in snapshot
