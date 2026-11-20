"""An automation's notices into a busy chat make room only among themselves.

An automation, or a scheduled job, that sends its result to the chat it came from (``send_message``
with ``session: "origin"``) while that chat runs a turn queues it there as a notice. The queue used
to hold 50 entries of any kind and make room by dropping the one at its head, whatever it was: a
message you typed while the turn ran, the turn queued to run again, a helper's report. Only a log
line said so. Now a queued notice is marked as one (``chat_queue.NOTICE``), and
``chat_utils.room_for_a_notice`` drops only the oldest notice once ``NOTICE_CAP`` of them wait,
telling the page and saying in the chat which automation's notice went. When ``QUEUE_BOUND``
messages of any kind wait, a notice is refused instead and goes out as a notification: nothing
waiting is dropped for it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from test_dashboard_file_io import _seed_cron_trigger

from personalclaw.dashboard.chat_queue import NOTICE, NOTICE_CAP, QUEUE_BOUND
from personalclaw.dashboard.chat_utils import _dequeue_next_message
from personalclaw.dashboard.handlers.messaging import api_send_message
from personalclaw.dashboard.state import CRON_NOTIFY_PREFIX, SUBAGENT_COMPLETION_PREFIX

CHAT = "chat-notices-1"
AUTOMATION = "check pipeline"


class _BusyChat:
    """One chat whose turn is running, on a state whose every frame and notification is kept,
    with an automation that delivers to it."""

    def __init__(self, tmp_path) -> None:
        self.frames: list[tuple[str, dict]] = []
        self.state = _make_state(tmp_path)
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        self.state.push_sessions_update = MagicMock()
        self.state.notify = MagicMock()
        self.session = self.state.get_or_create_session(CHAT)
        self.session.task = asyncio.get_running_loop().create_future()  # its turn runs

    def kinds(self) -> list[str]:
        """What waits in the queue, head first: ``notice`` or the item's words."""
        return ["notice" if item.get(NOTICE) else item["content"] for item in self.session._queue]

    def notes(self) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == "system"]

    def close(self) -> None:
        self.session.task.cancel()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


@pytest_asyncio.fixture
async def chat(tmp_path, monkeypatch) -> AsyncIterator[tuple[_BusyChat, TestClient]]:
    busy = _BusyChat(tmp_path)
    _seed_cron_trigger(tmp_path, monkeypatch, name=AUTOMATION, session_key=f"dashboard:{CHAT}")
    app = web.Application()
    app.router.add_post("/api/send-message", api_send_message)
    app["state"] = busy.state
    with patch("personalclaw.dashboard.handlers.sel", MagicMock()):
        async with TestClient(TestServer(app)) as http:
            yield busy, http
    busy.close()


async def _notices(http: TestClient, n: int) -> list[dict]:
    """*n* notices from the automation to the chat it came from, each one's answer."""
    answers = []
    for i in range(n):
        sent = await http.post(
            "/api/send-message",
            json={
                "text": f"build {i} failed",
                "session": "origin",
                "caller_session": "cron:abc12345",
            },
        )
        assert sent.status == 200
        answers.append(await sent.json())
    return answers


@pytest.mark.asyncio
async def test_a_notice_at_the_cap_keeps_a_message_you_typed(chat):
    """🔴 Red before: the 50th entry made room by dropping the head of the queue, her message."""
    busy, http = chat
    busy.session.queue_append("and check the deploy too", own_words="and check the deploy too")
    await _notices(http, NOTICE_CAP - 1)
    assert busy.kinds() == ["and check the deploy too"] + ["notice"] * (NOTICE_CAP - 1)

    await _notices(http, 1)

    assert busy.kinds() == ["and check the deploy too"] + ["notice"] * NOTICE_CAP
    assert busy.notes() == [], "nothing was dropped, so the chat says nothing"


@pytest.mark.asyncio
@pytest.mark.parametrize("head", ["retry", "report"])
async def test_a_notice_at_the_cap_keeps_a_retry_and_a_helpers_report(chat, head):
    """🔴 Red before: a retry of the turn (queued first) and a helper's report went the same way."""
    busy, http = chat
    if head == "retry":
        busy.session.queue_retry("run the tests again")
        words = "run the tests again"
    else:
        words = f"{SUBAGENT_COMPLETION_PREFIX}researcher] found 3 sources"
        busy.session.queue_append(words, asked_for_by={"kind": "agent", "name": "researcher"})
    await _notices(http, NOTICE_CAP)
    assert busy.kinds() == [words] + ["notice"] * NOTICE_CAP


@pytest.mark.asyncio
async def test_a_notice_past_the_cap_drops_the_oldest_notice_and_says_so(chat):
    """🔴 Red before: with notices alone queued it dropped the oldest silently: no frame told the
    page, and the chat said nothing."""
    busy, http = chat
    busy.session.queue_append("and check the deploy too")
    await _notices(http, NOTICE_CAP)
    oldest = busy.session._queue[1]
    assert "build 0 failed" in oldest["content"]
    placeholders = [m for m in busy.session.messages if m.get("role") == "queued"]
    assert len(placeholders) == NOTICE_CAP

    (answer,) = await _notices(http, 1)

    assert answer["session"] is True, "the new notice is in the chat"
    assert busy.kinds() == ["and check the deploy too"] + ["notice"] * NOTICE_CAP
    assert oldest["id"] not in [item["id"] for item in busy.session._queue]
    assert "build 1 failed" in busy.session._queue[1]["content"]
    # Its placeholder row went with it, and the page was told.
    placeholders = [m for m in busy.session.messages if m.get("role") == "queued"]
    assert len(placeholders) == NOTICE_CAP
    assert not any(f'"queue_id": "{oldest["id"]}"' in m["cls"] for m in placeholders)
    popped = [d for kind, d in busy.frames if kind == "queue_pop"]
    assert [d["queue_id"] for d in popped] == [oldest["id"]]
    assert popped[0]["session"] == busy.session.key
    # And the chat says, once, which automation's notice it was.
    assert busy.notes() == [
        f'This chat was busy, so the oldest notice waiting in it, from "{AUTOMATION}", was'
        " dropped to make room."
    ]


@pytest.mark.asyncio
async def test_at_the_queue_bound_a_notice_is_refused_and_nothing_is_dropped(chat):
    """At the total bound the notice goes out as a notification, and every queued message stays."""
    busy, http = chat
    for i in range(QUEUE_BOUND):
        busy.session.queue_append(f"message {i}")
    before = [item["id"] for item in busy.session._queue]

    (answer,) = await _notices(http, 1)

    assert answer["session"] is False, "the chat took nothing in"
    assert [item["id"] for item in busy.session._queue] == before
    assert [kind for kind, _ in busy.frames if kind == "queue_pop"] == []
    (call,) = busy.state.notify.call_args_list
    _kind, title, text = call.args
    assert title == f"Trigger: {AUTOMATION}"
    assert "build 0 failed" in text and "too busy to take it" in text


def test_a_message_typed_to_look_like_a_notice_is_still_hers(tmp_path):
    """🔴 Red before: the queue told notices apart by their words, so her message starting with the
    notice's wrapper was held apart from the messages it would merge with."""
    session = _make_state(tmp_path).get_or_create_session("chat-look-alike")
    look_alike = f'{CRON_NOTIFY_PREFIX}"Morning check"]\nnot really, it is me'
    session.queue_append(look_alike)
    session.queue_append("and one more thing")

    text, consumed = _dequeue_next_message(session, merge_enabled=True)

    assert len(consumed) == 2, "her two messages run as one turn, as any two of hers do"
    assert look_alike in text and "and one more thing" in text
    assert not any(item.get(NOTICE) for item in consumed)


def test_a_real_notice_still_runs_on_its_own(tmp_path):
    session = _make_state(tmp_path).get_or_create_session("chat-notice-alone")
    session.queue_append(f'{CRON_NOTIFY_PREFIX}"{AUTOMATION}"]\nbuild failed', notice=AUTOMATION)
    session.queue_append("and one more thing")

    text, consumed = _dequeue_next_message(session, merge_enabled=True)

    assert [item.get(NOTICE) for item in consumed] == [AUTOMATION]
    assert "build failed" in text and "one more thing" not in text
