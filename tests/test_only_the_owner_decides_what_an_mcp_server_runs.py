"""Only the owner decides what an MCP server runs, and nothing runs before they say yes.

🔴 THE DEFECT (measured on ``origin/main`` 5b69326ce). Adding an MCP server started it at once.
The Tools page's next read (``GET /api/mcp``) probed every server it had not seen, which spawns a
stdio server's command, and the native client started whatever ``mcp.json`` named for the next
agent call. Nothing asked who wrote the definition: a fixture's ``npx search-mcp`` was a real npm
package, and the probe downloaded and ran it. Every way in did the same. The Tools page's Add and
the MCP Tool Servers card saved and started a server in one step, with nothing shown first, and
Import from Claude Code or Codex, bringing a setup over, a pack's connector, an app's manifest and a
hand edit of ``mcp.json`` all ran what they wrote. An agent's shell could make that hand edit:
``mcp.json`` was not an owner-only path.

The contract now (`personalclaw.mcp_grants`): a server runs only once the owner allowed what it
runs, sealed to its definition (how it is reached, its command, arguments and folder, the names of
the variables it sets, a remote server's address and header names). The owner's own Add and Edit
say exactly what will run and ask before anything is saved. Every other way in writes a server
that waits for Allow on the Tools page, and Allow asks the same question first.

Every server here is a script this test wrote, which records each time it is started, or a command
that cannot resolve.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import json
import os
import sys
import textwrap
import types
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import mcp_client, mcp_discovery
from personalclaw.config import loader as config_loader
from personalclaw.config.secret_refs import write_mcp_document
from personalclaw.dashboard.handlers import mcp as mcp_handlers

UNRESOLVABLE = "/nonexistent/pc-fixture-mcp"
TOKEN = "fixture-owner-only-token-0123456789"


class _Server:
    """A stdio MCP server this test wrote: it records each launch, then serves one tool."""

    def __init__(self, root: Path, name: str = "notes") -> None:
        self.name = name
        self.log = root / f"{name}.launches"
        self.script = root / f"{name}_server.py"
        self.script.write_text(
            f"#!{sys.executable}\n" + textwrap.dedent(f"""
                with open({str(self.log)!r}, "a", encoding="utf-8") as fh:
                    fh.write("launched\\n")

                from mcp.server.fastmcp import FastMCP

                mcp = FastMCP("fixture")


                @mcp.tool(description="say hello")
                def hello(name: str) -> str:
                    return "hello " + name


                mcp.run(transport="stdio")
                """),
            encoding="utf-8",
        )
        self.script.chmod(0o755)

    @property
    def launches(self) -> int:
        return len(self.log.read_text(encoding="utf-8").splitlines()) if self.log.exists() else 0

    def spec(self, **extra: Any) -> dict[str, Any]:
        return {"command": str(self.script), "args": ["--fixture"], **extra}


def _grants() -> types.ModuleType:
    """`personalclaw.mcp_grants`, imported where it is used: on ``main`` it does not exist, and
    each test here fails there on what it measures rather than on the import."""
    from personalclaw import mcp_grants

    return mcp_grants


def _home() -> Path:
    return config_loader.config_dir()


def _mcp_json() -> dict[str, Any]:
    path = _home() / "mcp.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})


def _hand_write(servers: dict[str, Any]) -> None:
    """``mcp.json`` written without any owner surface: a hand edit, a restore, an agent's file."""
    write_mcp_document(_home() / "mcp.json", {"mcpServers": servers})


def _as_configured(name: str) -> mcp_discovery.McpServerInfo:
    [server] = [s for s in mcp_discovery.list_servers(include_disabled=True) if s.name == name]
    return server


def _started_for_agents() -> dict[str, Any]:
    return mcp_client._personalclaw_mcp_specs()


_ROUTES = (
    ("GET", "/api/mcp", "api_mcp_servers"),
    ("GET", "/api/mcp/active", "api_mcp_active"),
    ("POST", "/api/mcp/probe", "api_mcp_probe"),
    ("GET", "/api/mcp/probe", "api_mcp_probe_cached"),
    ("POST", "/api/mcp/probe/{name}", "api_mcp_probe_one"),
    ("GET", "/api/mcp/servers/{name}", "api_mcp_server_detail"),
    ("PUT", "/api/mcp/servers/{name}", "api_mcp_server_detail"),
    ("DELETE", "/api/mcp/servers/{name}", "api_mcp_server_detail"),
    ("POST", "/api/mcp/servers/{name}/allow", "api_mcp_server_allow"),
    ("GET", "/api/mcp/importable", "api_mcp_importable"),
    ("POST", "/api/mcp/apply", "api_mcp_apply"),
)


@contextlib.asynccontextmanager
async def _tools_page(monkeypatch) -> AsyncIterator[TestClient]:
    """The routes the Tools page calls, over a home that starts with no server at all."""
    monkeypatch.setattr(mcp_client, "_registry", None)
    monkeypatch.setattr(mcp_discovery, "_probe_cache", {})
    monkeypatch.setattr(mcp_handlers, "_mcp_probe_cache", [])
    monkeypatch.setattr(mcp_handlers, "_mcp_probe_ts", 0.0)
    monkeypatch.setattr(mcp_handlers, "_mcp_probe_in_progress", False)
    # The agent config's rebuild is not what this is about, and it adds PersonalClaw's own server,
    # which the page's probe would then start.
    monkeypatch.setattr(mcp_handlers, "_rebuild_agent_config_logged", lambda: None)
    monkeypatch.setattr("personalclaw.agent.rebuild_agent_config", lambda **_: None)
    app = web.Application()
    app["state"] = types.SimpleNamespace(_background_tasks=set())
    for method, path, handler in _ROUTES:
        # Allow is new: on `main` its route is missing, and a test fails on what it measures.
        if hasattr(mcp_handlers, handler):
            app.router.add_route(method, path, getattr(mcp_handlers, handler))
    try:
        async with TestClient(TestServer(app)) as http:
            yield http
            await _settled(http)
    finally:
        registry = mcp_client._registry
        if registry is not None:
            await registry.shutdown_all()


async def _settled(http: TestClient) -> None:
    """Every probe a read started in the background, finished."""
    tasks = http.app["state"]._background_tasks
    while tasks:
        await asyncio.gather(*list(tasks), return_exceptions=True)


async def _row(http: TestClient, method: str, path: str, name: str) -> dict[str, Any]:
    resp = await http.request(method, path)
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    rows = body if isinstance(body, list) else [body]
    [row] = [r for r in rows if r.get("name") == name]
    return row


def _every_read(name: str) -> list[tuple[str, str]]:
    return [
        ("GET", "/api/mcp"),
        ("POST", f"/api/mcp/probe/{name}"),
        ("POST", "/api/mcp/probe"),
        ("GET", "/api/mcp/probe"),
    ]


async def _waits_on_every_read(
    http: TestClient, name: str, server: _Server | None = None
) -> dict[str, Any]:
    """None of the routes the Tools page reads started the server, and each says it waits."""
    before = server.launches if server is not None else 0
    rows = [
        (method, path, await _row(http, method, path, name)) for method, path in _every_read(name)
    ]
    await _settled(http)
    if server is not None:
        assert server.launches == before, "reading the Tools page started the server"
    for method, path, row in rows:
        assert row["status"] == "waiting", f"{method} {path} said {row['status']!r}"
        assert row["allowed"] is False and row["error"] == _grants().WAITING_REASON
        assert row["tools"] == [] and row["allowRevision"]
    assert name not in _started_for_agents(), "an agent's next call would start it"
    return rows[-1][2]


# ── 🔴 nothing starts a server the owner has not allowed ──


@pytest.mark.asyncio
async def test_a_server_written_into_mcp_json_is_started_by_no_read_and_no_agent(
    tmp_path, monkeypatch
):
    """🔴 Red on main: the page's first read probed it, which ran the script."""
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        _hand_write(
            {
                "notes": server.spec(env={"API_TOKEN": TOKEN}),
                "unresolvable": {"command": UNRESOLVABLE, "args": []},
            }
        )
        await _waits_on_every_read(http, "notes", server)
        # A command that cannot resolve is not even looked for: it waits like any other.
        await _waits_on_every_read(http, "unresolvable")
        assert mcp_client.get_mcp_client_registry().get("notes") is None

    assert server.launches == 0


@pytest.mark.asyncio
async def test_the_agent_page_shows_a_waiting_server_as_not_running(tmp_path, monkeypatch):
    """🔴 Red on main: the agent page's list marked every server switched on as running."""
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        _hand_write({"notes": server.spec()})
        active = {r["name"]: r["enabled"] for r in await (await http.get("/api/mcp/active")).json()}
        assert active["notes"] is False, "a server that waits read as running"
        _grants().give(_as_configured("notes"))
        active = {r["name"]: r["enabled"] for r in await (await http.get("/api/mcp/active")).json()}
        assert active["notes"] is True

    assert server.launches == 0


@pytest.mark.asyncio
async def test_allow_says_what_runs_and_starts_it_only_on_a_yes(tmp_path, monkeypatch):
    """🔴 Red on main: there was no question to answer, and no Allow to give."""
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        _hand_write({"notes": server.spec(env={"API_TOKEN": TOKEN})})
        row = await _waits_on_every_read(http, "notes", server)
        allow = "/api/mcp/servers/notes/allow"

        asked = await http.post(allow, json={"revision": row["allowRevision"]})
        assert asked.status == 400, await asked.text()
        error = (await asked.json())["error"]
        assert error["code"] == "confirmation_required"
        consent = error["detail"]["consent"]
        assert f"{server.script} --fixture" in consent and "API_TOKEN" in consent
        assert TOKEN not in json.dumps(error), "the question showed a secret's value"
        assert error["detail"]["title"] == "Allow this MCP server to run?"

        stale = await http.post(allow, json={"revision": "not-what-the-page-read", "confirm": True})
        assert stale.status == 409 and (await stale.json())["error"]["code"] == "stale_write"
        await _settled(http)
        assert server.launches == 0, "a refused Allow started it"

        yes = await http.post(allow, json={"revision": row["allowRevision"], "confirm": True})
        assert yes.status == 200, await yes.text()
        await _settled(http)
        assert server.launches == 1, "the Allow did not start it"
        listed = await _row(http, "GET", "/api/mcp", "notes")
        assert listed["allowed"] is True and [t["name"] for t in listed["tools"]] == ["hello"]
        assert "notes" in _started_for_agents()


@pytest.mark.asyncio
async def test_a_change_to_what_it_runs_waits_again_and_a_new_secret_does_not(
    tmp_path, monkeypatch
):
    """🔴 Red on main, where no yes was ever asked for. A yes is to one definition: an argument
    added afterwards is a new question, and a secret replaced under the same name is not."""
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        _hand_write({"notes": server.spec(env={"API_TOKEN": TOKEN})})
        _grants().give(_as_configured("notes"))
        assert (await _row(http, "GET", "/api/mcp", "notes"))["allowed"] is True

        _hand_write({"notes": server.spec(env={"API_TOKEN": "a-rotated-value"})})
        assert (await _row(http, "GET", "/api/mcp", "notes"))["allowed"] is True

        _hand_write({"notes": {**server.spec(env={"API_TOKEN": TOKEN}), "args": ["--other"]}})
        await _settled(http)
        await _waits_on_every_read(http, "notes", server)


# ── 🔴 the owner's own Add and Edit ask before anything is saved ──


@pytest.mark.asyncio
async def test_the_tools_page_add_asks_first_and_saves_nothing_until_the_owner_agrees(
    tmp_path, monkeypatch
):
    """🔴 Red on main: the PUT saved the server, and the page's next read started it."""
    from personalclaw.config.credentials import credential_names

    server = _Server(tmp_path)
    body = {**server.spec(), "env": {"API_TOKEN": TOKEN}}
    async with _tools_page(monkeypatch) as http:
        stored_before = set(credential_names())
        asked = await http.put("/api/mcp/servers/notes", json=body)
        assert asked.status == 400, await asked.text()
        error = (await asked.json())["error"]
        assert error["code"] == "confirmation_required"
        consent = error["detail"]["consent"]
        assert consent.startswith("Saving “notes” lets PersonalClaw run ")
        assert f"{server.script} --fixture" in consent and "API_TOKEN" in consent
        assert TOKEN not in json.dumps(error)
        assert "notes" not in _mcp_json(), "the server was saved before the owner agreed"
        assert set(credential_names()) == stored_before, "a value was stored before the yes"
        await _settled(http)
        assert server.launches == 0

        saved = await http.put("/api/mcp/servers/notes", json={**body, "confirm": True})
        assert saved.status == 200, await saved.text()
        row = await _row(http, "POST", "/api/mcp/probe/notes", "notes")
        assert row["allowed"] is True and [t["name"] for t in row["tools"]] == ["hello"]
        assert server.launches == 1


@pytest.mark.asyncio
async def test_an_edit_that_changes_what_it_runs_asks_and_one_that_replaces_a_secret_does_not(
    tmp_path, monkeypatch
):
    """🔴 Red on main: an edit to the command saved and ran with nothing asked."""
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        body = {**server.spec(), "env": {"API_TOKEN": TOKEN}, "confirm": True}
        assert (await http.put("/api/mcp/servers/notes", json=body)).status == 200
        read = await (await http.get("/api/mcp/servers/notes")).json()

        edit = {"command": str(server.script), "args": ["--fixture", "--verbose"]}
        asked = await http.put(
            "/api/mcp/servers/notes", json=edit, headers={"If-Match": read["revision"]}
        )
        assert (
            asked.status == 400
            and "--verbose" in (await asked.json())["error"]["detail"]["consent"]
        )
        assert _mcp_json()["notes"]["args"] == ["--fixture"], "the edit was saved unasked"

        rotated = {**server.spec(), "env": {"API_TOKEN": "a-new-token-value"}}
        resp = await http.put(
            "/api/mcp/servers/notes", json=rotated, headers={"If-Match": read["revision"]}
        )
        assert resp.status == 200, await resp.text()
        assert _grants().allowed(_as_configured("notes")), "a new secret took the yes away"


@pytest.mark.asyncio
async def test_a_command_that_cannot_resolve_is_named_in_the_question(monkeypatch):
    async with _tools_page(monkeypatch) as http:
        asked = await http.put("/api/mcp/servers/ghost", json={"command": UNRESOLVABLE})
        assert asked.status == 400, await asked.text()
        consent = (await asked.json())["error"]["detail"]["consent"]
        assert f"run {UNRESOLVABLE} as you" in consent
        assert "ghost" not in _mcp_json()


def _provider_card(monkeypatch) -> None:
    """The MCP Tool Servers card's provider, as the instance routes find it."""
    ext = types.SimpleNamespace(
        provider_config=types.SimpleNamespace(multiInstance=True, settingsSchema={}, type="tool")
    )
    registry = types.SimpleNamespace(get=lambda name: ext if name == "mcp-tools" else None)
    monkeypatch.setattr("personalclaw.providers.registry.get_provider_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.instance_routes._rebuild_agent_config_safe", lambda: None
    )


@pytest.mark.asyncio
async def test_the_provider_card_asks_before_it_creates_or_changes_what_a_server_runs(
    tmp_path, monkeypatch
):
    """🔴 Red on main: the card wrote the server with nothing shown, and it ran."""
    from personalclaw.providers import instance_routes

    _provider_card(monkeypatch)
    server = _Server(tmp_path)
    app = web.Application()
    app.router.add_post("/api/providers/{name}/instances", instance_routes.handle_create_instance)
    app.router.add_put(
        "/api/providers/{name}/instances/{id}", instance_routes.handle_update_instance
    )
    config = {"transport": "stdio", "command": str(server.script), "args": "--fixture"}
    async with TestClient(TestServer(app)) as http:
        path = "/api/providers/mcp-tools/instances"
        asked = await http.post(path, json={"display_name": "card", "config": config})
        assert asked.status == 400, await asked.text()
        assert f"{server.script} --fixture" in (await asked.json())["error"]["detail"]["consent"]
        assert "card" not in _mcp_json()

        made = await http.post(
            path, json={"display_name": "card", "config": config, "confirm": True}
        )
        assert made.status == 201, await made.text()
        assert _grants().allowed(_as_configured("card"))
        revision = (await made.json())["instance"]["revision"]

        changed = {**config, "args": "--fixture --other"}
        asked = await http.put(
            f"{path}/card", json={"config": changed}, headers={"If-Match": revision}
        )
        assert asked.status == 400, await asked.text()
        assert _mcp_json()["card"]["args"] == ["--fixture"]
        # Switching it off and on is not a question: what it runs is unchanged.
        toggled = await http.put(f"{path}/card", json={"enabled": False})
        assert toggled.status == 200, await toggled.text()

    assert server.launches == 0


# ── 🔴 every other way in writes a server that waits ──


def _claude_code(root: Path, servers: dict[str, Any]) -> Path:
    path = root / "claude-home" / ".claude.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def _codex(root: Path, server: _Server) -> Path:
    home = root / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[mcp_servers.{server.name}]\ncommand = "{server.script}"\nargs = ["--fixture"]\n',
        encoding="utf-8",
    )
    return home


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["Claude Code", "Codex"])
async def test_import_from_another_tool_waits_for_the_owner(tool, tmp_path, monkeypatch):
    """🔴 Red on main: the import wrote the server, and the page's next read started it."""
    server = _Server(tmp_path)
    where = (
        _claude_code(tmp_path, {server.name: {"type": "stdio", **server.spec()}})
        if tool == "Claude Code"
        else _codex(tmp_path, server)
    )
    monkeypatch.setattr(mcp_discovery, "_import_sources", lambda: ((where, tool),))
    async with _tools_page(monkeypatch) as http:
        listed = (await (await http.get("/api/mcp/importable")).json())["servers"]
        [row] = [r for r in listed if r["name"] == server.name]
        change = {"name": server.name, "personalclaw": True, "from": row["id"]}
        applied = await (await http.post("/api/mcp/apply", json={"changes": [change]})).json()
        assert not any(r.get("error") for r in applied["results"]), applied
        assert server.name in _mcp_json()
        await _waits_on_every_read(http, server.name, server)

    assert server.launches == 0


@pytest.mark.asyncio
async def test_a_server_brought_over_at_onboarding_waits(tmp_path, monkeypatch):
    """🔴 Red on main: the server the onboarding import wrote was started by the next read."""
    from personalclaw.onboarding_import.model import ImportCategory, ImportItem
    from personalclaw.onboarding_import.writers import write_item

    server = _Server(tmp_path)
    item = ImportItem(
        source="claude_code",
        category=ImportCategory.MCP_SERVERS,
        key=server.name,
        name=server.name,
        payload=server.spec(),
    )
    async with _tools_page(monkeypatch) as http:
        assert write_item(item).outcome.value == "imported"
        await _waits_on_every_read(http, server.name, server)

    assert server.launches == 0


@pytest.mark.asyncio
async def test_a_packs_connector_waits(tmp_path, monkeypatch):
    """🔴 Red on main. A pack is someone else's: its connector names the command to run."""
    from personalclaw.packs.connectors import resolve_connector

    server = _Server(tmp_path, name="pack-notes")
    declaration = {"name": server.name, "category": "notes", **server.spec()}
    async with _tools_page(monkeypatch) as http:
        resolve_connector(declaration, mode="configure")
        assert server.name in _mcp_json()
        await _waits_on_every_read(http, server.name, server)

    assert server.launches == 0


@pytest.mark.asyncio
async def test_an_apps_server_waits_and_keeps_its_yes_until_the_app_is_removed(
    tmp_path, monkeypatch
):
    """🔴 Red on main: an app's manifest server was started by the next read. Switched off or
    updated, the app's server keeps the yes to what it runs; removed, it does not."""
    from personalclaw.apps import mcp_bridge
    from personalclaw.apps.manager import app_dir

    server = _Server(tmp_path)
    app_dir("fixture-app").mkdir(parents=True)  # where an installed app's server starts
    manifest = types.SimpleNamespace(name="fixture-app", mcpServers={"notes": server.spec()})
    key = "fixture-app:notes"
    async with _tools_page(monkeypatch) as http:
        assert mcp_bridge.register_app_mcp_servers(manifest) == [key]
        await _waits_on_every_read(http, key, server)

        grants = _grants()
        grants.give(_as_configured(key))
        mcp_bridge.deregister_app_mcp_servers("fixture-app", forget=False)
        mcp_bridge.register_app_mcp_servers(manifest)
        assert grants.allowed(_as_configured(key)), "an update took the yes away"

        mcp_bridge.deregister_app_mcp_servers("fixture-app", forget=True)
        mcp_bridge.register_app_mcp_servers(manifest)
        assert not grants.allowed(_as_configured(key)), "a removed app's yes came back"


def test_copying_a_server_into_claude_codes_file_needs_the_owners_yes(tmp_path):
    """🔴 Red on main. Claude Code starts what its file names, so handing it a server nobody
    allowed would run it before the owner's yes."""
    server = _Server(tmp_path)
    claude = tmp_path / "claude.json"
    claude.write_text('{"mcpServers": {}}', encoding="utf-8")
    before = claude.read_bytes()

    outcome = mcp_handlers._set_scope_entry(claude, "notes", enabled=True, spec=server.spec())

    assert outcome == "waiting" and claude.read_bytes() == before
    grants = _grants()
    grants.give(grants.server_of("notes", server.spec()))
    assert mcp_handlers._set_scope_entry(claude, "notes", enabled=True, spec=server.spec()) == (
        "added"
    )


@pytest.mark.asyncio
async def test_personalclaws_own_name_in_mcp_json_is_never_started(tmp_path, monkeypatch):
    """🔴 Red on main: ``mcp.json`` naming ``personalclaw-core`` was started for the next agent
    call. PersonalClaw's own server comes from its own code, never from that file."""
    server = _Server(tmp_path, name="impostor")
    async with _tools_page(monkeypatch) as http:
        _hand_write({"personalclaw-core": server.spec()})
        assert "personalclaw-core" not in _started_for_agents()
        grants = _grants()
        assert not grants.allowed(grants.server_of("personalclaw-core", server.spec()))
        await http.get("/api/mcp")
        await http.post("/api/mcp/probe")

    assert server.launches == 0


@pytest.mark.asyncio
async def test_a_switched_off_server_is_listed_and_not_started(tmp_path, monkeypatch):
    """🔴 Red on main: switched off, the server left the list, and the Tools page lost the row
    with its switch."""
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        _hand_write({"notes": server.spec(disabled=True)})
        assert (await _row(http, "GET", "/api/mcp", "notes"))["enabled"] is False
        _grants().give(_as_configured("notes"))
        row = await _row(http, "POST", "/api/mcp/probe/notes", "notes")
        assert row["status"] == "disabled" and row["allowed"] is True
        assert mcp_client.get_mcp_client_registry().get("notes") is None

    assert server.launches == 0


@pytest.mark.asyncio
async def test_removing_a_server_takes_the_yes_with_it(tmp_path, monkeypatch):
    """🔴 Red on main, where there was no yes to take. Added again under the name, even with the
    same definition, a server is asked about again."""
    server = _Server(tmp_path)
    async with _tools_page(monkeypatch) as http:
        body = {**server.spec(), "confirm": True}
        assert (await http.put("/api/mcp/servers/notes", json=body)).status == 200
        assert (await http.delete("/api/mcp/servers/notes")).status == 200

        _hand_write({"notes": server.spec()})
        await _settled(http)
        await _waits_on_every_read(http, "notes", server)


# ── who may say yes, and who may write the file ──


def test_an_app_cannot_allow_a_server():
    """The floor, true on main too: every MCP route is the owner's, reads included."""
    from personalclaw.apps.permissions import owner_only_api_reason

    assert owner_only_api_reason("/api/mcp/servers/notes/allow")


def test_the_gateways_internal_secret_reaches_no_mcp_route():
    """🔴 Red on main: ``/api/mcp/servers`` took the loopback internal secret, which a process on
    the machine presents, as the owner. Nothing in PersonalClaw called it that way."""
    from personalclaw.dashboard import server

    named: list[str] = []
    for node in ast.walk(ast.parse(inspect.getsource(server))):
        if isinstance(node, ast.keyword) and node.arg in ("internal_paths", "mixed_internal_paths"):
            named += [
                c.value
                for c in ast.walk(node.value)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            ]
    assert "/api/tools/invoke" in named, "vacuity: the internal paths were not found"
    assert [p for p in named if p.startswith("/api/mcp")] == []


@pytest.fixture
def agent_home(tmp_path, monkeypatch):
    """A home inside a folder that stands for the owner's `~`, with a workspace in it."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    (pc / "workspace").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(config_loader, "config_dir", lambda: pc)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    return pc


def test_the_agents_shell_cannot_write_mcp_json(agent_home):
    """🔴 Red on main: the native ``bash`` tool wrote a server into ``mcp.json``."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(agent_home / "workspace")
    written = json.dumps({"mcpServers": {"x": {"command": UNRESOLVABLE}}})
    for command in (
        f"printf '%s' '{written}' > {agent_home}/mcp.json",
        f"echo '{written}' > ../mcp.json",
        "cd .. && cp /dev/null mcp.json",
    ):
        result = asyncio.run(tools.invoke("bash", {"command": command}))
        assert not result.success and "an agent may not change it" in result.error, command
    assert not (agent_home / "mcp.json").exists()


def test_the_sandbox_denies_a_write_to_mcp_json_at_every_level(agent_home):
    """🔴 Red on main: the OS fence around the agent's shell left ``mcp.json`` writable."""
    from personalclaw.sandbox import _build_launcher_script, _build_seatbelt_profile

    real = os.path.realpath(agent_home)
    for level in ("standard", "cc", "strict"):
        assert f'(deny file-write* (literal "{real}/mcp.json"))' in _build_seatbelt_profile(level)
    # The launcher fences the home's names, not one path per file (the fence holds a name that
    # does not exist yet): `mcp.json` is among them.
    launcher = _build_launcher_script("standard")
    assert f'OWNER_HOME = "{real}"' in launcher
    names = next(line for line in launcher.splitlines() if line.startswith("OWNER_ONLY_NAMES = "))
    assert "mcp.json" in json.loads(names.split("=", 1)[1])


@pytest.mark.parametrize(
    "title", ["Write {home}/mcp.json", "Running: cp evil.json {home}/mcp.json"]
)
def test_a_tool_call_that_writes_mcp_json_is_refused_before_any_approval(agent_home, title):
    """🔴 Red on main: an ACP agent's own write and shell tools reached an approval with nothing
    screening the path."""
    from personalclaw.hooks import HookManager

    result = HookManager().on_tool_call(title.format(home=agent_home))

    assert result.action == "deny" and "an agent may not change it" in result.reason


def test_a_restore_or_a_sync_brings_back_no_yes():
    """🔴 Red on main, where `grants/` was in no inventory class, so the Doctor listed it as
    unclaimed. It is machine-local: a yes is given where the owner was shown what runs, so what a
    restore, an export or a sync brings from elsewhere waits for a yes on this machine."""
    from personalclaw import snapshot
    from personalclaw.durability import inventory as inv

    assert "grants" in inv.secret_paths()
    assert "grants" not in snapshot._extra_restore_paths_for_test_paths()
    assert "grants" not in {e.path for e in inv.export_entries()}


def test_the_config_writers_carry_no_mcp_server():
    """The floor, true on main too: no config field defines a server, so the config PATCH is not
    a way in."""
    from personalclaw.config.editable import _EDITABLE_CONFIG

    defining = [k for k in _EDITABLE_CONFIG if "mcpservers" in k.lower().replace("_", "")]
    assert defining == []
