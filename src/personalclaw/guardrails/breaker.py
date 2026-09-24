"""Per-provider circuit breaker for the model-call chokepoint (§2.3).

A three-state FSM keyed by ``ProviderEntry.name``:

    CLOSED  ──(N consecutive failures)──▶  OPEN
    OPEN    ──(recovery_secs elapsed)───▶  HALF_OPEN   (one probe allowed)
    HALF_OPEN ──(probe succeeds)────────▶  CLOSED
    HALF_OPEN ──(probe fails)───────────▶  OPEN         (recovery clock resets)

``is_open()`` is checked BEFORE any prompt work, so during a provider outage
overnight unattended runs fail in microseconds instead of stacking 30s timeouts —
the worst case the automation substrate would otherwise hit.

In-process state is deliberate for a single-user gateway: a restart resetting the
breaker is acceptable (§6 data model — "restart resets"). The state is process-
global (one breaker per provider name across the gateway); :func:`reset_breakers`
clears it, invoked by an autouse test fixture so per-test breaker state never
leaks between tests (the SEL-singleton discipline).
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)

# Defaults (overridable per breaker; config wiring lands in Session 2 via
# GuardrailsConfig.breaker). Local providers (ollama cold-start) warrant a higher
# threshold — Session 4's SafetyProfile carries per-provider tuning; until then
# these single defaults apply.
_DEFAULT_THRESHOLD = 5
_DEFAULT_RECOVERY_SECS = 30.0


class BreakerState(str, Enum):
    """The three breaker states, surfaced verbatim by the health view (§2.5)."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """A single provider's breaker. Not thread-safe by design — the gateway's
    model calls run on one asyncio loop, and the state transitions are cheap
    non-awaiting mutations, so there is no await between a read and its write."""

    def __init__(
        self,
        name: str,
        *,
        threshold: int = _DEFAULT_THRESHOLD,
        recovery_secs: float = _DEFAULT_RECOVERY_SECS,
    ) -> None:
        self.name = name
        self.threshold = max(1, int(threshold))
        self.recovery_secs = max(0.0, float(recovery_secs))
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0

    # ── Introspection (drives the health view) ──────────────────────────

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def state(self, *, now: float | None = None) -> BreakerState:
        """Current state, lazily promoting OPEN→HALF_OPEN once recovery elapses.

        State is time-derived rather than timer-driven (no background task) — the
        OPEN→HALF_OPEN promotion happens on read, matching the gateway's polling
        habit and keeping the breaker free of its own scheduling.
        """
        if self._state is BreakerState.OPEN:
            t = time.monotonic() if now is None else now
            if t - self._opened_at >= self.recovery_secs:
                self._state = BreakerState.HALF_OPEN
        return self._state

    def is_open(self, *, now: float | None = None) -> bool:
        """True when a call must be refused WITHOUT any work (CLOSED/HALF_OPEN pass).

        HALF_OPEN admits exactly one probe: the caller proceeds, and the outcome
        (:meth:`record_success` / :meth:`record_failure`) closes or re-opens.
        """
        return self.state(now=now) is BreakerState.OPEN

    def retry_after(self, *, now: float | None = None) -> float:
        """Seconds until the OPEN breaker becomes HALF_OPEN (0 when not OPEN)."""
        if self.state(now=now) is not BreakerState.OPEN:
            return 0.0
        t = time.monotonic() if now is None else now
        return max(0.0, self.recovery_secs - (t - self._opened_at))

    # ── Transitions ─────────────────────────────────────────────────────

    def record_success(self) -> None:
        """A call succeeded — reset failures and close the breaker."""
        if self._state is not BreakerState.CLOSED:
            logger.info("circuit breaker %r → CLOSED (recovered)", self.name)
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0

    def record_failure(self, *, now: float | None = None) -> None:
        """A call failed — count it and open the breaker at the threshold.

        A failure in HALF_OPEN (the probe failed) re-opens immediately and resets
        the recovery clock, regardless of the running count.
        """
        t = time.monotonic() if now is None else now
        if self._state is BreakerState.HALF_OPEN:
            self._consecutive_failures += 1
            self._state = BreakerState.OPEN
            self._opened_at = t
            logger.warning("circuit breaker %r → OPEN (half-open probe failed)", self.name)
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold:
            if self._state is not BreakerState.OPEN:
                logger.warning(
                    "circuit breaker %r → OPEN (%d consecutive failures)",
                    self.name,
                    self._consecutive_failures,
                )
            self._state = BreakerState.OPEN
            self._opened_at = t


# ── Process-global registry (one breaker per provider name) ──────────────────

_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    *,
    threshold: int = _DEFAULT_THRESHOLD,
    recovery_secs: float = _DEFAULT_RECOVERY_SECS,
) -> CircuitBreaker:
    """Return the shared breaker for provider ``name``, creating it on first use.

    ``threshold`` / ``recovery_secs`` apply only when the breaker is first
    created; a live breaker keeps its tuning (a mid-flight config change is a
    Session-2 concern). An empty name yields a throwaway breaker that is never
    registered — an unnamed provider can't share breaker state with anything.
    """
    if not name:
        return CircuitBreaker("", threshold=threshold, recovery_secs=recovery_secs)
    breaker = _BREAKERS.get(name)
    if breaker is None:
        breaker = CircuitBreaker(name, threshold=threshold, recovery_secs=recovery_secs)
        _BREAKERS[name] = breaker
    return breaker


def all_breakers() -> dict[str, CircuitBreaker]:
    """A snapshot view of every registered breaker (for the health view)."""
    return dict(_BREAKERS)


def reset_breakers() -> None:
    """Clear all process-global breaker state.

    Invoked by an autouse test fixture so breaker state from one test never leaks
    into the next (the same isolation discipline as the SEL singleton reset).
    """
    _BREAKERS.clear()
