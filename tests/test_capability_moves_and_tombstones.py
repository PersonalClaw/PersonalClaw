"""What an installed app's capabilities become when it is held off, renamed or retired (#3841).

* **Held off.** An app installed by an older core may declare two providers of one type with no
  ids. This core cannot tell those capabilities apart, so it registers none: the app stays off,
  saying why, its files untouched, until an update names each one.
* **Renamed.** An update that renames a capability declares the old id in ``renamedFrom``; what
  is kept under ``<app>/<old>`` moves to ``<app>/<new>`` once, and the installed record keeps the
  move, so running it again moves nothing. A move a store's mover failed stays pending, naming
  that mover, and the gateway's next start runs only that mover again.
* **Retired.** An id an update drops is a tombstone in the installed record, with its last label,
  the version that dropped it and whether the author declared it retired: a read of it says what
  it was and when it went, a declared one may never be declared again, and one dropped without
  saying so may come back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_manager, capability_lifecycle, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import registry as registry_module

APP = "mail-demo"

_PROVIDER_PY = "def create(config):\n    return None\n"


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    registry_module.reset_provider_registry()
    yield tmp_path
    registry_module.reset_provider_registry()


def _manifest(version: str, **fields: Any) -> dict[str, Any]:
    return {
        "name": APP,
        "version": version,
        "displayName": "Mail Demo",
        "description": "A demo mail app.",
    } | fields


def _bundle(root: Path, version: str, **fields: Any) -> Path:
    d = root / f"v{version}" / APP
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(_manifest(version, **fields)), encoding="utf-8")
    (d / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    return d


def _inbox(**extra: Any) -> dict[str, Any]:
    return {"type": "inbox", "implementation": "provider:create", **extra}


def _search(**extra: Any) -> dict[str, Any]:
    return {"type": "search", "implementation": "provider:create", **extra}


V1 = {"provider": _inbox(), "providers": [_search(id="news", label="Search the news")]}
V11 = {
    "providers": [
        _inbox(id="mail-inbox", label="Read your mail", renamedFrom=["inbox"]),
    ],
    "retiredCapabilities": ["news"],
}


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


# ── renamed ──────────────────────────────────────────────────────────────────────────────────


def test_an_update_moves_a_renamed_capability_once(tmp_path, movers) -> None:
    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
    res = app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True)
    assert res.ok, res.error
    assert movers == [(APP, "inbox", "mail-inbox")]
    meta = manager._read_installed(APP)
    assert meta.capabilityMoves == {"inbox": {"to": "mail-inbox", "in": "1.1.0"}}
    keys = [r.key for r in registry_module.get_provider_registry().list_for_app(APP)]
    assert keys == [f"{APP}/mail-inbox"], "the record is kept under the new id"

    again = app_manager.update(_bundle(tmp_path, "1.2.0", **V11), confirm=True)
    assert again.ok, again.error
    assert movers == [(APP, "inbox", "mail-inbox")], "a second update moves nothing again"
    assert manager._read_installed(APP).capabilityMoves == meta.capabilityMoves


def test_a_move_already_recorded_is_not_run_again(movers) -> None:
    old = AppManifest.from_dict(_manifest("1.0.0", **V1))
    new = AppManifest.from_dict(_manifest("1.1.0", **V11))
    meta = manager.InstalledApp(name=APP, version="1.0.0")
    capability_lifecycle.after_update(meta, old, new)
    capability_lifecycle.after_update(meta, old, new)
    assert movers == [(APP, "inbox", "mail-inbox")]


def test_a_move_a_mover_failed_stays_pending_and_is_not_done(tmp_path, movers) -> None:
    def breaks(app: str, old: str, new: str) -> None:
        raise OSError("the store is locked")

    capability_lifecycle.register_mover(breaks)
    try:
        assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
        res = app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True)
    finally:
        capability_lifecycle.unregister_mover(breaks)
    assert res.ok, "one store's failure does not undo the update"
    assert movers == [(APP, "inbox", "mail-inbox")], "the other store moved"
    move = manager._read_installed(APP).capabilityMoves["inbox"]
    assert move == {
        "to": "mail-inbox",
        "in": "1.1.0",
        "pending": [capability_lifecycle.mover_name(breaks)],
    }


def test_the_gateway_start_runs_only_a_failed_mover_again_and_then_the_move_is_done(
    tmp_path, movers, monkeypatch
) -> None:
    from personalclaw.apps import app_runtime

    tries: list[tuple[str, str, str]] = []

    def flaky(app: str, old: str, new: str) -> None:
        tries.append((app, old, new))
        if len(tries) == 1:
            raise OSError("the store is locked")

    capability_lifecycle.register_mover(flaky)
    monkeypatch.setattr(app_runtime, "load", lambda *manifests: None)
    try:
        assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
        assert app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True).ok
        assert "pending" in manager._read_installed(APP).capabilityMoves["inbox"]
        app_runtime.start_installed(gateway=False)
        assert len(tries) == 1, "a process that is not the gateway retries no move"
        app_runtime.start_installed()
        assert tries == [(APP, "inbox", "mail-inbox")] * 2
        assert movers == [(APP, "inbox", "mail-inbox")], "a mover that succeeded is not run again"
        done = {"inbox": {"to": "mail-inbox", "in": "1.1.0"}}
        assert manager._read_installed(APP).capabilityMoves == done
        app_runtime.start_installed()
        assert len(tries) == 2, "a done move is not run again"
    finally:
        capability_lifecycle.unregister_mover(flaky)


def test_a_pending_mover_that_is_not_registered_stays_pending(movers) -> None:
    meta = manager.InstalledApp(name=APP, version="1.1.0")
    meta.capabilityMoves = {"inbox": {"to": "mail-inbox", "in": "1.1.0", "pending": ["gone.mover"]}}
    assert capability_lifecycle.retry_pending_moves(meta) is False
    assert meta.capabilityMoves["inbox"]["pending"] == ["gone.mover"] and movers == []


# ── retired ──────────────────────────────────────────────────────────────────────────────────


def test_an_update_keeps_a_tombstone_for_each_id_it_drops(tmp_path, movers) -> None:
    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
    assert app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True).ok
    tombs = manager._read_installed(APP).retiredCapabilities
    assert tombs == {"news": {"label": "Search the news", "removedIn": "1.1.0", "declared": True}}
    assert "inbox" not in tombs, "a renamed id is moved, not retired"
    assert capability_lifecycle.retired_sentence(APP, "news") == (
        "Mail Demo removed its “Search the news” capability (news) in 1.1.0."
    )
    assert capability_lifecycle.retired_sentence(APP, "mail-inbox") == ""


def test_an_id_dropped_without_saying_so_is_still_a_tombstone(tmp_path) -> None:
    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
    quiet = {"providers": [_inbox(id="mail-inbox", label="Read your mail", renamedFrom=["inbox"])]}
    assert app_manager.update(_bundle(tmp_path, "1.1.0", **quiet), confirm=True).ok
    assert manager._read_installed(APP).retiredCapabilities == {
        "news": {"label": "Search the news", "removedIn": "1.1.0", "declared": False}
    }


def test_an_update_that_declares_a_retired_id_again_is_refused(tmp_path) -> None:
    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
    assert app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True).ok
    back = {
        "providers": [
            _inbox(id="mail-inbox", label="Read your mail"),
            _search(id="news", label="Search the headlines"),
        ]
    }
    res = app_manager.update(_bundle(tmp_path, "1.2.0", **back), confirm=True)
    assert not res.ok
    assert "news" in res.error and "removed in 1.1.0" in res.error, res.error
    meta = manager._read_installed(APP)
    assert meta.version == "1.1.0", "the installed version still runs"
    assert meta.retiredCapabilities["news"]["declared"] is True


_QUIET = {"providers": [_inbox(id="mail-inbox", label="Read your mail", renamedFrom=["inbox"])]}
_BACK = {
    "providers": [
        _inbox(id="mail-inbox", label="Read your mail"),
        _search(id="news", label="Search the headlines"),
    ]
}


def test_a_capability_dropped_without_retiring_it_may_come_back(tmp_path, caplog) -> None:
    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
    assert app_manager.update(_bundle(tmp_path, "1.1.0", **_QUIET), confirm=True).ok
    with caplog.at_level("INFO", logger="personalclaw.apps.capability_lifecycle"):
        res = app_manager.update(_bundle(tmp_path, "1.2.0", **_BACK), confirm=True)
    assert res.ok, res.error
    meta = manager._read_installed(APP)
    assert (meta.version, meta.retiredCapabilities) == ("1.2.0", {}), "its tombstone is cleared"
    assert (
        "capability news, dropped in 1.1.0 without being retired, is back in 1.2.0" in caplog.text
    )
    assert capability_lifecycle.retired_sentence(APP, "news") == ""


def test_a_silent_drop_retired_by_a_later_version_is_never_declared_again(tmp_path) -> None:
    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
    assert app_manager.update(_bundle(tmp_path, "1.1.0", **_QUIET), confirm=True).ok
    retired = {**_QUIET, "retiredCapabilities": ["news"]}
    assert app_manager.update(_bundle(tmp_path, "1.2.0", **retired), confirm=True).ok
    tomb = manager._read_installed(APP).retiredCapabilities["news"]
    assert (tomb["removedIn"], tomb["declared"]) == ("1.1.0", True)
    res = app_manager.update(_bundle(tmp_path, "1.3.0", **_BACK), confirm=True)
    assert not res.ok and "removed in 1.1.0" in res.error, res.error


@pytest.mark.asyncio
async def test_a_read_of_a_retired_capability_says_what_it_was(tmp_path) -> None:
    from personalclaw.providers.routes import register_routes

    assert app_manager.install(_bundle(tmp_path, "1.0.0", **V1), confirm=True).ok
    assert app_manager.update(_bundle(tmp_path, "1.1.0", **V11), confirm=True).ok

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[owner])
    register_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/providers/{APP}/capabilities/news")
        assert resp.status == 404
        assert (await resp.json())["error"] == (
            "Mail Demo removed its “Search the news” capability (news) in 1.1.0."
        )


# ── held off ─────────────────────────────────────────────────────────────────────────────────


def _installed_by_an_older_core(tmp_path: Path) -> Path:
    """Two channel providers with no ids, on disk as an older core left them, switched on."""
    live = manager.app_dir(APP)
    live.mkdir(parents=True)
    raw = _manifest(
        "1.0.0",
        providers=[
            {"type": "channel", "implementation": "provider:create"},
            {"type": "channel", "implementation": "provider:other"},
        ],
    )
    (live / "app.json").write_text(json.dumps(raw), encoding="utf-8")
    (live / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    manager._write_installed(
        APP, manager.InstalledApp(name=APP, version="1.0.0", displayName="Mail Demo", enabled=True)
    )
    return live


def test_an_app_whose_capabilities_cannot_be_told_apart_is_held_off(tmp_path) -> None:
    from personalclaw.apps import app_runtime

    live = _installed_by_an_older_core(tmp_path)
    before = {p.name: p.read_bytes() for p in live.iterdir() if p.is_file()}
    started = app_runtime.start_installed(gateway=False)
    assert APP not in started
    refused = app_manager.enable_refusal(APP)
    assert "providers[0] has no id" in refused and "give each an id" in refused, refused
    assert "stays off until an update" in refused, refused
    assert app_manager.enable(APP) is False
    assert registry_module.get_provider_registry().list_for_app(APP) == []
    assert {
        p.name: p.read_bytes() for p in live.iterdir() if p.is_file()
    } == before, "a held-off app's files are never rewritten"
