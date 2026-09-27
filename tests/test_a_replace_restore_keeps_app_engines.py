"""A replace restore keeps the engine an app it brings back has here, and says what became of each.

What ``integration`` did, driven on a scratch home with Clone Voice and its engine installed: a
replace restore of that home's own snapshot moved the whole of ``apps/`` into
``pre-restore-<ts>/``, the engine (``apps/clone-voice/venv``) with it, and planted the snapshot's
app, which never carries one. The restored app read as having no engine and offered Install
engine, which builds the same engine again (gigabytes, for a real one), while the working one sat
in the backup, where nothing reads it and nothing ever removes it.

An engine is built for this machine: capture leaves it out and a restore never plants one, so it is
not the home's state and a replace has nothing to put in its place. The restore now hands it back
to the app of the same name, the way an update keeps an app's engine, and the app's own check (its
interpreter, and the packages the restored version declares) says whether it still fits. An app
the snapshot does not have is set aside whole, engine included, so moving it back still undoes the
restore; the restore names that engine and its size, and deleting the folder reclaims it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.local_models import sidecar
from personalclaw.snapshot import restore_main
from tests.test_a_snapshot_leaves_app_engines_behind import (
    ENGINE,
    SENTENCE,
    _app,
    _engine,
    _home,
    _install_engine,
    _snapshot,
)

NEWER_ENGINE = "pclaw-fixture-engine==2.0"


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._is_gateway_running", lambda: False)


def _backup(home: Path) -> Path:
    (backup,) = sorted(home.glob("pre-restore-*"))
    return backup


def _declare(live: Path, requirements: list[str]) -> None:
    """The live app moved on after the snapshot: a version declaring other engine packages."""
    manifest = json.loads((live / "app.json").read_text(encoding="utf-8"))
    manifest["dependencies"] = {"sidecarDependencies": requirements}
    (live / "app.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_an_app_the_restore_brings_back_keeps_the_engine_it_has_here(tmp_path, monkeypatch, capsys):
    """🔴 Red on integration: the engine went into the backup and the restored app had none."""
    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])
    archive = _snapshot(tmp_path)
    (live / "data" / "notes.txt").write_text("written after the snapshot", encoding="utf-8")
    engine_file = live / "venv" / "lib" / "python3.13" / "site-packages" / "pclaw_fixture_engine"

    capsys.readouterr()
    assert restore_main([str(archive), "--mode", "replace"]) == 0
    said = capsys.readouterr().out

    assert sidecar.SidecarInstall.for_app("clone-voice").installed, "the app has its engine"
    assert (engine_file / "__init__.py").is_file(), "the very environment this machine built"
    assert (live / "data" / "notes.txt").read_text(encoding="utf-8") == "the app's own state"
    displaced = _backup(home) / "apps" / "clone-voice"
    assert not (displaced / "venv").exists(), "nothing of the engine is left in the backup"
    assert (displaced / "data" / "notes.txt").read_text(encoding="utf-8") == (
        "written after the snapshot"
    ), "and what the restore replaced is still there to undo it"
    assert (
        "Kept the engine each of these apps had here: Clone Voice. An engine is built for this "
        "machine, so the restore leaves it in place." in said
    )
    assert SENTENCE not in said, "no Install engine for an engine that is installed"


def test_an_engine_that_does_not_fit_the_version_that_came_back_is_brought_up_to_date(
    tmp_path, monkeypatch, capsys
):
    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    archive = _snapshot(tmp_path)
    _declare(live, [NEWER_ENGINE])
    _engine(live / "venv", [NEWER_ENGINE])

    capsys.readouterr()
    assert restore_main([str(archive), "--mode", "replace"]) == 0
    said = [line for line in capsys.readouterr().out.splitlines() if "engine" in line]

    assert sidecar.SidecarInstall.for_app("clone-voice").installed is False
    assert said[-1] == (
        "⚠️  Clone Voice's engine here does not match the version the restore brought back. "
        "Install engine, on the app's card in Settings → Providers, brings it up to date."
    )
    install, ran = _install_engine(monkeypatch, "clone-voice")
    assert ran == ["pip"], "the environment is kept; only the packages the version declares change"
    assert install.installed


def test_an_app_the_snapshot_does_not_have_is_set_aside_with_its_engine_and_its_size(
    tmp_path, monkeypatch, capsys
):
    home = _home(tmp_path, monkeypatch, "home")
    _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    archive = _snapshot(tmp_path)
    later = _app(home, "later-voice", "Later Voice", engine=[ENGINE])
    _engine(later / "venv", [ENGINE])

    capsys.readouterr()
    assert restore_main([str(archive), "--mode", "replace"]) == 0
    said = capsys.readouterr().out

    assert not later.exists(), "the snapshot does not have the app"
    aside = _backup(home) / "apps" / "later-voice"
    assert sidecar.venv_python(aside / "venv").is_symlink(), "its engine went with it, whole"
    from personalclaw.durability.footprint import _tree_bytes, human_bytes

    size = human_bytes(_tree_bytes(aside / "venv", skip_files=set(), skip_dirs=set()))
    assert (
        f"Later Voice's engine ({size}) is in {aside}/ with the app, which the snapshot does not "
        "have. It goes when you delete that folder." in said
    )


def test_an_import_that_replaces_says_what_became_of_each_engine(tmp_path, monkeypatch):
    """The API's replace (``POST /api/durability/import?mode=replace``) returns the same account."""
    from personalclaw.durability.footprint import _tree_bytes
    from personalclaw.portability import apply_import_zip, create_export_zip

    home = _home(tmp_path, monkeypatch, "home")
    live = _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    _engine(live / "venv", [ENGINE])
    data, _manifest = create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(data)
    later = _app(home, "later-voice", "Later Voice", engine=[ENGINE])
    _engine(later / "venv", [ENGINE])

    summary = apply_import_zip(archive, mode="replace")

    aside = home / summary["pre_restore"] / "apps" / "later-voice" / "venv"
    assert summary["engines_kept"] == ["Clone Voice"]
    assert summary["engines_set_aside"] == [
        {"app": "Later Voice", "bytes": _tree_bytes(aside, skip_files=set(), skip_dirs=set())}
    ]
    assert sidecar.SidecarInstall.for_app("clone-voice").installed


def test_a_replace_with_no_engine_to_keep_says_nothing_about_engines(tmp_path, monkeypatch):
    """The control: an app with no engine here, restored, is neither kept nor set aside."""
    from personalclaw.snapshot import _do_replace

    home = _home(tmp_path, monkeypatch, "home")
    _app(home, "clone-voice", "Clone Voice", engine=[ENGINE])
    snap = tmp_path / "snap"
    _app(snap, "clone-voice", "Clone Voice", engine=[ENGINE])

    assert _do_replace(snap, home, None) == {"engines_kept": [], "engines_set_aside": []}
