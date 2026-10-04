"""Unit tests for on-demand Schedule triggering.

trigger_schedule_job POSTs to the running gateway's /run route with the internal credential, the
id percent-encoded, on this home's gateway (``home_gateway.reach``). These tests stand a gateway
in for ``reach`` that records what it was sent and answers as told, so no gateway is needed;
`test_cron_trigger_takes_the_ids_cron_add_makes.py` drives it against the real routes.
"""

from __future__ import annotations

from typing import Any

import pytest

import personalclaw.schedule_trigger as st
from personalclaw import home_gateway


class _Gateway:
    """This home's gateway as ``trigger_schedule_job`` reaches it: what it was sent, and its
    answer."""

    def __init__(self, answer: dict[str, Any], status: int = 200) -> None:
        self.answer = answer
        self.status = status
        self.posted: list[dict[str, Any]] = []

    def post(self, path: str, body: dict, *, secret_header: str, work: str = "") -> tuple:
        self.posted.append({"path": path, "body": body, "header": secret_header, "work": work})
        return self.status, self.answer


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, unset_env):
    """``gateway(answer, status=200)``: this home's gateway answers the run route so."""
    unset_env("PERSONALCLAW_SESSION_KEY")

    def install(answer: dict[str, Any], status: int = 200) -> _Gateway:
        reached = _Gateway(answer, status)
        monkeypatch.setattr(home_gateway, "reach", lambda port=None: reached)
        return reached

    return install


def test_an_empty_id_is_refused_before_any_post(gateway) -> None:
    reached = gateway({"ok": True})
    assert st.trigger_schedule_job("  ") == (False, "no job id given")
    assert reached.posted == []


def test_the_id_is_percent_encoded_into_the_path(gateway) -> None:
    """An app's job name can hold any character; none of them may change the route."""
    reached = gateway({"ok": True})
    assert st.trigger_schedule_job("app:x:Nightly Sync/../../tokens?all=1")[0] is True
    assert [p["path"] for p in reached.posted] == [
        "/api/triggers/schedule:app%3Ax%3ANightly%20Sync%2F..%2F..%2Ftokens%3Fall%3D1/run"
    ]


def test_success(gateway) -> None:
    reached = gateway({"ok": True, "name": "Nightly Report"})
    ok, msg = st.trigger_schedule_job("abc123")
    assert ok is True
    assert "Nightly Report" in msg
    # Hits the unified trigger run route with the namespaced id (not a fresh service), with the
    # internal credential, naming its own command: run with no terminal here, a dispatch with no
    # session, so its run is the automation firing (`test_a_run_anyone_but_you_asks_for_is_a_fire`).
    (posted,) = reached.posted
    assert posted["path"] == "/api/triggers/schedule:abc123/run"
    assert (posted["header"], posted["work"]) == (
        "X-Internal-Secret",
        "unattended:cli:cron-trigger",
    )


@pytest.mark.parametrize(
    ("note", "said"),
    [
        ("failed: no chat model is bound", "'Nightly' failed: no chat model is bound"),
        ("no action provider configured", "'Nightly' did not run: no action provider configured"),
    ],
)
def test_a_run_that_did_not_succeed_says_why(note, said, gateway) -> None:
    """It said "trigger failed" and dropped the run route's own note on why."""
    gateway({"ok": False, "name": "Nightly", "result": note, "status": ""})
    assert st.trigger_schedule_job("clock:nightly") == (False, said)


def test_already_running(gateway) -> None:
    gateway({"ok": False, "running": True})
    ok, msg = st.trigger_schedule_job("abc123")
    assert ok is False
    assert "already running" in msg


def test_no_gateway_of_this_home_running_is_said_and_nothing_is_sent(monkeypatch) -> None:
    sentence = "No gateway is running for this home (/home/user/.personalclaw). Start it with: …"

    def none_running(port=None):
        raise home_gateway.NoGatewayRunning(sentence)

    monkeypatch.setattr(home_gateway, "reach", none_running)
    assert st.trigger_schedule_job("abc123") == (False, sentence)


def test_not_found_passthrough(gateway) -> None:
    # Gateway returns the 404 body as {"error": "job not found"}.
    gateway({"error": "not found"}, status=404)
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
            # Granted, as `tools.create` freezes it: the run it posts is refused otherwise.
            capabilities={"providers": ["run-prompt"]},
        )
    )
    posted: list[str] = []
    monkeypatch.setattr(
        mcp_automation,
        "_http_runner",
        lambda payload: posted.append(str(payload.get("trigger_id") or ""))
        or {"ok": True, "result": "ran"},
    )
    out = mcp_automation._call_tool_inner("automation_run", {"id": "clock:abc123"})
    assert posted == ["clock:abc123"]
    assert "\n  result: ran\n" in out, out


def test_an_unknown_id_is_refused_before_any_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A run that posted for an id the store does not have would fire whatever the gateway resolved
    that id to — the wrong automation, or none, reported as success."""
    from personalclaw import mcp_automation

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    posted: list[str] = []
    monkeypatch.setattr(
        mcp_automation, "_http_runner", lambda payload: posted.append("posted") or {"ok": True}
    )
    out = mcp_automation._call_tool_inner("automation_run", {"id": "clock:nope"})
    assert "no automation with id" in out
    assert posted == []
