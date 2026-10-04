"""A project import, a pack install, a lock and an export never go through a link the home holds.

The restore, the import of an export archive and a sync's pull already leave an item alone where the
home holds a link on the way to it (``durability.home_paths``). The same rule now holds at the other
doors that write into the home, read it to send it out, or lock in it:

* An import of a project archive wrote the new project's files wherever a ``projects`` folder that
  was a link led: its containment check resolved the folder first, so the files passed it.
* A pack's install took a store's folder that was a link for the store (its check resolved the
  store's folder too), so its prompts, skills, agents and staged automations, and its journal and
  ledger under a ``packs`` folder that was a link, were written wherever the link led. Its uninstall
  removed a component through the same folder.
* A store's lock was opened to write, which emptied whatever a link at the lock's name led to,
  made a file where a dangling one pointed, and emptied what a lock with a second name shared.
* A sync's export, which goes to the other machines, read a store through a link: a folder that
  was one, a database, a log, a folder on the way to a database, and a file of a store with a
  second name, so a file from outside the home went out in the store's name. A file of a store that
  was a link was left out in silence, and so read as deleted, and its delete went to the others.

Every link here leads to a scratch folder outside a scratch home, and each door is held to the same
thing: that folder is exactly as it was afterwards, the link is kept, and the result names it. The
controls run the same doors over ordinary files and folders, and each works as before.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.durability.home_paths import LinkInTheWay

#: Why an item was left as it is, as the result says it.
A_LINK = (
    "a symbolic link in this home, which nothing restored, imported or synced is written through"
)
NOT_READ = "a symbolic link in this home, which nothing exported or synced is read through"
NOT_READ_HARD = (
    "a hard link in this home (a file with another name), which nothing exported or synced is "
    "read through"
)
NO_LOCK = "a symbolic link in this home, which no lock is opened through"
NO_LOCK_HARD = (
    "a hard link in this home (a file with another name), which no lock is opened through"
)

#: What a file outside the home holds: never in anything a door writes or sends.
OUTSIDE = "a line kept outside the home"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)

    def locks() -> Path:
        # The suite keeps each test's job locks in a folder of its own; here they are the home's.
        (home / "locks").mkdir(exist_ok=True)
        return home / "locks"

    monkeypatch.setattr("personalclaw.concurrency._locks_dir", locks)
    return home


@pytest.fixture
def outside(tmp_path) -> Path:
    """A scratch folder outside the home: where every link here leads."""
    folder = (tmp_path / "outside").resolve()
    folder.mkdir()
    return folder


def _state(folder: Path) -> dict[str, tuple[bytes, int]]:
    """Every folder and file under *folder*, a file with its bytes, and each one's mode: what
    "exactly as it was" compares."""
    return {
        p.relative_to(folder).as_posix()
        + ("/" if p.is_dir() else ""): (
            b"" if p.is_dir() else p.read_bytes(),
            stat.S_IMODE(os.lstat(p).st_mode),
        )
        for p in sorted(folder.rglob("*"))
    }


def _database(path: Path, rows: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT OR IGNORE INTO notes VALUES (?, ?)", [(r, OUTSIDE) for r in rows])
    conn.commit()
    conn.close()


# ── a project archive's import ───────────────────────────────────────────────────────────────────


def _project_archive() -> bytes:
    from personalclaw.workflows import project_archive as pa
    from personalclaw.workflows.project_export import plan_export

    files = {"project.json": b"{}", "context/overview.md": b"# Overview\n"}
    return pa.write_archive(plan_export("p-src", project_name="Ingest rework", files=files), files)


@pytest.mark.asyncio
async def test_a_project_import_writes_nothing_through_a_projects_folder_that_is_a_link(
    tmp_path, outside
):
    """🔴 Red on integration: the import made the project and wrote its files wherever the home's
    ``projects`` folder led, since its check that a file is inside the project resolved the
    folder first."""
    from tests.test_project_import_route import _client, _form

    home = (tmp_path / "home").resolve()
    home.mkdir()
    (outside / "projects").mkdir()
    (home / "projects").symlink_to(outside / "projects", target_is_directory=True)
    before = _state(outside)

    async with _client(home) as client:
        r = await client.post("/api/projects/import", data=_form(_project_archive()))
        body = await r.json()

    assert _state(outside) == before, "the archive's project was written outside the home"
    assert r.status == 409, body
    assert body["error"]["code"] == "link_in_the_way"
    assert body["error"]["message"] == (
        f"projects ({A_LINK}), so no project can be imported into this home until the link is gone"
    )
    assert (home / "projects").is_symlink()


def test_the_project_import_command_says_what_a_link_kept_and_fails(
    home, outside, tmp_path, capsys
):
    import argparse

    from personalclaw.cli_project import project_main

    (outside / "projects").mkdir()
    (home / "projects").symlink_to(outside / "projects", target_is_directory=True)
    archive = tmp_path / "ingest-rework.zip"
    archive.write_bytes(_project_archive())
    before = _state(outside)

    code = project_main(
        argparse.Namespace(
            project_command="import", archive=str(archive), passphrase="", dry_run=False
        )
    )

    assert _state(outside) == before
    assert code == 1
    assert f"projects ({A_LINK}), so no project can be imported here" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_the_control_a_project_import_writes_its_files_into_the_home(tmp_path):
    from tests.test_project_import_route import _client, _form

    home = (tmp_path / "home").resolve()
    home.mkdir()
    async with _client(home) as client:
        r = await client.post("/api/projects/import", data=_form(_project_archive()))
        body = await r.json()

    assert r.status == 201, body
    assert body["left_unchanged"] == []
    assert "2 entities imported" in body["summary"]
    overview = home / "projects" / body["project_id"] / "context" / "overview.md"
    assert overview.read_bytes() == b"# Overview\n"
    assert stat.S_IMODE(overview.stat().st_mode) == 0o600


# ── a pack's install and uninstall ───────────────────────────────────────────────────────────────

PACK = "personal-cfo"


def _pack_gateway() -> web.Application:
    from personalclaw.dashboard.handlers.packs import register_pack_routes

    app = web.Application()
    register_pack_routes(app)
    return app


def _skip_connectors() -> dict[str, dict[str, str]]:
    from personalclaw.packs import bundled

    source = bundled.get_bundled(PACK)
    assert source is not None
    declared = json.loads((source.source / "connectors.json").read_text(encoding="utf-8"))
    return {str(row["name"]): {"mode": "skip"} for row in declared}


async def _install(client: TestClient) -> tuple[int, dict]:
    resp = await client.post(
        f"/api/packs/bundled/{PACK}/install", json={"connector_choices": _skip_connectors()}
    )
    return resp.status, await resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("store", ["prompts", "skills", "agents", "workflows", "packs"])
async def test_a_pack_install_writes_nothing_through_a_store_folder_that_is_a_link(
    home, outside, store
):
    """🔴 Red on integration: the layout's check resolved the store's folder before it asked
    whether a component was inside it, so a folder that was a link took the pack's files."""
    (outside / store).mkdir()
    (home / store).symlink_to(outside / store, target_is_directory=True)
    before = _state(outside)

    async with TestClient(TestServer(_pack_gateway())) as client:
        status, body = await _install(client)

    assert _state(outside) == before, f"the pack wrote through {store}/"
    assert status == 400, body
    assert body["error"]["code"] == "pack_refused_link"
    assert f"{store} ({A_LINK})" in body["error"]["message"]
    assert (home / store).is_symlink()
    assert not (home / "packs" / "installed.json").exists(), "a refused pack is not installed"


@pytest.mark.asyncio
async def test_the_control_a_pack_installs_beside_a_link_at_a_components_own_name(home, outside):
    """A link at a component's own path holds its id, as an installed copy would: the pack's copy
    lands under a fresh id beside it, and the link is left as it is."""
    (outside / "digest.yaml").write_text("name: elsewhere\n", encoding="utf-8")
    (home / "prompts").mkdir()
    (home / "prompts" / "cfo-spending-digest.yaml").symlink_to(outside / "digest.yaml")
    before = _state(outside)

    async with TestClient(TestServer(_pack_gateway())) as client:
        status, body = await _install(client)

    assert status == 200, body
    assert _state(outside) == before
    assert (home / "prompts" / "cfo-spending-digest.yaml").is_symlink()
    assert (home / "prompts" / "cfo-spending-digest-imported-1.yaml").is_file()


@pytest.mark.asyncio
async def test_an_uninstall_removes_nothing_through_a_store_folder_that_is_a_link(home, outside):
    """🔴 Red on integration: the pack's prompt was removed wherever the prompts folder led."""
    async with TestClient(TestServer(_pack_gateway())) as client:
        status, body = await _install(client)
        assert status == 200, body
        (home / "prompts").rename(outside / "prompts")
        (home / "prompts").symlink_to(outside / "prompts", target_is_directory=True)
        before = _state(outside)
        resp = await client.post(f"/api/packs/{PACK}/uninstall", json={"confirm": True})
        body = await resp.json()

    assert _state(outside) == before, "the uninstall removed a file outside the home"
    assert resp.status == 200, body
    kept = {k["ref"]: k["reason"] for k in body["uninstall"]["kept"]}
    assert kept["prompt:cfo-spending-digest"] == (
        "prompts is a link in this home, which nothing is removed through, so it stays"
    )


# ── a lock ───────────────────────────────────────────────────────────────────────────────────────


@contextmanager
def _as_the_task_board(_home: Path) -> Iterator[None]:
    from personalclaw.tasks import native

    native._as_one_writer(lambda: None)
    yield


def _locks():
    from personalclaw import record_files
    from personalclaw.concurrency import lock_path, single_flight
    from personalclaw.inbound import clients
    from personalclaw.schedule_history import ScheduleRunStore

    return {
        "a store of records": (
            lambda h: record_files.locked(h / "triggers.json"),
            ".triggers.lock",
        ),
        "the run history": (lambda h: ScheduleRunStore(h)._lock(), "cron-history/.history.lock"),
        "a job that runs once at a time": (
            lambda h: single_flight("durability:export"),
            lambda h: lock_path("durability:export").relative_to(h).as_posix(),
        ),
        "the task board": (_as_the_task_board, "tasks/.write.lock"),
        "the inbound clients": (lambda h: clients._locked(), "inbound_clients.json.lock"),
    }


LOCKS = pytest.mark.parametrize("which", list(_locks()))


def _lock_rel(which: str, home: Path) -> str:
    rel = _locks()[which][1]
    return rel(home) if callable(rel) else rel


def _take(which: str, home: Path) -> None:
    with _locks()[which][0](home):
        pass


def _plant(home: Path, rel: str, how: str, outside: Path) -> None:
    (home / rel).parent.mkdir(parents=True, exist_ok=True)
    if how == "dangling":
        (home / rel).symlink_to(outside / "made-by-a-lock")
        return
    kept = outside / "kept.txt"
    kept.write_text(OUTSIDE + "\n", encoding="utf-8")
    kept.chmod(0o644)
    if how == "symbolic":
        (home / rel).symlink_to(kept)
    else:
        os.link(kept, home / rel)


@LOCKS
@pytest.mark.parametrize("how", ["symbolic", "dangling", "hard"])
def test_a_lock_empties_and_makes_nothing_through_a_link(home, outside, which, how):
    """🔴 Red on integration: a lock opened to write emptied the file a link at its name led to,
    made the file a dangling one named, and emptied what a lock with a second name shared."""
    rel = _lock_rel(which, home)
    _plant(home, rel, how, outside)
    before = _state(outside)

    refused: LinkInTheWay | None = None
    try:
        _take(which, home)
    except LinkInTheWay as link:
        refused = link

    assert _state(outside) == before, "the lock wrote outside the home"
    why = NO_LOCK_HARD if how == "hard" else NO_LOCK
    assert refused is not None, "the lock was taken through the link"
    assert str(refused) == f"{rel} ({why})"


@LOCKS
def test_the_control_a_lock_opens_and_never_empties_its_own_file(home, which):
    rel = _lock_rel(which, home)
    (home / rel).parent.mkdir(parents=True, exist_ok=True)
    (home / rel).write_text("what a holder left\n", encoding="utf-8")

    _take(which, home)
    _take(which, home)

    assert (home / rel).read_text(encoding="utf-8") == "what a holder left\n"
    assert stat.S_IMODE((home / rel).stat().st_mode) == 0o600


def test_the_config_lock_changes_nothing_a_lock_with_a_second_name_shares(home, outside):
    """🔴 Red on integration: the config lock refused a symbolic link, but opened a lock with a
    second name and made the file it shares readable by its owner alone."""
    from personalclaw.config.loader import ConfigWriteError
    from personalclaw.config.transactions import _ConfigLock

    _plant(home, "config.json.lock", "hard", outside)
    before = _state(outside)

    refused: ConfigWriteError | None = None
    try:
        with _ConfigLock(home / "config.json", 1.0):
            pass
    except ConfigWriteError as exc:
        refused = exc

    assert _state(outside) == before, "the config lock changed a file outside the home"
    assert refused is not None, "the config lock was taken through the link"
    assert f"config.json.lock ({NO_LOCK_HARD})" in str(refused)
    assert "nothing was written" in str(refused)


def test_a_workspace_lock_writes_no_pid_through_a_link(home, outside):
    """🔴 Red on integration: the workspace lock emptied whatever its lock's link led to and wrote
    the holder's pid there."""
    from personalclaw.concurrency import lock_path
    from personalclaw.workflows.provisioning import acquire_workspace_lock, lock_key

    rel = lock_path(lock_key("r-1")).relative_to(home).as_posix()
    _plant(home, rel, "symbolic", outside)
    before = _state(outside)

    held = acquire_workspace_lock("r-1")

    assert _state(outside) == before
    assert not held.acquired
    assert f"{rel} ({NO_LOCK})" in held.reason


@pytest.mark.asyncio
async def test_a_write_whose_lock_is_a_link_is_answered_with_the_link(home, outside):
    """🔴 Red on integration: the comment was saved and the file the store's lock led to emptied.
    Now the write is refused, and the answer names the link."""
    from personalclaw.dashboard.handlers.doc_comments import register_doc_comment_routes
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    _plant(home, ".doc_comments.lock", "symbolic", outside)
    before = _state(outside)
    app = web.Application(middlewares=[request_boundary_middleware()])
    register_doc_comment_routes(app)
    async with TestClient(TestServer(app)) as client:
        r = await client.post(
            "/api/doc-comments", json={"doc_id": "notes.md", "comment": "Tighten this."}
        )
        body = await r.json()

    assert _state(outside) == before
    assert r.status == 409, body
    assert body["error"]["code"] == "link_in_the_way"
    assert f".doc_comments.lock ({NO_LOCK})" in body["error"]["message"]
    assert not (home / "doc_comments.json").exists()


# ── an export ────────────────────────────────────────────────────────────────────────────────────


def _sent(out: Path) -> bytes:
    """Every byte an export holds: what a sync sends the other machines."""
    return b"".join(p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file())


def _entity(folder: Path, rid: str, title: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{rid}.json").write_text(json.dumps({"id": rid, "title": title}), encoding="utf-8")


def _a_linked_folder_store(home: Path, outside: Path) -> None:
    (outside / "prompts").mkdir()
    (outside / "prompts" / "weekly.yaml").write_text(f"name: {OUTSIDE}\n", encoding="utf-8")
    (home / "prompts").symlink_to(outside / "prompts", target_is_directory=True)


def _a_linked_database(home: Path, outside: Path) -> None:
    _database(outside / "learning.db", {"elsewhere"})
    (home / "learning.db").symlink_to(outside / "learning.db")


def _a_linked_log(home: Path, outside: Path) -> None:
    line = {"ts": "2026-01-01T00:00:00+00:00", "title": OUTSIDE}
    (outside / "notifications.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    (home / "notifications.jsonl").symlink_to(outside / "notifications.jsonl")


def _a_linked_folder_on_the_way(home: Path, outside: Path) -> None:
    _database(outside / "knowledge" / "knowledge.db", {"elsewhere"})
    (home / "workspace").mkdir()
    (home / "workspace" / "knowledge").symlink_to(outside / "knowledge", target_is_directory=True)


def _a_linked_one_file_store(home: Path, outside: Path) -> None:
    (outside / "tool_prefs.json").write_text(json.dumps({"note": OUTSIDE}), encoding="utf-8")
    (home / "tool_prefs.json").symlink_to(outside / "tool_prefs.json")


@pytest.mark.parametrize(
    "plant, named",
    [
        (_a_linked_folder_store, "prompts"),
        (_a_linked_database, "learning.db"),
        (_a_linked_log, "notifications.jsonl"),
        (_a_linked_folder_on_the_way, "workspace/knowledge"),
        (_a_linked_one_file_store, "tool_prefs.json"),
    ],
    ids=["a-folder-store", "a-database", "a-log", "a-folder-on-the-way", "a-one-file-store"],
)
def test_a_sync_export_reads_no_store_through_a_link(home, outside, tmp_path, plant, named):
    """🔴 Red on integration: what the link led to went out in the store's name (a one-file store
    behind a link was left out in silence)."""
    from personalclaw.durability.shards import export_shards

    plant(home, outside)
    _entity(home / "tasks", "mine", "a task of this home's")
    before = _state(outside)
    out = tmp_path / "out"

    result = export_shards(home, out, for_sync=True)

    assert OUTSIDE.encode() not in _sent(out), "a file outside the home was exported"
    assert result.left_out == {named: NOT_READ}
    assert b"a task of this home's" in _sent(out), "the rest of the home is exported"
    assert _state(outside) == before


def test_a_sync_export_reads_no_file_of_a_store_through_a_link(home, outside, tmp_path):
    """🔴 Red on integration: a file of the store with a second name went out, and a file or a
    folder of it that was a symbolic link was left out in silence."""
    from personalclaw.durability.shards import export_shards

    tasks = home / "tasks"
    _entity(tasks, "mine", "a task of this home's")
    _entity(outside, "theirs", OUTSIDE)
    _entity(outside, "shared", OUTSIDE)
    _entity(outside / "sub", "deep", OUTSIDE)
    (tasks / "linked.json").symlink_to(outside / "theirs.json")
    os.link(outside / "shared.json", tasks / "shared.json")
    (tasks / "sub").symlink_to(outside / "sub", target_is_directory=True)
    out = tmp_path / "out"

    result = export_shards(home, out, for_sync=True)

    assert OUTSIDE.encode() not in _sent(out)
    assert result.left_out == {
        "tasks/linked.json": NOT_READ,
        "tasks/shared.json": NOT_READ_HARD,
        "tasks/sub": NOT_READ,
    }
    assert b"a task of this home's" in _sent(out)


def test_the_backup_export_reads_no_file_of_a_log_folder_through_a_link(home, outside, tmp_path):
    """🔴 Red on integration: a run history file that was a link went into the backup export."""
    from personalclaw.durability.shards import export_shards

    history = home / "cron-history"
    history.mkdir()
    (history / "nightly.jsonl").write_text(
        json.dumps({"started_at": "2026-01-01T00:00:00+00:00", "job": "nightly"}) + "\n",
        encoding="utf-8",
    )
    (outside / "other.jsonl").write_text(
        json.dumps({"started_at": "2026-01-02T00:00:00+00:00", "job": OUTSIDE}) + "\n",
        encoding="utf-8",
    )
    (history / "other.jsonl").symlink_to(outside / "other.jsonl")
    out = tmp_path / "out"

    result = export_shards(home, out)

    assert OUTSIDE.encode() not in _sent(out)
    assert result.left_out == {"cron-history/other.jsonl": NOT_READ}
    assert b"nightly" in _sent(out)


def test_the_machines_id_is_never_read_through_a_link(home, outside, tmp_path):
    """🔴 Red on integration: the id every copy names its machine by was read through a link the
    home held at it, so whatever the link led to went into every manifest a sync sends, and into
    every key it publishes under."""
    from personalclaw.durability.shards import export_shards, machine_id

    (outside / "notes.txt").write_text(OUTSIDE + "\n", encoding="utf-8")
    (home / "machine_id").symlink_to(outside / "notes.txt")
    _entity(home / "tasks", "mine", "a task of this home's")
    before = _state(outside)
    out = tmp_path / "out"

    refused: LinkInTheWay | None = None
    try:
        export_shards(home, out, for_sync=True)
    except LinkInTheWay as link:
        refused = link

    assert OUTSIDE.encode() not in _sent(out), "the machine's id was read through the link"
    assert refused is not None and str(refused) == f"machine_id ({NOT_READ})"
    assert not out.exists() or not any(out.iterdir()), "an export refused whole writes nothing"
    with pytest.raises(LinkInTheWay):
        machine_id(home)
    assert _state(outside) == before
    assert (home / "machine_id").is_symlink(), "the link is neither read nor replaced"


def test_a_store_behind_a_link_reads_as_changed_until_the_link_is_gone(home, outside, tmp_path):
    """🔴 Red on integration: once a store's folder became a link to the same files, the hourly
    export's change check measured it through the link, read it as unchanged, and never named it."""
    import shutil

    from personalclaw.durability.shards import dirty_entries, mark_exported

    _entity(home / "prompts", "weekly", "a prompt of this home's")
    state = tmp_path / "export_state.json"
    mark_exported(state, dirty_entries(home, state))
    assert "prompts" not in dirty_entries(home, state).entries, "the control: measured unchanged"
    shutil.copytree(home / "prompts", outside / "prompts", copy_function=shutil.copy2)
    shutil.rmtree(home / "prompts")
    (home / "prompts").symlink_to(outside / "prompts", target_is_directory=True)
    before = _state(outside)

    changes = dirty_entries(home, state)

    assert "prompts" in changes.entries
    assert "prompts" not in changes.fingerprints, "nothing measured through the link is recorded"
    assert _state(outside) == before


# ── a whole sync: nothing outside goes to the other machines, and no link reads as a delete ─────


@pytest.fixture
def scratch(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _published(store, machine: str) -> bytes:
    from personalclaw.durability.registry import machine_prefix

    return b"".join(
        data for key, data in store.objects.items() if key.startswith(machine_prefix(machine))
    )


def _tombstoned(store, machine: str, seq: int) -> set[str]:
    """The records machine *machine*'s copy *seq* says it deleted."""
    from personalclaw.durability.registry import shard_prefix

    raw = store.objects.get(f"{shard_prefix(machine, seq)}tasks/entities.jsonl", b"")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    return {row["id"] for row in rows if "deleted_at" in row}


def test_a_sync_sends_nothing_a_link_leads_to(tmp_path, outside, scratch):
    """🔴 Red on integration: the prompts a link led to went to the other machines."""
    from personalclaw.durability.sync_cycle import run_sync_cycle
    from tests.test_durability_sync_cycle import SharedStore

    a = tmp_path / "A"
    _entity(a / "tasks", "mine", "a task of this home's")
    _a_linked_folder_store(a, outside)
    store = SharedStore()

    report = run_sync_cycle(store, a, self_id="A", now="2026-01-01T00:00:00+00:00")

    assert report.ok, report.detail
    assert b"a task of this home's" in _published(store, "A"), "what a sync sends is readable here"
    assert OUTSIDE.encode() not in _published(store, "A"), "a file outside the home was sent"
    assert report.left_out == {"prompts": NOT_READ}
    assert f"prompts ({NOT_READ})" in report.detail


@pytest.mark.parametrize("where", ["a-file", "the-folder"])
def test_a_store_behind_a_link_is_never_read_as_deleted(tmp_path, outside, scratch, where):
    """🔴 Red on integration: a task whose file became a link was read as deleted, as was every
    task of a store whose folder became one, and the next copy carried the deletes to the others."""
    from personalclaw.durability.sync_cycle import run_sync_cycle
    from tests.test_durability_sync_cycle import SharedStore

    a = tmp_path / "A"
    _entity(a / "tasks", "t1", "first")
    _entity(a / "tasks", "t2", "second")
    store = SharedStore()
    assert run_sync_cycle(store, a, self_id="A", now="2026-01-01T00:00:00+00:00").ok
    if where == "a-file":
        _entity(outside, "t2", OUTSIDE)
        (a / "tasks" / "t2.json").unlink()
        (a / "tasks" / "t2.json").symlink_to(outside / "t2.json")
    else:
        (a / "tasks").rename(outside / "tasks")
        (outside / "tasks" / "t2.json").unlink()
        (a / "tasks").symlink_to(outside / "tasks", target_is_directory=True)
    _entity(a / "prompts", "new", "a change, so a new copy goes out")

    report = run_sync_cycle(store, a, self_id="A", now="2026-01-02T00:00:00+00:00")

    assert report.seq_published == 2, report.detail
    assert _tombstoned(store, "A", 2) == set(), "a link was read as a delete"
    assert OUTSIDE.encode() not in _published(store, "A")


def test_the_control_a_sync_carries_a_delete_made_here(tmp_path, scratch):
    """The positive control for the test above: a task removed here is a delete the next copy
    carries."""
    from personalclaw.durability.sync_cycle import run_sync_cycle
    from tests.test_durability_sync_cycle import SharedStore

    a = tmp_path / "A"
    _entity(a / "tasks", "t1", "first")
    _entity(a / "tasks", "t2", "second")
    store = SharedStore()
    assert run_sync_cycle(store, a, self_id="A", now="2026-01-01T00:00:00+00:00").ok
    (a / "tasks" / "t2.json").unlink()

    report = run_sync_cycle(store, a, self_id="A", now="2026-01-02T00:00:00+00:00")

    assert report.seq_published == 2, report.detail
    assert _tombstoned(store, "A", 2) == {"t2"}


# ── the one check, in its forms ──────────────────────────────────────────────────────────────────


class TestTheFormsOfTheCheck:
    def test_an_exports_check_names_a_link_in_its_words(self, tmp_path):
        from personalclaw.durability.home_paths import export_path

        (tmp_path / "elsewhere").mkdir()
        (tmp_path / "home").mkdir()
        (tmp_path / "home" / "tasks").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
        with pytest.raises(LinkInTheWay) as caught:
            export_path(tmp_path / "home", "tasks/t1.json")
        assert str(caught.value) == f"tasks ({NOT_READ})"

    def test_a_lock_names_its_path_in_the_home(self, home, outside):
        from personalclaw.durability.home_paths import open_lock

        (home / "locks").mkdir()
        (home / "locks" / "x.lock").symlink_to(outside / "x")
        with pytest.raises(LinkInTheWay) as caught:
            open_lock(home / "locks" / "x.lock")
        assert str(caught.value) == f"locks/x.lock ({NO_LOCK})"
        assert not (outside / "x").exists()

    def test_a_lock_that_is_a_folder_is_refused(self, home):
        from personalclaw.durability.home_paths import open_lock

        (home / "x.lock").mkdir()
        with pytest.raises(OSError):
            open_lock(home / "x.lock")


def test_a_project_archive_member_path_names_one_file(tmp_path):
    """The import writes each accepted file once, at its path in the project, by the home's rule:
    a member's path written with a doubled slash lands at the one it names."""
    from personalclaw.workflows import project_archive as pa
    from personalclaw.workflows.project_export import plan_export

    files = {"project.json": b"{}", "context//overview.md": b"# Overview\n"}
    raw = pa.write_archive(plan_export("p-src", project_name="Doubled", files=files), files)
    archive = tmp_path / "a.zip"
    archive.write_bytes(raw)
    with zipfile.ZipFile(archive) as zf:
        assert "project/context//overview.md" in zf.namelist()
    plan, extracted = pa.read_archive_plan(archive)
    left: list[str] = []

    written = pa.commit_import(plan, extracted, home=tmp_path, folder="projects/p-new", left=left)

    assert written == ["context//overview.md"] and left == []
    assert (tmp_path / "projects" / "p-new" / "context" / "overview.md").is_file()
