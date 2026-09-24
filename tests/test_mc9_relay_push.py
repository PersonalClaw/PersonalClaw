"""MOBILE-COMPANION `MC-9` — the ``relay`` backend: ids-only pings through a stateless relay.

The security promise is MC-5's, extended one hop: **what leaves this process for the relay
is a routing envelope around the same two ids — platform, token, kind, item_id — and
nothing else.** As in ``test_mc5_push_to_approval.py``, the leak tests assert on the bytes
handed to the HTTP layer (``push._post`` patched), downstream of every composer, not on the
guard that happens to sit next to the call site.

What is NOT here: the relay service itself (its content-free log audit lives in the
push-relay repo's own suite) and the native APNs/FCM leg (a store app on a physical
handset — the same ENVIRONMENT LIMIT MC-5 names for its locked-phone leg).

Isolation follows MC-5's rule exactly: ``PERSONALCLAW_HOME`` is the lever, asserted per
test, never a ``config_dir`` monkeypatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw import push
from personalclaw.config.loader import config_dir

# ── Fixtures (MC-5's, verbatim in shape) ─────────────────────────────────────


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
    """Capture every outbound POST at the HTTP boundary (see MC-5 on why ``_post``)."""
    calls: list[dict[str, object]] = []

    def fake_post(url: str, body: bytes, headers: dict[str, str]) -> int:
        calls.append({"url": url, "body": body, "headers": dict(headers)})
        return 201

    monkeypatch.setattr(push, "_post", fake_post)
    return calls


def _configure_relay(home: Path, url: str = "https://relay.example/ping") -> None:
    (home / "config.json").write_text(
        json.dumps({"mobile": {"push_backend": "relay", "relay_url": url}})
    )


# ── The token store ──────────────────────────────────────────────────────────


def test_register_round_trips_and_reregistering_replaces_the_row(home: Path) -> None:
    push.register_relay_token("phone-1", "ios", "tok-A")
    assert push.load_relay_tokens()["phone-1"]["token"] == "tok-A"
    # Platforms rotate tokens; keeping both rows would ping the phone twice.
    push.register_relay_token("phone-1", "ios", "tok-B")
    rows = push.load_relay_tokens()
    assert len(rows) == 1
    assert rows["phone-1"]["token"] == "tok-B"
    assert push.unregister_relay_token("phone-1") is True
    assert push.unregister_relay_token("phone-1") is False
    assert push.load_relay_tokens() == {}


def test_the_platform_vocabulary_is_closed(home: Path) -> None:
    """The relay's whole job is picking APNs vs FCM — an open string just defers the error."""
    with pytest.raises(ValueError):
        push.register_relay_token("phone-1", "windows", "tok")
    with pytest.raises(ValueError):
        push.register_relay_token("phone-1", "", "tok")
    with pytest.raises(ValueError):
        push.register_relay_token("phone-1", "ios", "")
    # Case is normalized, not refused: the shell reports Capacitor's platform string.
    push.register_relay_token("phone-1", "Android", "tok")
    assert push.load_relay_tokens()["phone-1"]["platform"] == "android"


def test_only_the_sender_fields_are_stored(home: Path) -> None:
    push.register_relay_token("phone-1", "ios", "tok")
    row = push.load_relay_tokens()["phone-1"]
    assert set(row) == {"platform", "token", "created_at"}


# ── The wire body ────────────────────────────────────────────────────────────


def test_the_relay_body_is_the_envelope_and_nothing_else(
    home: Path, sent: list[dict[str, object]]
) -> None:
    """Pinned as a SET at the HTTP boundary: platform, token, and the two-id payload."""
    payload = push.content_free_payload("approval", "apr-1")
    assert push.send_relay(payload, "https://relay.example/ping", "ios", "tok-A") is True
    assert len(sent) == 1
    body = json.loads(sent[0]["body"])
    assert set(body) == {"platform", "token", "payload"}
    assert body["platform"] == "ios"
    assert body["token"] == "tok-A"
    assert body["payload"] == {"kind": "approval", "item_id": "apr-1"}


def test_a_content_laden_payload_never_reaches_the_wire(
    home: Path, sent: list[dict[str, object]]
) -> None:
    """The gate sits INSIDE the sender, so no caller can compose content past it."""
    with pytest.raises(push.PushPayloadError):
        push.send_relay(
            {"kind": "approval", "item_id": "apr-1", "title": "Deploy prod?"},
            "https://relay.example/ping",
            "ios",
            "tok",
        )
    assert sent == []


def test_the_relay_refuses_a_plaintext_url(home: Path, sent: list[dict[str, object]]) -> None:
    """Config keeps the URL verbatim, which makes the sender the fail-closed point."""
    payload = push.content_free_payload("approval", "apr-1")
    assert push.send_relay(payload, "http://relay.example/ping", "ios", "tok") is False
    assert sent == []


# ── The plan-42 fan-out ──────────────────────────────────────────────────────


def test_deliver_fans_out_over_every_registered_device(
    home: Path, sent: list[dict[str, object]]
) -> None:
    _configure_relay(home)
    push.register_relay_token("phone-1", "ios", "tok-ios")
    push.register_relay_token("tablet-1", "android", "tok-android")
    assert push.deliver("approval", "apr-9") == 2
    bodies = [json.loads(c["body"]) for c in sent]
    assert {(b["platform"], b["token"]) for b in bodies} == {
        ("ios", "tok-ios"),
        ("android", "tok-android"),
    }
    for b in bodies:
        assert b["payload"] == {"kind": "approval", "item_id": "apr-9"}
    assert all(c["url"] == "https://relay.example/ping" for c in sent)


def test_a_relay_backend_without_a_url_delivers_nothing(
    home: Path, sent: list[dict[str, object]]
) -> None:
    (home / "config.json").write_text(json.dumps({"mobile": {"push_backend": "relay"}}))
    push.register_relay_token("phone-1", "ios", "tok")
    assert push.deliver("approval", "apr-1") == 0
    assert sent == []


def test_the_backend_switch_keeps_the_transports_apart(
    home: Path, sent: list[dict[str, object]]
) -> None:
    """A relay-configured install on the ntfy backend must not also ping the relay."""
    (home / "config.json").write_text(
        json.dumps(
            {
                "mobile": {
                    "push_backend": "ntfy",
                    "ntfy_topic_url": "https://ntfy.example/pc",
                    "relay_url": "https://relay.example/ping",
                }
            }
        )
    )
    push.register_relay_token("phone-1", "ios", "tok")
    assert push.deliver("approval", "apr-1") == 1
    assert len(sent) == 1
    assert sent[0]["url"] == "https://ntfy.example/pc"


# ── The routes ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_relay_routes_round_trip(home: Path) -> None:
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.push import register_push_routes

    _configure_relay(home)
    app = web.Application()
    register_push_routes(app)
    server = TestServer(app)
    client = TestClient(server, cookie_jar=aiohttp.DummyCookieJar())
    await client.start_server()
    try:
        status = await (await client.get("/api/push")).json()
        assert status["relay_configured"] is True
        assert status["relay_devices"] == []

        first = await client.post(
            "/api/push/relay-register",
            json={"device_id": "phone-1", "platform": "ios", "token": "tok-A"},
        )
        assert first.status == 200
        # The same one-switch statement as subscribe: registering IS "wake me".
        assert (await first.json())["approval_rule_written"] is True
        after = await (await client.get("/api/push")).json()
        assert after["relay_devices"] == ["phone-1"]
        assert after["approval_targeted"] is True
        again = await client.post(
            "/api/push/relay-register",
            json={"device_id": "phone-1", "platform": "ios", "token": "tok-B"},
        )
        assert (await again.json())["approval_rule_written"] is False

        bad = await client.post(
            "/api/push/relay-register",
            json={"device_id": "phone-2", "platform": "windows", "token": "tok"},
        )
        assert bad.status == 400
        assert (await bad.json())["error"]["code"] == "push_relay_registration_invalid"
        missing_token = await client.post(
            "/api/push/relay-register", json={"device_id": "phone-2", "platform": "ios"}
        )
        assert missing_token.status == 400

        gone = await client.post("/api/push/relay-unregister", json={"device_id": "phone-1"})
        assert gone.status == 200
        not_there = await client.post("/api/push/relay-unregister", json={"device_id": "phone-1"})
        assert not_there.status == 404
        assert (await not_there.json())["error"]["code"] == "push_relay_not_registered"
    finally:
        await client.close()


def test_the_relay_routes_are_not_exempt_from_auth() -> None:
    """MC-5's rail, extended to the two new routes: a registration endpoint reachable
    without a session would let anyone who can reach the gateway point pings at their
    own device token."""
    from personalclaw.dashboard import token_auth

    for path in ("/api/push/relay-register", "/api/push/relay-unregister"):
        assert path not in getattr(token_auth, "_BYPASS_EXACT", ())
        assert not any(
            path.startswith(prefix) for prefix in getattr(token_auth, "_BYPASS_PREFIXES", ())
        )


def test_the_audit_line_never_carries_the_token(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token is a capability against the vendor push service — SEL gets the device
    id and platform, never the token (the webpush-endpoint discipline, applied)."""
    lines: list[str] = []

    class _Sel:
        def log_api_access(self, **kw: object) -> None:
            lines.append(json.dumps({k: str(v) for k, v in kw.items()}))

    import personalclaw.sel as sel_mod

    monkeypatch.setattr(sel_mod, "sel", lambda: _Sel())

    import asyncio

    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.push import register_push_routes

    async def go() -> None:
        app = web.Application()
        register_push_routes(app)
        server = TestServer(app)
        client = TestClient(server, cookie_jar=aiohttp.DummyCookieJar())
        await client.start_server()
        try:
            await client.post(
                "/api/push/relay-register",
                json={"device_id": "phone-1", "platform": "ios", "token": "SECRET-TOKEN"},
            )
        finally:
            await client.close()

    asyncio.run(go())
    assert lines, "the registration route must audit"
    assert all("SECRET-TOKEN" not in line for line in lines)
