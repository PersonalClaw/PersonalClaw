"""Seed native (Tier-1) apps — ``native`` manifests become real installed apps on
first run (seed_builtin_apps), seeded ONCE, registered through the installed-app
path (never double), and LOCKED ON (disable/uninstall/force-uninstall refused).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.providers import loader


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Isolate the apps config dir AND point BUNDLED_DIR at a tmp fixture tree."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)

    bundled = tmp_path / "native"
    bundled.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", bundled)
    return tmp_path


def _native_manifest(root: Path, name: str, *, native: bool, provider: bool = True) -> None:
    d = root / "native" / name
    d.mkdir(parents=True)
    mani: dict = {
        "name": name,
        "version": "1.0.0",
        "displayName": name.title(),
        "description": f"{name} fixture",
    }
    if native:
        mani["native"] = True
    if provider:
        mani["provider"] = {
            "type": "search",
            "implementation": "personalclaw.search_providers.duckduckgo_provider:create_provider",
        }
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")


def test_seeds_native_app_as_installed(tmp_path):
    _native_manifest(tmp_path, "brave-search", native=True)
    seeded = app_manager.seed_builtin_apps()
    assert seeded == ["brave-search"]
    # It's now a real installed app: dir + installed.json (origin builtin, enabled).
    meta = manager._read_installed("brave-search")
    assert meta is not None
    assert meta.origin == "builtin" and meta.enabled
    assert (manager.app_dir("brave-search") / "app.json").is_file()
    assert (manager.app_dir("brave-search") / "data").is_dir()


def test_non_native_is_not_seeded(tmp_path):
    _native_manifest(tmp_path, "duckduckgo-search", native=False)
    seeded = app_manager.seed_builtin_apps()
    assert seeded == []
    assert manager._read_installed("duckduckgo-search") is None


def test_seed_is_idempotent_across_runs(tmp_path):
    _native_manifest(tmp_path, "brave-search", native=True)
    assert app_manager.seed_builtin_apps() == ["brave-search"]
    # Second run seeds nothing new (already-seeded marker).
    assert app_manager.seed_builtin_apps() == []


def test_native_app_is_locked_disable_and_uninstall_refused(tmp_path):
    """A native (Tier-1) app is locked on: disable / uninstall / force-uninstall
    all refuse, and it stays enabled + on disk."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    assert app_manager.disable("brave-search") is False
    assert app_manager.uninstall("brave-search") is False
    assert app_manager.force_uninstall("brave-search") is False
    meta = manager._read_installed("brave-search")
    assert meta is not None and meta.enabled is True  # still on
    assert (manager.app_dir("brave-search") / "app.json").is_file()  # still on disk


def test_native_app_survives_restart_reseed(tmp_path):
    """A seeded native app persists (installed record + registered) across a
    restart; the seed-once marker just avoids re-seeding, not de-registration."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    # Re-run seeding (simulates a gateway restart) — not re-seeded, still present.
    assert app_manager.seed_builtin_apps() == []
    meta = manager._read_installed("brave-search")
    assert meta is not None and meta.enabled is True


def test_native_manifest_resyncs_from_source_on_restart(tmp_path):
    """Bug #24: a native app's app.json is packaged-source-owned (the app is locked,
    user config lives in data/config.json). A manifest edit in apps/native/ MUST
    reach the existing install on the next boot — the old seed-once-skip stranded it
    (which is why the #21 create-task schema fix didn't propagate). Edit the source
    manifest, re-run seeding, and the INSTALLED app.json must reflect the change."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    installed = manager.app_dir("brave-search") / "app.json"
    assert "settingsSchema" not in installed.read_text()

    # Edit the SOURCE manifest (simulates a shipped manifest fix, e.g. a new field).
    src = tmp_path / "native" / "brave-search" / "app.json"
    mani = json.loads(src.read_text())
    mani["provider"]["settingsSchema"] = {
        "type": "object",
        "properties": {"new_field": {"type": "string"}},
    }
    src.write_text(json.dumps(mani), encoding="utf-8")

    # Re-run seeding (a gateway restart) — not re-seeded, but manifest re-synced.
    assert app_manager.seed_builtin_apps() == []
    resynced = json.loads(installed.read_text())
    assert "new_field" in (resynced["provider"]["settingsSchema"].get("properties") or {})


def test_native_manifest_resync_preserves_user_config_data(tmp_path):
    """The re-sync is manifest-only — it must never clobber data/config.json (the
    app's user config) or installed.json (enabled state)."""
    _native_manifest(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    data_cfg = manager.app_dir("brave-search") / "data" / "config.json"
    data_cfg.write_text(json.dumps({"user_setting": "keep-me"}), encoding="utf-8")

    # Change source manifest + re-seed.
    src = tmp_path / "native" / "brave-search" / "app.json"
    mani = json.loads(src.read_text())
    mani["description"] = "updated description"
    src.write_text(json.dumps(mani), encoding="utf-8")
    app_manager.seed_builtin_apps()

    # Manifest updated, but user config data untouched + still enabled.
    assert "updated description" in (manager.app_dir("brave-search") / "app.json").read_text()
    assert json.loads(data_cfg.read_text()) == {"user_setting": "keep-me"}
    assert manager._read_installed("brave-search").enabled is True


def test_native_app_skipped_by_bundled_discovery(tmp_path):
    """Native manifests register via the installed-app (seed) path only, so bundled
    discovery must skip them (no double registration). Post-taxonomy the native dir
    holds only native apps, so discovery is normally empty."""
    _native_manifest(tmp_path, "brave-search", native=True)
    _native_manifest(tmp_path, "stray-nonnative", native=False)
    discovered = {m.name for m in loader.discover_bundled_extensions()}
    assert "brave-search" not in discovered  # seeded → installed-app path
    assert "stray-nonnative" in discovered  # a non-native manifest still discovered


def test_ollama_migration_demotes_builtin_to_local(tmp_path):
    """ollama-models was de-cored from native to first-party but its installed.json
    still says origin=builtin, locking it. The migration in seed_builtin_apps() must
    downgrade origin to local and remove it from the seed marker so the user can
    disable/uninstall like any first-party app."""
    # Set up: ollama-models is in the seed marker and has origin=builtin (legacy state).
    # But its manifest does NOT have native:true (it's a normal first-party app).
    # Create a fake installed.json directly so the migration has something to fix.
    ollama_dir = tmp_path / "apps" / "ollama-models"
    ollama_dir.mkdir(parents=True)
    mani = {
        "name": "ollama-models",
        "version": "1.0.0",
        "displayName": "Ollama",
        "description": "local model runtime",
        "provider": {"type": "model", "implementation": "provider:create_provider"},
    }
    (ollama_dir / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    meta = {
        "name": "ollama-models",
        "version": "1.0.0",
        "displayName": "Ollama",
        "enabled": True,
        "installedAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "source": "builtin",
        "origin": "builtin",
        "resources": "gateway",
        "lifecycle": "gateway",
        "schemaVersion": 2,
    }
    (ollama_dir / "installed.json").write_text(json.dumps(meta), encoding="utf-8")

    # Seed marker includes it
    marker_path = tmp_path / "apps" / ".seeded-builtins.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"seeded": ["ollama-models"]}), encoding="utf-8")

    # Run seeding (no native manifests exist in BUNDLED_DIR — purely testing migration)
    app_manager.seed_builtin_apps()

    # After: origin demoted, seed marker no longer contains it.
    updated_meta = manager._read_installed("ollama-models")
    assert updated_meta is not None
    assert updated_meta.origin == "local"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert "ollama-models" not in marker["seeded"]

    # Confirm it is no longer native-locked
    assert not app_manager._is_native("ollama-models")
