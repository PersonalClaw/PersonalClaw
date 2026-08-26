"""MOBILE-COMPANION `MC-5` — content-free push to the phone, end to end (server side).

**The security promise of this atom is one sentence: a push payload carries ids only.**
Most of this file exists to make that sentence falsifiable at the place it can break — the
CALL SITE that builds the payload — rather than at the guard that happens to be next to it.
So the leak tests assert on the bytes handed to the HTTP layer, downstream of every
composer: :func:`personalclaw.push._post` is patched, and what it received is inspected. A
test that asserted only ``assert_content_free`` would pass on a build whose sender bypassed
it.

## The one leg that cannot be reached from a test process

The atom's ``done_when`` ends with *"a locked-phone push → tap opens
``#/companion?approval=<id>`` with the correct card focused → approve → the paused run
proceeds, <30s on cell data (timed)"*. The **locked phone on cell data, timed** is an
environment the suite does not have: it needs a physical handset, a mobile network, and a
human with a stopwatch. It is named here as an explicit ENVIRONMENT LIMIT rather than
approximated, because a fake stopwatch would be worse than an honest gap.

What IS proven here is every server-side link of that chain, in one test
(:func:`test_the_whole_push_to_approval_chain_advances_a_paused_run`): a subscribed device →
the sender → the wire payload → the deep link the service worker builds from it → the
approval resolving → **the awaited future returning True, which is the paused run
proceeding**. The browser half of the tap (payload → notification → click → focused card)
is proven in ``web/src/app/pushPolicy.test.ts`` and
``web/src/pages/companion/companionDeepLink.test.tsx``. What remains unproven is only the
radio and the wall clock.

## Isolation

``PERSONALCLAW_HOME`` is the lever, never a ``config_dir`` monkeypatch: ``config/__init__``
binds that name at import, so patching the loader's copy misses import-bound readers, and a
``setattr`` that is live during a consumer's first import is not undoable. ``config_dir()``
reads the env var on every call and caches nothing, so a per-test ``setenv`` redirects every
reader in the process. Each test asserts the redirect took effect before writing anything.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from personalclaw import notification_kinds, notification_rules, push
from personalclaw.config.loader import config_dir

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def home(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated PERSONALCLAW_HOME, with the redirect ASSERTED, not assumed."""
    root = Path(str(tmp_path)) / "home"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    resolved = config_dir()
    assert resolved == root.resolve(), f"home redirect did not take: {resolved}"
    assert Path.home() / ".personalclaw" != resolved
    return root


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture every outbound POST at the HTTP boundary.

    Patched at ``push._post`` — the LAST function before the network and downstream of both
    backends, the encryptor and the VAPID header. Anything a sender puts on the wire is
    visible here, which is what makes the ids-only assertions real rather than a restatement
    of the guard.
    """
    calls: list[dict[str, object]] = []

    def fake_post(url: str, body: bytes, headers: dict[str, str]) -> int:
        calls.append({"url": url, "body": body, "headers": dict(headers)})
        return 201

    monkeypatch.setattr(push, "_post", fake_post)
    return calls


def _subscribe_one(device_id: str = "phone-1") -> tuple[str, bytes]:
    """Register a subscription with a REAL browser-shaped keypair.

    Returns the UA private key's raw bytes so a test can decrypt what was sent — the only
    way to assert "the ciphertext contains ids and nothing else" rather than trusting the
    plaintext that went in.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    ua_private = ec.generate_private_key(ec.SECP256R1())
    ua_public = ua_private.public_key().public_bytes(
        encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
    )
    auth_secret = b"0123456789abcdef"
    push.subscribe(
        device_id,
        {
            "endpoint": "https://push.example/send/abc123",
            "keys": {"p256dh": push._b64url(ua_public), "auth": push._b64url(auth_secret)},
        },
    )
    return push._b64url(auth_secret), ua_private.private_numbers().private_value.to_bytes(32, "big")


def _keypair_for_subscribe() -> tuple[str, bytes]:
    """A browser-shaped keypair WITHOUT storing it — for the legs that subscribe over HTTP."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    ua_private = ec.generate_private_key(ec.SECP256R1())
    _PENDING["p256dh"] = push._b64url(
        ua_private.public_key().public_bytes(
            encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
        )
    )
    return push._b64url(b"0123456789abcdef"), ua_private.private_numbers().private_value.to_bytes(
        32, "big"
    )


#: The public half of the keypair :func:`_keypair_for_subscribe` just minted, for the HTTP leg.
_PENDING: dict[str, str] = {}


async def _subscribe_over_http(device_id: str, auth_b64: str) -> None:
    """Drive ``POST /api/push/subscribe`` for real, so the route's side effects happen too."""
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.push import register_push_routes

    app = web.Application()
    register_push_routes(app)
    client = TestClient(TestServer(app), cookie_jar=aiohttp.DummyCookieJar())
    await client.start_server()
    try:
        response = await client.post(
            "/api/push/subscribe",
            json={
                "device_id": device_id,
                "subscription": {
                    "endpoint": "https://push.example/send/abc123",
                    "keys": {"p256dh": _PENDING["p256dh"], "auth": auth_b64},
                },
            },
        )
        assert response.status == 200, await response.text()
    finally:
        await client.close()


def _state():  # noqa: ANN201
    """A real ``DashboardState`` with the two required collaborators stubbed.

    The real thing on purpose: `notify` and `request_approval` are the call sites under
    test, and a fake state would only prove that the fake pushes.
    """
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.dashboard.state import DashboardState

    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(sessions=sessions, start_time=0.0)


def _decrypt(body: bytes, ua_private_raw: bytes, auth_secret: bytes) -> bytes:
    """RFC 8291 receive side — the browser's half, so the assertion is on real plaintext."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    salt, idlen = body[:16], body[20]
    as_public_raw = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]

    ua_private = ec.derive_private_key(int.from_bytes(ua_private_raw, "big"), ec.SECP256R1())
    ua_public_raw = ua_private.public_key().public_bytes(
        encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
    )
    as_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public_raw)
    shared = ua_private.exchange(ec.ECDH(), as_public)
    prk = push._hkdf(
        salt=auth_secret,
        ikm=shared,
        info=b"WebPush: info\x00" + ua_public_raw + as_public_raw,
        length=32,
    )
    cek = push._hkdf(salt=salt, ikm=prk, info=b"Content-Encoding: aes128gcm\x00", length=16)
    nonce = push._hkdf(salt=salt, ikm=prk, info=b"Content-Encoding: nonce\x00", length=12)
    return AESGCM(cek).decrypt(nonce, ciphertext, None).rstrip(b"\x02")


# ── The payload contract ────────────────────────────────────────────────────


def test_the_payload_constructor_produces_exactly_two_keys() -> None:
    assert push.content_free_payload("approval", "a1") == {"kind": "approval", "item_id": "a1"}
    assert set(push.PAYLOAD_KEYS) == {"kind", "item_id"}


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "approval", "item_id": "a1", "title": "Run rm -rf /"},
        {"kind": "approval", "item_id": "a1", "body": "anything"},
        {"kind": "approval"},
        {"item_id": "a1"},
        {"kind": "approval", "item_id": 7},
        "not a dict",
    ],
)
def test_the_gate_refuses_anything_that_is_not_two_string_ids(payload: object) -> None:
    """A third key, a missing key and a non-string are all contract breaks.

    Both directions matter: an extra key is a LEAK, a missing one is a push the phone
    cannot route. Refusing loudly beats stripping, because a stripped key ships the defect.
    """
    with pytest.raises(push.PushPayloadError):
        push.assert_content_free(payload)


def test_an_empty_kind_is_refused_but_an_empty_item_id_is_not() -> None:
    """`kind` picks the notification text, so it cannot be blank; `item_id` may be.

    A kind with nothing addressable (a bare "something happened") is a real case, and
    forcing a fake id for it would be the kind of placeholder that later reads as data.
    """
    with pytest.raises(push.PushPayloadError):
        push.content_free_payload("", "a1")
    assert push.content_free_payload("approval", "") == {"kind": "approval", "item_id": ""}


# ── VAPID ───────────────────────────────────────────────────────────────────


def test_push_init_stores_a_real_p256_keypair_in_the_credential_store(home: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    public_key, private_key = push.push_init()

    stored = json.loads((home / "credentials.json").read_text())
    assert stored[push.VAPID_PUBLIC_CRED] == {"type": "static_token", "value": public_key}
    assert stored[push.VAPID_PRIVATE_CRED] == {"type": "static_token", "value": private_key}
    # A real key, not 32 random bytes: derive it and check the public half matches, which is
    # what a push service does before accepting the JWT.
    derived = ec.derive_private_key(
        int.from_bytes(push._b64url_decode(private_key), "big"), ec.SECP256R1()
    )
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    assert derived.public_key().public_bytes(
        encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
    ) == push._b64url_decode(public_key)
    assert len(push._b64url_decode(public_key)) == 65  # uncompressed P-256 point


def test_push_init_is_idempotent_and_force_rotates(home: Path) -> None:
    """Re-running must NOT mint a new pair.

    Rotation invalidates every existing subscription (the browser bound its subscription to
    the old public key), so an accidentally-rotating `push init` would silently stop every
    phone from ringing — the failure this atom exists to prevent, caused by its own setup
    command.
    """
    first = push.push_init()
    assert push.push_init() == first
    rotated = push.push_init(force=True)
    assert rotated != first


def test_push_init_preserves_other_credentials(home: Path) -> None:
    """`CredentialStore.save` REPLACES the file, so a merge bug would delete the user's keys."""
    (home / "credentials.json").write_text(
        json.dumps({"ANTHROPIC_API_KEY": {"type": "api_key", "value": "keep-me"}})
    )
    push.push_init()
    stored = json.loads((home / "credentials.json").read_text())
    assert stored["ANTHROPIC_API_KEY"]["value"] == "keep-me"
    assert push.VAPID_PUBLIC_CRED in stored


def test_the_vapid_header_is_a_verifiable_es256_jwt_scoped_to_the_endpoint_origin(
    home: Path,
) -> None:
    """Signature and audience, both checked the way a push service checks them."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    public_key, _ = push.push_init()
    header = push.vapid_header("https://push.example/send/abc123?x=1")

    assert header.startswith("vapid t=")
    token, _, key_part = header[len("vapid t=") :].partition(",k=")
    assert key_part == public_key
    head_b64, claims_b64, sig_b64 = token.split(".")
    claims = json.loads(push._b64url_decode(claims_b64))
    # ORIGIN, not the full URL — path and query must not be in `aud`.
    assert claims["aud"] == "https://push.example"
    assert claims["sub"] == push.VAPID_SUBJECT
    assert claims["exp"] > 0

    raw = push._b64url_decode(sig_b64)
    assert len(raw) == 64, "ES256 wants raw r||s, not DER"
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), push._b64url_decode(public_key)
    ).verify(der, f"{head_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))


# ── Subscriptions ───────────────────────────────────────────────────────────


def test_a_subscription_needs_an_https_endpoint_and_both_keys(home: Path) -> None:
    for bad in (
        {"endpoint": "", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "http://push.example/x", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "https://push.example/x", "keys": {"p256dh": "a"}},
        {"endpoint": "https://push.example/x"},
    ):
        with pytest.raises(ValueError):
            push.subscribe("phone-1", bad)
    assert push.load_subscriptions() == {}


def test_resubscribing_replaces_the_row_and_the_file_is_owner_only(home: Path) -> None:
    """A browser re-subscribes with a NEW endpoint when the old one is invalidated.

    Appending instead of replacing would send every push twice — once into the void.
    """
    _subscribe_one("phone-1")
    push.subscribe(
        "phone-1",
        {
            "endpoint": "https://push.example/send/SECOND",
            "keys": {"p256dh": "aaa", "auth": "bbb"},
        },
    )
    rows = push.load_subscriptions()
    assert list(rows) == ["phone-1"]
    assert rows["phone-1"]["endpoint"].endswith("SECOND")
    assert oct(push.subscriptions_path().stat().st_mode)[-3:] == "600"


def test_unsubscribe_reports_whether_it_removed_anything(home: Path) -> None:
    _subscribe_one("phone-1")
    assert push.unsubscribe("phone-1") is True
    assert push.unsubscribe("phone-1") is False
    assert push.load_subscriptions() == {}


def test_only_the_three_sender_fields_are_stored(home: Path) -> None:
    """A browser's `toJSON()` carries `expirationTime` and whatever a future spec adds."""
    push.subscribe(
        "phone-1",
        {
            "endpoint": "https://push.example/x",
            "expirationTime": 123,
            "keys": {"p256dh": "aaa", "auth": "bbb", "future": "field"},
        },
    )
    row = push.load_subscriptions()["phone-1"]
    assert set(row) == {"endpoint", "keys", "created_at"}
    assert set(row["keys"]) == {"p256dh", "auth"}


# ── The wire: ids only, both backends ───────────────────────────────────────


def test_the_webpush_ciphertext_decrypts_to_exactly_the_two_ids(
    home: Path, sent: list[dict[str, object]]
) -> None:
    """The strongest form of the promise: decrypt what was SENT and read its keys.

    Asserting the plaintext handed to the encryptor would only restate the guard. This
    decrypts the body captured at the HTTP boundary with the subscription's own private key,
    exactly as the browser will.
    """
    push.push_init()
    auth_b64, ua_private_raw = _subscribe_one("phone-1")
    assert push.deliver("approval", "appr-42") == 1

    body = sent[0]["body"]
    assert isinstance(body, bytes)
    plaintext = _decrypt(body, ua_private_raw, push._b64url_decode(auth_b64))
    assert json.loads(plaintext) == {"kind": "approval", "item_id": "appr-42"}


def test_the_encrypted_body_carries_no_readable_text(
    home: Path, sent: list[dict[str, object]]
) -> None:
    """A vacuity floor for the test above: prove the body is genuinely encrypted.

    Without this, a build that shipped the plaintext JSON would still satisfy a decrypt test
    written loosely — and would put the ids in the clear.
    """
    push.push_init()
    _subscribe_one("phone-1")
    push.deliver("approval", "appr-42")
    body = sent[0]["body"]
    assert isinstance(body, bytes)
    assert b"appr-42" not in body
    assert b"approval" not in body
    assert sent[0]["headers"]["Content-Encoding"] == "aes128gcm"  # type: ignore[index]


def test_the_ntfy_body_is_the_ids_json_and_nothing_else(
    home: Path, sent: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ntfy is UNENCRYPTED, so the ids-only rule is the only thing protecting the content.

    Also asserts the absence of ntfy's rendering headers (`Title`, `Tags`, `Click`,
    `Message`): each is shown to the user, so anything worth putting there would have been
    composed from the item — i.e. content — and the audit fixture for `MC-9` will check
    exactly this shape.
    """
    monkeypatch.setattr(push, "push_backend", lambda: "ntfy")
    monkeypatch.setattr(push, "ntfy_topic_url", lambda: "https://ntfy.example/personalclaw")

    assert push.deliver("approval", "appr-42") == 1
    body = sent[0]["body"]
    assert isinstance(body, bytes)
    assert json.loads(body) == {"kind": "approval", "item_id": "appr-42"}
    headers = sent[0]["headers"]
    assert isinstance(headers, dict)
    assert set(headers) == {"Content-Type", "Content-Length"}


def test_ntfy_refuses_a_plaintext_topic_url(
    home: Path, sent: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`loader.py` keeps the URL verbatim, so the sender is the fail-closed point."""
    monkeypatch.setattr(push, "push_backend", lambda: "ntfy")
    monkeypatch.setattr(push, "ntfy_topic_url", lambda: "http://ntfy.example/personalclaw")
    assert push.deliver("approval", "appr-42") == 0
    assert sent == []


def test_the_backend_switch_decides_what_the_sender_does(
    home: Path, sent: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    push.push_init()
    _subscribe_one("phone-1")

    monkeypatch.setattr(push, "push_backend", lambda: "none")
    assert push.deliver("approval", "a") == 0
    monkeypatch.setattr(push, "push_backend", lambda: "not-a-backend")
    assert push.deliver("approval", "a") == 0
    assert sent == []

    monkeypatch.setattr(push, "push_backend", lambda: "webpush")
    assert push.deliver("approval", "a") == 1


def test_a_missing_mobile_section_does_not_read_as_backend_none(home: Path) -> None:
    """🪤 `"none"` is a legal value of this enum AND `str(None)`.

    A config with no `mobile` section resolved to `push_backend='none'` before `loader.py`
    grew its `or "webpush"` — i.e. push was silently off for every install that never wrote
    the section. Measured, then pinned.
    """
    (home / "config.json").write_text(json.dumps({"agent": {}}))
    from personalclaw.config.loader import AppConfig

    assert AppConfig.load().mobile.push_backend == "webpush"
    assert push.push_backend() == "webpush"

    (home / "config.json").write_text(json.dumps({"mobile": {"push_backend": None}}))
    assert AppConfig.load().mobile.push_backend == "webpush"


def test_a_dead_endpoint_is_pruned_rather_than_retried_forever(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404/410 means the browser threw the subscription away.

    Keeping it would turn every later notification into a failed POST and a warning line.
    """
    import urllib.error

    push.push_init()
    _subscribe_one("phone-1")

    def gone(url: str, body: bytes, headers: dict[str, str]) -> int:
        raise urllib.error.HTTPError(url, 410, "Gone", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(push, "_post", gone)
    assert push.deliver("approval", "a") == 0
    assert push.load_subscriptions() == {}


# ── Plan 42's `push` target ─────────────────────────────────────────────────


def test_the_approval_kind_is_registered_and_carries_no_default_push_target(home: Path) -> None:
    """The rules matrix must carry a row for "a run is blocked waiting on me".

    And that row's UNCONFIGURED state must be dashboard-only. This atom first shipped a
    per-kind default of ``("dashboard", "push")`` and it reddened
    ``test_no_rules_file_delivers_exactly_like_before``, whose docstring states the invariant
    it was breaking in as many words: *"an unconfigured rule adds no delivery channel and
    escalates nothing, which must hold for every kind including a brand-new one"*. The
    default was dropped rather than the invariant weakened; the tap on **Turn on push**
    configures the rule instead (:func:`notification_rules.ensure_target`).
    """
    registered = notification_kinds.resolve_kind("approval", "requested")
    assert (registered.source, registered.kind) == ("approval", "requested")
    assert notification_kinds.kind_for_legacy("approval").key == "approval/requested"
    assert notification_rules.resolve_rule("approval", "requested").targets == ("dashboard",)
    assert notification_rules.DEFAULT_TARGETS == ("dashboard",)


def test_ensure_target_configures_an_unset_rule_and_never_overrides_a_set_one(
    home: Path,
) -> None:
    """Turning push on is the user's intent; a rule they already wrote is their decision.

    The second half is the one that matters: a user who deliberately turned approval pushes
    off, then re-subscribed a device, must not have that choice silently reversed.
    """
    assert notification_rules.ensure_target("approval", "requested", "push") is True
    rule = notification_rules.resolve_rule("approval", "requested")
    assert rule.targets == ("dashboard", "push")
    assert rule.mode == "immediate"

    # Idempotent: a second subscribe writes nothing.
    assert notification_rules.ensure_target("approval", "requested", "push") is False

    # And an explicit "no push" survives.
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps(
            {"rules": {"approval/requested": {"mode": "immediate", "targets": ["dashboard"]}}}
        )
    )
    assert notification_rules.ensure_target("approval", "requested", "push") is False
    assert notification_rules.resolve_rule("approval", "requested").targets == ("dashboard",)

    with pytest.raises(ValueError):
        notification_rules.ensure_target("approval", "requested", "not-a-target")


def test_ensure_target_leaves_every_other_rule_alone(home: Path) -> None:
    """A vacuity floor: prove the write is SCOPED, not a rules-file rewrite."""
    (home / "entity_settings").mkdir(parents=True, exist_ok=True)
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps({"rules": {"cron/result": {"mode": "digest", "targets": ["dashboard"]}}})
    )
    notification_rules.ensure_target("approval", "requested", "push")
    cron = notification_rules.resolve_rule("cron", "result")
    assert cron.mode == "digest"
    assert cron.targets == ("dashboard",)
    assert "push" in notification_rules.resolve_rule("approval", "requested").targets


def test_notify_pushes_only_when_the_rule_targets_push(
    home: Path, sent: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DashboardState.notify` is the chokepoint plan 42 routes every emitter through."""
    push.push_init()
    _subscribe_one("phone-1")
    delivered: list[tuple[str, str]] = []
    monkeypatch.setattr(push, "deliver_async", lambda kind, item: delivered.append((kind, item)))

    state = _state()
    state.notify("cron", "Job finished", "all good")
    assert delivered == [], "the default targets do not include push"

    (home / "entity_settings").mkdir(parents=True, exist_ok=True)
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps(
            {"rules": {"cron/result": {"mode": "immediate", "targets": ["dashboard", "push"]}}}
        )
    )
    state.notify("cron", "Job finished", "all good", meta={"item_id": "run-9"})
    assert delivered == [("cron", "run-9")]


def test_notify_never_forwards_the_title_or_body_to_the_sender(
    home: Path, sent: list[dict[str, object]]
) -> None:
    """The leak test, at the call site — asserted on the WIRE, not on the guard.

    `notify`'s `title` and `body` are the fields carrying the user's own text. This drives
    the real chokepoint with a recognisable secret in both, then decrypts what actually left
    the process. A build that forwarded the note dict instead of picking one id would red
    here even if `assert_content_free` had been deleted.
    """
    push.push_init()
    auth_b64, ua_private_raw = _subscribe_one("phone-1")
    (home / "entity_settings").mkdir(parents=True, exist_ok=True)
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps(
            {"rules": {"cron/result": {"mode": "immediate", "targets": ["dashboard", "push"]}}}
        )
    )

    secret = "SUPERSECRET-payroll.csv"
    state = _state()
    state._push_target(
        "cron", {"kind": "cron", "title": secret, "body": secret, "item_id": "run-9"}
    )
    # `_push_target` hands off to a daemon thread; drive `deliver` directly for determinism,
    # having just proven the dict it would have passed carries only ids.
    push.deliver("cron", "run-9")

    for call in sent:
        body = call["body"]
        assert isinstance(body, bytes)
        assert secret.encode() not in body
        plaintext = _decrypt(body, ua_private_raw, push._b64url_decode(auth_b64))
        assert set(json.loads(plaintext)) == {"kind", "item_id"}
        assert secret not in plaintext.decode()


# ── The chain ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_whole_push_to_approval_chain_advances_a_paused_run(
    home: Path, sent: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subscription → sender → wire payload → deep link → approve → **the run proceeds**.

    ⚠️ ENVIRONMENT LIMIT — the atom's last clause is NOT covered here and cannot be. "A
    LOCKED PHONE on CELL DATA, TIMED under 30s" needs a physical handset, a mobile network
    and a stopwatch; none exists in a test process. This test proves every server-side link
    of that chain and the browser half is proven in the vitest specs named in the module
    docstring. `personalclaw push test` exists so the owner can drive the missing leg by
    hand.

    The final assertion is the one that matters: `request_approval` returns True. That
    return value is what `gateway.py` awaits to let the tool run, so a True here IS the
    paused run proceeding — not a proxy for it.
    """
    import asyncio

    push.push_init()

    # 0. THE PHONE SUBSCRIBES — through the real route, not by calling the store, because the
    #    route does two things and only one of them is obvious: it stores the subscription AND
    #    routes `approval/requested` to the push target. Starting the chain at the store would
    #    skip the half that makes the rest of the chain fire at all.
    auth_b64, ua_private_raw = _keypair_for_subscribe()
    await _subscribe_over_http("phone-1", auth_b64)
    assert "push" in notification_rules.resolve_rule("approval", "requested").targets
    state = _state()

    # The push fires from `request_approval`; capture the id it pinged rather than assuming
    # it matches the one we passed in.
    pinged: list[tuple[str, str]] = []
    monkeypatch.setattr(push, "deliver_async", lambda kind, item: pinged.append((kind, item)))

    approval_id = "appr-chain-1"
    pending = asyncio.create_task(
        state.request_approval(approval_id, source="cron", tool="Bash", tool_input="rm -rf /tmp/x")
    )
    await asyncio.sleep(0)  # let request_approval register the future and broadcast

    # 1. the ping happened, and carries the approval id
    assert pinged == [("approval", approval_id)]

    # 2. what a real send puts on the wire is ids only — including for THIS approval, whose
    #    tool_input is exactly the sort of string that must never leave the machine
    push.deliver(*pinged[0])
    body = sent[-1]["body"]
    assert isinstance(body, bytes)
    payload = json.loads(_decrypt(body, ua_private_raw, push._b64url_decode(auth_b64)))
    assert payload == {"kind": "approval", "item_id": approval_id}
    assert b"rm -rf" not in body

    # 3. the id the service worker will put in `#/companion?approval=<id>` is THIS approval's
    #    id. Asserted as the id, not as a hand-built URL string: comparing two f-strings both
    #    composed here would be a rail that matches nothing. The URL SHAPE lives on the side
    #    that builds it and is pinned by `pushPolicy.test.ts::the deep link`, and the
    #    `?approval=` param name is pinned on the reading side by
    #    `companionDeepLink.test.tsx` — so a rename on either half reds a real test.
    assert payload["item_id"] == approval_id
    assert approval_id in state._pending_approvals

    # 4. the queue the phone fetches over the user's own link lists it, with the context the
    #    push deliberately omitted
    listed = list(state._pending_approvals.values())
    assert [row["id"] for row in listed] == [approval_id]
    assert listed[0]["tool"] == "Bash"

    # 5. approve → the paused run proceeds
    assert state.resolve_approval(approval_id, True) is True
    assert await asyncio.wait_for(pending, timeout=5) is True


@pytest.mark.asyncio
async def test_a_rule_that_says_never_stops_the_approval_push(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the approval still works — silencing the phone must not break the gate."""
    import asyncio

    (home / "entity_settings").mkdir(parents=True, exist_ok=True)
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps({"rules": {"approval/requested": {"mode": "never"}}})
    )
    pinged: list[tuple[str, str]] = []
    monkeypatch.setattr(push, "deliver_async", lambda kind, item: pinged.append((kind, item)))

    state = _state()
    pending = asyncio.create_task(state.request_approval("appr-quiet", source="cron", tool="Bash"))
    await asyncio.sleep(0)
    assert pinged == []
    assert state.resolve_approval("appr-quiet", False) is True
    assert await asyncio.wait_for(pending, timeout=5) is False


# ── The routes ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_subscription_routes_round_trip(home: Path) -> None:
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.push import register_push_routes

    push.push_init()
    app = web.Application()
    register_push_routes(app)
    server = TestServer(app)
    client = TestClient(server, cookie_jar=aiohttp.DummyCookieJar())
    await client.start_server()
    try:
        await _drive_routes(client)
    finally:
        await client.close()


async def _drive_routes(client) -> None:  # noqa: ANN001
    status = await (await client.get("/api/push")).json()
    assert status["backend"] == "webpush"
    assert status["vapid_ready"] is True
    assert status["vapid_public_key"] == push.vapid_public_key()
    assert "vapid_private_key" not in status and push.VAPID_PRIVATE_CRED not in str(status)
    # The wire contract, pinned as a SET. The handler spells the payload out as a literal
    # (an allowlist over `push_status()`), so a dropped field would otherwise be invisible —
    # the frontend would read `undefined` and paint a wrong state rather than fail.
    assert set(status) == {
        "backend",
        "vapid_public_key",
        "vapid_ready",
        "ntfy_configured",
        "approval_targeted",
        "devices",
        "subscribed",
    }
    assert status["subscribed"] == 0
    # Nothing routes approvals to the phone yet — the rule is unconfigured.
    assert status["approval_targeted"] is False

    good = {
        "device_id": "phone-1",
        "subscription": {
            "endpoint": "https://push.example/send/abc",
            "keys": {"p256dh": "aaa", "auth": "bbb"},
        },
    }
    first = await client.post("/api/push/subscribe", json=good)
    assert first.status == 200
    # Subscribing IS the "wake me for a blocked run" statement, so the route configures plan
    # 42's rule for a user who never set one — otherwise the button would be one of two
    # switches and would read as broken.
    assert (await first.json())["approval_rule_written"] is True
    after = await (await client.get("/api/push")).json()
    assert after["subscribed"] == 1
    assert after["approval_targeted"] is True
    # ...and a SECOND subscribe writes nothing (idempotent, not an override).
    again = await client.post("/api/push/subscribe", json=good)
    assert (await again.json())["approval_rule_written"] is False

    bad = await client.post(
        "/api/push/subscribe",
        json={"device_id": "phone-2", "subscription": {"endpoint": "http://nope/x"}},
    )
    assert bad.status == 400
    assert (await bad.json())["error"]["code"] == "push_subscription_invalid"

    assert (await client.post("/api/push/unsubscribe", json={"device_id": "phone-1"})).status == 200
    missing = await client.post("/api/push/unsubscribe", json={"device_id": "phone-1"})
    assert missing.status == 404
    assert (await missing.json())["error"]["code"] == "push_not_subscribed"


def test_the_push_routes_are_not_exempt_from_auth() -> None:
    """The MC-4 rail, extended.

    A subscription endpoint reachable without a session would let anyone who can reach the
    gateway register a destination for its pings — and read the VAPID public key while they
    were there. Asserted as a shape rather than a point-in-time grep so a future addition to
    either bypass list reds here.
    """
    from personalclaw.dashboard import token_auth

    for path in ("/api/push", "/api/push/subscribe", "/api/push/unsubscribe"):
        assert path not in getattr(token_auth, "_BYPASS_EXACT", ())
        assert not any(
            path.startswith(prefix) for prefix in getattr(token_auth, "_BYPASS_PREFIXES", ())
        )


def test_the_status_route_never_carries_the_private_key(home: Path) -> None:
    """A vacuity floor for the assertion above: the key EXISTS and is still absent."""
    public_key, private_key = push.push_init()
    status = push.push_status()
    assert status["vapid_public_key"] == public_key
    assert private_key not in json.dumps(status)
    assert private_key != public_key  # the two halves are genuinely different strings


def test_base64url_round_trips_without_padding(home: Path) -> None:
    """The keys travel unpadded (`applicationServerKey` and the `k=` param both want that)."""
    raw = bytes(range(65))
    encoded = push._b64url(raw)
    assert "=" not in encoded
    assert push._b64url_decode(encoded) == raw
    assert base64.urlsafe_b64decode(encoded + "===") == raw
