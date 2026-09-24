"""The DURABLE session map: ``GET /api/chat/sessions/{session}/map`` + persisted
per-turn telemetry (SEMANTIC-SESSION-MAP §B.3, atom ``SSM-2``).

Two things are on trial here, and each has a specific way of being fake:

* **The marks.** A mark's ``visibleIndex`` claims to be ``at_message_index`` — the
  coordinate ``POST .../fork`` and edit-resend speak. Asserting the field's VALUE
  proves nothing (it is whatever the derivation computed), so the round trip is put
  through the OTHER consumer of the coordinate: every mark is forked at its own
  ``visibleIndex`` and the fork's transcript must end on the message the mark points
  at. The length assertion is guarded against vacuity by a fixture where the mark
  count and the visible-message count DIFFER (7 vs 4) — a derivation that just
  counted messages would red.
* **The telemetry.** "Persisted" is only interesting across a reload, so the turn is
  driven through the real ``run_chat``, the session is then EVICTED from memory, and
  the map is re-read — which forces the rehydrate-from-disk path. A test that only
  looked at ``session.messages`` after the turn would pass on a purely in-memory
  stamp, i.e. on the exact bug this atom closes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.chat_session_map import (
    PERSISTED_MARK_KINDS,
    PREVIEW_CAP,
    SESSION_MARK_KINDS,
    SUMMARY_CAP,
    TURN_SUMMARY_KEY,
    TURN_TELEMETRY_KEY,
    build_turn_summary,
    build_turn_telemetry,
    preview_text,
    stamp_turn_summary,
    stamp_turn_telemetry,
    summarize_session_turn,
)
from personalclaw.dashboard.state import _ChatSession
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

_REPO_ROOT = Path(__file__).resolve().parents[1]

# ── fixture: a transcript whose mark count deliberately differs from its length ──


def _seed_mixed(state, name: str = "s1") -> _ChatSession:
    """user → tool → assistant, then user → failed tool → error → assistant.

    Four VISIBLE (user/assistant) messages, seven marks. The gap is the point.
    """
    s = state.get_or_create_session(name)
    s.append("user", "**first** question", "msg msg-u", ts="2026-09-17T05:00:00+00:00")
    s.append(
        "tool",
        "Bash",
        "msg msg-tool",
        ts="2026-09-17T05:00:01+00:00",
        meta={"tool_call_id": "t1", "input": "ls -la", "done": True},
    )
    s.append("assistant", "first answer", "msg msg-a", ts="2026-09-17T05:00:02+00:00")
    s.append("user", "second question", "msg msg-u", ts="2026-09-17T05:01:00+00:00")
    s.append(
        "tool",
        "Read",
        "msg msg-tool",
        ts="2026-09-17T05:01:01+00:00",
        meta={"tool_call_id": "t2", "input": "/etc/hosts", "done": True, "ok": False},
    )
    s.append("error", "provider exploded", "msg msg-err", ts="2026-09-17T05:01:02+00:00")
    s.append("assistant", "second answer", "msg msg-a", ts="2026-09-17T05:01:03+00:00")
    s.drain()
    return s


def _visible(session: _ChatSession) -> list[dict]:
    """The backend's visible list — the exact expression ``at_message_index`` indexes."""
    return [m for m in session.messages if m.get("role") in ("user", "assistant")]


@pytest.fixture
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.sessions.reset = AsyncMock()
    monkeypatch.setattr(state, "broadcast_ws", lambda t, d: None, raising=True)
    monkeypatch.setattr(state, "push_sessions_update", lambda: None, raising=True)
    return state


class TestMarksAndLength:
    @pytest.mark.asyncio
    async def test_one_mark_per_turn_plus_one_per_sub_event(self, _state):
        session = _seed_mixed(_state)
        async with TestClient(TestServer(_make_app(_state))) as client:
            r = await client.get("/api/chat/sessions/s1/map")
            assert r.status == 200
            marks = await r.json()

        # A top-level JSON array, assignable to SSM-1's `SessionMark[]`.
        assert isinstance(marks, list)
        # THE length assertion, and its vacuity guard: 7 marks over 4 visible messages,
        # so neither "len(messages)" nor "len(visible)" can satisfy it by accident.
        assert len(marks) == 7
        assert len(_visible(session)) == 4
        assert len(marks) != len(_visible(session))
        assert [m["kind"] for m in marks] == [
            "user",
            "assistant",
            "tool",
            "user",
            "assistant",
            "tool",
            "error",
        ]
        # Sub-events inherit the OWNING turn's role and coordinate (§A.2).
        assert [m["role"] for m in marks] == [
            "user",
            "assistant",
            "assistant",
            "user",
            "assistant",
            "assistant",
            "assistant",
        ]
        assert [m["visibleIndex"] for m in marks] == [0, 1, 1, 2, 3, 3, 3]
        # Every field SSM-1 declares non-null is present and typed on every mark.
        for i, m in enumerate(marks):
            assert m["markIndex"] == i
            assert m["kind"] in SESSION_MARK_KINDS
            assert isinstance(m["ts"], str) and m["ts"]
            assert isinstance(m["preview"], str) and m["preview"]
        # Markdown marks are stripped from the preview, never leaked into a row/name.
        assert marks[0]["preview"] == "first question"
        # A FAILED tool mark carries `ok: False`; a successful one carries no `ok` at all
        # (SSM-1's `ok?`), so "success" is absence rather than a second truthy state.
        assert "ok" not in marks[2]
        assert marks[5]["ok"] is False

    @pytest.mark.asyncio
    async def test_loop_reinjection_consumes_a_slot_without_minting_a_mark(self, _state):
        """The native ReAct loop re-injects the same prompt each cycle.

        ``hydrateTurns`` collapses those repeats into one turn but still ADVANCES the
        backend cursor, because the backend counted the message. Get that wrong and every
        coordinate after the repeat is off by one — silently addressing another turn.
        """
        s = _state.get_or_create_session("s2")
        s.append("user", "do it", "msg msg-u", ts="2026-09-17T06:00:00+00:00")
        s.append(
            "tool",
            "Bash",
            "msg msg-tool",
            ts="2026-09-17T06:00:01+00:00",
            meta={"tool_call_id": "t1", "input": "x", "done": True},
        )
        s.append("user", "do it", "msg msg-u", ts="2026-09-17T06:00:02+00:00")  # re-injection
        s.append("assistant", "done", "msg msg-a", ts="2026-09-17T06:00:03+00:00")
        s.drain()

        async with TestClient(TestServer(_make_app(_state))) as client:
            marks = await (await client.get("/api/chat/sessions/s2/map")).json()

        assert [m["kind"] for m in marks] == ["user", "assistant", "tool"]
        # The assistant turn is visible index 2 — NOT 1, which is what ignoring the
        # collapsed re-injection's consumed slot would produce.
        assert [m["visibleIndex"] for m in marks] == [0, 2, 2]
        assert _visible(s)[2]["content"] == "done"


class TestVisibleIndexRoundTrip:
    @pytest.mark.asyncio
    async def test_every_mark_forks_at_its_own_coordinate(self, _state):
        """``visibleIndex`` → ``at_message_index``, proved through the fork endpoint.

        The fork is inclusive, so forking at a mark's coordinate must produce exactly
        ``visibleIndex + 1`` messages ending on the message the mark points at.
        """
        session = _seed_mixed(_state)
        visible = _visible(session)
        async with TestClient(TestServer(_make_app(_state))) as client:
            marks = await (await client.get("/api/chat/sessions/s1/map")).json()
            for mark in marks:
                vi = mark["visibleIndex"]
                # The coordinate addresses a real slot in the list the backend indexes…
                assert 0 <= vi < len(visible)
                # …and the OTHER consumer of that coordinate agrees about which one.
                r = await client.post("/api/chat/sessions/s1/fork", json={"at_message_index": vi})
                assert r.status == 200, await r.text()
                body = await r.json()
                assert body["messages"] == vi + 1
                forked = _visible(_state._sessions[body["key"]])
                assert len(forked) == vi + 1
                assert forked[-1]["content"] == visible[vi]["content"]
                assert forked[-1]["role"] == visible[vi]["role"]

    @pytest.mark.asyncio
    async def test_the_coordinate_is_not_the_mark_index(self, _state):
        """A rail against the cheapest possible fake: returning ``markIndex`` twice."""
        _seed_mixed(_state)
        async with TestClient(TestServer(_make_app(_state))) as client:
            marks = await (await client.get("/api/chat/sessions/s1/map")).json()
        assert [m["markIndex"] for m in marks] != [m["visibleIndex"] for m in marks]


class TestDiskOnlySession:
    @pytest.mark.asyncio
    async def test_a_reload_does_not_change_the_map(self, _state):
        """The durability half: a session the gateway has not reopened maps IDENTICALLY.

        Compared against the resident map rather than against a hand-written expectation,
        so the two paths cannot drift apart without reddening — the derivation reads the
        buffer in one case and rehydrated disk content in the other.
        """
        from personalclaw.dashboard.chat_persistence import save_session_to_history

        session = _seed_mixed(_state)
        async with TestClient(TestServer(_make_app(_state))) as client:
            resident = await (await client.get("/api/chat/sessions/s1/map")).json()

            save_session_to_history(_state, session)
            _state._sessions.pop("s1")  # simulated reload: nothing in memory

            r = await client.get("/api/chat/sessions/s1/map")
            assert r.status == 200
            reloaded = await r.json()
        assert "s1" in _state._sessions  # served through the rehydrate-from-disk path
        assert reloaded == resident
        assert [m["visibleIndex"] for m in reloaded] == [0, 1, 1, 2, 3, 3, 3]

    @pytest.mark.asyncio
    async def test_unknown_session_answers_the_wire_error_envelope(self, _state):
        async with TestClient(TestServer(_make_app(_state))) as client:
            r = await client.get("/api/chat/sessions/nope/map")
            assert r.status == 404
            body = await r.json()
        assert body["error"]["code"] == "session_not_found"
        assert body["error"]["message"]


# ── the telemetry half: a real turn, then a real reload ──────────────────────────


def _turn_state(tmp_path, event: AgentEvent, text: str = "the answer"):
    """A DashboardState whose provider streams *text* as one chunk then *event*."""
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
    client = AsyncMock()
    client.provider_id = "anthropic"
    client.context_usage_pct = MagicMock(return_value=event.context_usage_pct)
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))

    from personalclaw.dashboard.state import DashboardState

    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    cb = MagicMock()
    from personalclaw.hooks import ToolHookResult

    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    state.context_builder = cb
    hs = MagicMock()
    hs.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hs
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()

    async def _stream(*_a, **_kw):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield event

    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    return state, client


_COMPLETE = AgentEvent(
    kind=EVENT_COMPLETE,
    stop_reason="end_turn",
    input_tokens=1200,
    output_tokens=340,
    cache_read_tokens=800,
    cache_creation_tokens=64,
    cost_usd=0.0123,
    duration_ms=4321,
    context_usage_pct=37.25,
    event_count=9,
    tool_call_count=2,
)


@pytest.mark.asyncio
async def test_turn_telemetry_persists_and_reappears_after_a_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state, _client = _turn_state(tmp_path, _COMPLETE)
    session = state.get_or_create_session("s9")
    session._trust = True
    session.model = "claude-sonnet-4-5"
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")

    # 1. LIVE: the turn's last assistant message carries the structured record.
    live = [m for m in session.messages if m.get("role") == "assistant"][-1]
    rec = live["meta"][TURN_TELEMETRY_KEY]
    assert rec["input_tokens"] == 1200
    assert rec["output_tokens"] == 340
    assert rec["cache_read_tokens"] == 800
    assert rec["cache_creation_tokens"] == 64
    assert rec["cost_usd"] == pytest.approx(0.0123)
    assert rec["priced"] is True
    assert rec["duration_ms"] == 4321
    assert rec["context_pct"] == pytest.approx(37.2)
    assert rec["events"] == 9
    assert rec["tool_calls"] == 2

    # 2. ON DISK: the record reached the conversation log, not just the buffer. This is
    #    the assertion that separates "persisted" from "stamped after the save".
    log_file = ConversationLog(base_dir=tmp_path)._path("dashboard:s9")
    on_disk = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    persisted = [e for e in on_disk if e.get("role") == "assistant"]
    assert persisted and persisted[-1]["meta"][TURN_TELEMETRY_KEY]["input_tokens"] == 1200

    # 3. AFTER A SIMULATED RELOAD: evict the session and read the map back. The endpoint
    #    rehydrates from disk, so a live-only stamp cannot satisfy this.
    state._sessions.pop("s9")
    async with TestClient(TestServer(_make_app(state))) as client:
        r = await client.get("/api/chat/sessions/s9/map")
        assert r.status == 200
        marks = await r.json()
    assert "s9" in state._sessions  # the read went through the rehydrate path
    carried = [m for m in marks if "telemetry" in m]
    assert len(carried) == 1
    assert carried[0]["kind"] == "assistant"
    assert carried[0]["telemetry"] == rec


@pytest.mark.asyncio
async def test_a_turn_that_reported_nothing_persists_no_telemetry(tmp_path, monkeypatch):
    """A row of zeros is a lie; absence is the honest answer."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state, _client = _turn_state(tmp_path, AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
    session = state.get_or_create_session("s10")
    session._trust = True
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")
    for m in session.messages:
        assert TURN_TELEMETRY_KEY not in (m.get("meta") or {})


class TestTelemetryShapeIsHonest:
    def test_unmeasured_context_is_null_and_disagrees_with_a_measured_zero(self):
        """``G8``: the producer must be able to say "unknown" — persisting a fabricated
        0% would make the exact defect ``test_context_pct_honesty.py`` guards durable."""
        kw = dict(
            input_tokens=10,
            output_tokens=1,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0,
            priced=False,
            duration_ms=5,
            events=1,
            tool_calls=0,
            model="m",
        )
        unmeasured = build_turn_telemetry(context_pct=None, **kw)
        measured_zero = build_turn_telemetry(context_pct=0.0, **kw)
        assert unmeasured["context_pct"] is None
        assert measured_zero["context_pct"] == 0.0
        assert unmeasured != measured_zero

    def test_unpriced_zero_cost_is_flagged_not_reported_as_free(self):
        rec = build_turn_telemetry(
            input_tokens=10,
            output_tokens=1,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0,
            priced=False,
            duration_ms=5,
            context_pct=1.0,
            events=1,
            tool_calls=0,
            model="unknown-model",
        )
        assert rec["cost_usd"] == 0.0
        assert rec["priced"] is False

    def test_no_record_at_all_when_the_turn_reported_no_activity(self):
        assert (
            build_turn_telemetry(
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cost_usd=0.0,
                priced=False,
                duration_ms=0,
                context_pct=None,
                events=0,
                tool_calls=0,
                model="",
            )
            is None
        )

    def test_stamp_targets_the_last_assistant_message_and_no_other(self):
        s = _ChatSession("x")
        s.append("user", "q", "msg msg-u")
        s.append("assistant", "a1", "msg msg-a")
        s.append("tool", "Bash", "msg msg-tool", meta={"tool_call_id": "t"})
        s.append("assistant", "a2", "msg msg-a")
        assert stamp_turn_telemetry(s, {"events": 1}) is True
        stamped = [m for m in s.messages if TURN_TELEMETRY_KEY in (m.get("meta") or {})]
        assert [m["content"] for m in stamped] == ["a2"]

    def test_stamp_is_a_no_op_with_nothing_to_stamp(self):
        s = _ChatSession("x")
        s.append("user", "q", "msg msg-u")
        assert stamp_turn_telemetry(s, {"events": 1}) is False
        assert stamp_turn_telemetry(s, None) is False


# ── the summary label: SSM-3 ─────────────────────────────────────────────────────
#
# The clause is "``meta.summary`` is non-empty and distinct from the raw first line",
# and BOTH halves are cheatable on their own. Non-empty passes on the string "turn";
# distinct-from-the-first-line passes on ANY other message's text — including a verbatim
# copy of the user's request pasted onto the assistant mark, which does no summarizing at
# all. So the assertions below pin the two things that make the label worth persisting:
# it names WHAT THE TURN DID (information no raw transcript line contains), and it reads
# the WHOLE request rather than its opening words (the filler opener is gone). A summary
# that merely echoed another line would satisfy the clause and fail here.


def _seed_tool_turn(state, name: str = "s20") -> _ChatSession:
    """One realistic completed turn: a chatty request, two tools (one failing), a reply."""
    s = state.get_or_create_session(name)
    s.append(
        "user",
        "Hey! Quick question — can you check how the config round-trip works in loader.py?\n"
        "Take your time, no rush.",
        "msg msg-u",
        ts="2026-09-19T05:00:00+00:00",
    )
    s.append(
        "tool",
        "Read",
        "msg msg-tool",
        ts="2026-09-19T05:00:01+00:00",
        meta={"tool_call_id": "t1", "input": "loader.py", "done": True},
    )
    # A result UPDATE for a call already seen — must not count as a second call.
    s.append(
        "tool",
        "Read",
        "msg msg-tool",
        ts="2026-09-19T05:00:02+00:00",
        meta={"tool_call_id": "t1", "input": "loader.py", "done": True},
    )
    s.append(
        "tool",
        "Grep",
        "msg msg-tool",
        ts="2026-09-19T05:00:03+00:00",
        meta={"tool_call_id": "t2", "input": "to_dict", "done": True, "ok": False},
    )
    s.append(
        "assistant",
        "Sure thing. Let me walk through it.\n\nThe loader reads the dataclass first.",
        "msg msg-a",
        ts="2026-09-19T05:00:04+00:00",
    )
    s.drain()
    return s


class TestSummaryLabel:
    def test_a_seeded_turn_gets_a_label_that_is_non_empty_and_not_the_raw_first_line(self, _state):
        """The clause, with its two cheap readings closed off."""
        session = _seed_tool_turn(_state, "s20")

        summary = summarize_session_turn(session)
        assert summary  # non-empty
        assert stamp_turn_summary(session, summary) is True
        stamped = [m for m in session.messages if m.get("role") == "assistant"][-1]
        assert stamped["meta"][TURN_SUMMARY_KEY] == summary

        # DISTINCT from the raw first line — asserted against BOTH readings of "first
        # line": the turn's rendered preview, and the literal first physical line.
        reply = "Sure thing. Let me walk through it.\n\nThe loader reads the dataclass first."
        assert summary != preview_text(reply, PREVIEW_CAP)
        assert summary != reply.splitlines()[0]
        request_first_physical_line = (
            "Hey! Quick question — can you check how the config round-trip works in loader.py?"
        )
        assert summary != request_first_physical_line
        assert summary != preview_text(request_first_physical_line, PREVIEW_CAP)

        # NON-VACUITY 1: it states what the turn DID. No raw line says this.
        assert "ran Read, Grep" in summary
        assert "(1 failed)" in summary  # the failing Grep, counted once
        assert summary.count("Read") == 1, "a repeat tool_call_id must not count twice"

        # NON-VACUITY 2: it read the WHOLE request, not its opening words — the greeting
        # and the politeness clause are gone, the subject survives.
        assert "Hey" not in summary
        assert "Quick question" not in summary
        assert "no rush" not in summary
        assert "config round-trip" in summary

    @pytest.mark.asyncio
    async def test_the_label_becomes_the_marks_preview_and_survives_a_reload(self, _state):
        """ "Surfaced via SSM-2's preview" — through the endpoint, after a real reload."""
        from personalclaw.dashboard.chat_persistence import save_session_to_history

        session = _seed_tool_turn(_state, "s21")
        summary = summarize_session_turn(session)
        assert stamp_turn_summary(session, summary) is True

        async with TestClient(TestServer(_make_app(_state))) as client:
            resident = await (await client.get("/api/chat/sessions/s21/map")).json()
            save_session_to_history(_state, session)
            _state._sessions.pop("s21")  # simulated reload: nothing in memory
            r = await client.get("/api/chat/sessions/s21/map")
            assert r.status == 200
            reloaded = await r.json()
        assert "s21" in _state._sessions  # went through the rehydrate-from-disk path

        assistant_marks = [m for m in reloaded if m["kind"] == "assistant"]
        assert len(assistant_marks) == 1
        assert assistant_marks[0]["preview"] == summary
        assert reloaded == resident  # buffer and disk derive the same map

        # The USER mark keeps the user's own words: a person scanning the rail for their
        # own question must still find it. Only the assistant mark trades its opening
        # words for the label.
        user_marks = [m for m in reloaded if m["kind"] == "user"]
        assert len(user_marks) == 1
        assert "Hey! Quick question" in user_marks[0]["preview"]
        assert user_marks[0]["preview"] != summary

    @pytest.mark.asyncio
    async def test_a_turn_with_no_outcome_persists_no_label_and_keeps_its_preview(self, _state):
        """Absence is the honest answer — the same gate telemetry applies to a row of zeros.

        A label with no outcome clause could only echo words already on the rail, so it is
        not written at all and the mark falls back to ``preview_text``.
        """
        s = _state.get_or_create_session("s22")
        s.append("user", "what is 2 + 2", "msg msg-u", ts="2026-09-19T06:00:00+00:00")
        s.append("assistant", "4", "msg msg-a", ts="2026-09-19T06:00:01+00:00")
        s.drain()

        assert summarize_session_turn(s) is None
        assert stamp_turn_summary(s, None) is False
        for m in s.messages:
            assert TURN_SUMMARY_KEY not in (m.get("meta") or {})

        async with TestClient(TestServer(_make_app(_state))) as client:
            marks = await (await client.get("/api/chat/sessions/s22/map")).json()
        assert [m["preview"] for m in marks] == ["what is 2 + 2", "4"]

    def test_a_credential_in_the_request_is_redacted_at_the_write(self):
        """``_prepare_messages`` scrubs ``content`` on the way out but NEVER ``meta``.

        So a label composed from raw text and persisted unredacted would be served
        verbatim, bypassing the scrub the preview it replaces gets for free. The redaction
        therefore has to happen at the write, and this is the test that says so.
        """
        secret = "sk-ant-api03-" + "A" * 95
        summary = build_turn_summary(
            request=f"deploy with {secret} please",
            reply="done",
            tool_names=["Bash"],
        )
        assert summary
        assert secret not in summary
        assert "sk-ant-api03" not in summary

    def test_no_user_row_means_no_turn_to_summarize(self):
        session = MagicMock()
        session.messages = [{"role": "assistant", "content": "# Heading\n\nbody"}]
        assert summarize_session_turn(session) is None


class TestSummaryOutcomeClause:
    """The outcome clause is the half no raw line contains, so its shape is pinned."""

    def test_distinct_tools_are_named_then_counted(self):
        summary = build_turn_summary(
            request="refactor the loader",
            reply="ok",
            tool_names=["Read", "Read", "Grep", "Edit", "Bash", "Write"],
        )
        assert summary == "refactor the loader — ran Read, Grep, Edit +2 more"

    def test_a_toolless_turn_borrows_the_replys_own_heading(self):
        """A heading is the reply summarizing itself — the best a tool-less turn can do."""
        summary = build_turn_summary(
            request="compare RAIDZ2 and dRAID",
            reply="## RAIDZ2 wins under 12 disks\n\nBecause resilver time…",
            tool_names=[],
        )
        assert summary == "compare RAIDZ2 and dRAID — RAIDZ2 wins under 12 disks"

    def test_an_errored_turn_says_so(self):
        summary = build_turn_summary(
            request="run the suite", reply="", tool_names=["Bash"], errored=True
        )
        assert summary == "run the suite — ran Bash — ended in an error"
        # …and an error alone is an outcome, even with no tool and no heading.
        assert build_turn_summary(
            request="run the suite", reply="", tool_names=[], errored=True
        ) == ("run the suite — ended in an error")

    def test_no_outcome_is_none_not_an_echo(self):
        assert build_turn_summary(request="hello there", reply="hi", tool_names=[]) is None

    def test_a_label_is_capped_below_the_preview_budget(self):
        summary = build_turn_summary(
            request="investigate " + "the persistent loader regression " * 12,
            reply="ok",
            tool_names=["Read"],
        )
        assert summary
        assert len(summary) <= SUMMARY_CAP
        assert SUMMARY_CAP < PREVIEW_CAP

    def test_a_filler_only_request_keeps_its_words_rather_than_going_blank(self):
        """Stripping must never empty the subject — "thanks" is all the subject there is."""
        summary = build_turn_summary(request="thanks!", reply="ok", tool_names=["Read"])
        assert summary == "thanks! — ran Read"

    def test_a_leading_word_that_merely_starts_like_filler_is_not_eaten(self):
        summary = build_turn_summary(
            request="Highlight the diff in loader.py", reply="ok", tool_names=["Read"]
        )
        assert summary == "Highlight the diff in loader.py — ran Read"


@pytest.mark.asyncio
async def test_run_chat_stamps_the_summary_before_the_save(tmp_path, monkeypatch):
    """The production call site FIRES — the label is not a function without a caller.

    Driven through the real ``run_chat``, then read back OFF DISK: a stamp placed after
    ``save_session_to_history`` would be in-memory only and would red the on-disk half.

    The user row is appended BEFORE ``run_chat``, because that is the real call order:
    ``run_chat`` never appends it — ``chat_handlers.py:354`` (HTTP) and ``state.py:659``
    (``start_or_queue``) both do, immediately before dispatching. A harness that skips it
    hands the runner a buffer with no turn boundary, which no production path produces.
    """
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state, _client = _turn_state(tmp_path, _COMPLETE, text="## Loader audit\n\nTwo gaps found.")
    session = state.get_or_create_session("s23")
    session._trust = True
    session.model = "claude-sonnet-4-5"
    session.append("user", "please audit loader.py", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "please audit loader.py")

    live = [m for m in session.messages if m.get("role") == "assistant"][-1]
    label = live["meta"][TURN_SUMMARY_KEY]
    assert label == "audit loader.py — Loader audit"

    log_file = ConversationLog(base_dir=tmp_path)._path("dashboard:s23")
    on_disk = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    persisted = [e for e in on_disk if e.get("role") == "assistant"]
    assert persisted and persisted[-1]["meta"][TURN_SUMMARY_KEY] == label

    state._sessions.pop("s23")
    async with TestClient(TestServer(_make_app(state))) as client:
        marks = await (await client.get("/api/chat/sessions/s23/map")).json()
    assert [m["preview"] for m in marks if m["kind"] == "assistant"] == [label]


# ── the cross-language mirror (one contract, two producers) ──────────────────────


class TestMirrorsTheFrontendContract:
    def test_mark_kind_vocabulary_matches_sessionMap_ts(self):
        """A kind added on one side only would give SSM-4 two vocabularies."""
        src = (_REPO_ROOT / "web/src/pages/chat/sessionMap.ts").read_text(encoding="utf-8")
        # Split on the ARRAY opener, not on the identifier: the declaration's own
        # `SessionMarkKind[]` type annotation carries a `]` that would truncate the block.
        block = src.split("SESSION_MARK_KINDS", 1)[1].split("= [", 1)[1].split("]", 1)[0]
        ts_kinds = tuple(re.findall(r"'([a-z]+)'", block))
        assert ts_kinds, "failed to parse the frontend vocabulary — fix the parse, not this"
        assert ts_kinds == SESSION_MARK_KINDS
        # The durable side can only witness the kinds the transcript records; the two
        # live-only kinds are named here so the gap is declared, not discovered.
        assert set(PERSISTED_MARK_KINDS) < set(SESSION_MARK_KINDS)
        assert set(SESSION_MARK_KINDS) - set(PERSISTED_MARK_KINDS) == {"subagent", "activity"}

    def test_preview_text_mirrors_previewText_ts(self):
        """Same fixtures as ``web/src/lib/previewText.test.ts``, same expectations."""
        md = "\n".join(
            [
                "# RAIDZ2 vs dRAID",
                "",
                "**Recommendation: use RAIDZ2.** At 6–12 disks *modestly* faster.",
                "> quoted line",
                "- a bullet",
                "2. numbered",
                "[a link](https://example.test/x) and `code`",
                "```ts",
                "const x = 1",
                "```",
            ]
        )
        out = preview_text(md)
        assert "RAIDZ2 vs dRAID" in out
        assert "Recommendation: use RAIDZ2." in out
        assert "a link and code" in out
        assert "const x = 1" in out
        for mark in ["**", "##", "# ", "> ", "- ", "](", "```", "`", "*"]:
            assert mark not in out, f"mark {mark!r} must be stripped"
        assert preview_text("a\n\nb\n c") == "a b c"
        long = preview_text("word " * 50, 55)
        assert len(long) <= 55 and long.endswith("…")
        assert preview_text("") == ""
        assert preview_text(None) == ""
