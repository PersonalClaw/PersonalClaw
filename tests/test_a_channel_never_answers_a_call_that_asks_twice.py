"""A messaging channel never answers a call that asks a second confirmation.

A channel's approval prompt answers on one press: "Allow once" approves. A call whose row names a
second confirmation (`approval_confirms`: a delete, a change outside the chat's folder, a send to
other people, spending, a tool that declares nothing) must not be approved that way, so no
surface skips the confirmation the row asks. Such a call is not put to a channel's prompt at all:
the owner is told on their channel that it waits, with the link to where it is asked, and a press
that arrives from a channel anyway (a prompt raised before, a channel's own reply) approves
nothing. A call that asks no second confirmation is prompted exactly as before.

Only the channels' outbound halves are fakes. The registry, the owner ids and the gateway's
approval callback are real, in a scratch home.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from chat_test_helpers import a_chat_from_a_channel
from test_a_channel_approval_offers_the_cards_answers import (  # noqa: F401 - a fixture
    CHANNEL,
    _Channel,
    _state,
    _until,
    isolated,
)
from test_approval_threading import _make_gateway

from personalclaw import approval_answer, channel_delivery
from personalclaw.approval_confirms import DELETES
from personalclaw.channel_delivery import ALLOW_FOR_THIS_CHAT, ALLOW_ONCE, DENY, ONE_CALL_ANSWERS
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent

pytestmark = pytest.mark.security


class _Told(_Channel):
    """A channel that also keeps every message it was asked to deliver."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw) -> str:
        self.texts.append(text)
        return "1"


def _connect() -> _Told:
    handle = _Told()
    channel_delivery.register(handle, provider=CHANNEL)
    save_credential(owner_id_credential(CHANNEL), f"owner-on-{CHANNEL}")
    return handle


async def _hold(state, chat, confirms: tuple[str, ...], request_id: str = "r-1") -> asyncio.Future:
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    chat._approval_futures[request_id] = future
    await state.hold_session_approval(
        chat,
        request_id,
        tool="bash",
        tool_input='{"command": "rm -rf build"}',
        tool_purpose="clear the build folder",
        agent="PersonalClaw",
        risk="destructive" if confirms else "caution",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
        confirms=confirms,
    )
    return future


@pytest.mark.asyncio
async def test_a_chat_on_a_channel_is_told_where_to_answer_a_call_that_asks_twice(
    isolated,  # noqa: F811
):
    channel = _connect()
    state = _state(isolated)
    chat = a_chat_from_a_channel(state, CHANNEL)
    approval_id = chat_approval_id(chat.key, "r-1")

    waiting = await _hold(state, chat, (DELETES,))
    await _until(lambda: channel.texts, "the owner was told on the chat's channel")
    assert channel.prompts == [], "a channel's one-press prompt never asks it"
    [told] = channel.texts
    assert "bash" in told
    assert "once more" in told
    assert approval_id in state._pending_approvals, "it still waits, where it is asked twice"
    waiting.cancel()


@pytest.mark.asyncio
async def test_a_press_from_a_channel_approves_nothing_that_asks_twice(
    isolated,  # noqa: F811
):
    _connect()
    state = _state(isolated)
    chat = a_chat_from_a_channel(state, CHANNEL)
    approval_id = chat_approval_id(chat.key, "r-1")
    waiting = await _hold(state, chat, (DELETES,))
    by = approval_answer.on_channel(CHANNEL)

    in_its_chat = (ALLOW_ONCE, ALLOW_FOR_THIS_CHAT, DENY)
    assert not state.answer_on_channel(approval_id, "approved", offered=ONE_CALL_ANSWERS, by=by)
    # "Allow for this chat" approves this call too, through the chat's own decision path.
    assert not state.answer_on_channel(approval_id, "trust", offered=in_its_chat, by=by)
    assert not state.resolve_approval(approval_id, True, by=by)
    assert not waiting.done()
    assert approval_id in state._pending_approvals
    # A Deny from the channel is no approval: it answers.
    assert state.answer_on_channel(approval_id, "rejected", offered=ONE_CALL_ANSWERS, by=by)
    await asyncio.wait_for(waiting, timeout=2)
    assert waiting.result() == "rejected"


@pytest.mark.asyncio
async def test_a_call_that_asks_no_second_confirmation_is_prompted_as_before(
    isolated,  # noqa: F811
):
    channel = _connect()
    state = _state(isolated)
    chat = a_chat_from_a_channel(state, CHANNEL)

    waiting = await _hold(state, chat, ())
    await _until(lambda: channel.prompts, "the chat's channel asked")
    assert channel.texts == []
    waiting.cancel()


# ── The gateway's own channel prompt, for a background call ──────────────────────────────────────


def _permission(title: str, tool_input: object, **kw: object) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST, title=title, request_id="req1", tool_input=tool_input, **kw
    )


async def _background(event: LLMEvent, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_OWNER_ID", "U000")
    gateway = _make_gateway()
    gateway._channel_delivery.inner.request_approval = AsyncMock(return_value=True)
    gateway.dashboard_state.request_approval = AsyncMock(return_value=False)
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        decided = await gateway._interactive_approval("subagent")(event, "")
    return gateway, decided


@pytest.mark.asyncio
async def test_a_background_call_that_asks_twice_is_not_put_to_a_channel(monkeypatch):
    event = _permission(
        "bash", {"command": "rm -rf build"}, risk_level="destructive", consequences=()
    )
    gateway, decided = await _background(event, monkeypatch)
    gateway._channel_delivery.inner.request_approval.assert_not_awaited()
    gateway.dashboard_state.request_approval.assert_awaited_once()
    kwargs = gateway.dashboard_state.request_approval.call_args.kwargs
    assert list(kwargs["confirms"]) == [DELETES]
    # The registry is not told a channel is asking: it tells the owner where to answer it.
    assert not kwargs.get("asked_on_channel")
    # The channel said yes for nothing: the decision is the one asked where it asks twice.
    assert decided.approved is False


@pytest.mark.asyncio
async def test_a_background_call_that_asks_no_second_confirmation_is_put_to_the_channel(
    monkeypatch,
):
    event = _permission("task_create", {"title": "x"}, risk_level="caution", consequences=())
    gateway, decided = await _background(event, monkeypatch)
    gateway._channel_delivery.inner.request_approval.assert_awaited_once()
    assert decided.approved is True
