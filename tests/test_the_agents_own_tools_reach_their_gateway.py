"""An agent's own tools reach the gateway they run under, wherever its home is.

Recall, the project's context, the triage rules, a prompt's render, a batch of subagents, the
self-nudge stop and an artifact's bound project are each served by one of the gateway's own routes.
The tool calls it over loopback with the internal credential the gateway wrote to
``<home>/.local_secret`` when it started, and the gateway admits that credential only on the routes
it lists (``server.INTERNAL_ROUTES`` / ``MIXED_INTERNAL_ROUTES``). ``GET /api/memory/recall`` was
not listed, so on a gateway that asks for a sign-in, which is every install (only the development
server's local-network bypass skips it), ``memory_recall`` failed every time. What it said was the
sentence meant for a browser: "This device isn't signed in to PersonalClaw".

Driven here the way an agent drives it: the real gateway (``start_dashboard``) asking for a sign-in,
its home in a folder that is not ``~/.personalclaw`` with ``HOME`` somewhere else (the layout of a
container's data volume), the tool provider the native agent calls, and the ``mcp-core`` server
an agent CLI runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest

from personalclaw import mcp_core
from personalclaw.tool_providers.registry import create_memory_provider, create_native_provider

#: The browser's sign-in sentence (`token_auth.not_signed_in_notice`). No answer to a tool says it.
SIGN_IN = "signed in to PersonalClaw"

#: The chat whose agent makes the calls, live in the gateway: every call made with the internal
#: credential names the work it is for, and a native agent's runtime names its chat around each.
CHAT = "dashboard:chat-own-tools"

#: Every auth shortcut a test process might inherit. Each one would admit the tool's call before
#: the internal credential is looked at, which is how this stayed unseen in development.
_AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_SESSION_KEY",
)


@dataclass
class _Gateway:
    home: Path
    user_home: Path
    port: int

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    @property
    def secret_path(self) -> Path:
        return self.home / ".local_secret"

    async def owner_token(self) -> str:
        """What `personalclaw token` does: trade `.local_secret` for a session token."""
        async with aiohttp.ClientSession() as http:
            resp = await http.get(
                self.url("/api/token/local"),
                headers={"X-Local-Secret": self.secret_path.read_text().strip()},
            )
            assert resp.status == 200, await resp.text()
            return (await resp.json())["token"]


@contextlib.asynccontextmanager
async def _gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Gateway]:
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)

    runner, state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    state.get_or_create_session(CHAT.removeprefix("dashboard:"), memory_mode="persistent")
    port = runner.addresses[0][1]
    # What the gateway exports to every child it starts (`gateway_base.publish`).
    monkeypatch.setenv("PERSONALCLAW_PORT", str(port))
    try:
        assert home.resolve() != (user_home / ".personalclaw").resolve()
        yield _Gateway(home=home, user_home=user_home, port=port)
    finally:
        await runner.cleanup()


async def _remember_a_fact(gw: _Gateway) -> None:
    """The owner saves a fact on the Memory page."""
    token = await gw.owner_token()
    async with aiohttp.ClientSession() as http:
        resp = await http.put(
            gw.url("/api/memory/semantic"),
            json={
                "key": "user.coffee_order",
                "value": "An oat flat white, no sugar.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200, await resp.text()


@contextlib.contextmanager
def _for_the_chat() -> Iterator[None]:
    """The chat's session bound for the calls made inside, as the native runtime binds it around
    each call it makes (``mcp_core.set_current_session_key``)."""
    token = mcp_core.set_current_session_key(CHAT)
    try:
        yield
    finally:
        mcp_core.reset_current_session_key(token)


async def _call(tool: str, arguments: dict) -> tuple[bool, str]:
    """One call through the provider the native agent's memory tools come from, for its chat."""
    with _for_the_chat():
        result = await create_memory_provider().invoke(tool, arguments)
    return result.success, (result.output if result.success else result.error) or ""


@pytest.mark.asyncio
async def test_memory_recall_reads_back_what_the_owner_saved(tmp_path, monkeypatch):
    """🔴 Red on integration: every recall answered with the browser's sign-in sentence."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        await _remember_a_fact(gw)

        ok, text = await _call("memory_recall", {"query": "coffee order"})

        assert SIGN_IN not in text
        assert ok, text
        assert "An oat flat white, no sugar." in text


@pytest.mark.asyncio
async def test_the_triage_rule_tools_list_add_and_revoke(tmp_path, monkeypatch):
    """🔴 Red on integration: the three calls behind `triage_rules_list` and `triage_rules`
    were refused like recall."""
    async with _gateway(tmp_path, monkeypatch):
        ok, text = await _call("triage_rules_list", {})
        assert ok and "No triage approval rules" in text, text

        ok, text = await _call(
            "triage_rules",
            {"action": "add", "pattern": "archive:sender:news.example.com", "verdict": "deny"},
        )
        assert ok and text.startswith("Added deny rule for archive:sender:news.example.com"), text
        rule_id = text.rsplit("(id: ", 1)[1].rstrip(")")

        ok, text = await _call("triage_rules_list", {})
        assert ok and f"(id: {rule_id})" in text, text

        ok, text = await _call("triage_rules", {"action": "revoke", "id": rule_id})
        assert ok and text == f"Revoked rule {rule_id}", text


@pytest.mark.asyncio
async def test_the_credential_still_cannot_teach_an_approve_rule(tmp_path, monkeypatch):
    """The approval-rule route takes the credential now, for the triage tools. What an agent may not
    do there is still refused, by the route itself: only the owner teaches an approve rule, and
    nothing is written."""
    async with _gateway(tmp_path, monkeypatch):
        rule = {"pattern": "archive:sender:news.example.com", "verdict": "approve"}
        with _for_the_chat():
            answer = await asyncio.to_thread(mcp_core._post, "/api/memory/approval-rules", rule)
        assert answer["error_detail"]["code"] == "approval_owner_only", answer

        ok, text = await _call("triage_rules_list", {})
        assert ok and "No triage approval rules" in text, text


@pytest.mark.asyncio
async def test_get_context_loads_the_projects_context(tmp_path, monkeypatch):
    """🔴 Red on integration. The tool an agent is told to call at the start of every task."""
    async with _gateway(tmp_path, monkeypatch):
        with _for_the_chat():
            result = await create_native_provider().invoke("get_context", {"query": "coffee"})

        text = result.output if result.success else result.error
        assert SIGN_IN not in (text or "")
        assert result.success, text


@pytest.mark.asyncio
async def test_the_mcp_server_an_agent_cli_runs_finds_the_same_credential(tmp_path, monkeypatch):
    """🔴 Red on integration. The ACP path: the gateway starts ``personalclaw mcp-core`` with
    ``PERSONALCLAW_HOME``, ``PERSONALCLAW_PORT`` and the chat it serves declared
    (``acp.mcp_servers``), and that process reads the credential from the home, not from ``HOME``.
    """
    async with _gateway(tmp_path, monkeypatch) as gw:
        await _remember_a_fact(gw)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(gw.user_home),
            "PERSONALCLAW_HOME": str(gw.home),
            "PERSONALCLAW_PORT": str(gw.port),
            "PERSONALCLAW_SESSION_KEY": CHAT,
        }
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "memory_recall", "arguments": {"query": "coffee order"}},
            },
        ]
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "personalclaw",
            "mcp-core",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdin = "".join(json.dumps(r) + "\n" for r in requests).encode()
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=60)

        replies = {m.get("id"): m for m in map(json.loads, out.decode().splitlines()) if m}
        assert 2 in replies, err.decode()[-2000:]
        result = replies[2]["result"]
        text = " ".join(part.get("text", "") for part in result.get("content", []))
        assert SIGN_IN not in text
        assert not result.get("isError"), text
        assert "An oat flat white, no sugar." in text


@pytest.mark.asyncio
async def test_a_route_that_does_not_take_the_credential_says_so(tmp_path, monkeypatch):
    """🔴 Red on integration: a tool calling a route the credential does not open was told to
    sign in, which no tool can do and which named the wrong cause."""
    async with _gateway(tmp_path, monkeypatch):
        answer = await asyncio.to_thread(mcp_core._get, "/api/status")

        assert SIGN_IN not in answer["error"]
        assert answer["error_detail"]["code"] == "internal_route_refused"
        assert "does not accept the gateway's internal credential" in answer["error"]


@pytest.mark.asyncio
async def test_a_credential_this_gateway_did_not_issue_is_named_as_such(tmp_path, monkeypatch):
    """🔴 Red on integration, where recall answered the sign-in sentence whatever the credential
    was, and a listed route answered a wrong one with the bare word "Forbidden"."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        issued = gw.secret_path.read_text()
        gw.secret_path.write_text("0" * 32)
        try:
            ok, text = await _call("memory_recall", {"query": "coffee order"})
        finally:
            gw.secret_path.write_text(issued)

        assert not ok
        assert "is not the one this gateway issued" in text, text


@pytest.mark.asyncio
async def test_a_tool_that_cannot_read_the_credential_says_where_it_looked(tmp_path, monkeypatch):
    """🔴 Red on integration: the tool sent an empty credential and read back the sign-in
    sentence (on a listed route, "Forbidden"). Now it sends nothing, and says which home holds
    no credential."""
    async with _gateway(tmp_path, monkeypatch) as gw:
        away = gw.secret_path.with_name(".local_secret.away")
        gw.secret_path.rename(away)
        try:
            ok, text = await _call("memory_recall", {"query": "coffee order"})
        finally:
            away.rename(gw.secret_path)

        assert not ok
        assert SIGN_IN not in text and "Forbidden" not in text
        assert f"no internal credential in {gw.home.resolve()}" in text, text
