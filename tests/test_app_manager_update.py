"""App Platform atomic update + rollback (A2).

Covers: a clean update bumps version + runs onUpdate + preserves data/; a
dangerous new version is refused with the OLD app left intact; an onUpdate-hook
failure rolls back to the old version (provider re-registered); and a leftover
.{name}.rollback dir from a mid-swap crash is reconciled at startup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.supply_chain import Verdict


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _src(tmp_path: Path, *, version: str = "1.0.0", subdir: str = "src",
         setup: dict | None = None, files: dict[str, str] | None = None) -> Path:
    d = tmp_path / subdir / "demo-app"
    d.mkdir(parents=True)
    mani = {"name": "demo-app", "version": version, "displayName": "Demo", "description": "x"}
    if setup:
        mani["setup"] = setup
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    for rel, content in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


class TestUpdate:
    def test_clean_update_bumps_version(self, tmp_path):
        app_manager.install(_src(tmp_path, version="1.0.0"))
        res = app_manager.update(_src(tmp_path, version="2.0.0", subdir="src2"))
        assert res.ok
        assert manager._read_installed("demo-app").version == "2.0.0"

    def test_update_preserves_data_dir(self, tmp_path):
        app_manager.install(_src(tmp_path, version="1.0.0"))
        # write app state into data/
        data = manager.app_dir("demo-app") / "data"
        data.mkdir(parents=True, exist_ok=True)
        (data / "state.json").write_text('{"runs": 5}', encoding="utf-8")
        app_manager.update(_src(tmp_path, version="1.1.0", subdir="src2"))
        # data/ survived the swap
        assert (manager.app_dir("demo-app") / "data" / "state.json").read_text() == '{"runs": 5}'

    def test_update_runs_onupdate_hook(self, tmp_path):
        app_manager.install(_src(tmp_path, version="1.0.0"))
        res = app_manager.update(_src(
            tmp_path, version="1.1.0", subdir="src2",
            setup={"onUpdate": "echo done > updated_marker.txt"},
        ))
        assert res.ok
        assert (manager.app_dir("demo-app") / "updated_marker.txt").is_file()

    def test_dangerous_update_refused_old_intact(self, tmp_path):
        app_manager.install(_src(tmp_path, version="1.0.0"))
        res = app_manager.update(_src(
            tmp_path, version="2.0.0", subdir="src2",
            files={"scripts/evil.sh": "rm -rf / --no-preserve-root\n"},
        ), confirm=True)
        assert not res.ok and res.scan.verdict is Verdict.DANGEROUS
        # old version untouched
        assert manager._read_installed("demo-app").version == "1.0.0"
        assert manager.app_dir("demo-app").is_dir()

    def test_onupdate_failure_rolls_back(self, tmp_path):
        app_manager.install(_src(tmp_path, version="1.0.0"))
        res = app_manager.update(_src(
            tmp_path, version="2.0.0", subdir="src2", setup={"onUpdate": "exit 9"},
        ))
        assert not res.ok and "rolled back" in res.error
        # rolled back to 1.0.0, app still present, no leftover rollback dir
        assert manager._read_installed("demo-app").version == "1.0.0"
        assert manager.app_dir("demo-app").is_dir()
        assert not (manager.apps_dir() / ".demo-app.rollback").exists()

    def test_update_not_installed_is_error(self, tmp_path):
        res = app_manager.update(_src(tmp_path, version="2.0.0"))
        assert not res.ok and "not installed" in res.error


class TestCrashRecovery:
    def test_recovers_interrupted_update(self, tmp_path):
        # Simulate a crash AFTER move(live→rollback) but BEFORE move(new→live):
        # live is gone, a .rollback dir holds the old app.
        app_manager.install(_src(tmp_path, version="1.0.0"))
        live = manager.app_dir("demo-app")
        rollback = manager.apps_dir() / ".demo-app.rollback"
        import shutil
        shutil.move(str(live), str(rollback))
        assert not live.exists()
        recovered = app_manager.recover_interrupted_updates()
        assert "demo-app" in recovered
        assert live.is_dir() and (live / "app.json").is_file()
        assert not rollback.exists()

    def test_drops_stale_rollback(self, tmp_path):
        # live present AND a .rollback exists (swap completed, crash before cleanup):
        # the rollback is stale → dropped, live untouched.
        app_manager.install(_src(tmp_path, version="1.0.0"))
        rollback = manager.apps_dir() / ".demo-app.rollback"
        rollback.mkdir()
        (rollback / "app.json").write_text("{}", encoding="utf-8")
        recovered = app_manager.recover_interrupted_updates()
        assert "demo-app" not in recovered
        assert not rollback.exists()
        assert manager.app_dir("demo-app").is_dir()
