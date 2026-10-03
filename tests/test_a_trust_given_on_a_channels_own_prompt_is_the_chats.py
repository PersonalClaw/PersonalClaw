"""A trust given on a channel's own approval prompt is the Trust of PersonalClaw's chat.

A channel app that runs a conversation itself (Slack's threads) asks about its own turn's calls on
a prompt of its own. Its "Trust session" button kept the trust inside the app: the chat the
dashboard shows for the conversation neither said it was trusted nor could switch it off, and the
security log had no row for the grant.

Now the prompt offers what the chat's approval card offers (``approval_brief_for(event,
chat=...)``), the pressed Allow for this chat is that chat's Trust in PersonalClaw
(``answer_in_chat``), and each later call asks core which of the chat's grants answers it
(``chat_grant``). So the chat's Permission mode shows Trust, switching it to Normal there makes the
next call ask, and the grant and the switch-off each leave the audit row they leave for any chat.

The dashboard state, its routes, the conversation log and the rules are real, in a scratch home;
only the session manager and the audit sink are doubles.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state, links_kept_in_a_session_map

from personalclaw import session_restrictions, trust_mode
from personalclaw.inbox_providers import native_source
from personalclaw.llm_helpers import save_conversation_turn
from personalclaw.sdk.channel import answer_in_chat, approval_brief_for, chat_grant

#: A conversation the channel runs itself, keyed as it keys it (its thread), and the channel.
THREAD = "1700000100.000100"
CHANNEL = "chatapp"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.dashboard.state as st
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    yield tmp_path
    trust_mode.disable_yolo()
    session_restrictions.clear(THREAD)


@pytest.fixture
def state(tmp_path):
    """The gateway's dashboard state, as the channel's own turn reaches it: the channel has linked
    the conversation to its thread, as it does before it runs a turn of one."""
    state = _make_state(tmp_path)
    state.push_sessions_update = MagicMock()
    links_kept_in_a_session_map(state.sessions).set_channel_link(THREAD, THREAD, "D0CHAT")
    before = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    yield state
    native_source.set_dashboard_state(before)


def _call(command: str = "", *, tool: str = "write_file") -> SimpleNamespace:
    """A call the channel's own turn asks about, as its runtime hands it over."""
    tool_input = json.dumps({"command": command} if command else {"path": "notes.md"})
    return SimpleNamespace(
        title="bash" if command else tool,
        tool_kind="",
        risk_level="",
        tool_input=tool_input,
        tool_purpose="tidy the notes",
        request_id="req-1",
        tool_meta={},
    )


def _keys(brief: dict | None) -> list[str]:
    assert brief is not None
    return [answer["key"] for answer in brief["answers"]]


def _rows(sel: MagicMock, operation: str) -> list[dict]:
    return [
        c.kwargs
        for c in sel.return_value.log_api_access.call_args_list
        if c.kwargs.get("operation") == operation
    ]


def _a_conversation_the_channel_wrote(state) -> None:
    """Two finished turns, written by the channel under its own key."""
    for asked, said in (("tidy the notes", "Done."), ("and the drafts?", "Moved them.")):
        save_conversation_turn(
            state.conversation_log, THREAD, asked, said, source_thread=THREAD, source_user="U1"
        )


# ── What the prompt offers ────────────────────────────────────────────────────────────────


def test_a_prompt_in_the_conversation_offers_what_the_chats_card_offers(state):
    assert _keys(approval_brief_for(_call(), chat=THREAD)) == ["approved", "trust", "rejected"]
    trust = approval_brief_for(_call(), chat=THREAD)["answers"][1]
    assert trust["label"] == "Allow for this chat"
    assert "until you change it back" in trust["promise"]


def test_a_prompt_outside_the_conversation_answers_the_call_alone(state):
    assert _keys(approval_brief_for(_call())) == ["approved", "rejected"]


@pytest.mark.parametrize(
    "case", ["a call that may destroy something", "a call to a host off the allowed hosts"]
)
def test_no_standing_answer_for_a_call_the_card_offers_none_for(state, case):
    command = "rm -rf build" if case.startswith("a call that may") else "curl https://example.com/x"
    assert _keys(approval_brief_for(_call(command), chat=THREAD)) == ["approved", "rejected"]


def test_the_one_rule_withholds_the_chats_trust_from_a_call_that_reaches_off_the_list(state):
    """``chat_answers`` is the rule both prompts asked in a chat use, the reach branch included:
    a call asked about whatever a grant says is never offered a "without asking"."""
    from personalclaw.channel_delivery import chat_answers

    assert [a.key for a in chat_answers(risk="caution", reach="")] == [
        "approved",
        "trust",
        "rejected",
    ]
    assert [a.key for a in chat_answers(risk="caution", reach="It reaches example.com.")] == [
        "approved",
        "rejected",
    ]
    assert [a.key for a in chat_answers(risk="destructive", reach="")] == ["approved", "rejected"]


def test_no_standing_answer_where_the_owner_could_not_see_it(state, monkeypatch):
    """No dashboard to show the chat, a conversation the dashboard never lists, and a ceiling that
    lets no Trust stand: each prompt answers the call alone."""
    session_restrictions.mark_incognito(THREAD)
    assert _keys(approval_brief_for(_call(), chat=THREAD)) == ["approved", "rejected"]
    session_restrictions.clear(THREAD)

    import personalclaw.guardrails.policy as policy

    with monkeypatch.context() as ceiling:
        ceiling.setattr(policy, "ceiling_permits_approval", lambda level: False)
        assert _keys(approval_brief_for(_call(), chat=THREAD)) == ["approved", "rejected"]

    native_source.set_dashboard_state(None)
    assert _keys(approval_brief_for(_call(), chat=THREAD)) == ["approved", "rejected"]


# ── The Trust, shown and switched off in the chat ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_for_this_chat_is_the_chats_trust_shown_and_switched_off_there(state):
    _a_conversation_the_channel_wrote(state)
    call = _call()
    assert chat_grant(THREAD, call) == "", "a call asks before anything is trusted"

    with patch("personalclaw.dashboard.approval_state.sel") as granted:
        assert answer_in_chat(THREAD, "trust", channel=CHANNEL, request_id="req-1") is True

    chat = state._sessions[THREAD]
    assert chat._trust is True
    assert [m["content"] for m in chat.messages] == [
        "tidy the notes",
        "Done.",
        "and the drafts?",
        "Moved them.",
    ], "the chat that holds the Trust is the conversation, loaded whole"
    (row,) = _rows(granted, "tool_approval:trust")
    assert row["outcome"] == "approved"
    assert row["resources"] == "req-1"
    assert row["metadata"] == {"decided_by": f"channel:{CHANNEL}"}
    assert chat_grant(THREAD, call) == "trust", "the next call runs without asking"

    async with TestClient(TestServer(_make_app(state))) as client:
        shown = await (await client.get(f"/api/chat/sessions/{THREAD}")).json()
        assert shown["approval"] == "trust"

        with patch("personalclaw.dashboard.chat_handlers.sel") as switched:
            off = await client.post("/api/chat/mode", json={"mode": "normal", "session": THREAD})
            assert off.status == 200
        (row,) = _rows(switched, "mode_change:normal")
        assert row["resources"] == THREAD

        shown = await (await client.get(f"/api/chat/sessions/{THREAD}")).json()
        assert shown["approval"] == "normal"
    assert chat_grant(THREAD, call) == "", "switched off in the chat, the next call asks again"


@pytest.mark.asyncio
async def test_a_trust_set_in_the_chat_answers_the_channels_next_call(state):
    """One Trust for the conversation, wherever it is set: the chat's Permission mode is it too."""
    _a_conversation_the_channel_wrote(state)
    async with TestClient(TestServer(_make_app(state))) as client:
        await client.get(f"/api/chat/sessions/{THREAD}")
        on = await client.post("/api/chat/mode", json={"mode": "trust", "session": THREAD})
        assert on.status == 200
    assert chat_grant(THREAD, _call()) == "trust"


def test_a_first_turn_still_running_gets_a_chat_of_its_own(state):
    """Nothing of the conversation is written while its first turn runs: the Trust gets a chat,
    and the turn the channel writes when it ends lands in it."""
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is True
    chat = state._sessions[THREAD]
    assert chat._trust is True and chat.messages == []

    save_conversation_turn(state.conversation_log, THREAD, "tidy the notes", "Done.")
    assert [m["content"] for m in chat.messages] == ["tidy the notes", "Done."]


def test_an_archived_conversation_is_loaded_whole_never_shadowed_by_a_blank_chat(state):
    from personalclaw.dashboard.chat_persistence import save_all_sessions_to_history

    _a_conversation_the_channel_wrote(state)
    state.conversation_log.update_metadata(THREAD, {"closed": True})
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is True
    assert len(state._sessions[THREAD].messages) == 4

    save_all_sessions_to_history(state)
    assert len(state.conversation_log.read_messages(THREAD)) == 4


# ── What is not taken ─────────────────────────────────────────────────────────────────────


def test_an_answer_for_this_call_alone_changes_nothing_beyond_it(state):
    for one_call in ("approved", "rejected"):
        assert answer_in_chat(THREAD, one_call, channel=CHANNEL) is True
    assert THREAD not in state._sessions
    assert chat_grant(THREAD, _call()) == ""


def test_an_answer_no_prompt_offers_is_not_taken(state):
    assert answer_in_chat(THREAD, "trust_agent", channel=CHANNEL) is False
    assert answer_in_chat(THREAD, "yolo", channel=CHANNEL) is False
    assert THREAD not in state._sessions


def test_a_chat_of_yours_no_channel_runs_is_not_a_channels_to_trust(state):
    """Only a conversation a channel linked to one of its threads is trusted from a channel's own
    prompt: a chat of yours that no channel runs is not, by whichever key a channel names it."""
    yours = state.get_or_create_session("chat-1-1700000000")
    for key in (yours.key, f"dashboard:{yours.key}"):
        assert _keys(approval_brief_for(_call(), chat=key)) == ["approved", "rejected"]
        assert answer_in_chat(key, "trust", channel=CHANNEL) is False
    assert yours._trust is False


def test_no_trust_is_taken_where_the_owner_could_not_see_it(state, monkeypatch):
    session_restrictions.mark_temporary(THREAD)
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is False
    session_restrictions.clear(THREAD)
    assert THREAD not in state._sessions

    import personalclaw.guardrails.policy as policy

    with monkeypatch.context() as ceiling, patch("personalclaw.approval_grants.refused") as refused:
        ceiling.setattr(policy, "ceiling_permits_approval", lambda level: False)
        assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is False
    assert refused.call_count == 1, "a Trust the ceiling refuses is audited"
    assert THREAD not in state._sessions

    native_source.set_dashboard_state(None)
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is False


def test_a_conversation_an_app_started_takes_no_trust_and_approves_nothing_on_its_own(state):
    _a_conversation_the_channel_wrote(state)
    state.conversation_log.update_metadata(THREAD, {"created_by_app": "some-app"})
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is False

    chat = state.get_or_create_session(THREAD)
    chat._trust = True
    trust_mode.enable_yolo(ttl_secs=60)
    assert chat_grant(THREAD, _call()) == ""


# ── Which grant answers a call ────────────────────────────────────────────────────────────


def test_no_grant_answers_a_call_to_a_host_off_the_allowed_hosts(state):
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is True
    assert chat_grant(THREAD, _call("curl https://example.com/x")) == ""
    trust_mode.enable_yolo(ttl_secs=60)
    assert chat_grant(THREAD, _call("curl https://example.com/x")) == ""


def test_yolo_answers_the_channels_calls_and_ends_with_yolo(state):
    trust_mode.enable_yolo(ttl_secs=60)
    assert chat_grant(THREAD, _call()) == "yolo"
    trust_mode.disable_yolo()
    assert chat_grant(THREAD, _call()) == ""


def test_trust_reads_answers_a_read_only_command_alone(state):
    chat = state.get_or_create_session(THREAD)
    chat._trust_reads = True
    assert chat_grant(THREAD, _call("ls build")) == "trust_reads"
    assert chat_grant(THREAD, _call("echo done > notes.md")) == ""
    assert chat_grant(THREAD, _call()) == ""


def test_a_trust_the_ceiling_no_longer_lets_stand_answers_nothing(state, monkeypatch):
    assert answer_in_chat(THREAD, "trust", channel=CHANNEL) is True
    import personalclaw.guardrails.policy as policy

    monkeypatch.setattr(policy, "ceiling_permits_approval", lambda level: False)
    assert chat_grant(THREAD, _call()) == ""
