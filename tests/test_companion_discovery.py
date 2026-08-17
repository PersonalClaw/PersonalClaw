"""LAN discovery for companion clients (COMPANION-APPS CA-5).

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
from unittest.mock import patch

import pytest

from personalclaw.companion import discovery as disc

LAN_HOST = "192.168.1.37"


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    """Point config_dir/config_path at a temp home and reset the module's process state.

    ``discovery`` holds a process-global advertiser (the gateway has exactly one), so a test
    that left one running would leak a live socket — and an announcement — into the next.
    """
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.config.loader.config_path", return_value=cfg),
    ):
        disc.shutdown()
        yield tmp_path
        disc.shutdown()


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
    """The record may carry only the four C3 keys — a new one has to be argued for here."""
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
    """The atom's no-op case: announcing 127.0.0.1 to a LAN publishes a broken record."""
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


@pytest.fixture()
def live_advertiser():
    """A real Advertiser on a high port. Unicast-driven, so no multicast setup is needed."""
    port = _free_udp_port()
    adv = disc.Advertiser(_service(), listen_port=port)
    assert adv.start() is True, "the advertiser could not open its socket"
    try:
        yield adv, port
    finally:
        adv.stop()


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
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
    """Off by default: a fresh install announces nothing (Success Criterion 5)."""
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


def test_discovery_cleanup_is_registered_before_the_app_is_frozen():
    """The goodbye handler must be appended BEFORE ``runner.setup()`` freezes ``on_cleanup``.

    Found by driving a real gateway: registering it beside the advertiser's start — which is
    where the bind host becomes known, and therefore the obvious place — raised
    ``RuntimeError: Cannot modify frozen list``. The fail-safe swallowed it, so the gateway
    came up fine and discovery simply never withdrew its record on shutdown. An ordering rail
    because the constraint IS an ordering, and nothing else in the file expresses it.
    """
    import pathlib

    from personalclaw.dashboard import server

    src = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    # Both raise ValueError if absent, so this rail can never pass vacuously.
    append_at = src.index("app.on_cleanup.append(_discovery_shutdown)")
    freeze_at = src.index("await runner.setup()")
    assert append_at < freeze_at, (
        "on_cleanup is frozen by runner.setup(); registering the discovery shutdown after it "
        "raises RuntimeError('Cannot modify frozen list.')"
    )
    # And the start must stay AFTER the bind decision, which is what it depends on.
    assert src.index("_discovery.set_gateway_bind(") > src.index("_bind_host = resolve_bind_host()")


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
    """Success Criterion 5: with discovery off, the typed-URL + code path is untouched.

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


def test_pairing_cannot_depend_on_discovery():
    """Structural: the auth layer must not import discovery, so it can never gate pairing.

    "Degradable" is only true if it cannot be made untrue by a later edit. If enrollment
    ever imported this module, turning discovery off would become a way to break pairing.
    """
    import ast
    import pathlib

    from personalclaw.auth import enrollment

    tree = ast.parse(pathlib.Path(enrollment.__file__).read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
    assert not [m for m in modules if "companion" in m], modules


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
