"""A record deleted on one machine stays deleted, there and on the other, for every store a sync
merges record by record.

🔴 A pull took a record the other machine still held for one this machine did not have, so a delete
came undone at the next pull of a copy the other machine made before the delete reached it, and the
copy this machine sent next carried the record again: the delete was lost on both. Only tasks and
projects kept a delete marker, which a pull never read; every other store kept none, so a deleted
automation, prompt or tag came back from the other machine's next copy, on every machine.

The rule now: a delete stands against every version of the record the deleting machine held before
it, and rides that machine's copies, so the other machine deletes the record too. A version the
deleting machine never held is an edit its delete never saw — made after the delete, or before it
arrived — and a conflict for review on both machines: the record stays deleted where it was deleted
and as edited where it was edited until she chooses. Nothing reads as deleted that could not be
read, and the machine that took a delete in does not send it back.

Driven end to end through ``run_sync_cycle``: two homes and the shared in-memory store the sync
tests use, and a real folder for the store APIs' own deletes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability.ancestors import Ancestors
from personalclaw.durability.conflict_resolve import resolve_conflict
from personalclaw.durability.sync_cycle import run_sync_cycle
from tests.test_an_edit_on_one_machine_reaches_the_other import MERGED_BY_ID, RID, _record
from tests.test_durability_conflict_review import _app
from tests.test_durability_convergence_e2e import FolderTransport
from tests.test_durability_sync_cycle import SharedStore


def _cycle(store, home: Path, name: str, now: str = "now"):
    report = run_sync_cycle(store, home, self_id=name, now=now)
    assert report.ok, report.error
    return report


def _agree(store, a: Path, b: Path) -> None:
    """A publishes, B takes it in and publishes, A pulls B's: the two hold the same copy."""
    for home, name in ((a, "A"), (b, "B"), (a, "A")):
        _cycle(store, home, name)


#: The id a store of records gives ``rec-1`` where it is not the record's own (a view's).
_RECORD_IDS = {"dashboard_views": f"view:{RID}"}


def _holds(entry_id: str, home: Path) -> bool:
    """Whether *home* holds ``rec-1`` in the store *entry_id*."""
    entry = inv.by_id(entry_id)
    assert entry is not None
    target = home / entry.path
    if entry.kind == inv.KIND_JSON_ENTITY_DIR:
        return (target / f"{RID}.json").exists()
    if not target.exists():
        return False
    assert entry.records is not None
    rid = _RECORD_IDS.get(entry_id, RID)
    records = entry.records.records(json.loads(target.read_text())) or []
    return any(r.get("id") == rid for r in records)


def _delete(entry_id: str, home: Path) -> None:
    """Delete ``rec-1`` from the store *entry_id* in *home*, as the store itself does: its file
    unlinked, or its record taken out of the store's one file. Nothing else is written."""
    entry = inv.by_id(entry_id)
    assert entry is not None
    target = home / entry.path
    if entry.kind == inv.KIND_JSON_ENTITY_DIR:
        (target / f"{RID}.json").unlink()
        return
    assert entry.records is not None
    rid = _RECORD_IDS.get(entry_id, RID)
    document = json.loads(target.read_text())
    kept = [r for r in entry.records.records(document) or [] if r.get("id") != rid]
    target.write_text(json.dumps(entry.records.document(document, kept)), encoding="utf-8")


def _change_something_else(home: Path, n: int = 1) -> None:
    """Change a record other than ``rec-1``, so the home's next sync sends a new copy."""
    tasks = home / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"other-{n}.json").write_text(json.dumps({"id": f"other-{n}", "n": n}), "utf-8")


# ── the delete stays made ─────────────────────────────────────────────────────


@pytest.mark.parametrize("entry_id", MERGED_BY_ID)
def test_a_delete_stays_made_when_the_other_machines_older_copy_arrives(tmp_path, entry_id):
    record = _record(entry_id)
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    record.write(a, "made on A")
    _agree(store, a, b)
    assert _holds(entry_id, b), "the record never reached B — the fixture is vacuous"

    _delete(entry_id, a)
    # B sends a copy before A's delete reaches it: B still holds the record, as A held it.
    _change_something_else(b)
    _cycle(store, b, "B")
    _cycle(store, a, "A")
    assert not _holds(entry_id, a), "B's older copy brought the record back on A"

    _cycle(store, b, "B")
    assert not _holds(entry_id, b), "A's delete did not reach B"
    _cycle(store, a, "A")
    assert not _holds(entry_id, a) and not _holds(entry_id, b)
    # The other machine only lagged behind the delete: nothing waits for review on either.
    assert conflicts_mod.ConflictQueue(a).items() == []
    assert conflicts_mod.ConflictQueue(b).items() == []


# ── an edit the delete never saw ──────────────────────────────────────────────


def _deleted_on_a_edited_on_b(tmp_path, entry_id: str = "tasks"):
    """A deletes ``rec-1`` while B, not yet having A's delete, edits it; then a cycle each."""
    record = _record(entry_id)
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    record.write(a, "made on A")
    _agree(store, a, b)
    _delete(entry_id, a)
    record.write(b, "edited on B")
    _cycle(store, b, "B")
    on_a = _cycle(store, a, "A")
    on_b = _cycle(store, b, "B")
    return store, a, b, record, on_a, on_b


@pytest.mark.parametrize("entry_id", ["tasks", "triggers", "tags"])
def test_an_edit_made_after_the_delete_is_a_conflict_on_both_machines(tmp_path, entry_id):
    _store, a, b, record, on_a, on_b = _deleted_on_a_edited_on_b(tmp_path, entry_id)

    # On A: deleted here, and B's edit is held for review, not brought back.
    assert not _holds(entry_id, a), "B's edit brought back a record A deleted"
    assert on_a.conflicts == 1
    [here] = conflicts_mod.ConflictQueue(a).items()
    assert (here.entry_id, here.entity_id, here.deleted) == (entry_id, RID, "here")
    assert here.local_row.get("deleted_at") and here.status == "needs-review"

    # On B: A's delete never saw B's edit, so the edit stays and the delete waits for review.
    assert record.text(b) == "edited on B", "A's delete dropped an edit it never saw"
    assert on_b.conflicts == 1
    [there] = conflicts_mod.ConflictQueue(b).items()
    assert (there.entity_id, there.deleted) == (RID, "there")
    assert there.remote_row.get("deleted_at")


def test_the_conflict_holds_on_later_cycles_until_she_chooses(tmp_path):
    store, a, b, record, _on_a, _on_b = _deleted_on_a_edited_on_b(tmp_path)
    for _ in range(2):
        _cycle(store, a, "A")
        _cycle(store, b, "B")
    assert not _holds("tasks", a) and record.text(b) == "edited on B"
    assert len(conflicts_mod.ConflictQueue(a).items()) == 1
    assert len(conflicts_mod.ConflictQueue(b).items()) == 1


def test_bringing_back_the_other_machines_version_takes_the_edit_in(tmp_path):
    store, a, b, record, _on_a, _on_b = _deleted_on_a_edited_on_b(tmp_path)
    [rec] = conflicts_mod.ConflictQueue(a).items()

    outcome = resolve_conflict(a, rec.id, "take_remote")

    assert outcome.ok, outcome.message
    assert record.text(a) == "edited on B"
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    _cycle(store, a, "A")
    assert record.text(a) == record.text(b) == "edited on B"
    assert [r.status for r in conflicts_mod.ConflictQueue(a).items()] == ["resolved"]


def test_keeping_it_deleted_sticks(tmp_path):
    store, a, b, _record_, _on_a, _on_b = _deleted_on_a_edited_on_b(tmp_path)
    [rec] = conflicts_mod.ConflictQueue(a).items()

    assert resolve_conflict(a, rec.id, "keep_local").ok
    _change_something_else(b, 5)  # B sends a new copy that still holds its edit
    _cycle(store, b, "B")
    _cycle(store, a, "A")

    assert not _holds("tasks", a), "a decision to keep it deleted was undone by the next copy"
    assert [r.status for r in conflicts_mod.ConflictQueue(a).items()] == ["resolved"]


def test_taking_the_other_machines_delete_deletes_the_edit_here(tmp_path):
    _store, _a, b, _record_, _on_a, _on_b = _deleted_on_a_edited_on_b(tmp_path)
    [rec] = conflicts_mod.ConflictQueue(b).items()

    outcome = resolve_conflict(b, rec.id, "take_remote")

    assert outcome.ok, outcome.message
    assert not _holds("tasks", b)
    assert outcome.note == "", "a delete is not a version that arrives by a store's rule"


@pytest.mark.asyncio
async def test_the_review_says_which_side_deleted_it(tmp_path):
    from personalclaw.config.loader import config_dir

    store = SharedStore()
    a, b = tmp_path / "A", config_dir()
    tasks = _record("tasks")
    tasks.write(a, "made on A")
    _agree(store, a, b)
    _delete("tasks", b)
    tasks.write(a, "edited on A")
    _cycle(store, a, "A")
    _cycle(store, b, "B")

    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/durability/conflicts")
        assert resp.status == 200
        body = await resp.json()
    [listed] = body["conflicts"]
    assert (listed["entity_id"], listed["deleted"]) == (RID, "here")
    assert listed["remote_row"]["data"]["title"] == "edited on A"
    assert listed["arrival"] == ""


# ── what a delete is, and what it is not ─────────────────────────────────────


def test_a_file_that_cannot_be_read_is_not_a_delete(tmp_path):
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    _record("tasks").write(a, "made on A")
    _agree(store, a, b)

    (a / "tasks" / f"{RID}.json").write_text("{half a record", encoding="utf-8")
    _cycle(store, a, "A")
    _cycle(store, b, "B")

    assert _holds("tasks", b), "a file A could not read deleted the record on B"
    assert Ancestors(a / "sync").deletions() == {}


def test_a_store_that_is_not_there_deletes_nothing(tmp_path):
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    _record("tasks").write(a, "made on A")
    _agree(store, a, b)

    (a / "tasks" / f"{RID}.json").rename(tmp_path / "moved-away.json")
    (a / "tasks").rmdir()
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    assert _holds("tasks", b), "a store missing on A read as every record deleted"

    _change_something_else(b)
    _cycle(store, b, "B")
    _cycle(store, a, "A")
    assert _holds("tasks", a), "nothing was known of the missing store, so B's copy comes back"


def test_the_machine_that_took_a_delete_does_not_send_it_back(tmp_path):
    """B deleted the task because A did. A then brings it back, as a restore of a snapshot that
    holds it would: B takes it back in, rather than deleting it on A again as if B had."""
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    tasks = _record("tasks")
    tasks.write(a, "made on A")
    _agree(store, a, b)
    kept = (a / "tasks" / f"{RID}.json").read_bytes()

    _delete("tasks", a)
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    assert not _holds("tasks", b)
    assert Ancestors(b / "sync").deletions() == {}, "B's copies would send A's delete back"

    (a / "tasks" / f"{RID}.json").write_bytes(kept)
    _cycle(store, a, "A")
    _cycle(store, b, "B")
    _cycle(store, a, "A")
    assert _holds("tasks", a) and _holds("tasks", b)
    assert conflicts_mod.ConflictQueue(a).items() == conflicts_mod.ConflictQueue(b).items() == []


def test_a_delete_is_sent_in_one_copy_and_then_that_copy_stands(tmp_path):
    """A delete changes what this machine's copy holds once: the next sync with nothing new sends
    no copy, as for any unchanged home."""
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    _record("tasks").write(a, "made on A")
    _agree(store, a, b)

    _delete("tasks", a)
    sent = _cycle(store, a, "A")
    again = _cycle(store, a, "A")

    assert sent.seq_published, "the delete was not sent"
    assert again.unchanged_since == sent.seq_published, again.detail


def test_the_retired_delete_side_log_is_removed_and_never_sent(tmp_path):
    store = SharedStore()
    a = tmp_path / "A"
    _record("tasks").write(a, "made on A")
    side_log = a / "tasks" / "_tombstones.jsonl"
    side_log.write_text('{"id": "gone", "deleted_at": "2026-09-01T00:00:00+00:00"}\n', "utf-8")

    _cycle(store, a, "A")

    assert not side_log.exists()
    assert not any(b"_tombstones.jsonl" in data for data in store.objects.values())


# ── a store's own delete, through its own API, over a real folder ────────────


def _in_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))


def test_a_task_deleted_by_its_store_is_deleted_on_the_other_machine(tmp_path, monkeypatch):
    from personalclaw.tasks.native import NativeTaskProvider, _tasks_dir

    transport = FolderTransport(tmp_path / "shared")
    a, b = tmp_path / "A", tmp_path / "B"
    _in_home(monkeypatch, a)
    task = asyncio.run(NativeTaskProvider().create_task(title="doomed"))
    _agree(transport, a, b)
    assert (b / "tasks" / f"{task.id}.json").exists()

    _in_home(monkeypatch, a)
    assert asyncio.run(NativeTaskProvider().delete_task(task.id)) is True
    assert not (_tasks_dir() / f"{task.id}.json").exists()
    _cycle(transport, a, "A")
    _cycle(transport, b, "B")

    assert not (b / "tasks" / f"{task.id}.json").exists()


def test_a_project_deleted_by_its_store_goes_whole_on_the_other_machine(tmp_path, monkeypatch):
    from personalclaw.tasks.hierarchy import HierarchyStore

    transport = FolderTransport(tmp_path / "shared")
    a, b = tmp_path / "A", tmp_path / "B"
    _in_home(monkeypatch, a)
    hierarchy = HierarchyStore()
    project = hierarchy.create_project(name="doomed")
    listed = hierarchy.create_task_list(project_id=project.id, name="sprint-1")
    (hierarchy.context_dir(project.id) / "brief.json").write_text('{"note": "x"}', "utf-8")
    (hierarchy.context_dir(project.id) / "notes.md").write_text("plain words\n", "utf-8")
    _agree(transport, a, b)
    there = b / "projects" / project.id
    assert (there / "project.json").exists() and (there / "context" / "notes.md").exists()
    assert (b / "tasks" / "task_lists" / f"{listed.id}.json").exists()

    _in_home(monkeypatch, a)
    assert HierarchyStore().delete_project(project.id) is True
    _cycle(transport, a, "A")
    _cycle(transport, b, "B")

    assert not (there / "project.json").exists()
    assert not (there / "context" / "brief.json").exists()
    assert not (there / "context" / "notes.md").exists(), "a file that is not JSON stayed"
    assert not (b / "tasks" / "task_lists" / f"{listed.id}.json").exists()
    _in_home(monkeypatch, b)
    assert project.id not in {p.id for p in HierarchyStore().list_projects()}


def test_a_saved_prompt_file_deleted_on_one_machine_goes_on_the_other(tmp_path):
    """A saved prompt is a YAML file, not JSON: its delete removes that file."""
    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    (a / "prompts").mkdir(parents=True)
    (a / "prompts" / "standup.yaml").write_text("title: Standup\n", "utf-8")
    _agree(store, a, b)
    assert (b / "prompts" / "standup.yaml").exists()

    (a / "prompts" / "standup.yaml").unlink()
    _cycle(store, a, "A")
    _cycle(store, b, "B")

    assert not (b / "prompts" / "standup.yaml").exists()


def test_a_delete_is_forgotten_only_after_a_copy_carried_it(tmp_path):
    """The horizon counts from the delete, but a delete is never forgotten unsent: while this
    machine's copies do not reach the store, it is kept, and the first copy that lands carries it.
    """
    from datetime import datetime, timedelta, timezone

    from personalclaw.durability.ancestors import DELETE_HORIZON_SECS
    from personalclaw.sync_transports.base import PushResult

    class Unreachable(SharedStore):
        down = False

        def push(self, objects):
            if self.down:
                return PushResult(outcome="transient", detail="the folder is not mounted")
            return super().push(objects)

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    at = lambda days: (start + timedelta(days=days)).isoformat()  # noqa: E731
    store = Unreachable()
    a, b = tmp_path / "A", tmp_path / "B"
    _record("tasks").write(a, "made on A")
    for home, name, day in ((a, "A", 0), (b, "B", 0), (a, "A", 0)):
        _cycle(store, home, name, at(day))
    _delete("tasks", a)

    store.down = True
    past = DELETE_HORIZON_SECS // 86400 + 2
    for day in (1, past):  # the delete is noticed on day 1; the store stays down past the horizon
        assert run_sync_cycle(store, a, self_id="A", now=at(day)).failure == "push"
    assert RID in Ancestors(a / "sync").deleted("tasks"), "forgotten before any copy carried it"

    store.down = False
    _cycle(store, a, "A", at(past + 1))
    assert RID not in Ancestors(a / "sync").deleted("tasks"), "kept past the horizon once sent"
    _cycle(store, b, "B", at(past + 1))
    assert not _holds("tasks", b), "the copy that landed did not carry the delete"
