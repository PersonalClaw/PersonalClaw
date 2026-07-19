"""Tests for session_health log scanner."""

import datetime
from pathlib import Path

from personalclaw.dashboard import session_health


def _ts_from_file(path: Path) -> str:
    """Return HH:MM:SS derived from file mtime — immune to clock skew."""
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")


def _write_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")
    import os
    os.utime(path, None)


def test_dead_provider_is_not_flagged(tmp_path: Path) -> None:
    """`Session X has dead provider — removing stale entry` is a healthy self-cleanup
    log line, not a stall. The session manager cleaned up a crashed provider; the
    next request cold-starts a fresh session transparently. Must NOT appear in
    the stalled set."""
    log = tmp_path / "gateway.log"
    _write_log(log, [""])  # touch to get mtime
    ts = _ts_from_file(log)
    _write_log(log, [
        f"{ts} WARNING personalclaw.session: Session chat-2-1776999999 has dead provider — removing stale entry",
    ])
    result = session_health.compute_session_health(log_path=log, now=log.stat().st_mtime)
    assert "chat-2-1776999999" not in result
    assert result == {}


def test_detects_prompt_stuck(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    _write_log(log, [""])  # touch to get mtime
    ts = _ts_from_file(log)
    _write_log(log, [
        f"{ts} WARNING personalclaw.dashboard.chat: ACP error in session chat-9-1776732990: Prompt error: {{'code': -32603, 'message': 'Internal error', 'data': 'Prompt already in progress'}}",
    ])
    result = session_health.compute_session_health(log_path=log, now=log.stat().st_mtime)
    assert result["chat-9-1776732990"]["reason"] == "prompt_stuck"


def test_ignores_internal_background_sessions(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    _write_log(log, [""])  # touch to get mtime
    ts = _ts_from_file(log)
    _write_log(log, [
        f"{ts} WARNING personalclaw.gateway: Injected timeout error for subagent abc into session _bg",
        f"{ts} WARNING personalclaw.gateway: Injected timeout error for subagent def into session cron_367da8a3",
        f"{ts} WARNING personalclaw.gateway: Injected timeout error for subagent ghi into session cron:daily_check",
    ])
    result = session_health.compute_session_health(log_path=log, now=log.stat().st_mtime)
    assert result == {}


def test_last_reason_wins_per_session(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    _write_log(log, [""])  # touch to get mtime
    ts = _ts_from_file(log)
    _write_log(log, [
        f"{ts} WARNING personalclaw.gateway: Injected timeout error for subagent abc into session chat-3-111",
        f"{ts} WARNING personalclaw.dashboard.chat: ACP error in session chat-3-111: Prompt already in progress",
    ])
    result = session_health.compute_session_health(log_path=log, now=log.stat().st_mtime)
    assert result["chat-3-111"]["reason"] == "prompt_stuck"


def test_skips_lines_outside_window(tmp_path: Path) -> None:
    log = tmp_path / "gateway.log"
    # Use a timestamp 20 minutes before file mtime — outside the 10-min window
    _write_log(log, [""])  # touch to get mtime
    mtime_dt = datetime.datetime.fromtimestamp(log.stat().st_mtime)
    old = (mtime_dt - datetime.timedelta(minutes=20)).strftime("%H:%M:%S")
    _write_log(log, [
        f"{old} WARNING personalclaw.dashboard.chat: ACP error in session chat-4-222: Prompt already in progress",
    ])
    result = session_health.compute_session_health(log_path=log)
    assert result == {}


def test_returns_empty_when_log_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.log"
    assert session_health.compute_session_health(log_path=missing) == {}
