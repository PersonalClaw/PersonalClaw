"""Bringing your setup over has one secret policy, whichever way it is brought.

A credential another tool holds is stored as a reference in the credential store, or it is left
out and counted. It never lands in memory, in a file nothing reads, or in plaintext config. The
onboarding step and the Tools page's Import apply that one policy, through the one MCP writer and
the platform's one credential detector (``security.redact_credentials``).

The end-to-end tests run against ``tests/fixtures/agent_tool_homes/noor`` (a copy of a real
``~/.claude`` and ``~/.codex``), copied into ``tmp_path``: ``HOME`` is the copy,
``CLAUDE_CONFIG_DIR`` and ``CODEX_HOME`` point into it, and ``PERSONALCLAW_HOME`` is a fresh
directory, so no test reads a developer's own tools or writes their ``~/.personalclaw``.

On ``origin/main`` before this change the two paths disagreed: the onboarding step dropped every
``env`` or header value whose NAME looked secret, so the user typed each key in again, while the
Tools page's Import stored it. Its name test also counted ``includeCoAuthoredBy`` (a boolean) as a
withheld credential. Claude Code's ``settings.json`` was copied into ``onboarding/staged/``, which
nothing reads, with the database password inside a URL-valued ``env`` entry in it. And the
detector missed two shapes a real setup holds: an incoming-webhook URL, so the Slack webhook in
the fixture's ``CLAUDE.md`` reached memory whole, and a fine-grained GitHub token
(``github_pat_…``).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.onboarding_import import ImportCategory, WriteOutcome, run_import, scan_source
from personalclaw.security import redact_credentials

FIXTURE = Path(__file__).parent / "fixtures" / "agent_tool_homes" / "noor"

#: The owner's press, Look in Claude Code and Codex: the two tools' setups, which a scan
#: reads on this machine only for a request that names them.
LOOK = ["setup:claude_code", "setup:codex"]
PRESS = frozenset(LOOK)

#: The Slack incoming webhook the fixture's CLAUDE.md posts to. Fake, but shaped exactly like a
#: live one, so the committed fixture carries a marker instead (the forge's push protection
#: refuses a commit that holds one). Assembled here and planted into the copy.
_SLACK_PATH = "services/T0EXAMPLE01/B0EXAMPLE01/9Xq2LmVb7TzR4wKpN8sYc1Df"
SLACK_WEBHOOK = "https://hooks.slack.com/" + _SLACK_PATH

#: Credentials the fixture carries, fake and real-shaped.
_GITHUB_PAT = "github_pat_11FIXTURE0NOT0A0REAL0TOKEN0000_forTestsOnly"
_CONTEXT7_KEY = "ctx7sk-9b2e4f61-7a3c-4d8e-b5f0-1c6a2d9e3f47"
_GRAFANA_TOKEN = "glsa_Q3v8Tn2Lk9Wx4Rb7Zc1Ym6Hd0Fp5Js_2e7a9c1b"
#: Inside ``FEEDSMITH_DEV_DATABASE_URL`` in Claude Code's ``settings.json`` ``env``: a name that
#: says nothing, around a value that is a credential.
_DB_PASSWORD = "s3a-Gull-Harbour-42"
_SENTRY_AUTH_TOKEN = "sntryu_4f0c9a2e7b1d4c8e9a6f3b2d1c0e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0"


@pytest.fixture
def noor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fixture home, copied and planted, with every root pointed at the copy or a fresh home."""
    home = tmp_path / "noor"
    shutil.copytree(FIXTURE, home)
    notes = home / ".claude" / "CLAUDE.md"
    notes.write_text(
        notes.read_text(encoding="utf-8").replace("{{SLACK_WEBHOOK_URL}}", SLACK_WEBHOOK),
        encoding="utf-8",
    )
    pclaw = tmp_path / "pclaw-home"
    pclaw.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pclaw))
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    monkeypatch.delenv("SENTRY_ACCESS_TOKEN", raising=False)

    from personalclaw.config.loader import config_dir
    from personalclaw.onboarding_import.sources import claude_code, codex

    assert SLACK_WEBHOOK in notes.read_text(encoding="utf-8")
    assert Path.home() == home, "HOME did not bind"
    assert config_dir() == pclaw, "PERSONALCLAW_HOME did not bind — the real home is at risk"
    assert claude_code.resolve_root() == home / ".claude", "CLAUDE_CONFIG_DIR did not bind"
    assert codex.resolve_root() == home / ".codex", "CODEX_HOME did not bind"
    return home


def _store() -> bytes:
    """The credential store's file: ``.env`` in the home (the keychain is off by default)."""
    from personalclaw.config.loader import env_path

    path = env_path()
    return path.read_bytes() if path.is_file() else b""


def _outside_the_store() -> bytes:
    """Every byte under the PersonalClaw home except the credential store's."""
    from personalclaw.config.loader import config_dir, env_path

    store = env_path()
    return b"".join(
        p.read_bytes() for p in sorted(config_dir().rglob("*")) if p.is_file() and p != store
    )


# ── the detector: the shapes it missed ────────────────────────────────────────


@pytest.mark.parametrize(
    "url, kept",
    [
        (SLACK_WEBHOOK, "https://hooks.slack.com/"),
        (
            "https://discord.com/api/webhooks/"
            + "1000000000000000001/Zx9_aQ-7bLkP2mN4oR6sT8uV0wY1zA3cE5gI7kM9oQ1sU3wY5",
            "https://discord.com/api/webhooks/",
        ),
        (
            "https://discordapp.com/api/webhooks/" + "1000000000000000001/aB3dE5fG7hJ9kL1mN3pQ5rS7",
            "https://discordapp.com/api/webhooks/",
        ),
        (
            "https://cartwheel.webhook.office.com/webhookb2/"
            + "7f3c2a1e-4b5d-4c6e-8f9a-0b1c2d3e4f5a@2a3b4c5d-6e7f-8a9b-0c1d-2e3f4a5b6c7d"
            + "/IncomingWebhook/9d8c7b6a5f4e3d2c1b0a/1e2d3c4b-5a6f-7e8d-9c0b-1a2b3c4d5e6f",
            "https://cartwheel.webhook.office.com/webhookb2/",
        ),
        (
            "https://hooks.zapier.com/" + "hooks/catch/1234567/3kd8f2a/",
            "https://hooks.zapier.com/",
        ),
    ],
    ids=["slack", "discord", "discordapp", "teams", "zapier"],
)
def test_an_incoming_webhook_url_is_a_credential_and_its_service_stays_named(
    url: str, kept: str
) -> None:
    """Whoever has an incoming webhook's URL can post as you: the secret is the path. The host
    stays, so a redacted note still says where it posted."""
    text = f"curl -s -X POST -H 'Content-type: application/json' --data '{{}}' {url}\n"
    cleaned, warnings = redact_credentials(text)

    assert url[len(kept) :] not in cleaned
    assert f"{kept}[REDACTED: webhook]" in cleaned
    assert len(warnings) == 1
    assert redact_credentials(cleaned) == (cleaned, []), "a redacted webhook stays as it is"


@pytest.mark.parametrize(
    "text",
    [
        "Incoming webhooks are documented at https://api.slack.com/messaging/webhooks",
        "The team is on https://discord.com/channels/1000000000000000001/1000000000000000999",
        "Post to https://hooks.slack.com/ once the app is installed.",
        "See https://github.com/cartwheel/carrier-webhooks/pull/412 for the fix.",
        "webhook_url is set on the Channels page",
    ],
)
def test_a_url_that_only_mentions_a_webhook_is_left_as_it_is(text: str) -> None:
    assert redact_credentials(text) == (text, [])


def test_a_fine_grained_github_token_is_a_credential() -> None:
    """``github_pat_…`` is GitHub's fine-grained token. The classic ``ghp_`` rule cannot see it,
    and ``GH_TOKEN`` is not a name the assignment rule knows."""
    cleaned, warnings = redact_credentials(f"export GH_TOKEN={_GITHUB_PAT}\n")
    assert _GITHUB_PAT not in cleaned and "github_pat_" not in cleaned
    assert warnings


# ── what the onboarding step brings over ──────────────────────────────────────


def test_a_slack_webhook_in_claude_md_never_reaches_memory(noor: Path) -> None:
    from personalclaw.config.loader import config_dir

    result = scan_source("claude_code", asked=PRESS)
    notes = {item.key: item for item in result.items}["CLAUDE.md"]
    assert _SLACK_PATH not in notes.text
    assert "https://hooks.slack.com/[REDACTED: webhook]" in notes.text
    # The webhook, and the password in the scratch database URL beside it: counted, on its row.
    assert notes.redactions == 2

    report = run_import([result], fingerprints=[notes.fingerprint])
    assert [r.outcome for r in report.results] == [WriteOutcome.IMPORTED]
    doc = config_dir() / report.results[0].destination
    assert "https://hooks.slack.com/[REDACTED: webhook]" in doc.read_text(encoding="utf-8")
    assert _SLACK_PATH.encode() not in _outside_the_store() + _store()


def test_a_fine_grained_token_pasted_into_a_memory_is_redacted(noor: Path) -> None:
    topic = (
        noor / ".claude" / "projects" / "-Users-noor-src-feedsmith" / "memory" / "maintainers.md"
    )
    topic.write_text(
        topic.read_text(encoding="utf-8") + f"\nThe release job's token is {_GITHUB_PAT}.\n",
        encoding="utf-8",
    )
    memories = {
        i.key: i
        for i in scan_source("claude_code", asked=PRESS).by_category(ImportCategory.MEMORIES)
    }
    maintainers = memories["projects/-Users-noor-src-feedsmith/memory/maintainers.md"]
    assert _GITHUB_PAT not in maintainers.text
    assert maintainers.redactions == 1


def test_a_password_inside_a_database_url_goes_only_to_the_credential_store(noor: Path) -> None:
    """``FEEDSMITH_DEV_DATABASE_URL`` is in Claude Code's settings ``env``, and the project's
    ``.mcp.json`` expands it into the dev database server's ``DATABASE_URI``. The server's value
    is stored as a reference; the settings themselves are never copied, so the password is in
    the credential store and nowhere else — and the Sentry token beside it is nowhere at all."""
    from personalclaw.config.loader import config_dir

    report = run_import([scan_source("claude_code", asked=PRESS)])
    assert WriteOutcome.REJECTED not in {r.outcome for r in report.results}

    assert not (config_dir() / "onboarding" / "staged").exists()
    assert _DB_PASSWORD.encode() not in _outside_the_store()
    assert _DB_PASSWORD.encode() in _store(), "positive control: the server's DATABASE_URI"
    assert _SENTRY_AUTH_TOKEN.encode() not in _outside_the_store() + _store()


def test_claude_codes_own_options_are_named_and_none_is_counted_as_a_credential(
    noor: Path,
) -> None:
    """Nothing in the fixture's Claude Code home is left out: it has no credential file, and every
    value its MCP servers set is stored. A boolean named ``includeCoAuthoredBy`` is an option."""
    result = scan_source("claude_code", asked=PRESS)

    assert result.secrets_skipped == 0
    settings = {n.what: n for n in result.not_imported}["Claude Code settings"]
    assert settings.count == 8
    assert settings.why == (
        "model, env, hooks, statusLine, includeCoAuthoredBy, cleanupPeriodDays and 2 more are "
        "Claude Code's own options. PersonalClaw keeps its own in Settings, so they stay in "
        "Claude Code."
    )
    assert result.notes == ["2 credential-like strings were redacted from imported text."]


def test_a_command_claude_code_refuses_is_one_personalclaw_refuses(noor: Path) -> None:
    """``permissions.deny`` → ``Bash(rm -rf:*)`` is a command Claude Code refuses. It comes over
    as one the shell denylist refuses, as Codex's ``forbidden`` rules do."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.security import denied_command, denied_command_patterns

    force, rm = r"\bgit\s+push\s+--force\b", r"\brm\s+-rf\b"
    result = scan_source("claude_code", asked=PRESS)
    denied = {i.title: i for i in result.by_category(ImportCategory.DENIED_COMMANDS)}
    assert {title: i.payload["pattern"] for title, i in denied.items()} == {
        "git push --force": force,
        "rm -rf": rm,
    }
    assert denied_command("rm -rf build") is None, "precondition: not refused yet"

    report = run_import([result], fingerprints=[i.fingerprint for i in denied.values()])
    assert {r.outcome for r in report.results} == {WriteOutcome.IMPORTED}
    assert AppConfig.load().security.denied_commands == [force, rm]
    assert {force, rm} <= set(denied_command_patterns())
    for command in ("rm -rf build", "bash -lc 'rm -rf build'", "cd x && rm -rf ."):
        assert getattr(denied_command(command), "pattern", None) == rm, command
    for command in ("rm -rfv build", "farm -rf", "git status"):
        assert denied_command(command) is None, command


def _deny(noor: Path, *rules: str) -> None:
    """Add ``rules`` to the copy's ``permissions.deny``."""
    path = noor / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["permissions"]["deny"].extend(rules)
    path.write_text(json.dumps(settings), encoding="utf-8")


def test_a_refused_command_that_holds_a_credential_is_left_out_and_counted(noor: Path) -> None:
    """Its pattern would carry the credential into ``config.json``, so it is not an item."""
    token = "sk-ant-api03-" + "Q" * 40
    _deny(noor, f"Bash(curl -H 'x-api-key: {token}' https://api.example.com:*)")

    result = scan_source("claude_code", asked=PRESS)
    titles = [i.title for i in result.by_category(ImportCategory.DENIED_COMMANDS)]
    assert titles == ["git push --force", "rm -rf"]
    assert result.secrets_skipped == 1
    assert token not in json.dumps(result.to_dict())

    run_import([result])
    assert token.encode() not in _outside_the_store() + _store()


def test_a_rule_this_import_cannot_turn_into_a_command_is_named(noor: Path) -> None:
    """A wildcard inside the command, or ``Bash`` alone, matches more than the words a command
    starts with; a rule about files or tools is not about commands at all."""
    _deny(noor, "Bash(git * main)", "Bash", "WebFetch(domain:pastebin.com)")

    left = {n.what: n for n in scan_source("claude_code", asked=PRESS).not_imported}
    assert left["Refused commands with wildcards"].count == 2
    assert left["Refused commands with wildcards"].why == (
        "A refused command comes over as the words it starts with. These rules use a wildcard "
        "instead, so they stay in Claude Code."
    )
    assert left["Other permission rules"].count == 17


def test_no_import_stages_a_file_nothing_reads(noor: Path) -> None:
    """Another tool's own options have no destination here, so there is no category for them and
    no file set aside for a review nothing offers."""
    from personalclaw.config.loader import config_dir

    run_import([scan_source("claude_code", asked=PRESS), scan_source("codex", asked=PRESS)])
    assert not (config_dir() / "onboarding" / "staged").exists()
    assert "settings" not in {category.value for category in ImportCategory}


# ── one policy on both paths ──────────────────────────────────────────────────


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import mcp as h

    app = web.Application()
    app["state"] = type("State", (), {"_background_tasks": set()})()
    app.router.add_get("/api/mcp/importable", h.api_mcp_importable)
    app.router.add_post("/api/mcp/apply", h.api_mcp_apply)
    return app


def _stored_servers() -> dict[str, dict]:
    from personalclaw.config.loader import config_dir

    return json.loads((config_dir() / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]


@pytest.mark.asyncio
async def test_the_onboarding_step_stores_a_server_the_way_tools_import_does(
    noor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same three servers, brought over each way into a home of their own: the same entry in
    ``mcp.json``, the same values behind its references, and no value in either file."""
    from personalclaw.config.secret_refs import resolve_mcp_spec

    monkeypatch.setattr("personalclaw.agent.rebuild_agent_config", lambda *a, **k: None)
    names = ("github", "context7", "grafana")

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "noor-tools-import"))
    async with TestClient(TestServer(_app())) as client:
        query = [("look_in", place) for place in LOOK]
        listing = await (await client.get("/api/mcp/importable", params=query)).json()
        # Codex has a `github` server too: the rows are Claude Code's, as the scan's items are.
        rows = {row["name"]: row for row in listing["servers"] if row["backend"] == "Claude Code"}
        changes = [
            {"name": n, "personalclaw": True, "from": rows[n]["id"], "place": rows[n]["place"]}
            for n in names
        ]
        answer = await (await client.post("/api/mcp/apply", json={"changes": changes})).json()
        assert all("error" not in row for row in answer["results"]), answer
    by_tools = {n: _stored_servers()[n] for n in names}
    resolved_by_tools = {n: resolve_mcp_spec(n, spec) for n, spec in by_tools.items()}

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "noor-onboarding"))
    result = scan_source("claude_code", asked=PRESS)
    picks = [
        i.fingerprint for i in result.by_category(ImportCategory.MCP_SERVERS) if i.target in names
    ]
    report = run_import([result], fingerprints=picks)
    assert [r.outcome for r in report.results] == [WriteOutcome.IMPORTED] * 3
    by_onboarding = {n: _stored_servers()[n] for n in names}

    assert by_onboarding == by_tools, "one writer, one entry"
    assert {n: resolve_mcp_spec(n, s) for n, s in by_onboarding.items()} == resolved_by_tools
    assert resolved_by_tools["github"]["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": _GITHUB_PAT}
    assert resolved_by_tools["grafana"]["headers"] == {"Authorization": f"Bearer {_GRAFANA_TOKEN}"}
    assert resolved_by_tools["context7"]["headers"] == {"CONTEXT7_API_KEY": _CONTEXT7_KEY}
    stored = json.dumps([by_tools, by_onboarding])
    for secret in (_GITHUB_PAT, _CONTEXT7_KEY, _GRAFANA_TOKEN):
        assert secret not in stored
