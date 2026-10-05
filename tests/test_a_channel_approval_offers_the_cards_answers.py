"""A chat channel's approval prompt offers the answers the dashboard's approval card offers.

The card in a chat answers a call once, or for the rest of the chat ("This chat", the chat's
Trust), or denies it. A prompt on the chat's own channel offered Approve and Deny alone, so a
question asked from a phone that needed eighteen reads was eighteen taps, one per call, with no
way to say "the rest of this chat too".

Core decides what a prompt offers (``DashboardApprovalState.channel_answers``) and hands it over
in the approval brief; the channel renders it and resolves the prompt with the answer pressed
(``ChannelDelivery.request_approval``). "Allow for this chat" is the card's "This chat" exactly:
it trusts that one chat, its next calls run without asking, the chat shows it, and nothing else
widens. It is offered only where it means what it says: on a prompt asked in the chat that is
asking, for a call that may not destroy anything, under a ceiling that lets a chat's Trust stand.

Only the channels' outbound halves and the agent's client are fakes. The approval registry, the
chat runner, the owner ids and the rules are real, in a scratch home.
"""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat_test_helpers import a_chat_from_a_channel, links_kept_in_a_session_map

from personalclaw import channel_delivery
from personalclaw.approval_brief import APPROVAL_BRIEF_META_KEY, compose_approval_brief
from personalclaw.channel_delivery import ALLOW_FOR_THIS_CHAT, ALLOW_ONCE, DENY
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.dashboard.approval_state import chat_approval_id
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

CHANNEL, OTHER = "chatapp", "otherapp"
CARD = Path(__file__).resolve().parents[1] / "web/src/pages/chat/ApprovalCard.tsx"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    keys = (CRED_OWNER_ID, owner_id_credential(CHANNEL), owner_id_credential(OTHER))
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    for key in keys:
        os.environ.pop(key, None)
    for provider in (CHANNEL, OTHER):
        channel_delivery.register(None, provider=provider)


class _Channel:
    """A channel's outbound half: each prompt it was asked, with the brief it was handed."""

    def __init__(self) -> None:
        self.prompts: list[SimpleNamespace] = []

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw) -> str:
        return "1"

    async def request_approval(
        self, event, *, source, parent_session_key="", sessions=None, on_prompted=None
    ):  # noqa: E301 - the protocol's shape
        brief = event.tool_meta[APPROVAL_BRIEF_META_KEY]
        pending = SimpleNamespace(
            future=asyncio.get_running_loop().create_future(),
            brief=brief,
            asked_in=parent_session_key,
        )
        self.prompts.append(pending)
        if on_prompted:
            on_prompted(pending)
        outcome = await pending.future
        ends = {a["key"]: a["ends"] for a in brief.get("answers", [])}
        return ends.get(outcome, outcome) == "approved"


def _connect(provider: str = CHANNEL) -> _Channel:
    handle = _Channel()
    channel_delivery.register(handle, provider=provider)
    save_credential(owner_id_credential(provider), f"owner-on-{provider}")
    return handle


def _state(tmp_path):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    # A chat's thread is kept where the gateway keeps it: the door links each chat it opens.
    links_kept_in_a_session_map(sessions)
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


async def _ask(
    state, chat, request_id: str = "r-1", *, risk: str = "caution", reach: str = ""
) -> asyncio.Future:
    """What the chat's runner does for a call that needs approval (``hold_session_approval``)."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    chat._approval_futures[request_id] = future
    await state.hold_session_approval(
        chat,
        request_id,
        tool="mcp/notes/list_directory",
        tool_input='{"path": "inbox"}',
        tool_purpose="see what is on your plate",
        agent="PersonalClaw",
        risk=risk,
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
        reach=reach,
    )
    return future


async def _until(check, what: str) -> None:
    for _ in range(300):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


def _keys(prompt) -> list[str]:
    return [a["key"] for a in prompt.brief.get("answers", [])]


# ── what a prompt offers ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_prompt_in_the_chat_that_asks_offers_allow_for_this_chat(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)

    waiting = await _ask(state, chat)
    await _until(lambda: channel.prompts, "the chat's channel asked")
    prompt = channel.prompts[0]
    assert prompt.asked_in == f"dashboard:{chat.key}", "asked in that chat"
    assert prompt.brief["answers"] == [
        {
            "key": "approved",
            "label": "Allow once",
            "ends": "approved",
            "word": "APPROVE",
            "promise": "",
        },
        {
            "key": "trust",
            "label": "Allow for this chat",
            "ends": "approved",
            "word": "TRUST",
            "promise": "Every tool in this chat runs without asking, until you change it back. "
            "A command that reaches a host off your allowed hosts, or that deletes your home "
            "folder, the filesystem root or the working folder, still asks.",
        },
        {"key": "rejected", "label": "Deny", "ends": "rejected", "word": "DENY", "promise": ""},
    ]
    waiting.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("risk", ["destructive", "unchecked"])
async def test_a_call_that_may_destroy_something_is_offered_this_call_alone(tmp_path, risk):
    """The card withholds its standing answers on a call that may destroy something (one that
    is destructive, or a shell command the screen could not check) until they are unlocked, and
    a prompt has no unlock."""
    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)

    waiting = await _ask(state, chat, risk=risk)
    await _until(lambda: channel.prompts, "asked")
    assert _keys(channel.prompts[0]) == ["approved", "rejected"]
    waiting.cancel()


@pytest.mark.asyncio
async def test_a_call_to_a_host_off_the_allowed_hosts_is_offered_this_call_alone(tmp_path):
    """The card asks about a command reaching a host off the allowed hosts whatever a grant says,
    so it offers that call no standing answer, and neither does the prompt: "Allow for this chat"
    would promise a "without asking" the call never gets. The prompt says why, as the card does."""
    from personalclaw.run_bounds import Reach, ask_note

    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)
    reach = ask_note(Reach(hosts=("pkgs.example.com",)))

    waiting = await _ask(state, chat, reach=reach)
    await _until(lambda: channel.prompts, "asked")
    assert _keys(channel.prompts[0]) == ["approved", "rejected"]
    assert channel.prompts[0].brief["reach"] == reach
    assert "pkgs.example.com" in reach
    waiting.cancel()


@pytest.mark.asyncio
async def test_a_prompt_outside_the_chat_that_asks_offers_this_call_alone(tmp_path):
    """A chat in PersonalClaw asked on "Send approvals to": "this chat" would name the owner's DM
    there, and Home and the Inbox, the other surfaces outside a chat, offer Approve and Deny."""
    import json

    channel = _connect()
    cfg = AppConfig.load()
    cfg.agent.approval_channel = CHANNEL
    cfg.save()
    rules = tmp_path / "entity_settings" / "notification_rules.json"
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text(
        json.dumps(
            {
                "rules": {
                    "approval/requested": {
                        "mode": "immediate",
                        "targets": ["dashboard", "channel_dm"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = _state(tmp_path)
    chat = state.get_or_create_session("here")

    waiting = await _ask(state, chat)
    await _until(lambda: channel.prompts, "Send approvals to asked")
    assert channel.prompts[0].asked_in == "", "asked in the owner's DM"
    assert _keys(channel.prompts[0]) == ["approved", "rejected"]
    waiting.cancel()


@pytest.mark.asyncio
async def test_under_a_ceiling_that_asks_for_a_person_the_chat_answer_is_not_offered(
    tmp_path, monkeypatch
):
    """The card's route refuses a chat's Trust under an ``ask`` ceiling; a prompt does not offer
    an answer that would be refused."""
    import personalclaw.guardrails.policy as policy

    monkeypatch.setattr(policy, "ceiling_permits_approval", lambda level: False)
    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)

    waiting = await _ask(state, chat)
    await _until(lambda: channel.prompts, "asked")
    assert _keys(channel.prompts[0]) == ["approved", "rejected"]
    waiting.cancel()


def test_a_call_with_no_chat_behind_it_is_offered_this_call_alone():
    """The gateway's relay (a subagent, a trigger's run) stamps a brief from the event alone."""
    event = SimpleNamespace(
        title="write_file", tool_input='{"path": "a.txt"}', tool_purpose="", risk_level="caution"
    )
    assert [a["key"] for a in compose_approval_brief(event)["answers"]] == ["approved", "rejected"]


# ── what the answer does ────────────────────────────────────────────────────────────────────


async def _stream(items):
    for item in items:
        yield item


def _permission(request_id: str) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="mcp/notes/list_directory",
        tool_kind="read",
        request_id=request_id,
        tool_input='{"path": "inbox"}',
        risk_level="caution",
    )


@contextmanager
def _agent(state, events: list[LLMEvent]):
    """The chat's agent: a client that asks for each call in *events*, then completes."""
    client = AsyncMock()
    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream(events))
    state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    state.sessions.record_failure = AsyncMock()
    state.sessions.check_context_usage = MagicMock()
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    builder = MagicMock()
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    with patch("personalclaw.dashboard.chat.sel", return_value=MagicMock()):
        yield client


def _permission_rows(chat) -> list[dict]:
    return [m for m in chat.messages if m.get("role") == "permission"]


@pytest.mark.asyncio
async def test_allow_for_this_chat_approves_the_next_call_in_that_chat_without_asking(tmp_path):
    from personalclaw.dashboard.chat import run_chat

    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)
    events = [_permission("r-1"), _permission("r-2"), LLMEvent(kind=EVENT_COMPLETE)]

    async def press_allow_for_this_chat() -> None:
        await _until(lambda: channel.prompts, "the first call asked on the channel")
        assert "trust" in _keys(channel.prompts[0])
        channel.prompts[0].future.set_result("trust")

    with _agent(state, events) as client:
        presser = asyncio.create_task(press_allow_for_this_chat())
        await asyncio.wait_for(run_chat(state, chat, "What is on my plate today?"), timeout=20)
        await presser

    approved = [c.args[0] for c in client.approve_tool.await_args_list]
    assert approved == ["r-1", "r-2"], "both calls ran"
    assert len(channel.prompts) == 1, "the second call asked nobody"
    assert len(_permission_rows(chat)) == 1, "the chat asked once"
    assert (
        '"resolved": "trust"' in _permission_rows(chat)[0]["cls"]
    ), "the chat's record says it was trusted, as the card's This chat records it"
    # What the dashboard shows of that chat, and where the owner turns it off.
    assert chat.to_dict()["trust"] is True
    state.sessions.set_approval_policy.assert_any_call(f"dashboard:{chat.key}", "auto")
    assert not state._pending_approvals


@pytest.mark.asyncio
async def test_allow_for_this_chat_never_reaches_another_chat(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    first = a_chat_from_a_channel(state, CHANNEL)
    second = a_chat_from_a_channel(state, CHANNEL)

    waiting_first = await _ask(state, first, "r-1")
    await _until(lambda: len(channel.prompts) == 1, "the first chat asked")
    channel.prompts[0].future.set_result("trust")
    assert await asyncio.wait_for(waiting_first, timeout=5) == "approved"
    assert first._trust is True

    waiting_second = await _ask(state, second, "r-1")
    await _until(lambda: len(channel.prompts) == 2, "the second chat asked as before")
    assert second._trust is False
    assert second.to_dict()["trust"] is False
    assert chat_approval_id(second.key, "r-1") in state._pending_approvals, "still asking"
    assert "trust" in _keys(channel.prompts[1]), "and it may be trusted in its own right"
    for call in state.sessions.set_approval_policy.call_args_list:
        assert call.args[0] != f"dashboard:{second.key}", "no policy was set on the other chat"
    waiting_second.cancel()


@pytest.mark.asyncio
async def test_an_answer_the_prompt_did_not_offer_decides_nothing(tmp_path):
    """A destructive call's prompt offers Allow once and Deny; a press that names the chat's
    Trust anyway is not one of them, so the approval waits on, and the chat is not trusted."""
    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)

    waiting = await _ask(state, chat, risk="destructive")
    await _until(lambda: channel.prompts, "asked")
    channel.prompts[0].future.set_result("trust")
    for _ in range(20):
        await asyncio.sleep(0)
    assert not waiting.done()
    assert chat._trust is False
    assert chat_approval_id(chat.key, "r-1") in state._pending_approvals
    waiting.cancel()


@pytest.mark.asyncio
async def test_allow_once_and_deny_on_the_channel_answer_as_before(tmp_path):
    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)

    once = await _ask(state, chat, "r-1")
    await _until(lambda: len(channel.prompts) == 1, "asked")
    channel.prompts[0].future.set_result(ALLOW_ONCE.key)
    assert await asyncio.wait_for(once, timeout=5) == "approved"
    assert chat._trust is False, "nothing is remembered"

    denied = await _ask(state, chat, "r-2")
    await _until(lambda: len(channel.prompts) == 2, "asked again")
    channel.prompts[1].future.set_result(DENY.key)
    assert await asyncio.wait_for(denied, timeout=5) == "rejected"


@pytest.mark.asyncio
async def test_the_audit_row_names_the_channel_that_answered(tmp_path):
    from personalclaw.sel import sel

    channel = _connect()
    state = _state(tmp_path)
    chat = a_chat_from_a_channel(state, CHANNEL)
    logged: list[dict[str, Any]] = []
    with patch.object(type(sel()), "log_api_access", lambda self, **kw: logged.append(kw)):
        waiting = await _ask(state, chat)
        await _until(lambda: channel.prompts, "asked")
        channel.prompts[0].future.set_result("trust")
        await asyncio.wait_for(waiting, timeout=5)
    rows = [r for r in logged if r.get("operation") == "tool_approval:trust"]
    assert len(rows) == 1
    assert rows[0]["metadata"] == {"decided_by": f"channel:{CHANNEL}"}


# ── one vocabulary with the card ────────────────────────────────────────────────────────────


def _card_scope(key: str) -> dict[str, str]:
    """One of the card's remember-scopes (``REMEMBER_SCOPES`` in ApprovalCard.tsx). Its promise is
    a quoted string, or a template whose ``${NAME}`` parts are the card's own string constants."""
    text = CARD.read_text(encoding="utf-8")
    block = text[text.index("const REMEMBER_SCOPES") :]
    scope = block[block.index(f"key: '{key}'") :]
    scope = scope[: scope.index("},")]
    promise = re.search(r"promise: \(\) => (?:'([^']+)'|`([^`]+)`)", scope)
    words = promise.group(1) or re.sub(
        r"\$\{([A-Z_]+)\}",
        lambda name: re.search(rf"const {name.group(1)} = '([^']+)'", text).group(1),
        promise.group(2),
    )
    return {
        "action": re.search(r"action: '([a-z_]+)' as const", scope).group(1),
        "promise": words,
    }


def test_allow_for_this_chat_is_the_card_s_this_chat_in_its_words():
    chat = _card_scope("chat")
    assert ALLOW_FOR_THIS_CHAT.key == chat["action"], "the same decision the card posts"
    assert ALLOW_FOR_THIS_CHAT.promise == chat["promise"], "the same promise the card shows"


def test_allow_once_is_the_card_s_just_this_once():
    once = _card_scope("once")
    assert ALLOW_ONCE.key == once["action"]
    assert ALLOW_ONCE.promise == "", "it remembers nothing, so a prompt says nothing more"
