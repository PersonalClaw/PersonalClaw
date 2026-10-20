"""Another home's learning log comes into this one whole: by a merge restore, a folder sync and an
archive import alike.

The learning log (``learning.db``: the captures waiting to be compiled, each capture pass's outcome,
the prompt budget samples, the ablation sweeps, the curator's undo journal and the surfacing events)
numbers its rows itself, and nothing else told one row from another. Every way another home's log
arrives merged it by those numbers, so a row whose number this home already used was dropped
without a word, and a proposal that named the captures it was compiled from by number named this
home's captures once it moved. An archive import did not merge the log at all. Each row now has an
identity that is the same in every home it reaches, the merges match rows by it, and a row written
before it had one gets it when the log is opened.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from personalclaw import snapshot
from personalclaw.durability import inventory as inv
from personalclaw.durability.cursor import CONSUMED
from personalclaw.durability.db_merge import make_db_merger
from personalclaw.durability.shards import export_shards
from personalclaw.learning import staging as staging_mod
from personalclaw.learning.curator import Mutation, MutationLog
from personalclaw.learning.hygiene import fingerprint
from personalclaw.learning.staging import FlushOutcome, StagingStore
from personalclaw.learning.surfacing_events import SurfacingEvent, SurfacingEventStore
from personalclaw.portability import apply_import_zip, create_export_zip, validate_import_zip

#: When each home's rows were written: the other home's first, this one's later.
THERE_AT = 1_790_000_000.0
HERE_AT = 1_790_400_000.0


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", lambda: None)


@pytest.fixture(autouse=True)
def _fresh_staging_store():
    staging_mod.reset_store()
    yield
    staging_mod.reset_store()


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, tz=timezone.utc).isoformat()


def _home(root: Path, monkeypatch) -> Path:
    """A home holding only its settings, made the active one."""
    root.mkdir(parents=True)
    (root / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    return root


def _write_log(home: Path, tag: str, *, at: float, captures: int = 2) -> None:
    """A learning log as its stores write one: captures (the first compiled already), a capture
    pass's outcome, a budget sample, an ablation sweep, a curator change and a surfacing event,
    each naming *tag*. Two homes written this way number their rows alike."""
    store = StagingStore(home)
    try:
        ids = [
            store.stage(cadence="per_turn", kind="lesson", content=f"{tag} capture {n}")
            for n in range(captures)
        ]
        store.mark_consumed(ids[:1], f"batch-{tag}")
        store.record_flush(
            cadence="per_turn",
            outcome=FlushOutcome.FLUSH_PRODUCED,
            detail=f"{tag} pass",
            staged_count=captures,
        )
        store.record_allocation(used_tokens=len(tag), budget_tokens=100, now=at)
        store.record_ablation(
            [{"heuristic": f"{tag} recency", "delta": 0.25, "verdict": "keep", "items": 3}], now=at
        )
    finally:
        store.close()
    journal = MutationLog(home)
    try:
        journal.append(
            Mutation(
                operation="age",
                kind="skill",
                entity=f"{tag} skill",
                before={"state": "active"},
                after={"state": "stale"},
                at=_iso(at),
            )
        )
    finally:
        journal.close()
    events = SurfacingEventStore(home)
    try:
        events.record(
            [
                SurfacingEvent(
                    kind="skill",
                    entity=f"{tag} skill",
                    arm="lexical",
                    confidence=0.7,
                    used=True,
                    query=f"{tag} question",
                    session=f"{tag} session",
                    created_ts=at,
                )
            ]
        )
    finally:
        events.close()


_READS = {
    "captures": "SELECT content, kind, consumed_by FROM staging",
    "passes": "SELECT detail, outcome, staged_count FROM flush_records",
    "samples": "SELECT used_tokens, budget_tokens, created_ts FROM allocation_samples",
    "sweeps": "SELECT heuristic, delta, verdict FROM ablation_sweeps",
    "changes": "SELECT entity, operation, at, undone_at FROM curator_mutations",
    "surfaced": "SELECT entity, query, session, created_ts FROM surfacing_events",
}


def _log(home: Path) -> dict[str, list[tuple]]:
    """Every row of the learning log by what it holds, never by its number: a row that came in
    under another row's number, or carries another row's state, reads as a different row. A
    table the log does not have holds no rows."""
    conn = sqlite3.connect(str(home / "learning.db"))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            what: sorted(conn.execute(sql).fetchall()) if sql.split()[-1] in tables else []
            for what, sql in _READS.items()
        }
    finally:
        conn.close()


def _union(*logs: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    return {what: sorted(row for log in logs for row in log[what]) for what in _READS}


def _two_homes(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """The other home, with one capture more than this one, and this home, made the active one.
    Their rows' numbers overlap in every table."""
    there = _home(tmp_path / "there", monkeypatch)
    _write_log(there, "there", at=THERE_AT, captures=3)
    here = _home(tmp_path / "here", monkeypatch)
    _write_log(here, "here", at=HERE_AT)
    return there, here


def _snapshot_of(home: Path, out: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    out.mkdir()
    assert snapshot.snapshot_main([str(out)]) == 0
    return next(out.glob("personalclaw-snapshot-*.tar.gz"))


def _export(home: Path, out: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    data, _ = create_export_zip()
    out.write_bytes(data)
    ok, why, _ = validate_import_zip(out)
    assert ok, why
    return out


def _learning_log() -> inv.StateEntry:
    return next(e for e in inv.sqlite_entries() if e.id == "learning_db")


def _identities(home: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(home / "learning.db"))
    try:
        return [r[0] for r in conn.execute(f'SELECT uid FROM "{table}" ORDER BY id')]
    finally:
        conn.close()


# ── every row of the other home's log comes in, once ─────────────────────────────────────────


def test_a_merge_restore_brings_in_every_row_of_another_homes_log(tmp_path, monkeypatch):
    there = _home(tmp_path / "there", monkeypatch)
    _write_log(there, "there", at=THERE_AT, captures=3)
    tarball = _snapshot_of(there, tmp_path / "snapshots", monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    _write_log(here, "here", at=HERE_AT)
    whole = _union(_log(here), _log(there))

    for _ in range(2):  # the second merge of the same snapshot adds nothing
        result = snapshot.restore_merge(tarball, None)
        assert result["ok"] is True and result["left_unchanged"] == []
        assert _log(here) == whole


def test_a_folder_sync_brings_in_every_row_of_another_machines_log(tmp_path, monkeypatch):
    there, here = _two_homes(tmp_path, monkeypatch)
    sent = tmp_path / "sent"
    export_shards(there, sent, for_sync=True)
    whole = _union(_log(here), _log(there))

    for _ in range(2):  # the same export read twice adds nothing
        assert make_db_merger(here)(_learning_log(), sent) == CONSUMED
        assert _log(here) == whole


def test_an_archive_import_brings_in_every_row_of_another_homes_log(tmp_path, monkeypatch):
    there = _home(tmp_path / "there", monkeypatch)
    _write_log(there, "there", at=THERE_AT, captures=3)
    archive = _export(there, tmp_path / "there.zip", monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    _write_log(here, "here", at=HERE_AT)
    whole = _union(_log(here), _log(there))

    for _ in range(2):  # importing the same archive again adds nothing
        summary = apply_import_zip(archive, "merge")
        assert "learning log (merged)" in summary["items"]
        assert summary["left_unchanged"] == []
        assert _log(here) == whole


def test_a_home_with_no_learning_log_takes_the_other_homes_whole(tmp_path, monkeypatch):
    """The control: each path carries every table of the log, so a merge that loses rows loses
    them in the merge."""
    there = _home(tmp_path / "there", monkeypatch)
    _write_log(there, "there", at=THERE_AT, captures=3)
    theirs = _log(there)
    tarball = _snapshot_of(there, tmp_path / "snapshots", monkeypatch)
    archive = _export(there, tmp_path / "there.zip", monkeypatch)
    sent = tmp_path / "sent"
    export_shards(there, sent, for_sync=True)

    restored = _home(tmp_path / "restored", monkeypatch)
    assert snapshot.restore_merge(tarball, None)["left_unchanged"] == []
    assert _log(restored) == theirs

    imported = _home(tmp_path / "imported", monkeypatch)
    assert "learning log (copied)" in apply_import_zip(archive, "merge")["items"]
    assert _log(imported) == theirs

    synced = tmp_path / "synced"
    synced.mkdir()
    assert make_db_merger(synced)(_learning_log(), sent) == CONSUMED
    assert _log(synced) == theirs


def test_every_row_has_an_identity_of_its_own(tmp_path, monkeypatch):
    _there, here = _two_homes(tmp_path, monkeypatch)
    for table in (
        "staging",
        "flush_records",
        "allocation_samples",
        "ablation_sweeps",
        "curator_mutations",
        "surfacing_events",
    ):
        ids = _identities(here, table)
        assert ids and all(ids), table
        assert len(set(ids)) == len(ids), table


def test_a_table_this_home_never_opened_takes_the_other_homes_rows(tmp_path, monkeypatch):
    """This home has captured turns but has never run the curator or surfaced a skill, so its
    log has no table for either; the other home's curator changes and surfacing events come in."""
    there = _home(tmp_path / "there", monkeypatch)
    _write_log(there, "there", at=THERE_AT)
    here = tmp_path / "here"
    here.mkdir()
    store = StagingStore(here)
    store.stage(cadence="per_turn", kind="lesson", content="here capture 0")
    store.close()
    sent = tmp_path / "sent"
    export_shards(there, sent, for_sync=True)

    assert make_db_merger(here)(_learning_log(), sent) == CONSUMED

    merged = _log(here)
    assert merged["changes"] == _log(there)["changes"]
    assert merged["surfaced"] == _log(there)["surfaced"]


# ── what refers to a row follows it ─────────────────────────────────────────────────────────


def test_a_proposal_names_the_captures_it_was_compiled_from_in_every_home(tmp_path, monkeypatch):
    """The drain compiles waiting captures into one proposal that names them, so a surprising
    proposal can be traced to the turns behind it. Named by number, an imported proposal named
    this home's captures."""
    from personalclaw.learning import proposals
    from personalclaw.resilience.degraded import _memory_staging_drain

    there = _home(tmp_path / "there", monkeypatch)
    compiled = ["there prefers metric units", "there writes dates day first"]
    store = StagingStore(there)
    for content in compiled:
        store.stage(cadence="per_turn", kind="lesson", content=content)
    store.close()
    assert asyncio.run(_memory_staging_drain()) == len(compiled)
    staging_mod.reset_store()
    (proposal,) = proposals.list_pending()
    archive = _export(there, tmp_path / "there.zip", monkeypatch)

    here = _home(tmp_path / "here", monkeypatch)
    store = StagingStore(here)
    for content in ("here likes tables", "here asks for sources"):
        store.stage(cadence="per_turn", kind="lesson", content=content)
    store.close()
    apply_import_zip(archive, "merge")

    store = StagingStore(here)
    try:
        sources = store.sources_for(proposals.get(proposal.id).staging_refs)
    finally:
        store.close()
    assert sorted(s["content_hash"] for s in sources) == sorted(fingerprint(c) for c in compiled)


def test_the_curators_changes_list_newest_first_after_a_merge(tmp_path, monkeypatch):
    """A row's number says when it arrived in this home; the curator's journal reads its changes
    newest first by when each was made."""
    there = tmp_path / "there"
    there.mkdir()
    journal = MutationLog(there)
    for n, at in enumerate((THERE_AT, THERE_AT + 60)):
        journal.append(Mutation(operation="age", kind="skill", entity=f"there {n}", at=_iso(at)))
    journal.close()
    here = tmp_path / "here"
    here.mkdir()
    journal = MutationLog(here)
    journal.append(Mutation(operation="archive", kind="skill", entity="here 0", at=_iso(HERE_AT)))
    journal.close()
    sent = tmp_path / "sent"
    export_shards(there, sent, for_sync=True)

    assert make_db_merger(here)(_learning_log(), sent) == CONSUMED

    journal = MutationLog(here)
    try:
        assert [c["entity"] for c in journal.changelog()] == ["here 0", "there 1", "there 0"]
        assert [m.entity for _id, m in journal.pending_undo()] == ["here 0", "there 1", "there 0"]
    finally:
        journal.close()


# ── a log written before its rows had identities ────────────────────────────────────────────

#: The learning log's numbered tables as an earlier version created them: numbers, and nothing else
#: that tells one row from another.
_EARLIER_TABLES = """
    CREATE TABLE staging (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, cadence TEXT NOT NULL,
        kind TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
        session_key TEXT NOT NULL DEFAULT '', created_ts REAL NOT NULL,
        meta TEXT NOT NULL DEFAULT '{}', consumed_by TEXT
    );
    CREATE TABLE flush_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cadence TEXT NOT NULL, outcome TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '', staged_count INTEGER NOT NULL DEFAULT 0,
        proposal_ids TEXT NOT NULL DEFAULT '[]', cost_usd REAL NOT NULL DEFAULT 0.0,
        created_ts REAL NOT NULL
    );
    CREATE TABLE allocation_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT, used_tokens INTEGER NOT NULL,
        budget_tokens INTEGER NOT NULL, created_ts REAL NOT NULL
    );
    CREATE TABLE ablation_sweeps (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sweep_ts REAL NOT NULL, heuristic TEXT NOT NULL,
        delta REAL NOT NULL, verdict TEXT NOT NULL, items INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE curator_mutations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, kind TEXT NOT NULL,
        entity TEXT NOT NULL, before TEXT NOT NULL DEFAULT '{}', after TEXT NOT NULL DEFAULT '{}',
        at TEXT NOT NULL, undone_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE surfacing_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity TEXT NOT NULL DEFAULT '',
        arm TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.0,
        used INTEGER NOT NULL DEFAULT 0, query TEXT NOT NULL DEFAULT '',
        session TEXT NOT NULL DEFAULT '', created_ts REAL NOT NULL
    );
"""


def _earlier_rows(conn: sqlite3.Connection, number: int, tag: str, at: float) -> None:
    """One row in each numbered table, numbered *number*, as an earlier version wrote it."""
    conn.execute(
        "INSERT INTO staging VALUES (?, '2026-09-21', 'per_turn', 'lesson', ?, ?, '', ?, '{}', ?)",
        (number, f"{tag} capture", fingerprint(f"{tag} capture"), at, f"batch-{tag}"),
    )
    conn.execute(
        "INSERT INTO flush_records VALUES (?, 'per_turn', 'flush_ok', ?, 0, '[]', 0.0, ?)",
        (number, f"{tag} pass", at),
    )
    conn.execute("INSERT INTO allocation_samples VALUES (?, 40, 100, ?)", (number, at))
    conn.execute(
        "INSERT INTO ablation_sweeps VALUES (?, ?, ?, 0.5, 'keep', 2)", (number, at, f"{tag} arm")
    )
    conn.execute(
        "INSERT INTO curator_mutations VALUES (?, 'age', 'skill', ?, '{}', '{}', ?, '')",
        (number, f"{tag} skill", _iso(at)),
    )
    conn.execute(
        "INSERT INTO surfacing_events VALUES (?, 'skill', ?, 'lexical', 0.5, 1, ?, ?, ?)",
        (number, f"{tag} skill", f"{tag} question", f"{tag} session", at),
    )


def _earlier_log(path: Path, rows: list[tuple[int, str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_EARLIER_TABLES)
        for number, tag, at in rows:
            _earlier_rows(conn, number, tag, at)
        conn.commit()
    finally:
        conn.close()


def _opened(home: Path) -> None:
    """The log opened by each store that keeps a table in it, as the gateway's work opens it."""
    for store in (StagingStore(home), MutationLog(home), SurfacingEventStore(home)):
        try:
            if isinstance(store, StagingStore):
                store.pending_count()
            else:
                store._ensure()
        finally:
            store.close()


_NUMBERED = (
    "staging",
    "flush_records",
    "allocation_samples",
    "ablation_sweeps",
    "curator_mutations",
    "surfacing_events",
)


def test_a_log_an_earlier_version_wrote_gets_its_identities_when_it_opens(tmp_path):
    here, copy = tmp_path / "here", tmp_path / "copy"
    _earlier_log(here / "learning.db", [(1, "one", THERE_AT), (2, "two", THERE_AT + 1)])
    shutil.copytree(here, copy)

    _opened(here)
    given = {table: _identities(here, table) for table in _NUMBERED}
    for table, ids in given.items():
        assert len(ids) == 2 and all(ids) and len(set(ids)) == 2, table

    _opened(here)
    assert {table: _identities(here, table) for table in _NUMBERED} == given, "opened again"
    _opened(copy)
    assert {t: _identities(copy, t) for t in _NUMBERED} == given, "the same rows in another home"


def test_an_archive_an_earlier_version_wrote_merges_without_doubling_what_both_hold(
    tmp_path, monkeypatch
):
    """The other home took this home's log once and added a row of its own; it still runs an
    earlier version, so its snapshot's rows have no identities. This home has opened its log
    since, and added a row under the same number. The rows both homes hold come in once."""
    there = _home(tmp_path / "there", monkeypatch)
    shared = [(1, "shared one", THERE_AT), (2, "shared two", THERE_AT + 1)]
    _earlier_log(there / "learning.db", [*shared, (3, "there", THERE_AT + 2)])
    tarball = _snapshot_of(there, tmp_path / "snapshots", monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    _earlier_log(here / "learning.db", shared)
    _opened(here)
    _write_log(here, "here", at=HERE_AT, captures=1)
    before = _log(here)
    theirs = _log(there)

    assert snapshot.restore_merge(tarball, None)["left_unchanged"] == []

    merged = _log(here)
    for what in _READS:
        mine = set(before[what])
        assert set(merged[what]) == mine | set(theirs[what]), what
        assert len(merged[what]) == len(mine | set(theirs[what])), f"{what}: a row came in twice"


def test_a_sync_leaves_the_copy_another_machine_sent_as_it_was(tmp_path):
    """The merge gives an earlier version's rows their identities in a private copy: the copy the
    other machine sent, which every machine syncing with it reads, is never written."""
    here = tmp_path / "here"
    here.mkdir()
    _write_log(here, "here", at=HERE_AT)
    sent = tmp_path / "sent"
    _earlier_log(sent / "db" / "learning_db.db", [(1, "there", THERE_AT)])
    before = (sent / "db" / "learning_db.db").read_bytes()

    assert make_db_merger(here)(_learning_log(), sent) == CONSUMED

    assert (sent / "db" / "learning_db.db").read_bytes() == before
    assert ("there capture", "lesson", "batch-there") in _log(here)["captures"]
