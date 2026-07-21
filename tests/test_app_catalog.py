"""App Store catalog — what's available to install (Library/Store split).

Covers: native (Tier-1) apps are seeded into the Library and are locked-on, so
they never appear as "available to install"; git/local source list add/remove.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, catalog, manager
from personalclaw.providers import loader


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    # catalog binds config_dir into its OWN namespace at import (from ... import
    # config_dir), so patch it there too — otherwise catalog._sources_path() escapes
    # the sandbox and reads/writes the real ~/.personalclaw/apps/app-sources.json.
    monkeypatch.setattr(catalog, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    # Neutralize the always-present first-party default source so these tests see a
    # clean local-source baseline (env set to a nonexistent path disables it).
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    return tmp_path


def _native(root: Path, name: str, *, native: bool, provider_type: str = "search") -> None:
    d = root / "native" / name
    d.mkdir(parents=True)
    mani: dict = {
        "name": name,
        "version": "1.0.0",
        "displayName": name.title(),
        "description": f"{name} fixture",
        "icon": "Plug",
        "provider": {
            "type": provider_type,
            "implementation": "personalclaw.search_providers.duckduckgo_provider:create_provider",
        },
    }
    if native:
        mani["native"] = True
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")


def test_native_app_seeded_then_absent_from_available(tmp_path):
    """A native app seeds into the Library, so it's not 'available to install'.
    (available_bundled surfaces only a native app MISSING from the Library — the
    defensive self-heal case — which doesn't happen in normal operation.)"""
    _native(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()  # → now in the Library
    assert "brave-search" not in {e.name for e in catalog.available_bundled()}


def test_native_app_cannot_be_force_uninstalled(tmp_path):
    """A native (Tier-1) app is locked: force-uninstall refuses, so it stays in the
    Library and never reappears as 'available'."""
    _native(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    assert app_manager.force_uninstall("brave-search") is False  # locked
    assert manager._read_installed("brave-search") is not None  # still installed
    assert "brave-search" not in {e.name for e in catalog.available_bundled()}


def test_missing_native_app_resurfaces_as_available(tmp_path):
    """Defensive self-heal: if a native app's installed record is somehow gone, it
    resurfaces in available_bundled so it can be restored (native apps are mandatory)."""
    _native(tmp_path, "brave-search", native=True)
    # NOT seeded → not in the Library → shows as available (native, provider search)
    entry = next((e for e in catalog.available_bundled() if e.name == "brave-search"), None)
    assert entry is not None
    assert entry.isProvider is True and entry.providerType == "search"
    assert entry.sourceKind == "native" and entry.icon == "Plug"


def test_git_sources_add_remove(tmp_path):
    assert catalog.list_git_sources() == []
    catalog.add_git_source("https://github.com/acme/cool-app.git")
    assert "https://github.com/acme/cool-app.git" in catalog.list_git_sources()
    # idempotent
    catalog.add_git_source("https://github.com/acme/cool-app.git")
    assert catalog.list_git_sources().count("https://github.com/acme/cool-app.git") == 1
    catalog.remove_git_source("https://github.com/acme/cool-app.git")
    assert "https://github.com/acme/cool-app.git" not in catalog.list_git_sources()


# ── local-directory app sources (workspace-core-app-split §4) ──


def _local_app(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0",
                "displayName": name.title(),
                "description": f"{name} local",
                "provider": {"type": "search", "implementation": "provider:create_provider"},
            }
        ),
        encoding="utf-8",
    )


def test_add_list_remove_local_source(tmp_path):
    src = tmp_path / "myapps"
    src.mkdir()
    assert catalog.list_local_sources() == []
    catalog.add_local_source(str(src))
    assert str(src) in catalog.list_local_sources()
    catalog.remove_local_source(str(src))
    assert catalog.list_local_sources() == []


def test_add_local_source_rejects_non_dir(tmp_path):
    with pytest.raises(ValueError):
        catalog.add_local_source(str(tmp_path / "does-not-exist"))


def test_local_source_apps_surface_in_catalog(tmp_path):
    src = tmp_path / "myapps"
    src.mkdir()
    _local_app(src, "tavily-search")
    catalog.add_local_source(str(src))
    cat = catalog.available_catalog()
    assert str(src) in cat["localSources"]
    assert "tavily-search" in {a["name"] for a in cat["localApps"]}
    # sourceKind flags it as local for the Store UI
    entry = next(a for a in cat["localApps"] if a["name"] == "tavily-search")
    assert entry["sourceKind"] == "local"


def test_first_party_source_is_present_and_not_removable(tmp_path, monkeypatch):
    """The first-party default source is always present, badges its apps
    'first-party', and refuses removal."""
    fp = tmp_path / "firstparty"
    fp.mkdir()
    _local_app(fp, "brave-search")
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(fp))
    # present in the list + its apps badged first-party
    assert str(fp) in catalog.list_local_sources()
    assert str(fp) in catalog.first_party_sources()
    cat = catalog.available_catalog()
    assert str(fp) in cat["firstPartySources"]
    entry = next(a for a in cat["localApps"] if a["name"] == "brave-search")
    assert entry["sourceKind"] == "first-party"
    # not removable
    with pytest.raises(ValueError):
        catalog.remove_local_source(str(fp))
    assert str(fp) in catalog.list_local_sources()


def test_git_and_local_sources_independent(tmp_path):
    src = tmp_path / "myapps"
    src.mkdir()
    catalog.add_git_source("https://github.com/x/pclaw-app-y")
    catalog.add_local_source(str(src))
    assert "https://github.com/x/pclaw-app-y" in catalog.list_git_sources()
    assert str(src) in catalog.list_local_sources()
    # removing one doesn't touch the other
    catalog.remove_git_source("https://github.com/x/pclaw-app-y")
    assert str(src) in catalog.list_local_sources()


def test_legacy_flat_sources_file_upgrades(tmp_path):
    """A pre-existing flat {"sources":[urls]} file reads as git sources (back-compat)."""
    p = tmp_path / "apps" / "app-sources.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"sources": ["https://github.com/x/legacy"]}), encoding="utf-8")
    assert "https://github.com/x/legacy" in catalog.list_git_sources()
    # adding a local source rewrites in the typed shape without losing the git one
    src = tmp_path / "myapps"
    src.mkdir()
    catalog.add_local_source(str(src))
    assert "https://github.com/x/legacy" in catalog.list_git_sources()
    assert str(src) in catalog.list_local_sources()


# ── SDK boundary (workspace-core-app-split §3) ──


def test_sdk_reexports_are_core_classes():
    """The SDK is a thin facade — its symbols ARE the core ABCs (one definition)."""
    from personalclaw.sdk.search import SearchProvider
    from personalclaw.search_providers.base import SearchProvider as CoreSP

    assert SearchProvider is CoreSP
    from personalclaw.sdk.tool import RiskLevel, ToolProvider
    from personalclaw.tool_providers.base import ToolProvider as CoreTP

    assert ToolProvider is CoreTP and RiskLevel is not None


def test_sdk_all_submodules_import():
    import importlib

    for name in (
        "search",
        "channel",
        "model",
        "memory",
        "embedding",
        "inbox",
        "knowledge",
        "prompt",
        "tool",
        "action",
        "manifest",
        "util",
    ):
        importlib.import_module(f"personalclaw.sdk.{name}")
    from personalclaw.sdk import SDK_VERSION

    assert isinstance(SDK_VERSION, str)


# ── P20: registry-index (federated app sources) ──────────────────────────────


def _write_registry(root: Path, apps: list[dict]) -> None:
    (root / "app-registry.json").write_text(json.dumps({"apps": apps}), encoding="utf-8")


def test_parse_registry_tolerant_of_shapes_and_garbage():
    # bare array OR {"apps":[...]}; drops nameless/malformed; dedups by name.
    bare = catalog._parse_registry(json.dumps([{"name": "a"}, {"name": "b", "repo": "u"}]))
    assert [p.name for p in bare] == ["a", "b"]
    obj = catalog._parse_registry(
        json.dumps({"apps": [{"name": "x"}, {"no": "name"}, "junk", {"name": "x"}]})
    )
    assert [p.name for p in obj] == ["x"]  # nameless + non-dict + dup dropped
    assert catalog._parse_registry("not json") == []


def test_local_source_registry_surfaces_remote_apps_without_dirscan(tmp_path):
    # A local source that publishes app-registry.json → its pointers become install
    # cards under remoteApps, WITHOUT any app.json on disk (no clone/dir-scan needed).
    src = tmp_path / "reg-src"
    src.mkdir()
    _write_registry(
        src,
        [
            {
                "name": "cool-app",
                "repo": "https://github.com/acme/cool.git",
                "subdirectory": "apps/cool",
                "displayName": "Cool App",
                "description": "neat",
            },
        ],
    )
    catalog.add_local_source(str(src))
    cat = catalog.available_catalog()
    remote = {a["name"]: a for a in cat["remoteApps"]}
    assert "cool-app" in remote
    e = remote["cool-app"]
    assert e["displayName"] == "Cool App" and e["sourceKind"] == "local"
    # the install POINTER carries repo + #subdirectory (routes through the scanner at install)
    assert e["pointer"] == "https://github.com/acme/cool.git#apps/cool"


def test_registry_index_is_cached_by_ttl(tmp_path):
    src = tmp_path / "reg-src"
    src.mkdir()
    _write_registry(src, [{"name": "app-one"}])
    p1 = catalog._fetch_registry_index(str(src), is_git=False, now=1000.0)
    assert [p.name for p in p1] == ["app-one"]
    # rewrite the index, but within the TTL the cached result stands
    _write_registry(src, [{"name": "app-two"}])
    p2 = catalog._fetch_registry_index(str(src), is_git=False, now=1000.0 + 100)
    assert [p.name for p in p2] == ["app-one"]  # cached
    # past the TTL → refetched
    p3 = catalog._fetch_registry_index(
        str(src), is_git=False, now=1000.0 + catalog._REGISTRY_TTL_SECS + 1
    )
    assert [p.name for p in p3] == ["app-two"]


def test_source_without_registry_falls_back_to_none(tmp_path):
    src = tmp_path / "plain-src"
    src.mkdir()  # no app-registry.json
    assert catalog._fetch_registry_index(str(src), is_git=False, now=5.0) is None


def test_registry_skips_already_installed(tmp_path):
    # A pointer whose app is already in the Library is not re-offered.
    src = tmp_path / "reg-src"
    src.mkdir()
    _write_registry(src, [{"name": "brave-search"}, {"name": "fresh-app"}])
    catalog.add_local_source(str(src))
    # brave-search is installed (native seed path); fresh-app is not.
    _native(tmp_path, "brave-search", native=True)
    app_manager.seed_builtin_apps()
    names = {a["name"] for a in catalog.available_catalog()["remoteApps"]}
    assert "fresh-app" in names and "brave-search" not in names


# ── P29: install-consent transparency (permissions + declared crons in the catalog) ──


def test_catalog_surfaces_permissions_and_crons_for_review(tmp_path):
    # An app that declares permissions + a cron surfaces them in its Store card so the
    # user can review WHAT it will be granted + WHAT it will run BEFORE installing.
    src = tmp_path / "consent-src"
    src.mkdir()
    d = src / "reminder-app"
    d.mkdir()
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": "reminder-app",
                "version": "1.0",
                "displayName": "Reminder App",
                "description": "posts a daily reminder",
                "permissions": {"cron": True, "api": ["/api/inbox"]},
                # a manifest cron runs an AGENT with a MESSAGE (that's how app_crons builds the
                # scheduled job) — the review summary must surface those, not a phantom action.
                "crons": [
                    {
                        "name": "daily-reminder",
                        "cron_expr": "0 9 * * *",
                        "agent": "reminder-bot",
                        "message": "Post today's reminders to the inbox.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog.add_local_source(str(src))
    entry = next(a for a in catalog.available_catalog()["localApps"] if a["name"] == "reminder-app")
    # permissions surfaced for review
    assert entry["permissions"].get("cron") is True
    assert "/api/inbox" in (entry["permissions"].get("api") or [])
    # declared crons surfaced (name + cadence + WHAT it runs) so the user sees the recurring job
    assert len(entry["crons"]) == 1
    c = entry["crons"][0]
    assert c["name"] == "daily-reminder"
    assert c["cron_expr"] == "0 9 * * *"
    # the truthful "what it runs" fields — agent + its prompt (not an action/command that
    # a manifest cron never has); this is what would silently be empty before the fix.
    assert c["agent"] == "reminder-bot"
    assert c["message"] == "Post today's reminders to the inbox."


def test_catalog_no_permissions_crons_is_empty_not_missing(tmp_path):
    # An app with no permissions/crons → empty dict/list (stable shape for the FE), not absent.
    src = tmp_path / "plain-consent"
    src.mkdir()
    _local_app(src, "plain-app")
    catalog.add_local_source(str(src))
    entry = next(a for a in catalog.available_catalog()["localApps"] if a["name"] == "plain-app")
    assert entry["permissions"] == {} and entry["crons"] == []


# ── P13–P16: installed_logger_roots() — the runtime replacement for the removed
#    constants.APP_LOGGER_ROOTS. Derives app log-namespace roots from ENABLED
#    installed apps' manifests (JSON only), de-duped, () when no apps dir. ──


def _install_app(root: Path, name: str, *, logger_roots: list[str], enabled: bool = True) -> None:
    """Write an installed app under ``apps/<name>/`` — installed.json (enabled state) +
    app.json (manifest with loggerRoots) — mirroring what manager.list_apps() reads."""
    d = root / "apps" / name
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name.title(),
                "enabled": enabled,
                "origin": "registry",
                "resources": "gateway",
                "lifecycle": "gateway",
            }
        ),
        encoding="utf-8",
    )
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name.title(),
                "description": f"{name} fixture",
                "loggerRoots": logger_roots,
            }
        ),
        encoding="utf-8",
    )


def test_installed_logger_roots_empty_when_no_apps_dir(tmp_path):
    # P16: fresh install — no apps/ dir yet → () (callers degrade to just 'personalclaw').
    assert not (tmp_path / "apps").exists()
    assert catalog.installed_logger_roots() == ()


def test_installed_logger_roots_collects_enabled_manifest_roots(tmp_path):
    # P13: an ENABLED app that declares loggerRoots contributes them.
    _install_app(tmp_path, "slack-app", logger_roots=["slack_runtime"])
    assert catalog.installed_logger_roots() == ("slack_runtime",)


def test_installed_logger_roots_skips_disabled_and_dedups(tmp_path):
    # P14: disabled apps contribute nothing; roots are de-duped preserving first-seen order.
    _install_app(tmp_path, "alpha-app", logger_roots=["alpha_rt", "shared_rt"])
    _install_app(tmp_path, "beta-app", logger_roots=["shared_rt", "beta_rt"])  # shared_rt dup
    _install_app(tmp_path, "off-app", logger_roots=["ghost_rt"], enabled=False)  # skipped
    roots = catalog.installed_logger_roots()
    assert roots == ("alpha_rt", "shared_rt", "beta_rt")
    assert "ghost_rt" not in roots  # disabled app's root never plumbed


def test_installed_logger_roots_ignores_apps_without_roots(tmp_path):
    # P15: an installed app with no loggerRoots (the common case) contributes nothing —
    # the roots list only carries apps that actually declare a non-personalclaw namespace.
    _install_app(tmp_path, "plain-app", logger_roots=[])
    _install_app(tmp_path, "logging-app", logger_roots=["custom_rt"])
    assert catalog.installed_logger_roots() == ("custom_rt",)
