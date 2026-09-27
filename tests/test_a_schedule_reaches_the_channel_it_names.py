"""A schedule's "Notify channel" names a chat channel, that channel checks the id, and it delivers.

Three things were wrong with it:

1. The API and the CLI accepted only ``^[CDGW][A-Z0-9]+$`` — one platform's channel-id shape, in
   core — so a chat id from any other channel (``4242``, ``-100123``) was refused.
2. The route was ``channel:<id>`` with no channel in it, and an id means nothing without the channel
   that issued it (#959).
3. Nothing sent it. ``triggers.delivery.deliver`` calls ``state.notify``, which never reads the
   destination, so a result "notified" to a channel went to the bell only.

Now the route is ``channel:<name>`` (the owner's direct messages there) or
``channel:<name>:<target>``, the channel's own ``validate_target`` checks the id, and the fire path
sends the result through that channel. Only the model-free parts are fakes: the channel's transport
and its delivery handle.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from personalclaw import channel_delivery, channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.triggers import delivery as D
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.schedule_view import to_schedule_row
from personalclaw.triggers.store import TriggerStore

NUMBERS = "numchat"  # a channel whose chats are numbers, negative for groups
CODES = "codechat"  # a channel whose channels are codes like C0123


class _Chat(ChannelTransportProvider):
    def __init__(self, name: str, display: str, pattern: str, example: str) -> None:
        self._name, self._display = name, display
        self._pattern, self._example = pattern, example

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._display)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    def validate_target(self, target: str) -> str:
        if re.fullmatch(self._pattern, target or ""):
            return ""
        return f"A {self._display} id looks like {self._example}."


class _Handle:
    """The channel's outbound half: what reached it."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.dms: list[str] = []
        self.sent: list[tuple[str, str]] = []

    async def open_dm(self, user_id: str) -> str:
        self.dms.append(user_id)
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw: Any) -> str:
        if self.fail:
            raise RuntimeError("the platform said no")
        self.sent.append((channel, text))
        return "m-1"


class _State:
    """The `DashboardState` members the schedule handlers and the delivery touch."""

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def push_refresh(self, *keys: str) -> None:
        return None

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        self.notes.append({"kind": kind, "title": title, "body": body, "meta": dict(meta or {})})


def _request(body: dict) -> object:
    class _Req:
        async def json(self) -> dict:
            return body

        def get(self, key: str, default: object = None) -> object:
            return default

    return _Req()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.cli_commands.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.cli_commands.sel", MagicMock())
    keys = [owner_id_credential(p) for p in (NUMBERS, CODES)]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)
    for name in (NUMBERS, CODES):
        channel_transports.unregister_transport(name)
        channel_delivery.register(None, provider=name)


def _numbers() -> _Chat:
    return _Chat(NUMBERS, "NumChat", r"-?\d+", "4242, or -100123 for a group")


def _codes() -> _Chat:
    return _Chat(CODES, "CodeChat", r"[CDGW][A-Z0-9]+", "C0123456789")


@pytest.fixture
def channels():
    channel_transports.register_transport(_numbers())
    channel_transports.register_transport(_codes())


async def _create(body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import triggers as handlers

    base = {
        "name": "nightly",
        "every": 3600,
        "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
    }
    resp = await handlers._create_schedule(_State(), {**base, **body}, _request(body))
    return resp.status, json.loads(resp.body.decode())


async def _update(raw: str, body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import triggers as handlers

    # A plain function: nothing awaits between a whole-form save's revision check and this write.
    resp = handlers._update_schedule(_State(), raw, body)
    return resp.status, json.loads(resp.body.decode())


def _stored(home, raw: str) -> Trigger:
    row = TriggerStore(base_dir=home).get(raw)
    assert row is not None
    return row.trigger


# ── each channel checks its own ids ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_id_the_channel_accepts_is_accepted(home, channels):
    for target in ("4242", "-100123"):
        status, body = await _create({"channel": f"{NUMBERS}:{target}"})
        assert status == 200, body
        assert _stored(home, body["trigger"]["raw_id"]).delivery == f"channel:{NUMBERS}:{target}"
        assert body["trigger"]["channel"] == f"{NUMBERS}:{target}"


@pytest.mark.asyncio
async def test_the_same_id_is_refused_by_a_channel_that_does_not_name_chats_that_way(channels):
    status, body = await _create({"channel": f"{CODES}:4242"})
    assert status == 400
    assert body["error"]["message"] == "A CodeChat id looks like C0123456789."
    # …and that channel's own shape is fine.
    status, body = await _create({"channel": f"{CODES}:C0123456789"})
    assert status == 200, body


@pytest.mark.asyncio
async def test_the_owners_dm_needs_no_id(home, channels):
    status, body = await _create({"channel": NUMBERS})
    assert status == 200, body
    assert _stored(home, body["trigger"]["raw_id"]).delivery == f"channel:{NUMBERS}"


@pytest.mark.asyncio
async def test_a_name_that_is_not_a_chat_channel_is_refused_with_a_sentence(channels):
    for channel in ("C0AP77JJSN6", "webui", "nochat:4242"):
        status, body = await _create({"channel": channel})
        assert status == 400, channel
        name = channel.split(":")[0]
        assert body["error"]["message"] == f"{name} isn't one of the chat channels set up here."


@pytest.mark.asyncio
async def test_an_edit_is_checked_the_same_way(home, channels):
    status, body = await _create({})
    assert status == 200, body
    raw = body["trigger"]["raw_id"]

    status, body = await _update(raw, {"channel": f"{NUMBERS}:-100123"})
    assert status == 200, body
    assert _stored(home, raw).delivery == f"channel:{NUMBERS}:-100123"

    status, body = await _update(raw, {"channel": f"{CODES}:-100123"})
    assert status == 400
    assert body["error"]["message"] == "A CodeChat id looks like C0123456789."
    assert _stored(home, raw).delivery == f"channel:{NUMBERS}:-100123", "a refusal changed nothing"


@pytest.mark.asyncio
async def test_a_failure_route_to_a_channel_is_checked_too(channels):
    status, body = await _create({"failure_delivery": "channel:nochat"})
    assert status == 400
    assert body["error"]["message"] == "nochat isn't one of the chat channels set up here."
    status, body = await _create({"failure_delivery": f"channel:{NUMBERS}:4242"})
    assert status == 200, body


def test_the_default_check_takes_any_plain_id():
    class _Plain(_Chat):
        validate_target = ChannelTransportProvider.validate_target

    plain = _Plain("plainchat", "PlainChat", "", "")
    assert plain.validate_target("room-7") == ""
    assert plain.validate_target("-100123") == ""
    assert plain.validate_target("") == "PlainChat needs a chat or channel id to send to."
    assert (
        plain.validate_target("two words") == "A PlainChat id has no spaces or control characters."
    )
    assert (
        plain.validate_target("bell\x07") == "A PlainChat id has no spaces or control characters."
    )
    assert plain.validate_target("x" * 257) == "That's too long for a PlainChat id."


# ── and the result goes there ────────────────────────────────────────────────────────────────


def _result(destination: str) -> D.Delivery:
    return D.build_delivery(
        trigger_id="clock:nightly",
        trigger_name="Nightly digest",
        ok=True,
        summary="wrote 3 items",
        run_id="r1",
        destination=destination,
    )


async def _delivered(state: _State) -> dict[str, Any]:
    """The note, once the channel send it waits for has finished."""
    for _ in range(200):
        if state.notes:
            return state.notes[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("no note was ever recorded")


def _connect(name: str, *, owner: str = "", fail: bool = False) -> _Handle:
    handle = _Handle(fail=fail)
    channel_transports.register_transport(_numbers() if name == NUMBERS else _codes())
    channel_delivery.register(handle, provider=name)
    if owner:
        save_credential(owner_id_credential(name), owner)
    return handle


@pytest.mark.asyncio
async def test_a_result_for_the_owner_goes_to_their_dm_on_that_channel():
    numbers = _connect(NUMBERS, owner="4242")
    codes = _connect(CODES, owner="U0OTHER")
    state = _State()

    assert D.deliver(state, _result(f"channel:{NUMBERS}")) is True
    note = await _delivered(state)

    assert numbers.dms == ["4242"]
    assert numbers.sent == [("dm-4242", "Nightly digest finished\nwrote 3 items")]
    assert codes.sent == [], "another channel's owner got it"
    # The bell keeps it, marked as sent there, so a "DM me" rule does not send it twice.
    assert note["meta"][D.SENT_TO_CHANNEL_KEY] == NUMBERS
    assert note["title"] == "Nightly digest finished"


@pytest.mark.asyncio
async def test_a_result_for_a_chat_goes_to_that_chat():
    numbers = _connect(NUMBERS, owner="4242")
    state = _State()

    D.deliver(state, _result(f"channel:{NUMBERS}:-100123"))
    await _delivered(state)

    assert numbers.sent == [("-100123", "Nightly digest finished\nwrote 3 items")]
    assert numbers.dms == [], "a named chat needs no DM"


@pytest.mark.parametrize(
    "setup,why",
    [
        ("not_connected", "NumChat isn't connected, so this didn't go out there."),
        ("no_owner", "NumChat doesn't know who you are yet, so this didn't go out there."),
        (
            "send_fails",
            "Sending it on NumChat failed, so it's only here. The gateway log has the error.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_when_the_channel_cannot_take_it_the_note_says_why(setup, why):
    if setup == "not_connected":
        channel_transports.register_transport(_numbers())
    elif setup == "no_owner":
        _connect(NUMBERS)
    else:
        _connect(NUMBERS, owner="4242", fail=True)
    state = _State()

    D.deliver(state, _result(f"channel:{NUMBERS}"))
    note = await _delivered(state)

    assert note["body"].endswith(why), note["body"]
    assert D.SENT_TO_CHANNEL_KEY not in note["meta"]


@pytest.mark.asyncio
async def test_a_retry_of_the_same_event_does_not_send_twice():
    numbers = _connect(NUMBERS, owner="4242")
    state = _State()
    seen: set[str] = set()
    result = _result(f"channel:{NUMBERS}")

    assert D.deliver(state, result, delivered_ids=seen) is True
    assert D.deliver(state, result, delivered_ids=seen) is False
    await _delivered(state)
    await asyncio.sleep(0.05)
    assert len(numbers.sent) == 1


@pytest.mark.asyncio
async def test_an_inbox_result_still_goes_straight_to_the_bell():
    """Vacuity floor: only a channel route waits on a send."""
    state = _State()
    assert D.deliver(state, _result("inbox")) is True
    assert state.notes and "sent_to_channel" not in state.notes[0]["meta"]


# ── what the schedule shows ──────────────────────────────────────────────────────────────────


def _trigger(delivery: str) -> Trigger:
    return Trigger(id="clock:nightly", name="nightly", kind="clock", delivery=delivery)


def test_the_row_names_the_channel_and_says_when_it_cannot_reach_it(channels):
    assert to_schedule_row(_trigger(f"channel:{NUMBERS}:4242"))["channel"] == f"{NUMBERS}:4242"
    assert to_schedule_row(_trigger(f"channel:{NUMBERS}:4242"))["channel_problem"] == ""
    # A route written before a route named its channel: no channel is called that.
    legacy = to_schedule_row(_trigger("channel:C0AP77JJSN6"))
    assert legacy["channel"] == "C0AP77JJSN6"
    assert legacy["channel_problem"] == "C0AP77JJSN6 isn't one of the chat channels set up here."
    assert to_schedule_row(_trigger("inbox"))["channel_problem"] == ""


# ── the CLI asks the same channels ───────────────────────────────────────────────────────────


def _cli(action: str, channel: str, **extra: Any) -> None:
    from personalclaw.cli_commands import _cron

    fields: dict[str, Any] = {
        "cron_action": action,
        "name": "ops" if action == "add" else None,
        "message": "check" if action == "add" else None,
        "channel": channel,
        "approval_mode": "" if action == "add" else None,
        # The owner's `--yes` to the agent job `cron add` creates; a channel it cannot take is
        # refused before that question is asked.
        "yes": True,
    }
    if action == "add":
        fields.update(every=300, cron_expr=None)
    else:
        fields.update(job_id="clock:ops", every_secs=None, cron_expr=None)
    fields.update(extra)
    _cron(argparse.Namespace(**fields))


@pytest.fixture
def installed(monkeypatch):
    """The CLI runs outside the gateway, so it builds the installed channels to ask them."""
    monkeypatch.setattr(
        "personalclaw.providers.loader.build_channel_transports", lambda: [_numbers(), _codes()]
    )


def test_the_cli_takes_a_chat_id_its_channel_accepts(home, installed):
    _cli("add", f"{NUMBERS}:-100123")
    rows = TriggerStore(base_dir=home).load()
    assert [r.trigger.delivery for r in rows] == [f"channel:{NUMBERS}:-100123"]

    _cli("update", f"{CODES}:C0123456789")
    assert TriggerStore(base_dir=home).get("clock:ops").trigger.delivery == (
        f"channel:{CODES}:C0123456789"
    )


def test_the_cli_says_why_it_refuses(home, installed, capsys):
    _cli("add", f"{CODES}:4242")
    assert "A CodeChat id looks like C0123456789." in capsys.readouterr().out
    assert TriggerStore(base_dir=home).load() == []

    _cli("add", "C0AP77JJSN6")
    assert "C0AP77JJSN6 isn't one of the chat channels set up here." in capsys.readouterr().out
    assert TriggerStore(base_dir=home).load() == []
