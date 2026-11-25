"""A scheduled job of one capability runs only while that capability is switched on (#3843).

An app's job may name the capability it belongs to (``crons[].capability``). It is in the trigger
store only while that capability is switched on, and switching the capability off takes it out at
once, as switching the app off does. A job that names none is the app's own, scheduled while the
app is on. A job naming a capability the app does not declare is refused at install, and install
consent says a job of a capability that starts off is not turned on by the install.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_manager, manager
from personalclaw.apps.app_crons import reconcile_app_crons
from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import registry as registry_module

pytestmark = pytest.mark.security

APP = "mail-kit"
DIGEST = f"app:{APP}:digest"
TIDY = f"app:{APP}:tidy"


class _Recorder:
    def create(self, ext: Any) -> Any:
        return object()

    def register(self, ext: Any, instance: Any) -> None:
        return None

    def deregister(self, ext: Any, instance: Any) -> None:
        return None


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    reg = registry_module.ProviderRegistry()
    for type_ in ("inbox", "search"):
        reg.register_type_handler(type_, _Recorder())  # type: ignore[arg-type]
    monkeypatch.setattr(registry_module, "_registry", reg)
    return tmp_path


def _manifest(crons: list[dict[str, Any]], *, starts_on: bool = True) -> dict[str, Any]:
    search = {"type": "search", "id": "search", "label": "Search your mail"}
    if not starts_on:
        search["startsOn"] = False
    return {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Mail Kit",
        "description": "A demo mail app.",
        "permissions": {"cron": True, "agent": "text"},
        "providers": [
            {"type": "inbox", "id": "inbox", "label": "Mail in your Inbox"},
            search,
        ],
        "crons": crons,
    }


JOBS = [
    {"name": "digest", "every": 3600, "message": "Sum up the new mail.", "capability": "inbox"},
    {"name": "tidy", "every": 86400, "message": "Archive what was read."},
]


def _with_impl(data: dict[str, Any]) -> dict[str, Any]:
    for p in data["providers"]:
        p["implementation"] = "provider:create"
    return data


def _install(root: Path, **kw: Any) -> None:
    d = root / "src" / APP
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(_with_impl(_manifest(JOBS, **kw))), encoding="utf-8")
    (d / "provider.py").write_text("def create(config):\n    return None\n", encoding="utf-8")
    res = app_manager.install(d, confirm=True)
    assert res.ok, res.error


def _scheduled(home: Path) -> set[str]:
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=home)
    reconcile_app_crons(store)
    return {row.trigger.id for row in store.load() if row.trigger.id.startswith("app:")}


def _switch(capability: str, on: bool) -> None:
    from personalclaw.apps import capability_switches

    capability_switches.switch(APP, capability, on)


def test_a_capabilitys_job_is_scheduled_only_while_it_is_on(home) -> None:
    _install(home)
    assert _scheduled(home) == {DIGEST, TIDY}
    _switch("inbox", False)
    assert _scheduled(home) == {TIDY}, "the inbox's job leaves; the app's own job stays"
    _switch("search", False)
    assert _scheduled(home) == {TIDY}
    _switch("inbox", True)
    assert _scheduled(home) == {DIGEST, TIDY}
    assert app_manager.disable(APP)
    assert _scheduled(home) == set(), "an app switched off schedules nothing"


def test_a_job_of_a_capability_whose_switch_cannot_be_read_is_not_scheduled(home) -> None:
    _install(home)
    path = manager.app_dir(APP) / "installed.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["capabilities"] = ["inbox"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert _scheduled(home) == {TIDY}


@pytest.mark.asyncio
async def test_switching_a_capability_off_takes_its_job_out_at_once(home) -> None:
    from personalclaw.dashboard.handlers.apps import register_app_routes
    from personalclaw.triggers.store import TriggerStore

    _install(home)
    store = TriggerStore(base_dir=home)
    reconcile_app_crons(store)

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    gw = web.Application(middlewares=[owner])
    register_app_routes(gw)
    async with TestClient(TestServer(gw)) as client:
        off = await client.post(f"/api/apps/{APP}/capabilities/inbox/off")
        assert off.status == 200, await off.text()
        assert {r.trigger.id for r in store.load()} == {TIDY}
        on = await client.post(f"/api/apps/{APP}/capabilities/inbox/on")
        assert on.status == 200, await on.text()
        assert {r.trigger.id for r in store.load()} == {DIGEST, TIDY}


def test_a_job_naming_a_capability_the_app_does_not_declare_is_refused() -> None:
    crons = [{"name": "digest", "every": 3600, "message": "Sum up.", "capability": "calendar"}]
    errors = AppManifest.from_dict(_with_impl(_manifest(crons))).validate()
    assert any("belongs to the capability 'calendar'" in e for e in errors), errors
    ok = AppManifest.from_dict(_with_impl(_manifest(JOBS))).validate()
    assert ok == []


def test_the_capability_a_job_belongs_to_round_trips() -> None:
    m = AppManifest.from_dict(_with_impl(_manifest(JOBS)))
    assert [c.capability for c in m.crons] == ["inbox", ""]
    again = AppManifest.from_dict(m.to_dict())
    assert [c.capability for c in again.crons] == ["inbox", ""]
    assert "capability" not in m.to_dict()["crons"][1]


def test_consent_says_a_job_of_a_capability_that_starts_off_is_not_turned_on() -> None:
    from personalclaw.apps.disclosure import describe

    crons = [{**JOBS[0], "capability": "search"}, JOBS[1]]
    off = describe(AppManifest.from_dict(_with_impl(_manifest(crons, starts_on=False))))
    assert {c["name"]: c["scheduled"] for c in off["crons"]} == {"digest": False, "tidy": True}
    on = describe(AppManifest.from_dict(_with_impl(_manifest(crons))))
    assert {c["name"]: c["scheduled"] for c in on["crons"]} == {"digest": True, "tidy": True}
