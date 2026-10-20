"""A turn's catalog of deferred tools names every one of PersonalClaw's own, however many tool
servers are set up.

A turn too big for every tool's schema sends the schemas that matter and lists the rest in a
catalog. The catalog filled its 6,000 characters provider by provider in name order, and every
external MCP server's tools sit under ``mcp``, which sorts before ``personalclaw-*``. In a captured
request with six servers set up, every one of PersonalClaw's own groups (tasks, knowledge,
workflows, automations, …) was cut to a count such as "personalclaw-automation (+9 tools)", and the
agent answered a question about the owner's automations without ever reading them: the turn never
named ``automation_list``. And the line that said what was cut was added after the room was spent,
so the catalog ran past its own bound.

Now each of the platform's groups says what it is for and names every tool it has; the servers,
one group each, and the installed apps share the room that leaves, the tools the request is about
first and then one a group in turn; a last line says what was left out and how to find it; and the
whole catalog stays inside its bound. Where the turn's ranking ties, the platform's own tool goes
first, in the schemas a turn carries and in ``tool_search``'s answer alike.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from test_native_runtime import _defn, _drain, _ScriptedModel

from personalclaw.agents.native.builtin_tools import (
    PLATFORM_CATEGORIES,
    PLATFORM_DISPLAY_NAME,
    PLATFORM_PROVIDER_NAME,
    NativeBuiltinToolProvider,
)
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tool_retrieval import (
    CATALOG_NOTE,
    CatalogGroup,
    ToolRetriever,
    _schema_chars,
    catalog_groups,
)
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.mcp_client import McpToolSpec
from personalclaw.providers import registry as apps
from personalclaw.providers.loader import load_factory
from personalclaw.providers.mcp_instances import MCP_TOOLS_EXTENSION
from personalclaw.tool_providers import registry as tools
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

#: The catalog's bound when a turn does not give one (`ToolRetriever.catalog`).
_ROOM = 6000

_VERBS = ("list", "get", "create", "update", "find", "delete", "read", "move", "label")
_NOUNS = ("entries", "pages", "items", "reports", "folders", "comments", "records", "sheets")

#: Tool servers set up the way a captured request had six: about fifty tools between them, each
#: with a sentence of description. Every one of their names sorts before PersonalClaw's own groups.
_SERVERS = {
    "api-docs": 5,
    "code-host": 18,
    "docs-lookup": 2,
    "error-feed": 9,
    "file-shelf": 14,
    "repo-wiki": 3,
}


def _server_tools(server: str, n: int) -> list[McpToolSpec]:
    return [
        McpToolSpec(
            name=f"{_VERBS[i % len(_VERBS)]}_{_NOUNS[i // len(_VERBS) % len(_NOUNS)]}_{i}",
            description=(
                f"{_VERBS[i % len(_VERBS)].title()} the {_NOUNS[i // len(_VERBS) % len(_NOUNS)]} "
                f"kept by the {server} service, filtered by owner and date."
            ),
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        for i in range(n)
    ]


class _Server:
    """One connected MCP server, as the MCP Tool Servers app's provider reads it."""

    def __init__(self, specs: list[McpToolSpec]) -> None:
        self._specs = specs

    async def list_tools(self) -> list[McpToolSpec]:
        return list(self._specs)


def _ship_the_platform(monkeypatch) -> list[ToolProvider]:
    """Every tool provider PersonalClaw ships, registered the way a gateway registers its bundled
    apps: each manifest in the app registry and each provider at core's standing. Returns them."""
    monkeypatch.setattr(apps, "_registry", None)  # a registry of this test's own
    registry = apps.get_provider_registry()
    shipped: list[ToolProvider] = []
    for app_dir in sorted(NATIVE_DIR.iterdir()):
        if not (app_dir / "app.json").exists():
            continue
        manifest = AppManifest.from_json_file(app_dir / "app.json")
        if not manifest.provider:
            continue
        registry.register(manifest)
        for cfg in manifest.all_providers():
            if cfg.type != "tool" or cfg.multiInstance:
                continue
            ext = apps.RegisteredProvider(
                name=manifest.name, manifest=manifest, provider_config=cfg
            )
            built = load_factory(ext)({})
            for provider in built if isinstance(built, list) else [built]:
                if provider is not None:
                    tools.register_provider(provider, app=manifest.name, core=True)
                    shipped.append(provider)
    return shipped


def _connect(servers: dict[str, list[McpToolSpec]]) -> ToolProvider:
    """The MCP Tool Servers app's own provider, serving *servers*, registered as it ships."""
    module = load_bundle_module(NATIVE_DIR / "mcp-tools", MCP_TOOLS_EXTENSION, "provider")
    conns = {name: _Server(specs) for name, specs in servers.items()}
    provider = module.McpToolProvider(lambda: conns)
    tools.register_provider(provider, app=MCP_TOOLS_EXTENSION, core=True)
    return provider


@dataclass
class _Turn:
    """What one turn sent the model: the tools it carried schemas for, and its catalog note."""

    surfaced: set[str]
    note: str
    own: dict[str, list[str]] = field(default_factory=dict)

    @property
    def listing(self) -> str:
        """The catalog itself: the note's lines after its opening sentence."""
        return self.note.split(CATALOG_NOTE + "\n", 1)[1].split("\n\n", 1)[0]

    def names(self, tool: str) -> bool:
        """Whether the turn names *tool*: by its schema, or by its whole name in the catalog."""
        whole = rf"(?<![\w/-]){re.escape(tool)}(?![\w/-])"
        return tool in self.surfaced or re.search(whole, self.listing) is not None


async def _her_turn(tmp_path: Path, monkeypatch, request: str) -> _Turn:
    shipped = _ship_the_platform(monkeypatch)
    _connect({name: _server_tools(name, n) for name, n in _SERVERS.items()})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    platform = NativeBuiltinToolProvider(
        cwd=workspace,
        categories=PLATFORM_CATEGORIES,
        provider_name=PLATFORM_PROVIDER_NAME,
        display=PLATFORM_DISPLAY_NAME,
    )
    model = _ScriptedModel(
        [[AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)]]
    )
    runtime = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=tools.tool_surface(platform),
        cwd=workspace,
    )
    await runtime.start()
    await _drain(runtime, request)
    sent = model.seen_messages[-1]
    notes = [str(m.get("content")) for m in sent if m.get("role") == "system"]
    note = next(n for n in notes if CATALOG_NOTE in n)
    own = {p.name: [t.name for t in await p.list_tools()] for p in [platform, *shipped]}
    surfaced = {t["function"]["name"] for t in model.last_tools or []}
    return _Turn(surfaced=surfaced, note=note, own=own)


@pytest.mark.asyncio
async def test_with_servers_that_sort_first_every_platform_group_is_named(tmp_path, monkeypatch):
    """Six servers' tools no longer take the room first: each of PersonalClaw's own groups is
    listed under its name with what it is for, and every one of its tools is named."""
    turn = await _her_turn(tmp_path, monkeypatch, "Run my weekly review from my notes.")
    for provider, names in turn.own.items():
        deferred = [n for n in names if n not in turn.surfaced]
        assert all(turn.names(n) for n in names), (
            provider,
            [n for n in names if not turn.names(n)],
        )
        if deferred:
            assert re.search(rf"^\[{re.escape(provider)}\] \S", turn.listing, re.M), provider
    assert "[personalclaw-automation] Create and manage automations in chat" in turn.listing
    # The servers are listed under their own names, and what did not fit is said, not dropped.
    assert "[mcp/code-host]" in turn.listing
    assert turn.listing.splitlines()[-1].startswith("[not listed, for room: tool_search(")
    assert len(turn.listing) <= _ROOM


@pytest.mark.parametrize(
    "request_text,tool",
    [
        ("Run my weekly review from my notes.", "automation_list"),
        ("Which of my tasks are ready to start?", "task_ready"),
        ("What decisions did I log last month?", "decision_list"),
        ("Show me my saved artifacts.", "artifact_list"),
        ("Which project runs are going right now?", "project_run_list"),
    ],
)
@pytest.mark.asyncio
async def test_a_request_about_a_platform_capability_surfaces_its_tool(
    tmp_path, monkeypatch, request_text, tool
):
    turn = await _her_turn(tmp_path, monkeypatch, request_text)
    assert turn.names(tool), turn.listing


# ── the catalog itself ───────────────────────────────────────────────────────────────────────


@dataclass
class _Def:
    name: str
    description: str = ""
    provider: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


def _fleet(servers: int, per: int, *, desc: int = 100) -> list[_Def]:
    """*servers* tool servers of *per* tools each, every description *desc* characters long."""
    return [
        _Def(
            name=f"mcp/server-{s:02d}/tool_{t:02d}",
            description=(f"Does thing {t} of server {s}. " + "x" * desc)[:desc],
            provider="mcp",
        )
        for s in range(servers)
        for t in range(per)
    ]


def _own(provider: str, names: list[str], purpose: str = "") -> tuple[list[_Def], dict]:
    defs = [
        _Def(name=n, description=f"{n.replace('_', ' ')}, as asked.", provider=provider)
        for n in names
    ]
    group = CatalogGroup(provider, own=True, purpose=purpose)
    return defs, {d.name: group for d in defs}


def _platform() -> tuple[list[_Def], dict[str, CatalogGroup]]:
    """Five of the platform's groups, about the size of the real ones."""
    defs: list[_Def] = []
    groups: dict[str, CatalogGroup] = {}
    for provider, stem, n in (
        ("personalclaw-automation", "automation", 12),
        ("personalclaw-tasks-tools", "task", 9),
        ("personalclaw-workflows", "workflow", 23),
        ("personalclaw-knowledge-tools", "knowledge", 9),
        ("personalclaw-memory", "memory", 6),
    ):
        d, g = _own(provider, [f"{stem}_op_{i:02d}" for i in range(n)], f"What {stem} is for")
        defs += d
        groups |= g
    return defs, groups


def _apps(n: int, per: int) -> list[_Def]:
    """*n* installed apps' providers of *per* tools each, every description 100 characters."""
    return [
        _Def(name=f"app{a:02d}_tool_{t:02d}", description="x" * 100, provider=f"app-{a:02d}")
        for a in range(n)
        for t in range(per)
    ]


@pytest.mark.parametrize("room", [_ROOM, 3000, 1500, 400, 60, 10])
@pytest.mark.parametrize("fleet", ["apps", "servers"])
def test_the_catalog_never_outgrows_its_budget(room, fleet):
    """Twenty-two groups of long descriptions: the last line that counts what was left out was
    added after the room was spent, and ran past it (6,355 characters of 6,000)."""
    defs = _apps(22, 25) if fleet == "apps" else _fleet(22, 25)
    assert len(ToolRetriever(defs).catalog(max_chars=room)) <= room


@pytest.mark.parametrize("room", [_ROOM, 2500, 900, 300, 40])
def test_the_platforms_groups_stay_named_whatever_the_room(room):
    """The platform's groups come first: listed with every tool's name while they fit, and counted
    by name on the last line when even that does not."""
    own_defs, groups = _platform()
    retriever = ToolRetriever([*_fleet(8, 20), *own_defs], groups=groups)
    text = retriever.catalog(max_chars=room)
    assert len(text) <= room
    if room >= _ROOM:
        assert all(re.search(rf"\b{d.name}\b", text) for d in own_defs)
    if room >= 300:
        assert all(f"personalclaw-{g}" in text for g in ("automation", "workflows", "memory"))


def test_the_servers_share_the_room_in_turn():
    """With nothing in the request to rank by, every server gets a line before any gets two, and
    the last line says how many of each it left out."""
    text = ToolRetriever(_fleet(10, 30)).catalog()
    described = {s: len(re.findall(rf"^- mcp/server-{s:02d}/", text, re.M)) for s in range(10)}
    assert min(described.values()) >= 1, described
    assert max(described.values()) - min(described.values()) <= 1, described
    last = text.splitlines()[-1]
    for s, n in described.items():
        assert f"mcp/server-{s:02d} (+{30 - n} more)" in last, last


def test_the_tools_a_request_is_about_are_described_first():
    """The tools the turn ranked for the request but could not carry are the first the catalog
    describes, ahead of the turn-by-turn share, wherever they sort."""
    own_defs, groups = _platform()
    # The desk's own other tools sort first, so a turn-by-turn share alone never reaches these.
    filler = [
        _Def(name=f"mcp/issue-desk/aa_tool_{i:02d}", description="Unrelated.", provider="mcp")
        for i in range(15)
    ]
    asked = [
        _Def(
            name=f"mcp/issue-desk/zz_{verb}_issues",
            description=f"{verb.title()} the open issues in the issue desk, newest first.",
            provider="mcp",
        )
        for verb in ("list", "find", "count", "close", "label")
    ]
    defs = [*_fleet(8, 20), *filler, *asked, *own_defs]
    retriever = ToolRetriever(defs, k=2, groups=groups)
    surfaced = {d.name for d in retriever.select("list the open issues in the issue desk")}
    text = retriever.catalog(exclude=surfaced)
    deferred = [d.name for d in asked if d.name not in surfaced]
    assert len(deferred) == 3, surfaced
    assert all(re.search(rf"^- {re.escape(n)}: ", text, re.M) for n in deferred), text
    assert all(re.search(rf"\b{d.name}\b", text) for d in own_defs)


def test_a_platform_tool_wins_a_tie_for_the_schema_budget():
    """A link in the request hints both a server's fetch tool and the platform's: when the turn's
    schema budget has room for one, it is the platform's, where name order chose the server's."""
    schema = {"type": "object", "properties": {"url": {"type": "string"}}}
    own = _Def(name="web_fetch", description="Read one page.", provider="personalclaw-web")
    server = _Def(name="mcp/aaa/fetch_page", description="Read one page.", provider="mcp")
    own.parameters = server.parameters = schema
    padding = [_Def(name=f"pad_{i}", description="unrelated", provider="pad") for i in range(60)]
    groups = {"web_fetch": CatalogGroup("personalclaw-web", own=True)}
    retriever = ToolRetriever([own, server, *padding], groups=groups)
    one = max(_schema_chars(own), _schema_chars(server))  # room for either, not both
    names = {d.name for d in retriever.select("summarize https://example.com/a", budget_chars=one)}
    assert "web_fetch" in names and "mcp/aaa/fetch_page" not in names, names


def test_tool_search_answers_with_what_the_run_is_shown():
    """The answer is cut at its limit after the tools the run is not shown are left out, and a
    tie goes to the platform's own: a narrowed run used to be told 'no tools matched'."""
    hidden = [
        _Def(name=f"mcp/aaa/report_{i:02d}", description="report", provider="mcp")
        for i in range(25)
    ]
    shown = [
        _Def(name=f"mcp/bbb/report_{i}", description="report", provider="mcp") for i in range(3)
    ]
    own = _Def(name="report_build", description="report", provider="personalclaw-reports")
    retriever = ToolRetriever(
        [*hidden, *shown, own],
        groups={"report_build": CatalogGroup("personalclaw-reports", own=True)},
    )
    hits = retriever.search("report", limit=5, shown=lambda n: not n.startswith("mcp/aaa/"))
    assert [h["name"] for h in hits] == ["report_build", *(d.name for d in shown)]


class _App(ToolProvider):
    """A tool provider an installed app adds."""

    @property
    def name(self) -> str:
        return "garden-planner"

    @property
    def display_name(self) -> str:
        return "Garden Planner"

    async def list_tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="bed_layout", description="Lay out a bed.", provider=self.name)]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="")


@pytest.mark.asyncio
async def test_a_platform_group_says_what_it_is_for_and_a_servers_tools_are_the_servers(
    tmp_path, monkeypatch
):
    """The platform's own groups are the providers PersonalClaw ships, said with their app's own
    words; an installed app's are its own, and the MCP Tool Servers app ships with PersonalClaw but
    serves each server's tools under that server."""
    shipped = {p.name: p for p in _ship_the_platform(monkeypatch)}
    _connect({"code-host": _server_tools("code-host", 2)})
    tools.register_provider(_App(), app="garden-planner")
    platform = NativeBuiltinToolProvider(
        cwd=tmp_path,
        categories=PLATFORM_CATEGORIES,
        provider_name=PLATFORM_PROVIDER_NAME,
        display=PLATFORM_DISPLAY_NAME,
    )
    served, _failures = await tools.serve(tools.tool_surface(platform))
    groups = catalog_groups(served)
    assert groups["automation_list"] == CatalogGroup(
        "personalclaw-automation", own=True, purpose="Create and manage automations in chat"
    )
    assert groups["read_file"] == CatalogGroup(
        PLATFORM_PROVIDER_NAME, own=True, purpose=PLATFORM_DISPLAY_NAME
    )
    assert groups["bed_layout"] == CatalogGroup("garden-planner")
    assert groups["mcp/code-host/list_entries_0"] == CatalogGroup("mcp/code-host")
    assert "personalclaw-automation" in shipped
