"""What is one machine's own account of itself stays on it: a sync never carries it or merges it.

🔴 The files that are one row of one machine's own account — what it spent on models each day
(``spend.json``, which its budget caps count), its tool counters, its context-savings ledger, when
its own backups and syncs last ran, which due-date notices it sent, and the legacy files each home
imports once — were exported by every sync and merged by id as one row. Two machines could never
agree on a counter, so once both had moved every pull recorded the file as a conflict to review;
and a copy taken from the review wrote the other machine's counters over this one's.

Each is now ``machine_local``: a snapshot and a backup carry it, a sync's export leaves it out, a
pull leaves this machine's as it is even from a peer that still sends one, and the review closes an
old conflict on one only by keeping this machine's. So are the files of a synced folder that are one
machine's own (``machine_local_within``): the agent CLI's runtime config, which holds what this
machine's owner allowed the agent CLI; each runner's health as this machine measured it; and the
template nudges' counters and candidates. The sync docs name each.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from personalclaw.durability import conflict_resolve as resolver
from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile
from personalclaw.durability.shards import export_shards
from personalclaw.durability.sync_cycle import run_sync_cycle
from tests.test_durability_sync_cycle import SharedStore

MACHINE_LOCAL = sorted(e.id for e in inv.INVENTORY if e.machine_local)

#: A small document of each machine-local store's own shape, keyed by entry id.
_ONE_MACHINES = {
    "spend": {"days": {"2026-09-28": {"usd": 1.5}}},
    "tool_usage": {"read_file": 3},
    "tokenjuice_savings": {"rows": {"2026-09|model|compressor": 12}},
    "durability_state": {"last_backup_at": "2026-09-28T02:00:00Z"},
    "task_due_notices": {"notified": {"task-1": "2026-09-28"}},
    "crons": {"jobs": []},
    "event_triggers": {"triggers": []},
    "autonudge": {"loops": {}},
}


def _write(home: Path, entry_id: str, document: dict) -> Path:
    entry = inv.by_id(entry_id)
    assert entry is not None
    path = home / entry.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


#: One file of each synced folder that stays on each machine: ``(entry id, path under it)``.
_WITHIN = {
    "agents": ("personalclaw.json", {"mcpServers": {}, "allowedTools": ["read_file"]}),
    "agent_metadata": ("claude-code.runner.json", {"runner": "claude-code", "last_check": {}}),
    "workflows": ("template_nudges.json", {"__turn__": 7}),
}


def test_every_machine_local_store_has_a_document_here():
    assert MACHINE_LOCAL == sorted(_ONE_MACHINES)
    assert sorted(_WITHIN) == sorted(e.id for e in inv.INVENTORY if e.machine_local_within)


@pytest.mark.parametrize("entry_id", MACHINE_LOCAL)
def test_a_backup_carries_it_and_a_sync_does_not(tmp_path, entry_id):
    home = tmp_path / "home"
    _write(home, entry_id, _ONE_MACHINES[entry_id])
    backup = export_shards(home, tmp_path / "backup")
    synced = export_shards(home, tmp_path / "sync", for_sync=True)
    assert any(s.path.startswith(f"{entry_id}/") for s in backup.shards), "a backup carries it"
    assert not any(s.path.startswith(f"{entry_id}/") for s in synced.shards)


def _write_within(home: Path, entry_id: str) -> tuple[inv.StateEntry, str]:
    entry = inv.by_id(entry_id)
    assert entry is not None
    name, document = _WITHIN[entry_id]
    path = home / entry.path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    # A file of the same folder that syncs, as the control.
    (home / entry.path / "shared-one.json").write_text(json.dumps({"id": "shared-one"}))
    return entry, name[: -len(".json")]


@pytest.mark.parametrize("entry_id", sorted(_WITHIN))
def test_a_backup_carries_a_machines_own_file_of_a_synced_folder_and_a_sync_does_not(
    tmp_path, entry_id
):
    home = tmp_path / "home"
    entry, row_id = _write_within(home, entry_id)
    assert inv.stays_here(entry, row_id)

    def rows(out: Path) -> set[str]:
        lines = (out / entry.id / "entities.jsonl").read_text().splitlines()
        return {json.loads(line)["id"] for line in lines if line.strip()}

    export_shards(home, tmp_path / "backup")
    export_shards(home, tmp_path / "sync", for_sync=True)
    assert rows(tmp_path / "backup") == {row_id, "shared-one"}, "a backup carries it"
    assert rows(tmp_path / "sync") == {"shared-one"}, "a sync carries the rest of the folder"


@pytest.mark.parametrize("entry_id", sorted(_WITHIN))
def test_a_peers_copy_of_a_machines_own_file_is_left_out(tmp_path, entry_id):
    entry, row_id = _write_within(tmp_path, entry_id)
    mine = tmp_path / entry.path / f"{row_id}.json"
    before = mine.read_bytes()
    theirs = [
        {"id": row_id, "data": {"from": "the other machine"}},
        {"id": "only-there", "data": {"id": "only-there"}},
    ]
    result = reconcile.reconcile_entry(
        tmp_path, entry, theirs, queue=conflicts_mod.ConflictQueue(tmp_path)
    )
    assert result.verdict == "consumed" and result.conflicts == 0
    assert mine.read_bytes() == before
    # CONTROL: the rest of the folder still syncs.
    assert (tmp_path / entry.path / "only-there.json").is_file()


def _cycle(store: SharedStore, home: Path, name: str):
    report = run_sync_cycle(store, home, self_id=name, now="now")
    assert report.ok, report.error
    return report


def test_two_machines_keep_their_own_spend_and_never_conflict_over_it(tmp_path):
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    for home in (a, b):
        _write(home, "spend", {"days": {}})
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)

    _write(a, "spend", {"days": {"2026-09-28": {"usd": 1.5}}})
    _write(b, "spend", {"days": {"2026-09-28": {"usd": 0.25}}})
    for home, name in ((a, "A"), (b, "B"), (a, "A"), (b, "B")):
        report = _cycle(store, home, name)
        assert report.conflicts == 0, f"{name} recorded a conflict over its own spend"
    assert json.loads((a / "spend.json").read_text())["days"]["2026-09-28"]["usd"] == 1.5
    assert json.loads((b / "spend.json").read_text())["days"]["2026-09-28"]["usd"] == 0.25
    assert conflicts_mod.ConflictQueue(a).items() == []
    assert conflicts_mod.ConflictQueue(b).items() == []


def test_a_peer_that_still_sends_one_changes_nothing_here(tmp_path):
    entry = inv.by_id("spend")
    assert entry is not None
    mine = _write(tmp_path, "spend", {"days": {"2026-09-28": {"usd": 1.5}}})
    before = mine.read_bytes()
    theirs = [{"id": "spend.json", "data": {"days": {"2026-09-28": {"usd": 99.0}}}}]
    result = reconcile.reconcile_entry(
        tmp_path,
        entry,
        theirs,
        ancestors={"spend.json": "an-old-agreement"},
        queue=conflicts_mod.ConflictQueue(tmp_path),
    )
    assert result.verdict == "consumed" and result.conflicts == 0
    assert mine.read_bytes() == before


def test_an_old_conflict_on_one_closes_only_by_keeping_this_machines(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    mine = _write(tmp_path, "spend", {"days": {"2026-09-28": {"usd": 1.5}}})
    before = mine.read_bytes()
    queue = conflicts_mod.ConflictQueue(tmp_path)
    record = conflicts_mod.ConflictRecord(
        entry_id="spend",
        entity_id="spend.json",
        domain=inv.DOMAIN_PLATFORM,
        surface=conflicts_mod.SURFACE_DURABILITY,
        ancestor_sha="a",
        local_sha="l",
        remote_sha="r",
        local_row={"id": "spend.json", "data": json.loads(before)},
        remote_row={"id": "spend.json", "data": {"days": {"2026-09-28": {"usd": 99.0}}}},
    )
    assert queue.record(record)
    refused = resolver.resolve_conflict(tmp_path, record.id, resolver.CHOICE_TAKE_REMOTE)
    assert not refused.ok and refused.code == "machine_local"
    assert mine.read_bytes() == before
    kept = resolver.resolve_conflict(tmp_path, record.id, resolver.CHOICE_KEEP_LOCAL)
    assert kept.ok and mine.read_bytes() == before


def test_an_old_conflict_on_the_agent_runtime_config_closes_only_by_keeping_this_machines(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    entry, row_id = _write_within(tmp_path, "agents")
    mine = tmp_path / "agents" / "personalclaw.json"
    before = mine.read_bytes()
    record = conflicts_mod.ConflictRecord(
        entry_id="agents",
        entity_id=row_id,
        domain=entry.domain,
        surface=conflicts_mod.SURFACE_DURABILITY,
        ancestor_sha="a",
        local_sha="l",
        remote_sha="r",
        local_row={"id": row_id, "data": json.loads(before)},
        remote_row={"id": row_id, "data": {"allowedTools": ["*"]}},
    )
    assert conflicts_mod.ConflictQueue(tmp_path).record(record)
    refused = resolver.resolve_conflict(tmp_path, record.id, resolver.CHOICE_TAKE_REMOTE)
    assert not refused.ok and refused.code == "machine_local"
    assert mine.read_bytes() == before
    assert resolver.taking_it_here(tmp_path, record) == ""
    assert resolver.resolve_conflict(tmp_path, record.id, resolver.CHOICE_KEEP_LOCAL).ok


_SYNC_DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture" / "tasks-triggers.md"
_LEAD = "**What is one machine's own stays on it.**"


def _paragraph(doc: str, lead: str) -> str:
    """The paragraph of *doc* that starts with *lead*: up to the next blank line."""
    start = doc.find(lead)
    assert start >= 0, f"{_SYNC_DOC.name} has no paragraph starting {lead!r}"
    end = doc.find("\n\n", start)
    return doc[start : end if end >= 0 else len(doc)]


def test_the_sync_docs_name_everything_that_stays_on_each_machine():
    named = set(re.findall(r"`([^`]+)`", _paragraph(_SYNC_DOC.read_text(), _LEAD)))
    staying = {e.path for e in inv.INVENTORY if e.machine_local} | {
        f"{e.path}/{glob}" for e in inv.INVENTORY for glob in e.machine_local_within
    }
    assert staying, "nothing is declared machine-local — the check is vacuous"
    assert staying <= named, sorted(staying - named)
