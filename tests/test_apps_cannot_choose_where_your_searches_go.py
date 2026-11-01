"""An installed app cannot choose where your searches go, or spend a search provider's key.

``PUT /api/search/active/{use_case}`` binds a search use (general, news, research…) to one
installed provider, and from then on every search your chats and runs make for that use goes to
it, with what was asked. ``POST /api/search/providers/{name}/test`` runs a real search through a
provider, on its key. Both are yours: an app whose grant covers ``/api/search`` is refused both,
in the words every owner-only refusal uses, and keeps the reads its grant gives it (the providers
and the current binding). Your own requests still bind and test.

Same harness as ``test_apps_cannot_change_your_models.py``: the real token middleware and the real
``app_permission_middleware``, a fixture app you installed through preview, consent and install,
and the token you minted for it, sent both ways an app sends one. The search routes are the real
handlers, over a private provider registry holding one provider that counts its searches.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_apps_cannot_change_your_models import _gateway, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw.apps import manager
from personalclaw.dashboard.handlers import search_registry
from personalclaw.search_providers import registry as search_reg
from personalclaw.search_providers import use_cases as search_uses
from personalclaw.search_providers.base import SearchCapabilities, SearchProvider, SearchResult

pytestmark = pytest.mark.security

APP = "probe-search"
FAMILY = "/api/search"
#: A provider the app ships, and the one your searches use.
THEIRS = "their-search"
YOURS = "your-search"
USE = "search-general"
OWNER_ONLY = "owner-only capability, not grantable to an app"


class _Provider(SearchProvider):
    """A search provider that records every search it runs: each one spends its key."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.searched: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return True

    def capabilities(self) -> SearchCapabilities:
        return SearchCapabilities()

    async def search(self, query: str, **_kwargs: Any) -> SearchResult:
        self.searched.append(query)
        return SearchResult(provider=self._name, query=query)


def _bundle(root: Path) -> Path:
    """A fixture app that declares the search routes, and ships nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Search",
        "description": "A fixture app that declares the routes your search backend is set up by.",
        "permissions": {"api": [FAMILY]},
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_sessions() -> Iterator[None]:
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    yield
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


@pytest.fixture
def providers(monkeypatch, home) -> dict[str, _Provider]:
    """A private provider registry holding yours and the app's, and your search bound to yours."""
    found = {name: _Provider(name) for name in (YOURS, THEIRS)}
    monkeypatch.setattr(search_reg, "_providers", dict(found))
    monkeypatch.setattr(search_uses, "_active_path", lambda: home / "active_search_providers.json")
    search_uses.set_active_search_provider(USE, YOURS)
    return found


def _routes() -> list[tuple[str, str, Any]]:
    """The search routes, at the templates the gateway registers them under."""
    return [
        ("GET", "/api/search/providers", search_registry.api_search_providers),
        ("POST", "/api/search/providers/{name}/test", search_registry.api_search_provider_test),
        ("GET", "/api/search/active", search_registry.api_search_active),
        ("PUT", "/api/search/active/{use_case}", search_registry.api_search_active_set),
    ]


async def _installed(gw: _Gateway, home: Path) -> str:
    """Your install of the fixture app, and the token you mint for it."""
    await gw.install(_bundle(home))
    return str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])


def _denials(rows: MagicMock, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("source") == "app_permissions"
        and c.kwargs.get("resources") == path
    ]


def _bound() -> list[str]:
    return search_uses.load_active_search_providers().get(USE, [])


@pytest.mark.asyncio
async def test_an_app_cannot_bind_your_searches(home, sel_rows, providers) -> None:
    path = f"/api/search/active/{USE}"
    async with _gateway(_routes()) as gw:
        token = await _installed(gw, home)
        as_sdk = await gw.call("PUT", path, {"providers": [THEIRS]}, app_token=token)
        as_backend = await gw.call(
            "PUT", path, {"providers": [THEIRS]}, app_token=token, as_backend=True
        )
        after_the_app = _bound()
        yours = await gw.ok("PUT", path, {"providers": [THEIRS]})

    for status, text in (as_sdk, as_backend):
        assert status == 403, text
        assert OWNER_ONLY in text, text
    assert after_the_app == [YOURS], "your searches still go where you sent them"
    assert len(_denials(sel_rows, path)) == 2, "each refusal leaves an SEL row naming the app"
    assert yours["providers"] == [THEIRS] and _bound() == [THEIRS], "you still bind"


@pytest.mark.asyncio
async def test_an_app_cannot_spend_a_providers_key_on_a_test(home, sel_rows, providers) -> None:
    path = f"/api/search/providers/{YOURS}/test"
    async with _gateway(_routes()) as gw:
        token = await _installed(gw, home)
        as_sdk = await gw.call("POST", path, app_token=token)
        as_backend = await gw.call("POST", path, app_token=token, as_backend=True)
        searched_for_the_app = list(providers[YOURS].searched)
        yours = await gw.ok("POST", path)

    for status, text in (as_sdk, as_backend):
        assert status == 403, text
        assert OWNER_ONLY in text, text
    assert searched_for_the_app == [], "no search ran on your provider's key for the app"
    assert len(_denials(sel_rows, path)) == 2
    assert yours["provider"]["name"] == YOURS and len(providers[YOURS].searched) == 1


@pytest.mark.asyncio
async def test_an_app_keeps_the_reads_its_grant_gives_it(home, sel_rows, providers) -> None:
    async with _gateway(_routes()) as gw:
        token = await _installed(gw, home)
        listed = await gw.call("GET", "/api/search/providers", app_token=token)
        bound = await gw.call("GET", "/api/search/active", app_token=token)

    assert listed[0] == 200, listed
    assert {p["name"] for p in json.loads(listed[1])["providers"]} == {YOURS, THEIRS}
    assert bound[0] == 200 and json.loads(bound[1])["use_cases"][USE] == [YOURS], bound
    assert not [c for c in sel_rows.call_args_list if c.kwargs.get("outcome") == "denied"]
    assert all(not p.searched for p in providers.values()), "a read spends no search"


def test_every_write_under_the_search_routes_is_yours() -> None:
    """The route table declares every write in the family owner-only, and the family fails closed
    on a write added tomorrow."""
    from route_census import routes

    from personalclaw.apps.permissions import (
        ROUTE_AUTHZ,
        SECURITY_ROUTE_FAMILIES,
        OwnerOnly,
        undeclared_security_route,
    )

    census = {
        (r["method"], r["path"])
        for r in routes()
        if r["path"] == FAMILY or r["path"].startswith(FAMILY + "/")
    }
    assert census == {(m, t) for m, t, _h in _routes()}, census
    assert FAMILY in SECURITY_ROUTE_FAMILIES
    writes = sorted(f"{m} {t}" for m, t in census if m not in ("GET", "HEAD"))
    assert len(writes) == 2, writes
    for key in writes:
        assert isinstance(ROUTE_AUTHZ.get(key), OwnerOnly), key
    assert undeclared_security_route("POST", f"{FAMILY}/{{id}}/a-route-added-tomorrow")
