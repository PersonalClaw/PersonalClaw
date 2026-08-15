"""Onboarding import — scanners, writers, and the three floors that define it (PEP-4).

Every test drives a FIXTURE foreign root under ``tmp_path`` and a FIXTURE PersonalClaw
home bound through ``PERSONALCLAW_HOME``: no test reads the developer's real ``~/.claude``
or writes their real ``~/.personalclaw``. ``test_root_resolution_prefers_env_var`` is the
proof that the resolver a production call uses is the one under test here.

The load-bearing tests, one per property the atom names:

* ``test_planted_secret_appears_nowhere_in_scan_output`` /
  ``test_planted_secret_never_reaches_the_home`` — secrets are counted and skipped. The
  second walks every byte written under the home, so a secret arriving through any
  destination (memory doc, memory record, mcp.json, staged settings, a skill file, the
  SEL audit log) fails it.
* ``test_rescan_is_idempotent`` / ``test_reimport_reports_existing_and_writes_nothing`` —
  counts, not just success: a scan that duplicated items or an import that rewrote a
  destination fails on the count/bytes, not on an exception.
* ``test_conflicting_*`` — the three destinations where a foreign item can collide with
  the user's own state each report ``conflict`` and leave the existing thing byte-identical.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from personalclaw.onboarding_import import (
    ImportCategory,
    WriteOutcome,
    fingerprint_of,
    get_source,
    list_sources,
    run_import,
    scan_source,
)
from personalclaw.onboarding_import.sources import claude_code, codex
from personalclaw.onboarding_import.writers import _WRITERS, mcp_config_path, staged_settings_path

#: The planted credential. If this string reaches ANY output — an item, a note, a log, a
#: file under the home — a test fails. Shaped like a real key so the redactors engage.
SECRET = "sk-ant-api03-PLANTEDSECRETVALUE000000000000000000000000000000AA"
SECRET2 = "ghp_PLANTEDGITHUBTOKENVALUE0000000000000"

_SKILL_MD = "---\nname: {name}\ndescription: {desc}\n---\n# {name}\nSteps.\n"


# ── fixtures ──────────────────────────────────────────────────────────────────


def _seed_claude_root(root: Path) -> None:
    """A fixture ``~/.claude``: instructions, memories, MCP, skills, settings — plus a
    credential in every place a real one shows up."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text(
        "# House rules\n\n- Always run the linter.\n"
        f"- The staging key is {SECRET} (do not share).\n",
        encoding="utf-8",
    )
    (root / "memories").mkdir()
    (root / "memories" / "prefs.md").write_text("User prefers concise answers.\n", encoding="utf-8")
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "weather": {
                        "command": "npx",
                        "args": ["-y", "weather-mcp"],
                        "env": {"WEATHER_API_KEY": SECRET, "REGION": "eu"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "settings.json").write_text(
        json.dumps({"theme": "dark", "apiKeyHelper": SECRET2, "verbose": True}),
        encoding="utf-8",
    )
    # A credential FILE: refused unread, counted, never opened.
    (root / ".credentials.json").write_text(json.dumps({"accessToken": SECRET}), encoding="utf-8")
    skill = root / "skills" / "tidy-notes"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        _SKILL_MD.format(name="tidy-notes", desc="Tidy up meeting notes"), encoding="utf-8"
    )
    (skill / "reference.md").write_text("Longer notes.\n", encoding="utf-8")
    # A credential file INSIDE the skill: counted at scan, never installed.
    (skill / ".env").write_text(f"TOKEN={SECRET2}\n", encoding="utf-8")


@pytest.fixture
def claude_root(tmp_path: Path) -> Path:
    root = tmp_path / "foreign" / ".claude"
    _seed_claude_root(root)
    return root


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated PersonalClaw home. Bound through the env var, which every store
    reads live — the robust lever (see ``tests/conftest.py``)."""
    h = tmp_path / "pclaw-home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    return h


def _tree(root: Path) -> dict[str, str]:
    """path → sha256, for byte-identity assertions."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[str(path.relative_to(root))] = digest
    return out


def _all_bytes(root: Path) -> bytes:
    chunks = [p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()]
    return b"".join(chunks)


# ── scan ──────────────────────────────────────────────────────────────────────


def test_scan_yields_instruction_mcp_and_skill_items(claude_root: Path) -> None:
    result = scan_source("claude_code", claude_root)

    assert result.present is True
    counts = result.counts()
    assert counts[ImportCategory.INSTRUCTIONS.value] == 1
    assert counts[ImportCategory.MEMORIES.value] == 1
    assert counts[ImportCategory.MCP_SERVERS.value] == 1
    assert counts[ImportCategory.SKILLS.value] == 1
    assert counts[ImportCategory.SETTINGS.value] == 1

    mcp = result.by_category(ImportCategory.MCP_SERVERS)[0]
    assert mcp.key == "weather"
    # The benign env survives; the secret-named key is gone entirely (not blanked).
    assert mcp.payload["env"] == {"REGION": "eu"}
    skill = result.by_category(ImportCategory.SKILLS)[0]
    assert Path(skill.path).name == "tidy-notes"


def test_missing_root_is_absent_not_an_error(tmp_path: Path) -> None:
    result = scan_source("claude_code", tmp_path / "nope")
    assert result.present is False
    assert result.items == []
    assert result.secrets_skipped == 0


def test_root_resolution_prefers_env_var(
    claude_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(claude_code.ENV_VAR, str(claude_root))
    assert claude_code.resolve_root() == claude_root
    # The default is the documented one — and it is only consulted when the var is unset.
    monkeypatch.delenv(claude_code.ENV_VAR, raising=False)
    assert claude_code.resolve_root() == Path(claude_code.DEFAULT_ROOT).expanduser()


def test_every_registered_source_resolves_and_scans_an_absent_root(tmp_path: Path) -> None:
    for source in list_sources():
        result = source.scan(tmp_path / f"absent-{source.name}")
        assert result.source == source.name
        assert result.present is False
    with pytest.raises(KeyError):
        get_source("no-such-tool")


def test_codex_scan_yields_instructions_and_mcp_servers(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    root.mkdir()
    (root / "AGENTS.md").write_text("Prefer small diffs.\n", encoding="utf-8")
    (root / "config.toml").write_text(
        'model = "gpt-5"\n\n[mcp_servers.docs]\ncommand = "docs-mcp"\n'
        f'[mcp_servers.docs.env]\nAPI_KEY = "{SECRET}"\n',
        encoding="utf-8",
    )
    result = codex.scan(root)

    assert result.present is True
    assert result.counts()[ImportCategory.INSTRUCTIONS.value] == 1
    assert result.counts()[ImportCategory.MCP_SERVERS.value] == 1
    assert result.secrets_skipped == 1
    assert SECRET not in json.dumps(result.to_dict())


# ── floor: secrets are counted and skipped ────────────────────────────────────


def test_planted_secret_appears_nowhere_in_scan_output(claude_root: Path) -> None:
    result = scan_source("claude_code", claude_root)
    blob = json.dumps(result.to_dict()) + "".join(
        item.text + json.dumps(item.payload) for item in result.items
    )

    assert SECRET not in blob
    assert SECRET2 not in blob
    # …and the user is TOLD, with a count: the credentials file, the MCP env key, the
    # settings key, and the .env inside the skill.
    assert result.secrets_skipped == 4
    assert any("skipped" in note for note in result.notes)
    # The credential embedded in CLAUDE.md prose was redacted, not silently dropped.
    assert result.redactions >= 1
    instructions = result.by_category(ImportCategory.INSTRUCTIONS)[0]
    assert "Always run the linter" in instructions.text
    assert SECRET not in instructions.text


def test_scan_and_import_never_write_to_the_foreign_root(claude_root: Path, home: Path) -> None:
    before = _tree(claude_root)
    results = [scan_source("claude_code", claude_root)]
    run_import(results)
    assert _tree(claude_root) == before


# ── floor: idempotence ────────────────────────────────────────────────────────


def test_rescan_is_idempotent(claude_root: Path) -> None:
    first = scan_source("claude_code", claude_root)
    second = scan_source("claude_code", claude_root)

    assert [i.fingerprint for i in first.items] == [i.fingerprint for i in second.items]
    assert len(first.items) == len(first.fingerprints())  # no duplicates within one scan
    assert first.counts() == second.counts()
    assert (first.secrets_skipped, first.redactions) == (
        second.secrets_skipped,
        second.redactions,
    )


def test_fingerprint_is_source_category_key_and_not_body() -> None:
    a = fingerprint_of("claude_code", ImportCategory.SKILLS, "tidy-notes")
    assert a == fingerprint_of("claude_code", "skills", "tidy-notes")
    assert a != fingerprint_of("codex", ImportCategory.SKILLS, "tidy-notes")
    assert a != fingerprint_of("claude_code", ImportCategory.MEMORIES, "tidy-notes")


# ── import ────────────────────────────────────────────────────────────────────


def test_import_creates_memories_mcp_entries_and_imported_skills(
    claude_root: Path, home: Path
) -> None:
    results = [scan_source("claude_code", claude_root)]
    report = run_import(results)

    assert report.counts()[WriteOutcome.IMPORTED.value] == 5
    assert report.counts()[WriteOutcome.CONFLICT.value] == 0
    assert report.counts()[WriteOutcome.REJECTED.value] == 0

    # memories: the full document under the memory dir + a record in the store's own
    # markdown projection.
    doc = home / "workspace" / "memory" / "imported" / "claude_code" / "CLAUDE.md"
    assert doc.is_file()
    assert "Always run the linter" in doc.read_text(encoding="utf-8")
    prefs = (home / "workspace" / "memory" / "preferences.md").read_text(encoding="utf-8")
    assert "Imported from claude_code (CLAUDE.md)" in prefs
    assert (
        home / "workspace" / "memory" / "imported" / "claude_code" / "memories__prefs.md"
    ).is_file() or (
        home / "workspace" / "memory" / "imported" / "claude_code" / "memories-prefs.md"
    ).is_file()

    # MCP entries: the user-owned override file the agent config merges.
    mcp = json.loads(mcp_config_path().read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["weather"]["command"] == "npx"
    assert "WEATHER_API_KEY" not in mcp["mcpServers"]["weather"]["env"]

    # skills/imported/claude_code/* — through the supply-chain gate.
    installed = home / "skills" / "imported" / "claude_code" / "tidy-notes"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "reference.md").is_file()
    assert not (installed / ".env").exists()  # the credential file never installed


def test_planted_secret_never_reaches_the_home(claude_root: Path, home: Path) -> None:
    run_import([scan_source("claude_code", claude_root)])
    blob = _all_bytes(home)
    assert SECRET.encode() not in blob
    assert SECRET2.encode() not in blob


def test_settings_are_staged_for_review_not_applied(claude_root: Path, home: Path) -> None:
    run_import([scan_source("claude_code", claude_root)])
    staged = staged_settings_path("claude_code", "settings.json")
    assert staged.is_file()
    payload = json.loads(staged.read_text(encoding="utf-8"))
    assert payload["settings"]["theme"] == "dark"
    assert "apiKeyHelper" not in payload["settings"]
    # Live config was never touched by the import.
    assert not (home / "config.json").exists()


def test_reimport_reports_existing_and_writes_nothing_new(claude_root: Path, home: Path) -> None:
    run_import([scan_source("claude_code", claude_root)])
    before = _tree(home)

    report = run_import([scan_source("claude_code", claude_root)])

    assert report.counts()[WriteOutcome.EXISTING.value] == 5
    assert report.counts()[WriteOutcome.IMPORTED.value] == 0
    changed = {
        path
        for path, digest in _tree(home).items()
        if before.get(path) != digest
        # SEL records every attempt (that is its job) and the WAL sidecar is not state.
        and not path.startswith("security_events") and not path.endswith(("-shm", "-wal"))
    }
    assert changed == set()


def test_selecting_one_category_imports_only_that_category(claude_root: Path, home: Path) -> None:
    report = run_import(
        [scan_source("claude_code", claude_root)], categories=[ImportCategory.MCP_SERVERS]
    )
    assert [r.category for r in report.results] == [ImportCategory.MCP_SERVERS]
    assert mcp_config_path().is_file()
    assert not (home / "skills" / "imported").exists()


# ── never clobber ─────────────────────────────────────────────────────────────


def test_conflicting_mcp_server_reports_conflict_and_keeps_existing(
    claude_root: Path, home: Path
) -> None:
    mine = {"mcpServers": {"weather": {"command": "my-own-weather", "args": []}}}
    mcp_config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_config_path().write_text(json.dumps(mine, indent=2), encoding="utf-8")
    before = mcp_config_path().read_bytes()

    report = run_import(
        [scan_source("claude_code", claude_root)], categories=[ImportCategory.MCP_SERVERS]
    )

    assert [r.outcome for r in report.results] == [WriteOutcome.CONFLICT]
    assert report.conflicts()[0].key == "weather"
    assert mcp_config_path().read_bytes() == before


def test_conflicting_skill_reports_conflict_and_keeps_existing(
    claude_root: Path, home: Path
) -> None:
    mine = home / "skills" / "imported" / "claude_code" / "tidy-notes"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(
        _SKILL_MD.format(name="tidy-notes", desc="MY OWN version"), encoding="utf-8"
    )
    before = _tree(mine)

    report = run_import(
        [scan_source("claude_code", claude_root)], categories=[ImportCategory.SKILLS]
    )

    assert [r.outcome for r in report.results] == [WriteOutcome.CONFLICT]
    assert _tree(mine) == before
    assert "MY OWN version" in (mine / "SKILL.md").read_text(encoding="utf-8")


def test_conflicting_instruction_doc_reports_conflict_and_keeps_existing(
    claude_root: Path, home: Path
) -> None:
    doc = home / "workspace" / "memory" / "imported" / "claude_code" / "CLAUDE.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("my own notes\n", encoding="utf-8")

    report = run_import(
        [scan_source("claude_code", claude_root)], categories=[ImportCategory.INSTRUCTIONS]
    )

    assert [r.outcome for r in report.results] == [WriteOutcome.CONFLICT]
    assert doc.read_text(encoding="utf-8") == "my own notes\n"


def test_conflict_detail_never_carries_a_value(claude_root: Path, home: Path) -> None:
    mcp_config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_config_path().write_text(
        json.dumps({"mcpServers": {"weather": {"command": "other", "env": {"K": SECRET}}}}),
        encoding="utf-8",
    )
    report = run_import(
        [scan_source("claude_code", claude_root)], categories=[ImportCategory.MCP_SERVERS]
    )
    assert SECRET not in json.dumps(report.to_dict())


# ── dispatch is exhaustive ────────────────────────────────────────────────────


def test_a_writer_exists_for_every_category() -> None:
    assert set(_WRITERS) == set(ImportCategory)
