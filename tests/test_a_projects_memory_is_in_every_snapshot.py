"""What a project's sessions remember comes back from a snapshot, and Doctor stops saying otherwise.

Memory is partitioned by working directory (`config.loader.memory_dir_for_cwd`): a project's runs
work in its own folder, and what they learn lands in that partition's database — the partition's
vector store and its full-text index share `memory_index.db` there. That database holds the
partition's facts and episodes, nothing else holds them, and no snapshot carried it: the tree copy
skips every database (a raw copy of a live one is torn), and only the home's own paths went
through the backup API. Doctor said so ("undeclared databases — these are in NO snapshot") and was
right; a replace restore then moved the partition aside with the rest of `workspace/`, and the
project's memories did not come back.

So the manifest declares the partitions as what they are, the memory store kept once per working
directory, and every path that copies the home treats them as it treats `memory.db`.
"""

from __future__ import annotations

import io
import shutil
import sqlite3
import tarfile
import zipfile
from pathlib import Path

import pytest

from personalclaw.durability import inventory
from personalclaw.snapshot import restore_main, snapshot_main

PROJECT = "/srv/example/newsletter"
FACT = ("project.release.cadence", "a release every six weeks")


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: False)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = (tmp_path / "home").resolve()
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _remember_in_the_project(home: Path) -> str:
    """A project session's memory, written by the writers a session uses: the partition's
    markdown store, then its vector store, which `context._attach_vector_store` opens on the same
    partition database. Returns the partition database's home-relative path."""
    from personalclaw.config.loader import memory_dir_for_cwd
    from personalclaw.memory import MemoryStore
    from personalclaw.vector_memory import VectorMemoryStore

    partition = memory_dir_for_cwd(PROJECT)
    MemoryStore(workspace=partition).init()
    store = VectorMemoryStore(db_path=partition / "memory_index.db")
    store.init()
    try:
        assert store.set_semantic(FACT[0], FACT[1], 0.9, "user_explicit") is None
    finally:
        store.close()
    rel = (partition / "memory_index.db").relative_to(home).as_posix()
    assert rel.startswith("workspace/_ext/"), rel
    return rel


def _facts(db: Path) -> dict[str, str]:
    import json

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT key, value_json FROM semantic_memory WHERE is_deleted=0"
        ).fetchall()
    finally:
        conn.close()
    return {k: json.loads(v) for k, v in rows}


def _snapshot(out: Path) -> Path:
    assert snapshot_main([str(out)]) == 0
    (tarball,) = out.glob("personalclaw-snapshot-*.tar.gz")
    return tarball


def test_the_manifest_claims_a_projects_memory_so_doctor_has_nothing_to_report(home):
    """🔴 Red on integration: `audit_home` named the partition database as undeclared, which is
    Home's 'Durability degraded' and Doctor's 'in NO snapshot'."""
    rel = _remember_in_the_project(home)
    audit = inventory.audit_home(home)
    assert rel not in audit.undeclared_dbs
    assert audit.ok, (audit.unclaimed, audit.undeclared_dbs)


def test_a_surprise_database_in_the_workspace_is_still_reported(home):
    """The claim is the partitions' memory database and nothing wider: the audit keeps its eye on
    every other database in the tree, the hazard it exists for."""
    _remember_in_the_project(home)
    partition = home / "workspace" / "_ext"
    for stray in (partition / "surprise.db", next(partition.iterdir()) / "notes.db"):
        sqlite3.connect(str(stray)).close()
    undeclared = inventory.audit_home(home).undeclared_dbs
    assert "workspace/_ext/surprise.db" in undeclared
    assert any(p.endswith("/notes.db") for p in undeclared), undeclared


def test_the_snapshot_carries_the_projects_memory(home, tmp_path):
    """🔴 Red on integration: the archive held the partition's markdown and not its database."""
    rel = _remember_in_the_project(home)
    tarball = _snapshot(tmp_path / "out")
    with tarfile.open(tarball) as tar:
        (member,) = [m for m in tar.getmembers() if m.name.endswith("/" + rel)]
        data = tar.extractfile(member).read()  # type: ignore[union-attr]
    copy = tmp_path / "copy.db"
    copy.write_bytes(data)
    assert _facts(copy) == {FACT[0]: FACT[1]}


def test_a_replace_restore_brings_the_projects_memory_back(home, tmp_path):
    """🔴 Red on integration: the restore moved the live partition aside with `workspace/`, and the
    archive had no copy to put back."""
    rel = _remember_in_the_project(home)
    kept = tmp_path / "kept.tar.gz"
    shutil.copy2(_snapshot(tmp_path / "out"), kept)

    shutil.rmtree(home)
    home.mkdir()
    assert restore_main([str(kept), "--mode", "replace"]) == 0

    assert _facts(home / rel) == {FACT[0]: FACT[1]}


def test_a_merge_restore_takes_the_projects_memories_in(home, tmp_path):
    """A merge takes the archive's memories into a partition this home also has, the way it
    takes `memory.db`'s in, and keeps what this home learned since."""
    rel = _remember_in_the_project(home)
    kept = tmp_path / "kept.tar.gz"
    shutil.copy2(_snapshot(tmp_path / "out"), kept)

    conn = sqlite3.connect(str(home / rel))
    conn.execute("DELETE FROM semantic_memory")
    conn.commit()
    conn.close()
    from personalclaw.vector_memory import VectorMemoryStore

    since = VectorMemoryStore(db_path=home / rel)
    since.init()
    try:
        assert since.set_semantic("project.owner", "the release lead", 0.9, "user_explicit") is None
    finally:
        since.close()

    assert restore_main([str(kept), "--mode", "merge"]) == 0

    assert _facts(home / rel) == {FACT[0]: FACT[1], "project.owner": "the release lead"}


def test_the_export_takes_the_projects_memory_through_the_backup_api(home):
    """An export already walked the partition's file raw out of the tree — a live database copied
    as bytes, the torn-copy hazard. Declared, it leaves the way `memory.db` does."""
    from personalclaw.portability import create_export_zip

    rel = _remember_in_the_project(home)
    data, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n.split("/", 1)[1] for n in zf.namelist() if "/" in n]
        assert names.count(rel) == 1, names
        assert not [n for n in names if n.startswith(rel + "-")], names
        copy = home.parent / "exported.db"
        copy.write_bytes(zf.read(next(n for n in zf.namelist() if n.endswith("/" + rel))))
    assert _facts(copy) == {FACT[0]: FACT[1]}
