"""An app update the gateway stopped in after the swap is finished at the next start.

An update swaps the new version's files in, drops the old ones, and only then writes the installed
record (version, tier, each rename moved and each dropped id's tombstone). A stop in between left
the new files under the old record: the version shown was the old one, and a rename the update
declared was never moved, because the next update compares against the files' manifest, which
already has the new id. The start-up recovery finished the swap and wrote nothing.

Now the update writes down, before the swap, which version it is putting live and what it replaces
(``apps/.<name>.update.json``), and the start finishes any update that names the version now live:
it writes the record and runs the moves and tombstones. One that never went live is forgotten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.apps import app_manager, capability_lifecycle, manager, update_recovery
from personalclaw.providers import registry as registry_module

APP = "mail-demo"

_PROVIDER_PY = "def create(config):\n    return None\n"


class _Stopped(BaseException):
    """The process going away part way: no `except Exception` sees it."""


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    registry_module.reset_provider_registry()
    yield tmp_path
    registry_module.reset_provider_registry()


@pytest.fixture
def movers():
    moved: list[tuple[str, str, str]] = []

    def record(app: str, old: str, new: str) -> None:
        moved.append((app, old, new))

    capability_lifecycle.register_mover(record)
    try:
        yield moved
    finally:
        capability_lifecycle.unregister_mover(record)


def _bundle(root: Path, version: str, **fields: Any) -> Path:
    d = root / f"v{version}" / APP
    d.mkdir(parents=True)
    manifest = {
        "name": APP,
        "version": version,
        "displayName": "Mail Demo",
        "description": "A demo mail app.",
    } | fields
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    return d


def _inbox(**extra: Any) -> dict[str, Any]:
    return {"type": "inbox", "implementation": "provider:create", **extra}


def _search(**extra: Any) -> dict[str, Any]:
    return {"type": "search", "implementation": "provider:create", **extra}


V1 = {"provider": _inbox(), "providers": [_search(id="news", label="Search the news")]}
#: Renames `inbox` and retires `news`.
V11 = {
    "providers": [_inbox(id="mail-inbox", label="Read your mail", renamedFrom=["inbox"])],
    "retiredCapabilities": ["news"],
}


def _marker() -> Path:
    return manager.apps_dir() / f".{APP}.update.json"


def _installed() -> manager.InstalledApp:
    meta = manager._read_installed(APP)
    assert meta is not None
    return meta


def _finished_as_v11(movers: list) -> None:
    meta = _installed()
    assert meta.version == "1.1.0", "the record still names the version the update replaced"
    assert movers == [(APP, "inbox", "mail-inbox")], "the rename was never moved"
    assert meta.capabilityMoves == {"inbox": {"to": "mail-inbox", "in": "1.1.0"}}
    assert set(meta.retiredCapabilities) == {"news"}, "the dropped id has no tombstone"
    assert not _marker().exists()


def _install_v1(tmp_path: Path) -> None:
    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok


def test_a_stop_after_the_swap_while_the_old_files_are_still_there_is_finished(
    tmp_path, monkeypatch, movers
) -> None:
    """The stop lands in the update's hook: the new files are live, the old ones in .rollback."""
    _install_v1(tmp_path)

    def stop(*_a: Any, **_kw: Any) -> None:
        raise _Stopped

    with monkeypatch.context() as m:
        m.setattr(app_manager, "_run_hook", stop)
        with pytest.raises(_Stopped):
            app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True)
    assert (manager.apps_dir() / f".{APP}.rollback").is_dir()
    assert _installed().version == "1.0.0"

    app_manager.recover_interrupted_updates()
    _finished_as_v11(movers)

    before = _installed()
    app_manager.recover_interrupted_updates()
    assert movers == [(APP, "inbox", "mail-inbox")], "a second recovery moved it again"
    assert _installed().capabilityMoves == before.capabilityMoves
    assert _installed().retiredCapabilities == before.retiredCapabilities


def test_a_stop_between_the_swap_and_the_record_is_finished(tmp_path, monkeypatch, movers) -> None:
    """The stop lands after the old files are gone and before the record is written."""
    _install_v1(tmp_path)
    real = app_manager._record_update
    calls = {"n": 0}

    def stop_once(*a: Any, **kw: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Stopped
        return real(*a, **kw)

    monkeypatch.setattr(app_manager, "_record_update", stop_once)
    with pytest.raises(_Stopped):
        app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True)
    assert not (manager.apps_dir() / f".{APP}.rollback").exists()
    assert _installed().version == "1.0.0"
    assert movers == []

    app_manager.recover_interrupted_updates()
    _finished_as_v11(movers)


def test_an_update_that_never_went_live_is_forgotten(tmp_path, monkeypatch, movers) -> None:
    """The stop lands between moving the old files aside and moving the new ones in: the start
    puts the old version back, and the update it named is not recorded."""
    _install_v1(tmp_path)
    real_move = app_manager.shutil.move
    moves = {"n": 0}

    def stop_on_second_move(src: str, dst: str, *a: Any, **kw: Any):
        moves["n"] += 1
        if moves["n"] == 2:
            raise _Stopped
        return real_move(src, dst, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "move", stop_on_second_move)
        with pytest.raises(_Stopped):
            app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True)
    assert _marker().is_file(), "the update wrote down nothing before the swap"

    app_manager.recover_interrupted_updates()
    assert _installed().version == "1.0.0"
    assert movers == []
    assert not _marker().exists()


def test_a_finished_or_failed_update_leaves_nothing_written_down(tmp_path, monkeypatch) -> None:
    _install_v1(tmp_path)
    assert app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True).ok
    assert not _marker().exists()

    def fail(*_a: Any, **_kw: Any) -> None:
        raise app_manager.AppLifecycleError("onUpdate failed")

    monkeypatch.setattr(app_manager, "_run_hook", fail)
    result = app_manager.update(_bundle(tmp_path, "1.2.0", **V11), confirm=True)
    assert not result.ok
    assert _installed().version == "1.1.0"
    assert not _marker().exists()


def test_a_note_that_names_no_app_is_dropped_at_the_start(tmp_path) -> None:
    _install_v1(tmp_path)
    stray = manager.apps_dir() / f".Not_An_App{update_recovery.NOTE_SUFFIX}"
    stray.write_text(json.dumps({"version": "9.9.9", "tier": "community"}), encoding="utf-8")
    app_manager.recover_interrupted_updates()
    assert not stray.exists(), "a note no app could have written stays, warning at every start"
    assert _installed().version == "1.0.0"


@pytest.mark.parametrize(
    "body",
    ["{not json", '{"version": "1.1.0"}', "[]"],
    ids=["not-json", "no-tier", "not-an-object"],
)
def test_a_note_that_cannot_be_read_as_one_is_dropped_at_the_start(tmp_path, body) -> None:
    _install_v1(tmp_path)
    _marker().write_text(body, encoding="utf-8")
    app_manager.recover_interrupted_updates()
    assert not _marker().exists(), "a note nothing can finish stays, warning at every start"
    assert _installed().version == "1.0.0"
