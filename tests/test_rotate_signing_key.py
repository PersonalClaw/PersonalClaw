"""The owner can replace the session signing key, and every device is told why it was signed out.

`session_store.rotate_key()` existed and nothing called it: there was no way to replace the key
every sign-in is signed with, from Settings or the CLI. Replacing it is the answer to "the key, or
a sign-in, may have been copied" — signing sessions out one at a time leaves the key that could
mint new ones. And had anything called it, it would not have been enough: the running gateway
kept the old key cached and every session in memory, and the store's endings were cleared, so
every signed-out device would have read the same "not signed in" a stranger does.

What is pinned here:

* a rotation ends EVERY session, on disk and in this process, and the key really changes;
* each ended device's next request is told the key was replaced — while a token the gateway
  never signed still gets the one sentence a stranger gets;
* the replaced key is kept ONLY to recognise the tokens it signed: it never admits one;
* the Settings route needs ``{"confirm": true}`` and answers the caller's own sentence;
* the CLI goes through a running gateway, and never rotates behind one that refused.
"""

from __future__ import annotations

import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.handlers.devices import register_device_routes

PORT = 10000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """The PERSISTED key and the sessions under *tmp_path*; neither blanket bypass set."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


def _mint(issuer: str = token_auth.ISSUER_LOGIN) -> token_auth.MintedSession:
    return token_auth.mint_session("owner", 7 * 86400, issuer=issuer)


async def _client() -> TestClient:
    """The device routes behind the real token middleware — where the key is used."""
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    register_device_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"pc_token_{PORT}={token}"}


# ── the rotation itself ────────────────────────────────────────────────────────


def test_a_rotation_ends_every_session_and_replaces_the_key(tmp_path):
    """🔴 Red on main: there was no rotation to call (`rotate_signing_key` did not exist)."""
    browser, device = _mint(), _mint(token_auth.ISSUER_PAIR)
    old_key = (tmp_path / session_store.KEY_FILE).read_bytes()
    assert token_auth.validate_token(browser.token, use_session_exp=True)[0]

    signed_out = token_auth.rotate_signing_key(actor="owner")

    assert signed_out == 2
    assert (tmp_path / session_store.KEY_FILE).read_bytes() != old_key, "the key did not change"
    for minted in (browser, device):
        valid, _user, reason = token_auth.validate_token(minted.token, use_session_exp=True)
        assert not valid, f"a session signed with the replaced key still works ({reason})"
        ended = session_store.ended_session(minted.nonce)
        assert ended is not None and ended.reason == session_store.END_KEY_REPLACED
    assert session_store.load_session_records() == {}
    # And signing in again works, under the new key.
    fresh = _mint()
    assert token_auth.validate_token(fresh.token, use_session_exp=True)[0]


def test_a_rotation_on_an_ephemeral_key_draws_a_new_one():
    """`--test-mode` and tests sign with an in-memory key; rotating it must not write a key file."""
    token_auth.use_ephemeral_secret(b"ephemeral-rotation-key-00000001")
    try:
        minted = _mint()
        token_auth.rotate_signing_key(actor="owner")
        assert not token_auth.validate_token(minted.token, use_session_exp=True)[0]
        assert token_auth._secret() != b"ephemeral-rotation-key-00000001"
    finally:
        token_auth.use_persistent_secret()


# ── what each signed-out device is told ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_signed_out_device_is_told_the_key_was_replaced():
    minted = _mint()
    token_auth.rotate_signing_key(actor="owner")
    client = await _client()
    try:
        resp = await client.get("/api/devices", headers=_cookie(minted.token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 403 and resp.headers.get("X-Auth-Required") == "true"
    assert payload["error"]["code"] == "session_signed_out", payload
    assert payload["error"]["detail"]["reason"] == session_store.END_KEY_REPLACED
    message = payload["error"]["message"]
    assert "the key PersonalClaw signs sign-ins with was replaced" in message, message
    assert message.startswith("Every device was signed out"), message


@pytest.mark.asyncio
async def test_a_token_the_gateway_never_signed_learns_nothing_new():
    """CONTROL: keeping the old key must not tell a forger what it tells a signed-out device."""
    minted = _mint()
    token_auth.rotate_signing_key(actor="owner")
    payload_part = minted.token.split(".")[0]
    forged = f"{payload_part}.{'A' * 43}"
    client = await _client()
    try:
        resp = await client.get("/api/devices", headers=_cookie(forged))
        payload = await resp.json()
    finally:
        await client.close()
    assert payload["error"]["code"] == "session_required", payload
    assert "replaced" not in payload["error"]["message"]


def test_the_replaced_key_never_admits_a_session():
    """The old key recognises its tokens only to explain their ending. A token it signs for a
    session that is LIVE now — someone holding the copied key — is refused all the same."""
    token_auth.rotate_signing_key(actor="owner")
    old = session_store.retired_key()
    assert old, "the replaced key must be kept to explain the endings"
    live = _mint()
    claims = json.loads(token_auth._b64url_decode(live.token.split(".")[0]))
    payload = json.dumps(claims, separators=(",", ":")).encode()
    import hashlib
    import hmac as _hmac

    sig = token_auth._b64url_encode(_hmac.new(old, payload, hashlib.sha256).digest())
    forged = f"{token_auth._b64url_encode(payload)}.{sig}"
    valid, _user, reason = token_auth.validate_token(forged, use_session_exp=True)
    assert not valid and reason == "invalid signature"


def test_the_replaced_key_is_forgotten_when_nothing_it_signed_is_explained(tmp_path):
    token_auth.rotate_signing_key(actor="owner")
    assert session_store.retired_key()
    # Other writes to the store keep it...
    session_store.save_session_records(session_store.load_session_records())
    assert session_store.retired_key()
    # ...until its retention passes.
    path = tmp_path / session_store.SESSIONS_FILE
    data = json.loads(path.read_text())
    data["retired_key"]["at"] = time.time() - session_store.RETIRED_KEY_RETENTION_SECS - 1
    path.write_text(json.dumps(data))
    assert session_store.retired_key() == b""


# ── the Settings route ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_route_needs_confirmation_and_rotates_nothing_without_it():
    minted = _mint()
    client = await _client()
    try:
        resp = await client.post("/api/auth/rotate-key", json={}, headers=_cookie(minted.token))
        payload = await resp.json()
    finally:
        await client.close()
    assert resp.status == 400 and payload["error"]["code"] == "confirmation_required"
    assert token_auth.validate_token(minted.token, use_session_exp=True)[0], "it rotated anyway"


@pytest.mark.asyncio
async def test_the_route_signs_everyone_out_and_answers_the_callers_sentence():
    """🔴 Red on main: the route did not exist."""
    caller, other = _mint(), _mint(token_auth.ISSUER_PAIR)
    client = await _client()
    try:
        resp = await client.post(
            "/api/auth/rotate-key", json={"confirm": True}, headers=_cookie(caller.token)
        )
        payload = await resp.json()
        after = await client.get("/api/devices", headers=_cookie(caller.token))
        after_payload = await after.json()
    finally:
        await client.close()
    assert resp.status == 200, payload
    assert payload["signed_out"] == 2
    notice = payload["notice"]
    assert notice["code"] == "session_signed_out"
    assert notice["reason"] == session_store.END_KEY_REPLACED
    assert "was replaced" in notice["message"]
    # The caller's own sentence is the one its next request carries.
    assert after_payload["error"]["message"] == notice["message"]
    assert not token_auth.validate_token(other.token, use_session_exp=True)[0]


@pytest.mark.asyncio
async def test_one_replacement_is_one_key_event_in_the_security_log(monkeypatch):
    """The key's event is the rotation's own, so the CLI with no gateway writes it too; the route
    wrote a second, and one replacement read as two in the security log."""
    events: list[dict] = []

    class _Log:
        def log_api_access(self, **fields):  # noqa: ANN003
            events.append(fields)

        def __getattr__(self, _name):  # noqa: ANN204 — every other SEL writer on the path
            return lambda *a, **k: None

    log = _Log()
    monkeypatch.setattr(token_auth, "_sel_fn", lambda: log)
    monkeypatch.setattr("personalclaw.sel.sel", lambda: log)
    caller = _mint()
    client = await _client()
    try:
        resp = await client.post(
            "/api/auth/rotate-key", json={"confirm": True}, headers=_cookie(caller.token)
        )
    finally:
        await client.close()
    assert resp.status == 200
    key_events = [e for e in events if e.get("operation") == "session_key_replaced"]
    assert [e.get("resources") for e in key_events] == ["sessions_signed_out=1"], key_events


# ── the CLI ────────────────────────────────────────────────────────────────────


def test_the_cli_rotates_here_only_when_no_gateway_is_running(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from personalclaw.auth import cli

    minted = _mint()
    old_key = (tmp_path / session_store.KEY_FILE).read_bytes()
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)  # and the home records no gateway
    assert cli.auth_cmd(SimpleNamespace(auth_command="rotate-key", port=None)) == 0
    assert (tmp_path / session_store.KEY_FILE).read_bytes() != old_key
    assert not token_auth.validate_token(minted.token, use_session_exp=True)[0]
    assert "Replaced the sign-in key" in capsys.readouterr().out


def test_the_cli_never_rotates_behind_a_gateway_that_refused(tmp_path, monkeypatch, capsys):
    """A running gateway holds the key in memory: rotating the file from here while it refused
    would leave it signing with the old key. So a refusal is reported, and nothing changes."""
    from types import SimpleNamespace

    from fakes import gateway_stand_in

    from personalclaw.auth import cli

    minted = _mint()
    old_key = (tmp_path / session_store.KEY_FILE).read_bytes()
    refusal = {"error": {"code": "service_unavailable", "message": "the key file is read-only"}}
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
    (tmp_path / ".local_secret").write_text("this-homes-secret", encoding="utf-8")
    routes = {("POST", "/api/auth/rotate-key"): (503, refusal)}
    with gateway_stand_in(tmp_path, routes=routes) as gateway:
        gateway.record()
        assert cli.auth_cmd(SimpleNamespace(auth_command="rotate-key", port=None)) == 1
    (asked,) = gateway.asked("/api/auth/rotate-key")
    assert asked.headers["x-internal-secret"] == "this-homes-secret"
    assert (tmp_path / session_store.KEY_FILE).read_bytes() == old_key
    assert token_auth.validate_token(minted.token, use_session_exp=True)[0]
    printed = capsys.readouterr()
    assert "the key file is read-only" in printed.err, "a refusal is printed on stderr"
    assert "Replaced" not in printed.out
