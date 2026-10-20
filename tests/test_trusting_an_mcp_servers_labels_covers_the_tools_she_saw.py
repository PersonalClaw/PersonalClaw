"""Trusting an MCP server's read-only labels covers exactly the tools the owner saw then.

A server labels its own tools, and a label it gives a tool later is one she never saw. So the trust
is kept with a digest of each tool's definition (`personalclaw.mcp_read_only_trust`): a tool the
server adds since, or one whose description, input schema or labels changed, asks like any untrusted
server's tool until she reviews it on the Tools page, which seals the tools again as the page showed
them. A tool still as she saw it runs unasked, as it did before.

Driven through the owner's own surface (the server's card, ``GET /api/mcp``, and its Trust, Review
and Stop trusting, ``/api/mcp/servers/{name}/read-only-trust``) and through the gate an agent's call
meets (the MCP Tool Servers provider, and a native session's turns), over a server in this process:
a connection whose listing each test sets as a server's release would change it, with each start
recorded the way a connection records one (`mcp_discovery.note_start`). No MCP server is run: the
configured command names nothing that exists.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.mcp_client import McpToolSpec

NAME = "wiki"
#: A command that names nothing: the server is the in-process one below, never a program.
SPEC = {"command": "/nonexistent/pc-fixture-wiki-mcp"}
TRUST_URL = f"/api/mcp/servers/{NAME}/read-only-trust"

_INPUTS = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}


def _tool(
    name: str,
    description: str,
    *,
    reads: bool = True,
    schema: dict | None = None,
    labels: dict | None = None,
) -> McpToolSpec:
    return McpToolSpec(
        name=name,
        description=description,
        input_schema=dict(schema if schema is not None else _INPUTS),
        annotations=dict(labels if labels is not None else {"readOnlyHint": reads}),
    )


SEARCH = _tool("search", "Search the team wiki.")
EDIT = _tool("edit_page", "Change a page of the team wiki.", reads=False)
#: A tool the server's next release adds, and labels read-only.
EXPORT = _tool("export_all", "Export every page of the team wiki.")
#: `search` with only its description changed.
SEARCH_REWORDED = _tool("search", "Search the team wiki and its archive.")


class _Server:
    """An MCP server in this process: what it lists, which a test changes as a release would, each
    start recorded where the card and the trust read it, and the calls it was sent."""

    def __init__(self, tools: list[McpToolSpec]) -> None:
        self.tools = list(tools)
        self.calls: list[str] = []

    def start(self, tools: list[McpToolSpec] | None = None) -> None:
        """A start of the server, listing *tools* (the last listing when None), recorded as a
        connection records its start."""
        from personalclaw.mcp_discovery import definition_seal, note_start

        if tools is not None:
            self.tools = list(tools)
        note_start(NAME, definition_seal(NAME, SPEC), tools=list(self.tools))

    async def list_tools(self) -> list[McpToolSpec]:
        return list(self.tools)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.calls.append(tool)
        return True, f"{tool} ran"


class _Registry:
    """The connected servers, as the MCP Tool Servers provider reads them."""

    def __init__(self, server: _Server) -> None:
        self._server = server

    def items(self) -> list[tuple[str, _Server]]:
        return [(NAME, self._server)]

    def get(self, name: str, _session_key: str | None = None) -> _Server | None:
        return self._server if name == NAME else None


_MCP_TOOLS = load_bundle_module(NATIVE_DIR / "mcp-tools", "mcp-tools", "provider")


def _provider(server: _Server) -> Any:
    registry = _Registry(server)
    return _MCP_TOOLS.McpToolProvider(lambda: registry)


async def _asks(server: _Server) -> dict[str, bool]:
    """Whether each of the server's tools asks before it runs, as an agent is handed it."""
    return {t.name: t.requires_approval for t in await _provider(server).list_tools()}


@pytest.fixture()
def wiki(monkeypatch):
    """The server, configured in ``mcp.json`` and allowed by the owner, and nothing it found yet."""
    from mcp_owner_allowed import allow_configured

    from personalclaw.config.loader import config_dir
    from personalclaw.config.secret_refs import write_mcp_document
    from personalclaw.dashboard.handlers import mcp as mcp_handlers
    from personalclaw.mcp_discovery import forget_probe

    write_mcp_document(config_dir() / "mcp.json", {"mcpServers": {NAME: dict(SPEC)}})
    allow_configured(NAME)
    forget_probe(NAME)
    # The page's own every-ten-minutes look at each server is not what these tests ask about: the
    # starts they record are what the card shows.
    monkeypatch.setattr(mcp_handlers, "_mcp_probe_ts", time.time())
    yield _Server([SEARCH, EDIT])
    forget_probe(NAME)


class _Page:
    """The Tools page's reads and writes for the server, over the gateway's own handlers."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    async def card(self) -> dict[str, Any]:
        resp = await self.client.get("/api/mcp")
        assert resp.status == 200, await resp.text()
        return next(row for row in await resp.json() if row["name"] == NAME)

    async def trust(self, *, listed: dict[str, str] | None = None, confirm: bool = True) -> Any:
        """Trust (or Review): the tools the card shows, each with the digest it shows."""
        if listed is None:
            listed = (await self.card())["readOnlyTrust"]["listed"]
        body: dict[str, Any] = {"tools": listed}
        if confirm:
            body["confirm"] = True
        return await self.client.post(TRUST_URL, json=body)

    async def stop_trusting(self) -> Any:
        return await self.client.delete(TRUST_URL)

    async def reconnect(self) -> dict[str, Any]:
        """The card's Reconnect: the server looked at again, and its card as it then reads."""
        resp = await self.client.post(f"/api/mcp/probe/{NAME}")
        assert resp.status == 200, await resp.text()
        return await resp.json()


@asynccontextmanager
async def _tools_page(tmp_path) -> AsyncIterator[_Page]:
    from personalclaw.dashboard import handlers

    app = _api_app(_make_state(tmp_path))
    app.router.add_get("/api/mcp", handlers.api_mcp_servers)
    app.router.add_post(TRUST_URL.replace(NAME, "{name}"), handlers.api_mcp_server_read_only_trust)
    app.router.add_delete(
        TRUST_URL.replace(NAME, "{name}"), handlers.api_mcp_server_read_only_trust
    )
    app.router.add_post("/api/mcp/probe/{name}", handlers.api_mcp_probe_one)
    async with TestClient(TestServer(app)) as client:
        yield _Page(client)


async def _trusted(page: _Page) -> dict[str, Any]:
    resp = await page.trust()
    assert resp.status == 200, await resp.text()
    return await resp.json()


# ── what the trust covers ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_the_server_adds_after_the_trust_asks_until_she_reviews_it(wiki, tmp_path):
    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)
    wiki.start([SEARCH, EDIT, EXPORT])

    asks = await _asks(wiki)
    assert asks["mcp/wiki/export_all"] is True, "a tool she never saw ran without asking"
    assert asks["mcp/wiki/search"] is False


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param(SEARCH_REWORDED, id="description"),
        pytest.param(
            _tool(
                "search",
                SEARCH.description,
                schema={**_INPUTS, "properties": {**_INPUTS["properties"], "space": {}}},
            ),
            id="input schema",
        ),
        pytest.param(
            _tool(
                "search",
                SEARCH.description,
                labels={"readOnlyHint": True, "openWorldHint": True},
            ),
            id="labels",
        ),
        pytest.param(_tool("edit_page", EDIT.description, reads=True), id="relabelled read-only"),
    ],
)
@pytest.mark.asyncio
async def test_a_tool_whose_definition_changed_asks_until_she_reviews_it(wiki, tmp_path, changed):
    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)
    wiki.start([changed if t.name == changed.name else t for t in (SEARCH, EDIT)])

    assert (await _asks(wiki))[f"mcp/wiki/{changed.name}"] is True


@pytest.mark.asyncio
async def test_a_tool_still_as_she_saw_it_runs_unasked(wiki, tmp_path):
    """The control: the trust still does what she gave it for. Started again, and listed with its
    schema's keys in another order (the digest is of the canonical form), `search` is the tool she
    saw; `edit_page`, which does not say it only reads, asks as it always did."""
    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)
    reordered = _tool("search", SEARCH.description, schema=dict(reversed(list(_INPUTS.items()))))
    assert list(reordered.input_schema) != list(SEARCH.input_schema)
    wiki.start([reordered, EDIT])

    assert await _asks(wiki) == {"mcp/wiki/search": False, "mcp/wiki/edit_page": True}


@pytest.mark.asyncio
async def test_her_trust_is_asked_for_first_and_names_what_will_run_unasked(wiki, tmp_path):
    wiki.start()
    async with _tools_page(tmp_path) as page:
        resp = await page.trust(confirm=False)
        assert resp.status == 400, await resp.text()
        error = (await resp.json())["error"]
        assert error["code"] == "confirmation_required"
        said = error["detail"]["consent"]
        assert "search" in said and "edit_page" not in said, said
        assert "asks you until you review it" in said, said
        assert (await page.card())["readOnlyTrust"]["trusted"] is False

    assert (await _asks(wiki))["mcp/wiki/search"] is True


# ── the Tools page shows the change, and the review seals the tools again ─────────────────────


@pytest.mark.asyncio
async def test_the_card_shows_what_changed_and_the_review_seals_it(wiki, tmp_path):
    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)
        wiki.start([SEARCH_REWORDED, EXPORT])

        trust = (await page.card())["readOnlyTrust"]
        assert trust["trusted"] is True
        assert trust["added"] == ["export_all"]
        assert trust["changed"] == [{"name": "search", "parts": ["description"]}]
        assert trust["removed"] == ["edit_page"]

        reviewed = await _trusted(page)
        assert reviewed["sealed"] == ["export_all", "search"]
        assert reviewed["changedSince"] == []
        after = (await page.card())["readOnlyTrust"]
        assert (after["added"], after["changed"], after["removed"]) == ([], [], [])

    assert await _asks(wiki) == {"mcp/wiki/search": False, "mcp/wiki/export_all": False}


@pytest_asyncio.fixture()
async def live(monkeypatch) -> AsyncIterator[dict[str, Any]]:
    """The connections agents' reads made, as the registry holds them: to wiki, a shared one and one
    for a chat, each of which listed its tools when it started, and one for another chat whose start
    is still under way; to notes, one that listed its tools. A task stands in for each one's actor,
    so a connection closed is its task ended."""
    from personalclaw import mcp_client

    registry = mcp_client.McpClientRegistry()
    loop = asyncio.get_running_loop()
    conns: dict[str, Any] = {}
    for label, name, scope, listed in (
        ("wiki, shared", NAME, "", True),
        ("wiki, for a chat", NAME, "chat-a", True),
        ("wiki, still starting", NAME, "chat-b", False),
        ("notes", "notes", "", True),
    ):
        spec = {"command": f"/nonexistent/pc-fixture-{name}-mcp"}
        conn = mcp_client.McpServerConn(name, spec, scope=scope)
        conn._task = loop.create_task(asyncio.Event().wait())
        if listed:
            conn._ready.set()
        registry._specs[name] = spec
        registry._conns[(name, scope, mcp_client._spec_hash(spec))] = conn
        conns[label] = conn
    monkeypatch.setattr(mcp_client, "_registry", registry)
    yield conns
    for conn in conns.values():
        conn._task.cancel()
    await asyncio.gather(*(conn._task for conn in conns.values()), return_exceptions=True)


async def _closed(conns: dict[str, Any]) -> list[str]:
    """The connections closed, once every close asked for has run."""
    await asyncio.sleep(0.05)
    return sorted(label for label, conn in conns.items() if conn._task.done())


@pytest.mark.asyncio
async def test_a_review_starts_the_servers_connections_again(wiki, live, tmp_path):
    """A connection an agent holds listed its tools when it started, maybe before the server
    changed them. After her yes each of the server's that did is closed, so its next use lists the
    tools as she reviewed them, rather than asking about the older listing it holds. A start still
    under way is left to (it lists them as they are once it has started), and no other server's
    connections are touched."""
    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)

    assert await _closed(live) == ["wiki, for a chat", "wiki, shared"]


@pytest.mark.asyncio
async def test_reconnect_starts_the_servers_connections_again(wiki, live, tmp_path, monkeypatch):
    """Reconnect looks at the server again, and its card then shows the tools as the server lists
    them now. What agents are offered follows it: each of the server's connections that listed its
    tools when it started is closed, so its next use lists them as the card shows them, instead of
    the card saying a tool changed while agents go on being offered the one from before. A start
    still under way is left to, and no other server's connections are touched."""
    from personalclaw import mcp_discovery

    async def look_again(name: str) -> Any:
        wiki.start([SEARCH_REWORDED, EDIT, EXPORT])
        return next(s for s in mcp_discovery.list_servers() if s.name == name)

    monkeypatch.setattr(mcp_discovery, "probe_one", look_again)
    wiki.start()
    async with _tools_page(tmp_path) as page:
        card = await page.reconnect()

    assert sorted(card["readOnlyTrust"]["listed"]) == ["edit_page", "export_all", "search"]
    assert await _closed(live) == ["wiki, for a chat", "wiki, shared"]


@pytest.mark.asyncio
async def test_a_tool_that_changed_after_the_page_read_it_is_not_sealed(wiki, tmp_path):
    """Her yes is to the definitions the page showed her: one the server changed between the
    page's read and her click is left out, and still asks."""
    wiki.start()
    async with _tools_page(tmp_path) as page:
        shown = (await page.card())["readOnlyTrust"]["listed"]
        wiki.start([SEARCH_REWORDED, EDIT])
        resp = await page.trust(listed=shown)
        assert resp.status == 200, await resp.text()
        body = await resp.json()

    assert body["sealed"] == ["edit_page"]
    assert body["changedSince"] == ["search"]
    assert (await _asks(wiki))["mcp/wiki/search"] is True


@pytest.mark.asyncio
async def test_stop_trusting_makes_each_of_its_tools_ask_again(wiki, tmp_path):
    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)
        assert (await _asks(wiki))["mcp/wiki/search"] is False
        resp = await page.stop_trusting()
        assert resp.status == 200, await resp.text()
        assert (await page.card())["readOnlyTrust"] == {"trusted": False, "listed": _listed(wiki)}

    assert (await _asks(wiki))["mcp/wiki/search"] is True


def _listed(server: _Server) -> dict[str, str]:
    from personalclaw.mcp_read_only_trust import digest

    return {t.name: digest(t) for t in sorted(server.tools, key=lambda t: t.name)}


@pytest.mark.asyncio
async def test_a_server_with_no_listing_has_nothing_to_trust(wiki, tmp_path):
    """Never started as it is defined now: there is no tool PersonalClaw could have shown her."""
    async with _tools_page(tmp_path) as page:
        resp = await page.client.post(
            TRUST_URL, json={"tools": {"search": "0" * 64}, "confirm": True}
        )
        assert resp.status == 409, await resp.text()
        assert (await resp.json())["error"]["code"] == "mcp_tools_not_listed"


@pytest.mark.asyncio
async def test_a_trust_that_does_not_name_digests_is_refused(wiki, tmp_path):
    wiki.start()
    async with _tools_page(tmp_path) as page:
        for tools in (["search"], {"search": "yes"}, {"search": None}):
            resp = await page.client.post(TRUST_URL, json={"tools": tools, "confirm": True})
            assert resp.status == 400, (tools, await resp.text())
            assert (await resp.json())["error"]["code"] == "invalid_request"
        assert (await page.card())["readOnlyTrust"]["trusted"] is False


# ── a chat that is open ─────────────────────────────────────────────────────────────────────────


class _CallsSearch:
    """A model that calls the wiki's `search` once per turn, then answers."""

    supports_tools = True
    _model = "scripted"

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        if messages and messages[-1].get("role") == "user":
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{len(messages)}",
                title="mcp/wiki/search",
                tool_input='{"q": "retries"}',
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


async def _turn_asked(runtime: NativeAgentRuntime) -> bool:
    """One turn: whether the call to `search` asked before it ran (and was declined)."""
    asked = False
    async for ev in runtime.stream("what does the wiki say about retries?"):
        if ev.kind == EVENT_PERMISSION_REQUEST:
            asked = True
            await runtime.reject_tool(ev.request_id)
    return asked


@pytest.mark.asyncio
async def test_an_open_chat_judges_its_tools_again_at_its_next_turn(wiki, tmp_path):
    """A session keeps the verdicts its catalog was built with, so a trust given, or a tool the
    server changed on a later start, reaches the chats already open at their next turn."""
    wiki.start()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_CallsSearch(),
        tool_providers=[_provider(wiki)],
        cwd=tmp_path,
    )
    await runtime.start()
    assert await _turn_asked(runtime) is True, "an untrusted server's read ran unasked"

    async with _tools_page(tmp_path) as page:
        await _trusted(page)
    assert await _turn_asked(runtime) is False
    assert wiki.calls == ["search"]

    wiki.start([SEARCH_REWORDED, EDIT])
    assert await _turn_asked(runtime) is True, "the open chat kept a verdict for a tool now changed"


# ── trust from before the record, and a server that is removed ─────────────────────────────────


@pytest.mark.asyncio
async def test_trust_given_before_the_record_existed_is_not_carried_over(wiki, tmp_path):
    """A name in the old list says nothing of which tools she saw, so it covers none: the server's
    tools ask and its card reads untrusted. The retired key is read by nothing."""
    import json

    from personalclaw.config.loader import AppConfig, config_dir

    path = config_dir() / "config.json"
    path.write_text(json.dumps({"security": {"mcp_read_only_servers": [NAME]}}), encoding="utf-8")
    config = AppConfig.load()
    assert "mcp_read_only_servers" not in config.to_dict()["security"]
    assert not hasattr(config.security, "mcp_read_only_servers")

    wiki.start()
    async with _tools_page(tmp_path) as page:
        assert (await page.card())["readOnlyTrust"]["trusted"] is False
    assert (await _asks(wiki))["mcp/wiki/search"] is True


@pytest.mark.asyncio
async def test_her_trust_goes_with_a_server_she_removes(wiki, tmp_path):
    from mcp_owner_allowed import allow_configured

    from personalclaw.config.loader import config_dir
    from personalclaw.config.secret_refs import remove_mcp_servers, write_mcp_document

    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)
        remove_mcp_servers([NAME])
        write_mcp_document(config_dir() / "mcp.json", {"mcpServers": {NAME: dict(SPEC)}})
        allow_configured(NAME)
        wiki.start()
        assert (await page.card())["readOnlyTrust"]["trusted"] is False

    assert (await _asks(wiki))["mcp/wiki/search"] is True


def test_personalclaws_own_server_has_no_trust_to_give_or_take():
    """Its tools declare what they do themselves (`mcp_core`), so its card offers no trust."""
    from personalclaw.dashboard.handlers.mcp_trust import read_only_trust_of
    from personalclaw.mcp_discovery import McpServerInfo

    assert read_only_trust_of(McpServerInfo(name="personalclaw-core", source="agent")) is None
    assert read_only_trust_of(McpServerInfo(name=NAME, source="mcp.json")) == {
        "trusted": False,
        "listed": None,
    }


# ── a description that changed on a server she does not trust ──────────────────────────────────


@pytest.fixture()
def told(tmp_path, monkeypatch):
    """The notices the gateway raises, through its own relay (`dashboard.lifecycle_hooks`) into a
    real dashboard state: what it kept, and what it pushed to the pages as a toast."""
    import asyncio
    from types import SimpleNamespace

    from aiohttp import web

    from personalclaw.dashboard.lifecycle_hooks import register_lifecycle_hooks

    state = _make_state(tmp_path / "state")
    toasts: list[dict] = []
    monkeypatch.setattr(state, "_broadcast", toasts.append)
    app = web.Application()
    app["state"] = state
    register_lifecycle_hooks(app)
    start = next(h for h in app.on_startup if h.__name__ == "_mcp_descriptions_relay_startup")
    stop = next(h for h in app.on_cleanup if h.__name__ == "_mcp_descriptions_relay_shutdown")
    asyncio.run(start(app))

    def notes() -> list[dict]:
        return [n for n in state._notification_log if n.get("kind") == "mcp_description_changed"]

    yield SimpleNamespace(notes=notes, toasts=toasts)
    asyncio.run(stop(app))


def test_a_changed_description_on_a_server_she_does_not_trust_is_told_quietly_once(wiki, told):
    wiki.start()
    wiki.start()
    assert told.notes() == [], "nothing changed: the first listing and the same one again"

    wiki.start([SEARCH_REWORDED, EDIT])
    wiki.start([SEARCH_REWORDED, EDIT])

    (note,) = told.notes()
    assert "wiki" in note["title"] and "search" in note["title"], note
    assert "Tools page" in note["body"], note
    assert note["statusUrl"] == "#/tools?q=wiki"
    assert (
        note["mode"] == "badge" and note["badge_only"] is True
    ), "a quiet notice interrupts nobody"
    assert told.toasts == []


def test_what_was_seen_of_a_description_outlives_the_process(wiki, told, monkeypatch):
    """The descriptions as last seen are kept with the trust, so a change that lands while the
    gateway is down (a server updated before the next start) is told at that start."""
    from personalclaw import mcp_read_only_trust

    wiki.start()
    # A new process: nothing of the first start is held in memory, only what is on disk.
    monkeypatch.setattr(mcp_read_only_trust, "_last_listing", {})
    monkeypatch.setattr(mcp_read_only_trust, "_reads", {})
    wiki.start([SEARCH_REWORDED, EDIT])

    assert len(told.notes()) == 1


@pytest.mark.asyncio
async def test_a_trusted_servers_change_is_on_its_card_and_raises_no_notice(wiki, told, tmp_path):
    """Trusted, the changed tool asks until she reviews it, and the card says what changed."""
    wiki.start()
    async with _tools_page(tmp_path) as page:
        await _trusted(page)
        wiki.start([SEARCH_REWORDED, EDIT])
        assert (await page.card())["readOnlyTrust"]["changed"] == [
            {"name": "search", "parts": ["description"]}
        ]

    assert told.notes() == []


def test_personalclaws_own_server_rewording_its_tools_tells_her_nothing(monkeypatch):
    """Its tools are PersonalClaw's own and say what they do themselves: an update that rewords
    them is not a server changing what it told her, so its starts are not compared."""
    import json

    from personalclaw import mcp_read_only_trust
    from personalclaw.agent import AGENT_FILENAME, agents_dir
    from personalclaw.mcp_discovery import _probed_as, forget_probe, list_servers, note_start

    agents_dir().mkdir(parents=True, exist_ok=True)
    (agents_dir() / AGENT_FILENAME).write_text(
        json.dumps({"mcpServers": {"personalclaw-core": {"command": "/nonexistent/pc-fixture"}}}),
        encoding="utf-8",
    )
    own = next(s for s in list_servers(include_disabled=True) if s.name == "personalclaw-core")
    told: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(mcp_read_only_trust, "_listeners", [lambda s, t: told.append((s, t))])
    before = mcp_read_only_trust.stamp()
    try:
        note_start(own.name, _probed_as(own), tools=[_tool("memory_recall", "Recall a memory.")])
        note_start(own.name, _probed_as(own), tools=[_tool("memory_recall", "Recall memories.")])
    finally:
        forget_probe(own.name)

    assert told == []
    assert mcp_read_only_trust.stamp() == before
