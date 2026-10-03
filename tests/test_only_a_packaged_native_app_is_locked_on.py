"""Only an app PersonalClaw ships is locked on, and an app from a source cannot make itself one.

A native app is one of the bundles core ships in ``apps/native/``. It cannot be switched off,
uninstalled or force-uninstalled. That lock was decided by the INSTALLED manifest's own ``native``
flag, and no install path refused the flag, so a Store app (from a git, local or first-party
source) that declared ``"native": true`` was locked on the moment it was installed: Deactivate,
Uninstall and Force uninstall all refused it, and the owner could not take it out of the product.

Native-ness is now where the app came from, never what its manifest says about itself: the
install record the packaged native source wrote (origin ``builtin``), for a name the packaged
source still ships. Every surface reads that one answer: the lock, the Apps list's ``native``, the
Providers card's ``managed``, an update and the update check. An install, an update or a review
from any other source whose manifest says ``native`` is refused before anything is written, and
the refusal names the field.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.apps import app_manager, backend_runtime, catalog, manager
from personalclaw.providers import loader

STORE_APP = "self-locking-app"
SHIPPED = "shipped-search"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Every write lands under ``tmp_path``, and the packaged native source is a fixture tree."""
    import personalclaw.config.loader as cfg
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    for module in (cfg, manager, catalog, _er, _inbox):
        monkeypatch.setattr(module, "config_dir", lambda: tmp_path)
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", packaged)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    assert manager.app_dir("probe").is_relative_to(tmp_path), "the app dir must be redirected"
    return tmp_path


def _bundle(root: Path, name: str, **extra) -> Path:
    d = root / name
    d.mkdir(parents=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name.replace("-", " ").title(),
        "description": f"{name} fixture",
        **extra,
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _store_source(tmp_path: Path, name: str = STORE_APP, **extra) -> Path:
    """An app as a Store source holds it: a folder the owner added, outside the package."""
    return _bundle(tmp_path / "store-source", name, **extra)


def _packaged(tmp_path: Path, name: str = SHIPPED, **extra) -> Path:
    """A native app as PersonalClaw ships it, in the packaged native source."""
    return _bundle(tmp_path / "packaged", name, native=True, **extra)


def _store_app_installed_with_the_flag(tmp_path: Path) -> None:
    """An app installed from a Store source before an install refused the flag: its installed
    ``app.json`` still says ``native``, and its record says where it really came from."""
    root = manager.app_dir(STORE_APP)
    (root / "data").mkdir(parents=True)
    (root / "app.json").write_text(
        json.dumps(
            {
                "name": STORE_APP,
                "version": "1.0.0",
                "displayName": "Self Locking App",
                "description": "came from a Store source",
                "native": True,
            }
        ),
        encoding="utf-8",
    )
    manager._write_installed(
        STORE_APP,
        manager.InstalledApp(
            name=STORE_APP,
            version="1.0.0",
            displayName="Self Locking App",
            enabled=True,
            source=str(tmp_path / "store-source" / STORE_APP),
            origin="local",
            tier="community",
        ),
    )


def _nothing_of(name: str) -> None:
    assert not manager.app_dir(name).exists(), "the app reached the live tree"
    assert manager._read_installed(name) is None, "an install record was written"
    staging = manager.apps_dir() / ".quarantine"
    assert not staging.exists() or not any(staging.iterdir()), "a staged copy was left behind"


# ── a Store app cannot declare itself native ────────────────────────────────────────────────


def test_an_install_of_a_store_app_that_says_it_is_native_is_refused_before_anything_is_written(
    tmp_path,
):
    src = _store_source(tmp_path, native=True)

    result = app_manager.install(src, confirm=True)

    assert not result.ok, "a Store app installed as a native app, locked on"
    assert "native" in result.error and STORE_APP in result.error, result.error
    _nothing_of(STORE_APP)


def test_the_review_names_the_field_and_offers_nothing_to_consent_to(tmp_path):
    src = _store_source(tmp_path, native=True)

    review = app_manager.preview(src)

    assert review.scan is None and not review.consent, "the review offered the install"
    assert "native" in review.error, review.error
    _nothing_of(STORE_APP)


def test_an_update_that_says_it_is_native_is_refused_and_the_installed_version_stays(tmp_path):
    assert app_manager.install(_store_source(tmp_path), confirm=True).ok
    newer = _bundle(tmp_path / "newer", STORE_APP, version="2.0.0", native=True)

    review = app_manager.preview(newer, name=STORE_APP)
    result = app_manager.update(newer, STORE_APP, confirm=True)

    assert review.scan is None and "native" in review.error, review.error
    assert not result.ok and "native" in result.error, result.error
    assert manager._read_installed(STORE_APP).version == "1.0.0"
    assert app_manager.uninstall(STORE_APP) is True, "the Store app must stay removable"


def test_an_install_the_owner_asks_for_still_says_why_over_http(tmp_path):
    """The Store dialog's two calls: the review answers 400 with the sentence, the install too."""

    async def drive() -> tuple[int, dict, int, dict, list[str]]:
        async with _client(tmp_path) as client:
            src = str(_store_source(tmp_path, native=True))
            review = await client.post("/api/apps/preview", json={"source": src})
            install = await client.post("/api/apps", json={"source": src, "consent": "x"})
            listed = (await (await client.get("/api/apps")).json())["apps"]
            return (
                review.status,
                await review.json(),
                install.status,
                await install.json(),
                [a["name"] for a in listed],
            )

    review_status, review, install_status, install, listed = asyncio.run(drive())

    assert review_status == 400 and review["error"]["code"] == "app_preview_failed", review
    assert "native" in review["error"]["message"], review
    assert install_status == 400 and "native" in install["error"], install
    assert STORE_APP not in listed
    _nothing_of(STORE_APP)


# ── an app installed with the flag before stays removable ───────────────────────────────────


def test_a_store_app_installed_with_the_flag_can_be_switched_off_and_removed(tmp_path):
    _store_app_installed_with_the_flag(tmp_path)

    assert app_manager.disable(STORE_APP) is True, "Deactivate refused a Store app"
    assert app_manager.uninstall(STORE_APP) is True, "Uninstall refused a Store app"
    assert app_manager.force_uninstall(STORE_APP) is True, "Force uninstall refused a Store app"
    assert not manager.app_dir(STORE_APP).exists()


def test_the_apps_list_reads_native_from_where_the_app_came_from(tmp_path):
    _store_app_installed_with_the_flag(tmp_path)
    _packaged(tmp_path)
    app_manager.seed_builtin_apps()

    rows = _list_rows()

    assert rows[STORE_APP]["native"] is False, "the Library hid Deactivate and Uninstall"
    assert rows[STORE_APP]["sourceKind"] == "local"
    assert rows[SHIPPED]["native"] is True
    assert rows[SHIPPED]["sourceKind"] == "native"


def test_the_providers_card_reads_managed_from_where_the_app_came_from(tmp_path):
    from personalclaw.apps import app_runtime
    from personalclaw.providers.registry import get_provider_registry, reset_provider_registry
    from personalclaw.providers.routes import handle_list_extensions

    search = {
        "type": "search",
        "implementation": "personalclaw.search_providers.duckduckgo_provider:create_provider",
    }
    _store_app_installed_with_the_flag(tmp_path)
    manifest_path = manager.app_dir(STORE_APP) / "app.json"
    flagged = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps({**flagged, "provider": search}), encoding="utf-8")
    _packaged(tmp_path, provider=search)
    reset_provider_registry()
    try:
        app_manager.seed_builtin_apps()
        app_runtime.start_installed(gateway=False)
        req = make_mocked_request("GET", "/api/providers", app=web.Application())
        cards = {
            c["name"]: c
            for c in json.loads(asyncio.run(handle_list_extensions(req)).text)["providers"]
        }
    finally:
        registry = get_provider_registry()
        for name in list(registry._extensions):
            registry.disable(name)
        reset_provider_registry()

    assert cards[STORE_APP]["managed"] is True, "Settings → Providers hid the Store app's switch"
    assert cards[SHIPPED]["managed"] is False


# ── a real native app stays locked ──────────────────────────────────────────────────────────


def test_a_native_app_the_package_seeded_still_refuses_every_removal(tmp_path):
    _packaged(tmp_path)
    assert app_manager.seed_builtin_apps() == [SHIPPED]

    assert app_manager.disable(SHIPPED) is False
    assert app_manager.uninstall(SHIPPED) is False
    assert app_manager.uninstall_keep_data(SHIPPED) is False
    assert app_manager.force_uninstall(SHIPPED) is False
    meta = manager._read_installed(SHIPPED)
    assert meta is not None and meta.enabled is True
    assert (manager.app_dir(SHIPPED) / "app.json").is_file()


def test_a_native_app_is_updated_with_personalclaw_never_from_a_source(tmp_path):
    _packaged(tmp_path)
    app_manager.seed_builtin_apps()
    impostor = _bundle(tmp_path / "store-source", SHIPPED, version="9.0.0")

    review = app_manager.preview(impostor, name=SHIPPED)
    result = app_manager.update(impostor, SHIPPED, confirm=True)

    assert review.scan is None and not review.consent, "the review offered the update"
    assert not result.ok, "a source replaced a native app's code, and it stayed locked on"
    installed = json.loads((manager.app_dir(SHIPPED) / "app.json").read_text(encoding="utf-8"))
    assert installed["version"] == "1.0.0"
    assert manager._read_installed(SHIPPED).version == "1.0.0"


def test_a_native_app_is_never_offered_an_update_from_a_source(tmp_path):
    _packaged(tmp_path)
    app_manager.seed_builtin_apps()
    local = tmp_path / "my-apps"
    _bundle(local, SHIPPED, version="9.0.0")
    catalog.add_local_source(str(local))

    assert SHIPPED not in {u["name"] for u in catalog.updates_available()}
    assert _list_rows()[SHIPPED]["updateAvailable"] is False


def test_the_packaged_source_reinstalls_a_missing_native_app_as_the_native_app_it_is(tmp_path):
    """The Store offers a native app whose install went missing, from the packaged source
    (``catalog.available_bundled``). Installed from there it is the native app it was, recorded
    as the seed records it, and locked."""
    packaged = _packaged(tmp_path)
    assert SHIPPED in {e.name for e in catalog.available_bundled()}

    result = app_manager.install(packaged, confirm=True)

    assert result.ok, result.error
    meta = manager._read_installed(SHIPPED)
    assert (meta.origin, meta.tier) == ("builtin", "builtin")
    assert app_manager.disable(SHIPPED) is False


def test_a_retired_native_app_stops_reading_as_shipped(tmp_path):
    """A name the package no longer ships is unlocked at boot, and its record stops saying it
    ships with PersonalClaw: the Tools page's badge and a contested tool name read the tier."""
    packaged = _packaged(tmp_path)
    app_manager.seed_builtin_apps()
    assert app_manager.trust_tier_of(SHIPPED) == "builtin"
    (packaged / "app.json").unlink()
    packaged.rmdir()

    app_manager.seed_builtin_apps()

    assert app_manager.disable(SHIPPED) is True
    assert app_manager.trust_tier_of(SHIPPED) == "community"


# ── an ordinary Store app is unchanged ──────────────────────────────────────────────────────


@pytest.mark.parametrize("declared", [{}, {"native": False}], ids=["no field", "native false"])
def test_an_ordinary_store_app_installs_switches_off_and_uninstalls(tmp_path, declared):
    src = _store_source(tmp_path, **declared)

    review = app_manager.preview(src)
    result = app_manager.install(src, consent=review.consent)

    assert review.consent and result.ok, result.error
    meta = manager._read_installed(STORE_APP)
    assert meta.origin == "local" and meta.tier == "community"
    assert app_manager.disable(STORE_APP) is True
    assert app_manager.enable(STORE_APP) is True
    assert app_manager.uninstall(STORE_APP) is True
    assert app_manager.force_uninstall(STORE_APP) is True
    assert not manager.app_dir(STORE_APP).exists()


def test_a_caller_cannot_record_a_store_app_as_shipped(tmp_path):
    """``builtin`` is the packaged source's alone: a caller that passes it for any other folder
    installs an ordinary app at the tier that folder earns."""
    result = app_manager.install(_store_source(tmp_path), origin="builtin", confirm=True)

    assert result.ok, result.error
    meta = manager._read_installed(STORE_APP)
    assert (meta.origin, meta.tier) == ("local", "community")
    assert app_manager.disable(STORE_APP) is True


# ── helpers ─────────────────────────────────────────────────────────────────────────────────


def _list_rows() -> dict[str, dict]:
    from personalclaw.dashboard.handlers import apps as apps_handlers

    req = make_mocked_request("GET", "/api/apps", app=web.Application())
    resp = asyncio.run(apps_handlers.api_apps_list(req))
    return {row["name"]: row for row in json.loads(resp.text)["apps"]}


@asynccontextmanager
async def _client(tmp_path: Path):
    from personalclaw.dashboard.handlers.apps import register_app_routes

    with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
        backend_runtime._supervisor = backend_runtime.BackendSupervisor()
        app = web.Application()
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            try:
                yield client
            finally:
                backend_runtime.get_backend_supervisor().stop_all()
