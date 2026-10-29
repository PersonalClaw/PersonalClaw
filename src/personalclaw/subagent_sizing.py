"""How many subagents may run at once on this host (``resolve_max_subagents``).

Moved out of ``subagent.py`` as it is (#5605's size step): the manager's module keeps its
``2800``-line watch band clear, and these numbers read only the host.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from personalclaw.hooks import safe_read_file

logger = logging.getLogger(__name__)


_MAX_CONCURRENT = 3

# Auto-size bounds (used when max_subagents == 0). Floor 2 so "auto" always beats
# a single-agent Pi-class host; ceiling 8 because past the SessionManager's
# 4-concurrent cold-start semaphore the marginal throughput falls off while OOM
# risk climbs. The per-agent memory budget reuses spawn_min_memory_gb (the
# headroom the spawn gate already requires per subagent), so the auto cap stays
# consistent with the existing admission control.
_AUTO_FLOOR = 2
_AUTO_CEILING = 8
_CPU_HEADROOM = 2  # leave cores for the gateway + OS


def _total_memory_gb() -> float:
    """Total host RAM in GB, cross-platform; 0.0 if it can't be determined.

    Mirrors the detection in ``dashboard/handlers_system.py`` (``sysctl
    hw.memsize`` on macOS, ``/proc/meminfo`` ``MemTotal`` on Linux) so the two
    agree on host facts without a third-party dependency.
    """
    try:
        if sys.platform == "darwin":
            out = (
                subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2).decode().strip()
            )
            return int(out) / (1024**3)
        if sys.platform == "linux":
            for line in safe_read_file("/proc/meminfo").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024**2)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0.0
    return 0.0


def resolve_max_subagents(configured: int, per_agent_gb: float = 4.0) -> int:
    """Resolve the subagent concurrency cap, auto-sizing when ``configured == 0``.

    An explicit non-zero ``configured`` is returned unchanged. ``0`` means
    "auto": size from host CPU and total memory, taking the lower of the two
    budgets and clamping to ``[_AUTO_FLOOR, _AUTO_CEILING]`` — a big host gets
    more parallelism, a small one is protected from OOM. ``per_agent_gb`` is the
    memory budgeted per concurrent subagent (defaults to the spawn gate's
    ``spawn_min_memory_gb``). When host facts are unavailable (unknown platform,
    unreadable meminfo) we fall back to the historical fixed cap.
    """
    if configured > 0:
        return configured
    cpu = os.cpu_count() or 0
    total_gb = _total_memory_gb()
    if cpu <= 0 or total_gb <= 0.0:
        logger.info(
            "subagent auto-size: host facts unavailable (cpu=%s, mem=%.1fGB) — "
            "falling back to fixed cap %d",
            cpu,
            total_gb,
            _MAX_CONCURRENT,
        )
        return _MAX_CONCURRENT
    cpu_based = max(1, cpu - _CPU_HEADROOM)
    mem_based = max(1, int(total_gb // max(per_agent_gb, 0.5)))
    resolved = max(_AUTO_FLOOR, min(_AUTO_CEILING, min(cpu_based, mem_based)))
    logger.info(
        "subagent auto-size: %d (cpu=%d→%d, mem=%.1fGB/%.1f→%d, bounds[%d,%d])",
        resolved,
        cpu,
        cpu_based,
        total_gb,
        per_agent_gb,
        mem_based,
        _AUTO_FLOOR,
        _AUTO_CEILING,
    )
    return resolved
