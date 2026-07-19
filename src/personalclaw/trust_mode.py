"""Canonical process-global YOLO / auto-approve trust state.

YOLO ("you only live once") is a global override that auto-approves every tool
invocation regardless of which surface triggered it — the channel ``!yolo``
command, the dashboard trust toggle, or the ``agent.yolo`` config flag. Because
it is a single process-wide security posture, it MUST have exactly one source of
truth. This module is that source; the dashboard state object and the channel
handler both delegate here rather than each keeping their own copy (which
previously drifted and had to be manually re-synced by the gateway).

Semantics:
- **Config-driven** YOLO (``from_config=True``) is permanent — no TTL, and it
  cannot be downgraded by a surface toggle.
- **Surface-driven** YOLO (channel ``!yolo on`` or the dashboard button) carries a
  TTL and auto-expires on read via :func:`is_yolo_active`.
- Expiry and disable fire registered ``on_disable`` callbacks so each surface can
  clear its own derived state (e.g. per-session approval policies, trusted-thread
  sets) without this module reaching into them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Default TTLs (seconds) for surface-driven activation.
YOLO_CHANNEL_TTL_SECS = 1800        # 30 min — channel `!yolo on`
YOLO_DASHBOARD_TTL_SECS = 6 * 3600  # 6 h — dashboard trust toggle (security ceiling)


class _TrustMode:
    """The single global auto-approve state. Not instantiated by callers —
    use the module-level function API, which binds to the shared instance."""

    def __init__(self) -> None:
        self._active: bool = False
        self._expires_at: float = 0.0        # 0.0 == no expiry (permanent)
        self._from_config: bool = False
        self._active_ttl: int = 0
        self._on_disable: list[Callable[[str], None]] = []

    def register_on_disable(self, cb: Callable[[str], None]) -> None:
        """Register a callback fired when YOLO turns off (manually or by expiry).

        The callback receives the reason: ``"manual"`` or ``"expired"``. Used by
        surfaces to clear their derived trust state (approval policies, trusted
        threads). Idempotent registration is the caller's responsibility.
        """
        if cb not in self._on_disable:
            self._on_disable.append(cb)

    def _fire_disable(self, reason: str) -> None:
        for cb in list(self._on_disable):
            try:
                cb(reason)
            except Exception:
                logger.warning("trust_mode on_disable callback failed", exc_info=True)

    def enable(self, *, ttl_secs: int | None = None, from_config: bool = False) -> None:
        """Turn YOLO on.

        ``from_config=True`` makes it permanent. A surface activation while
        config-driven YOLO is already active is a no-op (cannot downgrade a
        permanent posture to a TTL'd one).
        """
        if from_config:
            self._active = True
            self._from_config = True
            self._expires_at = 0.0
            self._active_ttl = 0
            logger.info("YOLO mode ON (config, permanent)")
            return
        if self._from_config:
            logger.info("YOLO already permanent from config — ignoring TTL activation")
            return
        ttl = ttl_secs if ttl_secs is not None else YOLO_DASHBOARD_TTL_SECS
        self._active = True
        self._active_ttl = ttl
        self._expires_at = time.monotonic() + ttl
        logger.info("YOLO mode ON (expires in %ds)", ttl)

    def disable(self) -> None:
        """Turn YOLO off (any source). Fires on_disable with reason ``manual``."""
        if self._active or self._from_config:
            self._active = False
            self._expires_at = 0.0
            self._from_config = False
            self._active_ttl = 0
            logger.info("YOLO mode OFF")
            self._fire_disable("manual")

    def is_active(self) -> bool:
        """Whether YOLO is on right now, auto-expiring a lapsed TTL on read."""
        if (
            self._active
            and not self._from_config
            and self._expires_at
            and time.monotonic() > self._expires_at
        ):
            self._active = False
            self._expires_at = 0.0
            logger.info("YOLO mode auto-expired after %ds", self._active_ttl)
            self._fire_disable("expired")
        return self._active

    @property
    def from_config(self) -> bool:
        return self._from_config

    def remaining_secs(self) -> float | None:
        """Seconds until auto-expiry, or None if inactive/permanent."""
        if not self.is_active() or self._from_config or not self._expires_at:
            return None
        return max(0.0, self._expires_at - time.monotonic())


# The one shared instance + thin function API bound to it.
_TRUST = _TrustMode()


def enable_yolo(*, ttl_secs: int | None = None, from_config: bool = False) -> None:
    _TRUST.enable(ttl_secs=ttl_secs, from_config=from_config)


def disable_yolo() -> None:
    _TRUST.disable()


def is_yolo_active() -> bool:
    return _TRUST.is_active()


def yolo_from_config() -> bool:
    return _TRUST.from_config


def yolo_remaining_secs() -> float | None:
    return _TRUST.remaining_secs()


def register_on_disable(cb: Callable[[str], None]) -> None:
    _TRUST.register_on_disable(cb)
