"""Tests for context_management module."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_config(tmp_path):
    with patch("personalclaw.context_management.config_dir", return_value=tmp_path):
        yield tmp_path


def test_cap_result_file_no_truncation(tmp_path):
    from personalclaw.context_management import cap_result_file

    p = tmp_path / "small.md"
    p.write_text("short content")
    assert cap_result_file(p) is False
    assert p.read_text() == "short content"


def test_cap_result_file_truncates(tmp_path):
    from personalclaw.context_management import RESULT_FILE_MAX_BYTES, cap_result_file

    p = tmp_path / "big.md"
    p.write_bytes(b"x" * (RESULT_FILE_MAX_BYTES + 10000))
    assert cap_result_file(p) is True
    assert p.stat().st_size <= RESULT_FILE_MAX_BYTES + 200  # marker overhead
    content = p.read_text()
    assert "truncated" in content


def test_cap_streaming_text_short():
    from personalclaw.context_management import cap_streaming_text

    assert cap_streaming_text("short") == "short"


def test_cap_streaming_text_long():
    from personalclaw.context_management import STREAMING_TEXT_MAX_CHARS, cap_streaming_text

    text = "a" * (STREAMING_TEXT_MAX_CHARS + 1000)
    result = cap_streaming_text(text)
    assert len(result) <= STREAMING_TEXT_MAX_CHARS + 20
    assert result.startswith("…(truncated)")


def test_cap_history():
    from personalclaw.context_management import HISTORY_MAX_ENTRIES, cap_history

    entries = [{"i": i} for i in range(HISTORY_MAX_ENTRIES + 100)]
    result = cap_history(entries)
    assert len(result) == HISTORY_MAX_ENTRIES
    assert result[0]["i"] == 100  # oldest kept


def test_check_session_budget_under(tmp_path):
    from personalclaw.context_management import check_session_budget

    (tmp_path / "agent-a.md").write_text("small")
    assert check_session_budget(tmp_path) is False


def test_check_session_budget_over(tmp_path):
    from personalclaw.context_management import SESSION_MAX_BYTES, check_session_budget

    (tmp_path / "agent-a.md").write_bytes(b"x" * (SESSION_MAX_BYTES + 1))
    assert check_session_budget(tmp_path) is True


def test_evict_completed_agents():
    from personalclaw.context_management import evict_completed_agents

    agents = {}
    for i in range(60):
        agents[f"a{i}"] = SimpleNamespace(done=True, started=float(i))
    evicted = evict_completed_agents(agents, max_retained=50)
    assert evicted == 10
    assert len(agents) == 50
    assert "a0" not in agents  # oldest evicted
    assert "a59" in agents  # newest kept


def test_evict_skips_running():
    from personalclaw.context_management import evict_completed_agents

    agents = {
        "running": SimpleNamespace(done=False, started=0.0),
        "done1": SimpleNamespace(done=True, started=1.0),
    }
    evicted = evict_completed_agents(agents, max_retained=1)
    assert evicted == 0  # only 1 completed, within limit


def test_cleanup_stale_sessions(tmp_config):
    import time

    from personalclaw.context_management import cleanup_stale_sessions

    sessions_dir = tmp_config / "sessions"
    sessions_dir.mkdir()
    old = sessions_dir / "old-session"
    old.mkdir()
    (old / "history.jsonl").write_text("{}")
    # Make it old
    import os

    old_time = time.time() - 86400 * 10
    os.utime(old / "history.jsonl", (old_time, old_time))

    new = sessions_dir / "new-session"
    new.mkdir()
    (new / "history.jsonl").write_text("{}")

    cleaned = cleanup_stale_sessions()
    assert cleaned == 1
    assert not old.exists()
    assert new.exists()


def test_orchestration_tracker_failure_limit():
    from personalclaw.context_management import OrchestrationTracker

    t = OrchestrationTracker()
    assert t.record_failure("task-a") is False  # 1
    assert t.record_failure("task-a") is False  # 2
    assert t.record_failure("task-a") is True  # 3 — limit reached
    assert t.failure_count("task-a") == 3


def test_orchestration_tracker_success_resets():
    from personalclaw.context_management import OrchestrationTracker

    t = OrchestrationTracker()
    t.record_failure("task-a")
    t.record_failure("task-a")
    t.record_success("task-a")
    assert t.failure_count("task-a") == 0
    assert t.record_failure("task-a") is False  # reset to 1


def test_orchestration_tracker_stage_timeout():
    from personalclaw.context_management import OrchestrationTracker

    t = OrchestrationTracker(stage_timeout_seconds=10)
    assert t.is_stage_timed_out() is False  # no stage started
    t.record_round(1)  # starts timer
    assert t.is_stage_timed_out() is False  # just started
    # Simulate elapsed time
    t._stage_start = time.monotonic() - 11
    assert t.is_stage_timed_out() is True


def test_orchestration_tracker_timeout_zero_disables():
    from personalclaw.context_management import OrchestrationTracker

    t = OrchestrationTracker(stage_timeout_seconds=0)
    t.record_round(1)
    t._stage_start = time.monotonic() - 9999
    assert t.is_stage_timed_out() is False  # disabled


def test_orchestration_tracker_timeout_human():
    from personalclaw.context_management import OrchestrationTracker

    assert OrchestrationTracker(stage_timeout_seconds=90).timeout_human == "1m30s"
    assert OrchestrationTracker(stage_timeout_seconds=60).timeout_human == "1m"
    assert OrchestrationTracker(stage_timeout_seconds=45).timeout_human == "45s"
    assert OrchestrationTracker(stage_timeout_seconds=1800).timeout_human == "30m"


def test_stage_timeout_resets_after_guidance():
    from personalclaw.context_management import OrchestrationTracker

    t = OrchestrationTracker(stage_timeout_seconds=10)
    t.record_round(1)  # starts timer
    assert t._stage_start > 0
    t.reset_after_guidance()  # clears timer (task failure path)
    assert t._stage_start == 0.0
    t.record_round(1)  # must restart timer — core fix
    assert t._stage_start > 0
    assert not t.is_stage_timed_out()


def test_tracker_round_limit():
    from personalclaw.context_management import MAX_STAGE_ROUNDS, OrchestrationTracker

    t = OrchestrationTracker()
    for _ in range(MAX_STAGE_ROUNDS - 1):
        assert t.record_round(1) is False
    assert t.record_round(1) is True  # limit reached
    assert t.round_count(1) == MAX_STAGE_ROUNDS


def test_tracker_escalation_and_force_fail():
    from personalclaw.context_management import (
        MAX_STAGE_ROUNDS,
        OrchestrationTracker,
    )

    t = OrchestrationTracker()
    # First escalation: hit round limit, then reset
    for _ in range(MAX_STAGE_ROUNDS):
        t.record_round(1)
    assert t.has_escalated
    t.reset_after_guidance()
    assert t.round_count(1) == 0
    assert not t.is_force_failed(1)

    # Second escalation: hit round limit again, then reset → force-fail
    for _ in range(MAX_STAGE_ROUNDS):
        t.record_round(1)
    t.reset_after_guidance()
    assert t.is_force_failed(1)


def test_tracker_current_stage_default():
    from personalclaw.context_management import OrchestrationTracker

    t = OrchestrationTracker()
    assert t.current_stage == 1  # default when no rounds recorded


def test_tracker_stop():
    from personalclaw.context_management import OrchestrationTracker

    t = OrchestrationTracker()
    assert not t.stopped
    t.stop()
    assert t.stopped


def test_tracker_reset_clears_task_failures():
    from personalclaw.context_management import MAX_STAGE_ROUNDS, OrchestrationTracker

    t = OrchestrationTracker()
    t.record_failure("task-a")
    t.record_failure("task-a")
    # Need to hit round limit to trigger has_escalated
    for _ in range(MAX_STAGE_ROUNDS):
        t.record_round(1)
    t.reset_after_guidance()
    assert t.failure_count("task-a") == 0


# ── looks_like_plan ─────────────────────────────────────────────────
