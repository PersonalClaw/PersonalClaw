"""Storing a secret that changes who can sign in asks for the owner, as its own route does.

Settings → Secrets stores a secret under whatever name it is sent, and a stored secret is mirrored
into the gateway's environment. That is where the gateway reads the local-network bypass, the
authenticator's seed, each inbound surface's token, a channel owner's id and the sign-in settings a
container hands it. So a session days old could, by storing one, let every device on the home
network in with no sign-in at all (at once), replace the second factor, make an integration token
or make another chat account a channel's owner: each a change whose own route asks for the owner
first (``dashboard/owner_presence.py``). Now this asks too. Removing one never asks (containment).
An ordinary secret, and a project's secret, which nothing reads from the environment, are stored as
before, and a connector pack may not name one at all.

Every request goes through the production token middleware (``signed_in_sessions``).
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from signed_in_sessions import OLD, client_for, guarded_app, owner_presence_rows, sign_in

import personalclaw
from personalclaw.auth.credentials import TOTP_SECRET_KEY
from personalclaw.config.credentials import credential_names, owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.handlers import secrets as secrets_h
from personalclaw.inbound.auth import client_surfaces, token_env_key
from personalclaw.packs import connectors
from personalclaw.secrets_vault import SIGN_IN_ENVIRONMENT, is_sign_in_key, project_secret_key

BYPASS = "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"

#: One name of each kind, each from the module that reads it.
SIGN_IN_NAMES = {
    "the local-network bypass": BYPASS,
    "the authenticator's seed": TOTP_SECRET_KEY,
    "an inbound surface's token": token_env_key("mcp"),
    "a channel owner's id": owner_id_credential("telegram"),
    "the id every channel shared": CRED_OWNER_ID,
    "whether sign-in is asked for": "PERSONALCLAW_AUTH_MODE",
    "the first password a container enrolls": "PERSONALCLAW_LOGIN_PASSWORD",
}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    # A stored secret is mirrored into this process's environment: give every name back as it was.
    for name in {*SIGN_IN_NAMES.values(), "GITHUB_TOKEN"}:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    yield
    token_auth.revoke_all_sessions()


@pytest.fixture()
def sel_events(monkeypatch) -> list[dict[str, Any]]:
    import personalclaw.sel as sel_mod

    events: list[dict[str, Any]] = []
    recorder = MagicMock()
    recorder.log_api_access.side_effect = lambda **kw: events.append(kw)
    monkeypatch.setattr(sel_mod, "sel", lambda: recorder)
    return events


def _routes(app: web.Application) -> None:
    secrets_h.register_secrets_routes(app)
    devices_h.register_device_routes(app)


async def _store(client, signed_in, name: str, value: str = "planted-value-1", **extra):
    return await client.post(
        "/api/secrets",
        json={"name": name, "value": value, **extra},
        headers=signed_in.headers,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(SIGN_IN_NAMES))
async def test_an_old_sign_in_is_asked_to_sign_in_again_before_storing_one(kind, sel_events):
    name = SIGN_IN_NAMES[kind]
    old = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _store(client, old, name)
        body = await resp.json()

    assert resp.status == 401, f"{kind} ({name}) was stored for a sign-in days old"
    assert "X-Auth-Required" not in resp.headers
    assert body["error"]["code"] == "fresh_sign_in_required"
    assert body["error"]["message"].startswith(
        "Storing a secret that changes who can sign in needs a sign-in from the last 10 minutes."
    )
    assert name not in credential_names()
    assert name not in os.environ
    [row] = owner_presence_rows(sel_events)
    assert (row["outcome"], row["resources"]) == (
        "denied",
        "Storing a secret that changes who can sign in",
    )


@pytest.mark.asyncio
async def test_the_bypass_an_old_sign_in_stores_no_longer_lets_a_request_with_no_sign_in_in():
    """What storing it did: every request from this computer or the home network got in."""
    old = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        before = await client.get("/api/devices")
        await _store(client, old, BYPASS, value="1")
        after = await client.get("/api/devices")

    assert before.status == 403
    assert after.status == 403, "a request with no sign-in got in after an old sign-in stored it"
    assert os.environ.get(BYPASS) is None


@pytest.mark.asyncio
async def test_a_recent_sign_in_stores_one(sel_events):
    fresh = sign_in()
    async with client_for(guarded_app(_routes)) as client:
        resp = await _store(client, fresh, TOTP_SECRET_KEY, value="JBSWY3DPEHPK3PXP")

    assert resp.status == 200
    assert TOTP_SECRET_KEY in credential_names()
    assert os.environ.get(TOTP_SECRET_KEY) == "JBSWY3DPEHPK3PXP"
    [row] = owner_presence_rows(sel_events)
    assert (row["outcome"], row["metadata"]) == ("granted", {"proof": "recent_sign_in"})


@pytest.mark.asyncio
async def test_an_ordinary_secret_is_stored_from_an_old_sign_in_without_asking(sel_events):
    old = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _store(client, old, "GITHUB_TOKEN")

    assert resp.status == 200
    assert "GITHUB_TOKEN" in credential_names()
    assert owner_presence_rows(sel_events) == []


@pytest.mark.asyncio
async def test_a_projects_secret_of_such_a_name_is_not_asked_about(sel_events):
    """A project's secret is read by that project's runs only, from the store, never from the
    environment: by any name, it changes nobody's sign-in."""
    old = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _store(client, old, BYPASS, value="1", project_id="proj-1")

    assert resp.status == 200
    assert project_secret_key("proj-1", BYPASS) in credential_names()
    assert os.environ.get(BYPASS) is None
    assert owner_presence_rows(sel_events) == []


@pytest.mark.asyncio
async def test_an_old_sign_in_removes_one_without_being_asked(sel_events):
    """Removing one is containment: a planted bypass must go from any sign-in, however old."""
    fresh, old = sign_in(), sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        assert (await _store(client, fresh, BYPASS, value="1")).status == 200
        assert (await client.get("/api/devices")).status == 200  # the bypass is on
        resp = await client.delete(f"/api/secrets?name={BYPASS}", headers=old.headers)
        after = await client.get("/api/devices")

    assert resp.status == 200
    assert after.status == 403
    assert BYPASS not in credential_names()
    assert [r["outcome"] for r in owner_presence_rows(sel_events)] == ["granted"]


def test_a_connector_pack_may_not_name_one():
    for kind, name in sorted(SIGN_IN_NAMES.items()):
        with pytest.raises(connectors.ConnectorResolutionError, match=name):
            connectors._save_credentials([name], {name: "planted-value-1"})
        assert name not in credential_names(), f"a pack stored {kind}"
    assert connectors._save_credentials(["GITHUB_TOKEN"], {"GITHUB_TOKEN": "x"}) == ["GITHUB_TOKEN"]


def test_the_names_are_the_ones_the_gateway_reads():
    """Each sign-in name comes from the module that reads it; the environment's are literals, so
    each must still be read somewhere in the gateway, or it is a name that changes nothing."""
    for surface in client_surfaces():
        assert is_sign_in_key(token_env_key(surface))
    assert is_sign_in_key(owner_id_credential("discord"))
    assert not is_sign_in_key("GITHUB_TOKEN")
    assert not is_sign_in_key("PERSONALCLAW_OWNER")
    root = Path(inspect.getfile(personalclaw)).parent
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
        if path.name != "secrets_vault.py"
    }
    for name in SIGN_IN_ENVIRONMENT:
        assert is_sign_in_key(name)
        readers = [path for path, text in sources.items() if f'"{name}"' in text]
        assert readers, f"{name} is listed as a sign-in setting, but the gateway never reads it"
