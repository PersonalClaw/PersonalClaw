"""Each capability of an app has its own switch and its own status (#3843).

The owner can run one capability of an app without the others: an app's ``enabled`` stays the
master switch, and each capability it declares is on or off by itself in the installed record
(``capabilities: {"<id>": {"on": true|false}}``). A capability that is off is not built, so it
reads no settings and starts nothing, and switching one changes that one alone. One that fails
leaves the others running, and the app's row says which runs and why one does not.

A manifest's ``startsOn: false`` installs a capability off. Until the app panel can switch one on,
a capability whose manifest says nothing starts on, as every capability did before the switches.
A record written before the switches runs every capability, and a capability an update adds
starts off: the switch is the owner's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_manager, app_runtime, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import registry as registry_module

pytestmark = pytest.mark.security

APP = "mail-kit"
_PROVIDER_PY = "def create(config):\n    return None\n"


class _Recorder:
    """A type handler that builds nothing real: it logs each build (``+key``) and teardown
    (``-key``), and a capability named in ``fail`` raises at build with that sentence."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.fail: dict[str, str] = {}

    def create(self, ext: Any) -> Any:
        if ext.capability in self.fail:
            raise RuntimeError(self.fail[ext.capability])
        self.log.append(f"+{ext.key}")
        return object()

    def register(self, ext: Any, instance: Any) -> None:
        return None

    def deregister(self, ext: Any, instance: Any) -> None:
        self.log.append(f"-{ext.key}")


@pytest.fixture
def built(tmp_path, monkeypatch) -> _Recorder:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    recorder = _Recorder()
    reg = registry_module.ProviderRegistry()
    for type_ in ("inbox", "search", "sync"):
        reg.register_type_handler(type_, recorder)  # type: ignore[arg-type]
    monkeypatch.setattr(registry_module, "_registry", reg)
    return recorder


def _provider(type_: str, id_: str, label: str, **extra: Any) -> dict[str, Any]:
    return {"type": type_, "implementation": "provider:create", "id": id_, "label": label} | extra


INBOX = _provider("inbox", "inbox", "Mail in your Inbox")
SEARCH = _provider("search", "search", "Search your mail")
SYNC = _provider("sync", "sync", "Keep folders in step", startsOn=False)


def _bundle(root: Path, version: str, providers: list[dict[str, Any]]) -> Path:
    d = root / f"v{version}" / APP
    d.mkdir(parents=True)
    manifest = {
        "name": APP,
        "version": version,
        "displayName": "Mail Kit",
        "description": "A demo mail app.",
        "providers": providers,
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    return d


def _install(root: Path, providers: list[dict[str, Any]] | None = None) -> None:
    res = app_manager.install(
        _bundle(root, "1.0.0", providers or [INBOX, SEARCH, SYNC]), confirm=True
    )
    assert res.ok, res.error


def _running() -> dict[str, bool]:
    return {
        r.capability: r.enabled for r in registry_module.get_provider_registry().list_for_app(APP)
    }


def _switches() -> dict[str, dict[str, Any]]:
    meta = manager._read_installed(APP)
    assert meta is not None
    return meta.capabilities


@web.middleware
async def _owner(request, handler):
    request["user"] = "owner"
    request["app"] = ""
    return await handler(request)


def _gateway() -> web.Application:
    from personalclaw.dashboard.handlers.apps import register_app_routes

    gw = web.Application(middlewares=[_owner])
    register_app_routes(gw)
    return gw


def _row(listing: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in listing["apps"] if a["name"] == APP)


# ── the record ───────────────────────────────────────────────────────────────────────────────


def test_an_install_switches_each_capability_as_its_manifest_starts_it(tmp_path, built) -> None:
    _install(tmp_path)
    assert _switches() == {"inbox": {"on": True}, "search": {"on": True}, "sync": {"on": False}}
    assert _running() == {"inbox": True, "search": True, "sync": False}
    assert f"+{APP}/sync" not in built.log, "a capability that starts off is never built"


def test_a_switch_that_reads_as_neither_yes_nor_no_is_off() -> None:
    meta = manager.InstalledApp.from_dict(
        {
            "name": APP,
            "capabilities": {
                "inbox": {"on": True},
                "search": {"on": "maybe"},
                "sync": {"on": "false"},
                "notes": {},
            },
        }
    )
    assert meta.capabilities == {
        "inbox": {"on": True},
        "search": {"on": False},
        "sync": {"on": False},
        "notes": {"on": False},
    }
    assert manager.InstalledApp.from_dict(meta.to_dict()).capabilities == meta.capabilities


def test_starts_on_must_be_true_or_false() -> None:
    manifest = AppManifest.from_dict(
        {
            "name": APP,
            "version": "1.0.0",
            "description": "A demo mail app.",
            "providers": [_provider("sync", "sync", "Keep folders in step", startsOn="no")],
        }
    )
    assert any("startsOn must be true or false" in e for e in manifest.validate())
    declared = AppManifest.from_dict(
        {
            "name": APP,
            "version": "1.0.0",
            "description": "A demo mail app.",
            "providers": [SYNC, SEARCH],
        }
    )
    assert [p.startsOn for p in declared.all_providers()] == [False, True]
    again = AppManifest.from_dict(declared.to_dict())
    assert [p.startsOn for p in again.all_providers()] == [False, True]


# ── one switch at a time ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switching_one_capability_changes_that_one_alone(tmp_path, built) -> None:
    _install(tmp_path)
    built.log.clear()
    async with TestClient(TestServer(_gateway())) as client:
        off = await client.post(f"/api/apps/{APP}/capabilities/search/off")
        assert off.status == 200, await off.text()
        assert await off.json() == {
            "ok": True,
            "name": APP,
            "capability": "search",
            "on": False,
            "status": "off",
            "reason": "",
        }
        assert built.log == [f"-{APP}/search"], "only that capability is torn down"
        assert _running() == {"inbox": True, "search": False, "sync": False}
        assert _switches()["search"] == {"on": False}

        on = await client.post(f"/api/apps/{APP}/capabilities/sync/on")
        assert on.status == 200, await on.text()
        body = await on.json()
        assert (body["on"], body["status"], body["reason"]) == (True, "running", "")
        assert built.log == [f"-{APP}/search", f"+{APP}/sync"]
        assert _running() == {"inbox": True, "search": False, "sync": True}
        assert _switches() == {
            "inbox": {"on": True},
            "search": {"on": False},
            "sync": {"on": True},
        }


@pytest.mark.asyncio
async def test_a_capability_the_app_does_not_declare_is_not_switched(tmp_path, built) -> None:
    _install(tmp_path)
    before = _switches()
    async with TestClient(TestServer(_gateway())) as client:
        resp = await client.post(f"/api/apps/{APP}/capabilities/calendar/on")
        assert resp.status == 404
        assert (await resp.json())["error"] == f"{APP!r} has no capability 'calendar'"
        resp = await client.post("/api/apps/not-installed/capabilities/search/on")
        assert resp.status == 404
    assert _switches() == before


@pytest.mark.asyncio
async def test_a_native_apps_capability_is_not_switched_off(tmp_path, built, monkeypatch) -> None:
    _install(tmp_path)
    monkeypatch.setattr(app_manager, "is_native", lambda name: name == APP)
    async with TestClient(TestServer(_gateway())) as client:
        resp = await client.post(f"/api/apps/{APP}/capabilities/search/off")
        assert resp.status == 409
        error = (await resp.json())["error"]
        assert error["code"] == "app_native" and "native app" in error["message"]
    assert _running()["search"] is True and _switches()["search"] == {"on": True}


def test_the_switch_routes_are_the_owners_alone() -> None:
    from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly

    for verb in ("on", "off"):
        rule = ROUTE_AUTHZ[f"POST /api/apps/{{name}}/capabilities/{{capability}}/{verb}"]
        assert isinstance(rule, OwnerOnly)


# ── failure stays contained, and the row says so ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_capability_that_fails_leaves_the_others_running(tmp_path, built) -> None:
    built.fail["inbox"] = "the token was refused"
    _install(tmp_path)
    assert _running() == {"inbox": False, "search": True, "sync": False}
    async with TestClient(TestServer(_gateway())) as client:
        row = _row(await (await client.get("/api/apps")).json())
    states = {c["id"]: (c["on"], c["status"], c["reason"]) for c in row["capabilities"]}
    assert states == {
        "inbox": (True, "failed", "the token was refused"),
        "search": (True, "running", ""),
        "sync": (False, "off", ""),
    }
    assert row["capabilitySummary"] == (
        "1 of 3 capabilities running · Mail in your Inbox failed: the token was refused"
        " · Keep folders in step is off"
    )


@pytest.mark.asyncio
async def test_a_settings_save_never_builds_a_capability_switched_off(tmp_path, built) -> None:
    from personalclaw.providers.routes import apply_saved_settings

    built.fail["inbox"] = "the token was refused"
    _install(tmp_path)
    async with TestClient(TestServer(_gateway())) as client:
        off = await client.post(f"/api/apps/{APP}/capabilities/inbox/off")
        assert (await off.json())["status"] == "off"
    rec = registry_module.get_provider_registry().get(APP, "inbox")
    assert rec is not None and rec.error == "", "switching it off clears what its build said"
    built.fail.clear()
    rec.error = "a repair's note on a provider that is not built"
    await apply_saved_settings(APP)
    assert _running()["inbox"] is False, "a save retries a failed capability only while it is on"
    assert f"+{APP}/inbox" not in built.log


@pytest.mark.asyncio
async def test_an_app_switched_off_runs_no_capability_and_says_so(tmp_path, built) -> None:
    _install(tmp_path)
    assert app_manager.disable(APP)
    built.log.clear()
    async with TestClient(TestServer(_gateway())) as client:
        on = await client.post(f"/api/apps/{APP}/capabilities/sync/on")
        assert on.status == 200, await on.text()
        body = await on.json()
        assert (body["on"], body["status"]) == (True, "off")
        assert body["reason"] == "Mail Kit is switched off."
        assert built.log == [], "the switch is kept; nothing of a switched-off app is built"
        row = _row(await (await client.get("/api/apps")).json())
        assert row["capabilitySummary"] == "Mail Kit is switched off."
    assert app_manager.enable(APP)
    assert _running() == {"inbox": True, "search": True, "sync": True}


# ── what carries across a restart and an update ──────────────────────────────────────────────


def test_a_restart_builds_only_the_capabilities_switched_on(tmp_path, built, monkeypatch) -> None:
    _install(tmp_path)
    reg = registry_module.ProviderRegistry()
    for type_ in ("inbox", "search", "sync"):
        reg.register_type_handler(type_, built)  # type: ignore[arg-type]
    monkeypatch.setattr(registry_module, "_registry", reg)
    built.log.clear()
    assert app_runtime.start_installed(gateway=False) == [APP]
    assert sorted(built.log) == [f"+{APP}/inbox", f"+{APP}/search"]
    assert _running() == {"inbox": True, "search": True, "sync": False}


def test_an_app_installed_before_the_switches_runs_every_capability(tmp_path, built) -> None:
    _install(tmp_path)
    meta = manager._read_installed(APP)
    assert meta is not None
    meta.capabilities = {}
    manager._write_installed(APP, meta)
    assert "capabilities" not in json.loads(
        (manager.app_dir(APP) / "installed.json").read_text(encoding="utf-8")
    )
    app_runtime.unload(APP, AppManifest.from_json_file(manager.app_dir(APP) / "app.json"))
    built.log.clear()
    app_runtime.start_installed(gateway=False)
    assert _running() == {"inbox": True, "search": True, "sync": True}


def test_an_update_that_adds_a_capability_leaves_it_off(tmp_path, built) -> None:
    _install(tmp_path, [INBOX, SEARCH])
    notes = _provider("search", "notes", "Search your notes")
    res = app_manager.update(_bundle(tmp_path, "1.1.0", [INBOX, SEARCH, notes]), confirm=True)
    assert res.ok, res.error
    assert _switches() == {"inbox": {"on": True}, "search": {"on": True}, "notes": {"on": False}}
    assert _running() == {"inbox": True, "search": True, "notes": False}


def test_an_update_keeps_the_owners_switches_and_carries_a_renamed_one(tmp_path, built) -> None:
    _install(tmp_path)
    assert app_manager.disable(APP)
    meta = manager._read_installed(APP)
    assert meta is not None
    meta.capabilities["search"] = {"on": False}
    meta.capabilities["sync"] = {"on": True}
    manager._write_installed(APP, meta)
    assert app_manager.enable(APP)
    renamed = _provider("search", "mail-search", "Search your mail", renamedFrom=["search"])
    res = app_manager.update(_bundle(tmp_path, "1.1.0", [renamed, SYNC]), confirm=True)
    assert res.ok, res.error
    assert _switches() == {"mail-search": {"on": False}, "sync": {"on": True}}, (
        "the inbox it dropped leaves the record; the rename keeps its switch; startsOn is only "
        "for an install"
    )
    assert _running() == {"mail-search": False, "sync": True}


def test_an_update_of_a_record_from_before_the_switches_keeps_what_ran(tmp_path, built) -> None:
    _install(tmp_path, [INBOX, SEARCH])
    meta = manager._read_installed(APP)
    assert meta is not None
    meta.capabilities = {}
    manager._write_installed(APP, meta)
    notes = _provider("search", "notes", "Search your notes")
    res = app_manager.update(_bundle(tmp_path, "1.1.0", [INBOX, SEARCH, notes]), confirm=True)
    assert res.ok, res.error
    assert _switches() == {"inbox": {"on": True}, "search": {"on": True}, "notes": {"on": False}}
