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
* ``test_a_pick_imports_exactly_the_chosen_items`` /
  ``test_a_chosen_fingerprint_the_scan_lacks_is_reported_missing`` — the pick is a set of
  fingerprints and the scan passed in is its allowlist: exactly the chosen items are written,
  and a fingerprint the scan does not contain imports nothing and is REPORTED.
* ``test_the_plan_says_before_the_import_what_the_import_then_does`` — the state a scan shows
  beside an item is the outcome its import reports, for every state, because the writer
  consults the planner rather than re-deciding.
* ``test_each_item_carries_its_own_withheld_count`` /
  ``test_split_tables_is_strip_secrets_with_the_count_kept_per_entry`` — a user can see WHICH
  item comes over without a credential, and the per-entry split cannot change a total.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
from pathlib import Path

import pytest

from personalclaw.onboarding_import import (
    ImportCategory,
    ItemState,
    WriteOutcome,
    fingerprint_of,
    get_source,
    list_sources,
    plans,
    run_import,
    scan_source,
)
from personalclaw.onboarding_import.floors import split_tables, strip_secrets
from personalclaw.onboarding_import.sources import claude_code, codex
from personalclaw.onboarding_import.writers import (
    _PLANNERS,
    _WRITERS,
    mcp_config_path,
    staged_settings_path,
)

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


def _picks(results, *categories: ImportCategory) -> list[str]:
    """The fingerprints of every scanned item in ``categories`` — how a pick names a group."""
    return [
        item.fingerprint
        for result in results
        for item in result.items
        if item.category in categories
    ]


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


def test_a_pick_imports_exactly_the_chosen_items(claude_root: Path, home: Path) -> None:
    """The pick is item by item: two of five chosen, two written, three reported as left out."""
    results = [scan_source("claude_code", claude_root)]
    memory, weather = (
        _picks(results, ImportCategory.MEMORIES)[0],
        _picks(results, ImportCategory.MCP_SERVERS)[0],
    )

    report = run_import(results, fingerprints=[memory, weather])

    assert sorted(r.fingerprint for r in report.results) == sorted([memory, weather])
    assert {r.outcome for r in report.results} == {WriteOutcome.IMPORTED}
    assert mcp_config_path().is_file()
    # Everything NOT chosen was left alone — and is accounted for, not silently absent.
    assert not (home / "skills" / "imported").exists()
    assert not staged_settings_path("claude_code", "settings.json").exists()
    doc = home / "workspace" / "memory" / "imported" / "claude_code" / "CLAUDE.md"
    assert not doc.exists()
    left_out = {item.fingerprint: plan.state for item, plan in report.unselected}
    assert set(left_out) == {i.fingerprint for i in results[0].items} - {memory, weather}
    assert set(left_out.values()) == {ItemState.NEW}
    assert report.missing == []


def test_a_chosen_fingerprint_the_scan_lacks_is_reported_missing(
    claude_root: Path, home: Path
) -> None:
    """The scan handed in is the allowlist: an id it does not contain imports NOTHING and is
    named in the report, rather than disappearing between the pick and the result."""
    results = [scan_source("claude_code", claude_root)]
    weather = _picks(results, ImportCategory.MCP_SERVERS)[0]
    stranger = fingerprint_of("claude_code", ImportCategory.SKILLS, "not-on-this-machine")

    report = run_import(results, fingerprints=[stranger, weather, stranger])

    assert [r.fingerprint for r in report.results] == [weather]
    assert report.missing == [stranger], "reported once, not once per mention"
    assert not (home / "skills" / "imported").exists()


def test_the_plan_says_before_the_import_what_the_import_then_does(
    claude_root: Path, home: Path
) -> None:
    """Every state the scan can show, set up for real, then imported: the plan beside each
    item is the outcome its row reports. One reading of the destination, not two."""
    # conflict: a differently-configured server of the same name the user already had.
    mcp_config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_config_path().write_text(
        json.dumps({"mcpServers": {"weather": {"command": "mine"}}}), encoding="utf-8"
    )
    # existing: the settings were already staged, byte-identically, by an earlier import.
    first = [scan_source("claude_code", claude_root)]
    run_import(first, fingerprints=_picks(first, ImportCategory.SETTINGS))
    # rejected: the skill's source directory vanished between the scan and the import.
    results = [scan_source("claude_code", claude_root)]
    skill = next(i for i in results[0].items if i.category is ImportCategory.SKILLS)
    shutil.rmtree(skill.path)

    before = plans(results)
    by_state = {plan.state for plan in before.values()}
    assert by_state == {ItemState.NEW, ItemState.CONFLICT, ItemState.EXISTING, ItemState.REJECTED}

    report = run_import(results)

    new_as_imported = {ItemState.NEW.value: WriteOutcome.IMPORTED.value}
    for row in report.results:
        planned = before[row.fingerprint]
        expected = new_as_imported.get(planned.state.value, planned.state.value)
        assert row.outcome.value == expected, row
        assert row.destination == planned.destination
        if planned.state is not ItemState.NEW:
            assert row.detail == planned.detail, "the scan and the report say the same sentence"


def test_planning_writes_nothing_to_the_home(claude_root: Path, home: Path) -> None:
    """A scan asks every item's plan; asking must not create a directory, a file or an audit
    row — the step opens this BEFORE the user has agreed to anything."""
    results = [scan_source("claude_code", claude_root)]
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    assert len(plans(results)) == len(results[0].items) == 5
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*")) == before


def test_unselected_items_carry_the_plan_they_had_before_the_writes(
    claude_root: Path, tmp_path: Path, home: Path
) -> None:
    """Two tools define an MCP server of one name. Importing Claude Code's changes what Codex's
    WOULD do — but the user left Codex's out while it read `new`, and that is the state that
    explains the choice. Planned after the writes, the report would call it a conflict."""
    codex_root = tmp_path / ".codex"
    codex_root.mkdir()
    (codex_root / "config.toml").write_text(
        '[mcp_servers.weather]\ncommand = "codex-weather"\n', encoding="utf-8"
    )
    results = [scan_source("claude_code", claude_root), scan_source("codex", codex_root)]
    claude_weather, codex_weather = _picks(results, ImportCategory.MCP_SERVERS)

    report = run_import(results, fingerprints=[claude_weather])

    assert [r.outcome for r in report.results] == [WriteOutcome.IMPORTED]
    left_out = {item.fingerprint: plan.state for item, plan in report.unselected}
    assert left_out[codex_weather] is ItemState.NEW
    # …and a scan made AFTER the import tells the truth about it from then on.
    assert plans(results)[codex_weather].state is ItemState.CONFLICT


# ── never clobber ─────────────────────────────────────────────────────────────


def test_conflicting_mcp_server_reports_conflict_and_keeps_existing(
    claude_root: Path, home: Path
) -> None:
    mine = {"mcpServers": {"weather": {"command": "my-own-weather", "args": []}}}
    mcp_config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_config_path().write_text(json.dumps(mine, indent=2), encoding="utf-8")
    before = mcp_config_path().read_bytes()

    results = [scan_source("claude_code", claude_root)]
    report = run_import(results, fingerprints=_picks(results, ImportCategory.MCP_SERVERS))

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

    results = [scan_source("claude_code", claude_root)]
    report = run_import(results, fingerprints=_picks(results, ImportCategory.SKILLS))

    assert [r.outcome for r in report.results] == [WriteOutcome.CONFLICT]
    assert _tree(mine) == before
    assert "MY OWN version" in (mine / "SKILL.md").read_text(encoding="utf-8")


def test_conflicting_instruction_doc_reports_conflict_and_keeps_existing(
    claude_root: Path, home: Path
) -> None:
    doc = home / "workspace" / "memory" / "imported" / "claude_code" / "CLAUDE.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("my own notes\n", encoding="utf-8")

    results = [scan_source("claude_code", claude_root)]
    report = run_import(results, fingerprints=_picks(results, ImportCategory.INSTRUCTIONS))

    assert [r.outcome for r in report.results] == [WriteOutcome.CONFLICT]
    assert doc.read_text(encoding="utf-8") == "my own notes\n"


def test_a_server_that_appears_after_the_plan_is_kept_not_overwritten(
    claude_root: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mcp.json` is the one destination several writers read-modify-write, so the writer
    re-asks the plan of its OWN read. A server the user adds between the plan and the write
    (simulated: the planner reads an empty file) is kept, never clobbered."""
    from personalclaw.onboarding_import import writers

    results = [scan_source("claude_code", claude_root)]
    weather = _picks(results, ImportCategory.MCP_SERVERS)
    stale = writers._PLANNERS[ImportCategory.MCP_SERVERS]

    def plan_then_the_user_edits(item):
        plan = stale(item)
        mcp_config_path().write_text(
            json.dumps({"mcpServers": {"weather": {"command": "added-meanwhile"}}}),
            encoding="utf-8",
        )
        return plan

    monkeypatch.setitem(writers._PLANNERS, ImportCategory.MCP_SERVERS, plan_then_the_user_edits)
    report = run_import(results, fingerprints=weather)

    assert [r.outcome for r in report.results] == [WriteOutcome.CONFLICT]
    kept = json.loads(mcp_config_path().read_text(encoding="utf-8"))
    assert kept["mcpServers"]["weather"] == {"command": "added-meanwhile"}


def test_conflict_detail_never_carries_a_value(claude_root: Path, home: Path) -> None:
    mcp_config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_config_path().write_text(
        json.dumps({"mcpServers": {"weather": {"command": "other", "env": {"K": SECRET}}}}),
        encoding="utf-8",
    )
    results = [scan_source("claude_code", claude_root)]
    report = run_import(results, fingerprints=_picks(results, ImportCategory.MCP_SERVERS))
    assert SECRET not in json.dumps(report.to_dict())
    assert SECRET not in json.dumps([plan.detail for plan in plans(results).values()])


# ── each item says what was withheld from IT ─────────────────────────────────


def test_each_item_carries_its_own_withheld_count(claude_root: Path) -> None:
    """The fixture plants one credential in each of four places. Each item that lost one says
    so on its own row, and the one that belongs to no item (the root credential file) is what
    the source total holds beyond them — so the numbers a user reads reconcile."""
    result = scan_source("claude_code", claude_root)
    by_key = {item.key: item for item in result.items}

    assert by_key["weather"].secrets_skipped == 1  # WEATHER_API_KEY, dropped from its env
    assert by_key["tidy-notes"].secrets_skipped == 1  # the .env inside the skill
    assert by_key["settings.json"].secrets_skipped == 1  # apiKeyHelper
    assert by_key["CLAUDE.md"].redactions >= 1  # the key in the prose, redacted
    assert by_key["memories/prefs.md"].secrets_skipped == 0
    assert result.secrets_skipped == 4
    assert result.secrets_outside_items() == 1  # .credentials.json, never opened
    # A count, never the value — on the wire as in memory.
    wire = json.dumps(result.to_dict())
    assert SECRET not in wire and SECRET2 not in wire
    assert '"secrets_skipped": 1' in wire


def test_the_reported_withheld_count_follows_the_choice(claude_root: Path, home: Path) -> None:
    """Importing only the instructions must not claim the MCP server's key and the skill's
    `.env` were withheld FROM THIS IMPORT — the user never picked them. What belongs to no
    item (the root credential file) is left behind whatever is picked, so it stays."""
    results = [scan_source("claude_code", claude_root)]
    only_claude_md = run_import(results, fingerprints=_picks(results, ImportCategory.INSTRUCTIONS))
    assert only_claude_md.secrets_skipped == 1
    assert only_claude_md.redactions >= 1

    everything = run_import([scan_source("claude_code", claude_root)])
    assert everything.secrets_skipped == 4


def test_codex_attributes_each_server_and_the_settings_remainder(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    root.mkdir()
    (root / "config.toml").write_text(
        f'model = "gpt-5"\napi_key = "{SECRET}"\n\n'
        '[mcp_servers.docs]\ncommand = "docs-mcp"\n'
        f'[mcp_servers.docs.env]\nAPI_KEY = "{SECRET}"\nTOKEN = "{SECRET2}"\n'
        '[mcp_servers.plain]\ncommand = "plain-mcp"\n',
        encoding="utf-8",
    )
    result = codex.scan(root)
    by_key = {item.key: item for item in result.items}

    assert by_key["docs"].secrets_skipped == 2
    assert by_key["plain"].secrets_skipped == 0
    assert by_key["config.toml"].secrets_skipped == 1  # api_key, from the settings remainder
    assert by_key["config.toml"].payload == {"model": "gpt-5"}
    assert result.secrets_skipped == 3
    assert result.secrets_outside_items() == 0


@pytest.mark.parametrize(
    "doc",
    [
        {"mcpServers": {"a": {"env": {"API_KEY": "x", "K": "v"}}, "b": {"command": "c"}}},
        {"mcpServers": {"auth-proxy": {"command": "c"}, "ok": {"token": "t"}}, "secret": 1},
        {"mcpServers": {"a": "not-a-dict", "b": {"args": [{"password": "p"}]}}, "top": [1]},
        {"mcpServers": ["not", "a", "table"], "access_key": "k"},
        {"mcp_servers": {"a": {"k": 1}}, "mcpServers": {"a": {"api_key": "z"}}},
        ["not", "a", "document"],
    ],
)
def test_split_tables_is_strip_secrets_with_the_count_kept_per_entry(doc) -> None:
    """The per-entry walk may not change a TOTAL: whatever the document's shape — secret-named
    servers, junk entries, a table that is not one, a name defined in two tables — the split's
    total is the whole-document strip's count, and every value it returns is stripped."""
    tables = ("mcp_servers", "mcpServers")
    split = split_tables(doc, tables)
    assert split.total == strip_secrets(doc)[1]
    for _name, entry, withheld in split.entries:
        assert strip_secrets(entry) == (entry, 0), "an entry left the floor unstripped"
        assert withheld >= 0
    assert strip_secrets(split.remainder)[1] == 0
    assert len({name for name, _e, _w in split.entries}) == len(split.entries)


# ── dispatch is exhaustive ────────────────────────────────────────────────────


def test_a_writer_exists_for_every_category() -> None:
    assert set(_WRITERS) == set(ImportCategory)
    assert set(_PLANNERS) == set(ImportCategory)


# ── The withheld-credential notes have ONE composer, and it agrees with its own counts ────────────
#
# These two sentences were written TWICE, word for word: `ScanResult.note_withheld` and, inline,
# `writers.import_report`. Both classes carry the same `secrets_skipped` / `redactions` / `notes`
# fields, so `model.withheld_notes` is now the only place either sentence exists.
#
# 🔑 WHY A `(s)` HEDGE WAS WORSE HERE THAN USUAL. The first sentence joins TWO nouns with
# "or", so a parenthetical hid two disagreements rather than one: the nouns AND the verb.
# `1 credential value(s) or file(s) were skipped` is wrong three times over, at the count a
# first import most often produces — most machines carry a single API key for the tool being
# imported from.
#
# 🪤 The counts are asserted on OPPOSING numbers, not just both sides of the boundary. With
# `secrets_skipped` and `redactions` on the same side, a sentence reading the other one's count
# would still look correct — the mistake this programme has made once and railed against twice.


def test_the_withheld_notes_have_exactly_one_composer():
    """Both producers must route through `withheld_notes`, not re-compose the sentences."""
    from personalclaw.onboarding_import import model, writers

    src_model = inspect.getsource(model.ScanResult.note_withheld)
    src_writer = inspect.getsource(writers.import_report)
    for name, src in (
        ("ScanResult.note_withheld", src_model),
        ("writers.import_report", src_writer),
    ):
        assert "withheld_notes(" in src, f"{name} must delegate to the shared composer"
        assert (
            "credential value" not in src
        ), f"{name} re-composes the sentence instead of delegating"
        assert (
            "credential-like string" not in src
        ), f"{name} re-composes the sentence instead of delegating"


def test_the_withheld_notes_agree_with_their_own_counts_on_both_sides_of_one():
    from personalclaw.onboarding_import.model import withheld_notes

    one = withheld_notes(secrets_skipped=1, redactions=1)
    assert one == [
        "1 credential value or file was skipped and not imported.",
        "1 credential-like string was redacted from imported text.",
    ]
    many = withheld_notes(secrets_skipped=3, redactions=3)
    assert many == [
        "3 credential values or files were skipped and not imported.",
        "3 credential-like strings were redacted from imported text.",
    ]
    # No sentence anywhere may carry the hedge again.
    for note in one + many:
        assert "(s)" not in note


def test_each_withheld_sentence_owns_its_OWN_count_not_its_siblings():
    """Opposing counts: a sentence reading the other's number would pass a same-side fixture."""
    from personalclaw.onboarding_import.model import withheld_notes

    skipped_one = withheld_notes(secrets_skipped=1, redactions=4)
    assert "1 credential value or file was skipped" in skipped_one[0]
    assert "4 credential-like strings were redacted" in skipped_one[1]

    redacted_one = withheld_notes(secrets_skipped=4, redactions=1)
    assert "4 credential values or files were skipped" in redacted_one[0]
    assert "1 credential-like string was redacted" in redacted_one[1]


def test_nothing_withheld_says_nothing():
    """The guard is the count itself — a zero must not produce an empty-sounding note."""
    from personalclaw.onboarding_import.model import withheld_notes

    assert withheld_notes(secrets_skipped=0, redactions=0) == []
    assert len(withheld_notes(secrets_skipped=2, redactions=0)) == 1
    assert len(withheld_notes(secrets_skipped=0, redactions=2)) == 1


def test_the_note_says_how_much_never_what():
    """The security property `note_withheld`'s docstring promises, asserted rather than trusted."""
    from personalclaw.onboarding_import.model import withheld_notes

    for note in withheld_notes(secrets_skipped=2, redactions=2):
        # A count and a noun, and no room for a value: no path separators, no '=' , no quotes.
        assert "/" not in note and "=" not in note and '"' not in note and "'" not in note
