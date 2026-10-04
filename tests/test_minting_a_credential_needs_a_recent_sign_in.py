"""Minting a lasting credential needs a recent sign-in, not just a live session.

Any live session — a browser sign-in lasts 30 days by default and up to 90 — could mint a device
pairing code, a device sign-in code, an integration token lasting 90 days, or the code that makes
an account a channel's owner (which can then ask that channel for a dashboard link). Each of those
outlives the session that minted it, and signing that session out leaves it working. So each now
needs proof that the owner is here now (``dashboard/owner_presence.py``): a sign-in from the last
ten minutes, the local machine secret, or the desktop app's own window — and an old session gets
``401 fresh_sign_in_required``, a sentence saying how to sign in again, and a security log row.

Containment is unchanged: signing a device out, signing out every other device, replacing the
signing key, revoking an integration token and turning incident mode on work from any session,
however old, because they are what the owner reaches for when something is wrong.

Every request goes through the production token middleware (``signed_in_sessions``).
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from signed_in_sessions import (
    COOKIE,
    LAN_ADDRESS,
    LOCAL_SECRET,
    OLD,
    ORIGIN,
    client_for,
    guarded_app,
    owner_presence_rows,
    sign_in,
)

from personalclaw import channel_transports, channel_trust
from personalclaw.auth import credentials as creds
from personalclaw.auth import enrollment, pairing
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.config import loader
from personalclaw.config.credentials import owner_id_credential
from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.owner_presence import PRESENCE_WINDOW_SECS
from personalclaw.inbound import auth as inbound_auth
from personalclaw.inbound import clients as clients_mod
from personalclaw.sdk.channel import ChannelCapabilities

PASSWORD = "correct-horse-battery-staple"
CHANNEL = "examplechat"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    clients_mod.reset_for_tests()
    monkeypatch.delenv(owner_id_credential(CHANNEL), raising=False)
    channel_transports.register_transport(_Channel())
    yield
    channel_transports.unregister_transport(CHANNEL)
    os.environ.pop(owner_id_credential(CHANNEL), None)
    for surface in inbound_auth.surfaces():
        os.environ.pop(inbound_auth.token_env_key(surface), None)
    clients_mod.reset_for_tests()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@pytest.fixture()
def sel_events(monkeypatch) -> list[dict[str, Any]]:
    import personalclaw.sel as sel_mod

    events: list[dict[str, Any]] = []
    recorder = MagicMock()
    recorder.log_api_access.side_effect = lambda **kw: events.append(kw)
    monkeypatch.setattr(sel_mod, "sel", lambda: recorder)
    return events


class _Channel(ChannelTransportProvider):
    """A chat channel whose owner pairs from the dashboard."""

    name = property(lambda self: CHANNEL)
    display_name = property(lambda self: "Example Chat")

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(inbound=True, owner_pairing=True)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True

    async def health(self) -> dict[str, Any]:
        return {"state": "ready", "detail": "example"}


def _routes(app: web.Application) -> None:
    from personalclaw.dashboard.handlers.channel_owner import (
        api_channel_owner_pairing_cancel,
        api_channel_owner_pairing_start,
    )
    from personalclaw.dashboard.handlers.core import api_incident, api_personalclaw_config_patch
    from personalclaw.dashboard.handlers.external_access import api_external_access_client

    devices_h.register_device_routes(app)
    app.router.add_post("/api/auth/enroll/start", auth_h.api_auth_enroll_start)
    app.router.add_post("/api/auth/confirm", auth_h.api_auth_confirm)
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    app.router.add_post("/api/external-access/clients", api_external_access_client)
    app.router.add_delete("/api/external-access/clients/{client_id}", api_external_access_client)
    app.router.add_post("/api/channels/{name}/owner/pairing", api_channel_owner_pairing_start)
    app.router.add_delete("/api/channels/{name}/owner/pairing", api_channel_owner_pairing_cancel)
    app.router.add_post("/api/incident", api_incident)
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)


def _clients() -> dict:
    path = clients_mod.clients_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _codes(module) -> dict:
    path = module.codes_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


#: Each credential kind: the request that mints it, and what it leaves behind when it does.
MINTS = {
    "pairing code": (
        "/api/devices/pair/start",
        {},
        lambda: _codes(pairing),
        "Pairing a device",
    ),
    "device sign-in code": (
        "/api/auth/enroll/start",
        {},
        lambda: _codes(enrollment),
        "Making a device sign-in code",
    ),
    "integration token": (
        "/api/external-access/clients",
        {"label": "Build bot", "surfaces": ["mcp"]},
        _clients,
        "Creating an integration token",
    ),
    "channel owner code": (
        f"/api/channels/{CHANNEL}/owner/pairing",
        {},
        lambda: channel_trust.owner_pairing_status(CHANNEL)["active"] or {},
        "Pairing a channel’s owner",
    ),
}


async def _mint(client, kind: str, headers: dict[str, str]) -> Any:
    path, body, _left, _action = MINTS[kind]
    return await client.post(path, json=body, headers=headers)


# ── an old session is asked to sign in again, and nothing is minted ─────


@pytest.mark.parametrize("kind", sorted(MINTS))
@pytest.mark.asyncio
async def test_an_old_session_cannot_mint_and_is_told_to_sign_in_again(kind, sel_events) -> None:
    _path, _body, left, action = MINTS[kind]
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _mint(client, kind, session.headers)
        assert resp.status == 401, await resp.text()
        # Not a sign-out: the dashboard must not read this device as signed out.
        assert resp.headers.get("X-Auth-Required") is None
        error = (await resp.json())["error"]
    assert error["code"] == "fresh_sign_in_required"
    assert error["message"].startswith(f"{action} needs a sign-in from the last 10 minutes.")
    assert "`personalclaw token`" in error["message"], "with no password, the link is the way"
    assert error["detail"] == {
        "action": action,
        "window_secs": PRESENCE_WINDOW_SECS,
        "password": False,
        "second_factor": False,
    }
    assert not left(), f"a {kind} was minted for a session that signed in days ago"
    (row,) = owner_presence_rows(sel_events)
    assert (row["outcome"], row["resources"]) == ("denied", action)


@pytest.mark.asyncio
async def test_a_page_that_asks_gets_the_question_as_an_answer_and_any_other_client_a_401():
    """The page asks the owner to sign in again by design, so the question reaches it as an
    answer, not as a failed request in its console — as a consent question does."""
    session = sign_in(age=OLD)
    asks = {**session.headers, "X-PersonalClaw-Consent": "ask"}
    async with client_for(guarded_app(_routes)) as client:
        resp = await _mint(client, "pairing code", asks)
        assert (resp.status, resp.headers.get("X-PersonalClaw-Consent-Asked")) == (200, "1")
        asked = await resp.json()
        resp = await _mint(client, "pairing code", session.headers)
        assert (resp.status, resp.headers.get("X-PersonalClaw-Consent-Asked")) == (401, None)
        assert await resp.json() == asked
    assert asked["error"]["code"] == "fresh_sign_in_required"
    assert not _codes(pairing), "the question minted nothing"


@pytest.mark.asyncio
async def test_the_answer_offers_the_password_where_password_sign_in_is_on() -> None:
    creds.set_password("jordan", PASSWORD)
    (loader.config_dir() / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
    )
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        error = (await (await _mint(client, "pairing code", session.headers)).json())["error"]
    assert error["detail"]["password"] is True
    assert error["message"].endswith("Confirm it’s you by signing in again with your password.")


# ── who can mint ────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(MINTS))
@pytest.mark.asyncio
async def test_a_recent_sign_in_mints(kind, sel_events) -> None:
    _path, _body, left, _action = MINTS[kind]
    session = sign_in(age=PRESENCE_WINDOW_SECS - 60)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _mint(client, kind, session.headers)
        assert resp.status == 200, await resp.text()
    assert left()
    (row,) = owner_presence_rows(sel_events)
    assert row["outcome"] == "granted" and row["metadata"] == {"proof": "recent_sign_in"}


@pytest.mark.parametrize("kind", sorted(MINTS))
@pytest.mark.asyncio
async def test_the_desktop_apps_own_window_mints_however_long_it_has_been_open(kind) -> None:
    """The app signs its window in itself, with the session the gateway handed it at start."""
    _path, _body, left, _action = MINTS[kind]
    window = sign_in(issuer=token_auth.ISSUER_READY, kind="desktop", name="PersonalClaw", age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _mint(client, kind, window.headers)
        assert resp.status == 200, await resp.text()
    assert left()


@pytest.mark.asyncio
async def test_the_gateways_own_session_counts_only_on_this_computer() -> None:
    """Carried to another device, the session the gateway handed its starter is just old."""
    window = sign_in(issuer=token_auth.ISSUER_READY, kind="desktop", age=OLD)
    async with client_for(guarded_app(_routes, remote=LAN_ADDRESS)) as client:
        resp = await _mint(client, "pairing code", window.headers)
        assert resp.status == 401
    assert not _codes(pairing)


@pytest.mark.asyncio
async def test_the_local_machine_secret_mints_from_an_old_session() -> None:
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        wrong = await _mint(client, "pairing code", {**session.headers, "X-Local-Secret": "b" * 32})
        assert wrong.status == 401
        resp = await _mint(
            client, "pairing code", {**session.headers, "X-Local-Secret": LOCAL_SECRET}
        )
        assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
async def test_the_local_machine_secret_counts_only_from_this_computer() -> None:
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes, remote=LAN_ADDRESS)) as client:
        resp = await _mint(
            client, "pairing code", {**session.headers, "X-Local-Secret": LOCAL_SECRET}
        )
        assert resp.status == 401


@pytest.mark.asyncio
async def test_with_authentication_off_there_is_no_sign_in_to_be_recent() -> None:
    from personalclaw.auth.modes import AuthConfig, AuthMode

    app = web.Application()
    app["port"] = 10000
    app["allowed_origins"] = {ORIGIN}
    app["auth_cfg"] = AuthConfig(mode=AuthMode.NONE)
    devices_h.register_device_routes(app)
    async with client_for(app) as client:
        resp = await client.post("/api/devices/pair/start", json={})
        assert resp.status == 200, await resp.text()


@pytest.mark.asyncio
async def test_an_app_never_stands_for_the_owner_however_recent_the_session() -> None:
    """An app's token narrows the owner's session to the app; it never mints for the owner."""
    session = sign_in()
    app_token, _expires = token_auth.app_session_token("jordan", "notes-app")
    headers = {**session.headers, "Authorization": f"Bearer {app_token}"}
    async with client_for(guarded_app(_routes)) as client:
        resp = await _mint(client, "pairing code", headers)
        assert resp.status == 401
        resp = await _mint(client, "pairing code", session.headers)
        assert resp.status == 200, await resp.text()


# ── signing in again ────────────────────────────────────────────────────


def _offer_password_sign_in() -> None:
    creds.set_password("jordan", PASSWORD)
    (loader.config_dir() / "config.json").write_text(
        json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_signing_in_again_lets_the_same_device_mint() -> None:
    _offer_password_sign_in()
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        assert (await _mint(client, "pairing code", session.headers)).status == 401
        resp = await client.post(
            "/api/auth/confirm", json={"password": PASSWORD}, headers=session.headers
        )
        assert resp.status == 200, await resp.text()
        renewed = resp.cookies[COOKIE].value
        assert renewed != session.token
        again = await _mint(client, "pairing code", {"Cookie": f"{COOKIE}={renewed}"})
        assert again.status == 200, await again.text()
        # The sign-in it replaced ended: a copy of the old cookie gains nothing from the check.
        old = await client.get("/api/auth/session", headers={"Cookie": f"{COOKIE}={session.token}"})
        assert old.status == 403
        assert (await old.json())["error"]["detail"]["reason"] == "replaced"
    assert session.nonce not in session_store.load_session_records()


@pytest.mark.asyncio
async def test_a_paired_phone_that_signs_in_again_stays_paired() -> None:
    _offer_password_sign_in()
    phone = sign_in(issuer=token_auth.ISSUER_PAIR, kind="mobile", name="Pixel", age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(
            "/api/auth/confirm", json={"password": PASSWORD}, headers=phone.headers
        )
        assert resp.status == 200, await resp.text()
    (record,) = session_store.paired_sessions().values()
    assert (record.device.name, record.device.kind) == ("Pixel", "mobile")


@pytest.mark.asyncio
async def test_a_wrong_password_signs_nothing_in_and_is_logged(sel_events) -> None:
    _offer_password_sign_in()
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(
            "/api/auth/confirm", json={"password": "not-the-password"}, headers=session.headers
        )
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == "auth_invalid_credentials"
        assert COOKIE not in resp.cookies
        assert (await _mint(client, "pairing code", session.headers)).status == 401
    denied = [r for r in owner_presence_rows(sel_events) if r["outcome"] == "denied"]
    assert denied[0]["resources"] == "Signing in again"


@pytest.mark.asyncio
async def test_signing_in_again_with_a_password_needs_password_sign_in_on() -> None:
    creds.set_password("jordan", PASSWORD)  # set, but the sign-in page is off
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(
            "/api/auth/confirm", json={"password": PASSWORD}, headers=session.headers
        )
        assert resp.status == 403
        error = (await resp.json())["error"]
    assert error["code"] == "auth_not_enabled"
    assert "`personalclaw token`" in error["message"]


# ── making sign-in less strict is changing how the owner signs in ──────


@pytest.mark.asyncio
async def test_an_old_session_cannot_turn_password_sign_in_on() -> None:
    creds.set_password("jordan", PASSWORD)
    session = sign_in(age=OLD)
    body = {"path": "auth.login_enabled", "value": True, "confirm": True}
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.patch("/api/config/personalclaw", json=body, headers=session.headers)
        assert resp.status == 401, await resp.text()
        assert (await resp.json())["error"]["code"] == "fresh_sign_in_required"
        assert loader.AppConfig.load().auth.login_enabled is False
        fresh = sign_in()
        resp = await client.patch("/api/config/personalclaw", json=body, headers=fresh.headers)
        assert resp.status == 200, await resp.text()
    assert loader.AppConfig.load().auth.login_enabled is True


@pytest.mark.asyncio
async def test_an_old_session_can_still_make_sign_in_stricter() -> None:
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.patch(
            "/api/config/personalclaw",
            json={"path": "auth.lockout_threshold", "value": 3},
            headers=session.headers,
        )
        assert resp.status == 200, await resp.text()
    assert loader.AppConfig.load().auth.lockout_threshold == 3


# ── containment never asks ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_containment_works_from_an_old_session() -> None:
    from personalclaw.guardrails import incident

    me = sign_in(age=OLD)
    other = sign_in(name="Chrome on Mac")
    third = sign_in(name="Safari on iPhone", kind="mobile")
    client_rec, _token = clients_mod.create_client("Build bot", surfaces=["mcp"], actor="test")
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(f"/api/devices/{other.session_id}/revoke", headers=me.headers)
        assert resp.status == 200, await resp.text()
        resp = await client.delete(
            f"/api/external-access/clients/{client_rec.client_id}", headers=me.headers
        )
        assert resp.status == 200, await resp.text()
        resp = await client.post("/api/incident", json={"reason": "lost phone"}, headers=me.headers)
        assert resp.status == 200, await resp.text()
        assert incident.incident_active()
        resp = await client.post(
            f"/api/channels/{CHANNEL}/owner/pairing", headers=me.headers, json={}
        )
        assert resp.status == 401  # minting still asks, beside the containment that did not
        resp = await client.delete(f"/api/channels/{CHANNEL}/owner/pairing", headers=me.headers)
        assert resp.status == 200, await resp.text()
        resp = await client.post(
            "/api/devices/revoke-others", json={"confirm": True}, headers=me.headers
        )
        assert resp.status == 200, await resp.text()
        assert third.nonce not in session_store.load_session_records()
        resp = await client.post("/api/auth/rotate-key", json={"confirm": True}, headers=me.headers)
        assert resp.status == 200, await resp.text()
    assert client_rec.client_id not in _clients()
