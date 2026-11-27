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
        "sync": {"on": False},
    }, "a mark that cannot be read is left without a switch"
    from personalclaw.apps.capability_switches import is_on, readable

    assert [is_on(meta, c) for c in ("inbox", "search", "sync", "notes")] == [
        True,
        False,
        False,
        False,
    ]
    assert [readable(meta, c) for c in ("inbox", "search", "sync", "notes")] == [
        True,
        False,
        True,
        False,
    ]
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
    meta.capabilities = None
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
    meta.capabilities = None
    manager._write_installed(APP, meta)
    notes = _provider("search", "notes", "Search your notes")
    res = app_manager.update(_bundle(tmp_path, "1.1.0", [INBOX, SEARCH, notes]), confirm=True)
    assert res.ok, res.error
    assert _switches() == {"inbox": {"on": True}, "search": {"on": True}, "notes": {"on": False}}


# ── switches that cannot be read, and two writers at once ────────────────────────────────────


def _rewrite_record(**fields: Any) -> None:
    path = manager.app_dir(APP) / "installed.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    "unreadable",
    [[], ["inbox"], "on", {}, {"inbox": "yes", "search": 3, "sync": None}],
    ids=["a list", "a list of ids", "a word", "an empty map", "every mark junk"],
)
def test_switches_that_cannot_be_read_run_nothing(tmp_path, built, monkeypatch, unreadable) -> None:
    _install(tmp_path)
    _rewrite_record(capabilities=unreadable)
    meta = manager._read_installed(APP)
    assert meta is not None and meta.capabilities is not None
    from personalclaw.apps.capability_switches import is_on

    assert [is_on(meta, cid) for cid in ("inbox", "search", "sync")] == [False, False, False]
    reg = registry_module.ProviderRegistry()
    for type_ in ("inbox", "search", "sync"):
        reg.register_type_handler(type_, built)  # type: ignore[arg-type]
    monkeypatch.setattr(registry_module, "_registry", reg)
    built.log.clear()
    app_runtime.start_installed(gateway=False)
    assert built.log == [], "a capability whose switch cannot be read is never built"
    assert (
        meta.to_dict()["capabilities"] is not None
    ), "written back, it stays a record with switches"


def test_only_a_record_with_no_switches_runs_every_capability(tmp_path, built) -> None:
    _install(tmp_path)
    path = manager.app_dir(APP) / "installed.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["capabilities"]
    path.write_text(json.dumps(data), encoding="utf-8")
    meta = manager._read_installed(APP)
    assert meta is not None and meta.capabilities is None
    _rewrite_record(capabilities={"inbox": {"on": True}, "search": {"on": "junk"}})
    meta = manager._read_installed(APP)
    from personalclaw.apps.capability_switches import is_on

    assert [is_on(meta, cid) for cid in ("inbox", "search", "sync")] == [True, False, False]


def test_an_empty_map_of_switches_is_kept_when_the_record_is_written() -> None:
    meta = manager.InstalledApp(name=APP, capabilities={})
    assert meta.to_dict()["capabilities"] == {}
    assert manager.InstalledApp.from_dict(meta.to_dict()).capabilities == {}
    assert "capabilities" not in manager.InstalledApp(name=APP).to_dict()


def test_a_switch_on_waits_for_a_disable_and_then_builds_nothing(
    tmp_path, built, monkeypatch
) -> None:
    import threading

    from personalclaw.apps import capability_switches

    _install(tmp_path)
    in_unload, go = threading.Event(), threading.Event()
    real_unload = app_runtime.unload

    def slow_unload(*args: Any, **kwargs: Any) -> list[str]:
        in_unload.set()
        assert go.wait(10), "the test never let the disable go on"
        return real_unload(*args, **kwargs)

    monkeypatch.setattr(app_runtime, "unload", slow_unload)
    disabled: list[bool] = []
    switched: list[Any] = []
    a = threading.Thread(target=lambda: disabled.append(app_manager.disable(APP)))
    a.start()
    assert in_unload.wait(10)
    built.log.clear()
    b = threading.Thread(
        target=lambda: switched.append(capability_switches.switch(APP, "sync", True))
    )
    b.start()
    b.join(0.5)
    assert b.is_alive(), "the switch waits for the disable that holds the record"
    go.set()
    a.join(10)
    b.join(10)
    assert disabled == [True]
    (status,) = switched
    assert (status.on, status.state) == (True, "off"), "the switch is kept, and the app is off"
    meta = manager._read_installed(APP)
    assert meta is not None and meta.enabled is False
    assert meta.capabilities is not None and meta.capabilities["sync"] == {"on": True}
    assert f"+{APP}/sync" not in built.log, "nothing of an app switched off is built"
    assert _running()["sync"] is False


UNREAD = (
    "Mail Kit's capability switches couldn't be read, so they stay off until you switch them on."
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unreadable",
    [[], {}, {"inbox": "yes", "search": 3, "sync": None}],
    ids=["a list", "an empty map", "every mark junk"],
)
async def test_switches_that_cannot_be_read_say_so_once(tmp_path, built, unreadable) -> None:
    _install(tmp_path)
    _rewrite_record(capabilities=unreadable)
    assert app_manager.enable(APP)
    async with TestClient(TestServer(_gateway())) as client:
        row = _row(await (await client.get("/api/apps")).json())
    states = {c["id"]: (c["on"], c["status"], c["reason"]) for c in row["capabilities"]}
    assert states == {cid: (False, "off", UNREAD) for cid in ("inbox", "search", "sync")}
    assert row["capabilitySummary"] == (
        "0 of 3 capabilities running · Mail in your Inbox, Search your mail and Keep folders in "
        "step are off: " + UNREAD
    )
    assert not any("_readable" in c for c in row["capabilities"])


@pytest.mark.asyncio
async def test_one_mark_that_cannot_be_read_leaves_the_others_as_written(tmp_path, built) -> None:
    _install(tmp_path)
    _rewrite_record(
        capabilities={"inbox": {"on": True}, "search": {"on": "maybe"}, "sync": {"on": False}}
    )
    assert app_manager.enable(APP)
    assert _running() == {"inbox": True, "search": False, "sync": False}
    async with TestClient(TestServer(_gateway())) as client:
        row = _row(await (await client.get("/api/apps")).json())
        states = {c["id"]: (c["status"], c["reason"]) for c in row["capabilities"]}
        assert states == {"inbox": ("running", ""), "search": ("off", UNREAD), "sync": ("off", "")}
        on = await client.post(f"/api/apps/{APP}/capabilities/sync/on")
        assert (await on.json())["status"] == "running"
    assert _switches() == {
        "inbox": {"on": True},
        "sync": {"on": True},
    }, "a switch writes the switches it can read, and the one asked for; search stays unread"
    from personalclaw.apps.capability_switches import status_of

    assert status_of(APP, "search").reason == UNREAD


def test_an_update_keeps_a_switch_it_cannot_read_off(tmp_path, built) -> None:
    _install(tmp_path, [INBOX, SEARCH])
    _rewrite_record(capabilities={"inbox": {"on": True}, "search": ["junk"]})
    notes = _provider("search", "notes", "Search your notes")
    res = app_manager.update(_bundle(tmp_path, "1.1.0", [INBOX, SEARCH, notes]), confirm=True)
    assert res.ok, res.error
    assert _switches() == {"inbox": {"on": True}, "notes": {"on": False}}
    assert _running() == {"inbox": True, "search": False, "notes": False}


def test_the_version_sync_keeps_a_switch_written_while_it_read(
    tmp_path, built, monkeypatch
) -> None:
    _install(tmp_path)
    meta = manager._read_installed(APP)
    assert meta is not None
    meta.lifecycle = "app"
    manager._write_installed(APP, meta)
    app_json = manager.app_dir(APP) / "app.json"
    declared = json.loads(app_json.read_text(encoding="utf-8"))
    declared["version"] = "1.0.1"  # an app that manages itself moved its own version
    app_json.write_text(json.dumps(declared), encoding="utf-8")
    real = manager.record_lock

    class SwitchLandsFirst:
        """The record lock, taken just after a switch of search's wrote the record."""

        def __init__(self, name: str) -> None:
            self.name, self.lock = name, real(name)

        def __enter__(self) -> "SwitchLandsFirst":
            self.lock.acquire()
            fresh = manager._read_installed(self.name)
            assert fresh is not None and fresh.capabilities is not None
            fresh.capabilities["search"] = {"on": False}
            manager._write_installed(self.name, fresh)
            return self

        def __exit__(self, *exc: Any) -> None:
            self.lock.release()

    monkeypatch.setattr(manager, "record_lock", SwitchLandsFirst)
    manager.list_apps()
    meta = manager._read_installed(APP)
    assert meta is not None and meta.version == "1.0.1"
    assert meta.capabilities is not None and meta.capabilities["search"] == {
        "on": False
    }, "the sync writes back the record it read under the lock, not the one it listed"
