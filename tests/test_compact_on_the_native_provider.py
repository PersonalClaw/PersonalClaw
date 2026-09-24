"""`/compact` is advertised on every provider, so the DEFAULT one has to run it (#470).

The composer's "/" menu and `/help` both list `/compact` from one map
(`chat_utils._SLASH_COMMAND_HINTS`, served by `GET /api/slash-commands`), whose own
docstring says the set "work[s] on any model". On the native provider — the default, and the
only one a fresh install has — it did not:

* `NativeAgentRuntime` overrode neither `supports_native_commands` (base `False`) nor
  `wait_for_compaction` (base `{"type": "timeout"}`), so
* `stream_slash_command`'s axis gate substituted a plain prompt, which asked the MODEL about
  the text "/compact" and got a chat message back — a conversation summary written into the
  transcript instead of a compaction, plus a `slash_fallback` notice, and
* the post-loop deferred branch was then skipped on `slash_substituted`, so even the
  dishonest "Compaction timed out." never arrived.

**Which of the two directions this took, and why.** The measured fact that decides it: the
native loop ALREADY compacts. `runtime._maybe_compact()` runs `context_compaction.compact`
on `self._messages` automatically at `_COMPACT_THRESHOLD_PCT = 70`, so the capability is
built, shipped and load-bearing — `/compact` is just the manual trigger for a pass that
already exists. Un-advertising it would have hidden a working feature behind a
provider-aware hints endpoint (and `GET /api/slash-commands` has no session, so it cannot
even tell which provider is bound). So: wire it.

The seam is one narrow capability, `compacts_in_process` — "this provider owns its own
message list", which is a different question from `supports_native_commands` ("a backend can
be handed a command over the wire"). Native answers True to the first and stays False on the
second, so every OTHER `/…` word is still honestly reported as a plain message.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context_compaction import total_chars
from personalclaw.dashboard.chat_utils import (
    _SLASH_COMMAND_HINTS,
    _broadcast_compaction_result,
    stream_slash_command,
)
from personalclaw.llm.events import EVENT_COMPACTION_STATUS, AgentEvent


def _convo(n_tool_rounds: int, tool_size: int = 2000) -> list[dict]:
    """A history with `n_tool_rounds` verbose tool results — the same shape
    `test_context_compaction.py` builds, so both files measure the same pass."""
    msgs: list[dict] = [{"role": "user", "content": "system + first message"}]
    for i in range(n_tool_rounds):
        msgs.append(
            {
                "role": "assistant",
                "content": f"step {i}",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "bash", "arguments": f'{{"path": "src/f{i}.py"}}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "X" * tool_size})
    msgs.append({"role": "user", "content": "latest request"})
    msgs.append({"role": "assistant", "content": "latest reply"})
    return msgs


def _runtime() -> NativeAgentRuntime:
    class _Model:
        supports_tools = True
        _model = "s"

    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="s"),
        model_provider=_Model(),
        tool_providers=[],
    )


async def _drain(stream) -> list[AgentEvent]:
    return [ev async for ev in stream]


# ── the capability, and the two questions it must not collapse ────────────────


class TestTheCapabilityIsNarrow:
    def test_native_compacts_in_process(self):
        assert _runtime().compacts_in_process is True

    def test_native_still_declares_no_wire_command_axis(self):
        """The two flags are orthogonal and the SECOND must stay False. Flipping
        `supports_native_commands` would have been the shorter fix and the wrong one: every
        other slash word would then reach `stream_command`, whose base implementation is a
        plain prompt wearing a command's name — the exact `G4` lie, with the notice
        suppressed because a substitution no longer happened."""
        assert _runtime().supports_native_commands is False

    def test_both_ABCs_default_to_False(self):
        """Declared on both method-compatible ABCs, like `supports_native_commands` — a
        provider that owns nothing must not inherit a True from either side."""
        from personalclaw.agents.provider import AgentProvider
        from personalclaw.llm.base import ModelProvider

        for abc in (AgentProvider, ModelProvider):
            assert abc.compacts_in_process.fget(object()) is False, abc.__name__


# ── the pass itself, through the command ──────────────────────────────────────


class TestTheCommandRunsTheRealPass:
    @pytest.mark.asyncio
    async def test_a_long_history_is_compacted_and_reports_before_after(self):
        rt = _runtime()
        rt._messages = _convo(10)
        before = total_chars(rt._messages)
        events = await _drain(rt.stream_command("/compact"))
        assert total_chars(rt._messages) < before, "the history itself must shrink"
        assert [e.kind for e in events] == [EVENT_COMPACTION_STATUS]
        assert events[0].text == "completed"
        # The summary is a MEASUREMENT, not prose a model wrote: the old behaviour's whole
        # failure was that a plausible-looking summary appeared while nothing was compacted.
        assert "characters" in events[0].title and "→" in events[0].title

    @pytest.mark.asyncio
    async def test_a_short_history_reports_noop_not_success(self):
        """`cc.compact` returns the list unchanged when there is no middle to fold. That is
        the common case for a fresh chat — the first thing anyone testing this will hit — so
        it must not claim "Conversation compacted."."""
        rt = _runtime()
        rt._messages = [{"role": "user", "content": "hello"}]
        events = await _drain(rt.stream_command("/compact"))
        assert [e.kind for e in events] == [EVENT_COMPACTION_STATUS]
        assert events[0].text == "noop"
        assert rt._messages == [{"role": "user", "content": "hello"}]

    @pytest.mark.asyncio
    async def test_any_other_command_is_still_the_plain_prompt_fallback(self):
        """`stream_command` is public; only `/compact` is special-cased, and the rest keep
        the base contract rather than silently compacting."""
        rt = _runtime()
        sent: list[str] = []

        async def _stream(message: str):
            sent.append(message)
            if False:  # pragma: no cover - an empty async generator
                yield AgentEvent(kind="text_chunk")

        rt.stream = _stream  # type: ignore[method-assign]
        await _drain(rt.stream_command("/context"))
        assert sent == ["/context"]


class TestTheExplicitPathHasNoTriggerPolicy:
    @pytest.mark.asyncio
    async def test_compact_ignores_the_threshold(self):
        """`_maybe_compact` refuses under 70%; a person who typed `/compact` has decided."""
        rt = _runtime()
        rt._messages = _convo(10)
        rt._last_context_pct = 20.0
        before = total_chars(rt._messages)
        await rt.compact()
        assert total_chars(rt._messages) < before

    @pytest.mark.asyncio
    async def test_an_explicit_press_cannot_latch_off_automatic_compaction(self):
        """🪤 The regression this file exists to pin. `should_compact` refuses when the last
        TWO recorded saves each reclaimed <10%, and a skipped pass records nothing — so if an
        explicit press appended its (truthful) 0%, two clicks on a short chat would disable
        threshold compaction for the rest of the session with no way to clear it, and history
        would grow unbounded until the model broke. The saves list belongs to the automatic
        trigger alone."""
        rt = _runtime()
        rt._messages = [{"role": "user", "content": "hello"}]
        await rt.compact()
        await _drain(rt.stream_command("/compact"))
        assert rt._compaction_saves == []

        # …and the automatic path still both fires and records.
        rt._messages = _convo(10)
        rt._last_context_pct = 85.0
        rt._maybe_compact()
        assert rt._compaction_saves and rt._compaction_saves[0] > 0


# ── the dispatch decision ─────────────────────────────────────────────────────


class _Client:
    """The two capability axes, settable, plus a recorder for which path ran."""

    def __init__(self, *, in_process: bool = False, wire: bool = False) -> None:
        self.compacts_in_process = in_process
        self.supports_native_commands = wire
        self.streamed: list[str] = []
        self.commanded: list[str] = []

    async def stream(self, message: str):
        self.streamed.append(message)
        yield AgentEvent(kind="text_chunk", text="plain answer")

    async def stream_command(self, command: str):
        self.commanded.append(command)
        yield AgentEvent(kind=EVENT_COMPACTION_STATUS, text="noop")


class TestStreamSlashCommandRoutesCompact:
    @pytest.mark.asyncio
    async def test_in_process_compaction_runs_the_command_and_says_nothing(self):
        """No notice: the command really ran, so there is nothing to disclose. A notice here
        would be the mirror of the original defect — telling the user it degraded when it
        didn't."""
        c = _Client(in_process=True)
        notices: list[str] = []
        await _drain(stream_slash_command(c, "/compact", prompt="/compact", notify=notices.append))
        assert c.commanded == ["/compact"] and c.streamed == []
        assert notices == []

    @pytest.mark.asyncio
    async def test_the_in_process_check_comes_before_the_axis_gate(self):
        """Order is the fix. Native answers False to `supports_native_commands`, so had the
        axis gate stayed first, `/compact` would still fall into the substitution."""
        c = _Client(in_process=True, wire=False)
        await _drain(stream_slash_command(c, "/compact", prompt="p", notify=lambda _t: None))
        assert c.commanded == ["/compact"]

    @pytest.mark.asyncio
    async def test_a_provider_that_can_do_neither_still_substitutes_and_says_so(self):
        c = _Client()
        notices: list[str] = []
        await _drain(stream_slash_command(c, "/compact", prompt="p", notify=notices.append))
        assert c.streamed == ["p"] and c.commanded == []
        assert notices and "plain message" in notices[0]

    @pytest.mark.asyncio
    async def test_other_commands_are_unaffected_by_the_new_branch(self):
        """The branch is keyed on `/compact`, not on the capability alone: a runtime that
        compacts in-process still cannot run `/context` as a command."""
        c = _Client(in_process=True)
        notices: list[str] = []
        await _drain(stream_slash_command(c, "/context", prompt="p", notify=notices.append))
        assert c.streamed == ["p"] and c.commanded == []
        assert notices


# ── the sentence (a server-composed sentence is a UI surface) ─────────────────


class TestTheReportedSentence:
    @pytest.mark.parametrize(
        ("status", "title", "expected"),
        [
            ("completed", "freed 40% (100 → 60 characters)", "Conversation compacted: freed 40%"),
            ("completed", "", "Conversation compacted."),
            ("noop", "", "Nothing to compact — this conversation is already short enough."),
            ("failed", "disk full", "Compaction failed: disk full"),
        ],
    )
    def test_each_terminal_status_has_its_own_sentence(self, status, title, expected):
        session = _FakeSession()
        state = _FakeState()
        text = _broadcast_compaction_result(
            state, session, AgentEvent(kind=EVENT_COMPACTION_STATUS, text=status, title=title)
        )
        assert text is not None and text.startswith(expected)
        assert session.appended and session.appended[0][1] == text

    def test_an_unknown_status_is_not_reported_at_all(self):
        """The `None` return is what tells the chat runner `saw_compaction` did NOT happen,
        so an in-progress frame must not consume the outcome."""
        assert (
            _broadcast_compaction_result(
                _FakeState(),
                _FakeSession(),
                AgentEvent(kind=EVENT_COMPACTION_STATUS, text="started"),
            )
            is None
        )


class _FakeSession:
    key = "s1"

    def __init__(self) -> None:
        self.appended: list[tuple[str, str, str]] = []

    def append(self, role: str, content: str, cls: str) -> None:
        self.appended.append((role, content, cls))


class _FakeState:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    def broadcast_ws(self, kind: str, payload: Any) -> None:
        self.sent.append((kind, payload))


# ── the advertisement, tied to the capability ─────────────────────────────────


def test_the_command_the_menu_advertises_is_the_one_the_default_provider_runs():
    """`_SLASH_COMMAND_HINTS` is documented as commands that "work regardless of the bound
    model". This is the assertion that keeps that claim true for the default provider rather
    than leaving it to prose."""
    assert "/compact" in _SLASH_COMMAND_HINTS
    assert _runtime().compacts_in_process is True


# ── the turn a user actually drives ───────────────────────────────────────────


class TestTurnLevel:
    """End to end through ``run_chat``, because every layer above was already "correct" while
    the feature did not work: the hint map listed it, the composer offered it, the dispatch
    ran, the post-loop branch existed — and a user still got a chat message. Only the whole
    turn can assert otherwise."""

    @pytest.mark.asyncio
    async def test_typing_compact_compacts_and_reports_it(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from chat_test_helpers import _make_state

        import personalclaw.trust_mode as _tm
        from personalclaw.dashboard.chat_runner import run_chat

        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        state.broadcast_ws = MagicMock()
        state.push_sessions_update = MagicMock()
        state.context_builder = None
        state.consolidator = None
        state._hook_store = None
        _tm.disable_yolo()

        rt = _runtime()
        rt._messages = _convo(10)
        before = total_chars(rt._messages)
        # The runtime is real; only the session factory and the model-free surfaces are faked.
        client = AsyncMock()
        client.compacts_in_process = rt.compacts_in_process
        client.supports_native_commands = rt.supports_native_commands
        client.stream_command = rt.stream_command
        client.context_usage_pct = MagicMock(return_value=10.0)

        async def _never(message):  # pragma: no cover — no model call may happen
            raise AssertionError("the MODEL was asked about the text '/compact'")
            yield

        client.stream = _never
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        session = state.get_or_create_session("compact-native")
        await run_chat(state, session, "/compact")

        assert total_chars(rt._messages) < before, "the history must actually shrink"
        roles = [m.get("role") for m in session.messages]
        assert "error" not in roles, f"the turn hard-errored: {session.messages}"
        said = [
            m.get("content", "")
            for m in session.messages
            if m.get("role") == "assistant" and m.get("content")
        ]
        assert any(str(s).startswith("Conversation compacted:") for s in said), said
        # 🪤 The deferred branch must NOT also run. It wipes the streamed chunks and waits on
        # `wait_for_compaction`, whose base default is `{"type": "timeout"}` — so reaching it
        # would overwrite the true outcome above with "Compaction timed out."
        client.wait_for_compaction.assert_not_awaited()
        assert not any("timed out" in str(s) for s in said)
        # And no substitution notice, because nothing was substituted.
        assert not [
            c
            for c in state.broadcast_ws.call_args_list
            if c.args
            and c.args[0] == "activity_event"
            and c.args[1].get("kind") == "slash_fallback"
        ]
