"""An app's settings routes read their request body through the one reader
(``request_validation.json_object_body``), so a malformed body is refused with the one error
envelope, by name, and nothing is saved. A save with no body is refused too, never read as an
empty form that would clear every setting. Adding an account needs no body: its label is
optional."""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_account_and_capability_settings import APP, _install, _manifest

from personalclaw.config import loader as config_loader
from personalclaw.providers import registry as registry_module
from personalclaw.providers.settings import (
    ProviderSettings,
    load_stored_account,
    load_stored_capability,
)


@pytest.fixture
def home():
    registry_module.reset_provider_registry()
    _install(_manifest())
    ProviderSettings.update_capability(APP, "search", {"max_results": 5})
    ProviderSettings.add_account(APP, "Work", "work")
    ProviderSettings.update_account(APP, {"server": "work.example.invalid"}, "work")
    ProviderSettings.update_capability(APP, "search", {"max_results": 7}, "work")
    yield config_loader.config_dir()
    registry_module.reset_provider_registry()


def _gateway() -> web.Application:
    from personalclaw.dashboard.handlers.apps import register_app_routes
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    gw = web.Application(middlewares=[owner, request_boundary_middleware()])
    register_app_routes(gw)
    return gw


def _stored() -> tuple[dict[str, Any], ...]:
    return (
        load_stored_capability(APP, "search"),
        load_stored_account(APP, "work"),
        load_stored_capability(APP, "search", "work"),
        {"accounts": [a["id"] for a in ProviderSettings.list_accounts(APP)]},
    )


SAVES = [
    ("PUT", f"/api/apps/{APP}/capabilities/search/config"),
    ("PUT", f"/api/apps/{APP}/accounts/work/config"),
    ("PUT", f"/api/apps/{APP}/accounts/work/capabilities/search/config"),
]
ROUTES = [*SAVES, ("POST", f"/api/apps/{APP}/accounts")]
NAMES = ["capability", "account", "account-capability", "account-add"]


async def _refusal(method: str, url: str, **body: Any) -> tuple[int, Any]:
    async with TestClient(TestServer(_gateway())) as client:
        response = await client.request(method, url, **body)
        return response.status, (await response.json())["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url", ROUTES, ids=NAMES)
async def test_a_malformed_body_is_refused_by_name_and_nothing_is_saved(home, method, url):
    before = _stored()
    status, error = await _refusal(
        method, url, data=b'{"max_results": ', headers={"Content-Type": "application/json"}
    )
    assert (status, error["code"]) == (400, "invalid_json")
    assert _stored() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url", ROUTES, ids=NAMES)
async def test_a_body_that_is_no_object_is_refused_by_name(home, method, url):
    before = _stored()
    status, error = await _refusal(method, url, json=["max_results", 9])
    assert (status, error["code"]) == (400, "invalid_body")
    assert _stored() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url", SAVES, ids=NAMES[:3])
async def test_a_save_with_no_body_is_refused_and_clears_nothing(home, method, url):
    before = _stored()
    status, error = await _refusal(method, url, data=b"")
    assert (status, error["code"]) == (400, "invalid_json")
    assert _stored() == before


@pytest.mark.asyncio
async def test_an_account_is_added_with_no_body(home) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        added = await client.post(f"/api/apps/{APP}/accounts")
        assert added.status == 201, await added.text()
        body = await added.json()
    assert body["label"] == ""
    assert body["id"] in [a["id"] for a in ProviderSettings.list_accounts(APP)]
