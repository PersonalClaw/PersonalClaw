"""A polled message's row is keyed on the message's own id, so two messages never share one.

The row id was ``{channel}_{timestamp}``. Every Mail Inbox row shares one channel (the receiving
address) and a mail's Date counts whole seconds, so the second of two mails sent in the same second
got the first one's id and was dropped as a duplicate: it never reached the Inbox. The id is now
``inbox_service.polled_item_id``: the source's own id for the message (a Message-ID, a Slack ts),
or the message's content when it has none, hashed, then the ``_{timestamp}`` every row id ends with.

Swept with it: the drop folder named a message with no id after its file (``{stem}_{n}``), so a file
dropped again under a used name named its messages as the first one's were; and muting the first
message of a thread parsed a key out of the row id, which named no thread once the id changed (and
named none before for a Slack ts ending in a zero, whose float spelling drops it).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personalclaw import inbox_service as mod
from personalclaw.inbox import InboxState, InboxStore
from personalclaw.inbox_providers.base import IncomingMessage
from personalclaw.inbox_service import InboxService

MAIL = SimpleNamespace(source_name="mail-inbox")
SLACK = SimpleNamespace(source_name="slack")
#: Two mails sent in the same second, to the same receiving address.
SECOND = 1790000000.0


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from personalclaw import notification_rules as nr

    monkeypatch.setattr(
        "personalclaw.providers.entity_routes.load_inbox_settings",
        lambda: {"auto_cleanup_enabled": True, "retention_days": 90},
    )
    rules_home = tmp_path / "rules-home"
    (rules_home / "entity_settings").mkdir(parents=True)
    monkeypatch.setattr(nr, "config_dir", lambda: rules_home)
    monkeypatch.setattr(mod, "operator_name", lambda: "")
    monkeypatch.setattr(mod, "_dashboard_state", lambda: None)
    return InboxService(
        state=InboxState(tmp_path / "state.json"), store=InboxStore(tmp_path / "inbox.json")
    )


def _mail(message_id: str, text: str, **kw) -> IncomingMessage:
    base = {
        "id": message_id,
        "channel_id": "me@example.test",
        "channel_name": "me@example.test",
        "text": text,
        "sender_id": "someone@example.test",
        "sender_name": "Someone",
        "timestamp": SECOND,
        "kind": "email",
    }
    base.update(kw)
    return IncomingMessage(**base)


def test_two_mails_sent_in_the_same_second_are_two_rows(svc):
    first = _mail("<a1@example.test>", "Subject: Invoice\n\nAttached.")
    second = _mail("<a2@example.test>", "Subject: Re: Invoice\n\nWrong one, sorry.")

    assert svc._ingest([first, second], source=MAIL) == 2
    assert sorted(i.message for i in svc.inbox.items.values()) == sorted([first.text, second.text])


def test_the_same_message_polled_again_is_still_one_row(svc):
    mail = _mail("<a1@example.test>", "hello")
    assert svc._ingest([mail], source=MAIL) == 1
    assert svc._ingest([_mail("<a1@example.test>", "hello")], source=MAIL) == 0


def test_a_message_with_no_id_is_keyed_by_its_content(svc):
    one, two = _mail("", "first"), _mail("", "second")
    assert svc._ingest([one, two], source=MAIL) == 2
    assert svc._ingest([_mail("", "first")], source=MAIL) == 0, "the same content is the same one"


def test_the_same_id_from_two_sources_or_channels_is_two_rows(svc):
    assert svc._ingest([_mail("m1", "x")], source=MAIL) == 1
    assert svc._ingest([_mail("m1", "x")], source=SLACK) == 1
    assert svc._ingest([_mail("m1", "x", channel_id="other@example.test")], source=MAIL) == 1


def test_a_row_id_is_one_path_segment_and_ends_with_its_time(svc):
    """Item routes take the id as a path segment, and a Message-ID may hold a ``/``."""
    mail = _mail("<CAx+7/9=q@mail.example.test>", "x")
    svc._ingest([mail], source=MAIL)
    (item,) = svc.inbox.items.values()
    assert "/" not in item.id
    assert item.ts == str(SECOND)
    assert item.id == mod.polled_item_id("mail-inbox", mail)


# ── the drop folder ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_drop_file_reused_by_name_keeps_both_messages(tmp_path, monkeypatch, svc):
    """Two drops of ``inbox.json``, each with one id-less message, read in two polls."""
    from personalclaw.config import loader
    from personalclaw.inbox_providers.filesystem_source import FilesystemSourceProvider

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    incoming = tmp_path / "inbox" / "incoming"
    incoming.mkdir(parents=True)
    source = FilesystemSourceProvider()
    for text in ("the build is red", "the build is green"):
        (incoming / "inbox.json").write_text(
            json.dumps({"messages": [{"text": text, "timestamp": SECOND}]}), encoding="utf-8"
        )
        messages, _ = await source.poll([], {}, "")
        svc._ingest(messages, source=source)

    assert sorted(i.message for i in svc.inbox.items.values()) == [
        "the build is green",
        "the build is red",
    ]


# ── muting the first message of a thread ─────────────────────────────────────────────────


def test_muting_a_first_message_mutes_the_replies_to_it(svc):
    """A Slack reply names its thread by the parent's ts, spelled as Slack spells it."""
    parent = IncomingMessage(
        id="1790000000.000100",
        channel_id="C1",
        channel_name="#ops",
        text="deploy?",
        sender_id="U1",
        timestamp=float("1790000000.000100"),
    )
    svc._ingest([parent], source=SLACK)
    (row,) = svc.inbox.items.values()
    assert row.thread_key == "1790000000.000100"

    svc.state.muted_threads.add(row.thread_key)  # what PUT /api/inbox/{id} mute_thread writes
    reply = IncomingMessage(
        id="1790000050.000200",
        channel_id="C1",
        channel_name="#ops",
        thread_id="1790000000.000100",
        text="yes, now",
        sender_id="U2",
        timestamp=1790000050.0002,
    )
    assert svc._ingest([reply], source=SLACK) == 0, "the muted thread's reply still arrived"


@pytest.mark.asyncio
async def test_the_inbox_api_and_the_inbox_op_mute_the_same_thread(tmp_path, monkeypatch):
    """Both writers of a mute read ``InboxItem.thread_key``, so an unmute finds either's."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.action_providers import inbox_op_provider as op
    from personalclaw.dashboard import handlers_inbox
    from personalclaw.inbox import InboxItem

    store = InboxStore(tmp_path / "inbox.json")
    inbox_state = InboxState(tmp_path / "state.json")
    for n in (1, 2):
        store.add(
            InboxItem(
                id=f"slack_{n:016x}_1790000000.0001",
                channel="C1",
                channel_name="#ops",
                thread_ts=None,
                message=f"m{n}",
                sender_id="U1",
                sender_name="U1",
                source="slack",
                can_reply=True,
                reply_target=f"179000000{n}.000100",
            )
        )
    state = SimpleNamespace(broadcast_ws=lambda *a, **k: None)
    monkeypatch.setattr(handlers_inbox, "_get_inbox", lambda _state: (inbox_state, store))

    app = web.Application()
    app["state"] = state
    app.router.add_put("/api/inbox/{id}", handlers_inbox.api_inbox_update)
    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/api/inbox/slack_0000000000000001_1790000000.0001", json={"mute_thread": True}
        )
        assert resp.status == 200, await resp.text()
    assert "1790000001.000100" in inbox_state.muted_threads

    monkeypatch.setattr(op, "live_store", lambda _state: store)
    monkeypatch.setattr(op, "live_state", lambda _state: inbox_state)
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(state=state),
    )
    from personalclaw.action_providers.base import ActionContext

    result = await op.InboxOpActionProvider().execute(
        {"op": "mute_thread", "item_id": "slack_0000000000000002_1790000000.0001"},
        ActionContext(event="test"),
    )
    assert result.success, result.error
    assert "1790000002.000100" in inbox_state.muted_threads
