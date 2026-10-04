"""An email's attachment is an attachment: listed, kept, offered for download, and read as data.

A mail source used to turn an attached PDF into text and append it to the body, so the Inbox
showed the attachment's bytes as the message (a heading of the quote's text, then font data) and
listed no attachment at all; nothing could download it. A message now carries its files beside
its words (``IncomingMessage.files``, ``ChannelMessage.files``), core keeps them with the row they
belong to (``attachments.keep``), the row lists each one with its name, type and size, the Inbox
serves each as a file to save, and an agent reading the message is told of each one and given its
text inside a fence.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app
from test_doc_parser import _pdf

from personalclaw import attachments
from personalclaw.attachments import Attachment
from personalclaw.dashboard import handlers_inbox as h
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.inbox import InboxItem, InboxState, InboxStore, redact_item
from personalclaw.inbox_providers.base import IncomingMessage
from personalclaw.inbox_service import InboxService, fence_message_for_prompt

QUOTE = _pdf("Subtotal 31080 plus HST", other_stream=b"(\x01 glyph run) (kern table)")


def _mail(**kw) -> IncomingMessage:
    return IncomingMessage(
        id="<revised@build.example.com>",
        channel_id="owner@example.com",
        channel_name="owner@example.com",
        text="Subject: Kitchen estimate\n\nThe revised quote is attached.",
        sender_id="builder@build.example.com",
        sender_name="Dana Builder",
        timestamp=1790726807.0,
        kind="email",
        **kw,
    )


def _service(tmp_path) -> InboxService:
    return InboxService(
        state=InboxState(tmp_path / "inbox_state.json"), store=InboxStore(tmp_path / "inbox.json")
    )


def _ingested(tmp_path, message: IncomingMessage) -> InboxItem:
    service = _service(tmp_path)
    assert service._ingest([message], source=SimpleNamespace(source_name="mail")) == 1
    [item] = service.inbox.items.values()
    return item


# ── what a message's row lists, and what is kept ─────────────────────────────────────────────


def test_a_mails_attachment_is_listed_and_kept_and_its_bytes_stay_out_of_the_message(tmp_path):
    item = _ingested(
        tmp_path, _mail(files=[Attachment("revised-quote.pdf", "application/pdf", QUOTE)])
    )

    assert item.message == "Subject: Kitchen estimate\n\nThe revised quote is attached."
    [record] = item.attachments
    assert record["name"] == "revised-quote.pdf"
    assert record["mimetype"] == "application/pdf"
    assert record["size"] == len(QUOTE)
    kept = attachments.path_of(item.id, record)
    assert kept is not None and kept.read_bytes() == QUOTE
    assert oct(kept.stat().st_mode & 0o777) == "0o600"


def test_a_file_too_large_or_past_the_count_is_listed_with_why(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_BYTES", 10)
    monkeypatch.setattr(attachments, "MAX_COUNT", 2)
    records = attachments.keep(
        "mail_owner",
        [
            Attachment("small.txt", "text/plain", b"hello"),
            Attachment("big.bin", "application/octet-stream", b"x" * 11),
            Attachment("third.txt", "text/plain", b"hi"),
        ],
    )
    assert [bool(r.get("file")) for r in records] == [True, False, False]
    assert "larger than" in records[1]["not_kept"]
    assert "at most 2" in records[2]["not_kept"]
    assert attachments.path_of("mail_owner", records[1]) is None


def test_a_name_the_sender_wrote_never_names_where_the_file_is_kept(tmp_path):
    [record] = attachments.keep(
        "mail_owner", [Attachment("../../../etc/passwd\n.pdf", "application/pdf", b"%PDF-1.4")]
    )
    assert (
        record["name"] == "../../../etc/passwd.pdf"
    ), "the name is shown as written, less controls"
    path = attachments.path_of("mail_owner", record)
    assert path is not None and path.parent == (attachments.attachments_root() / "mail_owner")
    assert "/" not in record["file"] and ".." not in record["file"].split(".")[0]
    # A record naming a file outside its folder names nothing.
    assert attachments.path_of("mail_owner", {**record, "file": "../mail_other/x"}) is None
    with pytest.raises(ValueError):
        attachments.keep("../escape", [Attachment("a.txt", "text/plain", b"a")])


def test_a_row_as_a_reader_is_served_lists_what_was_kept_and_never_where(tmp_path):
    item = _ingested(tmp_path, _mail(files=[Attachment("quote.pdf", "application/pdf", QUOTE)]))
    [served] = redact_item(item.to_dict())["attachments"]
    assert served == {
        "id": "1",
        "name": "quote.pdf",
        "mimetype": "application/pdf",
        "size": len(QUOTE),
        "kept": True,
    }


def test_a_message_with_no_files_keeps_nothing(tmp_path):
    item = _ingested(tmp_path, _mail())
    assert item.attachments == []
    assert not (attachments.attachments_root() / item.id).exists()


def test_the_rows_attachments_go_when_retention_removes_the_row(tmp_path):
    service = _service(tmp_path)
    service._ingest(
        [_mail(files=[Attachment("quote.pdf", "application/pdf", QUOTE)])],
        source=SimpleNamespace(source_name="mail"),
    )
    [item] = service.inbox.items.values()
    folder = attachments.attachments_root() / item.id
    assert folder.is_dir()
    item.created_at = 1.0
    assert service.inbox.cleanup_by_retention(retention_days=1) == 1
    assert not folder.exists()


# ── the download ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def served(tmp_path):
    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    store = InboxStore(tmp_path / "inbox.json")
    state._inbox_svc = SimpleNamespace(inbox=store, state=InboxState(tmp_path / "inbox_state.json"))
    item = InboxItem(
        id="mail_abc_1790726807.0",
        channel="owner@example.com",
        channel_name="owner@example.com",
        thread_ts=None,
        message="The revised quote is attached.",
        sender_id="builder@build.example.com",
        sender_name="Dana Builder",
        source="mail",
    )
    item.attachments = attachments.keep(
        item.id,
        [
            Attachment("Quote (revised).pdf", "text/html", QUOTE),
            Attachment("huge.bin", "application/octet-stream", b"x"),
        ],
    )
    item.attachments[1].pop("file")
    item.attachments[1]["not_kept"] = "it is larger than 25.0 MB, the most an attachment may be"
    store.add(item)
    app = _api_app(state)
    app.router.add_get("/api/inbox/{id}/attachments/{aid}", h.api_inbox_attachment)
    return SimpleNamespace(app=app, item=item)


@pytest.mark.asyncio
async def test_an_attachment_downloads_as_a_file_to_save_whatever_the_mail_said_it_is(served):
    async with TestClient(TestServer(served.app)) as http:
        resp = await http.get(f"/api/inbox/{served.item.id}/attachments/1")
        assert resp.status == 200
        assert await resp.read() == QUOTE
        # The mail called it text/html: a type the dashboard's origin must never render.
        assert resp.headers["Content-Type"] == "application/octet-stream"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Cache-Control"] == "no-store"
        assert "sandbox" in resp.headers["Content-Security-Policy"]
        disposition = resp.headers["Content-Disposition"]
        assert disposition.startswith("attachment;")
        assert "Quote" in disposition and "revised" in disposition


@pytest.mark.asyncio
async def test_an_attachment_that_was_not_kept_says_why_and_an_unknown_one_is_not_found(served):
    async with TestClient(TestServer(served.app)) as http:
        not_kept = await http.get(f"/api/inbox/{served.item.id}/attachments/2")
        assert not_kept.status == 404
        err = (await not_kept.json())["error"]
        assert err["code"] == "inbox_attachment_not_kept"
        assert "larger than 25.0 MB" in err["message"]
        unknown = await http.get(f"/api/inbox/{served.item.id}/attachments/9")
        assert (await unknown.json())["error"]["code"] == "inbox_attachment_not_found"
        no_row = await http.get("/api/inbox/mail_nothing_1.0/attachments/1")
        assert no_row.status == 404


# ── what an agent reading the message is told ────────────────────────────────────────────────


def test_a_draft_is_told_what_the_message_came_with_inside_its_fence(tmp_path):
    item = _ingested(tmp_path, _mail(files=[Attachment("quote.pdf", "application/pdf", QUOTE)]))
    fenced = fence_message_for_prompt(item, owner="")
    body = fenced.split(">", 1)[1].rsplit("</untrusted_content>", 1)[0]
    assert "Attached:" in body and "quote.pdf (application/pdf" in body


def test_an_agent_reads_an_attachments_text_fenced_with_its_name(tmp_path):
    item = _ingested(
        tmp_path,
        _mail(
            files=[
                Attachment("quote.pdf", "application/pdf", QUOTE),
                Attachment("photo.png", "image/png", b"\x89PNG\r\n\x1a\n"),
            ]
        ),
    )
    said = asyncio.run(attachments.reading(item.id, item.attachments, source="inbox")).text

    first, second = said.split("\n\nAttachment 2")
    fence = first.split("\n", 1)[1]
    assert fence.startswith("<untrusted_content") and fence.endswith("</untrusted_content>")
    assert "name: quote.pdf" in fence and "Subtotal 31080 plus HST" in fence
    assert "glyph" not in said, "a PDF's font streams are not its text"
    assert "an image, which is not read here" in second
    assert "name: photo.png" in second


def test_investigate_hands_the_chat_the_attachments_text(tmp_path):
    from personalclaw.investigate import _resolve_inbox_item

    service = _service(tmp_path)
    service._ingest(
        [_mail(files=[Attachment("quote.pdf", "application/pdf", QUOTE)])],
        source=SimpleNamespace(source_name="mail"),
    )
    [item] = service.inbox.items.values()
    ctx = asyncio.run(_resolve_inbox_item(item.id, SimpleNamespace(_inbox_svc=service)))
    assert "Attachments:" in ctx.snapshot and "quote.pdf" in ctx.snapshot
    assert "Subtotal 31080 plus HST" in ctx.snapshot


# ── the channel paths ─────────────────────────────────────────────────────────────────────────


def test_someone_new_is_held_with_what_their_message_came_with(tmp_path):
    from personalclaw.inbox_providers.native_source import hold_from_someone_new

    store = InboxStore(tmp_path / "inbox.json")
    state = SimpleNamespace(
        _inbox_svc=SimpleNamespace(inbox=store, state=InboxState(tmp_path / "s.json")),
        broadcast_ws=lambda *a, **k: None,
    )
    row = hold_from_someone_new(
        state,
        provider="email-channel",
        channel_name="Email",
        channel_id="builder@build.example.com",
        sender_id="builder@build.example.com",
        text="The revised quote is attached.",
        message_id="<m1@build.example.com>",
        ts=1790726807.0,
        files=[Attachment("quote.pdf", "application/pdf", QUOTE)],
    )
    assert row is not None and row.message == "The revised quote is attached."
    [record] = row.attachments
    assert attachments.path_of(row.id, record).read_bytes() == QUOTE


def test_a_channel_message_becomes_a_turn_carrying_its_files(tmp_path):
    from personalclaw.channel_inbound import _route_to_session
    from personalclaw.channel_transports.base import ChannelMessage
    from personalclaw.testing.channel_conformance import CapturedSession

    session = CapturedSession("dashboard:mail")
    appended: list[dict] = []
    session.append = lambda role, content, cls="", ts="", **kw: appended.append(  # type: ignore
        {"role": role, "content": content, **kw}
    )
    state = SimpleNamespace(
        get_linked_session=lambda key: session,
        broadcast_ws=lambda *a, **k: None,
        push_sessions_update=lambda: None,
        _background_tasks=set(),
    )

    async def _turn(*_a):
        return None

    msg = ChannelMessage(
        channel_id="builder@build.example.com",
        text="The revised quote is attached.",
        files=[Attachment("quote.pdf", "application/pdf", QUOTE)],
    )
    files = list(msg.files)

    async def _go():
        await _route_to_session(
            SimpleNamespace(dashboard_state=state),
            "email-channel",
            msg,
            msg.text,
            _turn,
            files=files,
        )

    asyncio.run(_go())
    [turn] = appended
    [path] = turn["meta"]["files"]
    assert os.path.dirname(path).endswith("uploads") and path.endswith("_quote.pdf")
    with open(path, "rb") as fh:
        assert fh.read() == QUOTE


def test_a_queued_channel_message_keeps_its_files_for_its_turn():
    from personalclaw.dashboard.state import _ChatSession

    session = _ChatSession("dashboard:mail")
    session.queue_append("see attached", channel="email-channel", files=["/tmp/u/x_quote.pdf"])
    assert session.queue_pop(0)["files"] == ["/tmp/u/x_quote.pdf"]
