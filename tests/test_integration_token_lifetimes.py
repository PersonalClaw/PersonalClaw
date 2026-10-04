"""An integration's token has a lifetime, a revoke, and a refusal that says why (ledger 317a).

The tokens an external agent reaches an inbound surface with — a surface token, and a registered
client's token — had no lifetime at all, so one pasted into an editor's config long ago still
worked. They are now under the policy a dashboard sign-in is under:

* a stated lifetime of at most 90 days, longer refused, never shortened;
* a sign-in and a sign-out row in the security log, in the shape every sign-in writes;
* listed in Settings → Devices, with a revoke;
* past its lifetime (or revoked, or replaced), a refusal sentence of its own kind — while the
  code a script branches on stays ``unauthorized`` for every refusal;
* a token from before lifetimes existed ends 90 days after it was issued (a client) or first
  seen (a surface token, whose issue time was never recorded).

Every surface is driven over HTTP, because the refusal a caller receives is the behaviour; a
unit test of the registry alone would pass with a surface that never consults it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from signed_in_sessions import without_sign_in

from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.inbound import a2a, auth, bridge
from personalclaw.inbound import caps as caps_mod
from personalclaw.inbound import capture_proxy
from personalclaw.inbound import clients as clients_mod
from personalclaw.inbound import mcp_http, openai_dialect

_SURFACES = ("openai", "mcp", "a2a", "capture", "bridge")
_DAY = 86400
_UNIFORM = HTTP_ERROR_CODES["unauthorized"]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, unset_env):
    """An isolated home per test: this suite mints credentials and writes the stores beside
    them, which must never reach the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    # Registered before the test: `save_credential` mirrors into os.environ behind monkeypatch's
    # back.
    unset_env(*(auth.token_env_key(surface) for surface in _SURFACES))
    caps_mod.reset_for_tests()
    clients_mod.reset_for_tests()
    yield
    caps_mod.reset_for_tests()
    clients_mod.reset_for_tests()


@pytest.fixture
def sel_rows(monkeypatch) -> list[dict]:
    rows: list[dict] = []

    class _Sel:
        def log_api_access(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("personalclaw.sel.sel", lambda: _Sel())
    return rows


def _cfg(monkeypatch, surface: str = "mcp") -> None:
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(enabled=True, **{surface: Surface(enabled=True)})
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))


async def _never(*_a, **_k):  # the OpenAI dialect's turn runner and agent mover
    raise AssertionError("a refused request ran a turn")


_ROUTES = {
    "mcp": ("POST", "/mcp"),
    "capture": ("POST", capture_proxy.ROUTE_OPENAI),
    "a2a": ("POST", a2a.ROUTE_TASKS),
    "openai": ("GET", openai_dialect.ROUTE_MODELS),
    "bridge": ("GET", "/actions"),
}


async def _surface_client(surface: str) -> TestClient:
    app = web.Application()
    if surface == "mcp":
        mcp_http.mount(app)
    elif surface == "capture":
        capture_proxy.register_routes(app)
    elif surface == "a2a":
        a2a.register_routes(app)
    elif surface == "openai":
        openai_dialect.register_routes(app, turn_runner=_never, agent_mover=_never)
    else:
        app.router.add_get("/actions", bridge.handle_actions)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _call(surface: str, token: str) -> tuple[int, dict]:
    """One request to *surface* with *token* as its bearer: ``(status, error object)``."""
    method, path = _ROUTES[surface]
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"} if surface == "mcp" else {}
    client = await _surface_client(surface)
    try:
        resp = await client.request(
            method,
            path,
            data=json.dumps(body) if method == "POST" else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            payload = await resp.json()
        except Exception:  # noqa: BLE001 — a non-JSON success body has no error object
            payload = {}
        return resp.status, (payload.get("error") or {}) if isinstance(payload, dict) else {}
    finally:
        await client.close()


def _registry() -> dict:
    from personalclaw.inbound import tokens

    return json.loads(tokens.registry_path().read_text(encoding="utf-8"))


def _age_surface_token(token: str, by_secs: float) -> None:
    """Move *token*'s record *by_secs* into the past — its lifetime has run that much longer."""
    from personalclaw.inbound import tokens

    data = _registry()
    row = data["tokens"][tokens.token_hash(token)]
    row["issued_at"] -= by_secs
    row["expires_at"] -= by_secs
    tokens.registry_path().write_text(json.dumps(data), encoding="utf-8")


def _age_client(client_id: str, by_secs: float, *, drop_expiry: bool = False) -> None:
    """Move a client's issue (and expiry) *by_secs* into the past; *drop_expiry* makes it a
    client registered before lifetimes existed."""
    path = clients_mod.clients_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    row = data[client_id]
    created = datetime.fromisoformat(row["created_at"]) - timedelta(seconds=by_secs)
    row["created_at"] = created.isoformat()
    if drop_expiry:
        row.pop("expires_at", None)
    else:
        row["expires_at"] = float(row["expires_at"]) - by_secs
    path.write_text(json.dumps(data), encoding="utf-8")


# ══ A surface token has a lifetime ═══════════════════════════════════════════════════


class TestSurfaceTokenLifetime:
    def test_a_created_token_lasts_90_days_unless_asked_for_less(self):
        from personalclaw.inbound import tokens

        before = time.time()
        default = auth.create_surface_token("mcp")
        short = auth.create_surface_token("capture", 3600)
        rows = _registry()["tokens"]
        mcp = rows[tokens.token_hash(default)]
        capture = rows[tokens.token_hash(short)]
        assert mcp["expires_at"] - mcp["issued_at"] == 90 * _DAY
        assert mcp["issued_at"] >= before and mcp["found"] is False
        assert capture["expires_at"] - capture["issued_at"] == 3600
        # The registry holds hashes and lifetimes, never a token.
        raw = tokens.registry_path().read_text(encoding="utf-8")
        assert default not in raw and short not in raw

    def test_a_lifetime_over_90_days_is_refused_not_shortened_and_nothing_is_stored(self):
        with pytest.raises(ValueError, match="at most 90 days, and 91 days is longer"):
            auth.create_surface_token("mcp", 91 * _DAY)
        assert auth.load_surface_token("mcp") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("surface", _SURFACES)
    async def test_every_surface_refuses_an_expired_token_and_says_why(self, monkeypatch, surface):
        _cfg(monkeypatch, surface)
        token = auth.create_surface_token(surface, 3600)
        _age_surface_token(token, 7200)
        status, error = await _call(surface, token)
        assert status == 401
        assert error.get("code") == "unauthorized", "scripts branch on one uniform code"
        message = str(error.get("message") or "")
        assert "token stopped working" in message and "1 hour after it was created" in message
        assert f"personalclaw inbound token create {surface} --rotate" in message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("surface", _SURFACES)
    async def test_a_token_this_gateway_never_issued_gets_only_the_uniform_refusal(
        self, monkeypatch, surface
    ):
        """The sentence is for the holder of a real token. A guess learns nothing more."""
        _cfg(monkeypatch, surface)
        auth.create_surface_token(surface)
        status, error = await _call(surface, "x" * 64)
        assert status == 401 and error.get("code") == "unauthorized"
        expected = "Incorrect API key provided." if surface == "openai" else _UNIFORM
        assert error.get("message") == expected

    @pytest.mark.asyncio
    async def test_a_live_token_still_works(self, monkeypatch):
        """The floor under the refusals above: a surface that refused everything passes them."""
        _cfg(monkeypatch, "mcp")
        token = auth.create_surface_token("mcp")
        status, _error = await _call("mcp", token)
        assert status == 200

    def test_a_token_never_seen_is_recorded_when_first_seen_and_lasts_90_days_from_then(
        self, monkeypatch, sel_rows
    ):
        """A token from before lifetimes existed — or set in Settings → Secrets, or injected by
        the environment — was never given an issue time, so it gets 90 days from first sight."""
        from personalclaw.inbound import tokens

        legacy = "L" * 64
        monkeypatch.setenv(auth.token_env_key("mcp"), legacy)
        before = time.time()
        assert auth.verify_bearer("mcp", legacy) is True
        row = _registry()["tokens"][tokens.token_hash(legacy)]
        assert row["found"] is True and row["issued_at"] >= before
        assert row["expires_at"] - row["issued_at"] == 90 * _DAY
        signed_in = [r for r in sel_rows if r["operation"] == "session_signed_in"]
        assert len(signed_in) == 1 and signed_in[0]["metadata"]["found"] is True
        # Seeing it again records nothing new.
        assert auth.verify_bearer("mcp", legacy) is True
        assert len([r for r in sel_rows if r["operation"] == "session_signed_in"]) == 1

    @pytest.mark.asyncio
    async def test_an_expired_found_token_says_it_was_first_seen_not_created(self, monkeypatch):
        _cfg(monkeypatch, "mcp")
        legacy = "L" * 64
        monkeypatch.setenv(auth.token_env_key("mcp"), legacy)
        assert auth.verify_bearer("mcp", legacy) is True
        _age_surface_token(legacy, 91 * _DAY)
        status, error = await _call("mcp", legacy)
        assert status == 401
        assert "90 days after it was first seen" in error["message"]

    def test_a_value_that_briefly_cannot_be_read_keeps_its_lifetime(self, monkeypatch):
        """A locked keychain or an unset variable hides the configured value for a while. That
        must not end its record, or the value would be found as new, with a fresh 90 days, the
        moment it could be read again."""
        from personalclaw.inbound import tokens

        token = "T" * 64
        monkeypatch.setenv(auth.token_env_key("mcp"), token)
        assert auth.verify_bearer("mcp", token) is True
        _age_surface_token(token, 91 * _DAY)
        monkeypatch.delenv(auth.token_env_key("mcp"))
        assert tokens.surface_token("mcp", auth.load_surface_token("mcp")) is None
        tokens.integration_rows()  # the Devices list reads while the value is hidden
        monkeypatch.setenv(auth.token_env_key("mcp"), token)
        assert auth.verify_bearer("mcp", token) is False
        assert tokens.surface_token("mcp", token).state() == "expired"

    def test_an_unreadable_registry_refuses_every_surface_token(self):
        """Recording each token as new would give it a fresh 90 days, so a registry that
        cannot be read refuses instead."""
        from personalclaw.inbound import tokens

        token = auth.create_surface_token("mcp")
        tokens.registry_path().write_text("{not json", encoding="utf-8")
        assert auth.verify_bearer("mcp", token) is False
        rows, problem = tokens.integration_rows()
        assert not [r for r in rows if r["kind"] == "surface"]
        assert "every surface token is refused" in problem


# ══ Revoked and replaced ═════════════════════════════════════════════════════════════


class TestRevokedAndReplaced:
    @pytest.mark.asyncio
    async def test_a_revoked_surface_token_is_refused_and_told_so_while_clients_keep_working(
        self, monkeypatch
    ):
        from personalclaw.inbound import tokens

        _cfg(monkeypatch, "mcp")
        token = auth.create_surface_token("mcp")
        _client, client_token = clients_mod.create_client("ide", surfaces=["mcp"])
        assert tokens.end_integration("surface-mcp") is True
        status, error = await _call("mcp", token)
        assert status == 401 and error["code"] == "unauthorized"
        assert "This MCP token was revoked today at" in error["message"]
        # Revoking the surface's token refuses THAT token; the surface stays on.
        status, _error = await _call("mcp", client_token)
        assert status == 200
        assert tokens.end_integration("surface-mcp") is False, "already revoked"

    def test_a_revoked_token_stays_refused_when_the_environment_sets_it_again(self, monkeypatch):
        from personalclaw.inbound import tokens

        token = "R" * 64
        monkeypatch.setenv(auth.token_env_key("mcp"), token)
        assert tokens.revoke_surface_token("mcp", token) is True
        monkeypatch.delenv(auth.token_env_key("mcp"))
        monkeypatch.setenv(auth.token_env_key("mcp"), token)  # the next start re-injects it
        assert auth.verify_bearer("mcp", token) is False

    @pytest.mark.asyncio
    async def test_a_rotated_token_tells_its_old_holder_it_was_replaced(self, monkeypatch):
        _cfg(monkeypatch, "mcp")
        old = auth.create_surface_token("mcp")
        new = auth.create_surface_token("mcp")
        status, error = await _call("mcp", old)
        assert status == 401 and error["code"] == "unauthorized"
        assert "was replaced by a newer one today at" in error["message"]
        status, _error = await _call("mcp", new)
        assert status == 200

    def test_only_what_can_no_longer_matter_is_forgotten(self):
        """A revoked or expired token may still be the configured value, so its record is never
        dropped; a replaced one is kept a year, a revoked client's a week."""
        from personalclaw.inbound import tokens

        now = time.time()
        surface = {"kind": "surface", "surface": "mcp", "issued_at": 1.0, "expires_at": 2.0}
        data = {
            "version": 1,
            "tokens": {
                "replaced-long-ago": {**surface, "replaced_at": now - 366 * _DAY},
                "replaced-lately": {**surface, "replaced_at": now - 10 * _DAY},
                "revoked-long-ago": {**surface, "revoked_at": now - 3650 * _DAY},
                "expired-long-ago": dict(surface),
                "client-long-ago": {"kind": "client", "at": now - 8 * _DAY},
                "client-lately": {"kind": "client", "at": now - _DAY},
            },
        }
        tokens._prune(data, now)
        assert set(data["tokens"]) == {
            "replaced-lately",
            "revoked-long-ago",
            "expired-long-ago",
            "client-lately",
        }

    def test_writers_at_once_lose_nothing(self):
        """The CLI and the gateway both write the record; each change is one locked
        read-modify-write, so none overwrites another."""
        import threading

        from personalclaw.inbound import tokens

        values = {s: s[0].upper() * 64 for s in _SURFACES}
        threads = [
            threading.Thread(target=tokens.issue_surface_token, args=(s, v, 3600))
            for s, v in values.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        recorded = {row["surface"] for row in _registry()["tokens"].values()}
        assert recorded == set(_SURFACES)

    def test_a_replaced_token_set_again_stays_refused(self, monkeypatch):
        """A token is replaced for a reason (it leaked, it was lost): pasted back by hand or
        restored from a backup, it is refused, not brought back to life."""
        from personalclaw.config.credentials import _dotenv_save_credentials
        from personalclaw.inbound import tokens

        key = auth.token_env_key("mcp")
        old = auth.create_surface_token("mcp")
        auth.create_surface_token("mcp")  # rotated
        _dotenv_save_credentials({key: old})  # set back by hand
        monkeypatch.setenv(key, old)
        assert auth.load_surface_token("mcp") == old
        assert auth.verify_bearer("mcp", old) is False
        assert tokens.surface_token("mcp", old).state() == "replaced"
        assert tokens.ending("mcp", old).reason == "mcp token replaced"

    def test_a_rotation_by_another_process_reaches_a_running_gateway_at_once(self, monkeypatch):
        """`personalclaw inbound token create --rotate` runs in its own process: it writes the
        store and records the old token as replaced, while the gateway's environment still
        holds the copy it read at startup. The old token must stop at once, and the new one
        work at once, without a restart."""
        from personalclaw.config.credentials import _dotenv_save_credentials
        from personalclaw.inbound import tokens

        key = auth.token_env_key("mcp")
        old = auth.create_surface_token("mcp")
        assert os.environ[key] == old  # the running process's copy
        new = "N" * 64
        _dotenv_save_credentials({key: new})  # what the other process wrote to the store
        tokens.issue_surface_token("mcp", new, 3600, actor="cli")
        assert os.environ[key] == old  # nothing has told this process yet
        assert auth.verify_bearer("mcp", old) is False
        assert auth.verify_bearer("mcp", new) is True
        assert os.environ[key] == new, "the stale copy is replaced as it is read"
        assert "was replaced by a newer one" in tokens.ending("mcp", old).sentence
        rows, _problem = tokens.integration_rows()
        assert [r["state"] for r in rows if r["id"] == "surface-mcp"] == ["live"]


# ══ The security log ═════════════════════════════════════════════════════════════════


class TestSecurityLog:
    def test_a_surface_token_signs_in_and_out_and_no_row_carries_it(self, sel_rows):
        from personalclaw.inbound import tokens

        first = auth.create_surface_token("mcp", 7 * _DAY)
        second = auth.create_surface_token("mcp")
        assert tokens.end_integration("surface-mcp", actor="owner") is True
        ops = [(r["operation"], r["metadata"].get("reason")) for r in sel_rows]
        assert ops == [
            ("session_signed_in", None),
            ("session_signed_out", "replaced"),
            ("session_signed_in", None),
            ("session_signed_out", "revoked"),
        ]
        signed_in = sel_rows[0]["metadata"]
        assert signed_in["session"] == "surface-mcp" and signed_in["kind"] == "surface_token"
        assert signed_in["issuer"] == "inbound" and signed_in["lifetime_secs"] == 7 * _DAY
        serialized = json.dumps(sel_rows)
        for secret in (first, second):
            assert secret not in serialized
            assert hashlib.sha256(secret.encode()).hexdigest() not in serialized

    def test_a_client_signs_in_with_its_lifetime_and_out_when_revoked(self, sel_rows):
        client, token = clients_mod.create_client("ide", surfaces=["mcp"], ttl_secs=30 * _DAY)
        clients_mod.revoke_client(client.client_id)
        ops = [r["operation"] for r in sel_rows]
        assert ops == ["session_signed_in", "session_signed_out"]
        assert sel_rows[0]["metadata"]["lifetime_secs"] == 30 * _DAY
        assert sel_rows[0]["metadata"]["session"] == f"client-{client.client_id}"
        assert sel_rows[1]["metadata"]["reason"] == "revoked"
        assert token not in json.dumps(sel_rows)


# ══ Client tokens ════════════════════════════════════════════════════════════════════


class TestClientTokenLifetime:
    def test_a_client_token_lasts_90_days_unless_asked_for_less_and_longer_is_refused(self):
        before = time.time()
        client, _token = clients_mod.create_client("ide", surfaces=["mcp"])
        assert before + 90 * _DAY <= client.expires_at <= time.time() + 90 * _DAY
        stored = json.loads(clients_mod.clients_path().read_text())[client.client_id]
        assert stored["expires_at"] == client.expires_at
        with pytest.raises(ValueError, match="at most 90 days"):
            clients_mod.create_client("too-long", surfaces=["mcp"], ttl_secs=91 * _DAY)
        assert "too-long" not in {c.label for c in clients_mod.load_clients().values()}

    def test_a_client_from_before_lifetimes_ends_90_days_after_it_was_created(self):
        old, old_token = clients_mod.create_client("old", surfaces=["mcp"])
        recent, recent_token = clients_mod.create_client("recent", surfaces=["mcp"])
        _age_client(old.client_id, 100 * _DAY, drop_expiry=True)
        _age_client(recent.client_id, 10 * _DAY, drop_expiry=True)
        matched, why = clients_mod.lookup_by_token(old_token, "mcp")
        assert matched is None and "expired" in why
        matched, why = clients_mod.lookup_by_token(recent_token, "mcp")
        assert matched is not None and why == ""
        created = datetime.fromisoformat(matched.created_at).timestamp()
        assert matched.expires_at == pytest.approx(created + 90 * _DAY)

    def test_a_client_from_before_lifetimes_whose_creation_cannot_be_read_has_expired(self):
        """Counting from now would give it a fresh 90 days every time it is read."""
        client, token = clients_mod.create_client("mangled", surfaces=["mcp"])
        path = clients_mod.clients_path()
        data = json.loads(path.read_text())
        data[client.client_id]["created_at"] = "not a time"
        data[client.client_id].pop("expires_at")
        path.write_text(json.dumps(data))
        matched, why = clients_mod.lookup_by_token(token, "mcp")
        assert matched is None and "expired" in why

    @pytest.mark.asyncio
    async def test_an_expired_client_is_told_so_under_the_uniform_code(self, monkeypatch):
        _cfg(monkeypatch, "mcp")
        auth.create_surface_token("mcp")
        client, token = clients_mod.create_client("ide", surfaces=["mcp"])
        _age_client(client.client_id, 91 * _DAY)
        status, error = await _call("mcp", token)
        assert status == 401 and error["code"] == "unauthorized"
        assert "The token for the “ide” client stopped working" in error["message"]
        assert "90 days after it was issued" in error["message"]
        assert "Register the client again" in error["message"]

    @pytest.mark.asyncio
    async def test_a_revoked_client_is_told_it_was_revoked(self, monkeypatch):
        _cfg(monkeypatch, "mcp")
        auth.create_surface_token("mcp")
        client, token = clients_mod.create_client("ide", surfaces=["mcp"])
        assert clients_mod.revoke_client(client.client_id) is True
        status, error = await _call("mcp", token)
        assert status == 401 and error["code"] == "unauthorized"
        assert "The token for the “ide” client was revoked today at" in error["message"]

    @pytest.mark.asyncio
    async def test_an_expired_client_bound_elsewhere_learns_nothing_here(self, monkeypatch):
        """The sentence is for the surface the client may use; anywhere else it is a stranger."""
        _cfg(monkeypatch, "mcp")
        auth.create_surface_token("mcp")
        client, token = clients_mod.create_client("ide", surfaces=["capture"])
        _age_client(client.client_id, 91 * _DAY)
        status, error = await _call("mcp", token)
        assert status == 401 and error["message"] == _UNIFORM


# ══ Settings → Devices lists them, with a revoke ═════════════════════════════════════


async def _devices_client() -> TestClient:
    from personalclaw.dashboard.handlers.devices import register_device_routes

    app = web.Application()
    app["allowed_origins"] = {"http://localhost:10000"}
    register_device_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestDevicesList:
    @pytest.mark.asyncio
    async def test_the_list_names_every_integration_token_and_never_one_of_them(self):
        surface_token = auth.create_surface_token("mcp", 7 * _DAY)
        client, client_token = clients_mod.create_client("ide", surfaces=["mcp", "a2a"])
        http = await _devices_client()
        try:
            resp = await http.get("/api/devices/integrations")
            assert resp.status == 200
            raw = await resp.text()
        finally:
            await http.close()
        body = json.loads(raw)
        assert body["problem"] == ""
        rows = {r["id"]: r for r in body["integrations"]}
        mcp = rows["surface-mcp"]
        assert mcp["kind"] == "surface" and mcp["name"] == "MCP token"
        assert mcp["state"] == "live" and mcp["surfaces"] == ["mcp"]
        assert mcp["expires_at"] - mcp["issued_at"] == pytest.approx(7 * _DAY)
        assert mcp["renew"] == "personalclaw inbound token create mcp --rotate"
        ide = rows[f"client-{client.client_id}"]
        assert ide["kind"] == "client" and ide["name"] == "ide"
        assert ide["surfaces"] == ["mcp", "a2a"] and ide["state"] == "live"
        for secret in (surface_token, client_token):
            assert secret not in raw
            assert hashlib.sha256(secret.encode()).hexdigest() not in raw

    @pytest.mark.asyncio
    async def test_revoking_from_the_list_stops_the_token_at_once(self, monkeypatch):
        _cfg(monkeypatch, "mcp")
        token = auth.create_surface_token("mcp")
        http = await _devices_client()
        try:
            resp = await http.post("/api/devices/integrations/surface-mcp/revoke")
            assert resp.status == 200 and (await resp.json())["revoked"] == "surface-mcp"
            listed = await (await http.get("/api/devices/integrations")).json()
            again = await http.post("/api/devices/integrations/surface-mcp/revoke")
            again_body = await again.json()
            unknown = await http.post("/api/devices/integrations/client-nope/revoke")
        finally:
            await http.close()
        assert auth.verify_bearer("mcp", token) is False
        assert {r["id"]: r["state"] for r in listed["integrations"]} == {"surface-mcp": "revoked"}
        assert again.status == 404 and again_body["error"]["code"] == "integration_unknown"
        assert unknown.status == 404

    @pytest.mark.asyncio
    async def test_revoking_a_client_from_the_list_deletes_it(self):
        client, token = clients_mod.create_client("ide", surfaces=["mcp"])
        http = await _devices_client()
        try:
            resp = await http.post(f"/api/devices/integrations/client-{client.client_id}/revoke")
        finally:
            await http.close()
        assert resp.status == 200
        assert clients_mod.lookup_by_token(token, "mcp")[0] is None
        assert client.client_id not in clients_mod.load_clients()

    @pytest.mark.asyncio
    async def test_a_cross_origin_revoke_is_refused(self):
        auth.create_surface_token("mcp")
        http = await _devices_client()
        try:
            resp = await http.post(
                "/api/devices/integrations/surface-mcp/revoke",
                headers={"Origin": "https://evil.example"},
            )
        finally:
            await http.close()
        assert resp.status == 403
        assert auth.verify_bearer("mcp", auth.load_surface_token("mcp") or "") is True


# ══ Registering a client with a lifetime ═════════════════════════════════════════════


class TestRegisterWithLifetime:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("ttl", "status", "code"),
        [("7d", 200, None), ("91d", 400, "token_ttl_too_long"), ("soon", 400, "token_ttl_invalid")],
    )
    async def test_the_create_route_takes_a_lifetime_up_to_90_days(self, ttl, status, code):
        from personalclaw.dashboard.handlers.external_access import api_external_access_client

        app = without_sign_in(web.Application())
        app.router.add_post("/api/external-access/clients", api_external_access_client)
        http = TestClient(TestServer(app))
        await http.start_server()
        try:
            resp = await http.post(
                "/api/external-access/clients",
                json={"label": "ide", "surfaces": ["mcp"], "ttl": ttl},
            )
            body = await resp.json()
        finally:
            await http.close()
        assert resp.status == status
        if code:
            assert body["error"]["code"] == code
            assert clients_mod.load_clients() == {}
        else:
            assert body["expires_at"] == pytest.approx(time.time() + 7 * _DAY, abs=60)
            assert "It works for 7 days" in body["token_notice"]


# ══ The command line ═════════════════════════════════════════════════════════════════


def _cli(action: str, *, surface: str = "mcp", rotate: bool = False, ttl: str = "") -> int:
    return auth.inbound_cmd(
        argparse.Namespace(
            inbound_command="token", surface=surface, token_action=action, rotate=rotate, ttl=ttl
        )
    )


class TestCommandLine:
    def test_create_states_the_lifetime_and_refuses_one_over_90_days(self, capsys):
        assert _cli("create", ttl="91d") == 1
        assert "An integration token can last at most 90 days" in capsys.readouterr().err
        assert auth.load_surface_token("mcp") is None
        assert _cli("create", ttl="7d") == 0
        assert "It works for 7 days, until" in capsys.readouterr().out

    def test_create_says_who_can_reach_the_surface_and_promises_no_config_that_widens_it(
        self, capsys
    ):
        """The dashboard refuses a program's request from another address before any surface
        reads it, so `public_url` + `allow_remote` never opened one: the closing line said they
        did. It says what is true — this machine only, and an SSH tunnel from anywhere else — and
        the bridge, which listens on its own port from the next start, says that."""
        assert _cli("create", surface="mcp") == 0
        out = capsys.readouterr().out
        assert "It takes requests only from programs on this machine" in out
        assert "over SSH" in out
        assert "allow_remote" not in out and "public_url" not in out
        assert _cli("create", surface="bridge") == 0
        out = capsys.readouterr().out
        assert "starts listening the next time PersonalClaw starts" in out
        assert "allow_remote" not in out

    def test_show_says_until_when_and_revoke_ends_it(self, capsys):
        assert _cli("create") == 0
        capsys.readouterr()
        assert _cli("show") == 0
        assert "it works until" in capsys.readouterr().out
        assert _cli("revoke") == 0
        assert "Revoked the mcp inbound token" in capsys.readouterr().out
        assert _cli("show") == 1
        assert "This MCP token was revoked today at" in capsys.readouterr().out
        assert _cli("revoke") == 1, "already revoked"

    def test_a_revoked_or_expired_token_can_be_replaced_without_rotate(self, capsys):
        assert _cli("create") == 0
        assert _cli("create") == 1, "a live token needs --rotate"
        assert _cli("revoke") == 0
        capsys.readouterr()
        assert _cli("create") == 0
        assert "Rotated the mcp inbound token" in capsys.readouterr().out
