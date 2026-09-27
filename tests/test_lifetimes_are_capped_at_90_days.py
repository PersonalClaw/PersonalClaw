"""No sign-in, link or token lasts longer than 90 days (ledger 285).

Measured on main after #3727: ``MAX_SESSION_TTL_SECS`` was a YEAR, and every way to ask for a
lifetime quietly accepted anything up to it — ``personalclaw token --ttl 8760h``,
``/api/token/local?ttl=8760h``, ``generate_token(…, 365 days)`` from an app, and
``auth.session_ttl`` set to ``365d`` in config. A long-lived credential is replaced at least
every 90 days, so that is the limit now, and a request for longer is REFUSED with a sentence
naming the limit and why — never clamped, because a clamp mints a credential the caller did not
ask for and tells them nothing. A config file that already says longer is the one
exception, by necessity: it is applied as 90 days (a hand-edited file must not brick the box) and
the doctor says so.

Two things main got wrong on the way, both measured here: ``personalclaw run`` asked the token
endpoint for ``ttl=3600``, which is not a duration the endpoint parses, and got the endpoint's
default instead of its hour; and the endpoint answered any unparseable ``ttl`` that way.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import secrets
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import cli_server
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers import core as core_h

LIMIT = 90 * 86400
PORT = 10000
SECRET = "synthetic-local-secret"
CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(cli_server, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"auth": {}}), encoding="utf-8")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()


def _claims(token: str) -> dict:
    return json.loads(token_auth._b64url_decode(token.split(".")[0]))


def _says_the_limit_and_why(sentence: str, requested: str) -> None:
    assert "90 days" in sentence, sentence
    assert requested in sentence, f"the refusal names what was asked for: {sentence}"
    assert "anyone who copies" in sentence, f"and why the limit exists: {sentence}"


# ── the limit itself ──────────────────────────────────────────────────────────────────────────


def test_the_limit_is_90_days_for_the_core_and_for_apps():
    from personalclaw.sdk import channel

    assert token_auth.MAX_SESSION_TTL_SECS == LIMIT
    assert channel.MAX_SESSION_TTL_SECS == LIMIT, "apps read the limit through the SDK"


def test_an_app_asking_for_longer_is_refused_with_the_sentence():
    """``generate_token`` is the SDK's door: an app that asks for a year gets the sentence to
    show its user, and no token."""
    from personalclaw.sdk.channel import generate_token

    before = set(ss.load_session_records())
    with pytest.raises(ValueError) as refused:
        generate_token("slack-user", 365 * 86400)
    _says_the_limit_and_why(str(refused.value), "365 days")
    assert set(ss.load_session_records()) == before, "nothing was minted"
    assert _claims(generate_token("slack-user", LIMIT))["session_exp"] > time.time()


def test_a_lifetime_is_read_in_minutes_hours_or_days_and_never_clamped():
    """One grammar for every place a lifetime is asked for, the same one config uses."""
    assert token_auth.parse_duration("30m") == 1800
    assert token_auth.parse_duration("20h") == 72000
    assert token_auth.parse_duration("90d") == LIMIT
    # Longer is returned as asked, so the caller can refuse it: a clamp here was the bug.
    assert token_auth.parse_duration("8760h") == 365 * 86400
    for bad in ("", "3600", "1y", "-1h", "1.5h", "h", "0x10h"):
        assert token_auth.parse_duration(bad) is None, bad


# ── personalclaw token ───────────────────────────────────────────────────────────────────────


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _run_token(monkeypatch, tmp_path, capsys, ttl: str) -> tuple[int, str, list[str]]:
    (tmp_path / ".local_secret").write_text(SECRET + "\n")
    monkeypatch.setenv("PERSONALCLAW_PORT", str(PORT))
    asked: list[str] = []

    def _urlopen(req, timeout=5):
        asked.append(req.full_url)
        return _Response(
            json.dumps({"token": "t.x", "expires_in": 60, "expires_at": time.time() + 60}).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    code = 0
    try:
        cli_server._token(argparse.Namespace(ttl=ttl, port=None))
    except SystemExit as exit_:
        code = int(exit_.code or 0)
    captured = capsys.readouterr()
    return code, captured.out + captured.err, asked


@pytest.mark.parametrize(("ttl", "named"), [("8760h", "365 days"), ("91d", "91 days")])
def test_personalclaw_token_refuses_longer_than_90_days(monkeypatch, tmp_path, capsys, ttl, named):
    code, said, asked = _run_token(monkeypatch, tmp_path, capsys, ttl)
    assert code == 1
    _says_the_limit_and_why(said, named)
    assert not asked, "nothing was asked of the gateway"


def test_personalclaw_token_accepts_90_days_in_days(monkeypatch, tmp_path, capsys):
    code, said, asked = _run_token(monkeypatch, tmp_path, capsys, "90d")
    assert code == 0, said
    assert asked and asked[0].endswith("ttl=90d")


# ── the token endpoint ───────────────────────────────────────────────────────────────────────


def _token_app() -> web.Application:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app["local_secret"] = SECRET
    app.router.add_get("/api/token/local", core_h.api_token_local)
    return app


async def _local(client: TestClient, ttl: str | None) -> tuple[int, dict]:
    query = "" if ttl is None else f"?ttl={ttl}"
    resp = await client.get(f"/api/token/local{query}", headers={"X-Local-Secret": SECRET})
    return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_the_token_endpoint_refuses_longer_than_90_days():
    async with TestClient(TestServer(_token_app())) as client:
        before = set(ss.load_session_records())
        status, body = await _local(client, "2161h")
        assert status == 400
        assert body["error"]["code"] == "token_ttl_too_long"
        _says_the_limit_and_why(body["error"]["message"], "2161 hours")
        assert set(ss.load_session_records()) == before, "nothing was minted"

        status, body = await _local(client, "90d")
        assert status == 200 and body["expires_in"] == LIMIT


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", ["3600", "forever", "1y", "0h"])
async def test_an_unreadable_lifetime_is_refused_not_replaced_with_a_default(ttl):
    """Main answered ``?ttl=3600`` with a 20-hour token: the caller asked for something else and
    was told nothing."""
    async with TestClient(TestServer(_token_app())) as client:
        status, body = await _local(client, ttl)
        assert status == 400, body
        assert body["error"]["code"] == "token_ttl_invalid"
        assert "30m" in body["error"]["message"] and "20h" in body["error"]["message"]


@pytest.mark.asyncio
async def test_personalclaw_run_gets_the_hour_it_asks_for(tmp_path):
    from personalclaw import cli_run

    (tmp_path / ".local_secret").write_text(SECRET)
    async with TestServer(_token_app(), port=0) as server:
        token = await asyncio.to_thread(cli_run.mint_local_token, server.port)
    claims = _claims(token)
    assert claims["session_exp"] - claims["iat"] == pytest.approx(3600, abs=5)


# ── auth.session_ttl ─────────────────────────────────────────────────────────────────────────


def _config_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.mark.asyncio
async def test_config_refuses_a_session_lifetime_over_90_days(tmp_path):
    cfg = tmp_path / "config.json"
    with patch("personalclaw.config.loader.config_path", return_value=cfg):
        async with TestClient(TestServer(_config_app())) as client:
            body = {"path": "auth.session_ttl", "value": "365d", "confirm": True}
            resp = await client.patch("/api/config/personalclaw", json=body)
            assert resp.status == 400
            error = (await resp.json())["error"]
            _says_the_limit_and_why(error if isinstance(error, str) else error["message"], "365d")
            assert "session_ttl" not in json.loads(cfg.read_text())["auth"], "nothing written"

            body["value"] = "90d"
            resp = await client.patch("/api/config/personalclaw", json=body)
            assert resp.status == 200, await resp.text()
            assert json.loads(cfg.read_text())["auth"]["session_ttl"] == "90d"


def test_personalclaw_config_set_refuses_it_too(tmp_path, capsys):
    from personalclaw import cli_config

    cfg = tmp_path / "config.json"
    with patch("personalclaw.config.loader.config_path", return_value=cfg):
        with pytest.raises(SystemExit):
            cli_config._config_cmd(
                argparse.Namespace(
                    config_action="set", key="auth.session_ttl", value="120d", file=None
                )
            )
    said = capsys.readouterr()
    _says_the_limit_and_why(said.out + said.err, "120d")
    assert "session_ttl" not in json.loads(cfg.read_text())["auth"]


def test_an_existing_config_over_90_days_is_applied_as_90_days():
    for configured in ("365d", "8760h", "91d"):
        auth_cfg = SimpleNamespace(session_ttl=configured)
        assert token_auth.browser_session_ttl(auth_cfg) == LIMIT, configured
        from personalclaw import gateway

        minted = gateway.mint_startup_token(token_auth.ISSUER_READY, auth_cfg)
        assert minted.lifetime_secs == LIMIT, "the harness token (--json-ready) follows it"


@pytest.mark.asyncio
async def test_the_doctor_reports_an_existing_config_over_90_days(tmp_path):
    from personalclaw.resilience import doctor

    (tmp_path / "config.json").write_text(json.dumps({"auth": {"session_ttl": "365d"}}))
    doctor._register_builtin_probes()
    probe = next((p for p in doctor.all_probes() if p.id == "security.session_lifetime"), None)
    assert probe is not None, "the Doctor has no row for a sign-in lifetime over the limit"
    result = await probe.run(doctor.DoctorContext(home=tmp_path))
    assert result.ok is False
    assert "365d" in result.detail and "90 days" in result.detail, result.detail
    assert "90d" in result.remedy, result.remedy

    (tmp_path / "config.json").write_text(json.dumps({"auth": {"session_ttl": "30d"}}))
    assert (await probe.run(doctor.DoctorContext(home=tmp_path))).ok is True


def test_personalclaw_doctor_says_so_too(tmp_path, monkeypatch, capsys):
    import urllib.error

    from personalclaw.cli_doctor import _doctor

    (tmp_path / "config.json").write_text(json.dumps({"auth": {"session_ttl": "365d"}}))
    for var in ("PERSONALCLAW_AUTH_MODE", "PERSONALCLAW_DEV_NO_AUTH"):
        monkeypatch.delenv(var, raising=False)
    ran = SimpleNamespace(returncode=0, stdout="Python 3.13.14", stderr="")
    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/bin/{b}"),
        patch("subprocess.run", return_value=ran),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
    ):
        with pytest.raises(SystemExit):
            _doctor()
    out = capsys.readouterr().out
    line = next((row for row in out.splitlines() if "session_ttl" in row), "")
    assert "365d" in line and "90 days" in line, out


# ── a credential minted before the limit ─────────────────────────────────────────────────────


def _legacy_token(issued_days_ago: float, lasts_days: float) -> tuple[str, str]:
    """A token as a build without the limit minted it: signed by this gateway's key, recorded in
    the store without a device block (the shape rows had before #3727), lasting *lasts_days*."""
    now = time.time()
    iat = now - issued_days_ago * 86400
    nonce = secrets.token_hex(8)
    payload = {
        "sub": "local-app",
        "exp": iat + 86400,
        "session_exp": iat + lasts_days * 86400,
        "iat": iat,
        "nonce": nonce,
    }
    raw = json.dumps(payload).encode()
    token = f"{token_auth._b64url_encode(raw)}.{token_auth._sign(raw)}"
    rows = json.loads(ss.sessions_path().read_text()) if ss.sessions_path().exists() else {}
    rows.setdefault("sessions", {})[nonce] = {"exp": payload["session_exp"], "issuer": "unknown"}
    ss.sessions_path().write_text(json.dumps(rows))
    return token, nonce


def _status_app() -> web.Application:
    async def _status(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app.router.add_get("/api/status", _status)
    return app


@pytest.mark.asyncio
async def test_a_year_long_token_from_before_stops_90_days_after_it_was_issued():
    stale, _ = _legacy_token(issued_days_ago=91, lasts_days=365)
    fresh, _ = _legacy_token(issued_days_ago=10, lasts_days=365)
    async with TestClient(TestServer(_status_app())) as client:
        ok = await client.get("/api/status", cookies={f"pc_token_{PORT}": fresh})
        assert ok.status == 200, "inside its first 90 days it still works"
        resp = await client.get(
            "/api/status", cookies={f"pc_token_{PORT}": stale}, headers={"User-Agent": CHROME}
        )
        assert resp.status == 403
        error = (await resp.json())["error"]
        assert error["code"] == "session_expired", error
        assert "lasted 90 days" in error["message"], error["message"]


def test_the_list_never_shows_a_sign_in_lasting_beyond_the_limit():
    _legacy_token(issued_days_ago=10, lasts_days=365)
    now = time.time()
    device = ss.DeviceInfo(id=ss.new_device_id(), minted_at=now - 5 * 86400)
    nonce = secrets.token_hex(8)
    ss.remember_session(nonce, now + 300 * 86400, issuer=ss.ISSUER_TOKEN, device=device)
    for row_nonce, record in ss.load_session_records().items():
        assert record.expiry <= time.time() + LIMIT + 5, row_nonce
    assert ss.load_session_records()[nonce].expiry == pytest.approx(device.minted_at + LIMIT)
