"""Detect a reachable local Ollama for the onboarding zero-key on-ramp (OU-13).

Two discovery modes, both non-destructive and credential-free, both built on the
SAME ``/api/tags`` probe the ``--seed-local-model`` path already ships
(:mod:`personalclaw.seed_local_model`) — there is no second socket and no provider
SDK on this path:

* :func:`detect_localhost` — the loopback case. A stock Ollama listens on
  ``http://localhost:11434``; probing it is a loopback round-trip, not a network
  scan, so the onboarding step may run it automatically.
* :func:`scan_local_network` — the LAN case. An OPT-IN, time-bounded sweep of the
  host's own private (RFC-1918) ``/24``(s) for an endpoint answering ``/api/tags``
  on Ollama's default port. It is never run without an explicit user action (the
  gateway route that calls it is a ``POST`` the wizard fires from a button), and it
  probes ONLY private addresses — every candidate is re-classified through
  :func:`personalclaw.net.guard.classify_host` and a non-private verdict is dropped
  before a single connection is opened, so a public host is never contacted.

Both modes surface an endpoint ONLY after a live ``/api/tags`` response that
contained a chat-capable model — a discovered card is never shown on a guess.

No credential is read or written anywhere here; binding a discovered endpoint is
:func:`personalclaw.seed_local_model.bind_local_model`, whose credential-free
contract this module deliberately does not duplicate.
"""

from __future__ import annotations

import concurrent.futures as cf
import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Callable

from personalclaw.seed_local_model import DEFAULT_ENDPOINT

logger = logging.getLogger(__name__)

#: Where a stock Ollama listens. One number, matched against the app's own default.
OLLAMA_PORT = 11434

#: A LAN sweep runs while a user waits on a wizard step, so it is quick and shallow.
SCAN_BUDGET_SECS = 3.0
#: Per-host connect/read budget. A LAN round-trip is sub-millisecond; a host with
#: nothing on the port refuses instantly, and a firewalled one is cut at this wall.
SCAN_PROBE_TIMEOUT_SECS = 0.3
#: Never probe more than one ``/24`` worth of hosts, whatever the interface mask is —
#: a bound on how much of the network a single opt-in click may touch.
SCAN_MAX_HOSTS = 256
SCAN_MAX_WORKERS = 64


@dataclass(frozen=True)
class DetectedEndpoint:
    """A reachable Ollama endpoint and the chat model it will bind to."""

    endpoint: str
    model: str

    def to_dict(self) -> dict[str, str]:
        return {"endpoint": self.endpoint, "model": self.model}


def _probe_chat(endpoint: str, *, timeout: float) -> str | None:
    """Return a chat-capable model id at ``endpoint``, or None.

    Reuses the exact ``/api/tags`` probe and capability inference the seed path
    ships, so localhost detection, the LAN scan and ``--seed-local-model`` all agree
    on "is this a bindable chat endpoint" from one implementation.
    """
    from personalclaw import seed_local_model

    models = seed_local_model._probe_models(
        endpoint, timeout=timeout
    )  # noqa: SLF001 — same subsystem: ONE /api/tags probe
    if not models:
        return None
    model = seed_local_model._pick(models, "chat")  # noqa: SLF001 — shared capability inference
    return model or None


def detect_localhost(
    endpoint: str = DEFAULT_ENDPOINT,
    *,
    timeout: float = SCAN_PROBE_TIMEOUT_SECS * 4,
) -> DetectedEndpoint | None:
    """Probe the loopback Ollama endpoint. Returns None when nothing is bindable.

    This is a loopback round-trip, not a network scan, so a caller may run it
    automatically. The default endpoint mirrors ``seed_local_model.DEFAULT_ENDPOINT``
    so a home bound here and one bound by ``--seed-local-model`` carry the same
    provider string.
    """
    model = _probe_chat(endpoint, timeout=timeout)
    if not model:
        return None
    return DetectedEndpoint(endpoint=endpoint, model=model)


def _local_private_ipv4s() -> list[str]:
    """This host's own private (RFC-1918) IPv4 addresses, best-effort.

    Discovers the primary outbound interface without sending a packet (a UDP
    ``connect`` to a documentation address only sets the source route), then adds
    anything the hostname resolves to for multi-homed hosts. Only addresses the
    authoritative classifier calls ``private`` are kept — a public or loopback
    source is not a subnet to sweep.
    """
    from personalclaw.net.guard import classify_host

    found: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1 (RFC 5737): never routed, no packet sent
            found.add(str(probe.getsockname()[0]))
        finally:
            probe.close()
    except OSError:
        logger.debug("local-model scan: outbound-interface probe failed", exc_info=True)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(str(info[4][0]).split("%", 1)[0])
    except OSError:
        logger.debug("local-model scan: hostname resolution failed", exc_info=True)
    return sorted(ip for ip in found if classify_host(ip).category == "private")


def _candidate_hosts(local_ips: list[str], *, max_hosts: int) -> list[str]:
    """Hosts to probe: every OTHER address on this host's private ``/24``(s).

    Our own addresses are skipped (localhost is covered separately), and each host is
    re-classified as private before it earns a place — the RFC-1918 gate, applied at
    enumeration and again at probe time.
    """
    from personalclaw.net.guard import classify_host

    own = set(local_ips)
    nets: list[ipaddress.IPv4Network] = []
    for ip in local_ips:
        try:
            net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        except ValueError:
            continue
        if net not in nets:
            nets.append(net)

    seen: set[str] = set()
    out: list[str] = []
    for net in nets:
        for host in net.hosts():
            h = str(host)
            if h in own or h in seen:
                continue
            if classify_host(h).category != "private":
                continue
            seen.add(h)
            out.append(h)
            if len(out) >= max_hosts:
                return out
    return out


def scan_local_network(
    *,
    budget_secs: float = SCAN_BUDGET_SECS,
    port: int = OLLAMA_PORT,
    probe_timeout: float = SCAN_PROBE_TIMEOUT_SECS,
    max_hosts: int = SCAN_MAX_HOSTS,
    candidates: list[str] | None = None,
    prober: Callable[[str], str | None] | None = None,
) -> list[DetectedEndpoint]:
    """Opt-in, time-bounded sweep of this host's private subnet(s) for an Ollama.

    Returns the endpoints that answered ``/api/tags`` with a chat-capable model —
    only those, and only after a live probe. ``candidates`` and ``prober`` are
    injectable for tests; whatever candidate list is handed in, the RFC-1918 gate is
    applied here too, so a non-private address can never be contacted.
    """
    from personalclaw.net.guard import classify_host

    if candidates is None:
        candidates = _candidate_hosts(_local_private_ipv4s(), max_hosts=max_hosts)
    hosts = [h for h in candidates if classify_host(h).category == "private"][:max_hosts]
    if not hosts:
        return []

    if prober is None:

        def prober(endpoint: str) -> str | None:  # noqa: F811 — the injectable default
            return _probe_chat(endpoint, timeout=probe_timeout)

    endpoints = {h: f"http://{h}:{port}" for h in hosts}
    found: list[DetectedEndpoint] = []
    executor = cf.ThreadPoolExecutor(max_workers=min(SCAN_MAX_WORKERS, len(hosts)))
    try:
        futures = {executor.submit(prober, ep): ep for ep in endpoints.values()}
        try:
            for future in cf.as_completed(futures, timeout=budget_secs):
                endpoint = futures[future]
                try:
                    model = future.result()
                except Exception:  # noqa: BLE001 — one host's failure never fails the sweep
                    model = None
                if model:
                    found.append(DetectedEndpoint(endpoint=endpoint, model=model))
        except cf.TimeoutError:
            # The whole-sweep wall clock is a hard deadline: whatever answered in time
            # is returned; the rest is abandoned. A slow/black-holed host cannot make
            # a first-run wizard step hang.
            logger.debug("local-model LAN scan hit its %.1fs budget", budget_secs)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    # Deterministic order regardless of which probe finished first.
    found.sort(key=lambda d: d.endpoint)
    return found
