"""LAN discovery for companion clients.

The properties under test are the ones that make broadcasting your gateway's existence on a
network an acceptable thing to ship:

* the advertised bytes carry **no credential and no content** — asserted against the real
  serialized packet with a real secret and a real session token on disk, not against the
  builder's intent;
* a **loopback-only** gateway advertises nothing, and says why;
* discovery **off** is the default and leaves the manual pairing path untouched;
* discovery is **legible** — the status surface reports the live advertiser, not the flag.

The wire format is exercised through real sockets (unicast, so no test depends on the
machine's multicast configuration) as well as against exact bytes.
"""

import json
import socket
import time
from unittest.mock import MagicMock, patch

import companion_discovery_shared
import pytest
from aiohttp import web

from personalclaw.companion import discovery as disc

_isolate = companion_discovery_shared._isolate

LAN_HOST = "192.168.1.37"


def _service(name: str = "Living room Mac", port: int = 10166) -> disc.ServiceInfo:
    return disc.ServiceInfo(
        instance_name=name,
        hostname="claw",
        port=port,
        address=LAN_HOST,
        txt=disc.build_txt(instance_name=name, port=port),
    )


class _StubAdvertiser:
    """Records lifecycle instead of opening a socket.

    ``reconcile()``'s job is deciding *whether* a service should be advertised and keeping
    one instance alive; that is what these tests measure. The socket half has its own tests
    below, so binding port 5353 here would only add a real multicast announcement as a side
    effect of running the suite.
    """

    instances: list["_StubAdvertiser"] = []

    def __init__(self, service, **_kw):
        self.service = service
        self.started = False
        self.stopped = False
        self.start_returns = True
        _StubAdvertiser.instances.append(self)

    def start(self):
        self.started = True
        return self.start_returns

    def stop(self):
        self.stopped = True

    @property
    def running(self):
        return self.started and not self.stopped


@pytest.fixture()
def stub_advertiser(monkeypatch):
    _StubAdvertiser.instances = []
    monkeypatch.setattr(disc, "Advertiser", _StubAdvertiser)
    return _StubAdvertiser


# ── the TXT record carries nothing sensitive ───────────────────────────────────


def test_txt_keys_are_a_closed_set():
    """The record may carry only these four keys — a new one has to be argued for here."""
    txt = disc.build_txt(instance_name="Living room Mac", port=10166)
    assert set(txt) == {"name", "port", "requires_pairing", "schema"}
    assert txt == {
        "name": "Living room Mac",
        "port": "10166",
        "requires_pairing": "1",
        "schema": "1",
    }


def test_encode_txt_ignores_keys_outside_the_closed_set():
    """Serialization filters by the allowlist, so a smuggled key never reaches the wire."""
    rdata = disc.encode_txt({"name": "box", "token": "sekrit-abc123", "port": "10166"})
    assert b"sekrit-abc123" not in rdata
    assert b"token" not in rdata
    assert disc.decode_txt(rdata) == {"name": "box", "port": "10166"}


def test_advertised_packet_contains_no_credential(_isolate):
    """The serialized announcement must not contain the gateway's secret or a session token.

    Asserted against the bytes that would leave the machine, with real credentials present
    on disk and in memory — "we did not add it" is a claim about the author's intent, and
    the thing worth testing is the packet.
    """
    local_secret = "s3cr3t-local-" + "f" * 32
    (_isolate / ".local_secret").write_text(local_secret, encoding="utf-8")
    session_token = "tok_" + "9" * 40
    (_isolate / "sessions.json").write_text(
        json.dumps({"sessions": [{"nonce": session_token, "device": "phone"}]}), encoding="utf-8"
    )
    enroll_code, _expires = _issue_enroll_code()

    packet = _service().packet()

    for secret in (local_secret, session_token, enroll_code):
        assert secret.encode() not in packet, f"a credential reached the wire: {secret[:8]}…"
    # And nothing that merely *looks* like a credential field, either.
    for word in (b"token", b"secret", b"password", b"nonce", b"session"):
        assert word not in packet.lower()


def _issue_enroll_code() -> tuple[str, float]:
    from personalclaw.auth import enrollment

    return enrollment.issue_code(label="test")


def test_instance_label_is_one_safe_dns_label():
    """A dot would silently re-home the service under a different type; 63 bytes is the max."""
    assert disc.instance_label("Living. room\tMac") == "Living room Mac"
    assert disc.instance_label("  spaced   out  ") == "spaced out"
    long_ascii = disc.instance_label("x" * 200)
    assert len(long_ascii.encode()) <= 63
    # Truncation lands on a codepoint boundary, so the label stays valid UTF-8.
    long_utf8 = disc.instance_label("é" * 60)
    assert len(long_utf8.encode()) <= 63
    assert long_utf8.encode().decode("utf-8") == long_utf8


# ── the advertise-or-not decision ──────────────────────────────────────────────


def test_loopback_only_bind_does_not_advertise_and_logs_why(caplog):
    """The no-op case: announcing 127.0.0.1 to a LAN publishes a broken record."""
    with caplog.at_level("INFO", logger="personalclaw.companion.discovery"):
        decision = disc.decide(enabled=True, bind_host="127.0.0.1", port=10166, instance_name="Mac")
    assert decision.advertise is False
    assert decision.reason == "loopback_only"
    assert decision.service is None
    logged = caplog.text
    assert "loopback" in logged.lower()
    # The log names the fix, not just the symptom.
    assert "PERSONALCLAW_BIND_HOST" in logged


def test_disabled_never_probes_the_network(monkeypatch):
    """Off is asked FIRST: a user who never opted in has no interface enumerated."""

    def _boom():
        raise AssertionError("interfaces were enumerated while discovery was disabled")

    monkeypatch.setattr(disc, "_primary_lan_ipv4", _boom)
    decision = disc.decide(enabled=False, bind_host="0.0.0.0", port=10166, instance_name="Mac")
    assert (decision.advertise, decision.reason) == (False, "disabled")


def test_no_lan_address_is_a_no_op(monkeypatch, caplog):
    monkeypatch.setattr(disc, "_primary_lan_ipv4", lambda: "")
    with caplog.at_level("INFO", logger="personalclaw.companion.discovery"):
        decision = disc.decide(enabled=True, bind_host="0.0.0.0", port=10166, instance_name="Mac")
    assert (decision.advertise, decision.reason) == (False, "no_lan_address")
    assert "no local-network address" in caplog.text


def test_advertises_when_bound_beyond_loopback():
    decision = disc.decide(
        enabled=True, bind_host=LAN_HOST, port=10166, instance_name="Living room Mac"
    )
    assert (decision.advertise, decision.reason) == (True, "advertising")
    assert decision.service is not None
    assert decision.service.address == LAN_HOST
    assert decision.service.port == 10166
    assert decision.service.instance == "Living room Mac._personalclaw._tcp.local."


def test_empty_instance_name_falls_back_to_the_hostname():
    decision = disc.decide(enabled=True, bind_host=LAN_HOST, port=10166, instance_name="")
    assert decision.service is not None
    assert decision.service.instance_name == socket.gethostname().split(".")[0]


def test_every_reason_has_a_human_sentence():
    """A closed reason set with a missing sentence would raise inside the status route."""
    for reason in ("advertising", "disabled", "loopback_only", "no_lan_address"):
        assert disc.Decision(False, reason).detail


# ── wire format ────────────────────────────────────────────────────────────────


def test_packet_round_trips_through_the_resolver():
    found = disc.collect([_service().packet()])
    assert len(found) == 1
    inst = found[0]
    assert inst.name == "Living room Mac"
    assert inst.port == 10166
    assert inst.addresses == [LAN_HOST]
    assert inst.base_url == f"http://{LAN_HOST}:10166"
    assert inst.requires_pairing is True


def test_goodbye_packet_withdraws_the_instance():
    """TTL 0 is how a stopping gateway avoids being cached at a dead port for two minutes."""
    svc = _service()
    assert disc.collect([svc.packet()])
    assert disc.collect([svc.packet(), svc.packet(ttl_zero=True)]) == []


def test_parse_message_rejects_malformed_input():
    with pytest.raises(ValueError):
        disc.parse_message(b"\x00\x01")  # shorter than a header
    # A compression-pointer loop must raise rather than spin: this parses unauthenticated
    # multicast traffic from any host on the network. The pointer at offset 12 targets
    # offset 12, so a naive decoder follows it forever.
    header = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    with pytest.raises(ValueError):
        disc.parse_message(header + b"\xc0\x0c")


def test_collect_ignores_unparseable_packets():
    assert disc.collect([b"garbage", _service().packet()])[0].name == "Living room Mac"


# ── real sockets ───────────────────────────────────────────────────────────────


# A free port cannot be RESERVED. `_free_udp_port` must close its probe socket before
# `start()` can bind the number it returned, and anything on the host — including the other
# xdist worker running this same file — may take it in that window. Measured on main's
# `Full` run 35764976454, where this fixture failed a macOS leg with `[Errno 48] Address
# already in use` (#3324). So acquire-and-bind is retried AS A UNIT: one attempt is a coin
# flip, and no probe can remove the race because the bind is a separate syscall.
_PORT_ATTEMPTS = 8


@pytest.fixture()
def live_advertiser():
    """A real Advertiser on a high port. Unicast-driven, so no multicast setup is needed."""
    for attempt in range(_PORT_ATTEMPTS):
        port = _free_udp_port()
        adv = disc.Advertiser(_service(), listen_port=port)
        if adv.start() is True:
            break
        if attempt == _PORT_ATTEMPTS - 1:
            pytest.fail(
                f"the advertiser could not open its socket on any of {_PORT_ATTEMPTS} "
                "separately probed free ports — that is port contention on this host, "
                "not a defect in the responder"
            )
    try:
        yield adv, port
    finally:
        adv.stop()


def _free_udp_port() -> int:
    """A port free for the WILDCARD bind `Advertiser.start` actually performs.

    Probing `127.0.0.1:0` answers a DIFFERENT question: a port can be free on loopback and
    held on another interface, and `start()` binds `("", port)`. So the probe binds the
    wildcard with the same `SO_REUSEADDR` the advertiser sets, and the two now agree about
    what "free" means.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def test_advertiser_answers_a_real_query_over_a_socket(live_advertiser):
    """The whole responder path: parse a query, build a response, put it on a socket."""
    _adv, port = live_advertiser
    found = disc.resolve(timeout=2.0, unicast_to=("127.0.0.1", port))
    assert [i.name for i in found] == ["Living room Mac"]
    assert found[0].base_url == f"http://{LAN_HOST}:10166"


def test_malformed_traffic_does_not_stop_the_responder(live_advertiser):
    """Garbage on the wire is expected input on a multicast group, not a fatal event."""
    _adv, port = live_advertiser
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for junk in (b"", b"\xff" * 40, b"\x00\x00\x00\x00\x00\x09"):
            sender.sendto(junk, ("127.0.0.1", port))
    finally:
        sender.close()
    time.sleep(0.2)
    assert [i.name for i in disc.resolve(timeout=2.0, unicast_to=("127.0.0.1", port))] == [
        "Living room Mac"
    ]


def test_resolver_returns_empty_when_nothing_answers():
    """Finding nothing is a normal answer — every caller keeps the type-the-URL path."""
    assert disc.resolve(timeout=0.5, unicast_to=("127.0.0.1", _free_udp_port())) == []


def test_stopped_advertiser_stops_answering(live_advertiser):
    adv, port = live_advertiser
    adv.stop()
    assert adv.running is False
    assert disc.resolve(timeout=0.5, unicast_to=("127.0.0.1", port)) == []


# ── the gateway-facing lifecycle ───────────────────────────────────────────────


def _enable_discovery(enabled: bool, name: str = "Living room Mac") -> None:
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.companion.discovery_enabled = enabled
    cfg.companion.instance_name = name
    cfg.save()


def test_default_config_does_not_advertise(stub_advertiser):
    """Off by default: a fresh install announces nothing."""
    disc.set_gateway_bind(LAN_HOST, 10166)
    decision = disc.reconcile()
    assert (decision.advertise, decision.reason) == (False, "disabled")
    assert stub_advertiser.instances == []


def test_reconcile_starts_then_stops_to_match_config(stub_advertiser):
    """The toggle takes effect live — no restart, so "on" and "advertising" are one state."""
    disc.set_gateway_bind(LAN_HOST, 10166)
    _enable_discovery(True)
    assert disc.reconcile().advertise is True
    assert len(stub_advertiser.instances) == 1
    assert stub_advertiser.instances[0].started is True

    _enable_discovery(False)
    assert disc.reconcile().advertise is False
    assert stub_advertiser.instances[0].stopped is True
    assert len(stub_advertiser.instances) == 1


def test_reconcile_is_idempotent(stub_advertiser):
    disc.set_gateway_bind(LAN_HOST, 10166)
    _enable_discovery(True)
    disc.reconcile()
    disc.reconcile()
    disc.reconcile()
    assert len(stub_advertiser.instances) == 1


def test_renaming_the_instance_restarts_the_advertiser(stub_advertiser):
    disc.set_gateway_bind(LAN_HOST, 10166)
    _enable_discovery(True, "Old name")
    disc.reconcile()
    _enable_discovery(True, "New name")
    disc.reconcile()
    assert len(stub_advertiser.instances) == 2
    assert stub_advertiser.instances[0].stopped is True
    assert stub_advertiser.instances[1].service.instance_name == "New name"


def test_reconcile_without_a_running_gateway_is_a_no_op(stub_advertiser):
    _enable_discovery(True)
    decision = disc.reconcile()
    assert (decision.advertise, decision.reason) == (False, "gateway_not_running")
    assert stub_advertiser.instances == []


def test_unreadable_config_fails_closed(stub_advertiser, monkeypatch):
    """This surface ANNOUNCES on a network: a broken read is not permission to broadcast."""
    from personalclaw.config import loader

    disc.set_gateway_bind(LAN_HOST, 10166)

    def _explode():
        raise OSError("config is gone")

    monkeypatch.setattr(loader.AppConfig, "load", staticmethod(_explode))
    assert disc.reconcile().advertise is False
    assert stub_advertiser.instances == []


def test_shutdown_stops_advertising_and_forgets_the_bind(stub_advertiser):
    disc.set_gateway_bind(LAN_HOST, 10166)
    _enable_discovery(True)
    disc.reconcile()
    disc.shutdown()
    assert stub_advertiser.instances[0].stopped is True
    assert disc.status()["reason"] == "gateway_not_running"


# ── legibility: the status surface ─────────────────────────────────────────────


def test_status_reports_live_state_not_the_config_flag(stub_advertiser):
    """A loopback-only gateway with discovery ON is the case a flag-only surface gets wrong."""
    disc.set_gateway_bind("127.0.0.1", 10166)
    _enable_discovery(True)
    disc.reconcile()
    st = disc.status()
    assert st["advertising"] is False
    assert st["reason"] == "loopback_only"
    assert "loopback" in str(st["detail"]).lower()
    assert st["txt"] == {}


def test_status_shows_exactly_what_is_broadcast(stub_advertiser):
    disc.set_gateway_bind(LAN_HOST, 10166)
    _enable_discovery(True)
    disc.reconcile()
    st = disc.status()
    assert st["advertising"] is True
    assert st["service_type"] == "_personalclaw._tcp.local."
    assert st["txt"] == {
        "name": "Living room Mac",
        "port": "10166",
        "requires_pairing": "1",
        "schema": "1",
    }
    assert st["addresses"] == [LAN_HOST]


class _LoopbackSite(web.TCPSite):
    """The gateway's listening site, on loopback whatever host it is handed. The host the gateway
    asked for is recorded, so a test can boot a gateway that binds beyond loopback without
    opening a socket beyond this machine."""

    asked: list[tuple[str, int]] = []

    def __init__(self, runner, host=None, port=None, **kwargs):
        _LoopbackSite.asked.append((host, port))
        super().__init__(runner, "127.0.0.1", port, **kwargs)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


#: How the gateway is started (``local_only``, its environment), the host it then binds, and what
#: discovery shows while it runs: the address advertised (None for nothing) and the reason.
_BINDS = [
    pytest.param(True, {}, "127.0.0.1", None, "loopback_only", id="loopback-by-default"),
    pytest.param(False, {}, "0.0.0.0", LAN_HOST, "advertising", id="every-interface"),
    pytest.param(
        False,
        {"PERSONALCLAW_AUTH_MODE": "none"},
        "127.0.0.1",
        None,
        "loopback_only",
        id="no-sign-in-stays-on-loopback",
    ),
    pytest.param(
        True,
        {"PERSONALCLAW_BIND_HOST": LAN_HOST},
        LAN_HOST,
        LAN_HOST,
        "advertising",
        id="host-named",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("local_only", "env", "bound", "address", "reason"), _BINDS)
async def test_the_gateway_advertises_where_it_bound_and_withdraws_when_it_stops(
    _isolate, monkeypatch, stub_advertiser, local_only, env, bound, address, reason
):
    """The real gateway (``start_dashboard``) with discovery on advertises the host and port it
    actually bound, or nothing on loopback, and when it stops it sends the goodbye and forgets
    the bind.

    The bind host is an outcome (the env var, then ``local_only``, then the rule that a gateway
    asking for no sign-in stays on loopback), so discovery has to be told it after it is decided.
    The goodbye is one of the gateway's stop hooks: registered beside the advertiser's start,
    after ``runner.setup()`` has frozen them, it raised ``Cannot modify frozen list``, the
    fail-safe swallowed it, and the record stayed up after the gateway had gone.
    """
    import personalclaw.dashboard.server as server_mod

    (_isolate / "user").mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(_isolate))
    monkeypatch.setenv("HOME", str(_isolate / "user"))
    for name in (
        "PERSONALCLAW_AUTH_MODE",
        "PERSONALCLAW_BIND_HOST",
        "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(disc, "_primary_lan_ipv4", lambda: LAN_HOST)
    _LoopbackSite.asked = []
    monkeypatch.setattr(server_mod.web, "TCPSite", _LoopbackSite)
    _enable_discovery(True)
    port = _free_port()

    runner, _state = await server_mod.start_dashboard(
        sessions=MagicMock(count=0), port=port, local_only=local_only
    )
    try:
        asked = [host for host, at in _LoopbackSite.asked if at == port]
        running = disc.status()
        advertisers = list(stub_advertiser.instances)
        live = [adv.running for adv in advertisers]
    finally:
        await runner.cleanup()
    stopped = disc.status()

    assert asked == [bound]
    assert running["reason"] == reason
    if address is None:
        assert (running["advertising"], advertisers) == (False, [])
    else:
        assert running["advertising"] is True
        assert (running["addresses"], running["port"]) == ([address], port)
        assert live == [True]
    assert all(adv.stopped for adv in advertisers)
    assert (stopped["advertising"], stopped["reason"]) == (False, "gateway_not_running")


def test_state_change_is_audited(stub_advertiser, monkeypatch):
    """SEL records the skips too — "I turned it on and nothing happened" is auditable."""
    rows = []
    monkeypatch.setattr(disc, "_audit", lambda decision: rows.append(decision.reason))
    disc.set_gateway_bind("127.0.0.1", 10166)
    _enable_discovery(True)
    disc.reconcile()
    disc.reconcile()  # unchanged state: no second row
    assert rows == ["loopback_only"]


def test_audit_emits_a_real_sel_row(_isolate, stub_advertiser):
    from personalclaw.sel import SecurityEventLog

    SecurityEventLog._instance = None
    SecurityEventLog._initialized = False
    try:
        log = SecurityEventLog(base_dir=_isolate)
        with patch("personalclaw.sel.sel", return_value=log):
            disc._audit(
                disc.decide(
                    enabled=True, bind_host=LAN_HOST, port=10166, instance_name="Living room Mac"
                )
            )
        rows = log.recent(limit=10)
        assert [r for r in rows if r.get("event_type") == "companion_discovery"], rows
        row = next(r for r in rows if r.get("event_type") == "companion_discovery")
        assert row["operation"] == "discovery_advertise_started"
        assert row["outcome"] == "advertising"
        # An audit row about a broadcast must not itself carry a credential.
        assert "token" not in json.dumps(row).lower()
    finally:
        SecurityEventLog._instance = None
        SecurityEventLog._initialized = False


# ── degradability: discovery is never a precondition ───────────────────────────


def test_discovery_off_leaves_the_manual_pairing_path_working(_isolate, stub_advertiser):
    """With discovery off, the typed-URL + code path is untouched.

    The manual path is `personalclaw auth enroll` → redeem the code from the other device,
    reached at a URL the user typed (or scanned from a QR, which renders the same URL). None
    of it consults discovery, and this asserts that by exercising it with discovery off.
    """
    from personalclaw.auth import enrollment

    disc.set_gateway_bind(LAN_HOST, 10166)
    _enable_discovery(False)
    assert disc.reconcile().advertise is False
    assert stub_advertiser.instances == []

    # The code path still mints and redeems, exactly once, with nothing advertised.
    code, expires_at = enrollment.issue_code(label="phone")
    assert expires_at > time.time()
    assert enrollment.redeem_code(code) is True
    assert enrollment.redeem_code(code) is False


def test_discovery_module_never_touches_the_auth_rail():
    """Discovery answers WHERE, never WHO MAY. It must not import the auth layer.

    Structural, because the failure mode is a future edit "helpfully" putting a code or a
    token in the TXT record to save a round trip — which would publish a credential to
    every device on the network.
    """
    import ast
    import pathlib

    source = pathlib.Path(disc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    assert not [m for m in imported if m.startswith("personalclaw.auth")], imported


def test_the_route_returns_the_status_payload(stub_advertiser):
    """The handler is a thin read over status() — no second vocabulary for the same state."""
    import asyncio

    from personalclaw.dashboard.handlers import api_companion_discovery

    disc.set_gateway_bind(LAN_HOST, 10166)
    _enable_discovery(True)
    disc.reconcile()

    response = asyncio.run(api_companion_discovery(object()))  # type: ignore[arg-type]
    body = json.loads(response.body)
    assert body == disc.status()
    assert body["advertising"] is True
    assert body["reason"] == "advertising"
    assert set(body["txt"]) == {"name", "port", "requires_pairing", "schema"}
