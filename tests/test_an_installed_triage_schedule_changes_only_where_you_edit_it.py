"""An installed Morning triage schedule changes only through its own edit route.

``POST /api/proactive/install`` is the Morning triage pack card's install, and the reconcile that
Settings › Inbox and the card's "Reconcile it" run. A ``cron`` in its body used to edit the
schedule whether it was installed or not, so any caller allowed the route could move when the
unattended digest runs, past the checks the automation's own edit route makes (``PUT
/api/triggers/{id}``, held to you, with its stale-write check). And the route was neither
owner-only nor in a security route family, so an app could be granted it.

Now the body's cron is taken only when the install creates the row (the pack card's first
install, where the cron is editable before Install). Against an installed row a different cron is
refused with ``409`` and a sentence pointing at the Triggers page, and the same cron, or none (the
reconcile), keeps the row's own. And the route is yours: an app is refused it at install and at
the request, in the words every owner-only refusal uses.

The owner's requests and the app's go through the real token middleware and the real
``app_permission_middleware`` (the harness of ``test_apps_cannot_change_your_models.py``), in
front of the real handler over a scratch home's trigger store.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from test_apps_cannot_change_your_models import PORT, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.dashboard.handlers.proactive import api_proactive_install
from personalclaw.proactive import digest_state

pytestmark = pytest.mark.security

APP = "probe-triage"
ROUTE = "/api/proactive/install"
YOURS = "30 7 * * 1-5"
MOVED = "0 3 * * *"
OWNER_ONLY = "owner-only capability, not grantable to an app"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr(digest_state, "trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_sessions() -> Iterator[None]:
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    yield
    revoke_all_sessions()


def _bundle(root: Path) -> Path:
    """A fixture app that declares the Morning triage routes, and ships nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Triage",
        "description": "A fixture app that declares the routes Morning triage is set up by.",
        "permissions": {"api": ["/api/proactive"]},
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


@asynccontextmanager
async def _gateway() -> AsyncIterator[_Gateway]:
    """The real token middleware and app permission middleware, in front of the app routes and the
    real install route."""
    from personalclaw.dashboard.server import app_permission_middleware
    from personalclaw.dashboard.token_auth import generate_token, token_auth_middleware

    app = web.Application(
        middlewares=[token_auth_middleware(port=PORT, local_only=False), app_permission_middleware]
    )
    app["state"] = SimpleNamespace(
        push_refresh=MagicMock(), _sessions={}, get_session=lambda _k: None
    )
    register_app_routes(app)
    app.router.add_post(ROUTE, api_proactive_install)
    owner = generate_token("owner", ttl_seconds=600)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        yield _Gateway(client, owner)


def _stored() -> str:
    row = digest_state.find_schedule(digest_state.trigger_store())
    return "" if row is None else str((row.spec or {}).get("expr", ""))


@pytest.mark.asyncio
async def test_an_installed_schedule_is_not_moved_by_the_install(home) -> None:
    async with _gateway() as gw:
        await gw.ok("POST", ROUTE, {"cron": YOURS})
        status, text = await gw.call("POST", ROUTE, {"cron": MOVED})

    assert status == 409, text
    assert "Triggers" in json.loads(text)["error"]["message"], text
    assert _stored() == YOURS, "the schedule runs when you set it"


@pytest.mark.asyncio
async def test_the_installed_schedules_own_cron_is_not_refused(home) -> None:
    async with _gateway() as gw:
        await gw.ok("POST", ROUTE, {"cron": YOURS})
        again = await gw.ok("POST", ROUTE, {"cron": YOURS})

    assert again["created"] is False and _stored() == YOURS


@pytest.mark.asyncio
async def test_the_first_install_takes_the_cards_cron_and_the_reconcile_keeps_it(home) -> None:
    async with _gateway() as gw:
        first = await gw.ok("POST", ROUTE, {"cron": YOURS})
        reconciled = await gw.ok("POST", ROUTE)

    assert first["created"] is True
    assert reconciled["created"] is False and _stored() == YOURS


@pytest.mark.asyncio
async def test_an_app_is_refused_the_install_route(home) -> None:
    async with _gateway() as gw:
        await gw.ok("POST", ROUTE, {"cron": YOURS})
        await gw.install(_bundle(home))
        token = str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])
        as_sdk = await gw.call("POST", ROUTE, {"cron": MOVED}, app_token=token)
        as_backend = await gw.call("POST", ROUTE, {"cron": MOVED}, app_token=token, as_backend=True)

    for status, text in (as_sdk, as_backend):
        assert status == 403, text
        assert OWNER_ONLY in text, text
    assert _stored() == YOURS


def test_an_app_cannot_declare_the_install_route() -> None:
    from personalclaw.apps.permissions import OWNER_ONLY_API_PATHS

    assert ROUTE in OWNER_ONLY_API_PATHS
    words: Any = OWNER_ONLY_API_PATHS[ROUTE]
    assert "Morning triage" in words, words
