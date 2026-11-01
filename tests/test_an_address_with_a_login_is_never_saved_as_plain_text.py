"""A login written into an address is never saved in a setting kept as plain text.

Only the settings an app declares sensitive, or names like a credential, go to the credential
store. Every other setting is written to the app's settings file as it was typed, handed back by
the settings routes as it is, and carried in every export. So a remote address typed with its
password or token in it (``https://ada:<password>@host/…``, ``https://<token>@host/…``,
``ada:<password>@host:path``) was saved in plain text, and every form that opened the app's
settings showed it.

The store every app settings writer goes through (``secret_refs.store``) now refuses such a value
in a plain-text setting before anything is written, naming the setting and what to do instead:
the Apps form, Settings → Providers, a capability's own settings, a provider instance, and an app
saving its own. A user name alone (``ssh://git@host``, ``https://ada@host``) is no credential and
saves as before, and so does a value already on disk that a save leaves as it was.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

pytestmark = pytest.mark.security

APP = "fixture-remote-app"
# A fixture credential, not a real one: the password in the fixture logins. It is named LOGIN_WORD,
# not PASSWORD, so the clear-text storage query doesn't flag the tests that write it to disk on
# purpose, as a value saved before this check existed.
LOGIN_WORD = "fixture-pass-7c1d9e"
# Built from parts so no scanner takes the fixture for a real token.
TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
# A token in no format the redactor knows: told only by its shape, 20 token characters mixing
# letters and digits. Built from parts for the same reason.
UNKNOWN_TOKEN = "q7Rk2" + "Zp9Xw" + "4Lm8N" + "v3Tb6"
SCHEMA = {
    "type": "object",
    "properties": {
        "repo_url": {"type": "string"},
        "branch": {"type": "string"},
        "token": {"type": "string", "x-meta": {"sensitive": True}},
    },
}

REFUSED = [
    f"https://ada:{LOGIN_WORD}@git.example/acme/repo.git",
    f"https://{TOKEN}@github.com/acme/repo.git",
    f"ssh://deploy:{LOGIN_WORD}@git.example:22/acme/repo.git",
    f"ada:{LOGIN_WORD}@git.example:acme/repo.git",
    f"https://{UNKNOWN_TOKEN}@git.example/acme/repo.git",
]
KEPT = [
    "https://git.example/acme/repo.git",
    "https://ada@bitbucket.org/acme/repo.git",
    "https://x-access-token@github.com/acme/repo.git",
    "ssh://git@github.com/acme/repo.git",
    "git@github.com:acme/repo.git",
    "main",
]


class _ProviderConfig:
    type = "sync"
    entity = ""
    capabilities: list[str] = []
    multiInstance = False
    settingsSchema = SCHEMA


class _Ext:
    name = APP
    enabled = False  # keeps the provider rebuild out of the write path
    error = ""
    provider_config = _ProviderConfig()


class _Registry:
    def get(self, name):
        return _Ext() if name == APP else None

    def list_for_app(self, name):
        ext = self.get(name)
        return [ext] if ext is not None else []


@pytest.fixture
def home(tmp_path):
    from personalclaw.apps import manager

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        bundle = tmp_path / "apps" / APP
        (bundle / "data").mkdir(parents=True)
        (bundle / "app.json").write_text(
            json.dumps(
                {
                    "name": APP,
                    "version": "1.0.0",
                    "displayName": "Remote Fixture",
                    "description": "fixture",
                    "setup": {"configSchema": SCHEMA},
                }
            ),
            encoding="utf-8",
        )
        from personalclaw.providers.settings import ProviderSettings

        ProviderSettings.save(APP, {"repo_url": "https://git.example/acme/repo.git"})
        yield tmp_path


def _stored() -> dict:
    from personalclaw.providers.settings import load_stored

    return load_stored(APP)


def _anywhere(home, needle: str) -> list[str]:
    return [
        str(p.relative_to(home))
        for p in sorted(home.rglob("*"))
        if p.is_file() and not p.is_symlink() and needle.encode() in p.read_bytes()
    ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=["apps-put", "providers-patch"])
async def test_a_form_saving_an_address_with_a_password_is_refused_and_nothing_is_written(
    home, route
) -> None:
    """🔴 Red before: the address was saved in plain text and handed back to the form."""
    async with _client() as c:
        body = await (await c.get(route)).json()
        send = c.put if route.startswith("/api/apps/") else c.patch
        resp = await send(
            route,
            json={**body["config"], "repo_url": REFUSED[0]},
            headers={"If-Match": f'"{body["revision"]}"'},
        )
        text = await resp.text()
    assert resp.status in (400, 422), text
    assert "repo_url" in text and "plain text" in text, "the refusal names the setting and why"
    assert LOGIN_WORD not in text, "the refusal never echoes the credential"
    assert _stored()["repo_url"] == "https://git.example/acme/repo.git"
    assert _anywhere(home, LOGIN_WORD) == []


@pytest.mark.parametrize(
    "value", REFUSED, ids=["password", "token", "ssh-password", "scp", "unknown-token"]
)
def test_every_writer_refuses_a_login_in_a_plain_text_setting(home, value) -> None:
    from personalclaw.apps import app_config
    from personalclaw.providers import instances
    from personalclaw.providers.settings import ProviderSettings

    writers = {
        "the Apps form": lambda: app_config.write_config(APP, {"repo_url": value}, SCHEMA),
        "a whole save": lambda: ProviderSettings.save(APP, {"repo_url": value}),
        "an app's own update": lambda: ProviderSettings.update(APP, {"repo_url": value}),
        "a capability's own": lambda: ProviderSettings.save_capability(
            APP, "sync", {"repo_url": value}
        ),
        "an instance": lambda: instances.create_instance(APP, "Work", {"repo_url": value}),
    }
    for what, write in writers.items():
        with pytest.raises((ValueError, app_config.AppConfigError), match="repo_url"):
            write()
    assert _stored() == {"repo_url": "https://git.example/acme/repo.git"}
    assert _anywhere(home, value) == [], "no writer put the address anywhere"


@pytest.mark.parametrize("value", KEPT)
def test_an_address_with_no_credential_in_it_saves_as_before(home, value) -> None:
    from personalclaw.providers.settings import ProviderSettings

    ProviderSettings.update(APP, {"repo_url": value})
    assert _stored()["repo_url"] == value


def test_a_setting_kept_as_a_secret_takes_such_an_address_into_the_store(home) -> None:
    from personalclaw.providers.settings import ProviderSettings

    ProviderSettings.update(APP, {"token": REFUSED[0]})
    assert ProviderSettings.load(APP)["token"] == REFUSED[0]
    assert _anywhere(home, LOGIN_WORD) == [".env"]


def test_a_value_already_on_disk_does_not_stop_a_save_that_leaves_it_as_it_was(home) -> None:
    """One saved before this release is the app's to clear where it is used: a save of another
    setting keeps it, and an app saving its own settings is not broken by it."""
    from personalclaw.providers.settings import ProviderSettings

    path = ProviderSettings.config_path(APP)
    path.write_text(json.dumps({"repo_url": REFUSED[0]}), encoding="utf-8")

    ProviderSettings.update(APP, {"branch": "dev"})
    assert _stored() == {"repo_url": REFUSED[0], "branch": "dev"}


def test_a_renamed_capability_carries_such_a_value_rather_than_stalling(home) -> None:
    """A move carries what is on disk; refusing it there would leave the move pending forever."""
    from personalclaw.providers.capability_settings_move import move_on_rename
    from personalclaw.providers.settings import ProviderSettings, load_stored_capability

    old = ProviderSettings.capability_path(APP, "old-sync")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(json.dumps({"repo_url": REFUSED[0]}), encoding="utf-8")

    move_on_rename(APP, "old-sync", "sync")
    assert load_stored_capability(APP, "sync") == {"repo_url": REFUSED[0]}
    assert not old.exists()
