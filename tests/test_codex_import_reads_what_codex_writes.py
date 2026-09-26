"""The Codex importer reads the files Codex actually writes (F-03 Codex half).

Every test runs against ``tests/fixtures/agent_tool_homes/noor/.codex`` — a small copy of a real
``~/.codex`` (Codex CLI 0.139 to 0.152) — copied into ``tmp_path``. ``HOME`` is the copy,
``CODEX_HOME`` its ``.codex``, ``CLAUDE_CONFIG_DIR`` a directory that does not exist and
``PERSONALCLAW_HOME`` a fresh one, so no test reads a developer's ``~/.codex`` or ``~/.claude`` or
writes their ``~/.personalclaw``.

On ``origin/main`` before this change the importer read ``AGENTS.md`` and ``config.toml`` and
nothing else. A remote server came over as SSE, with ``http_headers`` as a key nothing reads and
its bearer variable dropped as though it were a credential; a server Codex had turned off came over
running; ``auth.json`` went uncounted; and skills, agents, prompts, memories, rules and sessions
were not mentioned at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.onboarding_import import ImportCategory, WriteOutcome, run_import, scan_source

FIXTURE = Path(__file__).parent / "fixtures" / "agent_tool_homes" / "noor"

#: Credentials the fixture carries, fake and real-shaped. None may reach the scan's wire form.
_GITHUB_PAT = (
    "github_pat_11BKQ7T3A0x9QmTz4LpW2e_R8vYc3NfK6hJd1Sa5GqUo7XbM2iEw9"
    "PzLt4VnC8rDy0HsJf3AkWm6QeTg1BZx"
)
_DB_PASSWORD = "s3a-Gull-Harbour-42"
#: What the fixture's ``auth.json`` holds: never opened, so none of it can appear anywhere.
_AUTH_VALUES = (
    "fixture-codex-id-token-4d1e9a",
    "fixture-codex-access-token-7f3a2c",
    "fixture-codex-refresh-token-b82e61",
)
#: The value a test gives ``$SENTRY_ACCESS_TOKEN``, the variable Codex reads the sentry token from.
_SENTRY_TOKEN = "fixture-sentry-access-token-5c2e81"

_REVIEW = (
    "sessions/2026/07/22/rollout-2026-07-22T08-52-11-019f89e2-360a-74b5-a65d-4df5c169496e.jsonl"
)
_LOCUST = (
    "sessions/2026/08/20/rollout-2026-08-20T15-10-44-01a02095-36b6-7772-881c-abd3d127359e.jsonl"
)


@pytest.fixture
def noor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixture home, copied, with every root pointed at the copy or at a fresh home."""
    home = tmp_path / "noor"
    shutil.copytree(FIXTURE, home)
    pclaw = tmp_path / "pclaw-home"
    pclaw.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pclaw))
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    monkeypatch.delenv("SENTRY_ACCESS_TOKEN", raising=False)

    from personalclaw.config.loader import config_dir
    from personalclaw.onboarding_import.sources import codex

    assert Path.home() == home, "HOME did not bind"
    assert config_dir() == pclaw, "PERSONALCLAW_HOME did not bind — the real home is at risk"
    assert codex.resolve_root() == home / ".codex", "CODEX_HOME did not bind"
    return home


def _items(result, category: ImportCategory) -> dict[str, object]:
    return {item.key: item for item in result.by_category(category)}


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _everything(result) -> str:
    """Everything a scan hands on: its wire form, and every item's body and payload (which stay
    server-side, for the writers)."""
    return json.dumps(result.to_dict()) + "".join(
        item.text + json.dumps(item.payload) for item in result.items
    )


# ── MCP servers: Codex's meaning kept ─────────────────────────────────────────


def test_a_codex_url_server_is_streamable_http_with_its_headers(noor: Path) -> None:
    """Codex's one remote transport is Streamable HTTP, and ``http_headers`` are its headers. A
    bare ``url`` means SSE to PersonalClaw, so the scan says ``type: "http"`` itself."""
    from personalclaw.mcp_discovery import mcp_transport

    servers = _items(scan_source("codex"), ImportCategory.MCP_SERVERS)

    assert sorted(servers) == ["feedsmith-db", "github", "notes", "openaiDeveloperDocs", "sentry"]
    assert servers["sentry"].payload == {
        "type": "http",
        "url": "https://mcp.sentry.dev/mcp",
        "headers": {"X-Sentry-Org": "cartwheel"},
    }
    assert mcp_transport(servers["openaiDeveloperDocs"].payload) == "http"
    # No Codex-only key is left behind for PersonalClaw to misread or ignore.
    codex_keys = {"http_headers", "bearer_token_env_var", "env_http_headers", "enabled"}
    for item in servers.values():
        assert not codex_keys & set(item.payload), item.payload
    assert servers["feedsmith-db"].note == (
        "Codex's startup_timeout_sec setting for it is not carried over."
    )


def test_the_bearer_token_is_read_from_the_variable_codex_reads(
    noor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``bearer_token_env_var`` names a variable, not a credential: unset, the server says to add
    the token; set, the token is the server's ``Authorization`` header — kept out of the scan's
    wire form, and whole in the definition the MCP writer stores."""
    from personalclaw.onboarding_import.sources import codex

    sentry = _items(scan_source("codex"), ImportCategory.MCP_SERVERS)["sentry"]
    assert sentry.secrets_skipped == 0, "a variable's NAME is not a withheld credential"
    assert sentry.note == (
        "Its token comes from $SENTRY_ACCESS_TOKEN, which the environment PersonalClaw runs in "
        "does not set: add it as an Authorization header on the Tools page after importing."
    )

    monkeypatch.setenv("SENTRY_ACCESS_TOKEN", _SENTRY_TOKEN)
    spec = {s.name: s for s in codex.mcp_servers()}["sentry"].spec
    assert spec["headers"] == {
        "X-Sentry-Org": "cartwheel",
        "Authorization": f"Bearer {_SENTRY_TOKEN}",
    }
    result = scan_source("codex")
    sentry = _items(result, ImportCategory.MCP_SERVERS)["sentry"]
    assert sentry.note == ""
    assert sentry.secrets_skipped == 0, "stored, not left out"
    assert sentry.payload["headers"]["Authorization"] == f"Bearer {_SENTRY_TOKEN}"
    assert _SENTRY_TOKEN not in json.dumps(result.to_dict()) and _SENTRY_TOKEN not in repr(sentry)


def test_a_server_is_never_handed_one_of_personalclaws_own_variables(
    noor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personalclaw.onboarding_import.sources import codex

    config = noor / ".codex" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[mcp_servers.borrower]\nurl = "https://mcp.example.invalid/mcp"\n'
        'bearer_token_env_var = "PERSONALCLAW_LOGIN_PASSWORD"\n'
        'env_http_headers = { "X-Key" = "PERSONALCLAW_APP_SECRET", "X-Team" = "TEAM_ID" }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PERSONALCLAW_LOGIN_PASSWORD", "fixture-gateway-login-password")
    monkeypatch.setenv("PERSONALCLAW_APP_SECRET", "fixture-gateway-app-secret")
    monkeypatch.delenv("TEAM_ID", raising=False)

    borrower = {s.name: s for s in codex.mcp_servers()}["borrower"]
    assert "headers" not in borrower.spec
    assert borrower.note == (
        "It needs $TEAM_ID, which the environment PersonalClaw runs in does not set: add it on "
        "the Tools page after importing. It names $PERSONALCLAW_APP_SECRET and "
        "$PERSONALCLAW_LOGIN_PASSWORD, which are PersonalClaw's own and never handed to another "
        "tool's server."
    )


def test_a_server_codex_turned_off_arrives_turned_off(noor: Path) -> None:
    from personalclaw.config.loader import config_dir
    from personalclaw.mcp_client import McpClientRegistry

    notes = _items(scan_source("codex"), ImportCategory.MCP_SERVERS)["notes"]
    assert notes.payload["disabled"] is True
    assert notes.note == "It is turned off in Codex."
    assert notes.preselect is True, "it comes over as Codex has it: there, and off"

    report = run_import([scan_source("codex")], fingerprints=[notes.fingerprint])
    assert [r.outcome for r in report.results] == [WriteOutcome.IMPORTED]
    stored = json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert stored["notes"]["disabled"] is True
    registry = McpClientRegistry()
    registry.load_from_specs(stored)
    assert [name for name, _conn in registry.items()] == [], "a disabled server is not started"


def _app():
    from personalclaw.dashboard.handlers import mcp as h

    app = web.Application()
    app["state"] = type("State", (), {"_background_tasks": set()})()
    app.router.add_get("/api/mcp/importable", h.api_mcp_importable)
    app.router.add_post("/api/mcp/apply", h.api_mcp_apply)
    return app


@pytest.mark.asyncio
async def test_tools_import_lists_codex_servers_and_stores_the_bearer_token(
    noor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools › Import reads Codex too, through the same reader. The list holds no value; an
    import reads the whole definition server-side and its header values go to the credential
    store — the bearer token included."""
    from personalclaw.config.loader import config_dir
    from personalclaw.config.secret_refs import resolve_mcp_spec

    monkeypatch.setattr("personalclaw.agent.rebuild_agent_config", lambda *a, **k: None)
    monkeypatch.setenv("SENTRY_ACCESS_TOKEN", _SENTRY_TOKEN)
    before = _tree(noor / ".codex")

    async with TestClient(TestServer(_app())) as client:
        listing = await (await client.get("/api/mcp/importable")).json()
        rows = {row["name"]: row for row in listing["servers"]}
        assert set(rows) == {"feedsmith-db", "github", "notes", "openaiDeveloperDocs", "sentry"}
        assert {row["backend"] for row in rows.values()} == {"Codex"}
        assert (rows["sentry"]["transport"], rows["sentry"]["headers"]) == (
            "http",
            [
                {"name": "X-Sentry-Org", "hasValue": True},
                {"name": "Authorization", "hasValue": True},
            ],
        )
        assert rows["notes"]["note"] == "It is turned off in Codex."
        for secret in (_GITHUB_PAT, _DB_PASSWORD, _SENTRY_TOKEN):
            assert secret not in json.dumps(listing)

        answer = await (
            await client.post(
                "/api/mcp/apply",
                json={
                    "changes": [
                        {"name": "sentry", "personalclaw": True, "from": rows["sentry"]["id"]}
                    ]
                },
            )
        ).json()
        assert "error" not in answer["results"][0], answer

    stored = json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    sentry = stored["sentry"]
    assert (sentry["type"], sentry["url"]) == ("http", "https://mcp.sentry.dev/mcp")
    assert _SENTRY_TOKEN not in json.dumps(stored)
    assert all(v.startswith("{{secret:") for v in sentry["headers"].values()), sentry
    resolved = resolve_mcp_spec("sentry", sentry)["headers"]
    assert resolved == {"X-Sentry-Org": "cartwheel", "Authorization": f"Bearer {_SENTRY_TOKEN}"}
    assert _tree(noor / ".codex") == before, "Codex's own files are left as they are"


# ── every kind Codex keeps ────────────────────────────────────────────────────


def test_every_kind_codex_keeps_is_an_item_or_named_as_not_imported(noor: Path) -> None:
    result = scan_source("codex")

    assert {k: v for k, v in result.counts().items() if v} == {
        "instructions": 1,
        "memories": 2,
        "mcp_servers": 5,
        "skills": 1,
        "agents": 2,
        "prompts": 2,
        "conversations": 3,
        "denied_commands": 1,
    }
    assert [entry.to_dict() for entry in result.not_imported] == [
        {
            "what": "Command rules that ask first",
            "count": 3,
            "why": "PersonalClaw has no rule that asks before one particular command.",
        },
        {
            "what": "Codex settings",
            "count": 14,
            "why": "model, model_provider, model_reasoning_effort, approval_policy, sandbox_mode, "
            "notify and 8 more are Codex's own options. PersonalClaw keeps its own in Settings, "
            "so they stay in Codex.",
        },
        {
            "what": "Prompt history",
            "count": 5,
            "why": "PersonalClaw keeps no separate list of past prompts. The prompts in your "
            "conversations come over with them.",
        },
    ]
    # The browser's copy holds no credential. The values a server's `env` sets stay server-side,
    # in that server's definition alone, for the MCP writer to store; the login file is nowhere.
    wire = json.dumps(result.to_dict())
    for secret in (_GITHUB_PAT, _DB_PASSWORD, *_AUTH_VALUES):
        assert secret not in wire
    servers = _items(result, ImportCategory.MCP_SERVERS)
    assert servers["github"].payload["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": _GITHUB_PAT}
    others = [item for item in result.items if item.category is not ImportCategory.MCP_SERVERS]
    for secret in (_GITHUB_PAT, _DB_PASSWORD):
        assert secret not in "".join(item.text + json.dumps(item.payload) for item in others)
    for secret in _AUTH_VALUES:
        assert secret not in _everything(result)


def test_auth_json_is_counted_and_never_opened(noor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Codex login, when Codex keeps it in a file, sits at the root beside ``config.toml``.
    Nothing maps it, and the scan says it was there and left alone."""
    opened: list[str] = []
    real_open = Path.open

    def spy(self: Path, *args, **kwargs):
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    result = scan_source("codex")

    assert "config.toml" in opened, "positive control: the spy sees the scanner's reads"
    assert "auth.json" not in opened
    # The only credential left out is the login file, which belongs to no item.
    assert result.secrets_outside_items() == result.secrets_skipped == 1
    assert result.notes[0] == "1 credential value or file was skipped and not imported."


def test_codex_instructions_prefer_the_override_codex_reads(noor: Path) -> None:
    (noor / ".codex" / "AGENTS.override.md").write_text("Only this week: no refactors.\n")
    items = _items(scan_source("codex"), ImportCategory.INSTRUCTIONS)

    assert items["AGENTS.override.md"].preselect is True
    assert items["AGENTS.md"].preselect is False
    assert items["AGENTS.md"].note == "Codex reads your AGENTS.override.md instead of this file."


def test_codex_memories_are_what_its_memory_built(noor: Path) -> None:
    memories = noor / ".codex" / "memories"
    (memories / "raw_memories.md").write_text("- raw note\n", encoding="utf-8")
    (memories / "rollout_summaries").mkdir()
    (memories / "rollout_summaries" / "019f89e2.md").write_text("summary\n", encoding="utf-8")
    result = scan_source("codex")

    assert sorted(_items(result, ImportCategory.MEMORIES)) == [
        "memories/MEMORY.md",
        "memories/memory_summary.md",
    ]
    working = {n.what: n for n in result.not_imported}["Memory working files"]
    assert working.count == 2


def test_codex_reads_your_skills_from_both_of_its_places(noor: Path) -> None:
    """``$CODEX_HOME/skills`` and ``~/.agents/skills``, the one it reads today — and not the
    skills it ships in ``skills/.system``."""
    agents_skills = noor / ".agents" / "skills" / "release-notes"
    agents_skills.mkdir(parents=True)
    (agents_skills / "SKILL.md").write_text("---\nname: release-notes\ndescription: x\n---\n")
    system = noor / ".codex" / "skills" / ".system" / "skill-creator"
    system.mkdir(parents=True)
    (system / "SKILL.md").write_text("---\nname: skill-creator\ndescription: x\n---\n")

    skills = _items(scan_source("codex"), ImportCategory.SKILLS)
    assert sorted(skills) == ["feedsmith-bench", "release-notes"]
    assert skills["release-notes"].origin == "~/.agents/skills"
    # A Codex home named explicitly is read on its own: another home's skills are not its own.
    explicit = _items(scan_source("codex", noor / ".codex"), ImportCategory.SKILLS)
    assert sorted(explicit) == ["feedsmith-bench"]


def test_codex_agents_become_agent_profiles(noor: Path) -> None:
    from personalclaw.config.loader import AppConfig

    (noor / ".codex" / "agents" / "test_auditor.toml").write_text(
        'name = "test_auditor"\ndescription = "Finds weak tests."\nmodel = "gpt-5.5-codex"\n'
        'sandbox_mode = "read-only"\ndeveloper_instructions = """\nList untested behaviour.\n"""\n',
        encoding="utf-8",
    )
    result = scan_source("codex")
    agents = _items(result, ImportCategory.AGENTS)
    assert sorted(agents) == [
        "agents/reviewer.toml",
        "agents/sql-checker.toml",
        "agents/test_auditor.toml",
    ]
    auditor = agents["agents/test_auditor.toml"]
    assert (auditor.title, auditor.target) == ("test_auditor", "test-auditor")
    assert auditor.note == "Its Codex model and sandbox_mode settings are not carried over."
    assert agents["agents/reviewer.toml"].note == ""

    run_import([result], fingerprints=[a.fingerprint for a in agents.values()])
    profiles = AppConfig.load().agents
    assert profiles["reviewer"].system_prompt.startswith("You are a second reviewer after Claude")
    assert profiles["reviewer"].description == (
        "Second-opinion code review focused on failure modes. Reads, never edits."
    )
    assert profiles["sql-checker"].source == "local"
    assert profiles["test-auditor"].system_prompt == "List untested behaviour.\n"


def test_codex_prompts_keep_their_placeholders_as_variables(noor: Path) -> None:
    from personalclaw.prompt_providers.native_provider import NativePromptProvider

    result = scan_source("codex")
    prompts = _items(result, ImportCategory.PROMPTS)
    triage = prompts["prompts/triage-issue.md"]
    assert (triage.title, triage.target) == ("/prompts:triage-issue", "triage-issue")
    assert "Triage feedsmith issue {{issue}}." in triage.text and "$ISSUE" not in triage.text
    assert triage.payload["variables"] == [
        {"name": "issue", "type": "text", "description": "number", "required": True}
    ]

    run_import([result], fingerprints=[p.fingerprint for p in prompts.values()])
    stored = NativePromptProvider().get_prompt("changelog")
    assert stored is not None and "since {{since}}." in stored.content
    assert [(v.name, v.required) for v in stored.variables] == [("since", True)]


@pytest.mark.parametrize(
    "body, hint, content, variables",
    [
        (
            "Triage $ISSUE in $REPO. Not $$HOME.",
            "ISSUE=<number> REPO=<name>",
            "Triage {{issue}} in {{repo}}. Not $$HOME.",
            [
                {"name": "issue", "type": "text", "description": "number", "required": True},
                {"name": "repo", "type": "text", "description": "name", "required": True},
            ],
        ),
        (
            "Fix $1, then $2. Everything: $ARGUMENTS. Price: $$5 and $ alone.",
            "",
            "Fix {{arg1}}, then {{arg2}}. Everything: {{arguments}}. Price: $$5 and $ alone.",
            [
                {
                    "name": "arguments",
                    "type": "textarea",
                    "description": "What the prompt was given after its name.",
                },
                {"name": "arg1", "type": "text", "description": ""},
                {"name": "arg2", "type": "text", "description": ""},
            ],
        ),
        # A prompt with a named placeholder takes named values only, as it did in Codex.
        (
            "Open $FILE; $1 stays.",
            "",
            "Open {{file}}; $1 stays.",
            [{"name": "file", "type": "text", "description": "", "required": True}],
        ),
        ("No placeholders here.", "", "No placeholders here.", []),
    ],
)
def test_prompt_placeholders_follow_codexs_rule(body, hint, content, variables) -> None:
    from personalclaw.onboarding_import.sources.codex import prompt_template

    assert prompt_template(body, argument_hint=hint) == (content, variables)


# ── rules: a command Codex refuses is one PersonalClaw refuses ────────────────


def test_a_command_codex_refuses_is_one_personalclaw_refuses(noor: Path) -> None:
    from personalclaw.config.loader import AppConfig
    from personalclaw.security import denied_command_reason

    result = scan_source("codex")
    denied = result.by_category(ImportCategory.DENIED_COMMANDS)
    assert [(i.title, i.payload, i.note) for i in denied] == [
        ("rm -rf", {"pattern": r"\brm\s+-rf\b"}, "Codex's reason: Delete specific paths instead.")
    ]
    assert denied_command_reason("rm -rf build") is None, "precondition: not refused yet"

    report = run_import([result], fingerprints=[denied[0].fingerprint])
    assert [r.outcome for r in report.results] == [WriteOutcome.IMPORTED]
    assert AppConfig.load().security.denied_commands == [r"\brm\s+-rf\b"]
    for refused in ("rm -rf build", "bash -lc 'rm -rf build'", "cd x && rm -rf ."):
        assert denied_command_reason(refused) == r"\brm\s+-rf\b", refused
    for allowed in ("rm -rfv build", "farm -rf", "git push origin main"):
        assert denied_command_reason(allowed) != r"\brm\s+-rf\b", allowed

    again = run_import([scan_source("codex")], fingerprints=[denied[0].fingerprint])
    assert [r.outcome for r in again.results] == [WriteOutcome.EXISTING]


def test_a_rules_file_is_read_as_data_and_never_run(tmp_path: Path) -> None:
    from personalclaw.onboarding_import.sources.codex import parse_rules
    from personalclaw.onboarding_import.sources.common import denied_pattern

    marker = tmp_path / "ran"
    source = (
        'prefix_rule(pattern = ["git", ["push", "fetch"]], decision = "forbidden")\n'
        'prefix_rule(pattern = ["ls"])\n'
        'prefix_rule(["curl"], decision = "prompt", justification = "Network.")\n'
        'GIT = ["git"]\n'
        'prefix_rule(pattern = GIT + ["reset"], decision = "forbidden")\n'
        'prefix_rule(pattern = [], decision = "forbidden")\n'
        'prefix_rule(pattern = ["x"], decision = "maybe")\n'
        f'__import__("pathlib").Path({str(marker)!r}).write_text("ran")\n'
    )
    rules, unreadable = parse_rules(source)

    assert [(r.pattern, r.decision, r.justification) for r in rules] == [
        ((("git",), ("push", "fetch")), "forbidden", ""),
        ((("ls",),), "allow", ""),  # Codex's default decision
        ((("curl",),), "prompt", "Network."),
    ]
    assert unreadable == 3
    assert not marker.exists(), "a rules file is parsed, never executed"
    assert parse_rules("prefix_rule(") == ([], 1)

    pattern = denied_pattern(rules[0].pattern)
    assert pattern == r"\bgit\s+(?:push|fetch)\b"
    assert re.search(pattern, "git fetch origin") and not re.search(pattern, "git pull")
    assert denied_pattern((("./deploy.sh",), ("--prod",))) == r"(?<!\S)\./deploy\.sh\s+--prod\b"


def test_rules_that_ask_or_allow_are_named_not_imported(noor: Path) -> None:
    (noor / ".codex" / "rules" / "tui.rules").write_text(
        'prefix_rule(pattern = ["npm", "test"], decision = "allow")\n', encoding="utf-8"
    )
    result = scan_source("codex")
    left = {n.what: n.count for n in result.not_imported}
    assert left["Command rules that ask first"] == 3
    assert left["Command rules that allow without asking"] == 1
    assert len(result.by_category(ImportCategory.DENIED_COMMANDS)) == 1


# ── sessions → conversations ──────────────────────────────────────────────────


def test_a_codex_session_is_a_conversation_with_its_tool_calls_by_name(noor: Path) -> None:
    """Each prompt, each reply and each tool call by name — not a call's output, the model's
    reasoning, or the context Codex puts around a prompt. Titled as Codex lists it."""
    conversations = _items(scan_source("codex"), ImportCategory.CONVERSATIONS)

    assert sorted(c.title for c in conversations.values()) == [
        "Locust load test for DHL webhook",
        "Review async fetcher diff",
        "Verify eta_confidence backfill SQL",
    ]
    review = conversations[_REVIEW]
    messages = review.payload["messages"]
    assert [m["role"] for m in messages] == ["user", "tool", "tool", "assistant"]
    assert messages[1]["content"] == "exec_command: git diff main...feat/async-fetch --stat"
    assert messages[0]["content"].startswith("Review the diff on feat/async-fetch against main.")
    assert review.origin == "Project · ~/src/feedsmith"
    assert review.target == "019f89e2-360a-74b5-a65d-4df5c169496e"
    assert (review.payload["created_at"], review.payload["updated_at"]) == (
        "2026-07-22T12:52:11.402Z",
        "2026-07-22T12:52:41.634Z",
    )
    assert review.note == "2 messages. Tool calls come over by name; their output does not."
    locust = [m["content"] for m in conversations[_LOCUST].payload["messages"]]
    assert "apply_patch: Add File: loadtest/locustfile.py" in locust
    body = json.dumps([c.payload for c in conversations.values()])
    for absent in (
        "Chunk ID",
        "Process exited with code",
        "Success. Updated the following files",
        "gAAAAAB",
        "Inspecting the async fetcher diff",
    ):
        assert absent not in body, absent


def test_a_session_without_typed_prompt_events_reads_its_model_input(tmp_path: Path) -> None:
    """A session file with no ``user_message`` events still has its prompts in the model's input,
    where Codex's own context sits beside them: the context is left out."""
    from personalclaw.onboarding_import.sources.codex import read_rollout

    lines = [
        {
            "timestamp": "2026-09-01T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": "01a0aaaa-0000-7000-8000-000000000001", "cwd": "/w"},
        },
        {
            "timestamp": "2026-09-01T10:00:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<environment_context>\n<cwd>/w</cwd>\n</environment_context>",
                    }
                ],
            },
        },
        {
            "timestamp": "2026-09-01T10:00:02.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Why is CI red?"}],
            },
        },
        {
            "timestamp": "2026-09-01T10:00:09.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "A flaky test."}],
            },
        },
    ]
    path = tmp_path / "rollout-2026-09-01T10-00-00-01a0aaaa-0000-7000-8000-000000000001.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    conversation, session, _redactions = read_rollout(path, {})
    assert session == "01a0aaaa-0000-7000-8000-000000000001"
    assert [(m["role"], m["content"]) for m in conversation["messages"]] == [
        ("user", "Why is CI red?"),
        ("assistant", "A flaky test."),
    ]
    assert conversation["title"] == "Why is CI red?"


def test_archived_and_compressed_sessions_are_counted(noor: Path) -> None:
    sessions = noor / ".codex" / "sessions" / "2026" / "06" / "30"
    sessions.mkdir(parents=True)
    (
        sessions / "rollout-2026-06-30T09-00-00-01a00000-0000-7000-8000-000000000002.jsonl.zst"
    ).write_bytes(b"\x28\xb5\x2f\xfd")
    archived = noor / ".codex" / "archived_sessions"
    archived.mkdir()
    (
        archived / "rollout-2026-06-01T09-00-00-01a00000-0000-7000-8000-000000000003.jsonl"
    ).write_text("{}\n", encoding="utf-8")
    result = scan_source("codex")
    left = {n.what: n.count for n in result.not_imported}
    assert (left["Compressed conversations"], left["Archived conversations"]) == (1, 1)
    assert len(result.by_category(ImportCategory.CONVERSATIONS)) == 3


# ── the import, end to end ────────────────────────────────────────────────────


def test_import_lands_each_codex_kind_where_personalclaw_reads_it(noor: Path) -> None:
    from personalclaw.config.loader import config_dir
    from personalclaw.history import ConversationLog

    before = _tree(noor / ".codex")
    report = run_import([scan_source("codex")])
    outcomes = {r.key: r.outcome for r in report.results}
    assert WriteOutcome.CONFLICT not in outcomes.values()

    log = ConversationLog()
    sessions = {s["key"]: s for s in log.list_sessions()}
    key = "dashboard_codex-019f89e2-360a-74b5-a65d-4df5c169496e"
    assert sessions[key]["title"] == "Review async fetcher diff"
    messages = log.read_messages(key)
    assert [m["cls"] for m in messages] == [
        "msg msg-u",
        "msg msg-tool",
        "msg msg-tool",
        "msg msg-a",
    ]
    last = datetime.fromisoformat(messages[-1]["ts"].replace("Z", "+00:00")).timestamp()
    assert abs(log._path(key).stat().st_mtime - last) < 1, "listed where it happened, not now"

    servers = json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["openaiDeveloperDocs"] == {
        "type": "http",
        "url": "https://developers.openai.com/mcp",
    }
    assert _DB_PASSWORD not in json.dumps(servers), "the database URL went to the credential store"

    landed = {r.key: config_dir() / r.destination for r in report.results}
    assert "Tomás reviews the wording" in landed["memories/MEMORY.md"].read_text(encoding="utf-8")
    # Nothing Codex's login holds reached the home — through any destination.
    for path in config_dir().rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            for value in _AUTH_VALUES:
                assert value.encode() not in data, path
    assert _tree(noor / ".codex") == before, "importing from Codex must not change Codex"


def test_a_second_codex_import_finds_everything_already_here(noor: Path) -> None:
    first = run_import([scan_source("codex")])
    imported = {r.key for r in first.results if r.outcome is WriteOutcome.IMPORTED}
    again = run_import([scan_source("codex")])
    assert {r.outcome for r in again.results if r.key in imported} == {WriteOutcome.EXISTING}


def test_the_codex_environment_is_this_tests_own(noor: Path) -> None:
    """Guard for every test above: the scan reads the copy, never a developer's ``~/.codex``."""
    result = scan_source("codex")
    assert result.root == str(noor / ".codex")
    assert os.environ["CODEX_HOME"] == str(noor / ".codex")
