"""Saving an agent CLI's tool list warns of each entry no PersonalClaw tool it is offered matches.

On an agent CLI the list holds the PersonalClaw tools the CLI is offered (``mcp_shared``), and
nothing else: an entry naming a tool of PersonalClaw's own runtime, another tool server's tool, or
a typo lets the CLI call nothing, and the CLI's own built-in tools follow its permission mode. The
save said nothing of it, so an owner who listed ``read_file`` to keep a CLI agent to reading files
had a list that held none of what they meant. The save still stands, since the list holds the tools
it does match; its answer now warns of each such entry, and the agent's page shows the warning.
🔴 Red before: the two rows that expect a warning.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import mcp_core
from personalclaw.config.loader import AgentProfile, AppConfig

AGENT = "reviewer"


@web.middleware
async def _as_owner(request: web.Request, handler):
    """What the auth middleware sets for the owner's browser session."""
    request["user"] = "owner"
    return await handler(request)


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import (
        api_personalclaw_agent_update,
        api_personalclaw_agents,
    )

    app = web.Application(middlewares=[_as_owner])
    app.router.add_get("/api/agents", api_personalclaw_agents)
    app.router.add_put("/api/agents/{name}", api_personalclaw_agent_update)
    return app


def _agent(provider: str) -> None:
    cfg = AppConfig.load()
    cfg.agents = {AGENT: AgentProfile(description="reviews diffs", provider=provider)}
    cfg.save()


async def _save_tools(tools: list[str]) -> dict:
    """The Agents page's save of the list, over the revision it read."""
    async with TestClient(TestServer(_app())) as c:
        listed = await (await c.get("/api/agents")).json()
        revision = next(a for a in listed["agents"] if a["name"] == AGENT)["revision"]
        resp = await c.put(
            f"/api/agents/{AGENT}", json={"tools": tools}, headers={"If-Match": revision}
        )
        body = await resp.json()
        assert resp.status == 200, body
        return body


def test_the_tools_an_agent_cli_is_offered_are_read_from_the_core_tool_server():
    offered = mcp_core.own_tool_names()
    assert "memory_recall" in offered and "read_file" not in offered


@pytest.mark.asyncio
async def test_an_entry_an_agent_cli_is_not_offered_is_warned_of_and_the_list_is_saved():
    _agent("acp:claude-code")
    body = await _save_tools(["memory_recall", "read_file", "mcp/files/*"])

    warned = body["warnings"]
    assert len(warned) == 2, warned
    assert "“read_file”" in warned[0] and "“mcp/files/*”" in warned[1]
    assert all("CLI's own built-in tools follow its permission mode" in w for w in warned)
    assert AppConfig.load().agents[AGENT].tools == ["memory_recall", "read_file", "mcp/files/*"]


@pytest.mark.asyncio
async def test_a_typo_is_warned_of_too():
    _agent("acp:kiro-cli")
    body = await _save_tools(["memory_recal"])
    assert body["warnings"] and "“memory_recal”" in body["warnings"][0]


@pytest.mark.asyncio
async def test_a_list_every_entry_of_which_holds_is_saved_with_no_warning():
    _agent("acp:claude-code")
    assert "warnings" not in await _save_tools(["memory_*", "artifact_*"])


@pytest.mark.asyncio
async def test_personalclaws_own_agent_is_warned_of_nothing():
    """On PersonalClaw's own runtime ``read_file`` is a tool it may call: the list holds it."""
    _agent("native")
    assert "warnings" not in await _save_tools(["read_file", "mcp/files/*"])
