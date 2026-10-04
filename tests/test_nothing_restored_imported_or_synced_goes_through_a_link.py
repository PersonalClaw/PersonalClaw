"""A restore, an import and a sync write only inside the home, never through a link the home holds.

Where the home holds a link at a path the archive or another machine brings — a symbolic link at
the file, a symbolic link at a folder on the way to it, or a file with another name (a hard link)
— that item is left exactly as it is: nothing of it is written, nothing is written through the
link, nothing of it is moved aside, and the result names the path. Every link here leads to a
scratch folder outside the home, and every door is held to the same thing: that folder is exactly
as it was afterwards.

What went wrong before, each held here:

* A replace restore moved each single-file store aside and left a link where it found one, then
  copied the snapshot's file to that path, which wrote it wherever the link led. A link at a
  folder made the replace stop half way with an error.
* A merge restore and an import merged a database into whatever a link at its path led to, and
  copied the archive's files through a folder that was a link.
* A sync's pull wrote another machine's rows into a store whose folder was a link, appended its
  log lines to whatever a link at a log led to, and merged its database through one.
* The conflict review wrote the other machine's version through a store's link too.

The controls run the same doors with the same items as real files and folders, and each comes in
as before.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.durability.cursor import Cursor
from personalclaw.durability.registry import shard_prefix
from personalclaw.durability.sync_cycle import run_sync_cycle
from personalclaw.snapshot import restore_main, restore_merge
from tests.test_durability_sync_cycle import SharedStore

#: Why an item was left as it is, as the result says it.
A_LINK = (
    "a symbolic link in this home, which nothing restored, imported or synced is written through"
)
A_HARD_LINK = (
    "a hard link in this home (a file with another name), which nothing restored, imported or "
    "synced is written through"
)


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", lambda: None)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    (home / "config.json").write_text("{}", encoding="utf-8")
    return home


@pytest.fixture
def outside(tmp_path) -> Path:
    """A scratch folder outside the home: where every link here leads."""
    folder = (tmp_path / "outside").resolve()
    folder.mkdir()
    return folder


def _state(folder: Path) -> dict[str, bytes]:
    """Every folder and file under *folder*, a file with its bytes: what "exactly as it was"
    compares."""
    return {
        p.relative_to(folder).as_posix()
        + ("/" if p.is_dir() else ""): (b"" if p.is_dir() else p.read_bytes())
        for p in sorted(folder.rglob("*"))
    }


def _database(path: Path, rows: set[str]) -> bytes:
    """A database holding *rows* in a ``notes`` table, as one file; returns its bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT OR IGNORE INTO notes VALUES (?, 'a line')", [(r,) for r in rows])
    conn.commit()
    conn.close()
    return path.read_bytes()


def _notes(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {row[0] for row in conn.execute("SELECT id FROM notes")}
    finally:
        conn.close()


def _snapshot_archive(path: Path, files: dict[str, bytes]) -> Path:
    """A snapshot archive holding *files*, as ``personalclaw snapshot`` lays one out."""
    files = {"MANIFEST.json": b'{"version": 3}', **files}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        stage = Path(work) / "personalclaw-snapshot-20260101T000000Z"
        for rel, data in files.items():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            (stage / rel).write_bytes(data)
        with tarfile.open(path, "w:gz") as tar:
            tar.add(stage, arcname=stage.name)
    return path


def _export_archive(path: Path, files: dict[str, bytes]) -> Path:
    """An export archive holding *files*, as Settings → Import / Export writes one."""
    files = {"MANIFEST.json": b'{"version": 1}', **files}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(f"personalclaw-export-20260101T000000Z/{rel}", data)
    return path


def _restore(archive: Path, mode: str, capsys) -> tuple[int, str]:
    """``personalclaw restore <archive> --mode <mode>``: its status and what it printed."""
    code = restore_main([str(archive), "--mode", mode])
    return code, capsys.readouterr().out


def _named(what: str, rel: str, why: str = A_LINK) -> bool:
    return f"{rel} ({why})" in what


# ── a replace restore ────────────────────────────────────────────────────────────────────────────

#: Single-file stores a replace restore puts back: one of the named components' files, and two of
#: the stores it reaches through the inventory, a settings file and a database.
SINGLE_FILES = ["config.json", "tool_prefs.json", "learning.db"]


@pytest.mark.parametrize("dangling", [False, True], ids=["to-a-file", "to-nothing"])
@pytest.mark.parametrize("rel", SINGLE_FILES)
def test_a_replace_restore_writes_no_single_file_store_through_a_link(
    home, outside, tmp_path, capsys, rel, dangling
):
    """🔴 Red on integration: the replace moved the store aside, left the link where it was, and
    copied the snapshot's file to its path, so the file landed wherever the link led."""
    target = outside / rel
    if not dangling:
        target.write_bytes(b"a file of the user's, outside the home\n")
    (home / rel).unlink(missing_ok=True)
    (home / rel).symlink_to(target)
    before = _state(outside)
    archive = _snapshot_archive(
        tmp_path / "snap.tar.gz",
        {rel: b"the snapshot's copy\n", "active_models.json": b'{"chat": "local:small"}'},
    )

    code, out = _restore(archive, "replace", capsys)

    assert _state(outside) == before, "the snapshot's copy was written outside the home"
    assert (home / rel).is_symlink() and os.readlink(home / rel) == str(target)
    assert code == 1, "a replace that left a part unchanged ended as a whole one"
    assert _named(out, rel), out
    assert "Replace finished, but 1 part was left unchanged" in out
    # The rest of the snapshot is restored.
    assert (home / "active_models.json").read_bytes() == b'{"chat": "local:small"}'


def test_a_replace_restore_writes_nothing_through_a_folder_that_is_a_link(
    home, outside, tmp_path, capsys
):
    """🔴 Red on integration: a store whose folder was a link stopped the replace half way, with
    an error, after it had moved the stores before it aside."""
    (outside / "tasks").mkdir()
    (outside / "tasks" / "kept.json").write_text('{"id": "kept"}', encoding="utf-8")
    (home / "tasks").symlink_to(outside / "tasks", target_is_directory=True)
    before = _state(outside)
    archive = _snapshot_archive(
        tmp_path / "snap.tar.gz",
        {"tasks/t1.json": b'{"id": "t1"}', "tool_prefs.json": b'{"x": 1}'},
    )

    code, out = _restore(archive, "replace", capsys)

    assert _state(outside) == before
    assert (home / "tasks").is_symlink()
    assert code == 1 and _named(out, "tasks"), out
    assert (home / "tool_prefs.json").read_bytes() == b'{"x": 1}'


def test_a_replace_restore_leaves_a_hard_link_as_it_is(home, outside, tmp_path, capsys):
    """A file the home holds under a second name is left as it is, the same file, and named."""
    shared = outside / "tool_prefs.json"
    shared.write_text('{"mine": true}', encoding="utf-8")
    os.link(shared, home / "tool_prefs.json")
    before = _state(outside)
    archive = _snapshot_archive(tmp_path / "snap.tar.gz", {"tool_prefs.json": b'{"x": 1}'})

    code, out = _restore(archive, "replace", capsys)

    assert _state(outside) == before
    assert os.stat(home / "tool_prefs.json").st_ino == os.stat(shared).st_ino
    assert code == 1 and _named(out, "tool_prefs.json", A_HARD_LINK), out


def test_a_replace_through_an_import_writes_no_store_through_a_link(home, outside, tmp_path):
    """🔴 Red on integration: an export archive imported to replace the home took the same path."""
    from personalclaw.portability import apply_import_zip

    (outside / "tool_prefs.json").write_text('{"mine": true}', encoding="utf-8")
    (home / "tool_prefs.json").symlink_to(outside / "tool_prefs.json")
    before = _state(outside)
    archive = _export_archive(tmp_path / "export.zip", {"tool_prefs.json": b'{"x": 1}'})

    summary = apply_import_zip(archive, "replace")

    assert _state(outside) == before
    assert (home / "tool_prefs.json").is_symlink()
    assert summary["left_unchanged"] == [f"tool_prefs.json ({A_LINK})"]


# ── a merge restore and an import ────────────────────────────────────────────────────────────────


def _merge_restore(home: Path, archive_dir: Path, files: dict[str, bytes]) -> list[str]:
    """The dashboard's merge restore: what it left unchanged."""
    return restore_merge(_snapshot_archive(archive_dir / "snap.tar.gz", files), None)[
        "left_unchanged"
    ]


def _import(home: Path, archive_dir: Path, files: dict[str, bytes]) -> list[str]:
    """An import's merge: what it left unchanged, each said among its lines too."""
    from personalclaw.portability import apply_import_zip

    summary = apply_import_zip(_export_archive(archive_dir / "export.zip", files), "merge")
    assert all(part in summary["items"] for part in summary["left_unchanged"] if "(a " in part)
    return summary["left_unchanged"]


MERGES = pytest.mark.parametrize("merge", [_merge_restore, _import], ids=["restore", "import"])


@MERGES
def test_a_merge_writes_no_database_through_a_link(home, outside, tmp_path, merge):
    """🔴 Red on integration: the merge took the link's target for this home's database and
    merged the archive's rows into it, outside the home."""
    _database(outside / "learning.db", {"theirs"})
    (home / "learning.db").symlink_to(outside / "learning.db")
    before = _state(outside)
    archived = _database(tmp_path / "archived" / "learning.db", {"archived"})

    left = merge(home, tmp_path, {"learning.db": archived})

    assert _state(outside) == before
    assert (home / "learning.db").is_symlink()
    assert f"learning.db ({A_LINK})" in left


@MERGES
def test_a_merge_writes_nothing_into_a_database_with_another_name(home, outside, tmp_path, merge):
    """🔴 Red on integration: the merge wrote the archive's rows into the database file this home
    shared with a name outside it."""
    _database(outside / "learning.db", {"theirs"})
    os.link(outside / "learning.db", home / "learning.db")
    before = _state(outside)
    archived = _database(tmp_path / "archived" / "learning.db", {"archived"})

    left = merge(home, tmp_path, {"learning.db": archived})

    assert _state(outside) == before
    assert _notes(home / "learning.db") == {"theirs"}
    assert f"learning.db ({A_HARD_LINK})" in left


@MERGES
def test_a_merge_writes_nothing_through_a_folder_that_is_a_link(home, outside, tmp_path, merge):
    """🔴 Red on integration: a folder of the workspace that was a link took the archive's file,
    which the merge put wherever the link led."""
    (home / "workspace").mkdir()
    (home / "workspace" / "notes").symlink_to(outside, target_is_directory=True)
    before = _state(outside)

    left = merge(home, tmp_path, {"workspace/notes/today.md": b"the archive's notes\n"})

    assert _state(outside) == before
    assert f"workspace/notes ({A_LINK})" in left


@MERGES
def test_a_merge_reads_and_writes_no_store_of_records_through_a_link(
    home, outside, tmp_path, merge
):
    """🔴 Red on integration: the merge read the automations the link led to as this home's and
    put a file of them in the link's place."""
    automations = {"triggers": [{"id": "elsewhere", "name": "Kept elsewhere"}]}
    (outside / "triggers.json").write_text(json.dumps(automations), encoding="utf-8")
    (home / "triggers.json").symlink_to(outside / "triggers.json")
    before = _state(outside)
    archived = {"triggers": [{"id": "brief", "name": "Morning brief", "enabled": True}]}

    left = merge(home, tmp_path, {"triggers.json": json.dumps(archived).encode()})

    assert _state(outside) == before
    assert (home / "triggers.json").is_symlink(), "the link was replaced by a file"
    assert f"triggers.json ({A_LINK})" in left


@MERGES
def test_a_merge_opens_no_lock_through_a_link(home, outside, tmp_path, merge):
    """🔴 Red on integration: a store of records is written under the lock beside it, which the
    merge opened for writing, emptying whatever a link there led to."""
    (outside / "kept.txt").write_text("a file of the user's\n", encoding="utf-8")
    (home / ".triggers.lock").symlink_to(outside / "kept.txt")
    before = _state(outside)
    archived = {"triggers": [{"id": "brief", "name": "Morning brief"}]}

    left = merge(home, tmp_path, {"triggers.json": json.dumps(archived).encode()})

    assert _state(outside) == before
    assert not (home / "triggers.json").exists()
    assert f".triggers.lock ({A_LINK})" in left


def test_the_merge_restore_command_says_what_a_link_kept_and_fails(home, outside, tmp_path, capsys):
    """At the terminal: the merge goes on to the rest, its last line names the link, and the
    command fails."""
    (home / "tool_prefs.json").symlink_to(outside / "tool_prefs.json")
    archive = _snapshot_archive(
        tmp_path / "snap.tar.gz",
        {"tool_prefs.json": b'{"x": 1}', "active_models.json": b'{"chat": "local:small"}'},
    )

    code, out = _restore(archive, "merge", capsys)

    assert not (outside / "tool_prefs.json").exists()
    assert code == 1
    assert f"Merge finished, but 1 part was left unchanged: tool_prefs.json ({A_LINK})." in out
    assert (home / "active_models.json").read_bytes() == b'{"chat": "local:small"}'


# ── a sync's pull ────────────────────────────────────────────────────────────────────────────────


def _publish(machine: Path, store: SharedStore) -> None:
    """Machine *machine*'s sync, published at seq 1."""
    assert run_sync_cycle(store, machine, self_id="B", now="t").ok
    assert f"{shard_prefix('B', 1)}manifest.json" in store.objects


def _machine_b(tmp_path: Path) -> Path:
    """Another machine: a task, a prompt, a notification and a learning log of its own."""
    b = tmp_path / "B"
    (b / "tasks").mkdir(parents=True)
    (b / "tasks" / "from-b.json").write_text('{"id": "from-b", "title": "from B"}')
    (b / "prompts").mkdir()
    (b / "prompts" / "weekly.yaml").write_text("name: weekly\n", encoding="utf-8")
    note = {"ts": "2026-01-01T00:00:00+00:00", "title": "from B"}
    (b / "notifications.jsonl").write_text(json.dumps(note) + "\n", encoding="utf-8")
    _database(b / "learning.db", {"from-b"})
    return b


@pytest.fixture
def scratch(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _pull(tmp_path: Path, plant) -> tuple[Path, object]:
    """Machine B publishes; machine A, holding what *plant* puts in its home, pulls."""
    store = SharedStore()
    _publish(_machine_b(tmp_path), store)
    a = tmp_path / "A"
    a.mkdir()
    plant(a)
    return a, run_sync_cycle(store, a, self_id="A", now="t2")


def test_a_pull_writes_no_row_into_a_store_whose_folder_is_a_link(tmp_path, outside, scratch):
    """🔴 Red on integration: the prompts B sent were written wherever A's prompts folder led."""
    assert inv.by_id("prompts").kind == inv.KIND_JSON_ENTITY_DIR
    before = _state(outside)

    a, report = _pull(
        tmp_path, lambda a: (a / "prompts").symlink_to(outside, target_is_directory=True)
    )

    assert _state(outside) == before
    assert report.refused == {"prompts": A_LINK}
    assert (a / "tasks" / "from-b.json").exists(), "the rest of the change did not come in"
    assert Cursor(a / "sync").seq_of("B") == 0, "the change was moved past, never to come in"


def test_a_pull_appends_no_line_to_a_log_through_a_link(tmp_path, outside, scratch):
    """🔴 Red on integration: the notification B sent was appended to whatever A's log led to."""
    (outside / "notifications.jsonl").write_text('{"ts": "elsewhere"}\n', encoding="utf-8")
    before = _state(outside)

    a, report = _pull(
        tmp_path, lambda a: (a / "notifications.jsonl").symlink_to(outside / "notifications.jsonl")
    )

    assert _state(outside) == before
    assert report.refused == {"notifications.jsonl": A_LINK}


def test_a_pull_merges_no_database_through_a_link(tmp_path, outside, scratch):
    """🔴 Red on integration: B's learning log was merged into the database A's link led to."""
    _database(outside / "learning.db", {"elsewhere"})
    before = _state(outside)

    a, report = _pull(tmp_path, lambda a: (a / "learning.db").symlink_to(outside / "learning.db"))

    assert _state(outside) == before
    assert report.refused == {"learning.db": A_LINK}


def test_the_conflict_review_writes_nothing_through_a_link(tmp_path, outside, monkeypatch):
    """🔴 Red on integration: taking the other machine's version wrote it wherever the store's
    folder led."""
    from personalclaw.durability import conflict_resolve as resolver
    from personalclaw.durability import conflicts as conflicts_mod
    from personalclaw.durability import reconcile

    home = (tmp_path / "home").resolve()
    (home / "tasks").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    entry = inv.by_id("tasks")
    (home / "tasks" / "t1.json").write_text(json.dumps({"id": "t1", "title": "mine"}))
    local_rows = reconcile.read_local_rows(entry, home / "tasks")
    remote_rows = [{"id": "t1", "data": {"id": "t1", "title": "theirs"}}]
    agreed = conflicts_mod.row_sha({"id": "t1", "data": {"id": "t1", "title": "shared"}})
    (found,) = conflicts_mod.detect_conflicts(
        entry, local_rows, remote_rows, {"t1": agreed}, now="t"
    )
    assert conflicts_mod.ConflictQueue(home).record(found)
    # The store's folder made a link to a copy of it outside the home.
    (home / "tasks").rename(outside / "tasks")
    (home / "tasks").symlink_to(outside / "tasks", target_is_directory=True)
    before = _state(outside)

    out = resolver.resolve_conflict(home, found.id, resolver.CHOICE_TAKE_REMOTE, now="NOW")

    assert _state(outside) == before
    assert not out.ok and out.code == "write_failed"
    assert f"tasks ({A_LINK})" in out.message


# ── the controls: the same items as real files and folders come in as before ────────────────────


def test_the_control_a_replace_restore_puts_every_part_back(home, tmp_path, capsys):
    for rel in SINGLE_FILES:
        (home / rel).write_bytes(b"this home's own\n")
    (home / "tasks").mkdir()
    (home / "tasks" / "old.json").write_text('{"id": "old"}', encoding="utf-8")
    archive = _snapshot_archive(
        tmp_path / "snap.tar.gz",
        {**{rel: b"the snapshot's copy\n" for rel in SINGLE_FILES}, "tasks/t1.json": b"{}"},
    )

    code, out = _restore(archive, "replace", capsys)

    assert code == 0 and "✅ Replace complete." in out, out
    for rel in SINGLE_FILES:
        assert (home / rel).read_bytes() == b"the snapshot's copy\n"
    assert sorted(p.name for p in (home / "tasks").iterdir()) == ["t1.json"]
    (aside,) = home.glob("pre-restore-*")
    assert (aside / "tasks" / "old.json").is_file() and (aside / "tool_prefs.json").is_file()


@MERGES
def test_the_control_a_merge_brings_in_what_the_home_lacks(home, tmp_path, merge):
    archived = _database(tmp_path / "archived" / "learning.db", {"archived"})
    _database(home / "learning.db", {"mine"})

    left = merge(
        home,
        tmp_path,
        {
            "learning.db": archived,
            "workspace/notes/today.md": b"the archive's notes\n",
            "triggers.json": json.dumps({"triggers": [{"id": "b", "name": "Brief"}]}).encode(),
        },
    )

    assert left == []
    assert _notes(home / "learning.db") == {"mine", "archived"}
    assert (home / "workspace" / "notes" / "today.md").read_bytes() == b"the archive's notes\n"
    names = [t["name"] for t in json.loads((home / "triggers.json").read_text())["triggers"]]
    assert names == ["Brief"]


def test_the_control_a_pull_takes_the_whole_change_in(tmp_path, scratch):
    a, report = _pull(tmp_path, lambda a: None)

    assert report.ok and report.refused == {}
    assert (a / "tasks" / "from-b.json").exists()
    assert (a / "prompts" / "weekly.yaml").read_text() == "name: weekly\n"
    assert "from B" in (a / "notifications.jsonl").read_text()
    assert _notes(a / "learning.db") == {"from-b"}
    assert Cursor(a / "sync").seq_of("B") == 1


# ── the one check ────────────────────────────────────────────────────────────────────────────────


class TestHomePath:
    """``durability.home_paths.home_path``, which every write of those doors takes its path
    from."""

    def test_a_path_with_no_link_on_the_way_is_given(self, tmp_path):
        from personalclaw.durability.home_paths import home_path

        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b.json").write_text("{}")
        assert home_path(tmp_path, "a/b.json") == tmp_path / "a" / "b.json"
        assert home_path(tmp_path, "a/new/c.json") == tmp_path / "a" / "new" / "c.json"
        assert home_path(tmp_path, "missing/c.json") == tmp_path / "missing" / "c.json"

    def test_a_home_that_lives_behind_a_link_is_the_home(self, tmp_path):
        from personalclaw.durability.home_paths import home_path

        (tmp_path / "real").mkdir()
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
        assert home_path(tmp_path / "link", "x.json") == tmp_path / "link" / "x.json"

    @pytest.mark.parametrize(
        "plant, rel, named, why",
        [
            (lambda h, o: (h / "x.json").symlink_to(o / "x.json"), "x.json", "x.json", A_LINK),
            (
                lambda h, o: (h / "dir").symlink_to(o, target_is_directory=True),
                "dir/x.json",
                "dir",
                A_LINK,
            ),
            (
                lambda h, o: ((h / "a").mkdir(), (h / "a" / "b").symlink_to(o)),
                "a/b/c/x.json",
                "a/b",
                A_LINK,
            ),
            (
                lambda h, o: ((o / "x.json").write_text("{}"), os.link(o / "x.json", h / "x.json")),
                "x.json",
                "x.json",
                A_HARD_LINK,
            ),
            (
                lambda h, o: (h / "notes.db-wal").symlink_to(o / "notes.db-wal"),
                "notes.db",
                "notes.db-wal",
                A_LINK,
            ),
            (
                lambda h, o: (h / ".inbox.lock").symlink_to(o / "inbox.lock"),
                "inbox.json",
                ".inbox.lock",
                A_LINK,
            ),
        ],
        ids=[
            "at-the-file",
            "at-a-folder",
            "at-a-deeper-folder",
            "a-hard-link",
            "beside-a-db",
            "a-stores-lock",
        ],
    )
    def test_a_link_on_the_way_is_named(self, tmp_path, plant, rel, named, why):
        from personalclaw.durability.home_paths import LinkInTheWay, home_path

        home, elsewhere = tmp_path / "home", tmp_path / "elsewhere"
        home.mkdir()
        elsewhere.mkdir()
        plant(home, elsewhere)
        with pytest.raises(LinkInTheWay) as caught:
            home_path(home, rel)
        assert (caught.value.rel, caught.value.why) == (named, why)
        assert str(caught.value) == f"{named} ({why})"

    @pytest.mark.parametrize("rel", ["", "/abs/x", "../x", "a/../x", "a//b", "./x", "a/\x00"])
    def test_a_path_that_names_nothing_inside_is_refused(self, tmp_path, rel):
        from personalclaw.durability.home_paths import home_path

        with pytest.raises(ValueError):
            home_path(tmp_path, rel)
