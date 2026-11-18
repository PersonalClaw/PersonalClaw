"""A scoped pairing: one session model with a scope on it (scoped-pairing design §1, C1).

An automation client is paired like a phone, from a code the owner makes in Settings → Devices,
but the code names what the session may reach (its *scope*) and how long it lasts. The scope is
one fact kept in two places, a signed claim on the token and a field on the session's row, and
each request checks they agree, in both directions. Scoped tokens are signed under a key derived
for them, so a payload can't move between the two kinds. Nothing the client sends names or widens
the scope, sets its kind or renews it, and owner presence never counts it as the owner.

The gate that narrows what a scoped session reaches is the next commit's (C2); these tests hold
the session itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import DummyCookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from signed_in_sessions import LOCAL_SECRET, ORIGIN, guarded_app, sign_in, without_sign_in

from personalclaw.auth import pairing
from personalclaw.dashboard import scoped_sessions
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10000
COOKIE = f"pc_token_{PORT}"
DAY = 86400


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every store this surface touches points at *tmp_path*, never the real home."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@pytest.fixture()
def sel_events(monkeypatch) -> list[dict[str, Any]]:
    """Every SEL row, through the real ``sel()`` indirection."""
    import personalclaw.sel as sel_mod

    events: list[dict[str, Any]] = []
    recorder = MagicMock()
    recorder.log_api_access.side_effect = lambda **kw: events.append(kw)
    monkeypatch.setattr(sel_mod, "sel", lambda: recorder)
    return events


def _scopes():
    from personalclaw.dashboard import device_scopes

    return device_scopes


def _scoped(scope=("loops",), *, ttl: int = 30 * DAY, name: str = "Nightly runner"):
    """A scoped pairing's session, minted the way pair/complete mints one."""
    device = ss.DeviceInfo(id=ss.new_device_id(), name=name, kind="automation")
    return token_auth.mint_session(
        devices_h.PAIRED_DEVICE_USER,
        ttl,
        issuer=ss.ISSUER_PAIR,
        device=device,
        bound=True,
        scope=tuple(scope),
    )


def _phone():
    device = ss.DeviceInfo(id=ss.new_device_id(), name="Pixel", kind="mobile")
    return token_auth.mint_session(
        devices_h.PAIRED_DEVICE_USER, 30 * DAY, issuer=ss.ISSUER_PAIR, device=device, bound=True
    )


def _claims(token: str) -> dict[str, Any]:
    return json.loads(token_auth._b64url_decode(token.split(".", 1)[0]))


def _resigned(token: str, key: bytes) -> str:
    payload = token_auth._b64url_decode(token.split(".", 1)[0])
    sig = base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest())
    return f"{token.split('.', 1)[0]}.{sig.rstrip(b'=').decode()}"


def _derived_key() -> bytes:
    return hmac.new(token_auth._secret(), b"scope\0v1", hashlib.sha256).digest()


def _rewrite_row(nonce: str, change) -> None:
    """Edit *nonce*'s stored row as raw JSON (what another build, or a hand, would write), then
    forget the in-memory cache, so the next check reads the row."""
    path = ss.sessions_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    change(raw["sessions"][nonce])
    path.write_text(json.dumps(raw), encoding="utf-8")
    token_auth._state.clear_all()


def _valid(token: str) -> tuple[bool, str, str]:
    return token_auth.validate_token(token, use_session_exp=True)


# ── The vocabulary ──────────────────────────────────────────────────────


def test_the_vocabulary_is_fixed_and_a_scope_is_sorted() -> None:
    scopes = _scopes()
    assert scopes.SCOPES == ("status", "loops", "approvals", "loop_create", "loop_land")
    assert scopes.normalize_scope(["status", "loops", "loops"]) == ("loops", "status")
    for bad in (["loops", "admin"], ["LOOPS"], "loops", [1], [], None, {"loops": 1}):
        with pytest.raises(ValueError):
            scopes.normalize_scope(bad)


def test_this_release_grants_only_the_read_scopes() -> None:
    """The scopes that answer or cause work need the client's own principal (the next PR):
    until it exists, a code can't be made for them."""
    assert _scopes().GRANTABLE == ("status", "loops")


# ── Signing (F7) ────────────────────────────────────────────────────────


def test_the_hmac_domains_live_in_one_list_and_none_is_a_prefix_of_another() -> None:
    from personalclaw.dashboard import hmac_domains
    from personalclaw.dashboard.sign_in_pair import pair_for

    domains = hmac_domains.HMAC_DOMAINS
    assert set(domains) == {b"pair\0", b"url\0", b"scope\0v1"}
    for a in domains:
        for b in domains:
            assert a == b or not b.startswith(a), (a, b)
    secret = b"k" * 32
    expected = hmac.new(secret, hmac_domains.PAIR + b"n1", hashlib.sha256).digest()
    assert pair_for(secret, "n1") == base64.urlsafe_b64encode(expected).rstrip(b"=").decode()


def test_a_scoped_token_carries_its_scope_and_is_signed_under_the_derived_key() -> None:
    minted = _scoped(("status", "loops"))
    assert _claims(minted.token)["scope"] == ["loops", "status"]
    assert minted.token == _resigned(minted.token, _derived_key())
    assert minted.token != _resigned(minted.token, token_auth._secret())
    assert _valid(minted.token)[0] is True
    assert scoped_sessions.token_scope(minted.token) == ("loops", "status")


def test_a_scoped_payload_under_the_base_key_is_refused() -> None:
    minted = _scoped()
    forged = _resigned(minted.token, token_auth._secret())
    assert _valid(forged)[0] is False


def test_an_unscoped_payload_under_the_derived_key_is_refused() -> None:
    minted = _phone()
    forged = _resigned(minted.token, _derived_key())
    assert _valid(forged)[0] is False
    assert _valid(minted.token)[0] is True


def test_a_refused_scoped_session_still_says_why() -> None:
    """``_verified_claims`` knows the derived key: an ended scoped session gets its sentence."""
    minted = _scoped()
    token_auth.sign_out([minted.nonce], ss.END_SIGNED_OUT_ELSEWHERE, actor="owner")
    notice = token_auth.signed_out_notice(minted.token)
    assert notice is not None and notice.reason == ss.END_SIGNED_OUT_ELSEWHERE


def test_a_scope_is_minted_only_for_a_bound_pairing() -> None:
    with pytest.raises(ValueError):
        token_auth.mint_session("owner", DAY, issuer=ss.ISSUER_LOGIN, bound=True, scope=("loops",))
    with pytest.raises(ValueError):
        token_auth.mint_session("x", DAY, issuer=ss.ISSUER_PAIR, bound=False, scope=("loops",))
    with pytest.raises(ValueError):
        token_auth.mint_session("x", DAY, issuer=ss.ISSUER_PAIR, bound=True, scope=("admin",))


# ── Claim and record agree, both ways (F6) ──────────────────────────────


def test_the_row_keeps_the_scope_and_a_restart_reads_it_back() -> None:
    minted = _scoped(("status", "loops"))
    record = ss.load_session_records()[minted.nonce]
    assert record.scope == ("loops", "status")
    token_auth._state.clear_all()
    assert _valid(minted.token)[0] is True, "a cache miss reads the row"


MISMATCH_SENTENCE = (
    "This device's access didn't match what was granted, so it was signed out. Pair it again "
    "from Settings → Devices & Access → Devices."
)


def _signed_out_for_the_mismatch(minted) -> None:
    """A mismatch fails closed: the session is ended, remembered as such, and says why."""
    ended = ss.ended_session(minted.nonce)
    assert ended is not None and ended.reason == ss.END_SCOPE_MISMATCH
    assert minted.nonce not in ss.load_session_records()
    assert _valid(minted.token) == (False, "", "signed out"), "and it stays ended"
    notice = token_auth.signed_out_notice(minted.token)
    assert notice is not None and notice.message == MISMATCH_SENTENCE


def test_a_claim_that_differs_from_the_row_is_refused(sel_events) -> None:
    minted = _scoped(("loops",))
    _rewrite_row(minted.nonce, lambda row: row.update(scope=["status"]))
    valid, _user, reason = _valid(minted.token)
    assert (valid, reason) == (False, "device_scope_mismatch")
    assert any(e.get("operation") == "device_scope_mismatch" for e in sel_events)
    out = [e for e in sel_events if e.get("operation") == "session_signed_out"]
    assert out and out[-1]["metadata"]["reason"] == ss.END_SCOPE_MISMATCH
    _signed_out_for_the_mismatch(minted)


def test_a_claim_whose_row_lost_its_scope_is_refused() -> None:
    """An older build rewrote the store and dropped the field: never read as a full session."""
    minted = _scoped(("loops",))
    _rewrite_row(minted.nonce, lambda row: row.pop("scope"))
    assert ss.load_session_records()[minted.nonce].scope == ()
    assert _valid(minted.token) == (False, "", "device_scope_mismatch")
    _signed_out_for_the_mismatch(minted)


def test_a_scoped_row_whose_token_has_no_claim_is_refused() -> None:
    minted = _phone()
    _rewrite_row(minted.nonce, lambda row: row.update(scope=["loops"]))
    assert _valid(minted.token) == (False, "", "device_scope_mismatch")
    _signed_out_for_the_mismatch(minted)


def test_the_cached_scope_is_checked_too() -> None:
    """The cache entry carries the scope minted beside the nonce; a claim it doesn't match is
    refused without a file read deciding it."""
    minted = _scoped(("loops",))
    token_auth._state._scopes[minted.nonce] = ("status",)
    assert _valid(minted.token) == (False, "", "device_scope_mismatch")
    _signed_out_for_the_mismatch(minted)


def test_a_row_with_a_scope_outside_the_vocabulary_is_not_a_session() -> None:
    minted = _scoped(("loops",))
    _rewrite_row(minted.nonce, lambda row: row.update(scope=["loops", "admin"]))
    assert minted.nonce not in ss.load_session_records()
    assert _valid(minted.token)[0] is False


def test_a_scope_on_a_row_another_door_wrote_is_not_a_session() -> None:
    owner = sign_in()
    _rewrite_row(owner.nonce, lambda row: row.update(scope=["loops"]))
    assert owner.nonce not in ss.load_session_records()


def test_an_unscoped_session_is_unchanged() -> None:
    minted = _phone()
    assert "scope" not in _claims(minted.token)
    assert minted.token == _resigned(minted.token, token_auth._secret())
    assert "scope" not in json.loads(ss.sessions_path().read_text())["sessions"][minted.nonce]
    assert ss.load_session_records()[minted.nonce].scope == ()
    assert scoped_sessions.token_scope(minted.token) == ()


# ── The pool ────────────────────────────────────────────────────────────


def test_automation_clients_have_a_pool_of_four_of_their_own() -> None:
    phone = _phone()
    clients = [_scoped(name=f"runner {i}") for i in range(5)]
    records = ss.load_session_records()
    assert ss.pool_of(records[clients[-1].nonce]) == "automation"
    assert ss.POOL_CAPS["automation"] == 4
    assert clients[0].nonce not in records, "the fifth client signs out the idlest"
    assert all(c.nonce in records for c in clients[1:])
    assert phone.nonce in records, "a client never signs a phone out"
    assert ss.ended_session(clients[0].nonce).reason == ss.END_LIMIT


# ── The idle end (R3-6) ─────────────────────────────────────────────────


def _age(nonce: str, secs: float) -> None:
    def older(row):
        row["device"]["minted_at"] = row["device"].get("minted_at", time.time()) - secs
        row["device"]["last_seen"] = time.time() - secs

    _rewrite_row(nonce, older)


def test_a_client_unused_for_fourteen_days_is_signed_out_on_its_next_request(sel_events) -> None:
    minted = _scoped()
    _age(minted.nonce, 15 * DAY)
    assert _valid(minted.token)[0] is False
    ended = ss.ended_session(minted.nonce)
    assert ended is not None and ended.reason == ss.END_IDLE
    notice = token_auth.signed_out_notice(minted.token)
    assert notice is not None
    assert notice.message == (
        "This automation client was signed out after 14 days unused. Pair it again from "
        "Settings → Devices & Access → Devices."
    )
    rows = [e for e in sel_events if e.get("operation") == "session_signed_out"]
    assert rows and rows[-1]["metadata"]["reason"] == ss.END_IDLE


def test_the_request_that_ends_an_idle_client_is_refused_as_signed_out() -> None:
    """The first refusal after the idle end gives a sign-out's reason, as every later one does:
    the auth middleware and its log read it like any other sign-out, not as the idle check's own
    word."""
    minted = _scoped()
    _age(minted.nonce, 15 * DAY)
    assert _valid(minted.token) == (False, "", "signed out")
    assert _valid(minted.token)[0] is False


def test_the_idle_end_reads_the_cache_too(monkeypatch) -> None:
    minted = _scoped()
    later = time.time() + 15 * DAY
    monkeypatch.setattr(time, "time", lambda: later)
    assert _valid(minted.token)[0] is False
    assert ss.ended_session(minted.nonce).reason == ss.END_IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["row", "cache"])
async def test_the_first_request_after_the_idle_end_is_refused_and_never_served(
    where, monkeypatch
) -> None:
    """The idle check runs before the request is authorized: the request that finds the client
    idle ends it and is refused, and no route ever answers it first."""
    served: list[str] = []

    async def route(request: web.Request) -> web.Response:
        served.append(request.path)
        return web.json_response({"ok": True})

    app = guarded_app(lambda a: a.router.add_get("/api/served", route))
    minted = _scoped()
    if where == "row":
        _age(minted.nonce, 15 * DAY)
    else:
        later = time.time() + 15 * DAY
        monkeypatch.setattr(time, "time", lambda: later)
    headers = {"Cookie": f"{COOKIE}={minted.token}", "X-PersonalClaw-Pair": minted.pair}
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.get("/api/served", headers=headers)
        assert resp.status in (401, 403)
        again = await client.get("/api/served", headers=headers)
        assert again.status in (401, 403)
    assert served == [], "a route answered an idle client"
    assert ss.ended_session(minted.nonce).reason == ss.END_IDLE


def test_a_client_used_within_fourteen_days_stays_signed_in() -> None:
    minted = _scoped()
    _age(minted.nonce, 13 * DAY)
    assert _valid(minted.token)[0] is True


def test_the_sweep_ends_idle_clients_through_a_sign_out_and_leaves_a_phone() -> None:
    client, phone = _scoped(), _phone()
    _age(client.nonce, 15 * DAY)
    _age(phone.nonce, 15 * DAY)
    assert scoped_sessions.end_idle_sessions() == 1
    records = ss.load_session_records()
    assert client.nonce not in records and phone.nonce in records
    assert ss.ended_session(client.nonce).reason == ss.END_IDLE


@pytest.mark.asyncio
async def test_the_devices_list_does_not_show_an_idle_client() -> None:
    client = _scoped()
    _age(client.nonce, 15 * DAY)
    owner = sign_in()
    app = guarded_app(devices_h.register_device_routes)
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as c:
        resp = await c.get("/api/devices", headers=owner.headers)
        assert resp.status == 200
        rows = (await resp.json())["devices"]
    assert [r["kind"] for r in rows] == ["browser"]
    assert ss.ended_session(client.nonce).reason == ss.END_IDLE


def test_expiry_and_a_revoke_end_it(monkeypatch) -> None:
    short = _scoped(ttl=DAY)
    revoked = _scoped(name="other")
    token_auth.sign_out([revoked.nonce], ss.END_SIGNED_OUT_ELSEWHERE, actor="owner")
    assert _valid(revoked.token)[0] is False
    later = time.time() + DAY + 60
    monkeypatch.setattr(time, "time", lambda: later)
    assert _valid(short.token)[0] is False


# ── It can't widen itself (F8) ──────────────────────────────────────────


class _Request(dict):
    """What ``renew_sign_in`` reads of a request: its user, headers and address."""

    headers: dict[str, str] = {}
    remote = "127.0.0.1"


def test_renewing_a_scoped_sign_in_is_refused() -> None:
    """The path behind ``/api/auth/confirm`` would mint the session again with no scope."""
    minted = _scoped()
    before = set(ss.load_session_records())
    with pytest.raises(ValueError):
        token_auth.renew_sign_in(
            _Request(user=devices_h.PAIRED_DEVICE_USER), minted.nonce, DAY, owner="owner"
        )
    assert set(ss.load_session_records()) == before, "nothing minted, nothing ended"
    assert ss.load_session_records()[minted.nonce].scope == ("loops",)


async def _echo(request: web.Request) -> web.Response:
    return web.json_response({"scope": list(request.get("device_scope") or ())})


@pytest.mark.asyncio
async def test_the_middleware_names_the_request_s_scope() -> None:
    app = guarded_app(lambda a: a.router.add_get("/api/echo", _echo))
    minted, phone = _scoped(("status", "loops")), _phone()
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        for session, scope in ((minted, ["loops", "status"]), (phone, [])):
            headers = {"Cookie": f"{COOKIE}={session.token}", "X-PersonalClaw-Pair": session.pair}
            resp = await client.get("/api/echo", headers=headers)
            assert resp.status == 200
            assert (await resp.json())["scope"] == scope


@pytest.mark.asyncio
async def test_owner_presence_never_counts_a_scoped_session(sel_events) -> None:
    """A scoped session minted a second ago is no recent owner sign-in: the uniform 403."""
    app = guarded_app(devices_h.register_device_routes)
    minted = _scoped()
    headers = {
        "Cookie": f"{COOKIE}={minted.token}",
        "X-PersonalClaw-Pair": minted.pair,
        "Origin": ORIGIN,
    }
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.post("/api/devices/pair/start", json={}, headers=headers)
        assert resp.status == 403
        body = await resp.json()
    assert body["error"]["code"] == "device_scope_refused"
    message = body["error"]["message"]
    assert "loops" in message, "it names the scope"
    assert "/api/" not in message and "devices" not in message.lower(), "never the route"
    assert not pairing.codes_path().exists()


@pytest.mark.asyncio
async def test_a_scoped_session_shows_no_presence_proof() -> None:
    """``presence_proof``, which ``/api/auth/confirm`` reads too, is empty for a client that
    paired a second ago, while the owner's own new sign-in is a recent one."""
    from personalclaw.dashboard.owner_presence import PROOF_RECENT_SIGN_IN, presence_proof

    async def proof(request: web.Request) -> web.Response:
        return web.json_response({"proof": presence_proof(request)})

    app = guarded_app(lambda a: a.router.add_get("/api/proof", proof))
    minted, owner = _scoped(), sign_in()
    scoped_headers = {"Cookie": f"{COOKIE}={minted.token}", "X-PersonalClaw-Pair": minted.pair}
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        assert await (await client.get("/api/proof", headers=scoped_headers)).json() == {
            "proof": ""
        }
        owned = await (await client.get("/api/proof", headers=owner.headers)).json()
    assert owned == {"proof": PROOF_RECENT_SIGN_IN}


@pytest.mark.asyncio
async def test_under_the_local_network_bypass_a_scoped_session_is_still_not_the_owner(
    monkeypatch,
) -> None:
    """The bypass names the session without the sign-in check's scope: the row says it."""
    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")
    app = guarded_app(devices_h.register_device_routes)
    minted = _scoped()
    headers = {
        "Cookie": f"{COOKIE}={minted.token}",
        "X-PersonalClaw-Pair": minted.pair,
        "Origin": ORIGIN,
    }
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        resp = await client.post("/api/devices/pair/start", json={}, headers=headers)
        assert resp.status == 403
        assert (await resp.json())["error"]["code"] == "device_scope_refused"
    assert not pairing.codes_path().exists()


@pytest.mark.asyncio
async def test_under_the_local_network_bypass_a_scoped_session_shows_no_presence_proof(
    monkeypatch,
) -> None:
    from personalclaw.dashboard.owner_presence import presence_proof

    async def proof(request: web.Request) -> web.Response:
        return web.json_response({"proof": presence_proof(request)})

    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")
    app = guarded_app(lambda a: a.router.add_get("/api/proof", proof))
    minted = _scoped()
    headers = {"Cookie": f"{COOKIE}={minted.token}", "X-PersonalClaw-Pair": minted.pair}
    async with TestClient(TestServer(app), cookie_jar=DummyCookieJar()) as client:
        got = await (await client.get("/api/proof", headers=headers)).json()
    assert got == {"proof": ""}


# ── The codes (F15) ─────────────────────────────────────────────────────


def test_a_scoped_code_lives_in_its_own_file() -> None:
    """An older build reads only ``pair_codes.json``, so a live scoped code can't be redeemed
    there as a full session."""
    code, _exp = pairing.issue_code(label="runner", scope=("loops",), lifetime_secs=7 * DAY)
    assert pairing.scoped_codes_path().name == "pair_codes.scoped.json"
    stored = json.loads(pairing.scoped_codes_path().read_text())
    assert [rec["scope"] for rec in stored.values()] == [["loops"]]
    assert not pairing.codes_path().exists() or json.loads(pairing.codes_path().read_text()) == {}
    outcome = pairing.redeem_code(code)
    assert outcome.ok
    assert (outcome.scope, outcome.lifetime_secs, outcome.label) == (("loops",), 7 * DAY, "runner")
    assert not pairing.redeem_code(code).ok, "single use"


def test_an_unscoped_code_redeems_with_no_scope() -> None:
    code, _exp = pairing.issue_code(label="phone")
    outcome = pairing.redeem_code(code)
    assert outcome.ok and outcome.scope == () and outcome.lifetime_secs == 0


def test_the_outstanding_cap_counts_both_files() -> None:
    for _ in range(3):
        pairing.issue_code()
    for _ in range(3):
        pairing.issue_code(scope=("loops",), lifetime_secs=DAY)
    plain = json.loads(pairing.codes_path().read_text())
    scoped = json.loads(pairing.scoped_codes_path().read_text())
    assert len(plain) + len(scoped) == pairing._MAX_ACTIVE


def test_a_scoped_code_with_a_scope_outside_the_vocabulary_fails_closed() -> None:
    code, _exp = pairing.issue_code(scope=("loops",), lifetime_secs=DAY)
    path = pairing.scoped_codes_path()
    stored = json.loads(path.read_text())
    for rec in stored.values():
        rec["scope"] = ["admin"]
    path.write_text(json.dumps(stored))
    assert pairing.redeem_code(code).result == pairing.RESULT_INVALID


def test_a_scoped_code_with_a_lifetime_out_of_range_fails_closed() -> None:
    code, _exp = pairing.issue_code(scope=("loops",), lifetime_secs=DAY)
    path = pairing.scoped_codes_path()
    stored = json.loads(path.read_text())
    for rec in stored.values():
        rec["lifetime_secs"] = 91 * DAY
    path.write_text(json.dumps(stored))
    assert pairing.redeem_code(code).result == pairing.RESULT_INVALID


def test_a_scope_in_the_unscoped_file_fails_closed() -> None:
    code, _exp = pairing.issue_code()
    path = pairing.codes_path()
    stored = json.loads(path.read_text())
    for rec in stored.values():
        rec["scope"] = ["loops"]
    path.write_text(json.dumps(stored))
    assert pairing.redeem_code(code).result == pairing.RESULT_INVALID


@pytest.mark.parametrize(
    ("scope", "lifetime"),
    [((), DAY), (("admin",), DAY), (("loops",), 0), (("loops",), 91 * DAY), (("loops",), -DAY)],
)
def test_issuing_a_scoped_code_checks_its_scope_and_lifetime(scope, lifetime) -> None:
    """A lifetime belongs to a scoped code only, and both are checked where the code is made."""
    with pytest.raises(ValueError):
        pairing.issue_code(scope=scope, lifetime_secs=lifetime)
    assert not pairing.scoped_codes_path().exists()


# ── pair/start ──────────────────────────────────────────────────────────


def _owner_app() -> web.Application:
    return guarded_app(devices_h.register_device_routes)


def _owner_headers() -> dict[str, str]:
    return sign_in().headers


async def _start(client, body: dict[str, Any], headers: dict[str, str] | None = None):
    return await client.post(
        "/api/devices/pair/start", json=body, headers=headers or _owner_headers()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [["admin"], ["loops", "admin"], "loops", [], ["approvals"], ["loop_create"], ["loop_land"]],
)
async def test_pair_start_refuses_a_scope_it_can_t_grant(scope) -> None:
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        resp = await _start(client, {"scope": scope, "days": 30})
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "device_pair_scope_invalid"
    assert not pairing.scoped_codes_path().exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, 91, -1, "30", 1.5, True, None])
async def test_pair_start_refuses_a_lifetime_out_of_range(days) -> None:
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        resp = await _start(client, {"scope": ["loops"], "days": days})
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "device_pair_lifetime_invalid"


@pytest.mark.asyncio
async def test_pair_start_refuses_days_without_a_scope() -> None:
    """A lifetime is a scoped pairing's; sent alone it is refused, never dropped."""
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        resp = await _start(client, {"days": 30, "label": "phone"})
        assert resp.status == 400
        error = (await resp.json())["error"]
    assert error["code"] == "device_pair_lifetime_invalid"
    assert error["message"] == "days applies only to a scoped pairing."
    assert not pairing.codes_path().exists() and not pairing.scoped_codes_path().exists()


@pytest.mark.asyncio
async def test_pair_start_makes_a_scoped_code_for_thirty_days_by_default(sel_events) -> None:
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        resp = await _start(client, {"scope": ["status", "loops"], "label": "Runner"})
        assert resp.status == 200
        data = await resp.json()
    assert data["scope"] == ["loops", "status"] and data["lifetime_days"] == 30
    stored = list(json.loads(pairing.scoped_codes_path().read_text()).values())
    assert stored[0]["lifetime_secs"] == 30 * DAY
    row = [e for e in sel_events if e.get("operation") == "device_pair_started"][-1]
    assert "scope=loops,status" in row["resources"] and "lifetime_days=30" in row["resources"]
    assert data["code"].replace("-", "") not in json.dumps(sel_events)


@pytest.mark.asyncio
async def test_pair_start_refuses_a_scope_when_auth_is_off() -> None:
    """With auth off nothing would hold a scoped session to its scope (F10)."""
    app = without_sign_in(web.Application())
    app["port"] = PORT
    app["allowed_origins"] = {ORIGIN}
    devices_h.register_device_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/devices/pair/start", json={"scope": ["loops"]}, headers={"Origin": ORIGIN}
        )
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "device_pair_scope_unbindable"
        plain = await client.post("/api/devices/pair/start", json={}, headers={"Origin": ORIGIN})
        assert plain.status == 200, "an unscoped pairing is unchanged there"


@pytest.mark.asyncio
async def test_pair_start_refuses_a_scope_under_the_local_network_bypass(monkeypatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        resp = await _start(client, {"scope": ["loops"]})
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "device_pair_scope_unbindable"


@pytest.mark.asyncio
async def test_the_local_secret_makes_a_scoped_code() -> None:
    """The CLI's path: owner presence by the local secret, from this computer."""
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        headers = {**_owner_headers(), "X-Local-Secret": LOCAL_SECRET}
        resp = await _start(client, {"scope": ["loops"], "days": 7}, headers)
        assert resp.status == 200
        assert (await resp.json())["lifetime_days"] == 7


# ── pair/complete ───────────────────────────────────────────────────────


async def _scoped_code(client, **body) -> str:
    resp = await _start(client, {"scope": ["loops"], "days": 7, **body})
    assert resp.status == 200
    return (await resp.json())["code"]


@pytest.mark.asyncio
async def test_pair_complete_mints_the_code_s_scope_lifetime_and_kind(sel_events) -> None:
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        code = await _scoped_code(client, label="Runner")
        resp = await client.post(
            "/api/devices/pair/complete",
            json={
                "code": code,
                "kind": "mobile",
                "scope": ["loops", "status", "approvals", "loop_land"],
            },
            headers={"Origin": ORIGIN},
        )
        assert resp.status == 200
        body = await resp.json()
        token = resp.cookies[COOKIE].value
    assert body["kind"] == "automation"
    assert body["expires_in"] == 7 * DAY
    assert body["landing"] == "/#/loops"
    assert scoped_sessions.token_scope(token) == ("loops",), "the body's scope is ignored"
    record = ss.load_session_records()[token_auth.token_nonce(token)]
    assert record.scope == ("loops",)
    assert record.device.kind == "automation"
    assert record.device.name == "Runner"
    assert record.expiry - record.device.minted_at == pytest.approx(7 * DAY, abs=5)
    row = [e for e in sel_events if e.get("operation") == "device_paired"][-1]
    assert "kind=automation" in row["resources"] and "scope=loops" in row["resources"]


@pytest.mark.parametrize(
    "scope, label, declared, named",
    [
        (["loops"], "Runner", "Nightly runner", "Runner"),
        (["loops"], "", "Nightly runner", "Nightly runner"),
        ([], "phone", "Pixel", "Pixel"),
    ],
    ids=["a-client-is-the-owners-label", "a-client-with-none-is-its-own", "a-phone-names-itself"],
)
@pytest.mark.asyncio
async def test_an_automation_client_is_named_by_the_owners_label(
    scope, label, declared, named
) -> None:
    """A client's name is who the owner reads as acting, so the label she gave its code beats the
    name it declares: client B can't redeem as A's "Nightly runner". A phone names itself."""
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        resp = await _start(
            client, {**({"scope": scope, "days": 7} if scope else {}), "label": label}
        )
        code = (await resp.json())["code"]
        resp = await client.post(
            "/api/devices/pair/complete",
            json={"code": code, "device_name": declared},
            headers={"Origin": ORIGIN},
        )
        assert resp.status == 200
        token = resp.cookies[COOKIE].value
    assert ss.load_session_records()[token_auth.token_nonce(token)].device.name == named


@pytest.mark.asyncio
async def test_an_unscoped_redemption_can_t_call_itself_an_automation_client() -> None:
    async with TestClient(TestServer(_owner_app()), cookie_jar=DummyCookieJar()) as client:
        resp = await _start(client, {})
        code = (await resp.json())["code"]
        resp = await client.post(
            "/api/devices/pair/complete",
            json={"code": code, "kind": "automation", "scope": ["loops"]},
            headers={"Origin": ORIGIN, "User-Agent": "Unrecognised/1.0"},
        )
        assert resp.status == 200
        body = await resp.json()
        token = resp.cookies[COOKIE].value
    assert body["kind"] == "unknown"
    assert scoped_sessions.token_scope(token) == ()
    record = ss.load_session_records()[token_auth.token_nonce(token)]
    assert (record.scope, record.device.kind) == ((), "unknown")
    assert body["expires_in"] == token_auth.browser_session_ttl()


def test_a_client_declared_kind_is_never_automation() -> None:
    assert ss.sanitize_device_kind("automation") == "unknown"
    assert ss.sanitize_device_kind("Automation ") == "unknown"
