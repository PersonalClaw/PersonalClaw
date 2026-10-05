"""Core features: what this core offers apps, by name, and that each name keeps its promise.

An app asks ``personalclaw.sdk.features.core_has`` whether this core offers a contract, and names
the ones it relies on in ``requiresCoreFeatures``, which the compatibility check reads. A name is
therefore a published surface twice over: an app's code branches on it, and an app's manifest is
refused or admitted by it. So a name, once offered, is never withdrawn, and every offered name is
held here to the contract it stands for: a core that kept the name and lost the contract would be
the version skew this exists to catch, one level down.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw.apps.core_features import FEATURE_NAME_RE
from personalclaw.sdk import features
from personalclaw.sdk.features import (
    APPROVAL_ANSWERS,
    CHAT_TRUST,
    CLOSING_STREAMS,
    CORE_FEATURES,
    CUT_OFF_ANSWERS,
    DIGEST_REPLIES,
    GUARDED_DOWNLOAD,
    LINKS_NAME_THEIR_CHANNEL,
    MESSAGE_INSTRUCTIONS,
    MESSAGES_RUN_ONCE,
    PAIRED_OWNER,
    PRE_TOOL_HOOKS,
    TOOL_CALL_SCREEN,
    TURNS_NAME_THEIR_CHANNEL,
    TURNS_NAME_WHO_ASKED,
    core_has,
)

#: Every name a core has offered. A name leaves this set only with a deliberate break of every app
#: that declares it, so a removal from CORE_FEATURES fails here first.
OFFERED_ONCE = {
    "approval-answers",
    "chat-trust",
    "closing-streams",
    "cut-off-answers",
    "digest-replies",
    "guarded-download",
    "links-name-their-channel",
    "message-instructions",
    "messages-run-once",
    "paired-owner",
    "pre-tool-hooks",
    "tool-call-screen",
    "turns-name-their-channel",
    "turns-name-who-asked",
}


def test_the_sdk_publishes_the_names_and_the_question():
    assert set(features.__all__) == {
        "APPROVAL_ANSWERS",
        "CHAT_TRUST",
        "CLOSING_STREAMS",
        "CORE_FEATURES",
        "CUT_OFF_ANSWERS",
        "DIGEST_REPLIES",
        "GUARDED_DOWNLOAD",
        "LINKS_NAME_THEIR_CHANNEL",
        "MESSAGE_INSTRUCTIONS",
        "MESSAGES_RUN_ONCE",
        "PAIRED_OWNER",
        "PRE_TOOL_HOOKS",
        "TOOL_CALL_SCREEN",
        "TURNS_NAME_THEIR_CHANNEL",
        "TURNS_NAME_WHO_ASKED",
        "core_has",
    }
    assert APPROVAL_ANSWERS == "approval-answers"
    assert CHAT_TRUST == "chat-trust"
    assert CLOSING_STREAMS == "closing-streams"
    assert CUT_OFF_ANSWERS == "cut-off-answers"
    assert DIGEST_REPLIES == "digest-replies"
    assert GUARDED_DOWNLOAD == "guarded-download"
    assert LINKS_NAME_THEIR_CHANNEL == "links-name-their-channel"
    assert MESSAGE_INSTRUCTIONS == "message-instructions"
    assert MESSAGES_RUN_ONCE == "messages-run-once"
    assert PAIRED_OWNER == "paired-owner"
    assert PRE_TOOL_HOOKS == "pre-tool-hooks"
    assert TOOL_CALL_SCREEN == "tool-call-screen"
    assert TURNS_NAME_THEIR_CHANNEL == "turns-name-their-channel"
    assert TURNS_NAME_WHO_ASKED == "turns-name-who-asked"
    for name in (
        APPROVAL_ANSWERS,
        CHAT_TRUST,
        CLOSING_STREAMS,
        CUT_OFF_ANSWERS,
        DIGEST_REPLIES,
        GUARDED_DOWNLOAD,
        LINKS_NAME_THEIR_CHANNEL,
        MESSAGE_INSTRUCTIONS,
        MESSAGES_RUN_ONCE,
        PAIRED_OWNER,
        PRE_TOOL_HOOKS,
        TOOL_CALL_SCREEN,
        TURNS_NAME_THEIR_CHANNEL,
        TURNS_NAME_WHO_ASKED,
    ):
        assert name in CORE_FEATURES
        assert core_has(name) is True


def test_a_feature_this_core_does_not_offer_is_answered_no():
    assert core_has("a-feature-of-a-newer-core") is False
    assert core_has("") is False


def test_a_name_once_offered_is_never_withdrawn():
    assert OFFERED_ONCE <= CORE_FEATURES, (
        f"withdrawn: {sorted(OFFERED_ONCE - CORE_FEATURES)} — an app that declares a "
        "withdrawn name can no longer be installed anywhere"
    )


@pytest.mark.parametrize("name", sorted(CORE_FEATURES))
def test_every_name_is_a_name(name):
    assert FEATURE_NAME_RE.match(name), name


# ── each name is held to its contract ─────────────────────────────────────────────────────────


def _approval_answers_hold() -> None:
    """A channel's approval prompt is handed answers it can offer, whichever way it gets its brief:
    the one core stamps when it asks (from the dashboard's pending entry), one composed from the
    event for an approval the channel's own turn raised, and a stamped brief that came without
    answers, which is composed again rather than handed over with nothing to press."""
    from personalclaw.approval_brief import (
        APPROVAL_BRIEF_META_KEY,
        compose_approval_brief,
        entry_approval_brief,
    )
    from personalclaw.channel_delivery import APPROVAL_ENDINGS, ApprovalAnswer
    from personalclaw.sdk.channel import approval_brief_for

    def offerable(brief: dict | None) -> list[dict]:
        assert brief is not None
        answers = brief.get("answers")
        assert isinstance(answers, list) and answers, f"no answers in {brief!r}"
        for answer in answers:
            assert set(answer) == set(ApprovalAnswer.__dataclass_fields__)
            assert all(isinstance(v, str) for v in answer.values())
            assert answer["key"] and answer["label"] and answer["word"]
            assert answer["ends"] in APPROVAL_ENDINGS[:2]
        return answers

    event = SimpleNamespace(
        title="write_file", tool_input='{"path": "notes.md"}', tool_purpose="", tool_meta={}
    )
    composed = offerable(compose_approval_brief(event))
    assert [a["ends"] for a in composed] == ["approved", "rejected"]

    stamped = offerable(entry_approval_brief({"tool": "write_file", "tool_input": "{}"}))
    assert {a["key"] for a in stamped} >= {"approved", "rejected"}

    bare = {"tool": "write_file", "input": "", "purpose": "", "summary": ""}
    event.tool_meta = {APPROVAL_BRIEF_META_KEY: bare}
    assert offerable(approval_brief_for(event)) == composed


def _guarded_download_holds() -> None:
    """``open_url`` asks the guard before a request is sent: a source on this machine, which the
    owner has not allowed, is refused and never contacted, and once allowed it is read."""
    import http.server
    import json
    import threading

    from personalclaw.config.loader import config_dir
    from personalclaw.sdk.net import EgressBlocked, open_url

    asked: list[str] = []

    class _Source(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server's name
            asked.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"bytes")

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Source)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/file"
    try:
        with pytest.raises(EgressBlocked):
            open_url(url, timeout_s=10)
        assert asked == [], "a refused source was contacted"
        (config_dir() / "config.json").write_text(
            json.dumps({"security": {"egress": {"allow_hosts": ["127.0.0.1"]}}}), encoding="utf-8"
        )
        with open_url(url, timeout_s=10) as response:
            assert response.read() == b"bytes"
        assert asked == ["/file"]
    finally:
        server.shutdown()
        server.server_close()


def _chat_trust_holds() -> None:
    """A channel's own prompt in a conversation it runs itself offers that chat's Trust, the
    pressed Allow for this chat trusts PersonalClaw's chat for it, the next call runs on that
    Trust, and the chat switched back to Normal makes the next call ask."""
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from unittest.mock import MagicMock

    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.inbox_providers import native_source
    from personalclaw.sdk.channel import answer_in_chat, approval_brief_for, chat_grant

    with TemporaryDirectory() as scratch:
        sessions = MagicMock()
        # The channel linked the conversation to its thread, as it does before running a turn.
        sessions.get_channel_link.side_effect = lambda key: (
            (key, "D0CHAT") if key == "1700000000.000100" else (None, None)
        )
        state = DashboardState(
            sessions=sessions,
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=Path(scratch)),
        )
        state.push_sessions_update = MagicMock()
        before = native_source.get_dashboard_state()
        native_source.set_dashboard_state(state)
        try:
            event = SimpleNamespace(
                title="write_file", tool_input='{"path": "notes.md"}', tool_purpose="", tool_meta={}
            )
            brief = approval_brief_for(event, chat="1700000000.000100")
            assert brief is not None
            assert [a["key"] for a in brief["answers"]] == ["approved", "trust", "rejected"]
            assert chat_grant("1700000000.000100", event) == ""
            assert answer_in_chat("1700000000.000100", "trust", channel="chatapp") is True
            assert chat_grant("1700000000.000100", event) == "trust"
            state._sessions["1700000000.000100"]._trust = False
            assert chat_grant("1700000000.000100", event) == ""
        finally:
            native_source.set_dashboard_state(before)


def _links_name_their_channel_holds() -> None:
    """A chat linked to a thread on a channel answers on that channel, the session store names it,
    and linking it on another channel moves it there."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.session import SessionManager

    sessions = SessionManager(AppConfig())
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=None)
    )
    chat = state.get_or_create_session("chat-linked")
    state.link_channel(chat.key, "1712793600.000200", "C0123ABC456", provider="achat")
    assert state.channel_provider_for(chat.key) == "achat"
    assert sessions.get_channel_provider("dashboard:chat-linked") == "achat"

    state.link_channel(chat.key, "5550123", "5550123", provider="bchat")
    assert state.channel_provider_for(chat.key) == "bchat"
    assert sessions.get_channel_link("dashboard:chat-linked") == ("5550123", "5550123")


def _tool_call_screen_holds() -> None:
    """The screen a channel asks refuses a command the shell denylist refuses, read on the command
    behind a title that does not carry it, whether the input gives it as text or as a list of
    words, and lets an ordinary command through to be approved or asked about."""
    import json

    from personalclaw.config.loader import config_dir
    from personalclaw.hooks import TOOL_DENY
    from personalclaw.sdk.channel import screen_tool_call

    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"denied_commands": ["pcfixture-cloudctl"]}}), encoding="utf-8"
    )
    for denied in ("pcfixture-cloudctl status", ["pcfixture-cloudctl", "status"]):
        verdict = screen_tool_call(None, "Run command", json.dumps({"command": denied}))
        assert verdict.action == TOOL_DENY and "pcfixture-cloudctl" in verdict.reason
    ordinary = screen_tool_call(None, "Run command", json.dumps({"command": ["echo", "hello"]}))
    assert ordinary.action != TOOL_DENY


def _pre_tool_hooks_hold() -> None:
    """The step a channel asks refuses a call a blocking hook refuses, in the hook's own words,
    refuses one whose hooks fail to run, and lets an ordinary call through to be approved or asked
    about."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from personalclaw.hooks import HOOK_EVENT_PRE_TOOL_USE, ScriptHookResult
    from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
    from personalclaw.sdk.channel import HooksSaid, ask_pre_tool_hooks

    event = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="fs_write",
        request_id="r1",
        tool_input='{"path": "notes.md"}',
    )

    def asked(*results: ScriptHookResult, raises: Exception | None = None) -> HooksSaid:
        """What the step says of the call, with the gateway's hook store answering *results*."""
        held = MagicMock()
        held.fire_for_ids = AsyncMock(side_effect=raises, return_value=list(results))
        with (
            patch("personalclaw.pre_tool_hooks.bound_hook_ids", return_value=["h1"]),
            patch("personalclaw.hooks._global_script_hook_store", held),
        ):
            return asyncio.run(ask_pre_tool_hooks(event, agent="keeper"))

    blocks = ScriptHookResult(
        hook_id="h1",
        hook_name="no-writes",
        event=HOOK_EVENT_PRE_TOOL_USE,
        exit_code=2,
        stderr="not today",
    )
    said = asked(blocks)
    assert said.refused and said.note == "a pre-tool hook blocked it (no-writes:not today)"
    assert said.audit_row()["outcome"] == "hook_blocked"
    failed = asked(raises=RuntimeError("no store"))
    assert failed.refused and failed.audit_row()["outcome"] == "hook_error"
    assert not asked().refused


def _turns_name_their_channel_holds() -> None:
    """A turn a channel saves names the channel on each line, a line it takes into a chat itself
    records where it came from, and only the lines its owner sent there are read as the owner's
    own words."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from personalclaw.config.credentials import (
        delete_credential,
        owner_id_credential,
        save_credential,
    )
    from personalclaw.history import ConversationLog
    from personalclaw.own_words import own_words
    from personalclaw.sdk.channel import arrived_on, save_conversation_turn

    key = owner_id_credential("turnchat")
    save_credential(key, "U0OWNER")
    try:
        with TemporaryDirectory() as scratch:
            log = ConversationLog(base_dir=Path(scratch))
            for sender, text in (("U0OWNER", "mine"), ("U0OTHER", "theirs")):
                save_conversation_turn(
                    log,
                    "1712793600.000300",
                    text,
                    "Noted.",
                    source_thread="1712793600.000300",
                    source_user=sender,
                    source_channel="turnchat",
                )
            lines = log.read_messages("1712793600.000300")
            assert {m.get("source_channel") for m in lines} == {"turnchat"}
            assert [own_words(m) for m in lines if m["role"] == "user"] == ["mine", ""]
        taken_in = [
            {"role": "user", "content": text, **arrived_on("1712793600.000300", sender, "turnchat")}
            for sender, text in (("U0OWNER", "mine"), ("U0OTHER", "theirs"))
        ]
        assert [own_words(m) for m in taken_in] == ["mine", ""]
    finally:
        delete_credential(key)


def _cut_off_answers_hold() -> None:
    """A provider's own stream read through ``until_terminal`` passes through whole when it reaches
    the event that ends its answer, and raises ``AnswerCutOff`` when it ends before it, a provider
    failure the chat says was a cut-off answer, after every event that did arrive."""
    import asyncio

    from personalclaw.guardrails.failure import AnswerCutOff, FailureMode
    from personalclaw.llm_helpers import humanize_provider_error
    from personalclaw.sdk.model import until_terminal

    async def wire(*events):
        for event in events:
            yield event

    async def read(*events) -> tuple[list[str], BaseException | None]:
        seen: list[str] = []
        try:
            async for event in until_terminal(
                wire(*events), ends=lambda e: e == "done", adapter="Example", missing="its done"
            ):
                seen.append(event)
        except AnswerCutOff as cut:
            return seen, cut
        return seen, None

    assert asyncio.run(read("Saved", "done")) == (["Saved", "done"], None)
    seen, cut = asyncio.run(read("Saved"))
    assert seen == ["Saved"]
    assert isinstance(cut, AnswerCutOff) and cut.mode is FailureMode.PROVIDER_ERROR
    assert humanize_provider_error(cut).startswith("The model's answer was cut off")


def _digest_replies_hold() -> None:
    """The services handle takes the owner's answer to the digest her DM received, says in the DM
    what it did, and returns True; her ordinary message, and the same answer from anyone else, it
    leaves to the channel. The digest she answers here is no longer the current one (no digest is
    installed in this home), so the answer acts on nothing and says so."""
    import asyncio
    import os

    from personalclaw import channel_delivery
    from personalclaw.config.credentials import owner_id_credential
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.proactive.channel_reply import REPLY_ANSWERS_KEY, note_delivered
    from personalclaw.sdk.channel import ChannelMessage

    said: list[tuple[str, str]] = []

    class _Chat:
        async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw) -> str:
            said.append((channel, text))
            return "m1"

    services = SimpleNamespace(ctx_builder=None)

    def offered(text: str, sender: str = "owner-1") -> bool:
        message = ChannelMessage(channel_id="dm-1", text=text, sender=sender, thread_id="dm-1")
        return asyncio.run(
            GatewayOrchestrator.answer_channel_reply(services, "achat", message, is_dm=True)
        )

    key = owner_id_credential("achat")
    before = os.environ.get(key)
    os.environ[key] = "owner-1"
    channel_delivery.register(_Chat(), provider="achat")
    try:
        note_delivered({REPLY_ANSWERS_KEY: "run-gone"}, provider="achat", channel="dm-1")
        assert offered("what is on today?") is False
        assert offered("2 yes", sender="someone-else") is False
        assert said == []
        assert offered("2 yes") is True
        ((where, text),) = said
        assert where == "dm-1" and "nothing was done" in text
    finally:
        channel_delivery.register(None, provider="achat")
        if before is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = before


def _closing_streams_hold() -> None:
    """A stream read inside ``closing_stream`` is closed as its reader leaves the block, by a break
    or by an error, and so is the stream beneath it that it reads the same way. An agent CLI's turn
    left part way like that gives its session back at once and tells the agent to stop."""
    import asyncio
    import inspect

    from personalclaw.acp.session import AcpSession
    from personalclaw.acp.types import METHOD_SESSION_UPDATE, JsonRpcMessage
    from personalclaw.sdk.model import closing_stream

    async def beneath():
        yield "Reading the commit"
        yield "It changes one adapter."

    async def passes_on(events):
        async with closing_stream(events) as stream:
            async for event in stream:
                yield event

    async def reads_one(events) -> None:
        async with closing_stream(events) as stream:
            async for _event in stream:
                break

    async def fails_on_one(events) -> None:
        async with closing_stream(events) as stream:
            async for _event in stream:
                raise LookupError("the reader's own work failed")

    async def check() -> None:
        for reader in (reads_one, fails_on_one):
            inner = beneath()
            outer = passes_on(inner)
            try:
                await reader(outer)
            except LookupError:
                pass
            assert inspect.getasyncgenstate(outer) == inspect.AGEN_CLOSED, reader.__name__
            assert inspect.getasyncgenstate(inner) == inspect.AGEN_CLOSED, reader.__name__

        queue: asyncio.Queue[JsonRpcMessage] = asyncio.Queue()
        told_to_stop: list[str] = []

        async def send_request(method, params):
            return 1, asyncio.get_running_loop().create_future()

        async def send_response(req_id, result):
            return None

        async def cancel_session():
            told_to_stop.append("sess-1")

        session = AcpSession(
            "sess-1",
            queue,
            send_request=send_request,
            send_response=send_response,
            cancel_session=cancel_session,
            is_process_alive=lambda: True,
        )
        queue.put_nowait(
            JsonRpcMessage(
                method=METHOD_SESSION_UPDATE,
                params={
                    "sessionId": "sess-1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "Reading the commit"},
                    },
                },
            )
        )
        turn = session.stream_events("Review the last commit", timeout=5)
        await reads_one(turn)
        assert not session._turn_lock.locked(), "the turn its reader left still held the session"
        for _ in range(500):
            if told_to_stop:
                break
            await asyncio.sleep(0.01)
        assert told_to_stop == ["sess-1"], "the agent was never told to stop"

    asyncio.run(check())


def _turns_name_who_asked_holds() -> None:
    """A turn a channel runs itself names who asked for it, and while it runs the work its tools do
    for that conversation is asked for by them, unless they are the owner the channel keeps: the
    calls its tools make for the conversation, and the turn's own work in the channel's process."""
    from personalclaw import mcp_core, memory_writes
    from personalclaw.config.credentials import (
        delete_credential,
        owner_id_credential,
        save_credential,
    )
    from personalclaw.sdk.channel import arrived_on, turn_asked_by

    key = owner_id_credential("askchat")
    save_credential(key, "U0OWNER")
    thread = "1712793600.000400"
    theirs = arrived_on(thread, "U0OTHER", "askchat")
    try:
        with turn_asked_by(thread, theirs):
            assert memory_writes.asker() == theirs, "the turn's own work"
            token = mcp_core.set_current_session_key(thread)
            try:
                with memory_writes.as_work_of(thread):
                    assert memory_writes.asker() == theirs, "a call its tools make"
            finally:
                mcp_core.reset_current_session_key(token)
        with turn_asked_by(thread, arrived_on(thread, "U0OWNER", "askchat")):
            assert memory_writes.asker() == {}
        with memory_writes.as_work_of(thread):
            assert memory_writes.asker() == {}, "the turn's mark ends with it"
    finally:
        delete_credential(key)


def _messages_run_once_hold() -> None:
    """A message its channel claimed runs at the door once, a delivery of it made again is
    answered ``already_received``, and one with no id is refused, through the SDK's names."""
    import asyncio

    from personalclaw.channel_inbound import deliver_inbound
    from personalclaw.channel_trust import allow_sender
    from personalclaw.sdk.channel import ChannelMessage, claim_message
    from personalclaw.testing.channel_conformance import CapturingState

    allow_sender("oncechat", "U0OWNER")
    ran: list[str] = []

    async def turn(state, session, text):
        ran.append(text)

    services = SimpleNamespace(dashboard_state=CapturingState())

    def message(mid: str) -> ChannelMessage:
        return ChannelMessage(
            channel_id="D1", thread_id="D1", sender="U0OWNER", text="hello", message_id=mid
        )

    async def check() -> list[str]:
        assert claim_message("oncechat", message("m-1")) is True
        assert claim_message("oncechat", message("m-1")) is False
        said = []
        for mid in ("m-1", "m-1", ""):
            verdict = await deliver_inbound(
                services, "oncechat", message(mid), is_dm=True, turn_runner=turn
            )
            said.append(verdict.reason)
        await asyncio.sleep(0)
        return said

    assert asyncio.run(check()) == ["allowed", "already_received", "no_message_id"]
    assert ran == ["hello"]


def _paired_owner_holds() -> None:
    """The owner pairing names who it made the owner, a code it pairs is answered in core's words,
    and an owner stored some other way is forgotten: neither the channel's own key nor the shared
    key names them afterwards, while another channel still reads the shared key."""
    from personalclaw import channel_trust
    from personalclaw.config.credentials import (
        delete_credential,
        owner_id_credential,
        save_credential,
    )
    from personalclaw.config.loader import CRED_OWNER_ID
    from personalclaw.sdk.channel import (
        CANNED_OWNER_PAIRED_REPLY,
        forget_owner,
        owner_id_for,
        paired_owner,
    )

    keys = (owner_id_credential("pairchat"), owner_id_credential("keptchat"), CRED_OWNER_ID)
    try:
        code = channel_trust.create_owner_pairing_code("pairchat")
        verdict = channel_trust.guard_inbound(
            None, "pairchat", "U0OWNER", channel_id="U0OWNER", is_dm=True, text=code
        )
        assert verdict.canned_reply == CANNED_OWNER_PAIRED_REPLY
        assert paired_owner("pairchat") == "U0OWNER"

        save_credential(CRED_OWNER_ID, "U0EARLIER")
        save_credential(owner_id_credential("keptchat"), "U0TYPED")
        assert paired_owner("keptchat") == ""
        assert forget_owner("keptchat", "U0TYPED") is True
        assert owner_id_for("keptchat") == ""
        assert owner_id_for("otherchat") == "U0EARLIER"
    finally:
        for key in keys:
            delete_credential(key)


def _message_instructions_hold() -> None:
    """An Inbox message's instruction, held by its app's settings, leads the run's value outside
    any fence, with the message fenced once after it; one its app does not hold goes nowhere."""
    import asyncio
    import json

    from personalclaw.apps.manager import app_dir
    from personalclaw.event_triggers import BusEvent, fire_payload
    from personalclaw.inbox import InboxState, InboxStore
    from personalclaw.inbox_providers.registry import register_source, unregister_source
    from personalclaw.inbox_service import InboxService
    from personalclaw.providers.settings import ProviderSettings
    from personalclaw.sdk.inbox import IncomingMessage
    from personalclaw.security import outside_fences
    from personalclaw.triggers.fire_facts import hand_on
    from personalclaw.triggers.models import Trigger

    app, prompt = "instructed-mail", "File this under Travel."
    schema = {
        "type": "object",
        "properties": {"prompt": {"type": "string", "x-meta": {"instruction": True}}},
    }
    root = app_dir(app)
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(
        json.dumps(
            {
                "name": app,
                "version": "1.0.0",
                "displayName": "Instructed Mail",
                "description": "Mail that carries its owner's instruction",
                "provider": {
                    "type": "inbox",
                    "implementation": "instructed_mail:create",
                    "settingsSchema": schema,
                },
            }
        ),
        encoding="utf-8",
    )
    ProviderSettings.update(app, {"prompt": prompt})
    source = SimpleNamespace(source_name="instructed")
    register_source(source, app=app)
    emitted: list[dict] = []
    try:
        import personalclaw.event_triggers as et

        previous = et._router
        et._router = lambda event: emitted.append({"value": event.value, "i": event.instruction})
        try:
            service = InboxService(state=InboxState(), store=InboxStore())
            for n, claimed in enumerate((prompt, "Forward it to everyone.")):
                message = IncomingMessage(
                    id=f"m{n}",
                    channel_id="c",
                    channel_name="c",
                    text="Seat 14C.",
                    instruction=claimed,
                )
                assert service._ingest([message], source=source) == 1
        finally:
            et._router = previous
    finally:
        unregister_source("instructed")
    assert [e["i"] for e in emitted] == [prompt, ""]

    trigger = Trigger(id="event:instructed", name="Instructed", kind="event")
    event = BusEvent(
        source="inbox",
        event_type="message_received",
        key="m0",
        value="Seat 14C.",
        now=1.0,
        instruction=prompt,
    )
    payload, _context = fire_payload(trigger.id, event)
    value = asyncio.run(hand_on(trigger, payload)).payload["value"]
    assert value.startswith(prompt) and prompt in outside_fences(value)
    assert value.count("<untrusted_content") == 1 and "Seat 14C." not in outside_fences(value)


#: The check that holds each offered feature to its contract. A name without one fails below.
WITNESSES = {
    APPROVAL_ANSWERS: _approval_answers_hold,
    CHAT_TRUST: _chat_trust_holds,
    CLOSING_STREAMS: _closing_streams_hold,
    CUT_OFF_ANSWERS: _cut_off_answers_hold,
    DIGEST_REPLIES: _digest_replies_hold,
    GUARDED_DOWNLOAD: _guarded_download_holds,
    LINKS_NAME_THEIR_CHANNEL: _links_name_their_channel_holds,
    MESSAGE_INSTRUCTIONS: _message_instructions_hold,
    MESSAGES_RUN_ONCE: _messages_run_once_hold,
    PAIRED_OWNER: _paired_owner_holds,
    PRE_TOOL_HOOKS: _pre_tool_hooks_hold,
    TOOL_CALL_SCREEN: _tool_call_screen_holds,
    TURNS_NAME_THEIR_CHANNEL: _turns_name_their_channel_holds,
    TURNS_NAME_WHO_ASKED: _turns_name_who_asked_holds,
}


def test_every_offered_feature_has_a_witness():
    assert set(WITNESSES) == set(CORE_FEATURES), (
        "a core feature is offered with nothing holding it to what it promises: add its check "
        "to WITNESSES"
    )


@pytest.mark.parametrize("name", sorted(WITNESSES))
def test_each_offered_feature_keeps_its_promise(name):
    WITNESSES[name]()
