"""An agent CLI is offered, and may call, only the PersonalClaw tools its agent's tool list allows.

An agent's tool list (Agents › Tools, ``agents.<name>.tools``) held only on PersonalClaw's own
runtime. An agent on an agent CLI is handed PersonalClaw's tools through its tool servers
(``personalclaw-core``), and those filtered by the session's ``managedToolPolicy`` alone: nothing
read the list, so a narrow agent on a CLI was offered, and could call, every PersonalClaw tool. Now
the gateway's session tool policy carries the agent's list (``agents.tool_list.POLICY_KEY``) and
the tool servers offer only what it allows, and refuse a call outside it in the list's own words,
with an audit row. The CLI's own built-in tools are outside any list: they follow its own
permission mode, which the agent's settings say.

Driven through the gateway's real policy route, whose answer is what the tool server reads.
🔴 Red before: every row that expects the list carried, a tool withheld or a call refused.
"""

from __future__ import annotations

import json
import types
import urllib.error
from email.message import Message
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from fakes import answers_as_this_home

from personalclaw import gateway_base, home_gateway, mcp_shared
from personalclaw.config.loader import AgentProfile, AppConfig

#: A gateway's answer for a session it does not know (404), as ``_server_reads_each`` takes it.
NOT_KNOWN = "404"

#: What the tool server serves, by name: two PersonalClaw tools and the one every list keeps.
SERVED = ("memory_recall", "artifact_save", "tool_result_get")


@pytest.fixture(autouse=True)
def _first_read(monkeypatch):
    """The server's first read of its policy: nothing cached from another test."""
    for name, value in (
        ("_excluded_tools", None),
        ("_agent_list", None),
        ("_resolved_at", 0.0),
        ("_last_failure_time", 0.0),
        ("_last_startup_race_time", 0.0),
        ("_failure_count", 0),
    ):
        monkeypatch.setattr(mcp_shared, name, value, raising=False)


def _agents(**lists: list[str] | None) -> None:
    """The owner's agents, each with the tool list given (``None`` for none), on an agent CLI."""
    cfg = AppConfig.load()
    for name, tools in lists.items():
        cfg.agents[name] = AgentProfile(provider="acp:claude-code", tools=tools or [])
    cfg.save()


#: The unattended work a gateway runs with no agent named: a scheduled job's turn, a channel's.
UNATTENDED = ("cron:digest-7", "channel:slack:C0123")


def _state(agent: str) -> Any:
    """A gateway whose chat ``chat-7`` runs as *agent* (``""`` for none), as do its unattended
    sessions (:data:`UNATTENDED`) and its subagent ``sa-1``."""
    chat = types.SimpleNamespace(agent=agent)
    running = {key: agent for key in UNATTENDED}
    sessions = types.SimpleNamespace(
        get_agent=lambda key: running.get(key, ""), has_session=lambda key: key in running
    )
    subagents = {"sa-1": types.SimpleNamespace(agent=agent)}
    return types.SimpleNamespace(
        get_session=lambda name: chat if name == "chat-7" else None,
        sessions=sessions,
        subagents=subagents,
    )


async def _answer(agent: str, session: str) -> web.Response:
    from personalclaw.dashboard.handlers.sessions import api_session_tool_policy

    app = web.Application()
    app["state"] = _state(agent)
    request = make_mocked_request(
        "GET",
        "/api/session-tool-policy",
        headers={"X-Internal-Secret": "the-gateways-own", "X-Session-Key": session},
        app=app,
    )
    return await api_session_tool_policy(request)


async def _policy(agent: str, session: str = "dashboard:chat-7") -> dict:
    """What the gateway answers the session's tool server for its session tool policy."""
    resp = await _answer(agent, session)
    assert resp.status == 200, resp.text
    return json.loads(resp.body)


def _server_reads(monkeypatch, tmp_path, answer: dict | None) -> MagicMock:
    """The tool server's gateway answers its policy read with *answer* (``None``: unreachable).
    Returns the audit log the server writes to."""
    return _server_reads_each(monkeypatch, tmp_path, [answer])


def _server_reads_each(monkeypatch, tmp_path, answers: list) -> MagicMock:
    """As :func:`_server_reads`, the gateway answering each read with ``answers[0]`` as it is
    then: a test replaces it to change what the next read is answered. Returns the audit log."""
    monkeypatch.setenv(gateway_base.PORT_ENV, "7777")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "dashboard:chat-7")
    (tmp_path / ".local_secret").write_text("test-secret")
    monkeypatch.setattr(home_gateway, "_healthz", answers_as_this_home("test-secret"))

    def opened(_req, timeout=5):
        answer = answers[0]
        if answer is None:
            raise OSError("connection refused")
        if answer == NOT_KNOWN:
            raise urllib.error.HTTPError(_req.full_url, 404, "agent not resolved", Message(), None)
        body = MagicMock()
        body.read.return_value = json.dumps(answer).encode()
        body.__enter__ = MagicMock(return_value=body)
        body.__exit__ = MagicMock(return_value=False)
        return body

    monkeypatch.setattr(mcp_shared.home_gateway, "open_loopback", opened)
    audit = MagicMock()
    monkeypatch.setattr(mcp_shared, "sel", lambda: audit)
    return audit


def _serve(monkeypatch, frames: list[dict]) -> tuple[dict, list[str]]:
    """Run the tool server over *frames*; its answers by request id, and the tools it ran."""
    stdin = types.SimpleNamespace(
        buffer=types.SimpleNamespace(
            readline=iter([(json.dumps(f) + "\n").encode() for f in frames] + [b""]).__next__,
        )
    )
    monkeypatch.setattr(mcp_shared.sys, "stdin", stdin)
    answered: dict[Any, Any] = {}
    monkeypatch.setattr(
        mcp_shared, "respond", lambda req_id, result, error=None: answered.update({req_id: result})
    )
    ran: list[str] = []

    def call(name: str, _args: dict) -> str:
        ran.append(name)
        return "done"

    tools = [{"name": n, "inputSchema": {"type": "object"}} for n in SERVED]
    mcp_shared.run_mcp_stdio_loop("personalclaw-core", "1", lambda: list(tools), call)
    return answered, ran


def _listed(monkeypatch) -> list[str]:
    answered, _ = _serve(monkeypatch, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    return [t["name"] for t in answered[1]["tools"]]


def _call(monkeypatch, name: str) -> tuple[dict, list[str]]:
    frame = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name}}
    answered, ran = _serve(monkeypatch, [frame])
    return answered[2], ran


# ── the gateway's answer ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_policy_carries_the_agents_tool_list():
    _agents(reviewer=["memory_recall"])
    policy = await _policy("reviewer")
    assert policy["agentTools"]["patterns"] == ["memory_recall"]
    assert policy["agentTools"]["agent"] == "reviewer"


@pytest.mark.asyncio
async def test_a_chat_that_names_no_agent_carries_the_default_agents_list():
    from personalclaw.agents.defaults import default_agent_name

    _agents(**{default_agent_name(AppConfig.load()): ["memory_*"]})
    policy = await _policy("")
    assert policy["agentTools"]["patterns"] == ["memory_*"]


@pytest.mark.asyncio
@pytest.mark.parametrize("session", [*UNATTENDED, "subagent:sa-1"])
async def test_unattended_work_that_names_no_agent_carries_the_default_agents_list(session):
    """A scheduled job's turn, a channel's thread, a subagent whose record names no agent: each
    runs as the default agent, as PersonalClaw's own runtime holds it."""
    from personalclaw.agents.defaults import default_agent_name

    _agents(**{default_agent_name(AppConfig.load()): ["memory_*"]})
    policy = await _policy("", session)
    assert policy["agentTools"]["patterns"] == ["memory_*"]


@pytest.mark.asyncio
async def test_a_session_the_gateway_does_not_know_is_not_answered_for():
    """Its tool server asks again shortly: the session may not be registered yet."""
    from personalclaw.agents.defaults import default_agent_name

    _agents(**{default_agent_name(AppConfig.load()): ["memory_*"]})
    assert (await _answer("", "cron:never-started")).status == 404


@pytest.mark.asyncio
async def test_an_agent_with_no_list_carries_none():
    _agents(writer=None)
    assert "agentTools" not in await _policy("writer")


# ── what the tool server offers and answers ───────────────────────────────────


@pytest.mark.asyncio
async def test_the_server_offers_only_what_the_list_allows(monkeypatch, tmp_path):
    _agents(reviewer=["memory_recall"])
    _server_reads(monkeypatch, tmp_path, await _policy("reviewer"))
    assert _listed(monkeypatch) == ["memory_recall", "tool_result_get"]


@pytest.mark.asyncio
async def test_a_call_outside_the_list_is_refused_in_its_words_and_audited(monkeypatch, tmp_path):
    _agents(reviewer=["memory_recall"])
    audit = _server_reads(monkeypatch, tmp_path, await _policy("reviewer"))

    answer, ran = _call(monkeypatch, "artifact_save")

    assert ran == [], "a tool outside the agent's list ran"
    assert answer["isError"] is True
    said = json.dumps(answer)
    assert "reviewer" in said and "artifact_save" in said and "tool list" in said
    (row,) = [c.kwargs for c in audit.log_tool_invocation.call_args_list]
    assert (row["tool_name"], row["outcome"], row["error"]) == (
        "artifact_save",
        "refused",
        "agent_tools",
    )


@pytest.mark.asyncio
async def test_a_call_the_list_allows_runs(monkeypatch, tmp_path):
    _agents(reviewer=["memory_*"])
    _server_reads(monkeypatch, tmp_path, await _policy("reviewer"))
    answer, ran = _call(monkeypatch, "memory_recall")
    assert ran == ["memory_recall"] and not answer.get("isError")


@pytest.mark.parametrize(
    "sent",
    [{"agent": "reviewer", "patterns": "memory_recall"}, {"agent": "reviewer"}, "memory_recall"],
    ids=["patterns-not-a-list", "no-patterns", "not-an-object"],
)
def test_a_list_the_server_cannot_read_allows_only_what_every_list_keeps(
    monkeypatch, tmp_path, sent
):
    _server_reads(monkeypatch, tmp_path, {"agentTools": sent})
    assert _listed(monkeypatch) == ["tool_result_get"]


@pytest.mark.asyncio
async def test_an_agent_with_no_list_is_offered_every_tool(monkeypatch, tmp_path):
    _agents(writer=None)
    _server_reads(monkeypatch, tmp_path, await _policy("writer"))
    assert _listed(monkeypatch) == list(SERVED)


def test_while_the_gateway_does_not_answer_every_tool_is_offered(monkeypatch, tmp_path):
    """The read fails open, as it always has: the server cannot know of a list it was not told."""
    _server_reads(monkeypatch, tmp_path, None)
    assert _listed(monkeypatch) == list(SERVED)


# ── a list saved while a session is open ──────────────────────────────────────


def _aged() -> None:
    """The server's last read of its policy is as old as a read may be before the next."""
    mcp_shared._resolved_at -= mcp_shared._POLICY_FRESH_SECS


@pytest.mark.asyncio
async def test_a_list_narrowed_while_a_session_is_open_refuses_its_next_call(monkeypatch, tmp_path):
    """The tool server lives as long as the CLI, across turns: a list read once would keep
    offering what she has since taken away. Read again once it is a few seconds old, the narrowed
    list refuses the next call. 🔴 Red before: the policy was read once for the server's life."""
    _agents(reviewer=["memory_recall", "artifact_save"])
    answers = [await _policy("reviewer")]
    _server_reads_each(monkeypatch, tmp_path, answers)
    first, ran = _call(monkeypatch, "artifact_save")
    assert ran == ["artifact_save"] and not first.get("isError")

    _agents(reviewer=["memory_recall"])
    answers[0] = await _policy("reviewer")
    _aged()
    answer, ran = _call(monkeypatch, "artifact_save")

    assert ran == [], "a call the narrowed list leaves out ran"
    assert answer["isError"] is True and "tool list" in json.dumps(answer)


@pytest.mark.asyncio
async def test_a_list_read_moments_ago_is_not_read_again(monkeypatch, tmp_path):
    """Within a few seconds of a read, a call is answered by what was read, not by a new read.
    Green before too; it holds the read again to its window."""
    _agents(reviewer=["memory_recall"])
    answers = [await _policy("reviewer")]
    _server_reads_each(monkeypatch, tmp_path, answers)
    _call(monkeypatch, "memory_recall")
    answers[0] = {}  # what a read now would answer: no list at all

    answer, ran = _call(monkeypatch, "artifact_save")

    assert ran == [] and answer["isError"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["unreachable", "session-not-known", "no-session-key"])
async def test_a_read_again_that_fails_keeps_the_list_it_held(monkeypatch, tmp_path, why):
    """A read again that does not answer does not open every tool, whatever stops it: a gateway
    that no longer answers, one that no longer knows the session (404), a key the server can no
    longer find. The list read before holds, with no fail-open row. Green before too, when nothing
    was read again; it holds each of the read again's three guards (planted: each falls back to
    every tool, red)."""
    _agents(reviewer=["memory_recall"])
    answers = [await _policy("reviewer")]
    audit = _server_reads_each(monkeypatch, tmp_path, answers)
    _call(monkeypatch, "memory_recall")

    if why == "unreachable":
        answers[0] = None
    elif why == "session-not-known":
        answers[0] = NOT_KNOWN
    else:
        monkeypatch.delenv("PERSONALCLAW_SESSION_KEY")
    _aged()
    answer, ran = _call(monkeypatch, "artifact_save")

    assert ran == [] and answer["isError"] is True
    assert [c.kwargs["outcome"] for c in audit.log_api_access.call_args_list] == []
