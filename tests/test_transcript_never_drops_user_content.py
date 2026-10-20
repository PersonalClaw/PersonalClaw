"""A long answer never deletes the prompt, and a restart keeps everything the user saw.

The transcript is the user's data. Every test here drives the REAL store — a
``ConversationLog`` over ``tmp_path``, the real ``run_chat`` turn engine behind the real
``POST /api/chat``, the real shutdown flush ``save_all_sessions_to_history`` — and then
simulates a gateway restart the way one actually happens: a new ``DashboardState`` over
the same directory, which either restores recent sessions at boot or (the default,
``restore_sessions: false``) opens a chat from disk when the user clicks it.

What each test pins, and what it measured on the base it was written against:

* **A long answer deleted its own prompt.** Every streamed CHUNK was appended to the
  session buffer as its own entry, and the buffer silently trimmed its oldest entries at
  10,000 — so ~10,000 chunks pushed the user's prompt out. The overwrite guard then saw
  "buffer 1, disk 1" and refused to save the answer at all; the shutdown flush finally
  forced the one-message buffer over the file, deleting the only copy of the prompt.
* **A partial answer beside a provider error, and a resolved approval, vanished on
  restart.** Both were served while the gateway ran; neither was ever written.
* **A restored or reopened long chat lost its head.** Loading kept only the last 500
  (boot restore) or 200 (open from disk) messages, a new turn then went unsaved, and the
  shutdown flush rewrote the file from the window — the oldest messages were gone.
* **Resume stripped every message's ``meta``** (tool output, citations, telemetry) from
  memory, and the next save wrote the stripped copy over the file.
* **The chat list showed a file-size guess** (``size / 200``) for any chat not in memory.
* **Pagination served the last turn twice**, because the per-turn ``done`` sentinel sat in
  the transcript buffer and was counted as a message.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app

from personalclaw.dashboard.chat import (
    restore_recent_sessions,
    save_all_sessions_to_history,
)
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    LLMEvent,
)

# More chunks than the buffer cap that used to trim the transcript (10,000).
_LONG_ANSWER_CHUNKS = 10_005
_PROMPT = "Count from 1 to 10005 as digits separated by spaces. No other text."


def _state(tmp_path: Path, monkeypatch) -> DashboardState:
    """One gateway process's dashboard state over the shared on-disk store."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
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


def _provider(state: DashboardState, stream) -> AsyncMock:
    """Bind a provider whose turn is the async generator *stream*."""
    client = AsyncMock()
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = stream
    client.stream_command = stream
    client.approve_tool = AsyncMock()
    client.reject_tool = AsyncMock()
    state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    return client


def _events(events: list[LLMEvent]):
    async def _stream(_msg):
        for ev in events:
            yield ev

    return _stream


async def _send(http: TestClient, state: DashboardState, key: str, text: str) -> None:
    """Send *text* the way the web client does and wait for the turn to finish.

    A scripted provider can finish the whole turn before the response is read, so "the
    turn is over" is `session.task` having been cleared by the turn's own `finally`.
    """
    resp = await http.post("/api/chat?ws=1", json={"message": text, "session": key})
    assert resp.status == 200, await resp.text()
    session = state._sessions[key]
    for _ in range(12_000):
        if session.task is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the turn on {key} did not finish within 120s")


def _shape(messages: list[dict]) -> list[tuple]:
    """What a served message IS to the reader: role, text, and an approval's outcome."""
    out: list[tuple] = []
    for m in messages:
        row: tuple = (m["role"], m.get("content", ""))
        if m["role"] == "permission":
            row += ((m.get("meta") or {}).get("resolved"),)
        out.append(row)
    return out


async def _served(http: TestClient, key: str) -> list[tuple]:
    resp = await http.get(f"/api/chat/sessions/{key}")
    assert resp.status == 200, await resp.text()
    return _shape((await resp.json())["messages"])


async def _listed_count(http: TestClient, key: str):
    resp = await http.get("/api/chat/sessions")
    assert resp.status == 200
    rows = [r for r in await resp.json() if r["key"] == key]
    assert len(rows) == 1, f"{key} must be listed exactly once, got {rows}"
    return rows[0]["messages"]


def _disk_roles(state: DashboardState, key: str) -> list[str]:
    return [m["role"] for m in state.conversation_log.read_messages(f"dashboard:{key}")]


def _restart(tmp_path: Path, monkeypatch, state: DashboardState, *, restore: bool):
    """Stop this gateway the way `_shutdown` does and start a fresh one on the same disk."""
    save_all_sessions_to_history(state)
    fresh = _state(tmp_path, monkeypatch)
    if restore:
        restore_recent_sessions(fresh, 30)
    return fresh


def _write_chat(tmp_path: Path, key: str, count: int) -> list[str]:
    """A persisted chat of *count* alternating user/assistant messages; returns contents."""
    contents = [f"message {i}" for i in range(count)]
    lines = [
        json.dumps(
            {"_type": "metadata", "created_at": "2026-09-01T00:00:00", "last_consolidated": 0}
        )
    ]
    for i, text in enumerate(contents):
        role = "user" if i % 2 == 0 else "assistant"
        lines.append(json.dumps({"role": role, "content": text, "ts": f"2026-09-01T00:00:{i:06d}"}))
    (tmp_path / f"dashboard_{key}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return contents


# ── 1. A long answer ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [False, True], ids=["opened-from-disk", "restored-at-boot"])
async def test_a_long_answer_keeps_its_prompt_and_survives_a_restart(
    tmp_path, monkeypatch, restore
):
    state = _state(tmp_path, monkeypatch)
    key = "chat-11-1790358361"
    state.get_or_create_session(key)
    words = [f"{i} " for i in range(1, _LONG_ANSWER_CHUNKS + 1)]
    answer = "".join(words)
    _provider(
        state,
        _events(
            [LLMEvent(kind=EVENT_TEXT_CHUNK, text=w) for w in words]
            + [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]
        ),
    )

    async with TestClient(TestServer(_make_app(state))) as http:
        await _send(http, state, key, _PROMPT)
        live = await _served(http, key)

    assert live == [("user", _PROMPT), ("assistant", answer)], (
        "the open conversation must hold the question AND the whole answer — the prompt was "
        "trimmed out of the buffer by the answer's own chunks"
    )
    # The turn's own save must land the answer: no shutdown flush has run yet, so this is
    # what a crash right now would leave on disk.
    assert _disk_roles(state, key) == ["user", "assistant"]

    fresh = _restart(tmp_path, monkeypatch, state, restore=restore)
    async with TestClient(TestServer(_make_app(fresh))) as http:
        after = await _served(http, key)
        listed = await _listed_count(http, key)

    assert after == [("user", _PROMPT), ("assistant", answer)]
    assert listed == 2


# ── 2. What the user saw survives a restart ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [False, True], ids=["opened-from-disk", "restored-at-boot"])
async def test_a_partial_answer_beside_a_provider_error_survives_a_restart(
    tmp_path, monkeypatch, restore
):
    state = _state(tmp_path, monkeypatch)
    key = "chat-6-1790357456"
    state.get_or_create_session(key)

    async def _dies_mid_answer(_msg):
        for w in ("The capital ", "of France ", "is"):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=w)
        raise RuntimeError("upstream connection reset by peer")

    _provider(state, _dies_mid_answer)

    async with TestClient(TestServer(_make_app(state))) as http:
        await _send(http, state, key, "What is the capital of France?")
        before = await _served(http, key)

    texts = [row[1] for row in before]
    assert "The capital of France is" in texts, f"the partial answer must be served: {before}"
    assert [row[0] for row in before][-1] == "error", f"the provider error must be served: {before}"

    fresh = _restart(tmp_path, monkeypatch, state, restore=restore)
    async with TestClient(TestServer(_make_app(fresh))) as http:
        after = await _served(http, key)
        listed = await _listed_count(http, key)

    assert after == before, "a restart must keep the partial answer AND its error, as served"
    assert listed == len(after)


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [False, True], ids=["opened-from-disk", "restored-at-boot"])
async def test_a_resolved_approval_survives_a_restart(tmp_path, monkeypatch, restore):
    state = _state(tmp_path, monkeypatch)
    key = "chat-18-1790364719"
    state.get_or_create_session(key)
    _provider(
        state,
        _events(
            [
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="Running it now."),
                LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    title="bash",
                    tool_kind="execute",
                    request_id="req-appr-1",
                    tool_call_id="tc-appr-1",
                    tool_input={"command": "echo APPR"},
                ),
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="It printed APPR."),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        ),
    )

    async with TestClient(TestServer(_make_app(state))) as http:
        resp = await http.post("/api/chat?ws=1", json={"message": "echo APPR", "session": key})
        assert resp.status == 200
        session = state._sessions[key]
        task = session.task
        for _ in range(400):
            if "req-appr-1" in session._approval_futures:
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("the turn never asked for approval")
        # The user clicks Allow on the card.
        resp = await http.post(
            f"/api/chat/sessions/{key}/approve",
            json={"action": "approved", "request_id": "req-appr-1"},
        )
        assert resp.status == 200, await resp.text()
        await asyncio.wait_for(asyncio.shield(task), timeout=60)
        before = await _served(http, key)

    assert ("permission", "bash", "approved") in before, before

    fresh = _restart(tmp_path, monkeypatch, state, restore=restore)
    async with TestClient(TestServer(_make_app(fresh))) as http:
        after = await _served(http, key)
        resp = await http.get(f"/api/chat/sessions/{key}/map")
        assert resp.status == 200, await resp.text()
        kinds = [mark["kind"] for mark in await resp.json()]

    assert after == before, "the approval record must survive the restart, resolved as shown"
    assert "approval" in kinds, f"the session map must still mark the approval: {kinds}"


# ── 3. The chat list counts real messages ───────────────────────────────────


@pytest.mark.asyncio
async def test_the_listed_count_after_a_restart_is_the_real_count(tmp_path, monkeypatch):
    """Both doors a restarted list reads — a chat still on disk, and one restored at boot —
    must list the count the open conversation serves, and agree with each other."""
    state = _state(tmp_path, monkeypatch)
    key = "chat-1-1790341379"
    state.get_or_create_session(key)
    # A two-message chat whose answer is long enough that `size / 200` guesses ~20.
    answer = "A thorough answer. " * 200
    _provider(
        state,
        _events(
            [
                LLMEvent(kind=EVENT_TEXT_CHUNK, text=answer),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        ),
    )
    async with TestClient(TestServer(_make_app(state))) as http:
        await _send(http, state, key, "Explain it thoroughly.")
        assert await _listed_count(http, key) == 2

    on_disk = _restart(tmp_path, monkeypatch, state, restore=False)
    async with TestClient(TestServer(_make_app(on_disk))) as http:
        listed_from_disk = await _listed_count(http, key)
        served = await _served(http, key)
    restored = _restart(tmp_path, monkeypatch, on_disk, restore=True)
    async with TestClient(TestServer(_make_app(restored))) as http:
        listed_restored = await _listed_count(http, key)
    assert listed_from_disk == listed_restored == len(served) == 2


def test_a_session_list_row_counts_the_lines_of_a_file_no_count_was_recorded_for(tmp_path):
    """A file written by another writer (a channel app's append) still lists its real count."""
    log = ConversationLog(base_dir=tmp_path)
    for i in range(7):
        log.append("slack_thread_1", "user" if i % 2 == 0 else "assistant", "x" * 900)
    row = next(s for s in log.list_sessions() if s["key"] == "slack_thread_1")
    assert row["messages"] == 7


# ── 4. No window over the transcript loses its head ─────────────────────────


@pytest.mark.asyncio
async def test_a_long_chat_restored_at_boot_keeps_every_older_message(tmp_path, monkeypatch):
    key = "chat-2-1790356614"
    contents = _write_chat(tmp_path, key, 600)
    state = _state(tmp_path, monkeypatch)
    assert restore_recent_sessions(state, 0) == 1
    _provider(
        state,
        _events(
            [
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="answer 601"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        ),
    )
    async with TestClient(TestServer(_make_app(state))) as http:
        await _send(http, state, key, "question 600")
        served = await _served(http, key)
    assert [row[1] for row in served] == contents + ["question 600", "answer 601"]
    # The new turn reached disk on its own, and the older head is still there.
    on_disk = [m["content"] for m in state.conversation_log.read_messages(f"dashboard:{key}")]
    assert on_disk == contents + ["question 600", "answer 601"]

    save_all_sessions_to_history(state)
    on_disk = [m["content"] for m in state.conversation_log.read_messages(f"dashboard:{key}")]
    assert on_disk == contents + ["question 600", "answer 601"]


@pytest.mark.asyncio
async def test_a_long_chat_opened_after_a_restart_serves_and_keeps_every_message(
    tmp_path, monkeypatch
):
    key = "chat-7-1790357612"
    contents = _write_chat(tmp_path, key, 300)
    state = _state(tmp_path, monkeypatch)
    _provider(
        state,
        _events(
            [
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="answer 301"),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        ),
    )
    async with TestClient(TestServer(_make_app(state))) as http:
        opened = await _served(http, key)
        assert [row[1] for row in opened] == contents, "opening the chat must show all of it"
        assert await _listed_count(http, key) == 300
        await _send(http, state, key, "question 300")
    on_disk = [m["content"] for m in state.conversation_log.read_messages(f"dashboard:{key}")]
    assert on_disk == contents + ["question 300", "answer 301"]

    save_all_sessions_to_history(state)
    on_disk = [m["content"] for m in state.conversation_log.read_messages(f"dashboard:{key}")]
    assert on_disk == contents + ["question 300", "answer 301"]


@pytest.mark.asyncio
async def test_a_resumed_chat_keeps_its_tool_details(tmp_path, monkeypatch):
    key = "chat-9-1790357831"
    rows = [
        {"role": "user", "content": "list files", "ts": "2026-09-01T00:00:01"},
        {
            "role": "tool",
            "content": "bash",
            "ts": "2026-09-01T00:00:02",
            "meta": {"tool_call_id": "tc1", "output": "a.txt\nb.txt", "done": True},
        },
        {"role": "assistant", "content": "Two files.", "ts": "2026-09-01T00:00:03"},
    ]
    meta = {"_type": "metadata", "created_at": "2026-09-01T00:00:00", "closed": True}
    (tmp_path / f"dashboard_{key}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [meta, *rows]) + "\n", encoding="utf-8"
    )
    state = _state(tmp_path, monkeypatch)
    async with TestClient(TestServer(_make_app(state))) as http:
        resp = await http.post(f"/api/chat/sessions/{key}/resume", json={})
        assert resp.status == 200
        served = (await resp.json())["messages"]
    tool = next(m for m in served if m["role"] == "tool")
    assert tool.get("meta", {}).get("output") == "a.txt\nb.txt"

    save_all_sessions_to_history(state)
    on_disk = state.conversation_log.read_messages(f"dashboard:{key}")
    assert next(m for m in on_disk if m["role"] == "tool")["meta"]["output"] == "a.txt\nb.txt"


# ── 5. Stream bookkeeping is not transcript ─────────────────────────────────


@pytest.mark.asyncio
async def test_pagination_serves_each_message_once(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    key = "chat-3-1790341704"
    state.get_or_create_session(key)
    async with TestClient(TestServer(_make_app(state))) as http:
        for i in range(3):
            _provider(
                state,
                _events(
                    [
                        LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answer {i}"),
                        LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
                    ]
                ),
            )
            await _send(http, state, key, f"question {i}")
        whole = await (await http.get(f"/api/chat/sessions/{key}")).json()
        page = await (await http.get(f"/api/chat/sessions/{key}?limit=50")).json()
    expected = [c for i in range(3) for c in (f"question {i}", f"answer {i}")]
    assert [m["content"] for m in whole["messages"]] == expected
    assert whole["total"] == len(expected)
    assert [m["content"] for m in page["messages"]] == expected
    assert page["total"] == len(expected)


def test_ten_thousand_messages_are_never_trimmed(tmp_path, monkeypatch):
    """No cap may silently drop what the user wrote — not even past 10,000 entries."""
    from personalclaw.dashboard.chat import save_session_to_history

    state = _state(tmp_path, monkeypatch)
    session = state.get_or_create_session("chat-4-1790341852")
    for i in range(10_050):
        session.append("user", f"note {i}")
    assert session.messages[0]["content"] == "note 0"
    assert len(session.messages) == 10_050
    save_session_to_history(state, session)
    on_disk = state.conversation_log.read_messages("dashboard:chat-4-1790341852")
    assert len(on_disk) == 10_050
    assert on_disk[0]["content"] == "note 0"
