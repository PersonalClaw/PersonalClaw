"""A pull writes back only what the merge changed, and only over a file still as it was read.

🔴 A pull read a folder store, merged the other machine's rows in, and rewrote EVERY file of the
folder from what it had read. A write the store made to any file in between — a task saved, a theme
edited while the pull ran — was written away, and the next export told the other machine the old
version was current. A one-file append-only stream was rewritten the same way, so a line appended
meanwhile was lost; and a folder of append-only files, one per chat or per job, was rewritten as a
file per year beside them: a chat, or a job's history, named for the year.

Now the write is only what changed, and each file is replaced only while it still holds what the
pull read (compare-and-swap on its sha256): one this machine wrote in between is left as it is and
is not agreed on, so the next pull measures the other machine's version against it again. A one-file
stream is only appended to. A folder of append-only files is neither sent nor taken in. And a row
the other machine names by a path that is not a file of the store is never written.

Each case below puts the store's own write between the pull's read and its write, by running it as
the pull reaches its write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.durability import conflict_resolve as resolver
from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile, writeback
from personalclaw.durability.cursor import CONSUMED
from personalclaw.durability.shards import export_shards, import_shards

TASKS = inv.by_id("tasks")
NOTIFICATIONS = inv.by_id("notifications")


def _task(home: Path, tid: str, title: str) -> Path:
    path = home / "tasks" / f"{tid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": tid, "title": title}), encoding="utf-8")
    return path


def _meanwhile(monkeypatch, write) -> None:
    """Run the store's own *write* when the pull reaches its write — between its read and it."""
    original = writeback.apply_rows

    def apply_rows(*args, **kwargs):
        write()
        return original(*args, **kwargs)

    monkeypatch.setattr(writeback, "apply_rows", apply_rows)


def test_a_file_the_store_wrote_meanwhile_is_not_written_away(tmp_path, monkeypatch):
    mine = _task(tmp_path, "t1", "as it was")
    _task(tmp_path, "t2", "untouched")
    _meanwhile(monkeypatch, lambda: _task(tmp_path, "t1", "saved while the pull ran"))
    theirs = [{"id": "t3", "data": {"id": "t3", "title": "made there"}}]
    result = reconcile.reconcile_entry(tmp_path, TASKS, theirs)
    assert result.verdict == CONSUMED, result.detail
    assert json.loads(mine.read_text())["title"] == "saved while the pull ran"
    assert (tmp_path / "tasks" / "t3.json").is_file(), "what the other machine made still arrives"


def test_only_the_files_the_merge_changed_are_written(tmp_path):
    kept = [_task(tmp_path, tid, "mine") for tid in ("t1", "t2")]
    before = [(p.stat().st_ino, p.stat().st_mtime_ns) for p in kept]
    theirs = [{"id": "t3", "data": {"id": "t3", "title": "made there"}}]
    result = reconcile.reconcile_entry(tmp_path, TASKS, theirs)
    assert result.added == 1
    assert [(p.stat().st_ino, p.stat().st_mtime_ns) for p in kept] == before


def test_an_edit_that_lost_the_race_is_not_agreed_on(tmp_path, monkeypatch):
    # The other machine edited t1 (this machine holds the version the two agreed on), and this
    # machine saves t1 while the pull merges: its save stays, and t1 is not recorded as agreed,
    # so the next pull measures the other machine's edit against the save — a conflict to review
    # there, never a silent win for either side.
    mine = _task(tmp_path, "t1", "agreed")
    agreed = conflicts_mod.row_sha(
        conflicts_mod.compared(TASKS, {"id": "t1", "data": json.loads(mine.read_text())})
    )
    _meanwhile(monkeypatch, lambda: _task(tmp_path, "t1", "saved here meanwhile"))
    theirs = [{"id": "t1", "data": {"id": "t1", "title": "edited there"}}]
    result = reconcile.reconcile_entry(tmp_path, TASKS, theirs, ancestors={"t1": agreed})
    assert json.loads(mine.read_text())["title"] == "saved here meanwhile"
    assert "t1" not in result.new_ancestors


def test_a_line_appended_meanwhile_stays_and_the_new_ones_follow_it(tmp_path, monkeypatch):
    stream = tmp_path / NOTIFICATIONS.path
    lines = [{"id": "n1", "ts": "2026-09-01T00:00:00Z"}, {"id": "n2", "ts": "2026-09-02T00:00:00Z"}]
    stream.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")

    def append_one() -> None:
        with stream.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "n3", "ts": "2026-09-03T00:00:00Z"}) + "\n")

    _meanwhile(monkeypatch, append_one)
    theirs = [*lines, {"id": "n9", "ts": "2026-09-04T00:00:00Z"}]
    result = reconcile.reconcile_entry(tmp_path, NOTIFICATIONS, theirs)
    assert result.verdict == CONSUMED, result.detail
    ids = [json.loads(x)["id"] for x in stream.read_text().splitlines() if x.strip()]
    assert ids == ["n1", "n2", "n3", "n9"]


@pytest.mark.parametrize("entry_id", ["sessions", "cron_history", "channel_history"])
def test_a_folder_of_append_only_files_is_left_as_it_is(tmp_path, entry_id):
    entry = inv.by_id(entry_id)
    folder = tmp_path / entry.path
    folder.mkdir(parents=True)
    own = folder / "chat-1.jsonl"
    own.write_text(json.dumps({"ts": "2026-09-01T00:00:00Z", "text": "mine"}) + "\n")
    theirs = [{"ts": "2026-09-02T00:00:00Z", "text": "from another chat, on another machine"}]
    result = reconcile.reconcile_entry(tmp_path, entry, theirs)
    assert result.verdict == CONSUMED
    assert sorted(p.name for p in folder.iterdir()) == ["chat-1.jsonl"], "no file named for a year"
    assert own.read_text().count("\n") == 1


def test_a_sync_does_not_send_a_folder_of_append_only_files(tmp_path):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "chat-1.jsonl").write_text(
        json.dumps({"ts": "2026-09-01T00:00:00Z", "text": "a chat"}) + "\n"
    )
    backup = export_shards(tmp_path, tmp_path / "backup")
    synced = export_shards(tmp_path, tmp_path / "sync", for_sync=True)
    assert any(s.path.startswith("sessions/") for s in backup.shards), "a backup still holds it"
    assert not any(s.path.startswith("sessions/") for s in synced.shards)


@pytest.mark.parametrize("rid", ["../escaped", "../../escaped", "a/../../escaped"])
def test_a_row_naming_a_path_outside_the_store_is_never_written(tmp_path, rid):
    home = tmp_path / "home"
    _task(home, "t1", "mine")
    theirs = [{"id": rid, "data": {"id": "x", "title": "outside"}}]
    reconcile.reconcile_entry(home, TASKS, theirs)
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json"))
    assert written == ["home/tasks/t1.json"]


def test_a_peers_run_record_named_as_a_workflow_file_is_never_written(tmp_path):
    entry = inv.by_id("workflows")
    theirs = [
        {"id": "runs/run-1/state", "data": {"status": "running"}},
        {"id": "defs/digest/workflow", "data": {"name": "digest", "nodes": []}},
    ]
    reconcile.reconcile_entry(tmp_path, entry, theirs)
    assert not (tmp_path / "workflows" / "runs").exists()
    assert (tmp_path / "workflows" / "defs" / "digest" / "workflow.json").is_file()


def test_the_review_writes_nothing_over_a_file_changed_while_it_wrote(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    mine = _task(tmp_path, "t1", "mine")
    record = conflicts_mod.ConflictRecord(
        entry_id="tasks",
        entity_id="t1",
        domain=TASKS.domain,
        surface=conflicts_mod.SURFACE_DURABILITY,
        ancestor_sha="a",
        local_sha="l",
        remote_sha="r",
        local_row={"id": "t1", "data": {"id": "t1", "title": "mine"}},
        remote_row={"id": "t1", "data": {"id": "t1", "title": "theirs"}},
    )
    queue = conflicts_mod.ConflictQueue(tmp_path)
    assert queue.record(record)
    _meanwhile(monkeypatch, lambda: _task(tmp_path, "t1", "saved while the review wrote"))
    outcome = resolver.resolve_conflict(tmp_path, record.id, resolver.CHOICE_TAKE_REMOTE)
    assert json.loads(mine.read_text())["title"] == "saved while the review wrote"
    assert not outcome.ok and outcome.code == "moved"
    assert queue.get(record.id).status == conflicts_mod.STATUS_NEEDS_REVIEW


def test_a_pull_that_brings_nothing_new_writes_nothing(tmp_path):
    # Two machines with the same task: the pull finds nothing to change, and changes nothing.
    a, b = tmp_path / "A", tmp_path / "B"
    for home in (a, b):
        _task(home, "t1", "same")
    export_shards(a, tmp_path / "out", for_sync=True)
    rows = import_shards(tmp_path / "out").rows["tasks"]
    before = (b / "tasks" / "t1.json").stat().st_ino
    result = reconcile.reconcile_entry(b, TASKS, rows)
    assert result.verdict == CONSUMED and result.added == 0
    assert (b / "tasks" / "t1.json").stat().st_ino == before
