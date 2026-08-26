"""CA-7 V3 — the tunnel clauses, driven over a REAL TLS tunnel.

`CA-7`'s build half (commit `57102588`) is merged: `ws.py:_check_ws_origin` admits an
`Origin`-less upgrade when the authorizing session carries a paired `device` row, and
`tests/test_ca7_remote_wss_auth.py` pins that admission and its fail-closed edges. What that
suite does NOT do is *transport*: it drives `aiohttp`'s in-process test client straight at the
app, so no socket ever carried TLS, nothing sat between client and gateway, and no connection
was ever killed mid-session. The atom's `done_when` is written as an **observation** over a
tunnel, which is why the atom stayed `todo`. This file closes as much of that as is honestly
closable on one machine.

**WHAT IS REAL HERE** — stated up front, because an overclaim would be worse than a gap:

* A real, separate TLS-terminating relay (:class:`_TlsTunnel`) listens on its own port, presents
  a real certificate, and forwards to the gateway. It injects ``X-Real-IP`` on the request head
  exactly as a reverse proxy does. The client dials ``wss://`` and completes a real TLS
  handshake; `aiohttp` verifies the cert against the CA. **The client never holds the gateway's
  address** — its only peer is the tunnel.
* The gateway side is the real `/api/ws` route behind the real `token_auth` middleware, so the
  admission under observation is the shipped one, not a stand-in.
* Killing the tunnel really destroys live sockets, and the reconnect is a genuinely new TLS
  handshake against a newly bound port.
* Authentication is **cookie-borne**, which is what the companion guide mandates for a native
  client and what no existing `CA-7` test exercised — every test in the older file authenticates
  with `?token=`, the one mechanism the guide forbids.

**WHAT IS NOT REAL** — the honest gap, unchanged from the atom's log:

* The tunnel is **loopback**. There is no public DNS name, no publicly-trusted CA, and no
  internet path. The topology (client → owner's tunnel → gateway, TLS terminated at the tunnel)
  is real; the *remoteness* is not. A clause needing a genuinely remote peer is still unobserved.
* The client is a native `aiohttp` client, not a shipped desktop/mobile shell — the repo still
  has none (`desktop/main.js` holds one `backendUrl`). It is native in the sense the admission
  cares about: it has no document, so it sends no `Origin`.
* "Degrades gracefully" is observed as *the socket dies promptly and the session reconnects*.
  The SPA's backoff ladder is reused by reference, not re-driven here.

**THE VACUITY HAZARD, AND WHY IT IS NOT VACUOUS.** `check_origin` trusts an `Origin`-less
request outright when the peer is loopback (`origin.py:420-424`). Over a loopback tunnel the
gateway's peer *is* 127.0.0.1, so every test below would pass with the CA-7 device branch
deleted. `is_loopback` is therefore forced False for the duration of every handshake, and
:func:`test_the_device_session_is_what_admitted_it_not_the_tunnels_loopback_peer` proves the
admission collapses to 403 the moment the device row is removed.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import mock

import pytest
from aiohttp import ClientConnectionError, ClientSession, WSServerHandshakeError, web
from aiohttp.test_utils import TestServer

from personalclaw.dashboard import exposure
from personalclaw.dashboard import origin as origin_mod
from personalclaw.dashboard import session_store as ss
from personalclaw.dashboard import token_auth
from personalclaw.dashboard import ws as ws_mod
from personalclaw.dashboard.origin import build_allowed_origins

PORT = 10000
COOKIE = f"pc_token_{PORT}"


# ── isolation ────────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An isolated home, pinned three ways, with a proof the redirect took.

    `PERSONALCLAW_HOME` is the safe lever (read per call, cached nowhere) and is what
    `exposure`/`AppConfig.load` follow. Both `config_dir` bindings are patched too because
    `session_store` does `from personalclaw.config.loader import config_dir` at import time, so
    patching only the loader would leave the store writing to the REAL home. The assertion is
    the point of the fixture: a silent miss looks exactly like a passing test.
    """
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "config_dir", lambda: tmp_path, raising=False)
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    assert ss.sessions_path().parent == tmp_path, "session store still points at the real home"
    yield tmp_path
    token_auth.revoke_all_sessions()


# ── a real certificate, and a real TLS-terminating tunnel ────────────────────────────────────


def _self_signed(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """A throwaway cert for 127.0.0.1: (server context, client context that trusts it)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_p = tmp_path / "tunnel.crt"
    key_p = tmp_path / "tunnel.key"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert_p, key_p)
    client_ctx = ssl.create_default_context(cafile=str(cert_p))
    return server_ctx, client_ctx


class _TlsTunnel:
    """The owner's tunnel, in miniature: terminate TLS here, forward plaintext upstream.

    This is what `cloudflared`/`ssh -R`/Caddy do to a companion's traffic, reduced to the two
    properties the atom depends on: TLS is terminated *in front of* the gateway (so the client's
    URL is `wss://` while the gateway speaks plain http), and the gateway's peer is the tunnel
    rather than the client. It rewrites the request head to add `X-Real-IP`, which is the only
    way a proxied client's real address reaches `_resolved_client_ip`.

    :meth:`stop` closes the listener **and every live socket**, which is the "kill the tunnel
    mid-session" event — not a graceful shutdown the client could mistake for a normal close.
    """

    def __init__(
        self, up_host: str, up_port: int, ssl_ctx: ssl.SSLContext, real_ip: str = ""
    ) -> None:
        self._up = (up_host, up_port)
        self._ssl = ssl_ctx
        self.real_ip = real_ip
        self.port = 0
        self._server: asyncio.AbstractServer | None = None
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self._ssl)
        self.port = int(self._server.sockets[0].getsockname()[1])

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_r, up_w = await asyncio.open_connection(*self._up)
        except OSError:
            writer.close()
            return
        self._writers += [writer, up_w]
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except Exception:  # noqa: BLE001 — a client that vanished mid-head needs no ceremony
            writer.close()
            up_w.close()
            return
        if self.real_ip:
            # Insert before the blank line that ends the head, exactly as a proxy would.
            head = head[:-2] + f"X-Real-IP: {self.real_ip}\r\n".encode() + b"\r\n"
        up_w.write(head)
        await up_w.drain()
        await asyncio.gather(
            self._pump(reader, up_w), self._pump(up_r, writer), return_exceptions=True
        )

    @staticmethod
    async def _pump(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await r.read(65536)
                if not data:
                    break
                w.write(data)
                await w.drain()
        except Exception:  # noqa: BLE001 — either side dying ends the relay, which is the point
            pass
        finally:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass

    async def stop(self) -> None:
        """Kill the listener and every live socket, the way a dying tunnel does.

        `transport.abort()` rather than `close()` on purpose: `close()` flushes first and only
        *schedules* the FIN, so the gateway can sit in `ws.receive()` for its full 30s heartbeat
        before noticing. `abort()` drops the connection at once, which is both the honest
        simulation of a tunnel being killed and what keeps the gateway's handler from outliving
        the test.
        """
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for w in self._writers:
            try:
                w.transport.abort()
            except Exception:  # noqa: BLE001
                pass
        self._writers.clear()
        await asyncio.sleep(0.05)


# ── the gateway: the real route, the real middleware, plus a peer recorder ────────────────────


def _app() -> tuple[web.Application, list[dict[str, str]]]:
    """The shipped `/api/ws` behind the shipped token middleware, and every peer it saw."""
    seen: list[dict[str, str]] = []

    @web.middleware
    async def _record(request: web.Request, handler: Any) -> Any:
        seen.append(
            {
                "remote": request.remote or "",
                "host": request.headers.get("Host", ""),
                "origin": request.headers.get("Origin", ""),
                "x_real_ip": request.headers.get("X-Real-IP", ""),
            }
        )
        return await handler(request)

    app = web.Application(
        middlewares=[_record, token_auth.token_auth_middleware(port=PORT, local_only=False)]
    )
    app["allowed_origins"] = build_allowed_origins(PORT, False)
    state = mock.MagicMock()
    state._sessions = {}
    state.is_yolo_active.return_value = False
    app["state"] = state
    app.router.add_get("/api/ws", ws_mod.api_ws)
    return app, seen


def _paired_token(*, device: bool = True) -> str:
    """A real signed token whose session row optionally carries a paired device."""
    token = token_auth.generate_token("owner")
    if device:
        nonce = token_auth.token_nonce(token)
        assert nonce, "the minted token must carry a nonce for the test to mean anything"
        assert ss.attach_device(nonce, ss.DeviceInfo(id="dev-1", name="Phone", kind="mobile"))
    return token


class _Rig:
    """A started gateway plus a started tunnel in front of it."""

    def __init__(
        self,
        server: TestServer,
        tunnel: _TlsTunnel,
        client_ctx: ssl.SSLContext,
        seen: list[dict[str, str]],
    ) -> None:
        self.server = server
        self.tunnel = tunnel
        self.client_ctx = client_ctx
        self.seen = seen
        self._server_ctx: ssl.SSLContext = client_ctx  # replaced by `rig` with the server's

    @property
    def wss_url(self) -> str:
        """The URL a native client dials — the TUNNEL's address, never the gateway's."""
        return f"wss://127.0.0.1:{self.tunnel.port}/api/ws"

    async def restart_tunnel(self, real_ip: str = "") -> None:
        """Kill the tunnel and bring a new one up on a fresh port, as a tunnel restart does."""
        await self.tunnel.stop()
        self.tunnel = _TlsTunnel(
            self.server.host or "127.0.0.1", int(self.server.port or 0), self._server_ctx, real_ip
        )
        await self.tunnel.start()


@asynccontextmanager
async def rig(tmp_path: Path) -> AsyncIterator[_Rig]:
    """Gateway + TLS tunnel, torn down in order.

    A context manager rather than a fixture on purpose: this repo runs `pytest-asyncio` in strict
    mode with no `asyncio_mode` setting and has no async-fixture precedent, so an
    `async def` fixture errors at setup instead of running. `async with` needs no plugin support.
    """
    server_ctx, client_ctx = _self_signed(tmp_path)
    app, seen = _app()
    server = TestServer(app)
    await server.start_server()
    tunnel = _TlsTunnel(server.host or "127.0.0.1", int(server.port or 0), server_ctx)
    await tunnel.start()
    r = _Rig(server, tunnel, client_ctx, seen)
    r._server_ctx = server_ctx
    try:
        yield r
    finally:
        await r.tunnel.stop()
        # BOUNDED on purpose. `TestServer.close()` waits on live handlers, and an `/api/ws`
        # handler whose transport was aborted under it can still be parked in `receive()`. That
        # is teardown, reached only after every assertion has already run, so a stuck handler
        # must not be allowed to hang the suite — and cannot hide a finding either.
        try:
            await asyncio.wait_for(server.close(), timeout=10)
        except asyncio.TimeoutError:  # pragma: no cover — belt and braces
            pass


async def _open(rig: _Rig, token: str, *, origin: str | None = None) -> Any:
    """Open `/api/ws` through the tunnel as a native client would. Returns the live socket.

    Authentication is the **cookie**, which is what the guide mandates for a companion (a
    `?token=` IP-binds and a phone changes IP). No `Origin` is sent unless one is asked for:
    a native client has no document and therefore none to send.
    """
    headers = {"Cookie": f"{COOKIE}={token}"}
    if origin is not None:
        headers["Origin"] = origin
    sess = ClientSession()
    with mock.patch.object(origin_mod, "is_loopback", return_value=False):
        try:
            sock = await sess.ws_connect(rig.wss_url, headers=headers, ssl=rig.client_ctx)
        except BaseException:
            await sess.close()
            raise
    sock._pc_session = sess  # type: ignore[attr-defined]
    return sock


async def _close(sock: Any) -> None:
    """Release the socket and its session.

    A courteous close writes a close frame, which is impossible once the tunnel has been killed
    under the connection — `aiohttp` raises `ClientConnectionResetError`. That is cleanup noise,
    not a finding: it is raised only AFTER the drop each test has already asserted. It is
    swallowed narrowly (connection errors only) so a real failure still surfaces, and the
    session is closed either way.
    """
    try:
        # Bounded: a courteous close waits for the peer's close frame, which never comes from a
        # tunnel that has been killed, and aiohttp's own default wait is 10s per socket.
        await asyncio.wait_for(sock.close(), timeout=3)
    except (ClientConnectionError, ConnectionResetError, asyncio.TimeoutError):
        pass
    finally:
        await sock._pc_session.close()


async def _status(rig: _Rig, token: str, *, origin: str | None = None) -> int:
    """The handshake status: 101 on success, else the refusal's code."""
    try:
        sock = await _open(rig, token, origin=origin)
    except WSServerHandshakeError as exc:
        return int(exc.status)
    await _close(sock)
    return 101


# ── the done_when, clause by clause ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_native_client_reaches_the_gateway_over_a_real_tls_tunnel(tmp_path: Path) -> None:
    """Clause 1: a native client reaches the gateway over a tunnel using its device session.

    Every leg is asserted rather than assumed: the URL really is `wss://`, the socket really
    carried TLS, the upgrade really completed, the gateway really saw the TUNNEL as its peer
    (so the client's traffic was relayed, not delivered direct), and the client really sent no
    `Origin`.
    """
    async with rig(tmp_path) as r:
        token = _paired_token()
        assert r.wss_url.startswith("wss://"), "the client must dial TLS, not plaintext"
        assert str(r.tunnel.port) in r.wss_url
        assert str(r.server.port) not in r.wss_url, "the client must not hold the gateway's port"

        sock = await _open(r, token)
        try:
            # A real TLS transport, not merely a `wss://` string.
            assert sock._response.connection.transport.get_extra_info("ssl_object") is not None
            first = await asyncio.wait_for(sock.receive_json(), timeout=5)
            assert first["type"] == "sessions", "the live socket must carry real gateway traffic"
        finally:
            await _close(sock)

        upgrade = r.seen[-1]
        assert upgrade["origin"] == "", "a native client has no document and must send no Origin"
        assert upgrade["host"] == f"127.0.0.1:{r.tunnel.port}", "the client addressed the tunnel"


@pytest.mark.asyncio
async def test_the_device_session_is_what_admitted_it_not_the_tunnels_loopback_peer(
    tmp_path: Path,
) -> None:
    """VACUITY PROOF for every test above and below.

    The tunnel is loopback, and `check_origin` trusts an Origin-less loopback peer outright
    (`origin.py:420-424`). If that branch — not CA-7's device branch — were what admitted these
    upgrades, the suite would be measuring the tunnel instead of the feature. Remove the device
    row and the same request must be refused; keep it and the same request must be admitted.
    """
    async with rig(tmp_path) as r:
        assert await _status(r, _paired_token(device=True)) == 101
        assert await _status(r, _paired_token(device=False)) == 403


@pytest.mark.asyncio
async def test_no_new_origin_exemption_was_needed_to_reach_it_over_the_tunnel(
    tmp_path: Path,
) -> None:
    """The atom's security clause as a DIFFERENTIAL, which is the only non-vacuous way to say it.

    "No new origin exemption" is a claim about a difference: pairing a device must not change
    *which origins* are allowed to complete the upgrade. So the same origins are probed twice —
    once with a paired device session and once with an ordinary one — and the two verdicts must
    agree everywhere. A rail that merely re-read `build_allowed_origins` and compared it to
    itself would be a floor computed from the value it is meant to pin, and would pass no matter
    what was added to the set; this one cannot.

    The single admitted difference is the `Origin`-less case, asserted last, and that is exactly
    the seam `CA-7` opened.

    **Read the `pc.example.com` probe as the load-bearing one.** `is_loopback` is forced False
    for every handshake, so `127.0.0.1` here stands in for a genuinely remote address — which is
    the point of the stand-in, since a real tunnel's client *is* remote. Do not read the tunnel
    leg as "a loopback origin is refused in production": shipped `check_origin` trusts any
    loopback origin regardless of port (`origin.py:470`). `https://pc.example.com` is refused on
    the shipped rule itself, with no patch involved.
    """
    async with rig(tmp_path) as r:
        probes = [
            f"https://127.0.0.1:{r.tunnel.port}",  # the tunnel, standing in for a remote peer
            "https://pc.example.com",  # refused by the shipped rule, patch or no patch
            "http://evil.example",
            f"http://localhost:{PORT}",  # in the set unconditionally — must stay admitted
        ]
        paired = {p: await _status(r, _paired_token(device=True), origin=p) for p in probes}
        plain = {p: await _status(r, _paired_token(device=False), origin=p) for p in probes}
        assert paired == plain, f"a device session changed which origins are admitted: {paired}"
        assert paired[f"http://localhost:{PORT}"] == 101, "the probe set must not be all-refusals"
        assert paired["https://pc.example.com"] == 403

        # The one admitted difference, which is the whole of CA-7's seam.
        assert await _status(r, _paired_token(device=True), origin=None) == 101
        assert await _status(r, _paired_token(device=False), origin=None) == 403


@pytest.mark.asyncio
async def test_killing_the_tunnel_mid_session_drops_the_socket_promptly(tmp_path: Path) -> None:
    """Clause 2, first half: the drop is observed, and it is prompt rather than a hang.

    A client left hanging on a dead tunnel is the ungraceful failure this clause exists to rule
    out — it is what produces a UI stuck on "connected" with no traffic.

    **MEASURED: the drop arrives in one of two shapes, and a native client must handle both.**
    Either `receive()` returns a CLOSED/ERROR message, or it *raises*
    `ClientConnectionResetError` — the latter when aiohttp's autoping tries to answer a ping on
    the transport that has just died ("Cannot write to closing transport", raised by aiohttp's
    own websocket writer). Both are prompt notifications and both are acceptable;
    a shell that only catches the message shape would surface the raise as an unhandled error.
    `asyncio.TimeoutError` is deliberately NOT caught — a timeout here IS the hang under test.
    """
    async with rig(tmp_path) as r:
        sock = await _open(r, _paired_token())
        try:
            assert (await asyncio.wait_for(sock.receive_json(), timeout=5))["type"] == "sessions"
            await r.tunnel.stop()  # the tunnel dies under the live session
            try:
                await asyncio.wait_for(sock.receive(), timeout=5)
            except (ClientConnectionError, ConnectionResetError):
                pass  # the raise shape — still a prompt notification, not a hang
            assert (
                sock.closed or sock._writer.transport.is_closing()
            ), "the client still believes the dead socket is usable"
        finally:
            await _close(sock)


@pytest.mark.asyncio
async def test_the_same_device_session_reconnects_once_the_tunnel_returns(tmp_path: Path) -> None:
    """Clause 2, second half: the session survives the transport's death.

    This is the property that makes "reconnects gracefully" true, and it is not free: a session
    invalidated by the drop, consumed on first use, or bound to the dead connection would force
    a re-pair — which is exactly the ungraceful outcome. The reconnect is a genuinely new TLS
    handshake against a newly bound port.
    """
    async with rig(tmp_path) as r:
        token = _paired_token()
        sock = await _open(r, token)
        assert (await asyncio.wait_for(sock.receive_json(), timeout=5))["type"] == "sessions"
        dead_port = r.tunnel.port
        await _close(sock)

        await r.restart_tunnel()
        assert r.tunnel.port != dead_port, "the reconnect must cross a genuinely new listener"

        again = await _open(r, token)
        try:
            assert (await asyncio.wait_for(again.receive_json(), timeout=5))["type"] == "sessions"
        finally:
            await _close(again)


@pytest.mark.asyncio
async def test_the_reconnect_survives_the_changed_client_ip_a_real_tunnel_produces(
    tmp_path: Path,
) -> None:
    """WHY the reconnect above works, asserted at the deciding call site.

    A tunnel restart moves the client's apparent address, and a phone changes IP constantly.
    `token_auth` IP-binds — but only on the `?token=` path: `token_auth.py:1072` reads
    ``if not from_cookie and not check_token_ip(...)``. The cookie the guide mandates therefore
    skips the check by construction, and the tunnel's `X-Real-IP` proves the address really did
    change through `_resolved_client_ip` rather than being asserted in the abstract.
    """
    async with rig(tmp_path) as r:
        token = _paired_token()
        await r.restart_tunnel(real_ip="203.0.113.7")
        sock = await _open(r, token)
        try:
            assert (await asyncio.wait_for(sock.receive_json(), timeout=5))["type"] == "sessions"
        finally:
            await _close(sock)
        assert r.seen[-1]["x_real_ip"] == "203.0.113.7", "the tunnel must have moved the address"

        # And from a different address again, on the same session — the phone-changes-network case.
        await r.restart_tunnel(real_ip="198.51.100.22")
        sock = await _open(r, token)
        try:
            assert (await asyncio.wait_for(sock.receive_json(), timeout=5))["type"] == "sessions"
        finally:
            await _close(sock)
        assert r.seen[-1]["x_real_ip"] == "198.51.100.22"

        # The binding that the cookie path skips is real, and would have refused both reconnects.
        token_auth.bind_token_ip(token, "203.0.113.7")
        assert token_auth.check_token_ip(token, "203.0.113.7")
        assert not token_auth.check_token_ip(
            token, "198.51.100.22"
        ), "if the cookie path consulted this, the second reconnect could not have succeeded"


# ── no cloud middle tier: a path property, asserted ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_cloud_middle_tier_the_only_hop_is_the_owners_own_tunnel(tmp_path: Path) -> None:
    """Success Criterion 3 as a rail: client → owner's tunnel → gateway, and nothing else.

    Asserted as a closed accounting of the path rather than a comment: across the whole session
    the gateway saw exactly one peer, that peer is the tunnel on the owner's own loopback, and
    the client's only peer was the tunnel. There is no third address anywhere in the path, so
    there is no tier left for a broker to occupy.
    """
    async with rig(tmp_path) as r:
        sock = await _open(r, _paired_token())
        try:
            assert (await asyncio.wait_for(sock.receive_json(), timeout=5))["type"] == "sessions"
            # The client's only peer: the tunnel it dialed.
            peer = sock._response.connection.transport.get_extra_info("peername")
            assert peer[0] == "127.0.0.1" and peer[1] == r.tunnel.port
        finally:
            await _close(sock)

        peers = {s["remote"] for s in r.seen}
        assert peers == {"127.0.0.1"}, f"the gateway saw something other than the tunnel: {peers}"
        hosts = {s["host"] for s in r.seen}
        assert hosts == {f"127.0.0.1:{r.tunnel.port}"}, f"a third host appears in the path: {hosts}"


def test_no_cloud_middle_tier_the_gateway_advertises_no_host_the_owner_did_not_configure() -> None:
    """The static half: nothing in the product supplies a public host on the owner's behalf.

    A cloud tier would have to enter as a default — a fallback broker hostname the code reaches
    for when the owner configured none. With an empty home there is no such default, and when
    the owner does declare a URL it is returned verbatim rather than rewritten through anything.
    Add a vendor default to `public_url`'s fallback chain and this reds.
    """
    assert exposure.public_url() == "", "an unconfigured instance must advertise no host at all"
    assert exposure.public_host() == ""
    assert exposure.is_exposed() is False

    owner = SimpleNamespace(
        dashboard=SimpleNamespace(public_url="https://pc.example.com"),
        external_access=SimpleNamespace(public_url=""),
    )
    assert exposure.public_url(owner) == "https://pc.example.com"
    assert exposure.public_host(owner) == "pc.example.com", "the owner's host, not a rewrite"
    assert exposure.is_https(owner) is True
