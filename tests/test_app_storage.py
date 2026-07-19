"""App filesystem storage model (A1/A2/A4).

The storage contract an app can rely on:
* Each app has an ISOLATED data dir at ``~/.personalclaw/apps/{name}/data/`` —
  its own sandbox. The DIRECTORY always exists (created at install); the BACKEND
  is handed its path only when the app declares the ``storage`` capability
  (untrusted-app sandbox P3), so an app without it has no sanctioned persistence.
* It exists before any lifecycle hook runs and before the backend launches.
* It is PRESERVED across updates (A2 swap) and across disable/enable.
* The backend subprocess is handed the absolute path via the
  ``PERSONALCLAW_APP_DATA_DIR`` env var — a stable contract, not a guess.
* It is per-app (one app cannot see another's data dir).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _app(tmp_path: Path, name: str, *, version="1.0.0", subdir="src", backend=False,
         permissions: dict | None = None) -> Path:
    d = tmp_path / subdir / name
    d.mkdir(parents=True)
    mani = {"name": name, "version": version, "displayName": name, "description": "x"}
    if permissions is not None:
        mani["permissions"] = permissions
    if backend:
        mani["backend"] = {"entryPoint": "backend/server.py", "type": "python"}
        bd = d / "backend"; bd.mkdir()
        (bd / "server.py").write_text("import os\nprint(os.environ.get('PERSONALCLAW_APP_DATA_DIR'))\n", encoding="utf-8")
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    return d


def test_data_dir_isolated_per_app(tmp_path):
    app_manager.install(_app(tmp_path, "app-a"))
    app_manager.install(_app(tmp_path, "app-b", subdir="s2"))
    da, db = manager.app_data_dir("app-a"), manager.app_data_dir("app-b")
    assert da != db
    assert da == manager.app_dir("app-a") / "data"
    # writing to one doesn't touch the other
    (da / "x.txt").write_text("a", encoding="utf-8")
    assert not (db / "x.txt").exists()


def test_data_dir_survives_update(tmp_path):
    app_manager.install(_app(tmp_path, "app-a"))
    data = manager.app_data_dir("app-a")
    (data / "state.json").write_text('{"runs": 7}', encoding="utf-8")
    app_manager.update(_app(tmp_path, "app-a", version="2.0.0", subdir="s2"))
    assert (manager.app_dir("app-a") / "data" / "state.json").read_text() == '{"runs": 7}'


def test_data_dir_survives_disable_enable(tmp_path):
    app_manager.install(_app(tmp_path, "app-a"))
    data = manager.app_data_dir("app-a")
    (data / "keep.txt").write_text("v", encoding="utf-8")
    app_manager.disable("app-a")
    app_manager.enable("app-a")
    assert (manager.app_dir("app-a") / "data" / "keep.txt").read_text() == "v"


def test_backend_gets_data_dir_env(tmp_path, monkeypatch):
    # The supervisor must pass PERSONALCLAW_APP_DATA_DIR to the backend env.
    from personalclaw.apps import backend_runtime
    captured = {}

    class _FakeProc:
        def __init__(self): self.pid = 4321
        def poll(self): return None

    def _fake_popen(cmd, cwd=None, env=None, **kw):
        captured["env"] = env
        captured["cwd"] = cwd
        return _FakeProc()

    monkeypatch.setattr(backend_runtime.subprocess, "Popen", _fake_popen)
    sup = backend_runtime.BackendSupervisor()
    from personalclaw.apps.manifest import AppManifest
    # storage is a declared capability (sandbox P3) — grant it so the backend
    # receives its DATA_DIR.
    app_manager.install(_app(tmp_path, "svc", backend=True, permissions={"storage": True}))
    manifest = AppManifest.from_json_file(manager.app_dir("svc") / "app.json")
    sup.start(manifest)
    assert captured["env"]["PERSONALCLAW_APP_DATA_DIR"] == str(manager.app_dir("svc") / "data")
    assert captured["env"]["PERSONALCLAW_APP_NAME"] == "svc"


def test_backend_without_storage_permission_gets_no_data_dir(tmp_path, monkeypatch):
    """Sandbox P3: a backend whose app does NOT declare the storage capability is
    NOT handed PERSONALCLAW_APP_DATA_DIR (no sanctioned persistence path)."""
    from personalclaw.apps import backend_runtime
    captured: dict = {}

    class _FakeProc:
        def __init__(self): self.pid = 4322
        def poll(self): return None

    def _fake_popen(cmd, cwd=None, env=None, **kw):
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(backend_runtime.subprocess, "Popen", _fake_popen)
    sup = backend_runtime.BackendSupervisor()
    from personalclaw.apps.manifest import AppManifest
    app_manager.install(_app(tmp_path, "nostore", backend=True))  # no permissions
    manifest = AppManifest.from_json_file(manager.app_dir("nostore") / "app.json")
    sup.start(manifest)
    assert "PERSONALCLAW_APP_DATA_DIR" not in captured["env"]
    assert captured["env"]["PERSONALCLAW_APP_NAME"] == "nostore"


# ── app-name guard: app_data_dir must not create dirs for invalid names ──
# Regression: a fuzzed/invalid name handed to app_data_dir() used to silently
# mkdir a junk dir under apps/, which once accumulated 16k empty dirs and made
# list_apps() stat-storm. The guard rejects non-kebab names before any mkdir.


@pytest.mark.parametrize("good", ["snippet-lab", "a", "brave-search", "bedrock-models"])
def test_app_data_dir_accepts_valid_names(tmp_path, good):
    d = manager.app_data_dir(good)
    assert d == manager.app_dir(good) / "data"
    assert d.is_dir()


@pytest.mark.parametrize("bad", ["a--q0", "Not_Kebab", "../evil", "", ".", "UPPER", "a b"])
def test_app_data_dir_rejects_invalid_names_without_creating_dirs(tmp_path, bad):
    before = {p.name for p in (tmp_path / "apps").iterdir()} if (tmp_path / "apps").is_dir() else set()
    with pytest.raises(ValueError):
        manager.app_data_dir(bad)
    after = {p.name for p in (tmp_path / "apps").iterdir()} if (tmp_path / "apps").is_dir() else set()
    # No junk directory was created by the rejected call.
    assert before == after
