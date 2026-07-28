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
    """Claim one of the surface's concurrency slots. False when saturated."""
    with _inflight_lock:
        current = _inflight.get(surface, 0)
        if current >= caps.concurrent:
            return False
        _inflight[surface] = current + 1
        return True


def release_slot(surface: str) -> None:
    with _inflight_lock:
        _inflight[surface] = max(0, _inflight.get(surface, 0) - 1)


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
