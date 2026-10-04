"""A save that lets an agent call more tools asks the owner first; one that only narrows does not.

An agent's tool list is what PersonalClaw's own runtime lets it call (``agents.tool_list``), on
every chat and run of it, so it is a standing grant. Measured before this: it was not a security
control on the write path (``_AGENT_FIELD_SPECS``), so a save that added ``bash`` to a list, or
emptied a list (which is every tool), was stored with nobody asked, while the editor's own check
asked on the one tick that NARROWS a list: the first tool ticked on an empty one.

Now the write paths judge the direction against the stored list with the list's own matcher
(``tool_list.widens``) and answer a wider one ``400 confirmation_required`` with the consent the
editor shows, unless the request carries ``confirm: true``, as a looser approval mode is. A
narrower one is stored as it comes. The routes are the real handlers, over a real ``config.json``
in the test's own home.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.edit_spec import LOOSEN_TITLE
from personalclaw.config.loader import AgentProfile, AppConfig

AGENT = "researcher"

#: What the owner is asked to agree to, the sentence the consent dialog shows.
CONSENT = (
    "A wider tool list lets this agent call tools it could not call before, in every chat and "
    "run of it."
)


@web.middleware
async def _as_owner(request: web.Request, handler):
    """What the auth middleware sets for the owner's browser session."""
    request["user"] = "owner"
    return await handler(request)


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import (
        api_agent_detail,
        api_personalclaw_agent_update,
        api_personalclaw_agents,
        api_personalclaw_agents_create,
    )

    app = web.Application(middlewares=[_as_owner])
    # The one thing these routes ask of the gateway's state after a save: to refresh the page.
    app["state"] = SimpleNamespace(push_refresh=lambda _what: None)
    app.router.add_get("/api/agents", api_personalclaw_agents)
    app.router.add_post("/api/agents", api_personalclaw_agents_create)
    app.router.add_put("/api/agents/{name}", api_personalclaw_agent_update)
    app.router.add_patch("/api/agents/detail/{name}", api_agent_detail)
    return app


def _with_tools(tools: list[str]) -> None:
    cfg = AppConfig.load()
    cfg.agents[AGENT] = AgentProfile(provider="native", description="reads papers", tools=tools)
    cfg.save()


def _stored_tools(name: str = AGENT) -> list[str]:
    return list(AppConfig.load().agents[name].tools)


async def _save_tools(c: TestClient, tools: list[str], *, confirm: bool = False):
    """The editor's save of the tool list over the copy it read, as `api.updateAgent` sends it."""
    body = await (await c.get("/api/agents")).json()
    record = next(a for a in body["agents"] if a["name"] == AGENT)
    payload = {"tools": tools, **({"confirm": True} if confirm else {})}
    return await c.put(
        f"/api/agents/{AGENT}", json=payload, headers={"If-Match": f'"{record["revision"]}"'}
    )


async def _asked(resp) -> dict:
    assert resp.status == 400, await resp.text()
    error = (await resp.json())["error"]
    assert error["code"] == "confirmation_required"
    return error["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "saved", "change"),
    [
        pytest.param(["read_file"], ["read_file", "bash"], "Adds “bash”", id="a-tool-added"),
        pytest.param(["read_file", "grep"], [], "“read_file”, “grep” → Every tool", id="emptied"),
        pytest.param(["read_file"], ["read_*"], "Adds “read_*”; removes “read_file”", id="pattern"),
    ],
)
async def test_a_wider_list_is_asked_about_and_stored_only_with_consent(stored, saved, change):
    """🔴 Red before: each of these was stored with nobody asked."""
    _with_tools(stored)
    async with TestClient(TestServer(_app())) as c:
        detail = await _asked(await _save_tools(c, saved))
        assert detail["field"] == f"agents.{AGENT}.tools"
        assert detail["title"] == LOOSEN_TITLE
        assert detail["consent"] == CONSENT
        assert detail["change"] == change
        assert _stored_tools() == stored, "a refused save was written"

        granted = await _save_tools(c, saved, confirm=True)
        assert granted.status == 200, await granted.text()
    assert _stored_tools() == saved


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "saved"),
    [
        pytest.param([], ["bash"], id="the-first-tick-of-an-empty-list"),
        pytest.param(["read_file", "bash"], ["read_file"], id="a-tool-taken-away"),
        pytest.param(["mcp/files/*"], ["mcp/files/read"], id="a-name-the-pattern-covers"),
        pytest.param(["read_file"], ["read_file", "tool_result_get"], id="a-tool-every-list-keeps"),
    ],
)
async def test_a_list_that_only_narrows_is_stored_with_nobody_asked(stored, saved):
    """An empty list is every tool, so its first tick narrows the agent to that one tool."""
    _with_tools(stored)
    async with TestClient(TestServer(_app())) as c:
        resp = await _save_tools(c, saved)
        assert resp.status == 200, await resp.text()
    assert _stored_tools() == saved


@pytest.mark.asyncio
async def test_a_new_agent_starts_from_every_tool_so_its_list_asks_nothing():
    """A created agent is compared with an agent that has no list, which may call every tool."""
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post("/api/agents", json={"name": "writer", "tools": ["write_file"]})
        assert resp.status == 200, await resp.text()
    assert _stored_tools("writer") == ["write_file"]


@pytest.mark.asyncio
async def test_an_agent_clis_runtime_file_widens_only_by_gaining_a_tool_server():
    """The runtime file an agent CLI reads lists the tool servers it may call, where no entry is
    none: adding one asks, and emptying the list, which takes them all away, does not."""
    from personalclaw.agent import agents_dir

    folder = agents_dir()
    folder.mkdir(parents=True, exist_ok=True)
    runtime_file = folder / "personalclaw.json"
    runtime_file.write_text(
        json.dumps({"name": "personalclaw", "tools": ["@personalclaw-core"]}), encoding="utf-8"
    )
    async with TestClient(TestServer(_app())) as c:
        wider = await c.patch(
            "/api/agents/detail/personalclaw",
            json={"tools": ["@personalclaw-core", "@fixture-server"]},
        )
        detail = await _asked(wider)
        assert detail["change"] == "Adds “@fixture-server”"
        assert detail["consent"] == CONSENT
        narrower = await c.patch("/api/agents/detail/personalclaw", json={"tools": []})
        assert narrower.status == 200, await narrower.text()
    assert json.loads(runtime_file.read_text(encoding="utf-8"))["tools"] == []
