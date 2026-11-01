"""An Inbox reply goes out once: a row already answered is not answered again until reopened.

The panel hides Send on a settled row, but only in the tab that saw it settle. Another tab, or the
phone, still showing the draft could press Send again, and the route sent it again: the sender got
the reply twice, and a native row started a second turn in the posting chat. ``POST
/api/inbox/send`` now refuses a row whose ``replied_at`` is set and that has not been reopened,
with ``409 reply_already_sent``. Reopening the row (moving it back to an open status) lets a
follow-up go.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers_inbox import api_inbox_send
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus
from personalclaw.inbox_service import InboxService


@pytest.fixture(autouse=True)
def _isolate_inbox_files(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)


@pytest.fixture(autouse=True)
def _clean_sources():
    from personalclaw.inbox_providers import registry as app_sources

    app_sources._sources.clear()
    yield
    app_sources._sources.clear()


def _row(**kw) -> InboxItem:
    base = dict(
        id="mail_1790000000.1",
        channel="mail",
        channel_name="Mail",
        thread_ts=None,
        message="Can you confirm Thursday at ten?",
        sender_id="sam@example.org",
        sender_name="Sam",
        created_at=time.time(),
        source="mail",
        can_reply=True,
    )
    base.update(kw)
    return InboxItem(**base)


def _gateway(item: InboxItem, sent: list[str]) -> MagicMock:
    """The dashboard state over one Inbox row, its source a fake that records each reply."""
    from personalclaw.inbox_providers.base import MessageSourceProvider
    from personalclaw.inbox_providers.registry import register_source

    class _Mail(MessageSourceProvider):
        display_name = "Mail"

        @property
        def source_name(self) -> str:
            return "mail"

        async def poll(self, watched_channels, checkpoints, user_id):
            return [], {}

        async def send_reply(self, channel_id, text, thread_ts=None):
            sent.append(text)
            return True

        async def add_reaction(self, channel_id, ts, emoji):
            return False

        async def get_channel_history(self, channel_id, oldest, limit=200):
            return []

        async def resolve_user_name(self, user_id):
            return user_id

    register_source(_Mail())
    store = InboxStore()
    store.items[item.id] = item
    svc = InboxService(state=InboxState(), store=store, user_name="Noor")
    state = MagicMock()
    state._inbox_svc = svc
    state._inbox_store = svc.inbox
    state._inbox_state = svc.state
    state.broadcast_ws = lambda ev, payload: None
    return state


async def _send(state: MagicMock, item_id: str, text: str) -> web.Response:
    app = web.Application()
    app["state"] = state
    request = make_mocked_request("POST", "/api/inbox/send", app=app)

    async def body():
        return {"id": item_id, "text": text}

    request.json = body
    return await api_inbox_send(request)


@pytest.mark.asyncio
async def test_a_second_send_to_an_answered_row_is_refused_and_not_sent():
    """🔴 Red before: the second Send reached the source, and Sam got the reply twice."""
    import json

    sent: list[str] = []
    item = _row()
    state = _gateway(item, sent)

    first = await _send(state, item.id, "Thursday at ten works.")
    assert first.status == 200, first.text
    again = await _send(state, item.id, "Thursday at ten works.")

    assert again.status == 409
    error = json.loads(again.text)["error"]
    assert error["code"] == "reply_already_sent"
    assert error["replied_at"] == item.replied_at > 0
    assert sent == ["Thursday at ten works."]


@pytest.mark.asyncio
async def test_a_native_row_answered_twice_starts_one_turn():
    """A native row's reply starts a turn in the posting chat. 🔴 Red before: a second Send from
    another tab started a second turn with the same words."""
    item = _row(id="native_1790000000.2", source="native", reply_target="dashboard:chat-7")
    state = _gateway(item, [])
    session = MagicMock()
    state.get_session = MagicMock(return_value=session)

    assert (await _send(state, item.id, "Use the staging bucket.")).status == 200
    assert (await _send(state, item.id, "Use the staging bucket.")).status == 409

    assert session.enqueue_or_run_prompt.call_count == 1


@pytest.mark.asyncio
async def test_a_reopened_row_takes_a_follow_up():
    """Reopening the row is how a follow-up goes: the guard reads an answered row that settled."""
    sent: list[str] = []
    item = _row()
    state = _gateway(item, sent)
    assert (await _send(state, item.id, "Thursday at ten works.")).status == 200

    item.status = ItemStatus.PENDING.value
    follow_up = await _send(state, item.id, "Sorry, make that eleven.")

    assert follow_up.status == 200, follow_up.text
    assert sent == ["Thursday at ten works.", "Sorry, make that eleven."]
