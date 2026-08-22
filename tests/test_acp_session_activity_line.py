"""ACP-AGENT-PARITY `G14` + `G15` — the session activity line must tell the truth.

One sentence, two lies, fixed together:

* `G14` — the line named the launch command's BASENAME (``via
  acp:claude-agent-acp``, or ``via acp:npx`` under the npx fallback) instead of
  the runtime the user actually picked (``acp:claude-code``).
* `G15` — the verb was gated on ``resumed`` alone, and ``get_or_create``'s reuse
  path returns ``resumed=False`` unconditionally, so every turn of a long-lived
  session claimed "Session created".

Every test here asserts the RENDERED SENTENCE broadcast on the wire, not a flag:
a boolean assertion would have passed throughout both defects. The runtime-label
tests read ``provider_id`` off a REAL :class:`AcpAgentProvider` built through the
registry factory (not a hand-set string), so reverting the property reds the
sentence.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard.chat_runner import _run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.acp_agent import AcpAgentProvider, _factory
from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent
from personalclaw.llm.registry import ProviderEntry

# The measured `O7` shape: the claude-code bundle registers the entry as
# ``acp:claude-code`` but launches the ADAPTER, whose basename is
# ``claude-agent-acp`` — and under the npx fallback, ``npx``.
ADAPTER_ARGV = ["/opt/homebrew/bin/claude-agent-acp"]
NPX_ARGV = ["npx", "-y", "@zed-industries/claude-code-acp"]


def _entry(name: str, command: list[str]) -> ProviderEntry:
    return ProviderEntry(
        name=name,
        type="acp_agent",
        model="",
        options={"command": list(command), "dialect": "claude-code"},
    )


def _built(name: str, command: list[str]) -> AcpAgentProvider:
    """A real provider, built the way the registry builds one."""
    provider = _factory(entry=_entry(name, command))
    assert isinstance(provider, AcpAgentProvider)
    return provider


# ── G14: the runtime label names the runtime the user picked ─────────────────


class TestRuntimeLabelNamesTheConfiguredRuntime:
    """`G14` — ``provider_id`` is the configured entry name, not an inference."""

    def test_adapter_basename_does_not_leak_into_the_runtime_id(self):
        assert _built("acp:claude-code", ADAPTER_ARGV).provider_id == "acp:claude-code"

    def test_npx_fallback_does_not_degrade_to_acp_npx(self):
        """The gap's named regression: an npx launch must not read ``acp:npx``."""
        provider = _built("acp:claude-code", NPX_ARGV)
        assert provider.provider_id == "acp:claude-code"
        assert provider.provider_id != "acp:npx"

    def test_basename_inference_survives_only_as_the_fallback(self):
        """A provider built without a configured id keeps the old derivation.

        The inference is not deleted — it is demoted. This is the inverse floor:
        the fix is "prefer the configured id", not "never infer".
        """
        assert AcpAgentProvider(command=ADAPTER_ARGV).provider_id == "acp:claude-agent-acp"
        assert AcpAgentProvider(command=NPX_ARGV).provider_id == "acp:npx"

    def test_acp_prefix_is_an_invariant_not_decoration(self):
        """``chat_runner`` derives the not-gateable/SEL provider key as
        ``provider_id[4:]`` after a ``startswith("acp:")`` test, so an entry name
        missing the prefix must be prefixed rather than passed through — dropping
        it would silently disable the ungated-tool report."""
        assert _built("claude-code", ADAPTER_ARGV).provider_id == "acp:claude-code"

    def test_agrees_with_the_pooled_provider_on_the_same_runtime(self):
        """One runtime id, one value. ``AcpSessionProvider`` already returned the
        configured ``runtime_id``; the two classes must not disagree."""
        from personalclaw.llm.acp_session_provider import AcpSessionProvider

        built = _built("acp:claude-code", NPX_ARGV)
        pooled = AcpSessionProvider.__new__(AcpSessionProvider)
        pooled._runtime_id = "acp:claude-code"
        assert built.provider_id == pooled.provider_id


# ── the harness: one turn, capture the broadcast sentence ────────────────────


async def _async_iter(items):
    for item in items:
        yield item


def _state(tmp_path, *, provider_id: str, is_new: bool, resumed: bool):
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = provider_id
    sessions.get_or_create = AsyncMock(return_value=(client, is_new, resumed))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = None
    state.context_builder = cb
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _async_iter(
            [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]
        )
    )
    return state, client


def _session_lines(state) -> list[str]:
    """Every ``kind: "session"`` activity sentence broadcast this turn."""
    out = []
    for call in state.broadcast_ws.call_args_list:
        if not call.args or call.args[0] != "activity_event":
            continue
        payload = call.args[1] if len(call.args) > 1 else {}
        if isinstance(payload, dict) and payload.get("kind") == "session":
            out.append(str(payload.get("text") or ""))
    return out


async def _one_turn(tmp_path, *, provider_id: str, is_new: bool, resumed: bool) -> list[str]:
    state, _client = _state(tmp_path, provider_id=provider_id, is_new=is_new, resumed=resumed)
    session = _ChatSession("chat-1-g1415")
    session._trust = True
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await _run_chat(state, session, "hello")
    return _session_lines(state)


# ── G14 at the call site: the sentence itself ───────────────────────────────


class TestRenderedSentenceNamesTheRuntime:
    """The label reaches the wire. Composed end-to-end from a REAL provider's
    ``provider_id`` so a revert of the property reds *this sentence*."""

    @pytest.mark.asyncio
    async def test_sentence_names_the_configured_runtime_not_the_adapter(self, tmp_path):
        provider_id = _built("acp:claude-code", ADAPTER_ARGV).provider_id
        lines = await _one_turn(tmp_path, provider_id=provider_id, is_new=True, resumed=False)
        assert lines == ["Session created · default · auto · via acp:claude-code"], lines
        assert "claude-agent-acp" not in lines[0]

    @pytest.mark.asyncio
    async def test_sentence_under_the_npx_fallback_never_says_acp_npx(self, tmp_path):
        provider_id = _built("acp:claude-code", NPX_ARGV).provider_id
        lines = await _one_turn(tmp_path, provider_id=provider_id, is_new=True, resumed=False)
        assert lines == ["Session created · default · auto · via acp:claude-code"], lines
        assert "npx" not in lines[0]


# ── G15: created vs resumed vs continued ────────────────────────────────────


class TestSentenceVerbMatchesWhatHappened:
    """`G15` — the verb must describe the turn, and each verb needs its inverse
    floor so the rail reads as a requirement rather than one hard-coded word."""

    @pytest.mark.asyncio
    async def test_a_started_runner_with_a_fresh_conversation_says_created(self, tmp_path):
        lines = await _one_turn(tmp_path, provider_id="acp:claude-code", is_new=True, resumed=False)
        assert lines == ["Session created · default · auto · via acp:claude-code"], lines

    @pytest.mark.asyncio
    async def test_a_loaded_session_says_resumed_not_created(self, tmp_path):
        lines = await _one_turn(tmp_path, provider_id="acp:claude-code", is_new=True, resumed=True)
        assert lines == ["Session resumed · default · auto · via acp:claude-code"], lines
        assert "created" not in lines[0]

    @pytest.mark.asyncio
    async def test_a_reused_live_session_says_continued_not_created(self, tmp_path):
        """The measured `G15` symptom: every later turn of one conversation takes
        the reuse path (``is_new=False, resumed=False``) and used to claim
        "Session created"."""
        lines = await _one_turn(
            tmp_path, provider_id="acp:claude-code", is_new=False, resumed=False
        )
        assert lines == ["Session continued · default · auto · via acp:claude-code"], lines
        assert "created" not in lines[0]
        assert "resumed" not in lines[0]

    @pytest.mark.asyncio
    async def test_the_native_runtime_gets_the_same_three_verbs(self, tmp_path):
        """The line is not ACP-only — the same sentence labels a native turn."""
        created = await _one_turn(tmp_path, provider_id="native", is_new=True, resumed=False)
        continued = await _one_turn(tmp_path, provider_id="native", is_new=False, resumed=False)
        assert created == ["Session created · default · auto · via native"], created
        assert continued == ["Session continued · default · auto · via native"], continued

    @pytest.mark.asyncio
    async def test_exactly_one_session_sentence_per_turn(self, tmp_path):
        """Two divergent broadcast branches is how `G14` shipped a stale label in
        one of them. One turn emits one sentence, from one format string."""
        for is_new, resumed in ((True, False), (True, True), (False, False)):
            lines = await _one_turn(
                tmp_path, provider_id="acp:claude-code", is_new=is_new, resumed=resumed
            )
            assert len(lines) == 1, (is_new, resumed, lines)
