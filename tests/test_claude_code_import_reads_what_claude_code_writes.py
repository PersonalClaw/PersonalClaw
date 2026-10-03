"""The Claude Code importer reads the files Claude Code actually writes (F-01…F-04).

Every test runs against ``tests/fixtures/agent_tool_homes/noor`` — a small copy of a real
``~/.claude`` (Claude Code 2.1.x) and the one repository it names, laid out exactly as Claude Code
leaves them — copied into ``tmp_path``. ``HOME`` is that copy, so the projects the fixture's
``.claude.json`` records under its old machine's home (``/Users/noor/src/feedsmith``) are found
where a copied ``~/.claude`` finds them: under this home. ``CLAUDE_CONFIG_DIR`` is the copy's
``.claude`` and ``PERSONALCLAW_HOME`` is a fresh directory, so no test reads a developer's
``~/.claude`` or ``~/.codex`` or writes their ``~/.personalclaw``.

On ``origin/main`` before this change the importer read ``<root>/.mcp.json`` and
``<root>/memories/*.md`` — neither of which Claude Code writes — and knew no agents, commands,
rules, project instructions or transcripts: 0 of the fixture's 8 servers and 0 of its 6 memories
came over, and nothing said why.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.onboarding_import import ImportCategory, WriteOutcome, run_import, scan_source

FIXTURE = Path(__file__).parent / "fixtures" / "agent_tool_homes" / "noor"

#: Claude Code's setup as a place outside the home. The owner pressing Look in Claude Code
#: names it, and that press is what lets a scan read this machine's Claude Code.
PLACE = "setup:claude_code"
PRESS = frozenset({PLACE})

#: The Slack incoming webhook the persona's CLAUDE.md posts to. Fake, but shaped exactly like a
#: live one — which is why the committed fixture carries a marker instead: the forge's push
#: protection refuses a commit holding one. Assembled here, planted into the copy.
SLACK_WEBHOOK = (
    "https://hooks.slack.com/" + "services/T0EXAMPLE01/B0EXAMPLE01/9Xq2LmVb7TzR4wKpN8sYc1Df"
)


def plant_fixture_home(target: Path) -> Path:
    """Copy the fixture home to ``target`` with its planted values in place."""
    shutil.copytree(FIXTURE, target)
    notes = target / ".claude" / "CLAUDE.md"
    notes.write_text(
        notes.read_text(encoding="utf-8").replace("{{SLACK_WEBHOOK_URL}}", SLACK_WEBHOOK),
        encoding="utf-8",
    )
    return target


#: Credentials the fixture carries, fake and real-shaped. None may reach the scan's wire form.
_GITHUB_PAT = "github_pat_11FIXTURE0NOT0A0REAL0TOKEN0000_forTestsOnly"
_CONTEXT7_KEY = "ctx7sk-9b2e4f61-7a3c-4d8e-b5f0-1c6a2d9e3f47"
_GRAFANA_TOKEN = "glsa_Q3v8Tn2Lk9Wx4Rb7Zc1Ym6Hd0Fp5Js_2e7a9c1b"
_DB_PASSWORD = "s3a-Gull-Harbour-42"


@pytest.fixture
def noor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixture home, copied, with every root pointed at the copy or at a fresh home."""
    home = plant_fixture_home(tmp_path / "noor")
    pclaw = tmp_path / "pclaw-home"
    pclaw.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pclaw))
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")

    from personalclaw.config.loader import config_dir
    from personalclaw.onboarding_import.sources import claude_code

    assert SLACK_WEBHOOK in (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert Path.home() == home, "HOME did not bind"
    assert config_dir() == pclaw, "PERSONALCLAW_HOME did not bind — the real home is at risk"
    assert claude_code.resolve_root() == home / ".claude", "CLAUDE_CONFIG_DIR did not bind"
    return home


def _items(result, category: ImportCategory) -> dict[str, object]:
    return {item.key: item for item in result.by_category(category)}


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── MCP servers: all three scopes ─────────────────────────────────────────────


def test_mcp_servers_come_from_claude_json_and_each_projects_mcp_json(noor: Path) -> None:
    """User scope (``.claude.json`` → ``mcpServers``), local scope (``projects[p].mcpServers``)
    and project scope (``<project>/.mcp.json``), each labelled with where it was found."""
    result = scan_source("claude_code", asked=PRESS)
    servers = _items(result, ImportCategory.MCP_SERVERS)

    assert sorted(servers) == [
        "context7",
        "deepwiki",
        "github",
        "local:/Users/noor/work/carrier-webhooks:grafana",
        "local:/Users/noor/work/carrier-webhooks:kafka-dev",
        "project:/Users/noor/src/feedsmith:feedsmith-dev-db",
        "project:/Users/noor/src/feedsmith:playwright",
        "todoist",
    ]
    assert servers["github"].origin == "User scope"
    grafana = servers["local:/Users/noor/work/carrier-webhooks:grafana"]
    assert (grafana.target, grafana.origin) == (
        "grafana",
        "Local scope · /Users/noor/work/carrier-webhooks",
    )
    dev_db = servers["project:/Users/noor/src/feedsmith:feedsmith-dev-db"]
    assert (dev_db.target, dev_db.origin) == (
        "feedsmith-dev-db",
        "Project · ~/src/feedsmith/.mcp.json",
    )
    # Approved in Claude Code (`enabledMcpjsonServers`), so it starts ticked like any other.
    assert dev_db.preselect is True and dev_db.note == ""
    # `${FEEDSMITH_DEV_DATABASE_URL}` expanded from Claude Code's settings.json `env`, as Claude
    # Code expands it: PersonalClaw does not expand variables in a definition.
    assert dev_db.payload["env"]["DATABASE_URI"].startswith("postgresql://feedsmith:")
    # Whole, as Tools › Import reads it: the MCP writer stores each value in the credential store.
    assert servers["github"].payload["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": _GITHUB_PAT}
    assert grafana.payload["headers"] == {"Authorization": f"Bearer {_GRAFANA_TOKEN}"}
    assert (servers["github"].secrets_skipped, grafana.secrets_skipped) == (0, 0)
    wire = json.dumps(result.to_dict())
    for secret in (_GITHUB_PAT, _CONTEXT7_KEY, _GRAFANA_TOKEN, _DB_PASSWORD):
        assert secret not in wire


def test_a_project_server_nobody_approved_is_offered_but_not_ticked(noor: Path) -> None:
    """A repository's ``.mcp.json`` is the repository's code. Claude Code runs one of its servers
    only once approved; an unapproved one is still listed, with why, and never pre-ticked."""
    clone = noor / "src" / "cloned-demo"
    clone.mkdir(parents=True)
    (clone / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"demo-tools": {"command": "npx", "args": ["-y", "demo-mcp"]}}}),
        encoding="utf-8",
    )
    config_path = noor / ".claude" / ".claude.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["projects"]["/Users/noor/src/cloned-demo"] = {"enabledMcpjsonServers": []}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    result = scan_source("claude_code", asked=PRESS)
    demo = _items(result, ImportCategory.MCP_SERVERS)[
        "project:/Users/noor/src/cloned-demo:demo-tools"
    ]
    assert demo.preselect is False
    assert "never approved in Claude Code" in demo.note
    assert demo.to_dict()["preselected"] is False


def test_a_variable_nothing_sets_is_said_not_passed_on(noor: Path) -> None:
    mcp = noor / "src" / "feedsmith" / ".mcp.json"
    doc = json.loads(mcp.read_text(encoding="utf-8"))
    doc["mcpServers"]["playwright"]["env"] = {"PW_PROFILE": "${PLAYWRIGHT_PROFILE_DIR}"}
    mcp.write_text(json.dumps(doc), encoding="utf-8")

    item = _items(scan_source("claude_code", asked=PRESS), ImportCategory.MCP_SERVERS)[
        "project:/Users/noor/src/feedsmith:playwright"
    ]
    assert "${PLAYWRIGHT_PROFILE_DIR}" in item.note
    assert item.payload["env"] == {"PW_PROFILE": "${PLAYWRIGHT_PROFILE_DIR}"}


# ── memories: Claude Code's auto-memory ───────────────────────────────────────


def test_memories_are_the_auto_memory_topic_files_and_memory_md_is_their_index(noor: Path) -> None:
    result = scan_source("claude_code", asked=PRESS)
    memories = _items(result, ImportCategory.MEMORIES)

    assert sorted(memories) == [
        "projects/-Users-noor-src-feedsmith/memory/issue-412-cause.md",
        "projects/-Users-noor-src-feedsmith/memory/maintainers.md",
        "projects/-Users-noor-src-feedsmith/memory/release-checklist.md",
        "projects/-Users-noor-work-carrier-webhooks/memory/dedupe-is-postgres-not-lru.md",
        "projects/-Users-noor-work-carrier-webhooks/memory/never-quote-consignee-pii.md",
        "projects/-Users-noor-work-carrier-webhooks/memory/oncall-rotation.md",
    ]
    cause = memories["projects/-Users-noor-src-feedsmith/memory/issue-412-cause.md"]
    assert cause.title == "#412 cause is double escaping"  # the topic's own `name`
    assert cause.origin == "Project · ~/src/feedsmith"
    # A project that is not on this machine is still named by the path Claude Code recorded.
    pii = memories["projects/-Users-noor-work-carrier-webhooks/memory/never-quote-consignee-pii.md"]
    assert pii.origin == "Project · /Users/noor/work/carrier-webhooks"


def test_a_memory_md_with_notes_of_its_own_is_a_memory(noor: Path) -> None:
    index = noor / ".claude" / "projects" / "-Users-noor-src-feedsmith" / "memory" / "MEMORY.md"
    index.write_text(index.read_text(encoding="utf-8") + "- Tomás reviews changelog wording.\n")

    keys = set(_items(scan_source("claude_code", asked=PRESS), ImportCategory.MEMORIES))
    assert "projects/-Users-noor-src-feedsmith/memory/MEMORY.md" in keys


# ── agents, commands, rules, project instructions, transcripts ────────────────


def test_every_kind_claude_code_keeps_is_an_item_or_named_as_not_imported(noor: Path) -> None:
    result = scan_source("claude_code", asked=PRESS)

    assert sorted(_items(result, ImportCategory.INSTRUCTIONS)) == [
        "CLAUDE.md",
        "project:/Users/noor/src/feedsmith/CLAUDE.md",
        "rules/privacy.md",
        "rules/python.md",
    ]
    assert sorted(_items(result, ImportCategory.AGENTS)) == [
        "agents/code-reviewer.md",
        "agents/oncall-triage.md",
    ]
    assert sorted(_items(result, ImportCategory.PROMPTS)) == [
        "commands/handoff.md",
        "commands/standup.md",
    ]
    assert sorted(_items(result, ImportCategory.CONVERSATIONS)) == [
        "projects/-Users-noor-src-feedsmith/6277d318-971a-44e8-b375-0e64f38466e9.jsonl",
        "projects/-Users-noor/80aa7bec-27c9-4094-86e2-35fb104eed4f.jsonl",
        "projects/-Users-noor/9616f743-040c-4515-8bdf-701612aa21ba.jsonl",
    ]
    # What has no place here is named, with its size and its reason: prompt history, and the
    # parts of settings.json that are not a command Claude Code refuses.
    assert [(entry.what, entry.count) for entry in result.not_imported] == [
        ("Command rules that ask first", 4),
        ("Command rules that allow without asking", 13),
        ("Other permission rules", 16),
        ("Claude Code settings", 8),
        ("Prompt history", 5),
    ]
    assert result.not_imported[-1].why == (
        "PersonalClaw keeps no separate list of past prompts. The prompts in your "
        "conversations come over with them."
    )
    assert result.to_dict()["not_imported"][-1]["count"] == 5

    reviewer = _items(result, ImportCategory.AGENTS)["agents/code-reviewer.md"]
    assert reviewer.target == "code-reviewer"
    assert reviewer.note == (
        "Its Claude Code tools list (Read, Grep, Glob, Bash) and model (opus) are not carried over."
    )
    standup = _items(result, ImportCategory.PROMPTS)["commands/standup.md"]
    assert standup.target == "standup"
    assert "{{arguments}}" in standup.text and "$ARGUMENTS" not in standup.text
    assert standup.payload["variables"][0]["name"] == "arguments"
    assert standup.note == "Its Claude Code allowed tools are not carried over."


def test_a_transcript_brings_the_conversation_and_leaves_tool_output_behind(noor: Path) -> None:
    """Prompts, replies and each tool call by name — not a tool's output, a subagent's turns,
    Claude Code's own notices, or the summary it writes after compacting."""
    transcript = (
        noor / ".claude" / "projects" / "-Users-noor" / "80aa7bec-27c9-4094-86e2-35fb104eed4f.jsonl"
    )
    base = {"cwd": "/Users/noor", "sessionId": "80aa7bec", "timestamp": "2026-08-11T13:00:00.000Z"}
    extra = [
        {
            **base,
            "type": "user",
            "isCompactSummary": True,
            "message": {"role": "user", "content": "SUMMARY-TEXT"},
        },
        {
            **base,
            "type": "user",
            "isSidechain": True,
            "message": {"role": "user", "content": "SIDECHAIN-TEXT"},
        },
        {
            **base,
            "type": "user",
            "isMeta": True,
            "message": {"role": "user", "content": "META-TEXT"},
        },
        {
            **base,
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "TOOL-OUTPUT export API_TOKEN=abc",
                    }
                ],
            },
        },
    ]
    with transcript.open("a", encoding="utf-8") as handle:
        for line in extra:
            handle.write(json.dumps(line) + "\n")

    item = _items(scan_source("claude_code", asked=PRESS), ImportCategory.CONVERSATIONS)[
        "projects/-Users-noor/80aa7bec-27c9-4094-86e2-35fb104eed4f.jsonl"
    ]
    # The scan names the conversation and counts it; the import reads the messages themselves.
    assert item.payload == {}, "a scan holds no conversation's messages"
    assert item.note == "4 messages. Tool calls come over by name; their output does not."
    from personalclaw.onboarding_import.sources.claude_code import read_for_import

    conversation, _redactions = read_for_import(item)
    body = json.dumps(conversation["messages"])
    for absent in ("SUMMARY-TEXT", "SIDECHAIN-TEXT", "META-TEXT", "TOOL-OUTPUT"):
        assert absent not in body
    roles = [m["role"] for m in conversation["messages"]]
    assert roles == ["user", "tool", "assistant", "user", "tool", "tool", "tool", "assistant"]
    assert conversation["messages"][1]["content"] == "Bash: Find the process listening on 5432"
    assert (
        item.title
        == conversation["title"]
        == "Postgres.app won't start, says port 5432 is already in use. What's holding it?"
    )
    assert item.origin == "Project · ~"
    assert conversation["created_at"] == "2026-08-11T12:52:40.085Z"


def test_a_transcript_line_nested_past_the_recursion_limit_does_not_end_the_scan(
    noor: Path,
) -> None:
    """``json`` raises ``RecursionError``, not ``ValueError``, for a line nested deeper than the
    interpreter recurses. It is one unreadable line, and the scan reads on past it."""
    transcript = (
        noor / ".claude" / "projects" / "-Users-noor" / "80aa7bec-27c9-4094-86e2-35fb104eed4f.jsonl"
    )
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write("[" * 100_000 + "]" * 100_000 + "\n")

    item = _items(scan_source("claude_code", asked=PRESS), ImportCategory.CONVERSATIONS)[
        "projects/-Users-noor/80aa7bec-27c9-4094-86e2-35fb104eed4f.jsonl"
    ]
    assert item.origin == "Project · ~"


def test_import_lands_each_kind_where_personalclaw_reads_it(noor: Path) -> None:
    """Agents on the Agents page, commands as prompts, transcripts in Chat history, memories and
    instructions in memory, servers in ``mcp.json`` — and Claude Code's files untouched."""
    from personalclaw.config.loader import AppConfig, config_dir
    from personalclaw.history import ConversationLog
    from personalclaw.prompt_providers.native_provider import NativePromptProvider

    before = _tree(noor)
    report = run_import([scan_source("claude_code", asked=PRESS)])
    outcomes = {r.key: r.outcome for r in report.results}
    assert WriteOutcome.CONFLICT not in outcomes.values()
    assert WriteOutcome.REJECTED not in outcomes.values(), [
        (r.key, r.detail) for r in report.results if r.outcome is WriteOutcome.REJECTED
    ]

    agents = AppConfig.load().agents
    assert agents["code-reviewer"].system_prompt.startswith("You review diffs like a senior")
    assert agents["code-reviewer"].description.startswith("Reviews a diff for correctness")
    assert agents["code-reviewer"].model == "" and agents["code-reviewer"].approval_mode == ""

    standup = NativePromptProvider().get_prompt("standup")
    assert standup is not None and "{{arguments}}" in standup.content
    assert [v.name for v in standup.variables] == ["arguments"]

    log = ConversationLog()
    keys = {s["key"]: s for s in log.list_sessions()}
    key = "dashboard_claude-code-80aa7bec-27c9-4094-86e2-35fb104eed4f"
    assert keys[key]["title"].startswith("Postgres.app won't start")
    messages = log.read_messages(key)
    assert messages[0]["role"] == "user" and messages[0]["cls"] == "msg msg-u"
    assert messages[1]["cls"] == "msg msg-tool"
    last = datetime.fromisoformat(messages[-1]["ts"].replace("Z", "+00:00")).timestamp()
    assert abs(log._path(key).stat().st_mtime - last) < 1, "listed where it happened, not now"

    servers = json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert {"grafana", "kafka-dev", "feedsmith-dev-db", "playwright", "github"} <= set(servers)
    # The expanded database URL is a value, so it went to the credential store.
    assert _DB_PASSWORD not in json.dumps(servers)

    landed = {r.key: config_dir() / r.destination for r in report.results}
    cause = landed["projects/-Users-noor-src-feedsmith/memory/issue-412-cause.md"]
    assert "html.escape()" in cause.read_text(encoding="utf-8")
    project_rules = landed["project:/Users/noor/src/feedsmith/CLAUDE.md"]
    assert project_rules.read_text(encoding="utf-8") == (
        noor / "src" / "feedsmith" / "CLAUDE.md"
    ).read_text(encoding="utf-8")
    # The instructions are where every conversation reads them; the memory is a memory.
    assert project_rules.parent == config_dir() / "workspace" / "memory" / "instructions" / (
        "claude_code"
    )
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore()
    store.init()
    try:
        memories = [r["text"] for r in store.db.execute("SELECT text FROM episodic_memories")]
    finally:
        store.close()
    header = "#412 cause is double escaping — from Claude Code's memory (Project · ~/src/feedsmith)"
    assert any(m.startswith(f"{header}\n") and "html.escape()" in m for m in memories), memories

    assert _tree(noor) == before, "importing from Claude Code must not change Claude Code"


def test_a_second_import_finds_everything_already_here(noor: Path) -> None:
    run_import([scan_source("claude_code", asked=PRESS)])
    again = run_import([scan_source("claude_code", asked=PRESS)])
    assert {r.outcome for r in again.results} == {WriteOutcome.EXISTING}


def test_planning_every_kind_writes_nothing(noor: Path) -> None:
    """The step asks every item's plan before anything is chosen: agents, prompts and
    conversations included, that must not create a file or a directory in the home."""
    from personalclaw.config.loader import config_dir
    from personalclaw.onboarding_import import plans

    result = scan_source("claude_code", asked=PRESS)
    before = sorted(str(p) for p in config_dir().rglob("*"))
    planned = plans([result])
    assert {plan.state.value for plan in planned.values()} == {"new"}
    assert sorted(str(p) for p in config_dir().rglob("*")) == before


# ── Tools › Import: every scope, imported by the row's id ────────────────────


def _app():
    from personalclaw.dashboard.handlers import mcp as h

    app = web.Application()
    app["state"] = type("State", (), {"_background_tasks": set()})()
    app.router.add_get("/api/mcp/importable", h.api_mcp_importable)
    app.router.add_post("/api/mcp/apply", h.api_mcp_apply)
    return app


@pytest.mark.asyncio
async def test_tools_import_lists_every_scope_and_imports_a_local_server_by_its_row(
    noor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.config.loader import config_dir

    monkeypatch.setattr("personalclaw.agent.rebuild_agent_config", lambda *a, **k: None)
    claude_json = noor / ".claude" / ".claude.json"
    before = claude_json.read_bytes()

    async with TestClient(TestServer(_app())) as client:
        listing = await (await client.get("/api/mcp/importable", params={"look_in": PLACE})).json()
        rows = {row["name"]: row for row in listing["servers"]}
        assert set(rows) == {
            "github",
            "context7",
            "deepwiki",
            "todoist",
            "grafana",
            "kafka-dev",
            "feedsmith-dev-db",
            "playwright",
        }
        assert (rows["grafana"]["scope"], rows["grafana"]["origin"]) == (
            "local",
            "Local scope · /Users/noor/work/carrier-webhooks",
        )
        assert rows["feedsmith-dev-db"]["scope"] == "project"
        for secret in (_GITHUB_PAT, _CONTEXT7_KEY, _GRAFANA_TOKEN, _DB_PASSWORD):
            assert secret not in json.dumps(listing)

        answer = await (
            await client.post(
                "/api/mcp/apply",
                json={
                    "changes": [
                        {
                            "name": "grafana",
                            "personalclaw": True,
                            "from": rows["grafana"]["id"],
                            "place": rows["grafana"]["place"],
                        }
                    ]
                },
            )
        ).json()
        assert "error" not in answer["results"][0], answer

        stale = await (
            await client.post(
                "/api/mcp/apply",
                json={
                    "changes": [
                        {
                            "name": "kafka-dev",
                            "personalclaw": True,
                            "from": "0" * 16,
                            "place": PLACE,
                        }
                    ]
                },
            )
        ).json()
        assert "no longer has" in stale["results"][0]["error"]

        after = await (await client.get("/api/mcp/importable", params={"look_in": PLACE})).json()
        assert "grafana" not in {row["name"] for row in after["servers"]}

    stored = json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert stored["grafana"]["url"] == "https://grafana.cartwheel.internal/api/mcp"
    assert _GRAFANA_TOKEN not in json.dumps(stored), "the header went to the credential store"
    assert "kafka-dev" not in stored
    assert claude_json.read_bytes() == before, "Claude Code's own config is left as it is"


# ── the outbound export honours CLAUDE_CONFIG_DIR ────────────────────────────


def test_the_claude_code_agents_export_suggests_the_directory_claude_code_reads(
    noor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.packs.external_formats import CLAUDE_CODE_AGENTS, default_dest_dir

    assert default_dest_dir(CLAUDE_CODE_AGENTS) == noor / ".claude" / "agents"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(noor / "elsewhere"))
    assert default_dest_dir(CLAUDE_CODE_AGENTS) == noor / "elsewhere" / "agents"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert default_dest_dir(CLAUDE_CODE_AGENTS) == noor / ".claude" / "agents"


def test_a_file_from_a_folder_whose_trust_prompt_was_refused_starts_unticked(noor: Path) -> None:
    """Claude Code asks whether to trust a folder before it uses the folder's own files. A
    project file from a folder where that prompt was answered "no" is listed, with why, unticked."""
    (noor / "CLAUDE.md").write_text("Home-folder notes.\n", encoding="utf-8")
    config = json.loads((noor / ".claude" / ".claude.json").read_text(encoding="utf-8"))
    assert config["projects"]["/Users/noor"]["hasTrustDialogAccepted"] is False  # as recorded

    items = _items(scan_source("claude_code", asked=PRESS), ImportCategory.INSTRUCTIONS)
    home_notes = items["project:/Users/noor/CLAUDE.md"]
    assert home_notes.preselect is False
    assert home_notes.note == "Claude Code's trust prompt for this folder was never accepted."
    # A folder that WAS trusted is ticked as before.
    assert items["project:/Users/noor/src/feedsmith/CLAUDE.md"].preselect is True
