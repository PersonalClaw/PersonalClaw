"""Unit tests for on-demand Schedule triggering.

trigger_schedule_job validates the job-id locally and POSTs to the running
gateway's /run route via the internal-secret IPC (mcp_core._post). These tests
monkeypatch _post so no gateway is needed.
"""

from __future__ import annotations

import pytest

import personalclaw.mcp_core as mc
import personalclaw.schedule_trigger as st


def test_rejects_bad_job_id() -> None:
    ok, msg = st.trigger_schedule_job("nope!!")
    assert ok is False
    assert "invalid job id" in msg
    # Also empty.
    ok2, _ = st.trigger_schedule_job("")
    assert ok2 is False


def test_success(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: dict = {}

    def fake_post(path: str, body=None):
        posted["path"] = path
        posted["body"] = body
        return {"ok": True, "name": "Nightly Report"}

    monkeypatch.setattr(mc, "_post", fake_post)
    ok, msg = st.trigger_schedule_job("abc123")
    assert ok is True
    assert "Nightly Report" in msg
    # Hits the unified trigger run route with the namespaced id (not a fresh service).
    assert posted["path"] == "/api/triggers/schedule:abc123/run"


def test_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "_post", lambda path, body=None: {"ok": False, "running": True})
    ok, msg = st.trigger_schedule_job("abc123")
    assert ok is False
    assert "already running" in msg


def test_gateway_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "_post", lambda path, body=None: {"error": "connection refused"})
    ok, msg = st.trigger_schedule_job("abc123")
    assert ok is False
    assert "connection refused" in msg


def test_not_found_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    # Gateway returns the 404 body as {"error": "job not found"}.
    monkeypatch.setattr(mc, "_post", lambda path, body=None: {"error": "not found"})
    ok, msg = st.trigger_schedule_job("abc123")
    assert ok is False
    assert "not found" in msg


def test_mcp_tool_registered() -> None:
    from personalclaw.mcp_schedule import _list_tools

    names = {t["name"] for t in _list_tools()}
    assert "schedule_trigger" in names


def test_mcp_tool_invokes_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    from personalclaw import mcp_schedule

    monkeypatch.setattr(st, "trigger_schedule_job", lambda jid: (True, f"triggered {jid}"))
    out = mcp_schedule._call_tool_inner("schedule_trigger", {"job_id": "abc123"})
    assert "triggered abc123" in out
