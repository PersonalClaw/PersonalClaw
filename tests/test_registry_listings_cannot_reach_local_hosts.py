"""A registry listing cannot point the gateway at this computer, a private network or the cloud
metadata service.

A registry index is third-party text, and a listing's ``repo`` is where an install fetches the
bytes from. #3619 made it a plain ``https://`` URL, which still lets it name ``127.0.0.1``, a LAN
address, ``169.254.169.254``, or a public-looking name that resolves (or later rebinds) to any of
them. Measured on ``main`` before this change: such a listing was an ordinary Store card, and
previewing it ran ``git clone`` against whatever it named, with git resolving the name itself and
following any redirect the server sent.

Now a listing is checked three times, one rule. The Store shows it refused, with the reason, on
what it can see without asking DNS (reading the Store contacts no host a listing names): the URL's
form and an address written as a literal. An install resolves the host at that moment and refuses a
forbidden answer before anything is fetched. And the fetch runs git through a tunnel that asks the
egress guard about every host as git connects to it (``personalclaw.net.git``), so neither a name
that rebinds after the install's check nor a redirect reaches a forbidden address. The owner's own
choices stay reachable: a registry source's own host, a host on the egress allow-list, and anything
typed into Install from URL.
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import catalog, manager
from personalclaw.apps import source as app_source
from personalclaw.providers import loader

#: An address the guard classifies as public (a GitHub range). The fake DNS below maps names to
#: it, and :func:`public_host` decides what a connection to it reaches; nothing dials it for real.
PUBLIC_IP = "140.82.112.3"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    import personalclaw.config.loader as cfg
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    for module in (cfg, manager, catalog, _er, _inbox):
        monkeypatch.setattr(module, "config_dir", lambda: home)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    # No network sources: the shipped GitHub default would otherwise be cloned by every read.
    monkeypatch.setattr(catalog, "list_git_sources", lambda: [])
    catalog._registry_cache.clear()
    catalog._registry_failures.clear()
    yield home
    catalog._registry_cache.clear()
    catalog._registry_failures.clear()


@pytest.fixture
def dns(monkeypatch) -> dict[str, list[str]]:
    """The DNS the egress guard sees: ``table[host] = [ip, …]``. An IP literal resolves to itself
    and any other name is unresolvable, so no test depends on the network."""
    table: dict[str, list[str]] = {}

    def resolve(host: str) -> list[str]:
        if host in table:
            return list(table[host])
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            raise socket.gaierror(f"no such host {host!r}") from None

    monkeypatch.setattr("personalclaw.net.guard._resolve", resolve)
    return table


@pytest.fixture
def public_host(monkeypatch) -> SimpleNamespace:
    """What answers at ``PUBLIC_IP:443``: nothing until a test sets ``port`` to a local server.
    Every connection made to it is recorded in ``dialed``; every other address connects as usual."""
    real = socket.create_connection
    host = SimpleNamespace(dialed=[], port=None)

    def create_connection(address, *args, **kwargs):
        if address[0] != PUBLIC_IP:
            return real(address, *args, **kwargs)
        host.dialed.append((address[0], address[1]))
        if host.port is None:
            raise ConnectionRefusedError(f"nothing listens at {PUBLIC_IP} in this test")
        return real(("127.0.0.1", host.port), *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", create_connection)
    return host


def _relay(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            ready, _, _ = select.select([a, b], [], [], 10)
            if not ready:
                return
            for src in ready:
                data = src.recv(65536)
                if not data:
                    return
                (b if src is a else a).sendall(data)
    except OSError:
        return
    finally:
        a.close()
        b.close()


class _UpstreamHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            head += chunk
        target = head.split(b" ", 2)[1].decode()
        self.server.connects.append(target)
        port = self.server.routes.get(target)
        if port is None:
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        onward = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        _relay(self.request, onward)


@pytest.fixture
def upstream(monkeypatch):
    """The owner's environment proxy, which is how a plain ``git`` would reach the internet here.
    It records every ``host:port`` git asks it for and connects only what a test routes. A listing
    fetch must never use it: a proxy of the environment's choosing is a way around the guard."""
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _UpstreamHandler)
    server.daemon_threads = True
    server.connects = []
    server.routes = {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    for name in ("https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, url)
    for name in ("no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    yield server
    server.shutdown()
    server.server_close()


def _registry(tmp_path: Path, listings: list[dict], *, name: str = "registry-src") -> Path:
    """A registry the owner added as a local source, publishing ``listings``."""
    src = tmp_path / name
    src.mkdir()
    (src / "app-registry.json").write_text(json.dumps({"apps": listings}), encoding="utf-8")
    catalog.add_local_source(str(src))
    return src


def _cards() -> dict[str, dict]:
    return {e["name"]: e for e in catalog.available_catalog()["remoteApps"]}


# ── the Store shows the listing refused, and says why ─────────────────────────────────────────


@pytest.mark.parametrize(
    "repo, where",
    [
        ("https://127.0.0.1/evil.git", "this computer (127.0.0.1)"),
        ("https://[::1]/evil.git", "this computer (::1)"),
        ("https://[::ffff:127.0.0.1]/evil.git", "this computer (::ffff:127.0.0.1)"),
        ("https://0.0.0.0/evil.git", "this computer (0.0.0.0)"),
        ("https://10.1.2.3/evil.git", "a private network (10.1.2.3)"),
        ("https://192.168.1.20/evil.git", "a private network (192.168.1.20)"),
        ("https://169.254.169.254/latest.git", "the cloud-metadata address (169.254.169.254)"),
        ("https://metadata.google.internal/x.git", "the cloud-metadata service"),
    ],
)
def test_a_listing_naming_a_local_or_private_address_is_shown_refused(tmp_path, dns, repo, where):
    _registry(tmp_path, [{"name": "evil", "repo": repo}])
    card = _cards()["evil"]
    refused = card.get("refused", "")
    assert refused.startswith("Not installable"), f"the Store offers {repo!r} for install"
    assert where in refused, refused


def test_reading_the_store_asks_dns_about_no_listing_host(tmp_path, monkeypatch):
    """Opening the Store with only a local registry reaches nothing off this machine, and the
    Store says so (``networkSources`` is empty). So the Store judges a listing on what it can see
    without the network, and a host written as a name is judged when an install connects to it."""
    asked: list[str] = []

    def resolve(host: str) -> list[str]:
        asked.append(host)
        return [PUBLIC_IP]

    monkeypatch.setattr("personalclaw.net.guard._resolve", resolve)
    _registry(
        tmp_path,
        [
            {"name": "named", "repo": "https://apps.example/named.git"},
            {"name": "literal", "repo": "https://127.0.0.1/literal.git"},
        ],
    )
    catalog_now = catalog.available_catalog()
    cards = {e["name"]: e for e in catalog_now["remoteApps"]}
    assert asked == [], f"reading the Store resolved {asked}"
    assert catalog_now["networkSources"] == []
    assert cards["named"].get("refused", "") == ""
    assert "this computer (127.0.0.1)" in cards["literal"].get("refused", "")


def test_a_listing_on_a_public_host_is_installable(tmp_path, dns):
    dns["apps.example"] = [PUBLIC_IP]
    _registry(tmp_path, [{"name": "fine", "repo": "https://apps.example/fine.git"}])
    card = _cards()["fine"]
    assert card.get("refused", "") == ""
    assert card["pointer"] == "https://apps.example/fine.git"
    assert card["listedBy"] == str(tmp_path / "registry-src")


def test_a_listing_that_names_no_repo_downloads_from_the_owners_source(tmp_path, dns):
    """No ``repo`` means the app lives in the registry source itself, which the owner added."""
    src = _registry(tmp_path, [{"name": "own", "subdirectory": "apps/own"}])
    card = _cards()["own"]
    assert card.get("refused", "") == ""
    assert card["pointer"] == f"{src}#apps/own"
    assert card["listedBy"] == ""


def test_the_owner_added_sources_own_host_is_trusted_for_its_listings(tmp_path, dns, monkeypatch):
    """The owner added a registry at ``10.0.0.2``, so its listings may point back at that host,
    private or not. They may not point anywhere else private."""
    source = "https://10.0.0.2/registry.git"
    index = {
        "apps": [
            {"name": "house-tool", "repo": "https://10.0.0.2/apps/house-tool.git"},
            {"name": "elsewhere", "repo": "https://10.9.9.9/x.git"},
        ]
    }
    monkeypatch.setattr(catalog, "list_git_sources", lambda: [source])
    monkeypatch.setattr(catalog, "_read_git_registry", lambda url, deadline=None: json.dumps(index))
    monkeypatch.setattr(catalog, "_scan_git_sources", lambda **kw: [])
    cards = _cards()
    assert cards["house-tool"].get("refused", "") == ""
    assert "a private network (10.9.9.9)" in cards["elsewhere"].get("refused", "")


def test_a_host_on_the_owners_egress_allow_list_is_installable(tmp_path, dns, _isolate):
    """``security.egress.allow_hosts`` is where the owner says a private host is theirs to reach."""
    _registry(tmp_path, [{"name": "nas-app", "repo": "https://192.168.1.9/nas-app.git"}])
    assert "a private network (192.168.1.9)" in _cards()["nas-app"].get("refused", "")
    (_isolate / "config.json").write_text(
        json.dumps({"security": {"egress": {"allow_hosts": ["192.168.1.9"]}}}), encoding="utf-8"
    )
    assert _cards()["nas-app"].get("refused", "") == ""


def test_a_refused_listing_never_displaces_an_installable_one(tmp_path, dns):
    dns["apps.example"] = [PUBLIC_IP]
    _registry(tmp_path, [{"name": "tool", "repo": "https://127.0.0.1/tool.git"}], name="first")
    _registry(tmp_path, [{"name": "tool", "repo": "https://apps.example/tool.git"}], name="second")
    card = _cards()["tool"]
    assert card["pointer"] == "https://apps.example/tool.git"
    assert card.get("refused", "") == ""


# ── fetching from a refused listing never starts ─────────────────────────────────────────────


@asynccontextmanager
async def _client():
    from personalclaw.dashboard.handlers.apps import register_app_routes

    app = web.Application()
    register_app_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
def git_runs(monkeypatch) -> list[list[str]]:
    """Every ``git`` the install path runs, answered with a failure so nothing is fetched."""
    runs: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git":
            runs.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: not fetched in this test")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    return runs


def _route_body(route: str, source: str, **extra: str) -> tuple[str, dict]:
    if route == "update":
        return "/api/apps/evil/update", {"source": source, "consent": "x", **extra}
    if route == "install":
        return "/api/apps", {"source": source, "consent": "x", **extra}
    return "/api/apps/preview", {"source": source, **extra}


def _egress_rows() -> list[tuple[str, str]]:
    """The security log's guarded-git rows, oldest first: ``(outcome, what was asked for)``."""
    from personalclaw.sel import sel

    rows = [r for r in sel().recent(limit=200) if r.get("operation") == "egress_git"]
    rows.sort(key=lambda r: r.get("timestamp", ""))
    return [(r["outcome"], r["resources"]) for r in rows]


def _message(body: dict) -> str:
    error = body.get("error")
    return error.get("message", "") if isinstance(error, dict) else str(error or "")


@pytest.mark.parametrize("route", ["preview", "install", "update"])
@pytest.mark.parametrize(
    "repo, where",
    [
        ("https://127.0.0.1/evil.git", "this computer (127.0.0.1)"),
        (
            "https://evil.example/app.git",
            "this computer (evil.example, which resolves to 127.0.0.1)",
        ),
        # One private answer among public ones is enough: git may connect to any of them.
        (
            "https://mixed.example/app.git",
            "a private network (mixed.example, which resolves to 10.0.0.7)",
        ),
    ],
    ids=["literal", "name", "one-of-several"],
)
@pytest.mark.asyncio
async def test_a_refused_listing_is_refused_before_anything_is_fetched(
    tmp_path, dns, git_runs, route, repo, where
):
    dns["evil.example"] = ["127.0.0.1"]
    dns["mixed.example"] = [PUBLIC_IP, "10.0.0.7"]
    _registry(tmp_path, [{"name": "evil", "repo": repo}])
    catalog.available_catalog()  # the Store read that showed the card
    path, body = _route_body(route, repo)
    async with _client() as client:
        resp = await client.post(path, json=body)
        answer = await resp.json()
    assert git_runs == [], f"the gateway fetched from the listing: {git_runs}"
    assert resp.status == 400, answer
    assert where in _message(answer), answer


@pytest.mark.asyncio
async def test_a_listing_host_that_does_not_resolve_is_not_fetched(tmp_path, dns, git_runs):
    _registry(tmp_path, [{"name": "gone", "repo": "https://gone.example/x.git"}])
    catalog.available_catalog()
    async with _client() as client:
        resp = await client.post("/api/apps/preview", json={"source": "https://gone.example/x.git"})
        answer = await resp.json()
    assert git_runs == []
    assert resp.status == 400, answer
    assert answer["error"]["code"] == "app_source_unresolved"
    assert "Could not reach gone.example" in _message(answer), answer


@pytest.mark.asyncio
async def test_the_registry_sources_own_host_is_fetched_through_the_guard(
    tmp_path, dns, git_runs, monkeypatch
):
    """The owner added the registry at ``10.0.0.2``: its listing there is fetched, and still
    through the tunnel, so a redirect from it is judged like any other."""
    source = "https://10.0.0.2/registry.git"
    index = {"apps": [{"name": "house-tool", "repo": "https://10.0.0.2/apps/house-tool.git"}]}
    monkeypatch.setattr(catalog, "list_git_sources", lambda: [source])
    monkeypatch.setattr(catalog, "_read_git_registry", lambda url, deadline=None: json.dumps(index))
    monkeypatch.setattr(catalog, "_scan_git_sources", lambda **kw: [])
    catalog.available_catalog()
    async with _client() as client:
        await client.post(
            "/api/apps/preview", json={"source": "https://10.0.0.2/apps/house-tool.git"}
        )
    assert len(git_runs) == 1, git_runs
    assert any(arg.startswith("http.proxy=http://127.0.0.1:") for arg in git_runs[0]), git_runs


@pytest.mark.asyncio
async def test_a_card_that_says_it_is_a_listing_is_held_to_it(tmp_path, dns, git_runs):
    """After a restart the gateway has read no index yet, but the Store card in the browser still
    names the registry that listed it, so the fetch is held to the listing's rules regardless."""
    body = {"source": "https://10.0.0.5/x.git", "listedBy": "https://registry.example/r.git"}
    async with _client() as client:
        resp = await client.post("/api/apps/preview", json=body)
        answer = await resp.json()
    assert git_runs == []
    assert resp.status == 400, answer
    assert "a private network (10.0.0.5)" in _message(answer), answer
    assert _egress_rows() == [("denied", "https://10.0.0.5/x.git")]


@pytest.mark.asyncio
async def test_a_listing_that_names_a_folder_is_not_installed_from_it(tmp_path, dns, git_runs):
    here = tmp_path / "somewhere" / "sneaky"
    here.mkdir(parents=True)
    (here / "app.json").write_text(
        json.dumps({"name": "sneaky", "version": "1.0.0", "displayName": "S", "description": "x"}),
        encoding="utf-8",
    )
    _registry(tmp_path, [{"name": "sneaky", "repo": str(here)}])
    assert "a folder on this computer" in _cards()["sneaky"].get("refused", "")
    async with _client() as client:
        resp = await client.post("/api/apps/preview", json={"source": str(here)})
        answer = await resp.json()
    assert resp.status == 400, answer
    assert "a folder on this computer" in _message(answer), answer


@pytest.mark.asyncio
async def test_an_owner_typed_url_is_fetched_as_before(tmp_path, dns, git_runs):
    """Install from URL is the owner's own act: a private host they typed is theirs to use."""
    async with _client() as client:
        resp = await client.post("/api/apps/preview", json={"source": "https://10.0.0.5/mine.git"})
    assert resp.status == 400  # the recorder refuses the clone itself
    assert [run[:4] for run in git_runs] == [["git", "clone", "--depth", "1"]]


# ── the fetch checks every host as git connects to it ────────────────────────────────────────


def _tunnel_request(port: int, line: str) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
        conn.sendall(f"{line}\r\nHost: x\r\n\r\n".encode())
        return conn.recv(4096)


@pytest.mark.parametrize(
    "target, address, category",
    [
        ("169.254.169.254:443", "169.254.169.254", "metadata"),
        ("[::1]:443", "::1", "loopback"),
        ("10.0.0.9:443", "10.0.0.9", "private"),
    ],
)
def test_the_tunnel_refuses_a_forbidden_address(dns, public_host, target, address, category):
    from personalclaw.net.git import GuardedTunnel
    from personalclaw.net.policy import LISTING

    with GuardedTunnel(LISTING) as tunnel:
        reply = _tunnel_request(tunnel.port, f"CONNECT {target} HTTP/1.1")
    assert reply.startswith(b"HTTP/1.1 403"), reply
    assert [(r.address, r.category) for r in tunnel.refused] == [(address, category)]
    assert _egress_rows() == [("denied", target.replace("[", "").replace("]", ""))]


def test_the_tunnel_judges_a_name_by_what_it_resolves_to_when_git_connects(dns, public_host):
    """The rebinding case: whatever the name answered during the Store read, the connection is
    judged on what it answers now, and only a validated address is ever dialed."""
    from personalclaw.net.git import GuardedTunnel
    from personalclaw.net.policy import LISTING

    dns["rebind.example"] = ["127.0.0.1"]
    with GuardedTunnel(LISTING) as tunnel:
        reply = _tunnel_request(tunnel.port, "CONNECT rebind.example:443 HTTP/1.1")
    assert reply.startswith(b"HTTP/1.1 403"), reply
    assert (tunnel.refused[0].host, tunnel.refused[0].address) == ("rebind.example", "127.0.0.1")
    assert public_host.dialed == []


@pytest.mark.parametrize(
    "line, status",
    [
        ("GET http://apps.example/x HTTP/1.1", b"405"),
        ("CONNECT apps.example:22 HTTP/1.1", b"403"),
        ("CONNECT apps.example HTTP/1.1", b"400"),
    ],
)
def test_the_tunnel_only_opens_https_connections(dns, public_host, line, status):
    from personalclaw.net.git import GuardedTunnel
    from personalclaw.net.policy import LISTING

    dns["apps.example"] = [PUBLIC_IP]
    with GuardedTunnel(LISTING) as tunnel:
        reply = _tunnel_request(tunnel.port, line)
    assert reply.split(b" ", 2)[1] == status, reply
    assert public_host.dialed == []


class _Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.sendall(self.request.recv(64))


def test_an_allowed_connection_reaches_only_the_validated_address(dns, public_host):
    from personalclaw.net.git import GuardedTunnel
    from personalclaw.net.policy import LISTING

    echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=echo.serve_forever, daemon=True).start()
    public_host.port = echo.server_address[1]
    dns["apps.example"] = [PUBLIC_IP]
    try:
        with GuardedTunnel(LISTING) as tunnel:
            with socket.create_connection(("127.0.0.1", tunnel.port), timeout=10) as conn:
                conn.sendall(b"CONNECT apps.example:443 HTTP/1.1\r\nHost: apps.example:443\r\n\r\n")
                head = conn.recv(4096)
                conn.sendall(b"ping")
                echoed = conn.recv(64)
    finally:
        echo.shutdown()
        echo.server_close()
    assert head.startswith(b"HTTP/1.1 200"), head
    assert echoed == b"ping"
    assert public_host.dialed == [(PUBLIC_IP, 443)]
    assert tunnel.refused == []


requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@requires_git
@pytest.mark.parametrize(
    "hostile_env",
    [
        {},
        {"NO_PROXY": "*"},
        {"no_proxy": "*"},
        {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.proxy", "GIT_CONFIG_VALUE_0": ""},
        {"GIT_CONFIG_PARAMETERS": "'http.proxy'=''"},
        {"GIT_ALLOW_PROTOCOL": "file:http:https:ssh"},
    ],
    ids=["plain", "NO_PROXY", "no_proxy", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "protocols"],
)
def test_real_git_is_stopped_at_a_name_that_resolves_to_this_computer(
    dns, public_host, monkeypatch, hostile_env
):
    """Real git, through the tunnel, whatever the environment says. Each variable here would, if
    honoured, send git around the tunnel or widen what it may speak."""
    from personalclaw.net.git import GitEgressRefused, run_git_guarded
    from personalclaw.net.policy import LISTING

    for name, value in hostile_env.items():
        monkeypatch.setenv(name, value)
    dns["rebind.example"] = ["127.0.0.1"]
    with pytest.raises(GitEgressRefused) as refused:
        run_git_guarded(["ls-remote", "https://rebind.example/x.git"], policy=LISTING, timeout=60)
    assert refused.value.refusal.address == "127.0.0.1"


def _certificate(tmp_path: Path, host: str) -> tuple[Path, Path]:
    """A throwaway certificate for ``host``; git is told to trust it through GIT_SSL_CAINFO."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_p, key_p = tmp_path / f"{host}.crt", tmp_path / f"{host}.key"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_p, key_p


class _RedirectToMetadata(BaseHTTPRequestHandler):
    """A listing's host that answers git's first request with a redirect to the metadata service."""

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        self.send_response(302)
        self.send_header("Location", f"https://169.254.169.254{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@requires_git
@pytest.mark.asyncio
async def test_a_listing_redirected_to_the_metadata_address_is_stopped(
    tmp_path, dns, public_host, upstream, monkeypatch
):
    cert, key = _certificate(tmp_path, "listing.test")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectToMetadata)
    tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GIT_SSL_CAINFO", str(cert))
    monkeypatch.setattr(app_source, "_CLONE_TIMEOUT", 30)
    # The listing's host is the test server, whichever way git reaches it: at its public address
    # through the guarded tunnel, or by name through the environment's proxy.
    dns["listing.test"] = [PUBLIC_IP]
    public_host.port = server.server_address[1]
    upstream.routes["listing.test:443"] = server.server_address[1]

    _registry(tmp_path, [{"name": "redirected", "repo": "https://listing.test/evil.git"}])
    assert _cards()["redirected"].get("refused", "") == ""  # public when the Store read it
    try:
        async with _client() as client:
            resp = await client.post(
                "/api/apps/preview", json={"source": "https://listing.test/evil.git"}
            )
            answer = await resp.json()
    finally:
        server.shutdown()
        server.server_close()
    assert upstream.connects == [], f"git fetched around the guard: {upstream.connects}"
    message = _message(answer)
    assert resp.status == 400, answer
    assert "redirected the download to the cloud-metadata address (169.254.169.254)" in message
    assert public_host.dialed == [(PUBLIC_IP, 443)]
