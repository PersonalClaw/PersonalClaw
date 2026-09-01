"""EA-7 — the channel sender-trust read/revoke API (`handlers/channel_trust.py`).

The trust store shipped writable from two places (a pairing code, the unknown-sender
notification's Allow) and readable from none, so "who can talk to my agent right now?" had
no answer short of reading JSON out of the home directory. These are the two routes that
close that, plus the rails for the two ways this particular surface can go quietly wrong:
leaking the pairing secret into the read projection, and being shadowed by the neighbouring
`/api/channels/{name}` route.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw import channel_trust as ct
from personalclaw.dashboard.handlers import channel_trust as h


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the entity-settings store + SEL at tmp_path (the real home is never touched)."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield tmp_path


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def _get():
    """The GET projection, driven synchronously.

    The handlers are coroutines but the assertions are not, so each helper owns one
    `asyncio.run` — the same shape `test_channel_trust.py` uses. That keeps every test body
    plain and avoids sprinkling `@pytest.mark.asyncio` (this repo runs pytest-asyncio in
    strict mode, so the marker would be mandatory on each one).
    """
    req = make_mocked_request("GET", "/api/channels/trust")
    return _body(asyncio.run(h.api_channel_trust(req)))


def _revoke(provider, sender_id):
    req = make_mocked_request(
        "DELETE",
        f"/api/channels/trust/{provider}/senders/{sender_id}",
        match_info={"provider": provider, "sender_id": sender_id},
    )
    return asyncio.run(h.api_channel_trust_revoke(req))


# ── read ─────────────────────────────────────────────────────────────────────


def test_empty_store_reads_as_no_providers_not_as_an_error():
    """A fresh install has no trust state; that is an empty list, not a failure."""
    body = _get()
    assert body["providers"] == []
    # The vocabularies still come back, so a UI can render the posture words it will need.
    assert body["default_dm_policy"] == ct.DEFAULT_DM_POLICY
    assert "pairing" in body["dm_policies"]


def test_read_lists_paired_senders_with_their_provenance():
    ct.allow_sender("telegram", "u1", name="Alice", via="owner")
    ct.allow_sender("telegram", "u2", name="Bob", via="pairing")
    ct.track("telegram", "grp1", name="Standup")

    body = _get()
    assert [p["provider"] for p in body["providers"]] == ["telegram"]
    tg = body["providers"][0]
    assert [s["sender_id"] for s in tg["allowed_senders"]] == ["u1", "u2"]
    assert {s["sender_id"]: s["via"] for s in tg["allowed_senders"]} == {
        "u1": "owner",
        "u2": "pairing",
    }
    assert [s["name"] for s in tg["allowed_senders"]] == ["Alice", "Bob"]
    assert all(s["added_at"] for s in tg["allowed_senders"]), "provenance needs a timestamp"
    assert [c["channel_id"] for c in tg["tracked_channels"]] == ["grp1"]
    assert tg["policies"] == {"dm": "pairing", "group": "tracked_only"}


def test_read_is_provider_partitioned():
    ct.allow_sender("telegram", "u1")
    ct.allow_sender("discord", "u2")
    body = _get()
    got = {p["provider"]: [s["sender_id"] for s in p["allowed_senders"]] for p in body["providers"]}
    assert got == {"discord": ["u2"], "telegram": ["u1"]}


def test_the_read_projection_never_carries_the_pairing_secret():
    """The store holds a SHA-256 of the active code. The wire must not carry even that.

    A read surface that echoed `code_hash` would hand an offline-guessable digest of an
    8-digit code to every client of this endpoint — an 8-digit space is trivially
    brute-forced against a known hash. The endpoint reports only that a code is outstanding.
    """
    code = ct.create_pairing_code("telegram")
    body = _get()
    tg = body["providers"][0]

    assert tg["pairing_active"] is True
    assert tg["pairing_expires_at"], "a live code should say when it dies"

    blob = json.dumps(body)
    assert "code_hash" not in blob
    assert code not in blob
    import hashlib

    assert hashlib.sha256(code.encode()).hexdigest() not in blob


def test_the_read_projection_withholds_the_unknown_sender_contact_log():
    """`rate` is who TRIED to reach the owner — a different surface from who is allowed."""
    ct.note_unknown_sender(None, "telegram", "stranger")
    body = _get()
    blob = json.dumps(body)
    assert "rate" not in blob
    assert "stranger" not in blob
    assert body["providers"][0]["allowed_senders"] == []


# ── revoke ───────────────────────────────────────────────────────────────────


def test_revoke_removes_the_sender_and_reports_it():
    ct.allow_sender("telegram", "u1", name="Alice")
    resp = _revoke("telegram", "u1")

    assert resp.status == 200
    assert _body(resp)["ok"] is True
    assert ct.is_allowed_sender("telegram", "u1") is False
    assert (_get())["providers"][0]["allowed_senders"] == []


def test_revoke_emits_the_audit_row():
    """A revocation is a security event; it goes in the SEL like every other trust change."""
    from personalclaw.sel import sel

    ct.allow_sender("telegram", "u1")
    _revoke("telegram", "u1")

    ops = [(e.get("operation"), e.get("outcome")) for e in sel().recent(200)]
    assert ("sender_denied", "owner") in ops


def test_revoking_a_sender_who_is_not_allowed_is_a_404_not_a_silent_success():
    """`deny_sender` is idempotent, but the ROUTE must not be.

    A UI that revokes a row its list showed, and gets 200 for a sender the store never
    had, learns nothing about the fact that its list was stale. The typed code is what lets
    the client say "this list moved under you" instead of "revoked".
    """
    resp = _revoke("telegram", "ghost")
    assert resp.status == 404
    assert _body(resp)["error"]["code"] == "channel_trust_sender_unknown"


def test_revoke_percent_decodes_the_sender_id():
    """A sender id is opaque to core and may need escaping in a path segment."""
    ct.allow_sender("email", "someone@example.com")
    req = make_mocked_request(
        "DELETE",
        "/api/channels/trust/email/senders/someone%40example.com",
        match_info={"provider": "email", "sender_id": "someone%40example.com"},
    )
    resp = asyncio.run(h.api_channel_trust_revoke(req))
    assert resp.status == 200
    assert ct.is_allowed_sender("email", "someone@example.com") is False


def test_revoke_is_provider_scoped():
    """The same sender id on two providers is two different grants."""
    ct.allow_sender("telegram", "u1")
    ct.allow_sender("discord", "u1")
    _revoke("telegram", "u1")
    assert ct.is_allowed_sender("telegram", "u1") is False
    assert ct.is_allowed_sender("discord", "u1") is True


# ── rails ────────────────────────────────────────────────────────────────────


def test_the_error_code_is_in_the_append_only_registry():
    """A `json_error` code with no registry row is only caught by the full suite.

    `tests/test_http_error_codes_append_only.py` asserts every literal code reaches the
    registry, but it runs over the whole tree — so a targeted run of this module would ship
    a missing row green. This names the code locally.
    """
    from personalclaw.http_errors import HTTP_ERROR_CODES

    assert "channel_trust_sender_unknown" in HTTP_ERROR_CODES
    assert HTTP_ERROR_CODES["channel_trust_sender_unknown"]


def test_trust_route_is_not_shadowed_by_the_name_route():
    """`/api/channels/trust` must be registered BEFORE `/api/channels/{name}`.

    aiohttp resolves in registration order, so the dynamic `{name}` route swallows any
    literal sibling registered after it — the request would reach `api_channel_get` and
    answer `{"error": "unknown transport"}` with a 404, which reads like a missing provider
    rather than a mis-ordered router. The registration is inline in `start_dashboard` (a
    function that boots a real server), so the order is asserted where it is decided: in
    the source.
    """
    from pathlib import Path

    import personalclaw.dashboard.server as server

    lines = Path(server.__file__).read_text(encoding="utf-8").splitlines()
    trust = [i for i, ln in enumerate(lines) if '"/api/channels/trust"' in ln]
    named = [i for i, ln in enumerate(lines) if '"/api/channels/{name}"' in ln]

    # Floor: both registrations were actually found, so a rename cannot make this vacuous.
    assert trust, "no /api/channels/trust registration found in server.py"
    assert named, "no /api/channels/{name} registration found in server.py"
    assert max(trust) < min(named), (
        "/api/channels/trust is registered after /api/channels/{name} and will be shadowed "
        f"(trust at {[i + 1 for i in trust]}, name at {[i + 1 for i in named]})"
    )
