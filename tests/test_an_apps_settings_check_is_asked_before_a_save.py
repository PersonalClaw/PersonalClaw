"""An app's own check of its settings is asked before a form saves them; a refusal saves nothing.

What an app's settings may hold is not always a schema's to say: whether a remote address may carry
a user name, or whether a token may sit beside an ssh remote, is the app's own knowledge. An app
could only refuse such settings where it used them, after the form had saved them. A provider may
now declare ``settingsCheck`` in its manifest, and every route that saves the app's settings asks
it first (``apps.settings_check``): a sentence back is the route's 400 in the app's words, and
nothing is written.

The check is app code, so it runs off the event loop and is waited for a bounded time. One that
does not answer in time, cannot be imported or raises is a refusal that says the check could not
run, and a check still running is never started over. It runs for an installed app whether or not
the app is enabled, and it is told each secret that is set only as the mask.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

pytestmark = pytest.mark.security

APP = "fixture-checked-app"
SCHEMA = {
    "type": "object",
    "properties": {
        "repo_url": {"type": "string"},
        "token": {"type": "string", "x-meta": {"sensitive": True}},
    },
}
SAVED = "https://git.example/acme/repo.git"
REFUSAL = "Git remote URL: put the user name in User name, not in the address."

#: The app's check: an address with a user name in it is refused, in the app's words.
REFUSING = f"""
SEEN = []


def check_settings(settings):
    SEEN.append(dict(settings))
    return {REFUSAL!r} if "@" in str(settings.get("repo_url", "")) else ""
"""


class _ProviderConfig:
    type = "sync"
    entity = ""
    capabilities: list[str] = []
    multiInstance = False
    settingsSchema = SCHEMA


class _Ext:
    name = APP
    enabled = False  # an installed app that is not on is asked too
    error = ""
    provider_config = _ProviderConfig()


class _Registry:
    def get(self, name):
        return _Ext() if name == APP else None

    def list_for_app(self, name):
        ext = self.get(name)
        return [ext] if ext is not None else []


def _install(home, check_code: str | None, entry: str = "checks:check_settings") -> None:
    bundle = home / "apps" / APP
    (bundle / "data").mkdir(parents=True, exist_ok=True)
    provider = {
        "type": "sync",
        "implementation": "provider:create_provider",
        "settingsSchema": SCHEMA,
    }
    if check_code is not None:
        provider["settingsCheck"] = entry
        (bundle / "checks.py").write_text(check_code, encoding="utf-8")
    (bundle / "app.json").write_text(
        json.dumps(
            {
                "name": APP,
                "version": "1.0.0",
                "displayName": "Checked Fixture",
                "description": "fixture",
                "setup": {"configSchema": SCHEMA},
                "provider": provider,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    from personalclaw import app_code
    from personalclaw.apps import manager, settings_check
    from personalclaw.apps.native_contract import namespaced_module_name

    monkeypatch.setattr(app_code, "_roots", dict(app_code._roots))
    monkeypatch.setattr(app_code, "_loaded", app_code._loaded)
    monkeypatch.setattr(app_code, "_released", set(app_code._released))
    monkeypatch.setattr(settings_check, "_running", {})
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        yield tmp_path
    sys.modules.pop(namespaced_module_name(APP, "checks"), None)


def _seed() -> None:
    from personalclaw.providers.settings import ProviderSettings

    ProviderSettings.save(APP, {"repo_url": SAVED})


def _stored() -> dict:
    from personalclaw.providers.settings import load_stored

    return load_stored(APP)


def _check_module():
    from personalclaw.apps.native_contract import namespaced_module_name

    return sys.modules[namespaced_module_name(APP, "checks")]


@asynccontextmanager
async def _client():
    from personalclaw.dashboard.handlers.apps import api_app_config_get, api_app_config_put
    from personalclaw.providers import routes as provider_routes

    with patch.object(provider_routes, "get_provider_registry", lambda: _Registry()):
        app = web.Application()
        app.router.add_get("/api/apps/{name}/config", api_app_config_get)
        app.router.add_put("/api/apps/{name}/config", api_app_config_put)
        app.router.add_get("/api/providers/{name}/config", provider_routes.handle_get_config)
        app.router.add_patch("/api/providers/{name}/config", provider_routes.handle_patch_config)
        async with TestClient(TestServer(app)) as client:
            yield client


ROUTES = [f"/api/apps/{APP}/config", f"/api/providers/{APP}/config"]


async def _save(c: TestClient, route: str, values: dict):
    body = await (await c.get(route)).json()
    send = c.put if route.startswith("/api/apps/") else c.patch
    return await send(
        route, json={**body["config"], **values}, headers={"If-Match": f'"{body["revision"]}"'}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=["apps-put", "providers-patch"])
async def test_the_apps_check_refuses_a_save_in_its_own_words(home, route) -> None:
    """🔴 Red before: the form saved what the app would refuse only once it used it."""
    _install(home, REFUSING)
    _seed()
    async with _client() as c:
        resp = await _save(c, route, {"repo_url": "https://ada@git.example/acme/repo.git"})
        body = await resp.json()
    assert resp.status == 400, body
    assert body["error"] == REFUSAL
    assert _stored() == {"repo_url": SAVED}, "nothing was written"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=["apps-put", "providers-patch"])
async def test_settings_the_check_passes_save_as_before(home, route) -> None:
    _install(home, REFUSING)
    _seed()
    async with _client() as c:
        resp = await _save(c, route, {"repo_url": "https://git.example/acme/other.git"})
    assert resp.status == 200, await resp.text()
    assert _stored()["repo_url"] == "https://git.example/acme/other.git"


@pytest.mark.asyncio
async def test_an_app_that_declares_no_check_saves_as_before(home) -> None:
    _install(home, None)
    _seed()
    async with _client() as c:
        resp = await _save(c, ROUTES[0], {"repo_url": "https://ada@git.example/acme/repo.git"})
    assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code, entry, raised",
    [
        ("def check_settings(settings):\n    raise RuntimeError('boom')\n", None, "RuntimeError"),
        ("def check_settings(settings):\n    raise SystemExit(3)\n", None, "SystemExit"),
        (REFUSING, "checks:no_such_function", "AttributeError"),
        (REFUSING, "missing_module:check_settings", "ModuleNotFoundError"),
    ],
    ids=["raises", "exits", "no-function", "no-module"],
)
async def test_a_check_that_cannot_run_refuses_the_save(home, code, entry, raised) -> None:
    _install(home, code, entry or "checks:check_settings")
    _seed()
    async with _client() as c:
        resp = await _save(c, ROUTES[0], {"repo_url": "https://git.example/acme/other.git"})
        body = await resp.json()
    assert resp.status == 400, body
    assert body["error"] == (
        f"Checked Fixture's settings check could not run ({raised}), so nothing was saved."
    )
    assert _stored() == {"repo_url": SAVED}


@pytest.mark.asyncio
async def test_a_check_that_hangs_is_refused_within_the_bound_and_never_started_over(
    home, monkeypatch
) -> None:
    from personalclaw.apps import settings_check

    monkeypatch.setattr(settings_check, "CHECK_SECS", 0.5)
    _install(
        home,
        "import threading\n\nGATE = threading.Event()\n\n\n"
        "def check_settings(settings):\n    GATE.wait(30)\n    return ''\n",
    )
    _seed()
    try:
        async with _client() as c:
            started = time.monotonic()
            first = await _save(c, ROUTES[0], {"repo_url": "https://git.example/acme/a.git"})
            took = time.monotonic() - started
            assert first.status == 400
            assert "did not answer within" in (await first.json())["error"]
            assert took < 5, f"the save waited {took:.1f} s for a check bounded at 0.5 s"
            again = await _save(c, ROUTES[0], {"repo_url": "https://git.example/acme/b.git"})
            assert again.status == 400
            assert "still running" in (await again.json())["error"]
            assert _stored() == {"repo_url": SAVED}

            _check_module().GATE.set()
            await asyncio.wait_for(asyncio.wrap_future(settings_check._running[APP]), 5)
            done = await _save(c, ROUTES[0], {"repo_url": "https://git.example/acme/c.git"})
            assert done.status == 200, await done.text()
    finally:
        _check_module().GATE.set()


@pytest.mark.asyncio
async def test_a_save_whose_own_request_is_cancelled_is_cancelled_not_refused(
    home, monkeypatch
) -> None:
    """A client that leaves, or a shutdown, cancels the save: the cancel goes on up, and is never
    answered as the app's check refusing."""
    from personalclaw.apps import settings_check

    monkeypatch.setattr(settings_check, "CHECK_SECS", 5.0)
    _install(
        home,
        "import threading\n\nGATE = threading.Event()\n\n\n"
        "def check_settings(settings):\n    GATE.wait(30)\n    return ''\n",
    )
    _seed()
    try:
        save = asyncio.create_task(settings_check.refusal(APP, {"repo_url": SAVED}, whole=True))
        await asyncio.sleep(0.2)
        save.cancel()
        with pytest.raises(asyncio.CancelledError):
            await save
    finally:
        _check_module().GATE.set()


@pytest.mark.asyncio
async def test_an_account_save_is_checked_as_the_capability_will_read_it(home) -> None:
    """A capability's own setting is read over the account's, so an account save is judged with
    the capability's own value where it has one, as ``ProviderSettings.load`` reads them."""
    from personalclaw.apps import settings_check
    from personalclaw.apps.app_manager import _manifest_of
    from personalclaw.providers.settings import ProviderSettings

    _install(home, REFUSING)
    _seed()
    (cid,) = _manifest_of(APP).capability_ids()
    own = ProviderSettings.capability_path(APP, cid)
    own.parent.mkdir(parents=True, exist_ok=True)

    own.write_text(json.dumps({"repo_url": SAVED}), encoding="utf-8")
    refused_address = {"repo_url": "https://ada@git.example/acme/repo.git"}
    assert await settings_check.refusal(APP, refused_address, whole=True) == ""
    assert await settings_check.refusal(APP, refused_address) == ""

    own.write_text(json.dumps(refused_address), encoding="utf-8")
    assert await settings_check.refusal(APP, {"repo_url": SAVED}, whole=True) == REFUSAL
    assert await settings_check.refusal(APP, {"repo_url": SAVED}) == REFUSAL
    # A save of the capability's own settings is judged with its own over the account's.
    assert await settings_check.refusal(APP, {"repo_url": SAVED}, capability=cid) == ""


@pytest.mark.asyncio
async def test_the_check_is_told_a_secret_is_set_never_what_it_is(home) -> None:
    from personalclaw.apps.secret_fields import SECRET_MASK

    _install(home, REFUSING)
    _seed()
    token = "fixture-access-token-5e4d3c2b1a"
    async with _client() as c:
        resp = await _save(c, ROUTES[1], {"token": token})
    assert resp.status == 200, await resp.text()
    (seen,) = _check_module().SEEN
    assert seen["token"] == SECRET_MASK and seen["repo_url"] == SAVED
    assert token not in json.dumps(_check_module().SEEN)


def test_the_manifest_refuses_a_check_that_names_no_function() -> None:
    from personalclaw.apps.provider_config import ProviderConfig

    provider = ProviderConfig.from_dict(
        {"type": "sync", "implementation": "provider:create", "settingsCheck": "checks"}
    )
    assert any("settingsCheck" in e for e in provider.validate())
    good = ProviderConfig.from_dict(
        {"type": "sync", "implementation": "provider:create", "settingsCheck": "checks:check"}
    )
    assert not any("settingsCheck" in e for e in good.validate())
    assert good.to_dict()["settingsCheck"] == "checks:check"
