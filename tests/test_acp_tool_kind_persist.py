"""AAP-8 §2.5 gap 7, the `tool_kind` half — the declared kind reaches the PERSISTED row.

**Where it died, measured before anything was changed.** `translate.py` emits the kind
correctly (`extract_tool_event` reads `update["kind"]`, redacts it, puts it on
`AcpEvent.tool_kind`) and `chat_runner` computed `_kind` from it and broadcast it on the
live `tool_call` WS frame — but the `session.append("tool", …, meta={…})` beside that
broadcast wrote only `tool_call_id`/`purpose`/`input`. So the kind was **emitted and then
dropped one line before persistence**, which is why `acp-parity.md:219` reads
`tool_kind: null` on every row of a re-drive whose `meta.input` was populated: the two
values are computed six lines apart and only one of them was ever written.

That distinction decided the fix. Had the kind been absent on the inbound frame the
repair would have been a decoder change; it is present, so the repair is one key on the
persisted meta plus the reader that had no field to read it into.

**What a user sees, and why a persisted-only defect is still a visible one.** The card's
icon resolves through `iconForTool` (`web/src/pages/chat/toolRenderers/native.tsx`):
explicit native name → **declared ACP kind** (`_BY_KIND`) → keyword regex over the CLI's
prose title. Live, the kind arrived on the socket and the second rung worked. After a
reload it did not, so every ACP card fell through to the regex — and that rung penalises
the honest provider, which is `G34`'s finding in the other direction: kiro's truthful
`Running: pwd` and codex's mislabelled `Read file '…'` for the same shell command are
graded by their prose, not their declaration.

The turns below run through the real `run_chat` over a synthetic ACP event stream rather
than calling a predicate, because the defect was never in a predicate — it was a key
missing from a dict literal on the persistence path, and every unit test of the decoder
passed while it was missing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.translate import extract_tool_event
from personalclaw.acp.types import JsonRpcMessage
from personalclaw.dashboard.chat_persistence import _redact_meta
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.hooks import ToolHookResult
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TOOL_CALL, LLMEvent

# ── the run_chat harness (same shape as AAP-6's) ──────────────────────────────


async def _async_iter(items):
    for item in items:
        yield item


def _make_state(tmp_path):
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = "acp:kiro-cli"
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
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
    state.context_builder = cb
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


def _session(key="chat-1-aap8"):
    s = _ChatSession(key)
    s._trust = True  # no approval card: this turn is about the tool row, not the gate
    s.acp_provider = "acp:kiro-cli"
    return s


def _set_stream(client, events):
    client.stream = MagicMock(side_effect=lambda *a, **kw: _async_iter(events))


async def _drive(state, session):
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")


def _tool_rows(session) -> list[dict]:
    return [m for m in session.messages if m.get("role") == "tool"]


# ── real CLI frames, decoded by the real pipeline ─────────────────────────────


def _decoded_call(update: dict) -> LLMEvent:
    """A tool-CALL event produced by the real ACP pipeline from a real CLI frame.

    `extract_tool_event` then `acp_event_to_agent_event` — the two hops the live stream
    takes. Built this way rather than hand-writing `LLMEvent(tool_kind=…)`: a
    hand-written event tests the persistence dict against a value no decoder is proven
    to produce, and the whole defect being fixed here is a value that WAS produced and
    then dropped.
    """
    msg = JsonRpcMessage(
        method="session/update",
        params={"update": {"sessionUpdate": "tool_call", **update}},
    )
    ev = extract_tool_event(msg, {}, {}, [])
    assert ev is not None, f"the decoder produced no tool call for {update!r}"
    return acp_event_to_agent_event(ev)


#: One `tool_call` frame per provider, in the shape each one actually puts on the wire.
#:
#: * **claude** — `@agentclientprotocol/claude-agent-acp`. `kind` comes from the adapter's
#:   own per-tool table (`dist/tools.js`: `Read` → `"read"`, `Write`/`Edit` → `"edit"`,
#:   `Bash` → `"execute"`), alongside a `rawInput` holding the Claude tool's arguments.
#: * **codex** — `@agentclientprotocol/codex-acp`. `kind` plus a real `diff` content
#:   block; the block is `AAP-8`'s chip half and is pinned in
#:   `test_acp_tool_card_fidelity.py`, so what this frame is here for is the `kind`.
#: * **kiro** — `kiro-cli`, native ACP, no adapter. Honest operation titles (`G34`), which
#:   is exactly why its rows must not be graded by the title-regex fallback.
_PROVIDER_CALL_FRAMES = {
    "claude": {
        "toolCallId": "claude-1",
        "title": "Edit src/a.py",
        "kind": "edit",
        "rawInput": {"file_path": "src/a.py", "old_string": "a", "new_string": "b"},
    },
    "codex": {
        "toolCallId": "codex-1",
        "title": "Read file 'probe.txt'",
        "kind": "read",
        "rawInput": {"path": "probe.txt"},
    },
    "kiro": {
        "toolCallId": "kiro-1",
        "title": "Running: pwd",
        "kind": "execute",
        "rawInput": {"command": "pwd"},
    },
}


class TestTheDeclaredKindReachesThePersistedRow:
    """Each provider shape pinned separately — a single parametrised assertion over one
    frame would have passed on codex alone, which is how a "holds on 1 of 3" row is
    written in the first place."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", sorted(_PROVIDER_CALL_FRAMES))
    async def test_the_row_carries_the_kind_the_cli_declared(self, provider, tmp_path):
        frame = _PROVIDER_CALL_FRAMES[provider]
        state, client = _make_state(tmp_path)
        _set_stream(
            client,
            [_decoded_call(frame), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
        )
        session = _session()
        await _drive(state, session)

        rows = _tool_rows(session)
        assert rows, f"{provider}: the turn persisted no tool row at all"
        meta = rows[0].get("meta") or {}
        # Vacuity floor for the row itself: `input` is the half that ALREADY worked
        # (acp-parity.md:219), so if it is missing the turn did not reach the append and
        # the kind assertion below would be measuring nothing.
        assert meta.get("input"), f"{provider}: the row has no input — this turn proved nothing"
        assert meta.get("kind") == frame["kind"], f"{provider}: meta={meta!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", sorted(_PROVIDER_CALL_FRAMES))
    async def test_the_kind_survives_the_persistence_redaction_roundtrip(self, provider, tmp_path):
        """`_redact_meta` runs over every meta at the persistence read/write boundary. It
        is key-preserving today; assert that, because a future allowlist there would
        delete this fix silently and the live socket would keep it looking fixed."""
        frame = _PROVIDER_CALL_FRAMES[provider]
        state, client = _make_state(tmp_path)
        _set_stream(
            client,
            [_decoded_call(frame), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
        )
        session = _session()
        await _drive(state, session)

        meta = (_tool_rows(session)[0].get("meta") or {}).copy()
        assert _redact_meta(meta).get("kind") == frame["kind"]

    @pytest.mark.asyncio
    async def test_a_frame_that_declared_no_kind_persists_the_unknown_placeholder(self, tmp_path):
        """`unknown` is the decoder's OWN placeholder for "this frame declared none"
        (`SeenToolCall`), and `G10` requires that absence stay representable rather than
        resolving to something permissive. So it is persisted, not stripped: a reader can
        tell "declared nothing" from "never measured"."""
        state, client = _make_state(tmp_path)
        _set_stream(
            client,
            [
                _decoded_call({"toolCallId": "n-1", "title": "mystery", "rawInput": {"a": 1}}),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        session = _session()
        await _drive(state, session)
        assert (_tool_rows(session)[0].get("meta") or {}).get("kind") == "unknown"

    @pytest.mark.asyncio
    async def test_the_native_runtime_declares_no_kind_and_none_is_fabricated(self, tmp_path):
        """The vacuity floor that keeps this from becoming a fabricated field. The native
        loop passes `tool_kind=""` for its own tools (`task_modes.py`), and a persisted
        `kind: ""` would claim the runtime declared an empty kind. The key must be ABSENT,
        which is also what keeps `iconForTool`'s native-name rung ahead of the kind rung.
        """
        state, client = _make_state(tmp_path)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="nat-1",
                    title="bash",
                    tool_input='{"command": "pwd"}',
                ),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ],
        )
        session = _session()
        await _drive(state, session)
        meta = _tool_rows(session)[0].get("meta") or {}
        assert "kind" not in meta, meta

    @pytest.mark.asyncio
    async def test_the_live_socket_and_the_persisted_row_agree(self, tmp_path):
        """The two representations of one fact, asserted against each other. They are
        computed six lines apart from the same `_kind`, and the defect this file fixes was
        precisely that one of them was written and the other was not — so a test that
        checked only the row would not notice the next time they diverge."""
        frame = _PROVIDER_CALL_FRAMES["claude"]
        state, client = _make_state(tmp_path)
        _set_stream(
            client,
            [_decoded_call(frame), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
        )
        session = _session()
        await _drive(state, session)

        broadcast = [
            call.args[1]
            for call in state.broadcast_ws.call_args_list
            if call.args and call.args[0] == "tool_call"
        ]
        assert broadcast, "no tool_call frame was broadcast — the live half proved nothing"
        assert broadcast[0]["kind"] == (_tool_rows(session)[0].get("meta") or {})["kind"]
