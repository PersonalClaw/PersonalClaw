"""Another agent tool's setup is read only when you ask, or once you turned it on in Settings.

Settings → Security → Outside PersonalClaw's home says PersonalClaw reads and writes only inside
its home until a place is turned on there. Measured before this change, with stand-in files for
two other agent tools in a scratch ``HOME``:

* ``GET /api/mcp/importable``, which the Tools page sends on every visit, opened
  ``~/.claude.json``, ``~/.claude/settings.json``, ``~/.codex/config.toml`` and the ``.mcp.json``
  of a project Claude Code lists;
* ``GET /api/onboarding/import``, which the setup step sends as it opens, opened those, read
  ``CLAUDE.md`` and listed both tools' folders.

Now each tool's setup is a place (``outside_home``), and every reader of it asks the one check,
``outside_home.readable``: a press names the places it looks in (``look_in``), and a request that
names none reads only the places turned on. These tests watch the reads themselves: every file
opened and every folder listed under the scratch ``HOME``, outside PersonalClaw's own home.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

#: A command that can never resolve: nothing here is ever started.
COMMAND = "/nonexistent/pc-fixture-mcp"
CLAUDE_SERVER = "weather-fixture"
PROJECT_SERVER = "notes-fixture"
CODEX_SERVER = "calendar-fixture"
CLAUDE = "setup:claude_code"
CODEX = "setup:codex"

# ── what was read ─────────────────────────────────────────────────────────────────────────────

_READS = frozenset({"open", "os.listdir", "os.scandir"})
#: ``(event, path under HOME)`` for each read while a test watches, else ``None``.
_SEEN: list[tuple[str, str]] | None = None
#: The watched ``HOME``, and PersonalClaw's own home inside it, as ``(exact, prefix)`` forms.
_WATCHED: tuple[tuple[str, str], ...] = ()
_OWN: tuple[tuple[str, str], ...] = ()


def _forms(path: Path) -> tuple[tuple[str, str], ...]:
    normal = os.path.normpath(os.path.abspath(path))
    return tuple((form, form + os.sep) for form in {normal, os.path.realpath(normal)})


def _under(text: str, forms: tuple[tuple[str, str], ...]) -> str | None:
    for exact, prefix in forms:
        if text == exact or text.startswith(prefix):
            return text[len(exact) :] or "/"
    return None


def _audit(event: str, args: tuple) -> None:
    seen = _SEEN
    if seen is None or event not in _READS or not args:
        return
    path = args[0]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return  # a file descriptor: its path was seen when it was opened
    try:
        text = os.path.normpath(os.path.abspath(os.fsdecode(os.fspath(path))))
    except (TypeError, ValueError):
        return
    if _under(text, _OWN) is not None:
        return
    inside = _under(text, _WATCHED)
    if inside is not None:
        seen.append((event, "~" + inside))


sys.addaudithook(_audit)


@contextmanager
def reads(machine: SimpleNamespace) -> Iterator[list[tuple[str, str]]]:
    """Every file opened and folder listed under ``HOME``, outside PersonalClaw's home, in any
    thread, while the block runs."""
    global _SEEN, _WATCHED, _OWN
    seen: list[tuple[str, str]] = []
    _WATCHED, _OWN = _forms(machine.home), _forms(machine.pclaw)
    _SEEN = seen
    try:
        yield seen
    finally:
        _SEEN = None


def _of(seen: list[tuple[str, str]], *prefixes: str) -> list[tuple[str, str]]:
    return [(event, path) for event, path in seen if path.startswith(prefixes)]


# ── the machine ───────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A scratch ``HOME`` holding what Claude Code and Codex keep there, and a project Claude Code
    lists. PersonalClaw's home is ``HOME/.personalclaw``, as it is for anyone who set no other."""
    from personalclaw.config.loader import config_dir

    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "PERSONALCLAW_HOME", "PERSONALCLAW_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")

    project = home / "src" / "garden"
    project.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("# Garden\n\n- Water on Sundays.\n", encoding="utf-8")
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {PROJECT_SERVER: {"command": COMMAND, "args": ["notes"]}}}),
        encoding="utf-8",
    )
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text("# House rules\n\n- Lint first.\n", "utf-8")
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {CLAUDE_SERVER: {"command": COMMAND, "args": ["weather"]}},
                "projects": {str(project): {"enabledMcpjsonServers": [PROJECT_SERVER]}},
            }
        ),
        encoding="utf-8",
    )
    (home / ".codex").mkdir()
    (home / ".codex" / "AGENTS.md").write_text("# Codex rules\n\n- Small diffs.\n", "utf-8")
    (home / ".codex" / "config.toml").write_text(
        f'[mcp_servers.{CODEX_SERVER}]\ncommand = "{COMMAND}"\nargs = ["calendar"]\n',
        encoding="utf-8",
    )
    pclaw = config_dir()
    assert pclaw == home / ".personalclaw", f"PersonalClaw's home is {pclaw}, not in HOME"
    return SimpleNamespace(home=home, pclaw=pclaw, project=project)


def _allow(*place_ids: str) -> None:
    """What the Settings switch writes: the places turned on, in config.json."""
    from personalclaw.config.loader import config_dir

    path = config_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["outside_home"] = list(place_ids)
    path.write_text(json.dumps(data), encoding="utf-8")


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import personalclaw.agent
    from personalclaw.dashboard.handlers import mcp as h
    from personalclaw.dashboard.handlers.core import api_security_outside_home
    from personalclaw.dashboard.handlers.onboarding_import import (
        register_onboarding_import_routes,
    )

    # What an apply rebuilds afterwards is not what these tests watch.
    monkeypatch.setattr(personalclaw.agent, "rebuild_agent_config", lambda: None)
    app = web.Application()
    app["state"] = SimpleNamespace(_background_tasks=set())
    app.router.add_get("/api/mcp/importable", h.api_mcp_importable)
    app.router.add_post("/api/mcp/apply", h.api_mcp_apply)
    app.router.add_get("/api/security/outside-home", api_security_outside_home)
    register_onboarding_import_routes(app)
    return TestClient(TestServer(app))


def _tools(body: dict) -> dict[str, dict]:
    return {tool["place"]: tool for tool in body["tools"]}


def _sources(body: dict) -> dict[str, dict]:
    return {source["place"]: source for source in body["sources"]}


async def _job(client: TestClient) -> dict:
    """The onboarding import, once it has finished."""
    for _ in range(600):
        job = (await (await client.get("/api/onboarding/import/job")).json())["job"]
        if job is not None and job["status"] != "running":
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("the import did not finish")


# ── the Tools page ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tools_page_visit_reads_no_other_tools_setup(machine, monkeypatch):
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            resp = await client.get("/api/mcp/importable")
            body = await resp.json()
    assert resp.status == 200, body
    assert seen == [], f"a visit read another tool's files: {seen}"
    assert body["servers"] == [] and body["unreadable"] == []
    tools = _tools(body)
    assert set(tools) == {CLAUDE, CODEX}
    assert {p: (t["looked"], t["allowed"]) for p, t in tools.items()} == {
        CLAUDE: (False, False),
        CODEX: (False, False),
    }
    assert tools[CLAUDE]["name"] == "Claude Code" and tools[CODEX]["name"] == "Codex"


@pytest.mark.asyncio
async def test_looking_in_a_tool_reads_that_tool_and_lists_its_servers(machine, monkeypatch):
    """The positive control: the press reads exactly the tool it names."""
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            resp = await client.get("/api/mcp/importable", params={"look_in": CLAUDE})
            body = await resp.json()
    assert resp.status == 200, body
    assert ("open", "~/.claude.json") in seen
    assert ("open", "~/src/garden/.mcp.json") in seen
    assert _of(seen, "~/.codex") == [], "looking in one tool read another"
    rows = {(row["name"], row["place"]) for row in body["servers"]}
    assert rows == {(CLAUDE_SERVER, CLAUDE), (PROJECT_SERVER, CLAUDE)}
    tools = _tools(body)
    assert (tools[CLAUDE]["looked"], tools[CODEX]["looked"]) == (True, False)


@pytest.mark.asyncio
async def test_a_tool_turned_on_in_settings_is_read_without_a_press(machine, monkeypatch):
    _allow(CODEX)
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            body = await (await client.get("/api/mcp/importable")).json()
    assert ("open", "~/.codex/config.toml") in seen
    assert _of(seen, "~/.claude", "~/src") == [], "a tool nobody turned on was read"
    assert [(row["name"], row["place"]) for row in body["servers"]] == [(CODEX_SERVER, CODEX)]
    tools = _tools(body)
    assert (tools[CODEX]["looked"], tools[CODEX]["allowed"]) == (True, True)
    assert (tools[CLAUDE]["looked"], tools[CLAUDE]["allowed"]) == (False, False)


@pytest.mark.asyncio
async def test_importing_a_listed_server_reads_only_the_tool_it_came_from(machine, monkeypatch):
    from personalclaw.config.loader import config_dir

    async with _client(monkeypatch) as client:
        listing = await (await client.get("/api/mcp/importable", params={"look_in": CLAUDE})).json()
        row = next(r for r in listing["servers"] if r["name"] == CLAUDE_SERVER)
        change = {
            "name": row["name"],
            "personalclaw": True,
            "from": row["id"],
            "place": row["place"],
        }
        with reads(machine) as seen:
            body = await (await client.post("/api/mcp/apply", json={"changes": [change]})).json()
    assert "error" not in body["results"][0], body
    assert ("open", "~/.claude.json") in seen
    assert _of(seen, "~/.codex") == [], "importing from one tool read another"
    stored = json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert stored[CLAUDE_SERVER]["command"] == COMMAND


@pytest.mark.asyncio
async def test_an_import_that_names_no_place_reads_nothing(machine, monkeypatch):
    """A row id alone names no tool to look in, so nothing outside the home is read for it."""
    async with _client(monkeypatch) as client:
        listing = await (await client.get("/api/mcp/importable", params={"look_in": CLAUDE})).json()
        row = next(r for r in listing["servers"] if r["name"] == CLAUDE_SERVER)
        change = {"name": row["name"], "personalclaw": True, "from": row["id"]}
        with reads(machine) as seen:
            body = await (await client.post("/api/mcp/apply", json={"changes": [change]})).json()
    assert seen == [], f"an import that named no tool read one: {seen}"
    assert "place" in body["results"][0]["error"], body


@pytest.mark.asyncio
async def test_a_press_naming_what_is_not_a_tools_setup_is_refused(machine, monkeypatch):
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            skills = await client.get("/api/mcp/importable", params={"look_in": "agent-skills"})
            nowhere = await client.get(
                "/api/onboarding/import", params={"look_in": "setup:nowhere"}
            )
            bodies = [await skills.json(), await nowhere.json()]
    assert (skills.status, nowhere.status) == (400, 400), bodies
    assert [b["error"]["code"] for b in bodies] == ["bad_request", "bad_request"]
    assert seen == []


def test_a_lookup_by_name_reads_claude_codes_file_only_once_turned_on(machine):
    from personalclaw.dashboard.handlers import mcp as h

    with reads(machine) as seen:
        assert h._find_server_spec_anywhere(CLAUDE_SERVER) is None
    assert seen == [], f"a lookup by name read Claude Code's file: {seen}"

    _allow(CLAUDE)
    with reads(machine) as seen:
        spec = h._find_server_spec_anywhere(CLAUDE_SERVER)
    assert spec is not None and spec["command"] == COMMAND
    assert ("open", "~/.claude.json") in seen


# ── the setup step ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opening_the_setup_step_reads_no_other_tools_setup(machine, monkeypatch):
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            resp = await client.get("/api/onboarding/import")
            body = await resp.json()
    assert resp.status == 200, body
    assert seen == [], f"opening the step read another tool's files: {seen}"
    sources = _sources(body)
    assert set(sources) == {CLAUDE, CODEX}
    for source in sources.values():
        assert (source["looked"], source["allowed"], source["detected"]) == (False, False, False)
        assert source["items"] == []


@pytest.mark.asyncio
async def test_looking_in_the_tools_from_the_setup_step_reads_them(machine, monkeypatch):
    """The positive control: what the step's press names is read, and listed."""
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            body = await (
                await client.get(
                    "/api/onboarding/import", params=[("look_in", CLAUDE), ("look_in", CODEX)]
                )
            ).json()
    assert ("open", "~/.claude.json") in seen and ("open", "~/.codex/config.toml") in seen
    assert ("open", "~/.claude/CLAUDE.md") in seen and ("open", "~/src/garden/CLAUDE.md") in seen
    sources = _sources(body)
    assert all(s["looked"] and s["detected"] for s in sources.values()), sources
    titles = {(item["source"], item["title"]) for s in sources.values() for item in s["items"]}
    assert {("claude_code", CLAUDE_SERVER), ("codex", CODEX_SERVER)} <= titles


@pytest.mark.asyncio
async def test_a_setup_turned_on_is_looked_in_as_the_step_opens(machine, monkeypatch):
    _allow(CLAUDE)
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            body = await (await client.get("/api/onboarding/import")).json()
    assert ("open", "~/.claude.json") in seen
    assert _of(seen, "~/.codex") == [], "a tool nobody turned on was read"
    sources = _sources(body)
    assert (sources[CLAUDE]["looked"], sources[CLAUDE]["allowed"]) == (True, True)
    assert (sources[CODEX]["looked"], sources[CODEX]["items"]) == (False, [])


@pytest.mark.asyncio
async def test_the_import_press_reads_the_tools_it_names_and_no_other(machine, monkeypatch):
    from personalclaw.config.loader import config_dir

    async with _client(monkeypatch) as client:
        looked = await (
            await client.get("/api/onboarding/import", params={"look_in": CLAUDE})
        ).json()
        item = next(i for i in _sources(looked)[CLAUDE]["items"] if i["title"] == CLAUDE_SERVER)
        pick = {"fingerprints": [item["fingerprint"]], "look_in": [CLAUDE]}
        with reads(machine) as seen:
            started = await client.post("/api/onboarding/import", json=pick)
            assert started.status == 202, await started.text()
            job = await _job(client)
    assert job["status"] == "done", job
    assert [r["outcome"] for r in job["report"]["results"]] == ["imported"]
    assert ("open", "~/.claude.json") in seen
    assert _of(seen, "~/.codex") == [], "the import read a tool it was not asked to"
    stored = json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert CLAUDE_SERVER in stored


@pytest.mark.asyncio
async def test_an_import_press_naming_no_tool_reads_none(machine, monkeypatch):
    """The pick alone names no tool, so the import's own re-scan reads nothing outside the home,
    and what was picked is reported missing rather than brought over."""
    async with _client(monkeypatch) as client:
        looked = await (
            await client.get("/api/onboarding/import", params={"look_in": CLAUDE})
        ).json()
        item = next(i for i in _sources(looked)[CLAUDE]["items"] if i["title"] == CLAUDE_SERVER)
        with reads(machine) as seen:
            started = await client.post(
                "/api/onboarding/import", json={"fingerprints": [item["fingerprint"]]}
            )
            assert started.status == 202, await started.text()
            job = await _job(client)
    assert seen == [], f"an import that named no tool read one: {seen}"
    assert job["report"]["missing"] == [item["fingerprint"]], job


# ── Settings ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_lists_each_tools_setup_as_a_place_and_reads_none(machine, monkeypatch):
    async with _client(monkeypatch) as client:
        with reads(machine) as seen:
            body = await (await client.get("/api/security/outside-home")).json()
    assert seen == [], f"listing the places read one: {seen}"
    places = {p["id"]: p for p in body["places"]}
    home = machine.home
    assert places[CLAUDE]["label"] == "Your Claude Code setup"
    assert places[CLAUDE]["paths"] == [str(home / ".claude"), str(home / ".claude.json")]
    assert places[CODEX]["label"] == "Your Codex setup"
    assert places[CODEX]["paths"] == [str(home / ".codex"), str(home / ".agents" / "skills")]
    assert not places[CLAUDE]["allowed"] and not places[CODEX]["allowed"]
    assert "project" in places[CLAUDE]["detail"], "Claude Code's projects are read too: say so"

    _allow(CLAUDE)
    async with _client(monkeypatch) as client:
        body = await (await client.get("/api/security/outside-home")).json()
    assert {p["id"]: p["allowed"] for p in body["places"]}[CLAUDE] is True
