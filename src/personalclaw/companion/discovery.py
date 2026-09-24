"""Optional LAN discovery for companion clients (COMPANION-APPS C3 / S2).

The problem: a phone on the same Wi-Fi cannot guess ``http://192.168.1.37:10000``. Today
you read the LAN address off the machine and type it in. That works, and it keeps working
— this module never becomes a precondition for anything. It just removes the typing.

So: an **opt-in** mDNS/DNS-SD advertiser for ``_personalclaw._tcp.local.``, plus the
resolver a client uses to find it. Both sides speak the DNS wire format directly over a
multicast UDP socket; there is no third-party responder in the dependency set, and the
bytes that leave this machine are built here, in one function, where they can be read.

**What this deliberately does NOT do — read this before extending it:**

* **It carries no credential and no content.** The TXT record is built from a closed key
  set (:data:`_TXT_KEYS`) holding a name, a port, and two constants. A discovery packet is
  a broadcast: everything in it is public to every device on the network, forever, with no
  authentication. So it says only *"a PersonalClaw gateway is here, and it will want you
  to pair"*. Pairing itself is the auth layer's job and is unchanged by this module.
* **It does not widen access.** Discovery tells a client *where* to knock; the token rail
  still decides *whether* it gets in. Announcing an address grants nothing — a gateway
  that was already reachable at that address is exactly as reachable afterwards.
* **It is off by default** (``companion.discovery_enabled``), because announcing a service
  on a network you do not fully control is a posture choice, not a convenience default.
* **It refuses to advertise a loopback-only gateway.** Announcing ``127.0.0.1`` to the LAN
  would publish an address that resolves, on every other device, to *that device* — a
  guaranteed-broken record, and a small lie about what this machine offers. So the
  loopback case is a no-op with a log line that says why (:func:`decide`).

Everything is best-effort by construction: a socket this module cannot open, a network
that drops multicast, a router that filters it — none of that may affect the gateway. A
failed advertiser degrades to "type the URL", which is the path that existed before.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── DNS-SD constants (RFC 6762 mDNS / RFC 6763 DNS-SD) ─────────────────────────

#: The IPv4 mDNS group and port. Both are fixed by RFC 6762 — a resolver looks nowhere
#: else — but they stay parameters on the advertiser/resolver so a test can drive the real
#: socket path on a port that is not already owned by the host's own responder.
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

#: The service this gateway advertises. Registered form: ``_personalclaw._tcp``.
SERVICE_TYPE = "_personalclaw._tcp.local."

_TYPE_A = 1
_TYPE_PTR = 12
_TYPE_TXT = 16
_TYPE_SRV = 33
_TYPE_ANY = 255
_CLASS_IN = 1
#: Top bit of the class field in a response: "this record replaces what you cached".
_CACHE_FLUSH = 0x8000

#: RFC 6762 §10 record lifetimes: two minutes for the host-specific records (so a gateway
#: that vanishes without a goodbye is forgotten quickly), 75 minutes for the shared PTR.
_TTL_HOST = 120
_TTL_PTR = 4500

#: One DNS label is at most 63 bytes. The instance name is a single label.
_MAX_LABEL_BYTES = 63

#: The ONLY keys a TXT record may carry (COMPANION-APPS C3). This is a closed set on
#: purpose: the guard against a credential or a piece of content ending up in a broadcast
#: is not reviewer vigilance, it is that :func:`build_txt` cannot express one. Adding a key
#: here means arguing, in review, that every device on every network the owner ever joins
#: may read it.
_TXT_KEYS = ("name", "port", "requires_pairing", "schema")

#: TXT schema version, so a future client can tell what it is looking at.
_TXT_SCHEMA = "1"


# ── name / record encoding ─────────────────────────────────────────────────────


def _encode_name(name: str) -> bytes:
    """Encode a dotted DNS name as length-prefixed labels.

    No compression pointers are emitted. Compression is optional for a sender (RFC 1035
    §4.1.4) and costs a few dozen bytes here; not emitting it removes a whole class of
    offset bug from the code that builds packets other people's resolvers must parse.
    """
    out = bytearray()
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")[:_MAX_LABEL_BYTES]
        if not raw:
            continue
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def _decode_name(buf: bytes, offset: int) -> tuple[str, int]:
    """Decode a DNS name at *offset*, following compression pointers.

    Returns ``(name, offset_after_the_name_in_the_wire)``. Pointers must be followed on
    the read side even though we never write them: other responders (macOS mDNSResponder,
    Avahi) compress aggressively, so a resolver that ignored pointers would read garbage.

    A pointer loop would hang a resolver on attacker-controlled input, so hops are capped.
    """
    parts: list[str] = []
    end_offset = -1
    hops = 0
    while True:
        if offset >= len(buf):
            raise ValueError("name runs past the end of the message")
        length = buf[offset]
        if length & 0xC0 == 0xC0:
            if offset + 2 > len(buf):
                raise ValueError("truncated compression pointer")
            pointer = struct.unpack_from("!H", buf, offset)[0] & 0x3FFF
            if end_offset < 0:
                end_offset = offset + 2
            hops += 1
            if hops > 16:
                raise ValueError("compression pointer loop")
            offset = pointer
            continue
        offset += 1
        if length == 0:
            break
        if offset + length > len(buf):
            raise ValueError("truncated label")
        parts.append(buf[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return (".".join(parts) + "." if parts else "."), (end_offset if end_offset >= 0 else offset)


def _record(name: str, rtype: int, rdata: bytes, ttl: int, *, flush: bool) -> bytes:
    klass = _CLASS_IN | (_CACHE_FLUSH if flush else 0)
    return _encode_name(name) + struct.pack("!HHIH", rtype, klass, ttl, len(rdata)) + rdata


def encode_txt(txt: dict[str, str]) -> bytes:
    """Encode a TXT record's rdata: one length-prefixed ``key=value`` string per entry.

    An empty TXT record is a single zero byte, never zero bytes (RFC 6763 §6.1) — a
    zero-length rdata is what a resolver reads as "no such record".
    """
    out = bytearray()
    for key in _TXT_KEYS:
        if key not in txt:
            continue
        entry = f"{key}={txt[key]}".encode()[:255]
        out.append(len(entry))
        out += entry
    return bytes(out) if out else b"\x00"


def decode_txt(rdata: bytes) -> dict[str, str]:
    """Decode TXT rdata into a dict, keeping only well-formed ``key=value`` strings."""
    out: dict[str, str] = {}
    i = 0
    while i < len(rdata):
        length = rdata[i]
        i += 1
        chunk = rdata[i : i + length]
        i += length
        if b"=" not in chunk:
            continue
        key, _, value = chunk.partition(b"=")
        out[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return out


def instance_label(instance_name: str) -> str:
    """Reduce a user-typed instance name to one safe DNS-SD label.

    DNS-SD allows a rich first label (spaces and UTF-8 are legal, RFC 6763 §4.1.1), so the
    friendly name survives. What must not survive: dots (they would split the label and
    silently re-home the service under a different type), control characters, and anything
    past 63 *bytes* — the truncation is on a codepoint boundary so a multi-byte name never
    becomes invalid UTF-8 on the wire.
    """
    # Dots and control characters become spaces rather than vanishing: dropping the
    # separator out of "Living.room Mac" would silently glue two words together.
    cleaned = " ".join(
        "".join(" " if ch == "." or ch < " " else ch for ch in instance_name).split()
    )
    raw = cleaned.encode("utf-8")
    if len(raw) > _MAX_LABEL_BYTES:
        cleaned = raw[:_MAX_LABEL_BYTES].decode("utf-8", "ignore")
    return cleaned


def build_txt(*, instance_name: str, port: int) -> dict[str, str]:
    """Build the advertised TXT record. The ONLY place a TXT key is chosen.

    Every value here is already public to anyone on the network: the friendly name the
    owner typed, the port the gateway is listening on, and two constants. ``requires_pairing``
    exists so a client can say "you will need to pair" before it ever touches the gateway,
    rather than discovering it from a 401.
    """
    return {
        "name": instance_label(instance_name),
        "port": str(int(port)),
        "requires_pairing": "1",
        "schema": _TXT_SCHEMA,
    }


# ── message parsing (used by both sides) ───────────────────────────────────────


@dataclass(frozen=True)
class ResourceRecord:
    name: str
    rtype: int
    rdata: bytes
    ttl: int


@dataclass(frozen=True)
class Message:
    """A parsed DNS message — only the parts DNS-SD needs."""

    txn_id: int
    is_response: bool
    questions: tuple[tuple[str, int], ...]
    records: tuple[ResourceRecord, ...]


def parse_message(buf: bytes) -> Message:
    """Parse a DNS message. Raises ``ValueError`` on anything malformed.

    Every field is bounds-checked because this parses unauthenticated multicast traffic
    from arbitrary hosts: the only acceptable outcomes are a parsed message or a raised
    ``ValueError`` the caller drops.
    """
    if len(buf) < 12:
        raise ValueError("message shorter than a DNS header")
    txn_id, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", buf, 0)
    offset = 12
    questions: list[tuple[str, int]] = []
    for _ in range(qd):
        name, offset = _decode_name(buf, offset)
        if offset + 4 > len(buf):
            raise ValueError("truncated question")
        qtype = struct.unpack_from("!H", buf, offset)[0]
        offset += 4
        questions.append((name, qtype))
    records: list[ResourceRecord] = []
    for _ in range(an + ns + ar):
        name, offset = _decode_name(buf, offset)
        if offset + 10 > len(buf):
            raise ValueError("truncated record header")
        rtype, _klass, ttl, rdlen = struct.unpack_from("!HHIH", buf, offset)
        offset += 10
        if offset + rdlen > len(buf):
            raise ValueError("truncated record data")
        rdata = buf[offset : offset + rdlen]
        offset += rdlen
        records.append(ResourceRecord(name=name, rtype=rtype, rdata=rdata, ttl=ttl))
    return Message(
        txn_id=txn_id,
        is_response=bool(flags & 0x8000),
        questions=tuple(questions),
        records=tuple(records),
    )


# ── what gets advertised ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServiceInfo:
    """Everything advertised about this gateway. Built once, then only serialized."""

    instance_name: str
    hostname: str
    port: int
    address: str
    txt: dict[str, str]

    @property
    def instance(self) -> str:
        return f"{instance_label(self.instance_name)}.{SERVICE_TYPE}"

    @property
    def host(self) -> str:
        return f"{self.hostname}.local."

    def packet(self, *, ttl_zero: bool = False, question: tuple[str, int] | None = None) -> bytes:
        """Serialize the full record set as one mDNS response.

        ``ttl_zero`` builds the **goodbye** packet: identical records with TTL 0, which is
        how a responder tells the network to forget it immediately instead of leaving a
        stale address cached for two minutes after the gateway stops.

        ``question`` echoes a question back, which one-shot (non-5353-source) resolvers
        expect; multicast responses carry no question.
        """
        ttl_host = 0 if ttl_zero else _TTL_HOST
        ttl_ptr = 0 if ttl_zero else _TTL_PTR
        srv = struct.pack("!HHH", 0, 0, int(self.port)) + _encode_name(self.host)
        answers = [
            _record(SERVICE_TYPE, _TYPE_PTR, _encode_name(self.instance), ttl_ptr, flush=False),
            _record(self.instance, _TYPE_SRV, srv, ttl_host, flush=True),
            _record(self.instance, _TYPE_TXT, encode_txt(self.txt), ttl_host, flush=True),
        ]
        try:
            answers.append(
                _record(self.host, _TYPE_A, socket.inet_aton(self.address), ttl_host, flush=True)
            )
        except OSError:
            # No parseable IPv4 to publish. The SRV target still names the host, which a
            # Bonjour-capable client resolves itself — better a record set without an A
            # than no announcement at all.
            logger.debug(
                "discovery: %r is not an IPv4 address; omitting the A record", self.address
            )
        body = b"".join(answers)
        qsection = b""
        qdcount = 0
        if question is not None:
            qsection = _encode_name(question[0]) + struct.pack("!HH", question[1], _CLASS_IN)
            qdcount = 1
        header = struct.pack("!HHHHHH", 0, 0x8400, qdcount, len(answers), 0, 0)
        return header + qsection + body


# ── the advertise-or-not decision ──────────────────────────────────────────────

#: Closed set of reasons the advertiser is or is not running. The human sentence lives
#: beside the code so the API, the log line and the settings panel all say the same thing
#: — a second vocabulary in the frontend is how "off" and "on but inert" become
#: indistinguishable to a user.
_REASONS = {
    "advertising": "Advertising on the local network.",
    "disabled": "LAN discovery is off — companion apps need the URL typed in.",
    "loopback_only": (
        "LAN discovery is on, but this gateway is bound to loopback only, so nothing on "
        "your network could reach it. Not advertising."
    ),
    "no_lan_address": (
        "LAN discovery is on, but this machine has no local-network address to advertise. "
        "Not advertising."
    ),
    "gateway_not_running": "The gateway is not serving, so there is nothing to advertise.",
}


@dataclass(frozen=True)
class Decision:
    """Whether to advertise, why, and (when advertising) exactly what."""

    advertise: bool
    reason: str
    service: ServiceInfo | None = None

    @property
    def detail(self) -> str:
        return _REASONS[self.reason]


def _primary_lan_ipv4() -> str:
    """The address other devices on this LAN would see, or ``""``.

    Asks the kernel which source address it would use to reach the mDNS group. A UDP
    ``connect`` transmits nothing — it only installs a route — so this is a pure question,
    and it answers the right one: the interface that actually carries multicast, not
    whatever ``gethostname()`` happens to resolve to.
    """
    from personalclaw.dashboard.origin import is_loopback

    addr = ""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((MDNS_GROUP, MDNS_PORT))
            addr = str(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        addr = ""
    if addr and not is_loopback(addr):
        return addr
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidate = str(info[4][0])
            if not is_loopback(candidate):
                return candidate
    except (OSError, socket.gaierror):
        pass
    return ""


def decide(*, enabled: bool, bind_host: str, port: int, instance_name: str) -> Decision:
    """Decide whether to advertise. Pure apart from reading this machine's addresses.

    The order matters. ``enabled`` is asked first so a user who never opted in never has
    their interfaces enumerated. Then the loopback check: a gateway bound to ``127.0.0.1``
    is unreachable from every other device, so advertising it would publish a record that
    is wrong on arrival — a no-op plus a log line is the honest outcome, and the log names
    the fix rather than just the symptom.
    """
    from personalclaw.dashboard.origin import is_loopback, machine_hostname

    if not enabled:
        return Decision(False, "disabled")
    if not bind_host:
        return Decision(False, "gateway_not_running")
    if is_loopback(bind_host):
        logger.info(
            "LAN discovery is enabled but the gateway is bound to loopback only (%s) — not "
            "advertising: a %s record naming 127.0.0.1 resolves to the *client* on every "
            "other device. Bind beyond loopback (PERSONALCLAW_BIND_HOST=0.0.0.0) to advertise.",
            bind_host,
            SERVICE_TYPE,
        )
        return Decision(False, "loopback_only")
    address = bind_host if bind_host != "0.0.0.0" else _primary_lan_ipv4()
    if not address or is_loopback(address):
        logger.info(
            "LAN discovery is enabled but no local-network address was found to advertise "
            "(bind host %s) — not advertising.",
            bind_host,
        )
        return Decision(False, "no_lan_address")
    hostname = (machine_hostname() or "personalclaw").split(".")[0]
    name = instance_label(instance_name) or hostname
    return Decision(
        True,
        "advertising",
        ServiceInfo(
            instance_name=name,
            hostname=hostname,
            port=int(port),
            address=address,
            txt=build_txt(instance_name=name, port=int(port)),
        ),
    )


# ── the advertiser ─────────────────────────────────────────────────────────────


class Advertiser:
    """Answers mDNS queries for one :class:`ServiceInfo` from a daemon thread.

    A thread rather than an asyncio task because the work is one blocking ``recvfrom`` on
    one socket: a thread expresses that in ten lines, and it lets the whole module be
    driven by a test with no event loop. It owns nothing the gateway needs, so a crash in
    here can only ever cost discovery.
    """

    def __init__(
        self,
        service: ServiceInfo,
        *,
        group: str = MDNS_GROUP,
        listen_port: int = MDNS_PORT,
    ) -> None:
        self.service = service
        self._group = group
        self._listen_port = listen_port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> bool:
        """Open the socket, announce, and serve queries. ``False`` if the socket failed.

        The host's own responder already owns port 5353, so ``SO_REUSEPORT`` is required
        rather than optional: without it this returns False on every desktop OS and
        discovery would be a feature that only ever worked on paper.
        """
        if self._thread is not None:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reuse_port = getattr(socket, "SO_REUSEPORT", None)
            if reuse_port is not None:
                sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
            sock.bind(("", self._listen_port))
            mreq = struct.pack("4s4s", socket.inet_aton(self._group), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.settimeout(1.0)
        except OSError:
            logger.warning(
                "LAN discovery could not open the mDNS socket on port %d — companion apps "
                "will need the URL typed in. Everything else is unaffected.",
                self._listen_port,
                exc_info=True,
            )
            return False
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="companion-discovery", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Send the goodbye, then close. Idempotent."""
        self._stop.set()
        sock, self._sock = self._sock, None
        thread, self._thread = self._thread, None
        if sock is not None:
            try:
                sock.sendto(self.service.packet(ttl_zero=True), (self._group, self._listen_port))
            except OSError:
                logger.debug("discovery goodbye send failed", exc_info=True)
            sock.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- the loop -------------------------------------------------------------

    def announce(self) -> None:
        """Multicast the record set unsolicited, so clients already listening hear it."""
        sock = self._sock
        if sock is None:
            return
        try:
            sock.sendto(self.service.packet(), (self._group, self._listen_port))
        except OSError:
            logger.debug("discovery announce failed", exc_info=True)

    def _serve(self) -> None:
        # RFC 6762 §8.3 asks for a small burst of announcements rather than one, since a
        # single multicast datagram is allowed to vanish and nothing retransmits it. The
        # burst is INTERLEAVED with the receive loop, not run before it: sleeping through
        # the first three seconds meant a client that queried immediately after startup got
        # silence, which is exactly when a companion app asks.
        announces_left = 3
        next_announce = 0.0
        while not self._stop.is_set():
            if announces_left and time.monotonic() >= next_announce:
                self.announce()
                announces_left -= 1
                next_announce = time.monotonic() + 1.0
            sock = self._sock
            if sock is None:
                return
            try:
                data, addr = sock.recvfrom(9000)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            try:
                self._handle(data, addr)
            except Exception:
                # Unauthenticated multicast input: a malformed packet is expected traffic,
                # never a reason to stop answering.
                logger.debug("discovery: dropped a malformed query from %s", addr, exc_info=True)

    def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        msg = parse_message(data)
        if msg.is_response:
            return
        wanted = self._matching_question(msg)
        if wanted is None:
            return
        sock = self._sock
        if sock is None:
            return
        # RFC 6762 §5.4: a query whose source port is not 5353 is a one-shot resolver and
        # wants a unicast answer with its question echoed back.
        if addr[1] != self._listen_port:
            sock.sendto(self.service.packet(question=wanted), addr)
        else:
            sock.sendto(self.service.packet(), (self._group, self._listen_port))

    def _matching_question(self, msg: Message) -> tuple[str, int] | None:
        """The first question this service should answer, or ``None``.

        Matching is case-insensitive because DNS names are (RFC 4343) and real resolvers
        vary the case of the service type between queries.
        """
        mine = {
            SERVICE_TYPE.lower(): (_TYPE_PTR,),
            self.service.instance.lower(): (_TYPE_SRV, _TYPE_TXT),
            self.service.host.lower(): (_TYPE_A,),
        }
        for name, qtype in msg.questions:
            types = mine.get(name.lower())
            if types is not None and (qtype == _TYPE_ANY or qtype in types):
                return (name, qtype)
        return None


# ── the client resolver ────────────────────────────────────────────────────────


@dataclass
class DiscoveredInstance:
    """One gateway a client found. ``base_url`` is where pairing starts."""

    name: str = ""
    hostname: str = ""
    port: int = 0
    addresses: list[str] = field(default_factory=list)
    txt: dict[str, str] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        """``http://<addr-or-host>:<port>`` — empty until a port is known.

        Prefers the advertised IPv4 over the ``.local`` hostname: a client that resolves
        ``.local`` needs its own mDNS stack, and the whole point of discovery is to hand a
        client something it can use immediately.
        """
        host = self.addresses[0] if self.addresses else self.hostname.rstrip(".")
        return f"http://{host}:{self.port}" if host and self.port else ""

    @property
    def requires_pairing(self) -> bool:
        return self.txt.get("requires_pairing") == "1"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "hostname": self.hostname,
            "port": self.port,
            "addresses": list(self.addresses),
            "base_url": self.base_url,
            "requires_pairing": self.requires_pairing,
            "txt": dict(self.txt),
        }


def collect(messages: list[bytes]) -> list[DiscoveredInstance]:
    """Fold raw mDNS responses into the instances they describe.

    Split out from the socket so the parsing half is testable against exact bytes, and so
    a caller holding packets from anywhere (a capture, a second transport) can reuse it.
    Answers arrive spread across datagrams — PTR here, SRV/TXT there — so records are
    accumulated by instance name and only then assembled.
    """
    srv: dict[str, tuple[str, int]] = {}
    txt: dict[str, dict[str, str]] = {}
    addrs: dict[str, list[str]] = {}
    seen: set[str] = set()
    for raw in messages:
        try:
            msg = parse_message(raw)
        except ValueError:
            continue
        for rec in msg.records:
            if rec.rtype == _TYPE_PTR and rec.name.lower() == SERVICE_TYPE.lower():
                try:
                    target, _ = _decode_name(rec.rdata, 0)
                except ValueError:
                    continue
                if rec.ttl > 0:
                    seen.add(target)
                else:
                    seen.discard(target)  # a goodbye: the gateway said it is going away
            elif rec.rtype == _TYPE_SRV and len(rec.rdata) >= 7:
                port = struct.unpack_from("!H", rec.rdata, 4)[0]
                try:
                    target, _ = _decode_name(rec.rdata, 6)
                except ValueError:
                    continue
                srv[rec.name] = (target, port)
            elif rec.rtype == _TYPE_TXT:
                txt[rec.name] = decode_txt(rec.rdata)
            elif rec.rtype == _TYPE_A and len(rec.rdata) == 4:
                addrs.setdefault(rec.name, []).append(socket.inet_ntoa(rec.rdata))
    out: list[DiscoveredInstance] = []
    for instance in sorted(seen):
        host, port = srv.get(instance, ("", 0))
        record = txt.get(instance, {})
        out.append(
            DiscoveredInstance(
                name=record.get("name") or instance.split("." + SERVICE_TYPE)[0],
                hostname=host,
                port=port or int(record.get("port") or 0),
                addresses=addrs.get(host, []),
                txt=record,
            )
        )
    return out


def resolve(
    *,
    timeout: float = 2.0,
    group: str = MDNS_GROUP,
    listen_port: int = MDNS_PORT,
    unicast_to: tuple[str, int] | None = None,
) -> list[DiscoveredInstance]:
    """Look for gateways on the local network. Never raises; ``[]`` means "found none".

    This is the client half of the contract, kept here so a companion app (or
    ``personalclaw doctor``) has one implementation to call rather than each wrapper
    writing its own. It queries from an **ephemeral** source port, which per RFC 6762 §5.4
    makes every responder answer by unicast — so this works even while something else on
    the machine owns port 5353.

    ``[]`` is a normal answer, not an error: multicast is routinely filtered on guest and
    corporate Wi-Fi. A caller must always keep the "type the URL" path.
    """
    query = (
        struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
        + _encode_name(SERVICE_TYPE)
        + struct.pack("!HH", _TYPE_PTR, _CLASS_IN)
    )
    packets: list[bytes] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        logger.debug("discovery resolve: no socket", exc_info=True)
        return []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.settimeout(0.25)
        sock.sendto(query, unicast_to or (group, listen_port))
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(9000)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            packets.append(data)
    except OSError:
        logger.debug("discovery resolve failed", exc_info=True)
    finally:
        sock.close()
    return collect(packets)


# ── process-wide state: the gateway's advertiser ───────────────────────────────

_lock = threading.Lock()
_advertiser: Advertiser | None = None
_bind: tuple[str, int] | None = None
_last_reason = ""


def set_gateway_bind(host: str, port: int) -> None:
    """Record where the gateway actually bound. Called once, by the gateway, at startup.

    The bind address is not in config — it is the outcome of the env var, the auth mode's
    loopback invariant and the caller's flags — so the advertiser must be told rather than
    infer it. Inferring it is how you end up advertising a gateway that is not there.
    """
    global _bind
    with _lock:
        _bind = (host, int(port))


def _decision_locked() -> Decision:
    from personalclaw.config.loader import AppConfig

    if _bind is None:
        return Decision(False, "gateway_not_running")
    try:
        companion = AppConfig.load().companion
    except Exception:
        # Fail closed. This surface *announces on a network*; an unreadable config is not
        # permission to broadcast, so a broken read means "stay quiet" rather than
        # "assume the default" (the availability-fail-open rule is for reading knobs, not
        # for taking an action the user may never have asked for).
        logger.warning("discovery: config unreadable — not advertising", exc_info=True)
        return Decision(False, "disabled")
    return decide(
        enabled=bool(companion.discovery_enabled),
        bind_host=_bind[0],
        port=_bind[1],
        instance_name=str(companion.instance_name or ""),
    )


def reconcile() -> Decision:
    """Make the running advertiser match config. Idempotent; safe to call on every PATCH.

    This is what keeps the settings toggle from being a control that needs a restart to
    mean anything: the gateway calls it once at startup, and the config PATCH path calls
    it again, so "on" and "advertising" are the same state rather than two.
    """
    global _advertiser, _last_reason
    with _lock:
        decision = _decision_locked()
        current = _advertiser
        if decision.advertise and decision.service is not None:
            if current is not None and current.service == decision.service and current.running:
                return decision
            if current is not None:
                current.stop()
            fresh = Advertiser(decision.service)
            if not fresh.start():
                _advertiser = None
                _last_reason = ""
                return Decision(False, "no_lan_address")
            _advertiser = fresh
        elif current is not None:
            current.stop()
            _advertiser = None
        if decision.reason != _last_reason:
            _last_reason = decision.reason
            _audit(decision)
        return decision


def status() -> dict[str, object]:
    """What discovery is *actually* doing, for the API and the settings panel.

    Reports the live advertiser, not the config flag. "Enabled" and "advertising" can
    legitimately differ (a loopback-only gateway is the designed case), and a surface that
    showed only the flag would render that difference as success.
    """
    with _lock:
        decision = _decision_locked()
        live = _advertiser is not None and _advertiser.running
        service = _advertiser.service if _advertiser is not None else decision.service
    return {
        "advertising": live,
        "reason": decision.reason,
        "detail": decision.detail,
        "service_type": SERVICE_TYPE,
        "instance_name": service.instance_name if service else "",
        "port": service.port if service else 0,
        "addresses": [service.address] if service and service.address else [],
        "txt": dict(service.txt) if service else {},
    }


def shutdown() -> None:
    """Stop advertising and forget the bind. Called when the gateway stops."""
    global _advertiser, _bind, _last_reason
    with _lock:
        if _advertiser is not None:
            _advertiser.stop()
        _advertiser = None
        _bind = None
        _last_reason = ""


def _audit(decision: Decision) -> None:
    """One SEL row per change of discovery state (COMPANION-APPS §SEL).

    The *skips* are audited too, not just the starts: "the owner turned LAN discovery on
    and nothing was announced" is exactly the kind of thing an audit log should be able to
    answer later. Never raises — an audit failure must not stop or start a broadcast.
    """
    try:
        import uuid
        from datetime import UTC, datetime

        from personalclaw.sel import SecurityEvent, sel

        service = decision.service
        sel().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(UTC).isoformat(),
                event_type="companion_discovery",
                caller_identity="gateway",
                agent="personalclaw",
                source="background",
                operation=(
                    "discovery_advertise_started" if decision.advertise else "discovery_stopped"
                ),
                outcome=decision.reason,
                resources=(
                    f"service={SERVICE_TYPE} port={service.port}" if service else SERVICE_TYPE
                ),
            )
        )
    except Exception:
        logger.debug("discovery SEL emit failed", exc_info=True)
