"""An app update keeps what the app keeps in its folder: its data and its engine.

``update()`` replaces ``apps/<name>/`` with the new version's files. What that folder holds and
no bundle ships is the app's state: ``data/`` (``sdk.util.app_data_dir``) and ``venv/``, the
Python environment an ``execution: "sidecar"`` app's child runs in
(``sdk.sidecar.sidecar_venv_dir``) — where Voice Clone TTS's engine is installed. On main the
update copied ``data/`` into the new version and dropped ``venv/`` with the old version's files,
so every update deleted the installed engine (measured on
``voice-clone-tts``). A removal still takes the engine with the app, keep-data included: what
that rung keeps is ``data/``.

The venv here is a real one, made by core's own sidecar installer, and the engine is a module in
its ``site-packages``: "the engine survived" means the sidecar's interpreter still imports it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.local_models import sidecar

APP = "demo-app"
ENGINE = "pclaw_probe_engine"


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _bundle(tmp_path: Path, *, version: str, subdir: str, setup: dict | None = None) -> Path:
    d = tmp_path / subdir / APP
    d.mkdir(parents=True)
    mani = {"name": APP, "version": version, "displayName": "Demo", "description": "x"}
    if setup:
        mani["setup"] = setup
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    (d / "main.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    return d


def _installed_with_state(tmp_path: Path) -> Path:
    """Version 1.0.0 installed, with a note in ``data/`` and an engine installed in ``venv/``."""
    assert app_manager.install(_bundle(tmp_path, version="1.0.0", subdir="v1"), confirm=True).ok
    live = manager.app_dir(APP)
    (live / "data" / "notes.json").write_text('{"notes": 4}', encoding="utf-8")
    install = sidecar.SidecarInstall(APP, requirements=[])
    assert install.run() is True, install.status()
    assert install.venv == live / "venv" and install.managed
    purelib = subprocess.run(
        [
            str(sidecar.venv_python(install.venv)),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    engine = Path(purelib) / ENGINE
    engine.mkdir(parents=True)
    (engine / "__init__.py").write_text('WEIGHTS = "cloned-voice-engine"\n', encoding="utf-8")
    return live


def _engine_answer(live: Path) -> str:
    """What the sidecar's interpreter says when it imports the engine."""
    python = sidecar.venv_python(live / "venv")
    if not python.is_file():
        return "no venv"
    proc = subprocess.run(
        [str(python), "-c", f"import {ENGINE}; print({ENGINE}.WEIGHTS)"],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else f"import failed: {proc.stderr[-200:]}"


def test_an_update_keeps_the_apps_data_and_its_engine(tmp_path):
    live = _installed_with_state(tmp_path)
    before = _engine_answer(live)
    assert before == "cloned-voice-engine"

    res = app_manager.update(_bundle(tmp_path, version="2.0.0", subdir="v2"), confirm=True)

    assert res.ok, res.error
    assert manager._read_installed(APP).version == "2.0.0"
    assert (
        live / "main.py"
    ).read_text() == "VERSION = '2.0.0'\n", "the new version's files are live"
    assert (live / "data" / "notes.json").read_text() == '{"notes": 4}'
    assert _engine_answer(live) == before, "the update deleted the engine the sidecar runs"
    # Core's installer still reads the engine as installed: a re-run resumes nothing.
    again = sidecar.SidecarInstall(APP, requirements=[])
    assert again.installed and again.managed
    assert again.run() is True
    assert [s["status"] for s in again.status()["steps"]] == ["skipped"] * 3


def test_the_engine_is_moved_across_not_copied(tmp_path):
    """A 3 GB engine must not need 6 GB of disk for the length of an update."""
    live = _installed_with_state(tmp_path)
    venv_inode = os.stat(live / "venv").st_ino
    python_cfg_inode = os.stat(live / "venv" / "pyvenv.cfg").st_ino

    assert app_manager.update(_bundle(tmp_path, version="2.0.0", subdir="v2"), confirm=True).ok

    assert (live / "venv").is_dir(), "the update deleted venv/"
    assert os.stat(live / "venv").st_ino == venv_inode
    assert os.stat(live / "venv" / "pyvenv.cfg").st_ino == python_cfg_inode


def test_the_new_versions_update_hook_finds_the_engine_in_place(tmp_path):
    """An onUpdate that upgrades the engine needs it there when it runs."""
    live = _installed_with_state(tmp_path)
    probe = (
        "if [ -x venv/bin/python ]; then echo present; else echo absent; fi > engine_at_hook.txt"
    )

    res = app_manager.update(
        _bundle(tmp_path, version="2.0.0", subdir="v2", setup={"onUpdate": probe}), confirm=True
    )

    assert res.ok, res.error
    assert (live / "engine_at_hook.txt").read_text().strip() == "present"


def test_a_failed_update_gives_the_old_version_its_engine_and_data_back(tmp_path):
    live = _installed_with_state(tmp_path)
    venv_inode = os.stat(live / "venv").st_ino

    res = app_manager.update(
        _bundle(tmp_path, version="2.0.0", subdir="v2", setup={"onUpdate": "exit 9"}), confirm=True
    )

    assert not res.ok and "rolled back" in res.error
    assert manager._read_installed(APP).version == "1.0.0"
    assert (live / "main.py").read_text() == "VERSION = '1.0.0'\n"
    assert (live / "data" / "notes.json").read_text() == '{"notes": 4}'
    assert os.stat(live / "venv").st_ino == venv_inode
    assert _engine_answer(live) == "cloned-voice-engine"
    leftovers = [
        p.name for p in manager.apps_dir().iterdir() if p.name.startswith(".") and APP in p.name
    ]
    quarantined = [p.name for p in (manager.apps_dir() / ".quarantine").iterdir()]
    assert leftovers == [] and quarantined == [], (leftovers, quarantined)


def test_a_failed_update_does_not_hand_the_old_version_a_folder_its_hook_made(tmp_path):
    """Only what was carried forward goes back: the old version had no engine, and gets none."""
    assert app_manager.install(_bundle(tmp_path, version="1.0.0", subdir="v1"), confirm=True).ok
    live = manager.app_dir(APP)
    hook = "mkdir -p venv/bin && touch venv/bin/python && exit 3"

    res = app_manager.update(
        _bundle(tmp_path, version="2.0.0", subdir="v2", setup={"onUpdate": hook}), confirm=True
    )

    assert not res.ok and "rolled back" in res.error
    assert (live / "main.py").read_text() == "VERSION = '1.0.0'\n"
    assert not (live / "venv").exists()


def test_an_update_interrupted_after_the_swap_still_hands_the_engine_over(tmp_path):
    """The crash window the move opens: the new version is live, the engine is still in the
    old version's ``.rollback``. Startup recovery must give it to the live version, not drop it."""
    live = _installed_with_state(tmp_path)
    venv_inode = os.stat(live / "venv").st_ino
    rollback = manager.apps_dir() / f".{APP}.rollback"
    # The on-disk state at that instant: live → .rollback, the staged v2 (carrying a copy of
    # data/ and installed.json, as the update makes it) → live, nothing moved yet.
    live.rename(rollback)
    shutil.copytree(_bundle(tmp_path, version="2.0.0", subdir="v2"), live)
    shutil.copytree(rollback / "data", live / "data")
    shutil.copy2(rollback / manager.INSTALLED_META_FILENAME, live / manager.INSTALLED_META_FILENAME)

    app_manager.recover_interrupted_updates()

    assert not rollback.exists()
    assert (live / "main.py").read_text() == "VERSION = '2.0.0'\n"
    assert (live / "venv").is_dir(), "startup recovery dropped the engine with the stale rollback"
    assert os.stat(live / "venv").st_ino == venv_inode
    assert _engine_answer(live) == "cloned-voice-engine"
    assert (live / "data" / "notes.json").read_text() == '{"notes": 4}'


@pytest.mark.parametrize("rung", ["keep-data", "force"])
def test_removing_the_app_still_removes_its_engine(tmp_path, rung):
    """The contract the update must not blur: a removal takes the engine with the app's files.
    Keep-data keeps ``data/`` — what its dialog promises — and nothing else."""
    live = _installed_with_state(tmp_path)
    remove = app_manager.uninstall_keep_data if rung == "keep-data" else app_manager.force_uninstall

    assert remove(APP) is True

    assert not live.exists()
    parked = manager.apps_dir() / f".{APP}.data"
    if rung == "keep-data":
        assert sorted(p.name for p in parked.iterdir()) == ["notes.json"]
    else:
        assert not parked.exists()
    stray = [p for p in manager.apps_dir().rglob("pyvenv.cfg")]
    assert stray == [], stray
