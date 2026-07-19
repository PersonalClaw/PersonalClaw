"""Unit tests for bidirectional schedule→dashboard threading.

Covers inject_schedule_result_to_session: first-open link + hydrate, result
threading + dedup, and that hydration excludes tool/error rows and respects
the recent-N limit. Uses a real DashboardState with mocked sessions/crons.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.schedule_inject import (
    _HYDRATE_LIMIT,
    hydrate_session_from_history,
    inject_schedule_result_to_session,
)
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.schedule import ScheduleJob, make_agent_action


def _make_state() -> DashboardState:
    state = DashboardState(
        sessions=MagicMock(count=0),
        crons=MagicMock(),
        lessons=MagicMock(),
        start_time=time.time(),
    )
    # Real session store + a no-op push so get_or_create_session works.
    state._sessions = {}
    state.push_sessions_update = MagicMock()  # type: ignore[method-assign]

    def _get_or_create(name=None, agent="", **kwargs):
        if name and name in state._sessions:
            return state._sessions[name]
        sess = _ChatSession(key=name or "x", agent=agent)
        state._sessions[name or "x"] = sess
        return sess

    state.get_or_create_session = _get_or_create  # type: ignore[method-assign]
    state.conversation_log = None
    return state


def _job(**kw) -> ScheduleJob:
    return ScheduleJob(id=kw.get("id", "abc123"), name=kw.get("name", "Nightly"),
                       action=make_agent_action(message=kw.get("message", "do it"),
                                                 agent=kw.get("agent_id", "")))


def test_first_open_links_and_threads_result() -> None:
    state = _make_state()
    job = _job()
    session = inject_schedule_result_to_session(state, job, "the result", history=[])
    assert session.linked_session_key == "cron:abc123"
    assert session.title == "Cron: Nightly"
    assert any("the result" in m["content"] for m in session.messages)
    assert state.push_sessions_update.called


def test_result_dedup_on_second_inject() -> None:
    state = _make_state()
    job = _job()
    inject_schedule_result_to_session(state, job, "same result", history=[])
    n_after_first = len(state._sessions["cron-abc123"].messages)
    # Re-inject identical result → no duplicate appended.
    inject_schedule_result_to_session(state, job, "same result", history=[])
    assert len(state._sessions["cron-abc123"].messages) == n_after_first


def test_hydrates_history_on_first_open_only() -> None:
    state = _make_state()
    job = _job()
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "tool", "content": "🔧 ran something"},  # excluded
    ]
    session = inject_schedule_result_to_session(state, job, "", history=history)
    contents = [m["content"] for m in session.messages]
    assert "hello" in contents
    assert "hi there" in contents
    assert "🔧 ran something" not in contents  # tool rows not hydrated

    # Second call must NOT re-hydrate (already linked).
    before = len(session.messages)
    inject_schedule_result_to_session(state, job, "", history=history)
    assert len(session.messages) == before


def test_hydrate_respects_recent_limit() -> None:
    session = _ChatSession(key="cron-x", agent="")
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(_HYDRATE_LIMIT + 20)]
    hydrate_session_from_history(session, msgs)
    assert len(session.messages) == _HYDRATE_LIMIT
    # Kept the most-recent ones.
    assert session.messages[-1]["content"] == f"m{_HYDRATE_LIMIT + 19}"


def test_deleted_job_rebuilds_from_history() -> None:
    """A fired one-shot job (no live job) still threads from cron history."""
    state = _make_state()
    # Simulate the handler's deleted-job path: a synthetic job + history.
    job = ScheduleJob(id="gone99", name="cron-gone99", action=make_agent_action(message=""))
    history = [{"role": "assistant", "content": "final report"}]
    session = inject_schedule_result_to_session(state, job, "", history=history)
    assert session.linked_session_key == "cron:gone99"
    assert any("final report" in m["content"] for m in session.messages)
