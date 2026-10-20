"""Another tool's file that is THERE but cannot be read is named, never read as empty.

Claude Code's and Codex's config readers answered a file that was not there and a file that was
there but would not open or parse the same way: ``{}``. An empty config is "this tool keeps no
servers"; an unreadable one is "nobody knows what it keeps". So a truncated ``.claude.json`` or a
hand-edited ``config.toml`` with a typo read, on the Tools page's Import and on the onboarding
step, as "nothing to import".

Now a reader raises for a file that is there and unreadable, and each surface names the file and
why: the MCP listing both surfaces share (``McpListing.unreadable``), the Tools page's import list
(``GET /api/mcp/importable`` → ``unreadable``) and the onboarding scan
(``ScanResult.unreadable_files``, and a tool with one stays on the step). What the file holds, or
decides, is left out rather than guessed. Every root here is a scratch folder under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.home_paths import from_home
from personalclaw.onboarding_import import detected
from personalclaw.onboarding_import.sources import claude_code, codex

#: Sits inside every broken fixture, so a test can show the reason never quotes the file.
PLANTED = "planted-value-7c1f"
#: The owner's press, Look in Claude Code and Codex: what lets the Tools page's list read them.
LOOK = ("setup:claude_code", "setup:codex")

BROKEN_CLAUDE_CONFIG = (
    '{"mcpServers": {"notes": {"command": "notes-mcp", "env": {"TOKEN": "' + PLANTED + '"}}}'
)
BROKEN_CODEX_CONFIG = (
    '[mcp_servers.notes]\ncommand = "notes-mcp"\nenv = { TOKEN = "' + PLANTED + '"\n'
)


def _entries(listing) -> list[dict]:
    return [entry.to_dict() for entry in listing.unreadable]


def _server(command: str) -> dict:
    return {"command": command}


@pytest.fixture
def claude_root(tmp_path) -> Path:
    root = tmp_path / "claude-config"
    root.mkdir()
    return root


@pytest.fixture
def codex_root(tmp_path) -> Path:
    root = tmp_path / "codex-home"
    root.mkdir()
    return root


# ── the listing both surfaces read ─────────────────────────────────────────────


def test_a_claude_config_that_does_not_parse_is_named_and_lists_nothing(claude_root):
    config = claude_root / ".claude.json"
    config.write_text(BROKEN_CLAUDE_CONFIG, encoding="utf-8")
    listing = claude_code.mcp_servers(claude_root)
    assert listing.servers == []
    assert _entries(listing) == [
        {"path": from_home(config), "why": "it is not valid JSON (line 1, column 91)"}
    ]
    assert PLANTED not in json.dumps(_entries(listing)), "the reason quoted the file"


def test_a_claude_config_that_is_not_there_is_nothing_and_no_failure(claude_root):
    """The control: a missing config is still an empty listing with nothing to name."""
    listing = claude_code.mcp_servers(claude_root)
    assert listing.servers == [] and listing.unreadable == []


def test_a_config_that_is_not_an_object_is_named(claude_root):
    config = claude_root / ".claude.json"
    config.write_text('["notes"]', encoding="utf-8")
    assert _entries(claude_code.mcp_servers(claude_root)) == [
        {"path": from_home(config), "why": "it is not a JSON object"}
    ]


def test_an_unreadable_settings_file_leaves_out_only_the_project_servers(claude_root, tmp_path):
    """Whether a project's servers were approved, and what they expand to, is in settings.json."""
    project = tmp_path / "work" / "ledger-app"
    project.mkdir(parents=True)
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"deploy": _server("deploy-mcp")}}), encoding="utf-8"
    )
    (claude_root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"notes": _server("notes-mcp")},
                "projects": {str(project): {"mcpServers": {"ledger": _server("ledger-mcp")}}},
            }
        ),
        encoding="utf-8",
    )
    settings = claude_root / "settings.json"
    settings.write_text('{"env": {"A": "1"},', encoding="utf-8")
    listing = claude_code.mcp_servers(claude_root)
    assert [(s.name, s.scope) for s in listing.servers] == [("notes", "user"), ("ledger", "local")]
    assert _entries(listing) == [
        {"path": from_home(settings), "why": "it is not valid JSON (line 1, column 20)"}
    ]


def test_an_unreadable_project_file_leaves_out_that_project_alone(claude_root, tmp_path):
    good, bad = tmp_path / "work" / "garden", tmp_path / "work" / "ledger-app"
    for project, name in ((good, "planner"), (bad, "deploy")):
        project.mkdir(parents=True)
        (project / ".mcp.json").write_text(
            json.dumps({"mcpServers": {name: _server(f"{name}-mcp")}}), encoding="utf-8"
        )
    (bad / ".mcp.json").write_text('{"mcpServers": ', encoding="utf-8")
    (claude_root / ".claude.json").write_text(
        json.dumps({"projects": {str(good): {}, str(bad): {}}}), encoding="utf-8"
    )
    listing = claude_code.mcp_servers(claude_root)
    assert [s.name for s in listing.servers] == ["planner"]
    assert _entries(listing) == [
        {
            "path": from_home(bad / ".mcp.json"),
            "why": "it is not valid JSON (line 1, column 16)",
        }
    ]


def test_a_codex_config_that_does_not_parse_is_named_and_lists_nothing(codex_root):
    config = codex_root / "config.toml"
    config.write_text(BROKEN_CODEX_CONFIG, encoding="utf-8")
    listing = codex.mcp_servers(codex_root)
    assert listing.servers == []
    assert _entries(listing) == [
        {"path": from_home(config), "why": "it is not valid TOML (line 3, column 37)"}
    ]
    assert PLANTED not in json.dumps(_entries(listing)), "the reason quoted the file"


# ── the Tools page's Import ────────────────────────────────────────────────────


@pytest.fixture
def two_tools(monkeypatch, claude_root, codex_root):
    """Claude Code's config broken, Codex's fine with one server, nothing in PersonalClaw yet."""
    from personalclaw import mcp_discovery

    config = claude_root / ".claude.json"
    config.write_text(BROKEN_CLAUDE_CONFIG, encoding="utf-8")
    (codex_root / "config.toml").write_text(
        '[mcp_servers.planner]\ncommand = "planner-mcp"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        mcp_discovery,
        "_import_sources",
        lambda: (("claude_code", lambda: config), ("codex", lambda: codex_root)),
    )
    monkeypatch.setattr(mcp_discovery, "_mcp_json_paths", lambda: (claude_root / "none.json",))
    monkeypatch.setattr(mcp_discovery, "_load_agent_config", lambda: {})
    return config


def test_the_import_list_names_the_file_it_could_not_read_beside_what_it_could(two_tools):
    from personalclaw import mcp_discovery

    servers, unreadable, _tools = mcp_discovery.discover_importable_servers(asked=LOOK)
    assert [(s["name"], s["backend"]) for s in servers] == [("planner", "Codex")]
    assert unreadable == [
        {
            "backend": "Claude Code",
            "path": from_home(two_tools),
            "why": "it is not valid JSON (line 1, column 91)",
        }
    ]


@pytest.mark.asyncio
async def test_the_import_route_answers_the_unreadable_file(two_tools):
    from personalclaw.dashboard.handlers.mcp import api_mcp_importable

    query = "&".join(f"look_in={place}" for place in LOOK)
    resp = await api_mcp_importable(make_mocked_request("GET", f"/api/mcp/importable?{query}"))
    body = json.loads(resp.body.decode())
    assert resp.status == 200
    assert [s["name"] for s in body["servers"]] == ["planner"]
    assert [(u["backend"], u["path"]) for u in body["unreadable"]] == [
        ("Claude Code", from_home(two_tools))
    ]


# ── the onboarding step ────────────────────────────────────────────────────────


def test_a_tool_whose_config_is_unreadable_stays_on_the_step_and_says_so(claude_root):
    config = claude_root / ".claude.json"
    config.write_text(BROKEN_CLAUDE_CONFIG, encoding="utf-8")
    result = claude_code.scan(claude_root)
    assert result.items == []
    assert result.to_dict()["unreadable_files"] == [
        {"path": from_home(config), "why": "it is not valid JSON (line 1, column 91)"}
    ]
    assert detected([result]) == [result], "left off the step, it reads as nothing found"


def test_a_tool_with_nothing_in_it_is_still_not_detected(claude_root):
    """The control: present and empty, with nothing unreadable, is still 'nothing found'."""
    result = claude_code.scan(claude_root)
    assert result.items == [] and result.unreadable_files == []
    assert detected([result]) == []


def test_a_file_asked_for_twice_is_named_once(claude_root):
    """settings.json is read for the project servers and again for its refused commands."""
    settings = claude_root / "settings.json"
    settings.write_text('{"env": {"A": "1"},', encoding="utf-8")
    result = claude_code.scan(claude_root)
    assert [entry.path for entry in result.unreadable_files] == [from_home(settings)]


def test_an_instruction_file_that_will_not_open_is_named(claude_root, monkeypatch):
    instructions = claude_root / "CLAUDE.md"
    instructions.write_text("Answer in one paragraph.\n", encoding="utf-8")
    real = Path.read_text

    def refused(self, *args, **kwargs):
        if self == instructions:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refused)
    result = claude_code.scan(claude_root)
    assert result.items == []
    assert result.to_dict()["unreadable_files"] == [
        {"path": from_home(instructions), "why": "it could not be opened (Permission denied)"}
    ]


def test_a_codex_config_and_agent_that_do_not_parse_are_named_and_the_rest_comes_over(
    codex_root,
):
    config = codex_root / "config.toml"
    config.write_text(BROKEN_CODEX_CONFIG, encoding="utf-8")
    agents = codex_root / "agents"
    agents.mkdir()
    (agents / "reviewer.toml").write_text(
        'name = "reviewer"\ndeveloper_instructions = """Read twice.\n', encoding="utf-8"
    )
    (agents / "writer.toml").write_text(
        'name = "writer"\ndeveloper_instructions = "Write short."\n', encoding="utf-8"
    )
    result = codex.scan(codex_root)
    assert [item.title for item in result.items] == ["writer"]
    assert result.to_dict()["unreadable_files"] == [
        {"path": from_home(config), "why": "it is not valid TOML (line 3, column 37)"},
        {"path": from_home(agents / "reviewer.toml"), "why": "it is not valid TOML"},
    ]
