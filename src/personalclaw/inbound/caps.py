"""Request caps for inbound surfaces (MCP-READONLY-INBOUND §C1).

An inbound surface is the one place an outside caller sets the pace, so every
dimension it could exhaust gets a ceiling: body size, wall-clock, request rate,
concurrency, and result size. The numbers are deliberately modest — this surface
exists for a person's IDE to ask occasional questions, not to serve traffic.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Caps:
    """Ceilings for one inbound surface."""

    body_bytes: int = 64 * 1024
    deadline_s: int = 30
    rps: float = 1.0  # sustained
    burst: int = 20  # bucket capacity, so a client can batch a few calls
    concurrent: int = 4
    max_items: int = 100
    max_result_bytes: int = 2 * 1024 * 1024


DEFAULT_CAPS = Caps()


class _TokenBucket:
    """Sustained-rate limiter with burst headroom.

    A bucket rather than a fixed window: an IDE typically fires a handful of calls
    at once when a panel opens and then goes quiet, which a fixed window would
    reject even though the average rate is trivial.
    """

    def __init__(self, rps: float, burst: int) -> None:
        self._rps = max(0.01, float(rps))
        self._burst = max(1, int(burst))
        self._tokens: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def take(self, key: str, *, now: float | None = None) -> bool:
        stamp = time.monotonic() if now is None else now
        with self._lock:
            last = self._last.get(key, stamp)
            tokens = self._tokens.get(key, float(self._burst))
            tokens = min(float(self._burst), tokens + (stamp - last) * self._rps)
            if tokens < 1.0:
                self._last[key] = stamp
                self._tokens[key] = tokens
                return False
            self._tokens[key] = tokens - 1.0
            self._last[key] = stamp
            return True

    def retry_after(self) -> int:
        """Whole seconds a refused caller should wait — always at least 1, since
        `Retry-After: 0` invites an immediate retry storm."""
        return max(1, int(round(1.0 / self._rps)))


_buckets: dict[str, _TokenBucket] = {}
_bucket_lock = threading.Lock()

_inflight: dict[str, int] = {}
_inflight_lock = threading.Lock()


def _bucket(surface: str, caps: Caps) -> _TokenBucket:
    with _bucket_lock:
        existing = _buckets.get(surface)
        if existing is None:
            existing = _TokenBucket(caps.rps, caps.burst)
            _buckets[surface] = existing
        return existing


def check_rate(surface: str, client_key: str, caps: Caps = DEFAULT_CAPS) -> bool:
    """False when the caller has exceeded the sustained rate (caller returns 429)."""
    return _bucket(surface, caps).take(client_key)


def retry_after_secs(surface: str, caps: Caps = DEFAULT_CAPS) -> int:
    return _bucket(surface, caps).retry_after()


def acquire_slot(surface: str, caps: Caps = DEFAULT_CAPS) -> bool:
    """Claim one of the CLIENT's concurrency slots. False when saturated.

    ``surface`` is really "the bucket key" — the caller passes a per-client key (see
    `slot_key`), so a saturated client cannot starve the others. It kept the old
    parameter name because the whole module keys on the same string.
    """
    with _inflight_lock:
        current = _inflight.get(surface, 0)
        if current >= caps.concurrent:
            return False
        _inflight[surface] = current + 1
        return True


def release_slot(surface: str) -> None:
    with _inflight_lock:
        _inflight[surface] = max(0, _inflight.get(surface, 0) - 1)


# ── per-client caps (§1.3) ───────────────────────────────────────────────────


def rate_key(surface: str, client_id: str, peer_fallback: str = "") -> str:
    """The bucket key for one caller: the CLIENT, falling back to the peer.

    Per-client rather than per-peer is the point: two integrations behind one loopback
    address are two callers, and rate-limiting them as one lets a chatty IDE panel
    starve a scheduled job. The peer fallback exists only for a request refused
    *before* identity is known, so an unauthenticated flood is still bounded.
    """
    return f"{surface}|client:{client_id}" if client_id else f"{surface}|peer:{peer_fallback}"


def slot_key(surface: str, client_id: str, peer_fallback: str = "") -> str:
    """Concurrency key — same identity rule as :func:`rate_key`, separate namespace so
    a rate bucket and a slot counter can never collide on one dict entry."""
    return "slots|" + rate_key(surface, client_id, peer_fallback)


def caps_for(client=None) -> Caps:
    """The effective caps: module constants, config overrides, then per-client overrides.

    Three layers in that order, resolved HERE rather than at each surface, so
    "what limit applies to this request?" has one answer. A client's `rate_overrides`
    can only be read for the three rate dimensions — a per-client override of
    `max_result_bytes` would let a client raise its own memory ceiling.
    """
    body_bytes = DEFAULT_CAPS.body_bytes
    rps, burst, concurrent = DEFAULT_CAPS.rps, DEFAULT_CAPS.burst, DEFAULT_CAPS.concurrent
    try:
        from personalclaw.config.loader import AppConfig

        ea = AppConfig.load().external_access
        rps, burst, concurrent = float(ea.rate_rps), int(ea.rate_burst), int(ea.rate_concurrent)
    except Exception:  # noqa: BLE001 — unreadable config keeps the module constants
        logger.debug("inbound: cap config unreadable; using module defaults", exc_info=True)
    overrides = dict(getattr(client, "rate_overrides", None) or {}) if client is not None else {}
    for name, setter in (("rps", "rps"), ("burst", "burst"), ("concurrent", "concurrent")):
        if name not in overrides:
            continue
        try:
            value = float(overrides[name])
        except (TypeError, ValueError):
            continue
        if setter == "rps":
            rps = max(0.01, value)
        elif setter == "burst":
            burst = max(1, int(value))
        else:
            concurrent = max(1, int(value))
    return Caps(
        body_bytes=body_bytes,
        deadline_s=DEFAULT_CAPS.deadline_s,
        rps=rps,
        burst=burst,
        concurrent=concurrent,
        max_items=DEFAULT_CAPS.max_items,
        max_result_bytes=DEFAULT_CAPS.max_result_bytes,
    )


def check_rate_for_client(
    surface: str, client_id: str, peer_fallback: str = "", caps: Caps | None = None
) -> bool:
    """Per-client sustained-rate check. False ⇒ the caller returns 429.

    The bucket is created per KEY with that key's caps, so a client whose override
    widened its rate does not also widen everyone else's — the defect a single
    per-surface bucket has by construction.
    """
    effective = caps or DEFAULT_CAPS
    key = rate_key(surface, client_id, peer_fallback)
    with _bucket_lock:
        bucket = _buckets.get(key)
        if bucket is None:
            bucket = _TokenBucket(effective.rps, effective.burst)
            _buckets[key] = bucket
    return bucket.take(key)


def retry_after_for_client(
    surface: str, client_id: str, peer_fallback: str = "", caps: Caps | None = None
) -> int:
    key = rate_key(surface, client_id, peer_fallback)
    with _bucket_lock:
        bucket = _buckets.get(key)
    if bucket is None:
        return max(1, int(round(1.0 / max(0.01, (caps or DEFAULT_CAPS).rps))))
    return bucket.retry_after()


def clamp_items(items: list, caps: Caps = DEFAULT_CAPS) -> list:
    return items[: caps.max_items]


def clamp_text(text: str, caps: Caps = DEFAULT_CAPS) -> str:
    """Cap a result body, VISIBLY — a silent truncation would let a caller believe
    it had the whole answer."""
    if len(text.encode("utf-8")) <= caps.max_result_bytes:
        return text
    keep = caps.max_result_bytes - 128
    return text.encode("utf-8")[:keep].decode("utf-8", errors="ignore") + (
        "\n…[truncated: result exceeded the inbound size cap]"
    )


def reset_for_tests() -> None:
    """Clear the process-global buckets/counters.

    Rate limiters are inherently process-global, which makes them order-dependent
    across tests; this is the explicit reset hook so a test never inherits another
    test's spent budget.
    """
    with _bucket_lock:
        _buckets.clear()
    with _inflight_lock:
        _inflight.clear()
