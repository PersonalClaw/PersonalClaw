"""App Store catalog — what's available to install (Library/Store split).

Covers: native (Tier-1) apps are seeded into the Library and are locked-on, so
they never appear as "available to install"; git/local source list add/remove.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, catalog, manager
from personalclaw.apps import source as app_source
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
    # The APE-7 update-surfacing path persists its notified high-water mark via
    # entity_routes (entity_settings/app_updates.json) and falls back to an InboxStore
    # under inbox.config_dir — both bind config_dir at import into their own namespace,
    # so patch those too or the "no re-nag" state would touch the real ~/.personalclaw.
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    monkeypatch.setattr(_er, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(_inbox, "config_dir", lambda: tmp_path)
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


def test_default_first_party_git_source_present():
    """The published first-party apps repo ships as a Store default (so a plain
    pip install surfaces first-party apps, uninstalled). It is not user-removable."""
    defaults = catalog.list_git_sources()
    assert "https://github.com/PersonalClaw/PersonalClawApps.git" in defaults
    # A bundled default can't be removed via remove_git_source (it's not a user src).
    catalog.remove_git_source("https://github.com/PersonalClaw/PersonalClawApps.git")
    assert "https://github.com/PersonalClaw/PersonalClawApps.git" in catalog.list_git_sources()


def test_git_sources_add_remove(tmp_path):
    # Defaults are always present; user sources accumulate alongside them.
    assert "https://github.com/acme/cool-app.git" not in catalog.list_git_sources()
    catalog.add_git_source("https://github.com/acme/cool-app.git")
    assert "https://github.com/acme/cool-app.git" in catalog.list_git_sources()
    # idempotent
    catalog.add_git_source("https://github.com/acme/cool-app.git")
    assert catalog.list_git_sources().count("https://github.com/acme/cool-app.git") == 1
    catalog.remove_git_source("https://github.com/acme/cool-app.git")
    assert "https://github.com/acme/cool-app.git" not in catalog.list_git_sources()


# ── git-source install from a multi-app repo (PUBL-9) ──
#
# The published apps repo (PersonalClawApps) publishes NO app-registry.json, so the
# Store reaches it through the clone-then-subdir-scan fallback and installs one app at
# a time via a ``url#app`` pointer. These tests build a real bare git repo of the same
# shape and drive it over ``file://`` — the identical git code path, no network.


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=Fixture",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _app_manifest(name: str) -> str:
    return json.dumps(
        {
            "name": name,
            "version": "0.1.0",
            "displayName": name.replace("-", " ").title(),
            "description": f"{name} fixture app",
        }
    )


def _bare_repo_with_apps(root: Path, names: list[str], *, root_app: str = "") -> str:
    """A real bare git repo holding one app subdir per name → its ``file://`` URL.

    ``root_app`` instead writes a single ``app.json`` at the repo root (the single-app
    repo shape). ``root/work`` stays behind with the bare repo wired as ``origin`` so
    :func:`_publish_app` can add a commit later."""
    work = root / "work"
    work.mkdir(parents=True)
    _git("init", "--initial-branch=main", ".", cwd=work)
    (work / "README.md").write_text("fixture apps repo\n", encoding="utf-8")
    if root_app:
        (work / "app.json").write_text(_app_manifest(root_app), encoding="utf-8")
    for name in names:
        d = work / name
        d.mkdir()
        (d / "app.json").write_text(_app_manifest(name), encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "fixture apps", cwd=work)
    bare = root / "apps.git"
    _git("clone", "--bare", str(work), str(bare), cwd=root)
    _git("remote", "add", "origin", str(bare), cwd=work)
    return f"file://{bare}"


def _publish_app(root: Path, name: str) -> None:
    """Add one more app to the fixture repo and push it to the bare clone."""
    work = root / "work"
    d = work / name
    d.mkdir()
    (d / "app.json").write_text(_app_manifest(name), encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", f"add {name}", cwd=work)
    _git("push", "origin", "HEAD:main", cwd=work)


@pytest.fixture
def offline_git_sources(monkeypatch):
    """Drop the bundled default source and empty the module-global scan caches.

    ``available_catalog()`` shallow-clones every configured git source and the shipped
    default is the real published repo, so without this a test reaches github.com; the
    caches are process-global, so without the clear they leak between tests."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()
    yield
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()


def test_git_source_subdir_apps_surface_as_install_cards(tmp_path, offline_git_sources):
    """A multi-app git repo with no registry index surfaces one install card per app."""
    url = _bare_repo_with_apps(tmp_path / "repo", ["alpha-app", "beta-app"])
    catalog.add_git_source(url)

    cat = catalog.available_catalog()
    git_apps = cat["gitApps"]
    # Vacuity floor: a scan that discovers nothing must FAIL here, not pass forever on
    # an empty list (a broken clone degrades to [] by design).
    assert len(git_apps) == 2, git_apps
    by_name = {e["name"]: e for e in git_apps}
    assert set(by_name) == {"alpha-app", "beta-app"}
    for name, entry in by_name.items():
        assert entry["sourceKind"] == "git"
        assert entry["source"] == url
        # The install pointer install() has to accept for this card.
        assert entry["pointer"] == f"{url}#{name}"
    assert url in cat["gitSources"]


def test_catalog_git_pointer_resolves_to_the_named_app(tmp_path, offline_git_sources):
    """The pointer the catalog hands out IS an install source: it resolves to that
    app's directory, not the repo root. This is the end of the Store install path."""
    url = _bare_repo_with_apps(tmp_path / "repo", ["alpha-app", "beta-app"])
    catalog.add_git_source(url)
    cards = [e for e in catalog.available_catalog()["gitApps"] if e["name"] == "beta-app"]
    assert cards, "fixture produced no beta-app card"

    resolved = app_source.resolve(cards[0]["pointer"])
    try:
        manifest = json.loads((resolved.path / "app.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "beta-app"
        # A remote clone is untrusted → the scanner's external tier, and the caller
        # cleans up the whole clone, not just the app subdir.
        assert resolved.origin == "external"
        assert resolved.cleanup is True
        assert resolved.cleanup_path != resolved.path
    finally:
        if resolved.cleanup:
            app_source._rmtree(resolved.cleanup_path)


def test_multi_app_git_url_without_a_suffix_names_the_apps(tmp_path):
    """A multi-app repo URL pasted bare must say it holds many apps and how to pick
    one. It used to fail deep in staging as "no app.json in source" — true of the repo
    ROOT and useless to a user holding a repo full of apps."""
    url = _bare_repo_with_apps(tmp_path / "repo", ["alpha-app", "beta-app"])

    with pytest.raises(app_source.SourceError) as excinfo:
        app_source.resolve(url)
    msg = str(excinfo.value)
    assert "2 apps" in msg
    assert f"{url}#alpha-app" in msg  # the exact string the user should type
    assert "beta-app" in msg
    assert "app.json" not in msg  # not the old root-manifest message


def test_single_app_git_repo_still_resolves_at_its_root(tmp_path):
    """A repo with a root manifest is one app — the multi-app hint must not fire."""
    url = _bare_repo_with_apps(tmp_path / "repo", [], root_app="solo-app")

    resolved = app_source.resolve(url)
    try:
        manifest = json.loads((resolved.path / "app.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "solo-app"
    finally:
        if resolved.cleanup:
            app_source._rmtree(resolved.cleanup_path)


def test_git_repo_with_no_apps_resolves_and_leaves_the_manifest_error_to_install(tmp_path):
    """A repo holding no apps at all keeps the plain "no app.json" failure — the
    multi-app hint must not swallow the genuinely-appless case."""
    url = _bare_repo_with_apps(tmp_path / "repo", [])

    resolved = app_source.resolve(url)
    try:
        assert resolved.path.is_dir()
        assert not (resolved.path / "app.json").exists()
    finally:
        if resolved.cleanup:
            app_source._rmtree(resolved.cleanup_path)


def test_git_scan_is_cached_within_its_ttl(tmp_path, offline_git_sources):
    """The subdir scan caches per URL, so a second catalog read inside the TTL returns
    the OLD answer without re-cloning. Pinned so a validation run can tell a cached
    pass from a fresh one."""
    repo = tmp_path / "repo"
    url = _bare_repo_with_apps(repo, ["alpha-app"])

    first = catalog._scan_git_source(url, now=1000.0)
    assert {e.name for e in first} == {"alpha-app"}

    _publish_app(repo, "beta-app")
    cached = catalog._scan_git_source(url, now=1000.0 + catalog._GIT_SCAN_TTL_SECS - 1)
    assert {e.name for e in cached} == {"alpha-app"}, "second scan inside the TTL re-cloned"

    fresh = catalog._scan_git_source(url, now=1000.0 + catalog._GIT_SCAN_TTL_SECS + 1)
    assert {e.name for e in fresh} == {"alpha-app", "beta-app"}


def test_same_repo_with_and_without_dot_git_is_one_source(tmp_path):
    """GitHub serves a repo at both spellings, so they must not become two sources —
    two sources means two full clones per catalog refresh for one set of apps."""
    catalog.add_git_source("https://github.com/acme/cool-app.git")
    catalog.add_git_source("https://github.com/acme/cool-app")

    assert [s for s in catalog.list_git_sources() if "cool-app" in s] == [
        "https://github.com/acme/cool-app.git"
    ]
    # Either spelling removes it.
    catalog.remove_git_source("https://github.com/acme/cool-app")
    assert not [s for s in catalog.list_git_sources() if "cool-app" in s]


def test_published_default_source_is_not_duplicated_by_a_user_add():
    """Measured against the real gateway: POST /api/apps/sources with the published repo
    URL typed WITHOUT '.git' appended a SECOND source for the same repo — a full extra
    shallow clone per catalog refresh (3.4s measured) that surfaced zero extra apps."""
    user_sources = catalog.add_git_source("https://github.com/PersonalClaw/PersonalClawApps")

    assert [s for s in catalog.list_git_sources() if "PersonalClawApps" in s] == [
        "https://github.com/PersonalClaw/PersonalClawApps.git"
    ]
    # Nor persisted as a user source: the stored list must not shadow a default, or
    # retiring that default would silently hand the user a duplicate source.
    assert user_sources == []


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


def test_catalog_carries_the_declared_quality_block(tmp_path):
    """APE-4: a Store card badges the declared quality bar BEFORE install, so the
    catalog entry has to carry it — the pre-install card reads this payload, not
    ``/api/apps``. And an app that declared nothing must arrive as ``{}``, so the card
    renders no badges rather than a row of misses it never signed up for."""
    src = tmp_path / "myapps"
    src.mkdir()
    for name, quality in (("badged-app", {"tested": True, "a11y": False}), ("quiet-app", None)):
        d = src / name
        d.mkdir(parents=True)
        mani = {
            "name": name,
            "version": "1.0.0",
            "displayName": name.title(),
            "description": f"{name} fixture",
        }
        if quality is not None:
            mani["quality"] = quality
        (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    catalog.add_local_source(str(src))
    by_name = {a["name"]: a for a in catalog.available_catalog()["localApps"]}
    assert by_name["badged-app"]["quality"] == {"tested": True, "a11y": False}
    assert "designSystem" not in by_name["badged-app"]["quality"]
    assert by_name["quiet-app"]["quality"] == {}


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


# ── update surfacing (APE-7) ──


def _installed_app(root: Path, name: str, version: str) -> None:
    """Write an installed app under apps/<name>/ (installed.json + app.json), as
    `manager.list_apps` discovers it."""
    d = root / "apps" / name
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps({"name": name, "version": version, "enabled": True, "origin": "local"}),
        encoding="utf-8",
    )
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "displayName": name.title(),
                "description": f"{name} installed",
            }
        ),
        encoding="utf-8",
    )


def _source_app(root: Path, name: str, version: str) -> None:
    """Write a source-side app manifest (a local source dir of app subdirs)."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "displayName": name.title(),
                "description": f"{name} source",
            }
        ),
        encoding="utf-8",
    )


class _FakeState:
    """Minimal state: records notify() calls; no live inbox service (so
    emit_attention_item uses its own InboxStore under the patched config_dir)."""

    def __init__(self) -> None:
        self.notifications: list[tuple[str, str, str]] = []
        self._inbox_svc = None

    def notify(self, kind, title, body, *, meta=None):  # noqa: ANN001
        self.notifications.append((kind, title, body))


def test_updates_available_lists_only_newer(tmp_path):
    _installed_app(tmp_path, "notes", "1.0.0")
    _installed_app(tmp_path, "todo", "2.0.0")
    _installed_app(tmp_path, "wiki", "1.0.0")
    src = tmp_path / "myapps"
    src.mkdir()
    _source_app(src, "notes", "1.2.0")  # newer → listed
    _source_app(src, "todo", "1.9.0")  # older → not listed
    _source_app(src, "wiki", "1.0.0")  # same → not listed
    catalog.add_local_source(str(src))

    updates = catalog.updates_available()
    by_name = {u["name"]: u for u in updates}
    assert set(by_name) == {"notes"}
    assert by_name["notes"]["installedVersion"] == "1.0.0"
    assert by_name["notes"]["latestVersion"] == "1.2.0"
    assert by_name["notes"]["displayName"] == "Notes"


def test_updates_available_ignores_app_with_no_source(tmp_path):
    """An installed app whose source declares nothing newer (or isn't present) is absent."""
    _installed_app(tmp_path, "notes", "1.0.0")
    src = tmp_path / "myapps"
    src.mkdir()
    catalog.add_local_source(str(src))  # empty source
    assert catalog.updates_available() == []


def test_updates_available_takes_highest_across_sources(tmp_path):
    _installed_app(tmp_path, "notes", "1.0.0")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _source_app(a, "notes", "1.1.0")
    _source_app(b, "notes", "1.3.0")
    catalog.add_local_source(str(a))
    catalog.add_local_source(str(b))
    updates = {u["name"]: u for u in catalog.updates_available()}
    assert updates["notes"]["latestVersion"] == "1.3.0"


def test_surface_app_updates_emits_one_notification(tmp_path):
    _installed_app(tmp_path, "notes", "1.0.0")
    src = tmp_path / "myapps"
    src.mkdir()
    _source_app(src, "notes", "1.2.0")
    catalog.add_local_source(str(src))

    state = _FakeState()
    updates = catalog.surface_app_updates(state)
    assert {u["name"] for u in updates} == {"notes"}
    assert len(state.notifications) == 1
    kind, title, _ = state.notifications[0]
    # the wire kind resolves back to the registered apps/update pair
    from personalclaw import notification_kinds as nk

    assert nk.kind_for_legacy(kind).key == "apps/update"
    assert "Notes" in title


def test_surface_app_updates_does_not_renag_on_review(tmp_path):
    """Re-computing with the same latest_version emits no second notification (dedup by
    name + latest_version, persisted outside the inbox)."""
    _installed_app(tmp_path, "notes", "1.0.0")
    src = tmp_path / "myapps"
    src.mkdir()
    _source_app(src, "notes", "1.2.0")
    catalog.add_local_source(str(src))

    state = _FakeState()
    catalog.surface_app_updates(state)
    catalog.surface_app_updates(state)  # second view — must not re-nag
    assert len(state.notifications) == 1


def test_surface_app_updates_refires_only_for_a_newer_version(tmp_path):
    """A NEWER latest_version is a genuinely new event → a new notification fires."""
    _installed_app(tmp_path, "notes", "1.0.0")
    src = tmp_path / "myapps"
    src.mkdir()
    _source_app(src, "notes", "1.2.0")
    catalog.add_local_source(str(src))

    state = _FakeState()
    catalog.surface_app_updates(state)
    assert len(state.notifications) == 1
    # source bumps again → a new version the user hasn't been told about
    _source_app(src, "notes", "1.3.0")
    catalog.surface_app_updates(state)
    assert len(state.notifications) == 2
    assert catalog._load_notified()["notes"] == "1.3.0"


def test_surface_app_updates_no_state_is_noop_emit(tmp_path):
    """Called before the dashboard exists: still returns the list, emits nothing."""
    _installed_app(tmp_path, "notes", "1.0.0")
    src = tmp_path / "myapps"
    src.mkdir()
    _source_app(src, "notes", "1.2.0")
    catalog.add_local_source(str(src))
    updates = catalog.surface_app_updates(None)
    assert {u["name"] for u in updates} == {"notes"}


def test_app_update_notification_kind_is_registered():
    from personalclaw import notification_kinds as nk

    k = nk.resolve_kind("apps", "update")
    assert k.key == "apps/update"
    assert k.attention is True
    assert k.default_mode == "immediate"
    # the wire string round-trips (or a rule against it would be silently ignored)
    assert nk.kind_for_legacy_pair("apps", "update") == "app_update"
    assert nk.kind_for_legacy("app_update").key == "apps/update"


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


def _provider_app(root: Path, name: str, ptype: str, caps: list[str]) -> None:
    """A local-source app declaring a provider type AND its capabilities."""
    d = root / name
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0",
                "displayName": name.title(),
                "description": f"{name} fixture",
                "provider": {
                    "type": ptype,
                    "implementation": "provider:create_provider",
                    "capabilities": caps,
                },
            }
        ),
        encoding="utf-8",
    )


def test_catalog_entry_carries_declared_provider_capabilities(tmp_path):
    """ONBOARDING-UX OU-2: a Store card exposes the provider's DECLARED capabilities.

    ``providerType`` alone cannot tell a chat model from a speech model — a speech app
    is ``providerType: "model"`` with ``capabilities: ["stt"]`` — so a surface that
    groups apps by what they DO (the onboarding essential-apps step) would otherwise
    have to guess from author-controlled ``tags``, and would offer a transcription app
    as a chat provider that has nothing bindable behind it.
    """
    src = tmp_path / "firstparty"
    src.mkdir()
    _provider_app(src, "openai-models", "model", ["chat", "streaming", "embedding"])
    _provider_app(src, "faster-whisper", "model", ["stt"])
    _provider_app(src, "brave-search", "search", ["search"])
    catalog.add_local_source(str(src))

    by_name = {a["name"]: a for a in catalog.available_catalog()["localApps"]}
    assert by_name["openai-models"]["providerCapabilities"] == ["chat", "streaming", "embedding"]
    # The discriminator this field exists for: same providerType, different lane.
    assert by_name["faster-whisper"]["providerType"] == "model"
    assert by_name["faster-whisper"]["providerCapabilities"] == ["stt"]
    assert "chat" not in by_name["faster-whisper"]["providerCapabilities"]
    assert by_name["brave-search"]["providerCapabilities"] == ["search"]


def test_catalog_entry_capabilities_empty_for_a_non_provider_app(tmp_path):
    """An app that declares no provider gets an empty list, never a missing key —
    the frontend reads it unconditionally."""
    src = tmp_path / "plain"
    src.mkdir()
    d = src / "note-app"
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {"name": "note-app", "version": "1.0", "displayName": "Notes", "description": "x"}
        ),
        encoding="utf-8",
    )
    catalog.add_local_source(str(src))
    entry = next(a for a in catalog.available_catalog()["localApps"] if a["name"] == "note-app")
    assert entry["isProvider"] is False
    assert entry["providerCapabilities"] == []


# ── ET-4: the curated registry ships as a SEEDED, REMOVABLE default git source ──
#
# The mechanism under test and why it is not the bundled tuple: `_DEFAULT_GIT_SOURCES`
# is folded into every read of `list_git_sources()`, so removing one of those cannot
# persist. The registry is instead WRITTEN ONCE into app-sources.json plus a marker, so
# the normal DELETE removes it and the marker keeps it removed on the next start.


def _registry_fixture_repo(root: Path, *, app_name: str = "fixture-registry-app") -> str:
    """A real local git repo publishing an ``app-registry.json`` index — the POSITIVE
    CONTROL for "the seeded source is actually consulted".

    The shipped registry (`scratch/registry/registry.json`) is EMPTY until ET-6, so a
    test that asserted "zero listings from the registry" would pass with the source
    skipped entirely. This fixture publishes one listing, so the assertion below can
    only pass if the seeded source was fetched and parsed."""
    repo = root / "registry-fixture-repo"
    repo.mkdir()
    (repo / "app-registry.json").write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "name": app_name,
                        "repo": "https://example.invalid/fixture-app.git",
                        "displayName": "Fixture Registry App",
                        "description": "listed by the registry index, not cloned",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    git = [
        "git",
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", "app-registry.json"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "index"], cwd=repo, check=True)
    return str(repo)


def test_registry_source_seeds_only_behind_the_config_flag(tmp_path, monkeypatch):
    """The flag gates SEEDING: off ⇒ no source and NO marker (so a later on still seeds)."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    url = catalog._REGISTRY_GIT_SOURCE
    cfg_file = tmp_path / "config.json"

    cfg_file.write_text(json.dumps({"apps": {"registry_source_enabled": False}}), encoding="utf-8")
    assert catalog.seed_default_git_sources() == []
    assert url not in catalog.list_git_sources()
    # No marker either — the flag must be a real gate, not a one-shot that burns the seed.
    assert not (tmp_path / "apps" / "app-sources.json").is_file()

    cfg_file.write_text(json.dumps({"apps": {"registry_source_enabled": True}}), encoding="utf-8")
    assert catalog.seed_default_git_sources() == [url]
    assert url in catalog.list_git_sources()
    raw = json.loads((tmp_path / "apps" / "app-sources.json").read_text(encoding="utf-8"))
    assert url in raw["git"], "the registry must be a REAL row, not a fold-in default"
    assert "registry" in raw["seeded"]

    # A second start seeds nothing more and never duplicates the row.
    assert catalog.seed_default_git_sources() == []
    assert catalog.list_git_sources().count(url) == 1


def test_registry_source_seeds_on_a_fresh_home_with_no_config_file(tmp_path, monkeypatch):
    """A fresh install has no `apps` key at all — absence must take the shipped default (on).

    The polarity matters: reading the flag with the fail-closed `_expose_flag` alone would
    make a brand-new home seed NOTHING, which is the opposite of "ships as a default"."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    assert not (tmp_path / "config.json").is_file()
    assert catalog.seed_default_git_sources() == [catalog._REGISTRY_GIT_SOURCE]


def test_unreadable_flag_value_resolves_to_the_shipped_default(tmp_path, monkeypatch):
    """MEASURED platform behaviour, pinned so nobody re-derives it wrong.

    A non-bool at this path never reaches the field mapping: `load()`'s schema type-gate
    replaces it with the field's dataclass default first (`_apply_field_default`, logging
    "using default"). So a corrupted value resolves to the SHIPPED posture — registry ON —
    NOT to fail-closed-off. Worth a rail because the instinct on a flag that adds a network
    source is to guard it with `_expose_flag`, and such a guard would be dead code here."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    (tmp_path / "config.json").write_text(
        json.dumps({"apps": {"registry_source_enabled": "perhaps"}}), encoding="utf-8"
    )
    from personalclaw.config.loader import AppConfig

    assert AppConfig.load().apps.registry_source_enabled is True
    assert catalog.seed_default_git_sources() == [catalog._REGISTRY_GIT_SOURCE]


def test_removing_the_seeded_registry_source_survives_a_restart(tmp_path, monkeypatch):
    """Remove it, then RESTART: it must stay gone.

    "Restart" here is a genuinely fresh interpreter with fresh module state, reading the
    same home — the in-memory list can't lie to us, and neither can a monkeypatched
    module global. The subprocess sees the real `_REGISTRY_GIT_SOURCE`, so the URL it
    would re-seed is the same one this test removed."""
    url = catalog._REGISTRY_GIT_SOURCE
    assert catalog.seed_default_git_sources() == [url]
    assert url in catalog.list_git_sources()

    catalog.remove_git_source(url)
    assert url not in catalog.list_git_sources()
    raw = json.loads((tmp_path / "apps" / "app-sources.json").read_text(encoding="utf-8"))
    assert url not in raw["git"]
    assert "registry" in raw["seeded"], "the marker must OUTLIVE the removal, or it comes back"

    code = (
        "import json;from personalclaw.apps import catalog;"
        "print(json.dumps({'seeded': catalog.seed_default_git_sources(),"
        "'listed': catalog.list_git_sources()}))"
    )
    env = {**os.environ, "PERSONALCLAW_HOME": str(tmp_path)}
    env.pop("PERSONALCLAW_FIRST_PARTY_APPS_DIR", None)
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["seeded"] == [], "a restart re-seeded a source the user removed"
    assert url not in out["listed"]


def test_seeded_registry_is_a_default_the_user_may_remove(tmp_path, monkeypatch):
    """The two labelling bits the Store renders: "Default" yes, "unremovable" no."""
    url = catalog._REGISTRY_GIT_SOURCE
    catalog.seed_default_git_sources()
    assert url in catalog.default_git_sources()
    assert url not in catalog.builtin_git_sources()
    # The bundled apps repo is the other side of the contract: a default that CANNOT go.
    bundled = catalog._DEFAULT_GIT_SOURCES[0]
    assert bundled in catalog.default_git_sources()
    assert bundled in catalog.builtin_git_sources()


def test_the_seeded_registry_source_is_actually_consulted(tmp_path, monkeypatch):
    """The Store fetches the seeded source's index and lists what it publishes.

    Fails if seeding never happened, if the row never reached `list_git_sources()`, or if
    the catalog skipped the source — i.e. it is the falsifiable form of "a fresh home lists
    registry apps in the Store", run against a fixture index because the real registry
    ships empty until ET-6."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    # Neutralize the first-party local default (a set env var wins exclusively; a
    # nonexistent path disables it) so only the seeded source can contribute listings.
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "nope"))
    catalog._registry_cache.clear()
    catalog._git_scan_cache.clear()

    repo = _registry_fixture_repo(tmp_path)
    monkeypatch.setattr(catalog, "_REGISTRY_GIT_SOURCE", repo)
    assert catalog.seed_default_git_sources() == [repo]

    cat = catalog.available_catalog()
    assert repo in cat["gitSources"]
    assert repo in cat["defaultGitSources"]
    assert repo not in cat["builtinGitSources"]
    assert "fixture-registry-app" in [e["name"] for e in cat["remoteApps"]]


def test_seed_marker_survives_unrelated_source_edits(tmp_path, monkeypatch):
    """Every write path round-trips the marker. If `add_git_source` dropped it, the next
    start would resurrect a removed default — the defect this whole marker exists to stop."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    catalog.seed_default_git_sources()
    catalog.remove_git_source(catalog._REGISTRY_GIT_SOURCE)
    catalog.add_git_source("https://github.com/acme/unrelated.git")
    catalog.add_local_source(str(tmp_path))
    raw = json.loads((tmp_path / "apps" / "app-sources.json").read_text(encoding="utf-8"))
    assert "registry" in raw["seeded"]
    assert catalog.seed_default_git_sources() == []


def test_a_registry_listed_app_still_hits_the_scanner_gate(tmp_path, monkeypatch):
    """No new install path: an app the SEEDED REGISTRY lists installs through the one
    scanner-gated chokepoint, and a dangerous verdict is terminal even with confirm=True.

    Drives the whole route the user drives — seed the source, read the Store card, install
    by the exact `pointer` the card hands over. A bypass added for registry-sourced apps
    (an "official source, skip the scan" shortcut is the tempting one) reds this."""
    from personalclaw.supply_chain import Verdict

    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "nope"))
    catalog._registry_cache.clear()
    catalog._git_scan_cache.clear()

    # An app with content the scanner calls dangerous, published as the registry's listing.
    app_src = tmp_path / "src" / "dangerous-app"
    app_src.mkdir(parents=True)
    (app_src / "app.json").write_text(
        json.dumps(
            {
                "name": "dangerous-app",
                "version": "1.0.0",
                "displayName": "Dangerous App",
                "description": "registry-listed, scanner-refused",
            }
        ),
        encoding="utf-8",
    )
    (app_src / "scripts").mkdir()
    (app_src / "scripts" / "evil.sh").write_text("rm -rf / --no-preserve-root\n", encoding="utf-8")

    repo = tmp_path / "registry-fixture-repo"
    repo.mkdir()
    (repo / "app-registry.json").write_text(
        json.dumps({"apps": [{"name": "dangerous-app", "repo": str(app_src)}]}), encoding="utf-8"
    )
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", "app-registry.json"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "index"], cwd=repo, check=True)

    monkeypatch.setattr(catalog, "_REGISTRY_GIT_SOURCE", str(repo))
    assert catalog.seed_default_git_sources() == [str(repo)]

    card = next(
        e for e in catalog.available_catalog()["remoteApps"] if e["name"] == "dangerous-app"
    )
    res = app_manager.install(card["pointer"], confirm=True)
    assert not res.ok
    assert res.scan.verdict is Verdict.DANGEROUS
    assert not manager.app_dir("dangerous-app").exists()  # nothing landed live


def test_seeding_never_reaches_for_the_installer(tmp_path, monkeypatch):
    """The seeder writes a source LIST and nothing else — it cannot fetch, resolve or
    install. A companion to the behavioural gate test above: that one proves the gate
    still refuses, this one proves the seed path never had a chance to skip it."""
    seed_src = inspect.getsource(catalog.seed_default_git_sources)
    body = seed_src.split('"""', 2)[-1]
    for forbidden in ("install", "resolve", "clone", "requests", "urlopen"):
        assert forbidden not in body, f"the seeder must not {forbidden} anything"


# --- ET-4 negative clause, pinned structurally ------------------------------------
# "the scanner gate at install is unchanged (no new install path)" is a claim about
# what does NOT exist, and the behavioural rail above can only prove the ONE route it
# drives. A second route that skipped the gate would leave it green. So the census
# below pins the chokepoint itself: every app tree that lands on disk goes through
# ``default_scanner.scan(<staged tree>)``, and that call exists in exactly two places
# in the whole package. A third one is a new install path by definition.


def _scanner_gate_call_sites(token: str) -> set[tuple[str, str]]:
    """Census ``token`` across the shipped package → {(module path, enclosing def)}.

    Enclosing *function* rather than line number on purpose: a gate can be moved into
    a different function while the line count stays put, and the function is what the
    caller reaches. Uses ``ast`` so a match inside a string or comment still resolves
    to a real def (or to ``<module>`` if it is not inside one at all).
    """
    import ast

    import personalclaw

    pkg_root = Path(personalclaw.__file__).resolve().parent
    files = sorted(p for p in pkg_root.rglob("*.py"))
    # Vacuity floor: a broken glob (wrong root, wrong suffix) reads as "no call sites"
    # and would make every assertion below trivially true.
    assert len(files) > 100, f"census walked only {len(files)} files under {pkg_root}"

    sites: set[tuple[str, str]] = set()
    for path in files:
        text = path.read_text(encoding="utf-8")
        if token not in text:
            continue
        tree = ast.parse(text)
        owners: list[tuple[int, int, str]] = [
            (n.lineno, n.end_lineno or n.lineno, n.name)
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for lineno, line in enumerate(text.splitlines(), start=1):
            if token not in line:
                continue
            enclosing = [name for start, end, name in owners if start <= lineno <= end]
            sites.add(
                (
                    path.relative_to(pkg_root).as_posix(),
                    enclosing[-1] if enclosing else "<module>",
                )
            )
    return sites


def test_the_install_scanner_gate_has_exactly_two_call_sites():
    """No new install path — pinned by census, not by reading a diff.

    ``default_scanner.scan(`` is the supply-chain gate over a STAGED tree. The whole
    package reaches it from three places, and only two of them are app installs:

    * ``app_manager.install`` — first install of an app.
    * ``app_manager.update`` — re-install over an existing app.
    * ``supply_chain.scan_dir`` — the module-level re-export, whose only production
      caller is ``skills/marketplace.py`` (a SKILL, not an app). Listed so the census
      is the real one rather than a filtered one; it is not an app install path.

    Adding a registry-aware install route (the tempting shortcut is "it came from the
    curated registry, skip the scan") must either add a fourth call site — which reds
    this — or route around the scanner entirely, which reds
    ``test_a_registry_listed_app_still_hits_the_scanner_gate``. Between the two rails
    there is no third way to add an ungated install path.

    If you are here because this test went red: the fix is not to widen the expected
    set. It is to justify the new install path, and then widen it deliberately.
    """
    expected = {
        ("apps/app_manager.py", "install"),
        ("apps/app_manager.py", "update"),
        ("supply_chain.py", "scan_dir"),
    }
    assert _scanner_gate_call_sites("default_scanner.scan(") == expected

    # Vacuity control: the same census over a token that is not in the package must
    # come back empty. Without this, a walker that silently reads nothing (or an
    # `ast.parse` that raised into a swallowed except) would look like agreement.
    assert _scanner_gate_call_sites("default_scanner.scan_every_registry_app(") == set()


# --- ET-4 listing clause: the seeded source's index has ONE accepted filename -------
# "a fresh dev home lists registry apps in the Store" is the atom's one clause that
# cannot be closed from inside core, and the reasons are outside it: the seeded URL
# `https://github.com/PersonalClaw/registry.git` does not exist yet (`git ls-remote` →
# "Repository not found"), and ET-3's staged index is `{"apps": []}` until ET-6 lists
# something.
#
# What IS reachable is the CONTRACT between the two halves, and it turns out to be one
# filename and nothing else. Core enumerates a source's apps from an index named
# `catalog._REGISTRY_FILENAME` at the source root — the same contract for every git and
# local source. ET-3 publishes the same content under `registry.json`. Measured: an
# ET-3 schema-valid row lists with NO core change when the index carries core's name,
# and lists nothing when it carries ET-3's. So the rail below pins the filename as the
# whole of the remaining gap, so that whoever closes it (ET-5 owns reading the richer
# `maintainer`/`last_validated` fields) reaches for a rename rather than a parser
# change — and so that a future parser that quietly stops reading an ET-3-shaped row
# reds here instead of in a user's empty Store.

_STAGED_REGISTRY = Path(__file__).resolve().parent.parent / "scratch" / "registry"


def _et3_shaped_row(name: str) -> dict:
    """One listing row carrying every key ET-3's row schema marks required.

    Built against the schema rather than copied from it, so a new required field in
    `registry.schema.json` reds this instead of drifting silently.
    """
    schema = json.loads((_STAGED_REGISTRY / "registry.schema.json").read_text(encoding="utf-8"))
    required = schema["properties"]["apps"]["items"]["required"]
    assert required, "the row schema declares no required fields — fixture is vacuous"
    row = {
        "name": name,
        "repo": f"https://github.com/acme/{name}.git",
        "types": ["tool"],
        "permissions_declared": [],
        "license": "MIT",
        "maintainer": "acme",
        "added": "2026-08-25",
    }
    missing = set(required) - set(row)
    assert not missing, f"ET-3's row schema now requires {sorted(missing)} — widen the fixture"
    return row


def _seed_registry_publishing(index_name: str, tmp_path, monkeypatch) -> list[str]:
    """Publish one ET-3-shaped row under ``index_name`` in a local git repo, seed it as
    the default registry source, and return the app names the Store lists."""
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "nope"))
    catalog._registry_cache.clear()
    catalog._git_scan_cache.clear()

    repo = tmp_path / f"registry-{index_name}"
    repo.mkdir()
    (repo / index_name).write_text(
        json.dumps({"apps": [_et3_shaped_row("probe-app")]}), encoding="utf-8"
    )
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", index_name], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "index"], cwd=repo, check=True)

    monkeypatch.setattr(catalog, "_REGISTRY_GIT_SOURCE", str(repo))
    sources = tmp_path / "apps" / "app-sources.json"
    if sources.exists():
        sources.unlink()
    assert catalog.seed_default_git_sources() == [str(repo)]
    return [e["name"] for e in catalog.available_catalog().get("remoteApps", [])]


def test_the_seeded_registry_lists_only_under_cores_index_filename(tmp_path, monkeypatch):
    """The seeded default lists an ET-3-shaped row — and only when the index is named
    what core reads. The negative half is the measured gap; the positive half is what
    stops it being a rail that matches nothing.
    """
    assert _seed_registry_publishing(catalog._REGISTRY_FILENAME, tmp_path, monkeypatch) == [
        "probe-app"
    ]
    # ET-3's own filename. Same bytes, same row, same seeded source — no listing. This
    # is the entire remaining distance to the atom's unmet clause.
    assert _seed_registry_publishing("registry.json", tmp_path, monkeypatch) == []
