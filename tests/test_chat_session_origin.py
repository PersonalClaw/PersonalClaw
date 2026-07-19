"""The chat-history list classifies each session by ORIGIN so the UI can default
to the user's own chats and hide goal-loop / code-project worker sessions behind a
filter. ``_origin_of`` is the pure classifier; this pins its key/app parsing."""

from __future__ import annotations

from personalclaw.dashboard.chat_handlers import _origin_of


def test_manual_chat_is_manual():
    assert _origin_of("my-chat-abc") == ("manual", "")


def test_loop_worker_keyed_by_prefix():
    assert _origin_of("loop-a3e7768f") == ("loop", "a3e7768f")


def test_code_main_worker():
    # Unified: code is a loop kind — its worker session is loop-<id> (origin "loop").
    assert _origin_of("loop-99875eaa") == ("loop", "99875eaa")


def test_code_task_worker_resolves_to_loop_id():
    # loop-<id>-<taskId> (a parallel code task-worker) → the parent loop id, not
    # "<id>-<taskId>" (the loop id is the leading 8-hex segment).
    assert _origin_of("loop-99875eaa-t-60e24dbb") == ("loop", "99875eaa")


def test_planner_session_has_no_standing_loop():
    # loop-plan-<id> is the stepwise planner session — no loop to link back to.
    assert _origin_of("loop-plan-4171e24c") == ("loop", "")


def test_campaign_worker():
    assert _origin_of("campaign-xyz") == ("campaign", "xyz")


def test_app_tag_fallback_when_no_prefix():
    # A disk-only worker whose key lacks the prefix but carries a persisted app tag.
    assert _origin_of("dashboard-thing", "loop") == ("loop", "")
    # a legacy "code" app tag now maps to the unified "loop" origin
    assert _origin_of("plain", "code") == ("loop", "")


def test_unknown_app_is_manual():
    assert _origin_of("plain", "") == ("manual", "")
    assert _origin_of("plain", "weird") == ("manual", "")
