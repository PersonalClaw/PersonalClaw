"""Tests for the fresh-session resumability audit + MCP replay-as-fake-server (Session 4).

The resume-audit proves a persisted loop can answer done/verified/next/how-to-verify from
disk ALONE (no in-memory session) — the audit that would have caught the dead-resume bugs.
The FakeMcpServer proves a recorded mcp trace replays deterministically offline.
"""

from __future__ import annotations

import pytest

from harness import resume_audit
from harness.replay import FakeMcpServer, TraceEvent
from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    # Isolate the loop store to a temp dir (destructive-test-isolation rule).
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    return tmp_path


def _goal(**over) -> Loop:
    base = dict(
        id="",
        name="G",
        kind="goal",
        task="investigate the latency regression and propose a fix",
        project_id="p-1",
        kind_config={"goal_type": "open_ended", "granularity": "balanced"},
    )
    base.update(over)
    return store.create(Loop(**base))


def _code(**over) -> Loop:
    base = dict(
        id="",
        name="C",
        kind="code",
        task="add oauth login",
        project_id="p-1",
        plan=[
            {"stage": "design", "title": "Design", "min_cycles": 1},
            {"stage": "build", "title": "Build", "min_cycles": 2},
        ],
        phase_status={"design": "done"},
        kind_config={"entry_stage": "design", "queued_task_ids": []},
    )
    base.update(over)
    return store.create(Loop(**base))


# ── resume audit ──────────────────────────────────────────────────────────────


def test_missing_loop_is_not_answerable() -> None:
    r = resume_audit.audit_loop("nonexistent-id")
    assert not r.exists
    assert not r.ok
    assert "not found" in r.failures()[0]


def test_freshly_created_goal_loop_is_fully_answerable() -> None:
    g = _goal()
    r = resume_audit.audit_loop(g.id)
    assert r.exists
    assert r.done_answerable  # has a status
    assert r.verified_answerable  # loop dir exists (0 verdicts is a definitive answer)
    assert r.next_answerable  # non-phased: next cycle derivable from findings count
    assert r.how_to_verify_answerable  # persisted task text
    assert r.ok


def test_phased_loop_names_next_stage_from_disk() -> None:
    c = _code()
    r = resume_audit.audit_loop(c.id)
    assert r.ok
    assert r.detail["phased"] is True
    # design is done → next stage is build, derived from plan vs phase_status on disk.
    assert r.detail["next_stage"] == "build"


def test_resume_after_simulated_restart_uses_disk_only() -> None:
    # Create + advance a loop, then audit WITHOUT any in-memory session — the audit reads
    # only persisted state, so this models a fresh process after a crash/restart.
    g = _goal()
    store.update_status(g.id, LoopStatus.RUNNING)
    store.write_verdict(g.id, 1, {"roi": 0.8, "summary": "found the N+1 query"})
    r = resume_audit.audit_loop(g.id)
    assert r.ok
    assert r.detail["status"] == LoopStatus.RUNNING.value
    assert r.detail["verdict_count"] == 1  # verified-from-disk


def test_complete_loop_is_terminal_answerable() -> None:
    g = _goal()
    store.update_status(g.id, LoopStatus.COMPLETE)
    r = resume_audit.audit_loop(g.id)
    assert r.ok
    assert r.detail["terminal_or_attention"] is True


def test_loop_with_blank_task_flags_how_to_verify() -> None:
    # A loop persisted with no task text can't answer "how to verify" from disk.
    g = _goal(task="")
    r = resume_audit.audit_loop(g.id)
    assert not r.how_to_verify_answerable
    assert not r.ok
    assert any("how to verify" in f for f in r.failures())


# ── MCP replay-as-fake-server ───────────────────────────────────────────────


def _mcp_event(tool: str, arguments: dict, ok: bool, output: str) -> TraceEvent:
    return TraceEvent.from_json(
        {
            "ts": 0.0,
            "stream": "mcp",
            "key": "srv",
            "type": "call_tool",
            "payload": {"tool": tool, "arguments": arguments, "ok": ok, "output": output},
        }
    )


def test_fake_mcp_server_replays_recorded_response() -> None:
    server = FakeMcpServer([_mcp_event("search", {"q": "latency"}, True, "3 results")])
    ok, out = server.call_tool("search", {"q": "latency"})
    assert ok is True and out == "3 results"


def test_fake_mcp_server_arg_order_independent() -> None:
    server = FakeMcpServer([_mcp_event("f", {"a": 1, "b": 2}, True, "ok")])
    # Different dict order → same canonical key → same recorded response.
    ok, out = server.call_tool("f", {"b": 2, "a": 1})
    assert ok and out == "ok"


def test_fake_mcp_server_returns_successive_responses() -> None:
    server = FakeMcpServer(
        [
            _mcp_event("t", {}, True, "first"),
            _mcp_event("t", {}, True, "second"),
        ]
    )
    assert server.call_tool("t", {})[1] == "first"
    assert server.call_tool("t", {})[1] == "second"
    # Exhausted → repeats the last (deterministic, never fabricates).
    assert server.call_tool("t", {})[1] == "second"


def test_fake_mcp_server_miss_surfaces_gap() -> None:
    server = FakeMcpServer([_mcp_event("known", {}, True, "x")])
    ok, out = server.call_tool("unknown", {})
    assert ok is False
    assert "no recorded response" in out
