"""A snapshot leaves every app's Python environment behind, and a restore says what to reinstall.

What main did, driven on a scratch home with Voice Clone TTS and its engine installed:

* ``personalclaw snapshot`` captured ``apps/voice-clone-tts/venv``: 1,015 archive members, the whole
  ``site-packages``, minus the three interpreter links the copy skips (it printed a warning for
  each). A real engine is torch, gigabytes, built for one machine's OS, CPU and Python.
* ``personalclaw restore`` planted that copy: no interpreter, but the package receipt. The engine
  read as not installed, so the app offered Install engine; Install engine re-made the interpreter
  and SKIPPED pip ("requirements already installed"), the index saw no request, and the engine read
  as installed with the snapshot's packages. On another machine those are another machine's build.

Now the venv is left out of snapshots and exports, a restore never plants one an older archive
still carries, and the restore names each app whose engine has to be installed again.
"""

from __future__ import annotations

import dataclasses
import io
import json
import sqlite3
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.local_models import sidecar
from personalclaw.snapshot import restore_main, snapshot_main

ENGINE = "pclaw-fixture-engine==1.0"
SENTENCE = (
    "has no engine here: a snapshot leaves engines out, since each one is built for the machine "
    "it runs on. Install it with Install engine, on the app's card in Settings → Providers."
)


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: False)


def _home(tmp_path: Path, monkeypatch, name: str) -> Path:
    home = tmp_path / name
    home.mkdir()
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _app(home: Path, name: str, display: str, *, engine: list[str], sidecar_app: bool = True):
    """An installed app the way the installer leaves it, with its own data."""
    live = home / "apps" / name
    (live / "data").mkdir(parents=True)
    (live / "data" / "notes.txt").write_text("the app's own state", encoding="utf-8")
    provider = {"type": "model", "implementation": "provider:create_provider"}
    if sidecar_app:
        provider["execution"] = "sidecar"
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": display,
        "description": "fixture",
        "provider": provider,
        "dependencies": {"sidecarDependencies": engine} if engine else {},
    }
    (live / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (live / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )
    return live


def _engine(venv: Path, requirements: list[str]) -> None:
    """An engine Install engine left behind: a real venv's shape, receipt and marker included."""
    python = sidecar.venv_python(venv)
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (venv / "pyvenv.cfg").write_text(f"home = {Path(sys.executable).parent}\n", encoding="utf-8")
    site = venv / "lib" / "python3.13" / "site-packages" / "pclaw_fixture_engine"
    site.mkdir(parents=True)
    (site / "__init__.py").write_text("VERSION = '1.0'\n", encoding="utf-8")
    (venv / sidecar._MARKER).write_text('{"created_by": "personalclaw"}\n', encoding="utf-8")
    (venv / ".personalclaw-deps.json").write_text(json.dumps(sorted(requirements)), "utf-8")


def _snapshot(tmp_path: Path) -> Path:
    out = tmp_path / "snaps"
    assert snapshot_main([str(out)]) == 0
    [archive] = sorted(out.glob("personalclaw-snapshot-*.tar.gz"))
    return archive


def _members(archive: Path) -> list[str]:
    with tarfile.open(archive, "r:gz") as tar:
        return [m.name.split("/", 1)[1] for m in tar.getmembers() if "/" in m.name]


def _an_older_archive(tmp_path: Path, monkeypatch) -> Path:
    """A snapshot as main wrote it: the `apps` entry without `*/venv`, so the venv rides along."""
    old = tuple(
        dataclasses.replace(e, derived_within=("*/.app_secret",)) if e.id == "apps" else e
        for e in inv.INVENTORY
    )
    with monkeypatch.context() as m:
        m.setattr(inv, "INVENTORY", old)
        archive = _snapshot(tmp_path)
    assert "apps/clone-voice/venv/.personalclaw-deps.json" in _members(archive), "not main's shape"
    return archive


def _install_engine(monkeypatch, app: str) -> tuple[sidecar.SidecarInstall, list[str]]:
    """Press Install engine, with venv creation and pip stood in for (what ran is recorded)."""
    ran: list[str] = []

    def run(self, argv, *, label, timeout):
        ran.append(label)
        if label == "python -m venv":
            python = sidecar.venv_python(Path(argv[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(sidecar.SidecarInstall, "_run", run)
    install = sidecar.SidecarInstall.for_app(app)
    assert install is not None
    assert install.run(), install.error
    return install, ran


# ── capture ──────────────────────────────────────────────────────────────────────────────────


def test_a_snapshot_leaves_every_app_venv_behind_and_keeps_the_app(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])
    # An update in flight parks the old version beside the new one, venv and all.
    rollback = home / "apps" / ".clone-voice.rollback"
    rollback.mkdir()
    _engine(rollback / "venv", [ENGINE])

    members = _members(_snapshot(tmp_path))

    assert [m for m in members if "/venv" in m] == []
    for kept in ("app.json", "installed.json", "data/notes.txt"):
        assert f"apps/clone-voice/{kept}" in members, kept
    # The copy never walks into the environment, so no interpreter link is even met.
    assert "Skipping symlink" not in capsys.readouterr().out


def test_an_export_leaves_every_app_venv_behind(tmp_path, monkeypatch):
    from personalclaw.portability import create_export_zip

    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])

    data, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n.split("/", 1)[1] for n in zf.namelist() if "/" in n]

    assert [n for n in names if "/venv" in n] == []
    assert "apps/clone-voice/data/notes.txt" in names


def test_the_hourly_export_leaves_every_app_venv_behind(tmp_path, monkeypatch):
    """`durability.auto_backup` (on by default) runs this every hour, and on main it wrote every
    file of the engine into the shards' blobs."""
    import hashlib

    from personalclaw.durability.shards import export_shards

    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])

    export_shards(home, tmp_path / "shards")

    blobs = {p.name for p in (tmp_path / "shards").rglob("*") if p.is_file()}

    def exported(path: Path) -> bool:
        return hashlib.sha256(path.read_bytes()).hexdigest() in blobs

    engine_files = [p for p in (live / "venv").rglob("*") if p.is_file() and not p.is_symlink()]
    assert engine_files and [p for p in engine_files if exported(p)] == []
    assert exported(live / "data" / "notes.txt")


# ── restore ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["replace", "merge"])
def test_an_older_archives_venv_is_not_planted_and_install_engine_runs_pip(
    tmp_path, monkeypatch, mode
):
    """On main the restore planted the venv minus its interpreter, and Install engine skipped pip
    because the receipt came back with it."""
    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])
    archive = _an_older_archive(tmp_path, monkeypatch)

    fresh = _home(tmp_path, monkeypatch, "fresh")
    assert restore_main([str(archive), "--mode", mode]) == 0

    restored = fresh / "apps" / "clone-voice"
    assert (restored / "data" / "notes.txt").read_text(encoding="utf-8") == "the app's own state"
    assert not (restored / "venv").exists()
    assert sidecar.SidecarInstall.for_app("clone-voice").installed is False

    install, ran = _install_engine(monkeypatch, "clone-voice")
    assert ran == ["python -m venv", "pip"]
    assert [s.status for s in install.steps] == ["done", "done", "skipped"]
    assert install.installed


def test_the_restore_names_each_app_whose_engine_must_be_installed_again(
    tmp_path, monkeypatch, capsys
):
    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])
    # A sidecar app with no engine packages has nothing to install; an in-process app has no
    # engine at all. Neither is named.
    _app(home, "plain-sidecar", "Plain Sidecar", engine=[])
    _app(home, "in-process", "In Process", engine=[], sidecar_app=False)
    archive = _snapshot(tmp_path)

    _home(tmp_path, monkeypatch, "fresh")
    capsys.readouterr()
    assert restore_main([str(archive), "--mode", "replace"]) == 0

    said = [line for line in capsys.readouterr().out.splitlines() if "engine" in line]
    assert said == [f"⚠️  Clone Voice {SENTENCE}"]


def test_a_merge_keeps_the_engine_this_home_already_has_and_says_nothing(
    tmp_path, monkeypatch, capsys
):
    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])
    archive = _an_older_archive(tmp_path, monkeypatch)

    here = _home(tmp_path, monkeypatch, "here")
    mine = _app(here, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(mine / "venv", [ENGINE])
    capsys.readouterr()
    assert restore_main([str(archive), "--mode", "merge"]) == 0

    assert sidecar.SidecarInstall.for_app("clone-voice").installed
    assert sidecar.venv_python(mine / "venv").is_symlink(), "the live engine was touched"
    assert "engine" not in capsys.readouterr().out


def test_a_store_another_entry_owns_inside_a_tree_still_comes_back(tmp_path, monkeypatch):
    """`loop` declares `loops.db` within it because the database is its own entry, restored by its
    own pass. Leaving what capture leaves out must not leave that out. A replace restore: a merge
    leaves a loop's records out altogether, as what ran on a machine stays on it
    (`test_what_ran_on_a_machine_stays_on_it.py`)."""
    home = _home(tmp_path, monkeypatch, "home")
    (home / "loop" / "run-1").mkdir(parents=True)
    (home / "loop" / "run-1" / "finding.md").write_text("found", encoding="utf-8")
    conn = sqlite3.connect(str(home / "loop" / "loops.db"))
    conn.execute("CREATE TABLE runs(id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO runs VALUES('run-1')")
    conn.commit()
    conn.close()
    assert "loops.db" in inv.by_id("loop").derived_within
    archive = _snapshot(tmp_path)

    fresh = _home(tmp_path, monkeypatch, "fresh")
    assert restore_main([str(archive), "--mode", "replace"]) == 0

    assert (fresh / "loop" / "run-1" / "finding.md").read_text(encoding="utf-8") == "found"
    conn = sqlite3.connect(str(fresh / "loop" / "loops.db"))
    assert [r[0] for r in conn.execute("SELECT id FROM runs")] == ["run-1"]
    conn.close()
