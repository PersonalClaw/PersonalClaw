"""`G4` — a slash command an agent cannot run must ANSWER, not hard-error the turn.

The measured defect (ACP-AGENT-PARITY `O23`): the dashboard sent
``_vendor.dev/commands/execute`` for any ``/word`` message with no capability check at all.
claude-code adapter 0.60.0 does not implement that method, answered JSON-RPC ``-32601``,
and the turn died with ``Prompt error: {'code': -32601, …}`` — the plain-prompt fallback the
audit assumed *did not exist*.

The contract these tests pin, one file because it is one decision:

* the flag is READ off the ``initialize`` handshake (same shape as ``loadSession``), and it
  is an allowlist — silence means "do not send";
* an unadvertised command produces NO wire frame, so nothing can fail;
* ``-32601`` is TYPED, so only "this agent cannot" can trigger a substitution — every other
  JSON-RPC error still surfaces;
* the substitution runs only while the turn has produced NOTHING. After output it is
  refused, because re-issuing would duplicate the answer and bill the turn twice;
* and the substitution is SAID OUT LOUD — a user who typed ``/compact`` and silently got a
  plain-prompt answer was handed a different thing than they asked for.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from chat_test_helpers import _make_state

from personalclaw.acp.errors import (
    AcpCommandFailedAfterOutput,
    AcpCommandsUnsupported,
    AcpError,
    AcpMethodNotFound,
)
from personalclaw.acp.types import CAP_COMMANDS, METHOD_COMMANDS_EXECUTE
from personalclaw.constants import JSONRPC_METHOD_NOT_FOUND
from personalclaw.dashboard.chat_utils import (
    SLASH_FALLBACK_ACTIVITY_KIND,
    stream_slash_command,
)
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

# The capability set adapter 0.60.0 really advertised (`O1`, verbatim). Used as the
# NEGATIVE fixture: none of these seven keys is a command capability, which is why the
# gate closes for the provider the audit marked WIRED.
_CAPS_0_60_0 = {
    "_meta": {},
    "auth": {},
    "loadSession": True,
    "mcpCapabilities": {},
    "promptCapabilities": {},
    "providers": {},
    "sessionCapabilities": {},
}


# ── wire harness (scripted stdout + a recording stdin) ────────────────────────
class _ScriptedStdout:
    """Pre-scripted JSON-RPC frames, then a brief idle and EOF (a real pipe does not
    EOF the instant a turn's frames are written)."""

    def __init__(self, frames: list[dict]):
        self._lines = [(json.dumps(f) + "\n").encode() for f in frames]
        self._i = 0

    async def readline(self) -> bytes:
        if self._i >= len(self._lines):
            await asyncio.sleep(0.2)
            return b""
        line = self._lines[self._i]
        self._i += 1
        return line


class _RecordingTransport:
    """Reads the script; RECORDS every frame written. The recording is the census that
    makes "no request was sent" an assertion rather than a hope."""

    def __init__(self, frames: list[dict]):
        self._out = _ScriptedStdout(frames)
        self.writes: list[str] = []

    async def readline(self) -> bytes:
        return await self._out.readline()

    async def write(self, data: str) -> None:
        self.writes.append(data)

    def is_alive(self) -> bool:
        return True


def _connection(frames: list[dict]):
    """A live AcpConnection over the scripted transport, reader started."""
    from personalclaw.acp.reader import FrameRouter
    from personalclaw.acp.session import AcpConnection

    transport = _RecordingTransport(frames)
    router = FrameRouter(transport.readline)
    conn = AcpConnection(None, router, transport=transport)
    router.start()
    return conn, transport


# ── 1. the capability comes from the handshake, and silence means no ──────────
class TestCapabilityIsRead:
    @pytest.mark.asyncio
    async def test_the_measured_0_60_0_capability_set_closes_the_gate(self):
        conn, _ = _connection([{"id": 1, "result": {"agentCapabilities": _CAPS_0_60_0}}])
        caps = await conn.initialize({}, timeout=5)
        # The flag is derived from what the agent SAID, exactly like `loadSession` — and
        # `loadSession` being true here proves the fixture is a real capability set, so a
        # False command flag is a measurement, not an empty dict.
        assert caps["loadSession"] is True
        assert conn.supports_native_commands is False

    @pytest.mark.asyncio
    async def test_an_agent_that_advertises_commands_opens_the_gate(self):
        conn, _ = _connection(
            [{"id": 1, "result": {"agentCapabilities": {**_CAPS_0_60_0, CAP_COMMANDS: True}}}]
        )
        await conn.initialize({}, timeout=5)
        assert conn.supports_native_commands is True

    @pytest.mark.asyncio
    async def test_the_flag_is_false_before_any_handshake(self):
        conn, _ = _connection([])
        # A command sent to a process that has not spoken yet is the same unguarded send.
        assert conn.supports_native_commands is False


# ── 2. an unadvertised command puts NOTHING on the wire ──────────────────────
class TestNoFrameWithoutAdvertisement:
    @pytest.mark.asyncio
    async def test_client_stream_command_sends_no_frame_and_refuses(self):
        """The load-bearing one. The script answers ANY second request with the exact
        ``-32601`` adapter 0.60.0 returned, so removing the gate in
        ``AcpClient.stream_command`` does not merely flip a flag — the turn hard-errors
        with :class:`AcpMethodNotFound`, which is `O23` reproduced."""
        from unittest.mock import AsyncMock

        from personalclaw.acp.client import AcpClient

        conn, transport = _connection(
            [
                {"id": 1, "result": {"agentCapabilities": _CAPS_0_60_0}},
                {
                    "id": 2,
                    "error": {
                        "code": JSONRPC_METHOD_NOT_FOUND,
                        "message": '"Method not found": _vendor.dev/commands/execute',
                    },
                },
            ]
        )
        await conn.initialize({}, timeout=5)
        client = AcpClient()
        client._connection = conn
        client._session = conn._bind_session("sess-g4")
        client._session_id = "sess-g4"
        client._can_execute_commands = conn.supports_native_commands
        client.ensure_ready = AsyncMock()

        before = len(transport.writes)
        with pytest.raises(AcpCommandsUnsupported):
            [ev async for ev in client.stream_command("/compact")]
        assert transport.writes[before:] == [], (
            "a command frame reached an agent that never advertised commands — "
            "that write is what -32601s the whole turn"
        )
        assert not any(METHOD_COMMANDS_EXECUTE in w for w in transport.writes)

    @pytest.mark.asyncio
    async def test_session_provider_refuses_the_same_way(self):
        """The concurrent path reads the SAME derivation, so a co-tenant session cannot
        disagree with the N=1 client about what the process can do."""
        from personalclaw.llm.acp_session_provider import AcpSessionProvider

        conn, transport = _connection([{"id": 1, "result": {"agentCapabilities": _CAPS_0_60_0}}])
        await conn.initialize({}, timeout=5)
        session = conn._bind_session("sess-g4b")
        provider = AcpSessionProvider(conn, session, runtime_id="acp:g4", model="m", agent_name="a")
        assert provider.supports_native_commands is False
        before = len(transport.writes)
        with pytest.raises(AcpCommandsUnsupported):
            [ev async for ev in provider.stream_command("/compact")]
        assert transport.writes[before:] == []

    @pytest.mark.asyncio
    async def test_send_command_degrades_to_empty_instead_of_raising(self):
        """``compact()`` drives ``send_command`` fire-and-forget, and the provider
        contract calls compaction a no-op where it isn't supported. So the refusal is
        absorbed HERE and nowhere else — ``stream_command`` still raises."""
        from unittest.mock import AsyncMock

        from personalclaw.acp.client import AcpClient

        client = AcpClient()
        client.ensure_ready = AsyncMock()
        client._can_execute_commands = False
        assert await client.send_command("/compact") == ""


# ── 3. -32601 is typed; every other error stays a real failure ────────────────
class TestOnlyMethodNotFoundIsSpecial:
    async def _drive_command(self, error: dict) -> list:
        from personalclaw.acp.session import AcpSession

        conn, _ = _connection([{"id": 1, "error": error}])
        session = conn._bind_session("sess-err")
        assert isinstance(session, AcpSession)
        return [ev async for ev in session.stream_command("/compact", timeout=5)]

    @pytest.mark.asyncio
    async def test_minus_32601_raises_the_typed_error_naming_the_method(self):
        with pytest.raises(AcpMethodNotFound) as caught:
            await self._drive_command(
                {"code": JSONRPC_METHOD_NOT_FOUND, "message": '"Method not found"'}
            )
        assert caught.value.method == METHOD_COMMANDS_EXECUTE
        assert caught.value.code == JSONRPC_METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_any_other_code_is_not_a_method_not_found(self):
        """The swallowing guard. If this error typed as "cannot", the caller would answer
        a genuine mid-command failure by silently re-asking as a prompt."""
        with pytest.raises(AcpError) as caught:
            await self._drive_command({"code": -32000, "message": "boom"})
        assert not isinstance(caught.value, AcpMethodNotFound)
        assert "boom" in str(caught.value)


# ── 4. the three dispatch outcomes ───────────────────────────────────────────
class _FakeProvider:
    """A provider that answers a prompt, and optionally fails a command."""

    def __init__(self, *, supports: bool, command_events=(), command_error=None):
        self.supports_native_commands = supports
        self._command_events = list(command_events)
        self._command_error = command_error
        self.prompts: list[str] = []
        self.commands: list[str] = []

    async def stream(self, message: str):
        self.prompts.append(message)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ANSWER")
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def stream_command(self, command: str):
        self.commands.append(command)
        for ev in self._command_events:
            yield ev
        if self._command_error is not None:
            raise self._command_error


async def _dispatch(provider, command="/compact", prompt="/compact"):
    notices: list[str] = []
    events = [
        ev
        async for ev in stream_slash_command(
            provider, command, prompt=prompt, notify=notices.append
        )
    ]
    return events, notices


class TestDispatchOutcomes:
    @pytest.mark.asyncio
    async def test_unadvertised_command_is_answered_as_a_plain_prompt(self):
        provider = _FakeProvider(supports=False)
        events, notices = await _dispatch(provider)
        # The user asked a question and got an answer. This is the whole gap: `O23`
        # produced an error card here.
        assert [e.text for e in events if e.kind == EVENT_TEXT_CHUNK] == ["ANSWER"]
        assert provider.prompts == ["/compact"]
        assert provider.commands == [], "nothing may be dispatched as a command"
        assert len(notices) == 1 and "/compact" in notices[0]

    @pytest.mark.asyncio
    async def test_method_not_found_before_any_output_substitutes(self):
        provider = _FakeProvider(
            supports=True, command_error=AcpMethodNotFound(METHOD_COMMANDS_EXECUTE)
        )
        events, notices = await _dispatch(provider)
        assert [e.text for e in events if e.kind == EVENT_TEXT_CHUNK] == ["ANSWER"]
        assert provider.commands == ["/compact"] and provider.prompts == ["/compact"]
        assert len(notices) == 1

    @pytest.mark.asyncio
    async def test_method_not_found_after_output_refuses_to_duplicate(self):
        """The subtle half. The command streamed a chunk and THEN 404'd. Re-issuing would
        append a second answer to the same assistant message and bill the turn twice, so
        the turn stops with an explanation instead."""
        provider = _FakeProvider(
            supports=True,
            command_events=[LLMEvent(kind=EVENT_TEXT_CHUNK, text="PARTIAL")],
            command_error=AcpMethodNotFound(METHOD_COMMANDS_EXECUTE),
        )
        seen: list[LLMEvent] = []
        notices: list[str] = []
        raised: Exception | None = None
        # Caught by hand, not via `pytest.raises`: the assertions that matter are about
        # what the turn PRODUCED, and a `raises` block would short-circuit past them, so a
        # regression would red on "DID NOT RAISE" instead of on the duplication itself.
        try:
            async for ev in stream_slash_command(
                provider, "/compact", prompt="/compact", notify=notices.append
            ):
                seen.append(ev)
        except AcpCommandFailedAfterOutput as exc:
            raised = exc
        assert provider.prompts == [], "a post-output fallback DUPLICATED the turn"
        assert [e.text for e in seen] == ["PARTIAL"], "partial output is kept, not replayed"
        assert notices == [], "nothing was substituted, so nothing may claim it was"
        # The refusal is legible to the user, not a raw JSON-RPC blob.
        assert raised is not None, "a command that failed mid-turn must not pass silently"
        assert "duplicate" in str(raised) and "/compact" in str(raised)

    @pytest.mark.asyncio
    async def test_a_different_error_is_never_substituted(self):
        """A real failure must still be a failure. Widening the match to ``AcpError``
        would answer a broken command with a plausible-looking essay about its name."""
        provider = _FakeProvider(
            supports=True, command_error=AcpError("Prompt error: {'code': -32000}")
        )
        events: list[LLMEvent] = []
        notices: list[str] = []
        raised: Exception | None = None
        try:
            async for ev in stream_slash_command(
                provider, "/compact", prompt="/compact", notify=notices.append
            ):
                events.append(ev)
        except AcpError as exc:
            raised = exc
        # Asserted on the OUTCOME first, so a widened `except` reds on the swallowing
        # rather than on a missing raise.
        assert provider.prompts == [], "a -32000 failure was SWALLOWED and answered as a prompt"
        assert notices == [] and events == []
        assert raised is not None and not isinstance(raised, AcpCommandFailedAfterOutput)
        assert "-32000" in str(raised)

    @pytest.mark.asyncio
    async def test_an_advertised_command_still_runs_natively(self):
        """The gate must not swallow the feature it protects."""
        provider = _FakeProvider(
            supports=True, command_events=[LLMEvent(kind=EVENT_TEXT_CHUNK, text="usage report")]
        )
        events, notices = await _dispatch(provider)
        assert [e.text for e in events] == ["usage report"]
        assert provider.prompts == [] and notices == []


# ── 5. the provider contract + the wrapper that must not shadow it ────────────
class TestProviderContract:
    def test_a_provider_with_no_command_axis_says_so(self):
        from personalclaw.llm.base import ModelProvider

        class _Native(ModelProvider):
            """A provider with no command axis at all — the native loop's shape."""

            async def start(self) -> None: ...

            async def shutdown(self) -> None: ...

            async def stream(self, message: str):  # pragma: no cover — never driven
                yield LLMEvent(kind=EVENT_COMPLETE)

            async def approve_tool(self, request_id) -> None: ...

            async def reject_tool(self, request_id) -> None: ...

            def context_usage_pct(self) -> float:
                return 0.0

        assert _Native().supports_native_commands is False

    def test_the_guard_reports_the_inner_providers_answer(self, tmp_path, monkeypatch):
        """``ModelCallGuard.__getattr__`` only fires on a lookup MISS, and
        ``ModelProvider`` declares this property — so without an explicit pass-through the
        wrapper answers the ABC's False for an agent that has commands, and every slash
        command silently degrades to text."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        from personalclaw.guardrails.model_call import ModelCallGuard

        def _wrap(inner):
            return ModelCallGuard(inner, use_case="chat", provider_name="acp:test", model="m")

        assert _wrap(_FakeProvider(supports=True)).supports_native_commands is True
        assert _wrap(_FakeProvider(supports=False)).supports_native_commands is False


# ── 6. the turn a user actually drives ───────────────────────────────────────
class TestTurnLevel:
    @pytest.mark.asyncio
    async def test_slash_turn_answers_and_announces_instead_of_erroring(
        self, tmp_path, monkeypatch
    ):
        """End to end through ``_run_chat``: `/compact` on a provider with no command axis
        lands an ASSISTANT message (not the `O23` error card) and broadcasts the
        substitution on the activity channel CE2-8 established."""
        from unittest.mock import AsyncMock, MagicMock

        import personalclaw.trust_mode as _tm

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.context_builder = None
        state.consolidator = None
        state._hook_store = None
        _tm.disable_yolo()

        client = AsyncMock()
        client.context_usage_pct = MagicMock(return_value=10.0)
        client.supports_native_commands = False
        provider = _FakeProvider(supports=False)
        client.stream = provider.stream

        async def _never(command):  # pragma: no cover — the gate must not reach this
            raise AssertionError("a command was dispatched to a provider that has none")
            yield

        client.stream_command = _never
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        session = state.get_or_create_session("g4")
        from personalclaw.dashboard.chat_runner import _run_chat

        await _run_chat(state, session, "/compact")

        roles = [m.get("role") for m in session.messages]
        assert "error" not in roles, f"the turn hard-errored: {session.messages}"
        assert any(
            m.get("role") == "assistant" and "ANSWER" in str(m.get("content", ""))
            for m in session.messages
        ), f"no answer reached the user: {session.messages}"
        assert provider.prompts == ["/compact"]
        notices = [
            call.args[1]
            for call in state.broadcast_ws.call_args_list
            if call.args
            and call.args[0] == "activity_event"
            and call.args[1].get("kind") == SLASH_FALLBACK_ACTIVITY_KIND
        ]
        assert len(notices) == 1, "the user must be told the command was not run natively"
        assert "/compact" in notices[0]["text"]
        # DISCOVERY: `/compact` has a post-turn branch that wipes the streamed chunks and
        # waits up to 120 s for a compaction the provider will never fire. Gated on the
        # substitution, or the fix would trade an error card for a stall that ends by
        # announcing "Compaction timed out." and discarding the answer above.
        client.wait_for_compaction.assert_not_awaited()
