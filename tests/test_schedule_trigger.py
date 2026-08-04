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


def test_the_immediate_fire_tool_is_registered() -> None:
    """`automation_run` is `schedule_trigger`'s successor (S109 retired the alias). Same shape: an
    MCP process cannot own the LLM turn, so an immediate run posts to the gateway's HTTP `/run`."""
    from personalclaw.mcp_automation import _list_tools

    names = {t["name"] for t in _list_tools()}
    assert "automation_run" in names
    assert not [n for n in names if n.startswith("schedule_")]


def test_the_immediate_fire_tool_posts_to_the_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The HTTP hand-off is the contract worth pinning, not the tool's name. Driven through the real
    dispatcher against a real store row, with only the poster stubbed."""
    from personalclaw import mcp_automation
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    TriggerStore(base_dir=tmp_path).upsert(
        Trigger(
            id="clock:abc123",
            name="abc",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 3600},
            workflow={"inline": {"provider": "run-prompt", "config": {"message": "go"}}},
        )
    )
    posted: list[str] = []
    monkeypatch.setattr(
        mcp_automation,
        "_http_runner",
        lambda payload: posted.append(str(payload.get("trigger_id") or "")) or {"status": "ok"},
    )
    mcp_automation._call_tool_inner("automation_run", {"id": "clock:abc123"})
    assert posted == ["clock:abc123"]


def test_an_unknown_id_is_refused_before_any_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A run that posted for an id the store does not have would fire whatever the gateway resolved
    that id to — the wrong automation, or none, reported as success."""
    from personalclaw import mcp_automation

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    posted: list[str] = []
    monkeypatch.setattr(
        mcp_automation, "_http_runner", lambda payload: posted.append("posted") or {"status": "ok"}
    )
    out = mcp_automation._call_tool_inner("automation_run", {"id": "clock:nope"})
    assert "no automation with id" in out
    assert posted == []
