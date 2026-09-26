"""Background compression shortens what the model reads, never the chat the user keeps.

Every test drives the REAL store under the per-test home: a ``ConversationLog`` over
``<home>/sessions``, the dashboard's own save (``save_session_to_history``) for a chat's
file and header, the SDK's ``save_conversation_turn`` for a channel thread, the job the
heartbeat runs (``run_bg_compression_pass``), what the model is handed when a chat resumes
(``ConversationLog.history_for_model``, and a real turn on a fresh runtime through
``run_chat``), and the startup/shutdown sweep (``cleanup_orphaned_sessions``).

What each test pins, and what it measured on the base it was written against:

* **The compression job rewrote an idle chat's file.** A 40-message chat idle for a week
  came back as 25 rows — one summary row for the oldest 16, the next 16 cut to 600
  characters — and its header lost the title, pin, folder, tags and message count. The
  summary row itself was ``role: "system"``, which the model's history bootstrap filters
  out, so the model never read it either.
* **A channel thread past 2 MB was cut to its last 200 lines** by ``append``.
* **The dropped lines went to ``sessions/archive/``, and two jobs deleted them**: the
  prune that ran on every archive write (files older than 7 days), and the startup /
  shutdown sweep, which removed the whole directory once nothing new had landed in it for
  7 days. Deleting a compressed chat left its dropped messages in that directory.
* **The pass stalled behind the three oldest idle chats**: it only ever looked at the
  first three, so three small ones kept every other chat from being compressed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.bg_compress import run_bg_compression_pass
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat import save_session_to_history
from personalclaw.dashboard.chat_persistence import resolve_session
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.sdk.channel import save_conversation_turn
from personalclaw.skills import SkillsLoader

_SUMMARY_MARK = "EARLIER-TURNS-SUMMARY"
_THIRTY_DAYS = 30 * 86400
_ARCHIVE_BATCH = "dashboard_chat-2-1780000000__20260801-120000.jsonl"


@pytest.fixture()
def home() -> Path:
    """The per-test home every resolver in this process agrees on (conftest redirects it)."""
    return config_loader.config_dir()


@pytest.fixture()
def summarizer(monkeypatch):
    """The background model, replaced by a deterministic stand-in that counts its calls."""
    calls: list[str] = []

    async def _fake(text, *, cap=2000, raw_ref=""):
        calls.append(text)
        return f"{_SUMMARY_MARK} ({len(text)} chars of earlier conversation)"

    monkeypatch.setattr("personalclaw.tool_providers.prose_compress.compress_prose", _fake)
    monkeypatch.setattr("personalclaw.bg_compress._record_savings", lambda *a, **k: None)
    return calls


def _state(home: Path, monkeypatch) -> DashboardState:
    """One gateway process's dashboard state over the home's session store."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: home)
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=home / "sessions"),
    )
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.context_builder = None
    state.consolidator = None
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    import personalclaw.trust_mode as trust_mode

    trust_mode.disable_yolo()
    return state


def _turns(n_turns: int, *, tag: str = "") -> list[tuple[str, str]]:
    """Alternating user/assistant messages, each unique, long enough to be worth compressing."""
    out: list[tuple[str, str]] = []
    for i in range(n_turns):
        out.append(("user", f"{tag}Q{i:02d}: how should we budget part {i} of the kitchen? " * 3))
        out.append(
            (
                "assistant",
                f"{tag}A{i:02d}: here is the detailed plan for part {i}, step by step. " * 12,
            )
        )
    return out


def _idle_chat(state: DashboardState, key: str, rows: list[tuple[str, str]]) -> Path:
    """A titled, pinned, filed, tagged dashboard chat, saved by the dashboard, idle 30 days."""
    session = state.get_or_create_session(key)
    session.title = "Kitchen renovation budget"
    session._titled = True
    session.pinned = True
    session.folder_id = "fld-home"
    session.tags = ["renovation", "money"]
    for i, (role, text) in enumerate(rows):
        session.append(role, text, ts=f"2026-08-01T10:{i // 60:02d}:{i % 60:02d}", broadcast=False)
    save_session_to_history(state, session)
    path = state.conversation_log._path(f"dashboard:{key}")
    old = time.time() - _THIRTY_DAYS
    os.utime(path, (old, old))
    return path


def _header(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


async def _served(state: DashboardState, key: str) -> list[tuple[str, str]]:
    """The chat as the user sees it when they open it."""
    async with TestClient(TestServer(_make_app(state))) as http:
        resp = await http.get(f"/api/chat/sessions/{key}")
        assert resp.status == 200, await resp.text()
        return [(m["role"], m["content"]) for m in (await resp.json())["messages"]]


# ── 1. An idle chat ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_job_leaves_an_idle_chat_byte_for_byte_and_the_model_reads_its_summary(
    home, monkeypatch, summarizer
):
    state = _state(home, monkeypatch)
    key = "chat-1-1780000000"
    rows = _turns(20)
    assert len(rows) == 40 and sum(len(t) for _, t in rows) > 8_000
    path = _idle_chat(state, key, rows)
    before = path.read_bytes()
    header_before = _header(path)
    assert header_before["message_count"] == 40

    stats = await run_bg_compression_pass(state.conversation_log, embed_fn=None)

    # The job ran and did its work — this is not a pass that skipped the chat.
    assert [s["key"] for s in stats] == [f"dashboard_{key}"]
    assert len(summarizer) == 1

    on_disk = state.conversation_log.read_messages(f"dashboard:{key}")
    assert [
        (m["role"], m["content"]) for m in on_disk
    ] == rows, "every message must still be in the chat, word for word"
    header = _header(path)
    for field, value in {
        "title": "Kitchen renovation budget",
        "pinned": True,
        "folder_id": "fld-home",
        "tags": ["renovation", "money"],
        "message_count": 40,
    }.items():
        assert header.get(field) == value, f"the header lost {field!r}: {header}"
    assert path.read_bytes() == before, "the chat file must be byte-for-byte what the user saved"

    # Opened after a restart, the chat shows every message.
    assert await _served(_state(home, monkeypatch), key) == rows

    # What the model reads of it (the reader a channel thread's history comes through): the
    # summary in place of the oldest span, the newest turns verbatim. Section 4 drives the
    # same through a real dashboard turn.
    view = state.conversation_log.history_for_model(f"dashboard:{key}", 100)
    assert view[0]["role"] == "summary" and _SUMMARY_MARK in view[0]["content"]
    assert rows[0][1] not in [m["content"] for m in view], "the summarized span was re-read"
    assert view[-2:] == [
        {"role": "user", "content": rows[-2][1]},
        {"role": "assistant", "content": rows[-1][1]},
    ]


@pytest.mark.asyncio
async def test_the_summary_stops_standing_in_once_the_span_it_covers_changes(
    home, monkeypatch, summarizer
):
    """A summary is derived data: an edit inside its span makes the model read the edit."""
    state = _state(home, monkeypatch)
    key = "chat-3-1780000000"
    rows = _turns(20)
    _idle_chat(state, key, rows)
    await run_bg_compression_pass(state.conversation_log, embed_fn=None)
    view = state.conversation_log.history_for_model(f"dashboard:{key}", 100)
    assert view[0]["role"] == "summary"

    # The user edits the second message of the chat — inside the summarized span.
    session = state._sessions[key]
    session.messages[1]["content"] = "EDITED: the plan changed completely."
    save_session_to_history(state, session, force=True)

    view = state.conversation_log.history_for_model(f"dashboard:{key}", 100)
    assert "EDITED: the plan changed completely." in [m["content"] for m in view]
    assert view[0]["role"] == "user", "a summary of text that changed must not be read"


@pytest.mark.asyncio
async def test_a_pass_is_not_stalled_by_the_oldest_idle_chats(home, monkeypatch, summarizer):
    """Three small chats older than a compressible one used to take the whole budget."""
    state = _state(home, monkeypatch)
    log = state.conversation_log
    for n in range(3):
        log.append(f"tiny-{n}", "user", "hi")
        log.append(f"tiny-{n}", "assistant", "hello")
        stamp = time.time() - _THIRTY_DAYS - (10 - n) * 86400
        os.utime(log._path(f"tiny-{n}"), (stamp, stamp))
    key = "chat-5-1780000000"
    _idle_chat(state, key, _turns(20))

    stats = await run_bg_compression_pass(log, embed_fn=None)

    assert [s["key"] for s in stats] == [f"dashboard_{key}"]


@pytest.mark.asyncio
async def test_deleting_a_compressed_chat_leaves_nothing_of_it_behind(
    home, monkeypatch, summarizer
):
    state = _state(home, monkeypatch)
    key = "chat-6-1780000000"
    rows = _turns(20, tag="ORCHID-")
    _idle_chat(state, key, rows)
    stats = await run_bg_compression_pass(state.conversation_log, embed_fn=None)
    assert [s["key"] for s in stats] == [f"dashboard_{key}"], "the chat must be compressed first"

    assert state.conversation_log.delete_session(f"dashboard:{key}")

    leftovers = [
        p.relative_to(home).as_posix()
        for p in (home / "sessions").rglob("*")
        if p.is_file() and "ORCHID-" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert leftovers == [], f"a deleted chat's messages must not survive it: {leftovers}"
    summaries = [
        p.relative_to(home).as_posix()
        for p in (home / "sessions").rglob("*")
        if p.is_file() and _SUMMARY_MARK in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert summaries == [], f"a deleted chat's summary must not survive it: {summaries}"


# ── 2. A channel thread ─────────────────────────────────────────────────────


def test_a_channel_thread_past_2mb_keeps_every_line(home):
    """The SDK path every channel app writes through never cuts the thread."""
    log = ConversationLog(base_dir=home / "sessions")
    key = "slack_C0123_1712345678.000100"
    written: list[tuple[str, str]] = []
    for i in range(1_100):
        user = f"U{i:04d} " + "the vendor asked about the invoice again. " * 24
        assistant = f"R{i:04d} " + "noted, drafting the reply with the new totals. " * 21
        save_conversation_turn(
            log, key, user, assistant, source_thread="1712345678.000100", source_user="U42"
        )
        written += [("user", user), ("assistant", assistant)]

    kept = [(m["role"], m["content"]) for m in log.read_messages(key)]
    assert len(kept) == len(written), f"{len(written) - len(kept)} lines were cut from the thread"
    assert kept == written
    assert log._path(key).stat().st_size > 2 * 1024 * 1024, "the thread is past the old 2 MB cut"
    assert not (home / "sessions" / "archive").exists(), "nothing was moved out of the thread"
    # What the model reads stays bounded by the read window, not by cutting the file.
    assert [m["content"] for m in log.recent(key, max_messages=20)] == [t for _, t in written[-20:]]


# ── 3. The archive keeps what older versions trimmed ────────────────────────


def _legacy_archive_batch(home: Path) -> tuple[Path, bytes]:
    """A batch an older version trimmed out of a chat — the only copy of those lines."""
    adir = home / "sessions" / "archive"
    adir.mkdir(parents=True, exist_ok=True)
    path = adir / _ARCHIVE_BATCH
    body = (
        json.dumps({"_type": "archive", "reason": "bg_compress", "count": 2})
        + "\n"
        + json.dumps({"role": "user", "content": "ONLY-COPY: the original question"})
        + "\n"
        + json.dumps({"role": "assistant", "content": "ONLY-COPY: the original answer"})
        + "\n"
    )
    path.write_text(body, encoding="utf-8")
    old = time.time() - _THIRTY_DAYS
    os.utime(path, (old, old))
    return path, body.encode("utf-8")


def test_the_startup_and_shutdown_sweep_keeps_the_archive(home, monkeypatch):
    from personalclaw.session_pid import cleanup_orphaned_sessions

    batch, body = _legacy_archive_batch(home)
    monkeypatch.setattr(
        "personalclaw.session_pid._session_pid_file_path", lambda: home / "session_pids.txt"
    )
    with (
        patch("personalclaw.session_pid._cleanup_orphaned_mcp_servers", return_value=0),
        patch("os.kill", side_effect=ProcessLookupError),
    ):
        cleanup_orphaned_sessions()

    assert batch.exists(), "the sweep deleted the only copy of lines an older version trimmed"
    assert batch.read_bytes() == body


def test_deleting_a_chat_takes_its_own_trimmed_lines_and_no_one_elses(home):
    """Nothing prunes the archive now, so deleting a chat is what removes its batches."""
    log = ConversationLog(base_dir=home / "sessions")
    log.append("dashboard:chat-2-1780000000", "user", "the chat an older version trimmed")
    batch, _ = _legacy_archive_batch(home)
    # A different chat whose key starts with this one's, and its own trimmed lines.
    log.append("dashboard:chat-2-1780000000__b", "user", "a neighbour")
    neighbour = (
        home / "sessions" / "archive" / "dashboard_chat-2-1780000000__b__20260801-120000.jsonl"
    )
    neighbour.write_text('{"role": "user", "content": "NEIGHBOUR"}\n', encoding="utf-8")

    assert log.delete_session("dashboard:chat-2-1780000000")

    assert not batch.exists(), "a deleted chat's trimmed lines must go with it"
    assert neighbour.exists(), "another chat's trimmed lines must stay"


@pytest.mark.asyncio
async def test_no_write_path_prunes_the_archive(home, monkeypatch, summarizer):
    """Compressing a chat and growing a thread past 2 MB used to prune batches past 7 days."""
    # The prune was rate-limited to once an hour per process; make sure it would run.
    monkeypatch.setattr("personalclaw.history._last_cleanup", 0.0, raising=False)
    batch, body = _legacy_archive_batch(home)
    state = _state(home, monkeypatch)
    _idle_chat(state, "chat-7-1780000000", _turns(20))
    await run_bg_compression_pass(state.conversation_log, embed_fn=None)
    monkeypatch.setattr("personalclaw.history._last_cleanup", 0.0, raising=False)
    for i in range(1_100):
        save_conversation_turn(
            state.conversation_log, "slack_C9_1.2", f"u{i} " + "x" * 1_000, f"a{i} " + "y" * 1_000
        )

    assert batch.exists(), "a write path deleted the only copy of lines an older version trimmed"
    assert batch.read_bytes() == body


# ── 4. A resumed chat hands the model its summary ────────────────────────────


class _RecordingModel:
    """A model provider that records every request and answers one short line."""

    supports_tools = True
    _model = "recorder"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _restarted(home: Path, monkeypatch) -> tuple[DashboardState, _RecordingModel]:
    """A restarted gateway: the real assembler, and a FRESH native runtime for the next turn."""
    state = _state(home, monkeypatch)
    model = _RecordingModel()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="recorder"),
        model_provider=model,
        tool_providers=[],
        cwd=home,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    state.sessions._sessions = {}
    state.sessions.get_channel_link = MagicMock(return_value=(None, None))
    state.sessions.get_or_create = AsyncMock(return_value=(runtime, True, False))
    state.context_builder = ContextBuilder(
        memory=MemoryStore(workspace=home / "ws"),
        skills=SkillsLoader(skills_path=home / "skills", install_builtins=False),
        conversation_log=state.conversation_log,
    )
    state._hook_store = None
    return state, model


@pytest.mark.asyncio
async def test_a_resumed_compressed_chat_hands_the_model_its_summary_not_its_oldest_turns(
    home, monkeypatch, summarizer
):
    key = "chat-8-1780000000"
    rows = _turns(20)
    _idle_chat(_state(home, monkeypatch), key, rows)
    assert await run_bg_compression_pass(_state(home, monkeypatch).conversation_log, embed_fn=None)

    state, model = await _restarted(home, monkeypatch)
    session = resolve_session(state, key)  # opened from disk, as the dashboard does
    assert len(session.messages) == 40
    ask = "what should we order first?"
    session.append("user", ask, "msg msg-u")  # the dashboard appends the bubble first
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, ask)

    sent = "\n".join(str(m.get("content") or "") for m in model.requests[0])
    assert "THREAD CONVERSATION HISTORY" in sent, "the chat was not restored at all"
    assert _SUMMARY_MARK in sent, "the fresh runtime was not handed the background summary"
    assert rows[0][1].strip() not in sent, "the summarized span was handed over verbatim"
    assert rows[-1][1].strip() in sent and rows[-2][1].strip() in sent
    assert sent.count(ask) == 1
