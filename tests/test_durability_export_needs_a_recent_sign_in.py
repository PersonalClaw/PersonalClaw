"""Downloading everything PersonalClaw keeps needs a recent sign-in, not just a live session.

The whole-home export (``POST /api/durability/export``) hands out as much as a minted credential:
the owner's work, memory and settings, from one request. A browser sign-in lasts 30 days by
default, so a session left signed in on a shared or lost device could otherwise take the whole
home. So the export asks for proof that the owner is here now (``dashboard/owner_presence.py``),
as minting a credential does: a sign-in from the last ten minutes, the local machine secret, the
desktop app's own window, or authentication switched off. An old session gets
``401 fresh_sign_in_required``, a sentence saying how to sign in again, and a security log row,
and nothing is exported.

Every request goes through the production token middleware (``signed_in_sessions``).
"""

from __future__ import annotations

import io
import zipfile
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from signed_in_sessions import (
    LOCAL_SECRET,
    OLD,
    client_for,
    guarded_app,
    owner_presence_rows,
    sign_in,
)

from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.owner_presence import ACTION_EXPORT, PRESENCE_WINDOW_SECS

pytestmark = pytest.mark.security

EXPORT = "/api/durability/export"


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


def _routes(app: web.Application) -> None:
    from personalclaw.dashboard.handlers import durability

    app.router.add_post(EXPORT, durability.api_durability_export)


def _exports(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("operation") == "durability.export"]


def _is_the_zip(blob: bytes) -> bool:
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    return any(n.endswith("MANIFEST.json") for n in names)


@pytest.mark.asyncio
async def test_an_old_session_is_asked_to_sign_in_again(sel_events) -> None:
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(EXPORT, json={}, headers=session.headers)
        assert resp.status == 401, await resp.text()
        # Not a sign-out: the dashboard must not read this device as signed out.
        assert resp.headers.get("X-Auth-Required") is None
        assert resp.content_type == "application/json"
        error = (await resp.json())["error"]
    assert error["code"] == "fresh_sign_in_required"
    assert error["message"].startswith(f"{ACTION_EXPORT} needs a sign-in from the last 10 minutes.")
    assert error["detail"]["action"] == ACTION_EXPORT
    assert error["detail"]["window_secs"] == PRESENCE_WINDOW_SECS
    (row,) = owner_presence_rows(sel_events)
    assert (row["outcome"], row["resources"]) == ("denied", ACTION_EXPORT)
    assert not _exports(sel_events), "the refused request exported"


@pytest.mark.asyncio
async def test_a_page_that_asks_gets_the_question_and_never_the_zip() -> None:
    """The dashboard's export says it asks the owner, so the question reaches it as an answer
    marked as one, never as the download."""
    session = sign_in(age=OLD)
    asks = {**session.headers, "X-PersonalClaw-Consent": "ask"}
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(EXPORT, json={}, headers=asks)
        assert (resp.status, resp.headers.get("X-PersonalClaw-Consent-Asked")) == (200, "1")
        assert resp.content_type == "application/json"
        assert (await resp.json())["error"]["code"] == "fresh_sign_in_required"


@pytest.mark.asyncio
async def test_a_recent_sign_in_exports(sel_events) -> None:
    session = sign_in(age=PRESENCE_WINDOW_SECS - 60)
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(EXPORT, json={}, headers=session.headers)
        assert resp.status == 200, await resp.text()
        assert _is_the_zip(await resp.read())
    (row,) = owner_presence_rows(sel_events)
    assert row["outcome"] == "granted" and row["metadata"] == {"proof": "recent_sign_in"}


@pytest.mark.asyncio
async def test_the_local_machine_secret_exports_without_asking() -> None:
    session = sign_in(age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        wrong = await client.post(
            EXPORT, json={}, headers={**session.headers, "X-Local-Secret": "b" * 32}
        )
        assert wrong.status == 401
        resp = await client.post(
            EXPORT, json={}, headers={**session.headers, "X-Local-Secret": LOCAL_SECRET}
        )
        assert resp.status == 200, await resp.text()
        assert _is_the_zip(await resp.read())


@pytest.mark.asyncio
async def test_the_desktop_apps_own_window_exports_however_long_it_has_been_open() -> None:
    window = sign_in(issuer=token_auth.ISSUER_READY, kind="desktop", name="PersonalClaw", age=OLD)
    async with client_for(guarded_app(_routes)) as client:
        resp = await client.post(EXPORT, json={}, headers=window.headers)
        assert resp.status == 200, await resp.text()
