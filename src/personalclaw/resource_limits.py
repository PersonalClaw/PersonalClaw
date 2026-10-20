"""POSIX resource-limit helpers, guarded for platforms without the ``resource`` module.

The stdlib ``resource`` module is POSIX-only and absent on native Windows. Importing it
unconditionally at gateway boot ``ImportError``s the process before it can serve — you
cannot even reach "run it and see what breaks." This module guards the import ONCE (mirroring
:mod:`personalclaw._spawn_exec_shim`) and exposes the two things core needs, so every consumer
degrades through one helper instead of repeating a bare ``import resource``:

* :func:`raise_fd_limit` — raise the current process's own ``RLIMIT_NOFILE`` soft limit toward
  a target, best-effort. The gateway calls it at boot because each ACP agent session uses ~6
  FDs (3 pipes) plus MCP server subprocesses and the default macOS soft limit (256) is too low.
* :func:`resource_limits_available` — whether the ``resource`` facility exists on this host,
  so ``doctor`` can report resource-limit availability honestly rather than silently.

Degradation contract (load-bearing): on a platform without ``resource`` (native Windows) NO
limit is applied and NOTHING is raised — the process boots and runs with whatever ``RLIMIT_NOFILE``
it inherited, and :func:`resource_limits_available` returns ``False``. On a POSIX host the
behaviour is byte-identical to the previous inline code: the soft limit is raised to
``min(hard, DEFAULT_FD_TARGET)`` when it is currently lower, and never above the inherited hard
cap. This never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

try:  # ``resource`` is POSIX-only; absent on native Windows.
    import resource as _resource
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _resource = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Raise the soft NOFILE cap toward this target, clamped to the inherited hard limit. The
# default macOS soft limit (256) is too low for the gateway's ACP + MCP subprocess fan-out.
DEFAULT_FD_TARGET = 10240


def resource_limits_available() -> bool:
    """True when the POSIX ``resource`` module (the rlimit facility) exists on this host."""
    return _resource is not None


@dataclass(frozen=True)
class FdLimitResult:
    """Outcome of :func:`raise_fd_limit`.

    ``available`` is whether the ``resource`` facility was present at all; ``raised`` is
    whether this call actually lifted the soft limit (it is ``False`` when the limit was
    already at/above target, or when the facility is absent). ``soft``/``target`` are the
    before-value and the value we set, or ``None`` when the facility is unavailable.
    """

    available: bool
    raised: bool
    soft: Optional[int] = None
    target: Optional[int] = None


def raise_fd_limit(target: int = DEFAULT_FD_TARGET) -> FdLimitResult:
    """Raise this process's ``RLIMIT_NOFILE`` soft limit toward *target*, best-effort.

    On a platform without ``resource`` this is a no-op returning
    ``FdLimitResult(available=False, raised=False)`` — the documented no-limit degradation.
    Never raises: a platform that has ``resource`` but refuses the ``setrlimit`` still boots.
    """
    if _resource is None:
        return FdLimitResult(available=False, raised=False)
    try:
        soft, hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        eff_target = min(hard, target)
        if soft < eff_target:
            _resource.setrlimit(_resource.RLIMIT_NOFILE, (eff_target, hard))
            logger.info("Raised FD limit: %d → %d", soft, eff_target)
            return FdLimitResult(available=True, raised=True, soft=soft, target=eff_target)
        return FdLimitResult(available=True, raised=False, soft=soft, target=eff_target)
    except Exception:  # noqa: BLE001 - a failure to raise the ceiling is never fatal
        return FdLimitResult(available=True, raised=False)
