"""A scheduled result reaches the bell, the notification record and its chat channel in its words.

A trigger whose action ran a command reported "feedsmith digest finished" and nothing more: to the
bell, to the notification record and to the chat its owner named as the Notify channel. What the
command printed ("23 feeds: 3 new entries …") reached the trigger's history row and nowhere else,
so choosing a chat channel to read the result there gave a title with nothing under it. The same
fire path said "… failed" with no reason when a command exited non-zero, said "… finished" for a
run that did nothing, and cut an agent's reply to 512 characters before any surface saw it.

Now each surface gets the result, sized for it: the notification holds what the bell and the
notifications page show (the bell reads its first line), the chat channel gets room for more, and
a result too long for either is cut where a line ends, with a last line saying where the rest is.
Only the model-free parts are fakes: the action's result, and the chat channel's transport.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

import personalclaw.action_providers as AP
from personalclaw import channel_delivery, channel_transports
from personalclaw.action_providers.base import ActionResult
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import delivery as D
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

TRIGGER_ID = "clock:feed-digest"
CHAT = "numchat"
#: An autonomous action on the rung ladder, so the fire is dispatched rather than held. The result
#: is the fake's; the delivery under test does not depend on which provider produced it.
_ACTION = "create-task"

DIGEST_OUTPUT = (
    "23 feeds: 3 new entries, 14 unchanged, 1 failed\n"
    "wrote 586 entries to /home/noor/Documents/digest.html"
)


class _State:
    """`DashboardState.notify`'s shape, recording what reached the bell and the record."""

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def notify(self, kind, title, body, *, meta=None, raised_by_app=""):
        self.notes.append({"kind": kind, "title": title, "body": body, "meta": dict(meta or {})})


class _Result:
    """The action, returning whatever the test hands it."""

    def __init__(self, result: ActionResult) -> None:
        self.result = result

    async def execute(self, config, ctx, timeout=30):
        return self.result


class _Chat(ChannelTransportProvider):
    name = property(lambda self: CHAT)
    display_name = property(lambda self: "NumChat")

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True


class _Handle:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw: Any) -> str:
        self.sent.append(text)
        return "m-1"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    key = owner_id_credential(CHAT)
    monkeypatch.delenv(key, raising=False)
    yield tmp_path
    os.environ.pop(key, None)
    channel_transports.unregister_transport(CHAT)
    channel_delivery.register(None, provider=CHAT)


@pytest.fixture()
def chat(home) -> _Handle:
    handle = _Handle()
    channel_transports.register_transport(_Chat())
    channel_delivery.register(handle, provider=CHAT)
    save_credential(owner_id_credential(CHAT), "4242")
    return handle


def _store(home, *, delivery: str = "inbox") -> None:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=TRIGGER_ID,
            name="feed digest",
            kind="clock",
            enabled=True,
            spec={"kind": "cron", "expr": "45 6 * * *"},
            delivery=delivery,
            failure_delivery="",
            capabilities={"providers": [_ACTION]},
            workflow={"inline": {"provider": _ACTION, "config": {}}},
        )
    )


def _orchestrator(state: _State) -> GatewayOrchestrator:
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    return orch


async def _fire(home, monkeypatch, result: ActionResult, *, delivery: str = "inbox") -> _State:
    """Fire the stored trigger through the dispatch every scheduled fire takes."""
    _store(home, delivery=delivery)
    monkeypatch.setattr(AP, "get_action_provider", lambda name: _Result(result))
    state = _State()
    trigger = TriggerStore(base_dir=home).get(TRIGGER_ID).trigger
    await _orchestrator(state)._fire_store_trigger(trigger, {"trigger_id": TRIGGER_ID})
    return state


async def _settled(state: _State, count: int = 1) -> list[dict[str, Any]]:
    """The notes, once a channel send they wait on has finished."""
    for _ in range(200):
        if len(state.notes) >= count:
            return state.notes
        await asyncio.sleep(0.01)
    return state.notes


# ── the result, on each surface ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_bell_and_the_record_carry_what_the_command_printed(home, monkeypatch):
    """🔴 Before: the note was `feed digest finished` with body ''."""
    state = await _fire(home, monkeypatch, ActionResult(success=True, stdout=DIGEST_OUTPUT))
    [note] = state.notes
    assert note["title"] == "feed digest finished"
    assert note["body"] == DIGEST_OUTPUT
    # The bell shows the body's first line: the command's own summary line.
    assert note["body"].splitlines()[0] == "23 feeds: 3 new entries, 14 unchanged, 1 failed"


@pytest.mark.asyncio
async def test_the_chat_channel_is_sent_the_result(home, monkeypatch, chat):
    """🔴 Before: the chat got `feed digest finished`, nothing else."""
    state = await _fire(
        home,
        monkeypatch,
        ActionResult(success=True, stdout=DIGEST_OUTPUT),
        delivery=f"channel:{CHAT}",
    )
    [note] = await _settled(state)
    assert chat.sent == [f"feed digest finished\n{DIGEST_OUTPUT}"]
    assert note["body"] == DIGEST_OUTPUT
    assert note["meta"][D.SENT_TO_CHANNEL_KEY] == CHAT


@pytest.mark.asyncio
async def test_the_sentence_an_action_wrote_is_what_it_says(home, monkeypatch):
    """An action that wrote a sentence for a person is read by it, as its history row is."""
    result = ActionResult(
        success=True,
        stdout='{"steps": 3, "site": "example.com"}',
        summary="Browse finished in 3 steps at example.com.",
    )
    [note] = (await _fire(home, monkeypatch, result)).notes
    assert note["body"] == "Browse finished in 3 steps at example.com."


@pytest.mark.asyncio
async def test_a_failed_command_says_why(home, monkeypatch):
    """🔴 Before: `feed digest failed` with body '', and a history row reading only "the action
    reported failure". The command's own error output says what went wrong."""
    result = ActionResult(
        success=False,
        exit_code=2,
        stdout="fetching 23 feeds",
        stderr="loading feeds\nfeedsmith: error: feeds.opml not found",
    )
    [note] = (await _fire(home, monkeypatch, result)).notes
    assert note["title"] == "feed digest failed"
    assert (
        note["body"] == "exited with code 2: loading feeds\nfeedsmith: error: feeds.opml not found"
    )
    runs, _total = await ScheduleRunStore(home).list_for_job(TRIGGER_ID, 0, 5)
    assert runs[0]["error"] == note["body"]


@pytest.mark.asyncio
async def test_a_failure_with_nothing_said_names_its_exit_code(home, monkeypatch):
    [note] = (await _fire(home, monkeypatch, ActionResult(success=False, exit_code=7))).notes
    assert note["body"] == "exited with code 7"


@pytest.mark.asyncio
async def test_a_run_that_had_nothing_to_do_sends_no_note(home, monkeypatch):
    """🔴 Before: "feed digest finished" for a run whose action reported it did nothing. A `skip`
    succeeds silently (`ActionResult.outcome`); its history row says why."""
    result = ActionResult(success=True, stdout="nothing new since the last run", outcome="skip")
    state = await _fire(home, monkeypatch, result)
    assert state.notes == []
    runs, _total = await ScheduleRunStore(home).list_for_job(TRIGGER_ID, 0, 5)
    assert runs[0]["status"] == "skipped_noop"


# ── sized for each surface ───────────────────────────────────────────────────────────────────


def _long_output(lines: int = 400) -> str:
    return "\n".join(f"line {i:03d}: copied a file to the backup" for i in range(1, lines + 1))


def test_a_long_result_is_cut_where_a_line_ends_and_says_where_the_rest_is():
    note = D.build_delivery(
        trigger_id=TRIGGER_ID, trigger_name="feed digest", ok=True, summary=_long_output()
    )
    for body, cap in ((note.body, D.NOTE_BODY_CAP), (note.channel_body, D.CHANNEL_BODY_CAP)):
        assert len(body) <= cap
        *kept, last = body.split("\n")
        assert all(line.startswith("line ") and line.endswith("backup") for line in kept), kept
        assert kept[0] == "line 001: copied a file to the backup"
        assert last == D.REST_IN_HISTORY
    # The chat channel has room for more of it than the notification.
    assert len(note.channel_body) > len(note.body)


def test_a_single_line_too_long_is_cut_at_a_word():
    words = " ".join(["verified"] * 800)
    body = D.build_delivery(trigger_id=TRIGGER_ID, ok=True, summary=words).body
    first, last = body.split("\n")
    assert len(body) <= D.NOTE_BODY_CAP
    assert first.endswith("verified…")
    assert last == D.REST_IN_HISTORY


def test_a_cut_run_summary_points_at_the_run():
    """A workflow run's note links to its run, and that page holds its whole summary."""
    note = D.build_delivery(
        trigger_id=TRIGGER_ID, ok=True, summary=_long_output(), run_id="3f2a91c0"
    )
    assert note.body.split("\n")[-1] == D.REST_ON_RUN_PAGE


def test_a_result_that_fits_is_left_whole():
    note = D.build_delivery(trigger_id=TRIGGER_ID, ok=True, summary=DIGEST_OUTPUT)
    assert note.body == note.channel_body == DIGEST_OUTPUT


# ── the work a fire started, when it ends ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agents_whole_reply_reaches_the_chat(home, chat):
    """🔴 Before: the reply was cut at 512 characters before the chat or the bell saw it."""
    _store(home, delivery=f"channel:{CHAT}")
    reply = "\n".join(
        f"* Item {i}: an appointment, a deadline or a note worth reading at breakfast."
        for i in range(1, 11)
    )
    assert 512 < len(reply) < 1000
    state = _State()
    assert _orchestrator(state)._report_to_its_trigger(TRIGGER_ID, summary=reply) is True
    [note] = await _settled(state)
    assert note["body"] == reply
    assert chat.sent == [f"feed digest finished\n{reply}"]
