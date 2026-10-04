"""Changing the sign-in password asks for the current one; setting the first one does not.

``POST /api/auth/password`` answered any live session: the only condition was that the request
carried one. So a phone left unlocked, or a session cookie copied once, could replace the owner's
password and lock them out of their own sign-in. Now a password that is already set is changed
only with the current one, and the authenticator code when one is set up — whatever the session,
fresh or not — and a wrong one is refused, written to the security log and counted toward the
sign-in page's lockout. The first password is still set from a live session, as long as that
session signed in recently: it is a new way in.

Every request goes through the production token middleware (``signed_in_sessions``).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from signed_in_sessions import OLD, client_for, guarded_app, owner_presence_rows, sign_in

from personalclaw.auth import credentials as creds
from personalclaw.auth import totp
from personalclaw.config import credentials as cred_store
from personalclaw.config import loader
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h

CURRENT = "correct-horse-battery-staple"
NEW = "a-new-and-longer-passphrase"


@pytest.fixture(autouse=True)
def _clean_state():
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield
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


def _routes(app) -> None:
    app.router.add_post("/api/auth/password", auth_h.api_auth_set_password)


def _lockout(threshold: int) -> None:
    (loader.config_dir() / "config.json").write_text(
        json.dumps({"auth": {"lockout_threshold": threshold}}), encoding="utf-8"
    )


async def _change(client, session, **body) -> Any:
    return await client.post("/api/auth/password", json=body, headers=session.headers)


@pytest.mark.asyncio
async def test_a_change_without_the_current_password_is_refused() -> None:
    creds.set_password("jordan", CURRENT)
    session = sign_in()
    async with client_for(guarded_app(_routes)) as client:
        resp = await _change(client, session, username="jordan", password=NEW)
        assert resp.status == 400, await resp.text()
        assert (await resp.json())["error"]["code"] == "auth_password_required"
    assert creds.verify_password("jordan", CURRENT), "the password was replaced"
    assert not creds.verify_password("jordan", NEW)


@pytest.mark.asyncio
async def test_a_wrong_current_password_is_refused_logged_and_counted(sel_events) -> None:
    creds.set_password("jordan", CURRENT)
    _lockout(2)
    session = sign_in()
    async with client_for(guarded_app(_routes)) as client:
        for _ in range(2):
            resp = await _change(
                client, session, username="jordan", password=NEW, current_password="not-it-at-all"
            )
            assert resp.status == 401
            assert (await resp.json())["error"]["code"] == "auth_invalid_credentials"
        # The guesses count: a session cannot be used to grind the password.
        resp = await _change(
            client, session, username="jordan", password=NEW, current_password=CURRENT
        )
        assert resp.status == 429
        assert resp.headers.get("Retry-After")
    assert creds.verify_password("jordan", CURRENT)
    refused = [r for r in owner_presence_rows(sel_events) if r["outcome"] == "denied"]
    assert len(refused) == 3, refused
    assert {r["resources"] for r in refused} == {"Changing the sign-in password"}
    blob = json.dumps(sel_events)
    assert CURRENT not in blob and NEW not in blob and "not-it-at-all" not in blob


@pytest.mark.asyncio
async def test_the_current_password_changes_it_from_an_old_session_too(sel_events) -> None:
    """The credential is the proof here, so a sign-in's age does not matter."""
    creds.set_password("jordan", CURRENT)
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _change(
            client, session, username="jordan", password=NEW, current_password=CURRENT
        )
        assert resp.status == 200, await resp.text()
    assert creds.verify_password("jordan", NEW)
    assert not creds.verify_password("jordan", CURRENT)
    (granted,) = [r for r in owner_presence_rows(sel_events) if r["outcome"] == "granted"]
    assert granted["metadata"] == {"proof": "identity"}


@pytest.mark.asyncio
async def test_an_enrolled_authenticator_is_asked_for_too(monkeypatch) -> None:
    monkeypatch.setattr(cred_store, "save_credential", lambda k, v: None)
    secret = totp.new_secret()
    creds.set_password("jordan", CURRENT)
    creds.set_totp_secret(secret)
    monkeypatch.setenv(creds.TOTP_SECRET_KEY, secret)
    session = sign_in()
    async with client_for(guarded_app(_routes)) as client:
        resp = await _change(
            client, session, username="jordan", password=NEW, current_password=CURRENT
        )
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == "auth_totp_required"
        resp = await _change(
            client,
            session,
            username="jordan",
            password=NEW,
            current_password=CURRENT,
            totp="000000",
        )
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == "auth_invalid_credentials"
        assert creds.verify_password("jordan", CURRENT)
        resp = await _change(
            client,
            session,
            username="jordan",
            password=NEW,
            current_password=CURRENT,
            totp=totp.code_now(secret),
        )
        assert resp.status == 200, await resp.text()
    assert creds.verify_password("jordan", NEW)


@pytest.mark.asyncio
async def test_the_first_password_is_set_from_a_live_session() -> None:
    session = sign_in()
    async with client_for(guarded_app(_routes)) as client:
        resp = await _change(client, session, username="jordan", password=CURRENT)
        assert resp.status == 200, await resp.text()
        assert (await resp.json())["username"] == "jordan"
    assert creds.verify_password("jordan", CURRENT)


@pytest.mark.asyncio
async def test_the_first_password_from_an_old_session_asks_to_sign_in_again() -> None:
    """The first password is a new way in, so a sign-in from days ago does not set it."""
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await _change(client, session, username="jordan", password=CURRENT)
        assert resp.status == 401
        body = await resp.json()
        assert body["error"]["code"] == "fresh_sign_in_required"
        assert body["error"]["message"].startswith("Setting a sign-in password needs a sign-in")
    assert not creds.has_credentials()


@pytest.mark.asyncio
async def test_a_short_new_password_is_still_refused_naming_the_floor() -> None:
    session = sign_in()
    async with client_for(guarded_app(_routes)) as client:
        resp = await _change(client, session, username="jordan", password="short")
        assert resp.status == 400
        assert "at least" in (await resp.json())["error"]
    assert not creds.has_credentials()
