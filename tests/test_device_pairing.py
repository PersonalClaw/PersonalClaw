"""Device pairing + the Devices registry (COMPANION-APPS C2 / CA-1).

The atom's four clauses are asserted SEPARATELY, because they fail separately and three of the
four fail silently:

1. pair/start → complete writes a durable row carrying ``device`` and ``issuer``, minted through
   the ordinary token path (no second credential type);
2. that row still authenticates after the process forgets everything it knew;
3. reusing (or outrunning) a code is refused with the two distinct typed codes;
4. after a revoke the device's NEXT request is refused, and stays refused across a restart —
   the classic bug here is a revoke that un-revokes on reboot, which is worse than no revoke
   because the owner was told it worked.

Plus the audit floor: every route emits a SEL event INCLUDING its denials, and no event, log or
response ever carries the code or the nonce.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.auth import pairing
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import devices as devices_h

PORT = 10000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every store this surface touches points at *tmp_path*, never the real home."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(pairing, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()
    yield tmp_path
    token_auth.revoke_all_sessions()
    auth_h.reset_lockouts()


@pytest.fixture()
def sel_events(monkeypatch) -> list[dict[str, Any]]:
    """Capture SEL calls through the REAL ``_sel()`` indirection the routes use."""
    import personalclaw.sel as sel_mod

    events: list[dict[str, Any]] = []
    recorder = MagicMock()
    recorder.log_api_access.side_effect = lambda **kw: events.append(kw)
    monkeypatch.setattr(sel_mod, "sel", lambda: recorder)
    return events


def _app() -> web.Application:
    app = web.Application()
    app["port"] = PORT
    app["allowed_origins"] = {f"http://localhost:{PORT}"}
    devices_h.register_device_routes(app)
    return app


def _expire_stored_codes(home) -> None:
    """Age every outstanding code without waiting 300s, leaving the record in place."""
    path = pairing.codes_path()
    codes = json.loads(path.read_text(encoding="utf-8"))
    for rec in codes.values():
        rec["expires_at"] = time.time() - 1
    path.write_text(json.dumps(codes), encoding="utf-8")


# ── The pairing code ────────────────────────────────────────────────────


def test_a_pairing_code_round_trips_exactly_once(_isolated) -> None:
    code, _exp = pairing.issue_code()
    assert pairing.redeem_code(code).result == pairing.RESULT_OK
    assert pairing.redeem_code(code).result == pairing.RESULT_INVALID, "must be single-use"


def test_an_expired_code_is_told_apart_from_an_unknown_one(_isolated) -> None:
    """Two rejections, because they need two different sentences in the UI."""
    code, _exp = pairing.issue_code()
    _expire_stored_codes(_isolated)
    assert pairing.redeem_code(code).result == pairing.RESULT_EXPIRED
    assert pairing.redeem_code("ABCDEFGH").result == pairing.RESULT_INVALID


def test_an_expired_code_is_not_answered_twice(_isolated) -> None:
    """Reporting `expired` forever would leave a permanent oracle for one guess."""
    code, _exp = pairing.issue_code()
    _expire_stored_codes(_isolated)
    assert pairing.redeem_code(code).result == pairing.RESULT_EXPIRED
    assert pairing.redeem_code(code).result == pairing.RESULT_INVALID


def test_the_code_is_hashed_at_rest(_isolated) -> None:
    code, _exp = pairing.issue_code()
    raw = pairing.codes_path().read_text(encoding="utf-8")
    assert code not in raw, "reading the store must not yield a redeemable credential"
    assert pairing.format_code(code) not in raw


def test_the_code_store_is_owner_only(_isolated) -> None:
    pairing.issue_code()
    assert oct(pairing.codes_path().stat().st_mode)[-3:] == "600"


def test_a_dash_formatted_code_redeems(_isolated) -> None:
    """It is read off one screen and typed into another, dash included."""
    code, _exp = pairing.issue_code()
    assert pairing.redeem_code(pairing.format_code(code)).result == pairing.RESULT_OK


@pytest.mark.parametrize("bad", ["", "SHORT", "TOOLONGCODE", "!!!!!!!!", "IIIIOOOO"])
def test_a_malformed_code_is_invalid(_isolated, bad) -> None:
    assert pairing.redeem_code(bad).result == pairing.RESULT_INVALID


def test_outstanding_codes_are_capped(_isolated) -> None:
    """Scarcity is a rate limit: nobody can ask for thousands and widen the guess space."""
    for _ in range(pairing._MAX_ACTIVE + 4):
        pairing.issue_code()
    # Read the store, not a convenience counter — the file is what bounds the guess space.
    assert len(pairing._prune(pairing._load())) == pairing._MAX_ACTIVE


def test_an_unreadable_store_fails_closed(_isolated) -> None:
    code, _exp = pairing.issue_code()
    pairing.codes_path().write_text("{not json", encoding="utf-8")
    assert pairing.redeem_code(code).result == pairing.RESULT_INVALID


def test_a_consumption_that_cannot_persist_refuses(_isolated, monkeypatch) -> None:
    """A code that stays redeemable is worse than a pairing the user has to retry."""
    code, _exp = pairing.issue_code()
    monkeypatch.setattr(
        pairing, "atomic_write", lambda *a, **k: (_ for _ in ()).throw(OSError("full"))
    )
    assert pairing.redeem_code(code).result == pairing.RESULT_INVALID


def test_the_label_survives_to_the_redemption(_isolated) -> None:
    code, _exp = pairing.issue_code(label="Kitchen tablet")
    assert pairing.redeem_code(code).label == "Kitchen tablet"


def test_pairing_codes_live_apart_from_enrollment_codes(_isolated) -> None:
    """Two surfaces, two stores: an enrollment code must not open the pairing door."""
    from personalclaw.auth import enrollment

    assert pairing.codes_path() != enrollment.codes_path()


# ── The widened session row ─────────────────────────────────────────────


def _device(**kw) -> ss.DeviceInfo:
    base = {"id": "dev-1", "name": "Pixel", "kind": "mobile", "minted_at": time.time()}
    base.update(kw)
    return ss.DeviceInfo(**base)  # type: ignore[arg-type]


def test_a_device_row_round_trips(_isolated) -> None:
    ss.remember_session("n1", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device())
    record = ss.load_session_records()["n1"]
    assert record.issuer == ss.ISSUER_PAIR
    assert record.device is not None
    assert (record.device.id, record.device.kind) == ("dev-1", "mobile")


def test_the_expiry_projection_matches_the_records(_isolated) -> None:
    """`load_sessions` is a narrower VIEW of one shape, not a second reader."""
    exp = time.time() + 3600
    ss.remember_session("n1", exp, issuer=ss.ISSUER_PAIR, device=_device())
    ss.remember_session("n2", exp)
    assert ss.load_sessions() == {n: r.expiry for n, r in ss.load_session_records().items()}


def test_an_old_shape_row_is_discarded(_isolated) -> None:
    """A bare-float row has no issuer, so the registry could neither describe nor revoke it."""
    ss.sessions_path().write_text(
        json.dumps({"sessions": {"legacy": time.time() + 3600}}), encoding="utf-8"
    )
    assert ss.load_session_records() == {}
    assert ss.load_sessions() == {}, "an un-attributable session must not authenticate"


def test_attach_refuses_an_absent_nonce(_isolated) -> None:
    """Annotating must never resurrect a session the store already retired."""
    assert ss.attach_device("gone", _device()) is False
    assert ss.load_session_records() == {}


def test_attach_preserves_the_original_expiry(_isolated) -> None:
    exp = time.time() + 1234
    ss.remember_session("n1", exp)
    assert ss.attach_device("n1", _device()) is True
    assert ss.load_session_records()["n1"].expiry == exp


def test_nonces_for_device_finds_every_session(_isolated) -> None:
    """Re-pairing before the old session expires is legitimate; a partial revoke is not."""
    ss.remember_session("n1", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device())
    ss.remember_session("n2", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device())
    ss.remember_session("n3", time.time() + 3600)
    assert sorted(ss.nonces_for_device("dev-1")) == ["n1", "n2"]
    assert ss.nonces_for_device("nope") == []
    assert ss.nonces_for_device("") == []


def test_a_device_kind_outside_the_vocabulary_becomes_unknown(_isolated) -> None:
    assert ss.sanitize_device_kind("<img src=x>") == "unknown"
    assert ss.sanitize_device_kind("MOBILE") == "mobile"
    assert ss.sanitize_device_kind("") == "unknown"


def test_a_device_name_is_bounded_and_single_line(_isolated) -> None:
    assert ss.sanitize_device_name("a" * 500) == "a" * ss.MAX_DEVICE_NAME
    assert ss.sanitize_device_name("two\nlines\ttabbed") == "twolinestabbed"


def test_the_store_stays_owner_only_with_a_device_row(_isolated) -> None:
    ss.remember_session("n1", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device())
    assert oct(ss.sessions_path().stat().st_mode)[-3:] == "600"


def test_only_device_rows_are_in_the_registry(_isolated) -> None:
    ss.remember_session("owner", time.time() + 3600)
    ss.remember_session("phone", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device())
    assert set(ss.device_sessions()) == {"phone"}


def test_stats_count_devices_without_naming_them(_isolated) -> None:
    ss.remember_session("secret-nonce", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device())
    stats = ss.session_stats()
    assert stats["devices"] == 1
    assert "secret-nonce" not in json.dumps(stats)


# ── The last-seen writer (CA-2) ─────────────────────────────────────────
#
# C1 deliberately shipped `DeviceInfo` WITHOUT `last_seen`, on the grounds that its only honest
# writer is the authorize path and that a throttled write there is a cost decision, not a field
# declaration. These assert the three properties that made it payable: it is throttled, it never
# gates the verdict, and an unstamped device reads as "never" rather than as freshly paired.


def _stored_device(nonce: str) -> ss.DeviceInfo:
    """The device on *nonce*'s row, read back off disk."""
    device = ss.load_session_records()[nonce].device
    assert device is not None
    return device


def _count_saves(monkeypatch) -> list[int]:
    """Count real ``save_session_records`` calls. The WRITE is the cost being throttled."""
    calls = [0]
    original = ss.save_session_records

    def counting(records):
        calls[0] += 1
        original(records)

    monkeypatch.setattr(ss, "save_session_records", counting)
    return calls


def test_last_seen_round_trips_through_the_store(_isolated) -> None:
    seen = time.time() - 30
    ss.remember_session(
        "n1", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device(last_seen=seen)
    )
    assert _stored_device("n1").last_seen == pytest.approx(seen)
    assert json.loads(ss.sessions_path().read_text())["sessions"]["n1"]["device"]["last_seen"] == (
        pytest.approx(seen)
    ), "the field must be on disk, not only on the dataclass"


def test_a_row_with_no_last_seen_reads_as_never_not_as_paired_at(_isolated) -> None:
    """THE distinction the C1 note is about: a device that never returned must not read fresh."""
    minted = time.time() - 86400
    ss.sessions_path().write_text(
        json.dumps(
            {
                "sessions": {
                    "n1": {
                        "exp": time.time() + 3600,
                        "issuer": ss.ISSUER_PAIR,
                        "device": {
                            "id": "dev-1",
                            "name": "Pixel",
                            "kind": "mobile",
                            "minted_at": minted,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    device = ss.load_session_records()["n1"].device
    assert device is not None
    assert device.minted_at == pytest.approx(minted)
    assert device.last_seen == 0.0, "an absent stamp is 'never', never a backfill of minted_at"


def test_a_stale_device_is_stamped_once_and_then_throttled(_isolated, monkeypatch) -> None:
    ss.remember_session(
        "n1", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device(last_seen=0.0)
    )
    calls = _count_saves(monkeypatch)
    assert ss.touch_device_last_seen("n1") is True
    assert calls[0] == 1
    assert ss.touch_device_last_seen("n1") is False, "still fresh: inside the throttle window"
    assert calls[0] == 1, "the second touch must not rewrite the store"


def test_a_stamp_older_than_the_threshold_is_written_again(_isolated, monkeypatch) -> None:
    """The throttle must not be a one-shot: an idle device still updates a minute later."""
    ss.remember_session(
        "n1", time.time() + 3600, issuer=ss.ISSUER_PAIR, device=_device(last_seen=0.0)
    )
    assert ss.touch_device_last_seen("n1") is True
    calls = _count_saves(monkeypatch)
    later = time.time() + ss.LAST_SEEN_THROTTLE_SECS + 1
    assert ss.touch_device_last_seen("n1", now=later) is True
    assert calls[0] == 1
    assert _stored_device("n1").last_seen == pytest.approx(later)


def test_a_non_device_session_is_never_stamped(_isolated, monkeypatch) -> None:
    """A plain owner-token row has no device, so there is nothing to be 'last seen'."""
    ss.remember_session("owner", time.time() + 3600)
    calls = _count_saves(monkeypatch)
    assert ss.touch_device_last_seen("owner") is False
    assert ss.touch_device_last_seen("never-stored") is False
    assert calls[0] == 0, "a no-op must be a no-op on disk too"


def test_two_rapid_authorizations_write_the_store_once(_isolated, monkeypatch) -> None:
    """The property that made the field payable, asserted at the AUTHORIZE path."""
    token = token_auth.generate_token("owner", ttl_seconds=3600)
    nonce = next(iter(ss.load_session_records()))
    ss.attach_device(nonce, _device(last_seen=0.0))
    calls = _count_saves(monkeypatch)

    assert token_auth.validate_token(token, use_session_exp=True)[0] is True
    assert token_auth.validate_token(token, use_session_exp=True)[0] is True

    assert calls[0] == 1, "two authorizations inside the window must cost ONE store write"
    assert _stored_device(nonce).last_seen > 0.0


def test_an_authorization_still_succeeds_when_the_stamp_raises(_isolated, monkeypatch) -> None:
    """Best-effort by contract: a store that cannot be stamped must not deny a valid session."""
    token = token_auth.generate_token("owner", ttl_seconds=3600)
    nonce = next(iter(ss.load_session_records()))
    ss.attach_device(nonce, _device(last_seen=0.0))

    def exploding(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(ss, "touch_device_last_seen", exploding)
    valid, _user, reason = token_auth.validate_token(token, use_session_exp=True)
    assert valid is True, reason

    token_auth._state.clear_all()  # force the adopt-from-store path too
    assert token_auth.validate_token(token, use_session_exp=True)[0] is True


def _age_stored_last_seen(nonce: str, age_secs: float) -> None:
    """Push one row's stamp *age_secs* into the past, leaving in-memory state alone."""
    raw = json.loads(ss.sessions_path().read_text(encoding="utf-8"))
    raw["sessions"][nonce]["device"]["last_seen"] = time.time() - age_secs
    ss.sessions_path().write_text(json.dumps(raw), encoding="utf-8")


def test_the_in_memory_throttle_suppresses_even_the_read(_isolated, monkeypatch) -> None:
    """The two layers are separable: the map suppresses a write the store WOULD have allowed."""
    token = token_auth.generate_token("owner", ttl_seconds=3600)
    nonce = next(iter(ss.load_session_records()))
    ss.attach_device(nonce, _device(last_seen=0.0))
    assert token_auth.validate_token(token, use_session_exp=True)[0] is True
    _age_stored_last_seen(nonce, ss.LAST_SEEN_THROTTLE_SECS + 10)

    calls = _count_saves(monkeypatch)
    assert token_auth.validate_token(token, use_session_exp=True)[0] is True
    assert calls[0] == 0, "the in-memory attempt map must short-circuit before the file read"


def test_a_restart_forgets_the_throttle(_isolated, monkeypatch) -> None:
    """In-memory state: after a restart the first authorized request stamps again.

    Paired with the test above — same aged-on-disk setup, opposite expectation — because that
    is the only way to observe the map being cleared rather than merely present.
    """
    token = token_auth.generate_token("owner", ttl_seconds=3600)
    nonce = next(iter(ss.load_session_records()))
    ss.attach_device(nonce, _device(last_seen=0.0))
    assert token_auth.validate_token(token, use_session_exp=True)[0] is True
    _age_stored_last_seen(nonce, ss.LAST_SEEN_THROTTLE_SECS + 10)

    token_auth._state.clear_all()  # the in-memory half of a restart
    calls = _count_saves(monkeypatch)
    assert token_auth.validate_token(token, use_session_exp=True)[0] is True
    assert calls[0] == 1, "a restart must not inherit the previous process's throttle"


# ── The routes ──────────────────────────────────────────────────────────


async def _start(client) -> dict[str, Any]:
    resp = await client.post("/api/devices/pair/start", json={})
    assert resp.status == 200
    return await resp.json()


async def _complete(client, code: str, **body) -> Any:
    return await client.post("/api/devices/pair/complete", json={"code": code, **body})


@pytest.mark.asyncio
async def test_pair_start_returns_a_code_and_a_qr_url(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
    assert len(data["code"]) == pairing._CODE_LEN + 1, "grouped XXXX-XXXX for reading"
    assert data["code"] in data["pairing_url"], "the QR payload must be actionable on its own"
    assert data["expires_in"] == pairing.PAIR_CODE_TTL_SECS
    assert data["expires_at"] > time.time()


@pytest.mark.asyncio
async def test_clause_1_complete_writes_a_durable_device_row(_isolated) -> None:
    """CLAUSE 1: a sessions.json row with device+issuer, and no new token type."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        resp = await _complete(client, data["code"], device_name="Pixel 9", kind="mobile")
        assert resp.status == 200
        body = await resp.json()
        assert resp.cookies[f"pc_token_{PORT}"], "the device gets the ORDINARY session cookie"
        token = resp.cookies[f"pc_token_{PORT}"].value

    records = ss.load_session_records()
    assert len(records) == 1
    nonce, record = next(iter(records.items()))
    assert record.issuer == ss.ISSUER_PAIR
    assert record.device is not None
    assert record.device.id == body["device_id"]
    assert (record.device.name, record.device.kind) == ("Pixel 9", "mobile")
    # No new token type: the same validator the owner's browser goes through accepts it.
    assert token_auth.validate_token(token, use_session_exp=True)[0] is True
    assert nonce not in json.dumps(body), "a response must never carry the nonce"


@pytest.mark.asyncio
async def test_clause_2_the_device_session_survives_a_restart(_isolated) -> None:
    """CLAUSE 2: the row still authenticates once the process forgets everything."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        resp = await _complete(client, data["code"], device_name="Pixel", kind="mobile")
        token = resp.cookies[f"pc_token_{PORT}"].value

    token_auth._state.clear_all()  # the in-memory half of a restart
    token_auth.reset_secret_cache()
    valid, user, reason = token_auth.validate_token(token, use_session_exp=True)
    assert valid is True, reason
    assert user == devices_h.PAIRED_DEVICE_USER


@pytest.mark.asyncio
async def test_clause_3_a_reused_code_is_refused_as_invalid(_isolated) -> None:
    """CLAUSE 3a: device_pair_code_invalid on reuse."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        assert (await _complete(client, data["code"])).status == 200
        resp = await _complete(client, data["code"])
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == devices_h.ERR_CODE_INVALID
    assert len(ss.load_session_records()) == 1, "the second attempt minted nothing"


@pytest.mark.asyncio
async def test_clause_3_an_expired_code_is_refused_as_expired(_isolated) -> None:
    """CLAUSE 3b: device_pair_code_expired, a different code from a wrong one."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        _expire_stored_codes(_isolated)
        resp = await _complete(client, data["code"])
        assert resp.status == 401
        assert (await resp.json())["error"]["code"] == devices_h.ERR_CODE_EXPIRED
    assert ss.load_session_records() == {}


@pytest.mark.asyncio
async def test_clause_4_revoke_locks_the_device_out_across_a_restart(_isolated) -> None:
    """CLAUSE 4: the NEXT request is refused, live AND after the process forgets."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        resp = await _complete(client, data["code"], device_name="Pixel", kind="mobile")
        token = resp.cookies[f"pc_token_{PORT}"].value
        device_id = (await resp.json())["device_id"]
        assert token_auth.validate_token(token, use_session_exp=True)[0] is True

        revoked = await client.post(f"/api/devices/{device_id}/revoke", json={})
        assert revoked.status == 200
        assert (await revoked.json())["revoked"] == 1

    # Live: the in-memory half bit, with no restart involved.
    assert token_auth.validate_token(token, use_session_exp=True)[0] is False
    # Across a restart: the durable half bit too, so it cannot come back to life.
    token_auth._state.clear_all()
    token_auth.reset_secret_cache()
    valid, _user, reason = token_auth.validate_token(token, use_session_exp=True)
    assert valid is False, "a revoke that un-revokes on reboot is worse than no revoke"
    assert reason in ("no active sessions", "token superseded", "session expired")
    assert ss.device_sessions() == {}


@pytest.mark.asyncio
async def test_the_registry_lists_the_device_and_never_a_nonce(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        await _complete(client, data["code"], device_name="Pixel", kind="mobile")
        nonce = next(iter(ss.load_session_records()))

        resp = await client.get("/api/devices")
        assert resp.status == 200
        payload = await resp.json()

    assert len(payload["devices"]) == 1
    row = payload["devices"][0]
    assert {"id", "name", "kind", "minted_at", "last_seen", "issuer", "expires_at"} <= set(row)
    assert row["issuer"] == ss.ISSUER_PAIR
    assert nonce not in json.dumps(payload), "the registry is read aloud; the nonce is a credential"


@pytest.mark.asyncio
async def test_a_freshly_paired_device_is_listed_as_never_seen(_isolated) -> None:
    """Pairing is not a sighting. The panel renders 0.0 as "never"; a backfill would lie."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        await _complete(client, data["code"], device_name="Pixel", kind="mobile")
        row = (await (await client.get("/api/devices")).json())["devices"][0]

    assert row["last_seen"] == 0.0
    assert row["minted_at"] > 0.0, "and it is NOT that the row has no timestamps at all"


@pytest.mark.asyncio
async def test_the_registry_reports_a_real_last_seen_once_stamped(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        await _complete(client, data["code"], device_name="Pixel", kind="mobile")
        nonce = next(iter(ss.load_session_records()))
        assert ss.touch_device_last_seen(nonce) is True
        row = (await (await client.get("/api/devices")).json())["devices"][0]

    assert row["last_seen"] == pytest.approx(time.time(), abs=30)


@pytest.mark.asyncio
async def test_an_owner_session_is_not_a_device(_isolated) -> None:
    """The registry must not list the owner's own browser as a paired device."""
    token_auth.generate_token("owner", ttl_seconds=3600)
    async with TestClient(TestServer(_app())) as client:
        payload = await (await client.get("/api/devices")).json()
    assert payload["devices"] == []


@pytest.mark.asyncio
async def test_revoking_an_unknown_device_is_a_404(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/devices/nope/revoke", json={})
        assert resp.status == 404
        assert (await resp.json())["error"]["code"] == devices_h.ERR_UNKNOWN_DEVICE


@pytest.mark.asyncio
async def test_revoke_drops_every_session_of_that_device(_isolated) -> None:
    """A revoke that only dropped the newest would leave the device logged in."""
    async with TestClient(TestServer(_app())) as client:
        first = await _complete(client, (await _start(client))["code"], device_name="Phone")
        device_id = (await first.json())["device_id"]
        # A second session for the SAME device id, as a re-pair before expiry produces.
        token_auth.generate_token(devices_h.PAIRED_DEVICE_USER, ttl_seconds=3600)
        extra = next(
            n for n in ss.load_session_records() if not ss.load_session_records()[n].device
        )
        ss.attach_device(extra, ss.DeviceInfo(id=device_id, name="Phone", kind="mobile"))

        resp = await client.post(f"/api/devices/{device_id}/revoke", json={})
        assert (await resp.json())["revoked"] == 2
    assert ss.device_sessions() == {}


@pytest.mark.asyncio
async def test_a_hostile_device_name_and_kind_are_clamped(_isolated) -> None:
    """`pair/complete` is auth-exempt, so its body is untrusted input into a rendered file."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        await _complete(
            client,
            data["code"],
            device_name="<script>alert(1)</script>" + "x" * 200,
            kind="../../etc/passwd",
        )
    device = next(iter(ss.device_sessions().values())).device
    assert device is not None
    assert device.kind == "unknown"
    assert len(device.name) == ss.MAX_DEVICE_NAME


@pytest.mark.asyncio
async def test_an_omitted_device_name_is_derived(_isolated) -> None:
    """C2 (b): a client holding nothing but a code must still be able to pair."""
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        resp = await _complete(client, data["code"])
        assert resp.status == 200
    device = next(iter(ss.device_sessions().values())).device
    assert device is not None
    assert device.name, "an unnamed device would be unidentifiable in the registry"


@pytest.mark.asyncio
async def test_an_undeclared_kind_is_derived_from_the_user_agent(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        resp = await client.post(
            "/api/devices/pair/complete",
            json={"code": data["code"]},
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"},
        )
        assert resp.status == 200
    device = next(iter(ss.device_sessions().values())).device
    assert device is not None
    assert (device.kind, device.name) == ("mobile", "iPhone")


@pytest.mark.asyncio
async def test_the_owners_label_beats_a_derived_name(_isolated) -> None:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/devices/pair/start", json={"label": "Kitchen tablet"})
        await _complete(client, (await resp.json())["code"])
    device = next(iter(ss.device_sessions().values())).device
    assert device is not None and device.name == "Kitchen tablet"


@pytest.mark.asyncio
async def test_pair_complete_is_rate_limited(_isolated) -> None:
    """An 8-character credential behind an unrated endpoint is a grindable one."""
    cfg = auth_h._auth_cfg()
    threshold = int(cfg.lockout_threshold)
    async with TestClient(TestServer(_app())) as client:
        for _ in range(threshold):
            assert (await _complete(client, "ABCDEFGH")).status == 401
        resp = await _complete(client, "ABCDEFGH")
        assert resp.status == 429
        assert (await resp.json())["error"]["code"] == devices_h.ERR_LOCKED_OUT
        assert resp.headers["Retry-After"]


# ── The audit floor ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_route_emits_a_sel_event(_isolated, sel_events) -> None:
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        resp = await _complete(client, data["code"], device_name="Pixel", kind="mobile")
        device_id = (await resp.json())["device_id"]
        await client.get("/api/devices")
        await client.post(f"/api/devices/{device_id}/revoke", json={})

    ops = [e["operation"] for e in sel_events]
    for expected in ("device_pair_started", "device_paired", "devices_listed", "device_revoked"):
        assert expected in ops, f"{expected} was not audited"
    assert all(e["source"] == "devices" for e in sel_events)


@pytest.mark.asyncio
async def test_the_denials_are_audited_too(_isolated, sel_events) -> None:
    """A rejection that leaves no trace is indistinguishable from an attempt never made."""
    async with TestClient(TestServer(_app())) as client:
        await _complete(client, "ABCDEFGH")
        await client.post("/api/devices/nope/revoke", json={})

    denied = [(e["operation"], e["outcome"]) for e in sel_events if e["outcome"] == "denied"]
    assert ("device_paired", "denied") in denied
    assert ("device_revoked", "denied") in denied


@pytest.mark.asyncio
async def test_the_audit_never_carries_the_code_or_the_nonce(_isolated, sel_events) -> None:
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        await _complete(client, data["code"], device_name="Pixel", kind="mobile")
    nonce = next(iter(ss.load_session_records()))
    dumped = json.dumps(sel_events)
    assert data["code"] not in dumped, "a code in the security log is a code in every log shipper"
    assert nonce not in dumped


@pytest.mark.asyncio
async def test_a_broken_sel_does_not_break_the_route(_isolated, monkeypatch) -> None:
    """An audit failure must not eat the reply the user is waiting for."""
    import personalclaw.sel as sel_mod

    monkeypatch.setattr(sel_mod, "sel", lambda: (_ for _ in ()).throw(RuntimeError("no sel")))
    async with TestClient(TestServer(_app())) as client:
        assert (await client.post("/api/devices/pair/start", json={})).status == 200


# ── The auth exemption ──────────────────────────────────────────────────


def test_only_the_redeem_path_and_its_page_are_bypass_exempt() -> None:
    """Redeeming has no session yet; minting a code requires already being the owner."""
    assert "/api/devices/pair/complete" in token_auth._BYPASS_EXACT
    assert "/pair" in token_auth._BYPASS_EXACT, "the page must be reachable without a session"
    assert "/api/devices/pair/start" not in token_auth._BYPASS_EXACT
    assert "/api/devices" not in token_auth._BYPASS_EXACT


# ── /pair — the joining device's redeem screen ──────────────────────────


@pytest.mark.asyncio
async def test_the_pairing_url_points_at_a_route_that_exists(_isolated) -> None:
    """The whole point of the atom: `pair/start`'s URL must not be a dead end.

    Drives the minted `pairing_url` back at the SAME app, so a route that is registered under a
    different path than the URL advertises fails here rather than in a user's hands.
    """
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        path = data["pairing_url"].split(data["pairing_url"].split("/")[2], 1)[1]
        assert path.startswith("/pair?code=")
        resp = await client.get(path)
        assert resp.status == 200, "the URL handed to the device 404'd"
        assert resp.content_type == "text/html"
        assert resp.headers["Cache-Control"] == "no-store"
        html = await resp.text()
    assert "Pair this device" in html
    assert "id='c'" in html, "the code field the device types into"


@pytest.mark.asyncio
async def test_the_redeem_page_never_interpolates_the_query_string(_isolated) -> None:
    """The document is a CONSTANT — which is what makes exempting it from auth open nothing.

    The code is read out of `location.search` in the browser, so nothing caller-controlled
    reaches the served bytes and there is no injection surface on an unauthenticated route.
    """
    hostile = "%3Cscript%3Ealert(1)%3C/script%3E"
    async with TestClient(TestServer(_app())) as client:
        with_code = await (await client.get(f"/pair?code={hostile}")).text()
        bare = await (await client.get("/pair")).text()
    assert with_code == bare, "the query string changed the served document"
    assert "alert(1)" not in with_code
    assert devices_h._PAIR_HTML == bare


@pytest.mark.asyncio
async def test_a_browser_that_already_has_a_session_is_sent_home(_isolated) -> None:
    """Self-pairing would overwrite this browser's own cookie and spend an eviction slot."""
    token = token_auth.generate_token("owner")
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(f"/pair?token={token}", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/"
        # A browser with no session still gets the form, so the redirect is not unconditional.
        assert (await client.get("/pair")).status == 200


def test_every_refusal_the_redeem_path_can_return_has_copy_on_the_page() -> None:
    """An unmapped code renders as a raw identifier, which is not a sentence.

    Keyed on the handler's own constants, so adding a refusal without its copy reds here
    instead of shipping `device_pair_locked_out` to a person.
    """
    for code in (
        devices_h.ERR_CODE_INVALID,
        devices_h.ERR_CODE_EXPIRED,
        devices_h.ERR_LOCKED_OUT,
        devices_h.ERR_ORIGIN,
    ):
        assert f"{code}:" in devices_h._PAIR_SCRIPT, f"{code} has no copy on the redeem page"


def test_the_page_reads_the_error_envelope_the_route_actually_emits() -> None:
    """Both standalone pages branch on `error.code`; PL-8's envelope nests it there.

    Asserted against a REAL response body rather than the string, so a change to the envelope
    shape reds here even though the pages are inline scripts no type checker reads.
    """
    from personalclaw.http_errors import json_error

    body = json.loads(json_error(devices_h.ERR_CODE_EXPIRED, status=401).body)
    assert body["error"]["code"] == devices_h.ERR_CODE_EXPIRED
    assert "res.data.error.code" in devices_h._PAIR_SCRIPT
    assert "res.data.error.code" in auth_h._LOGIN_SCRIPT


def test_the_two_standalone_pages_share_one_token_block() -> None:
    """`/login` and `/pair` are the only surfaces that cannot inherit `web/`'s design system.

    A second hand-written copy of the tokens drifts silently — neither page has a
    visual-regression test that would notice.
    """
    from personalclaw.dashboard.handlers.page_shell import PAGE_STYLE

    assert PAGE_STYLE in devices_h._PAIR_HTML
    assert PAGE_STYLE in auth_h._LOGIN_HTML
    assert "--primary" in PAGE_STYLE, "vacuity floor: the shared block carries real tokens"


@pytest.mark.asyncio
async def test_a_wrong_origin_is_refused_on_both_pair_routes(_isolated, monkeypatch) -> None:
    monkeypatch.setattr(devices_h, "check_origin", lambda _r: False)
    async with TestClient(TestServer(_app())) as client:
        for path in ("/api/devices/pair/start", "/api/devices/pair/complete"):
            resp = await client.post(path, json={"code": "ABCDEFGH"})
            assert resp.status == 403
            assert (await resp.json())["error"]["code"] == devices_h.ERR_ORIGIN


@pytest.mark.asyncio
async def test_a_session_that_cannot_be_attributed_is_retracted(_isolated, monkeypatch) -> None:
    """An un-listed device session is the exact failure the registry exists to prevent."""
    monkeypatch.setattr(devices_h, "attach_device", lambda *a, **k: False)
    async with TestClient(TestServer(_app())) as client:
        data = await _start(client)
        resp = await _complete(client, data["code"])
        assert resp.status == 503
    assert ss.load_session_records() == {}, "the unattributable session was retracted"
