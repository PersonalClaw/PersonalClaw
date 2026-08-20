"""Hardware-aware model fit — ONE memory budget, ONE verdict (LOCAL-MODEL-MANAGER-V2 §9).

Before this module the host facts a fit answer needs were collected in three unrelated
places: system memory in :mod:`personalclaw.local_models.residency`, the GPU/VRAM probe in
the system-metrics handler, and free disk inline at whichever call site happened to need
it. Three collectors mean three answers, and a fit chip that disagrees with the download
panel is worse than no chip at all — so every host fact is gathered here, once, and every
fit question is answered by :func:`fit_verdict` reading :func:`usable_memory_bytes`.

The arithmetic that makes this worth centralising is the memory budget. A machine's usable
budget is NOT "system RAM plus whatever the GPU reports":

* **Unified memory** (Apple Silicon, and any integrated GPU) is one pool the CPU and GPU
  share. Adding its "VRAM" to system RAM counts the same bytes twice and reports a budget
  larger than the machine physically has — which loads a model the host cannot hold and
  OOMs at load time, after the user already waited for a multi-gigabyte download.
* Only a **discrete** GPU's VRAM is a genuinely separate pool, so only that adds.
* A fixed **reserve** for the OS and the runtime is always subtracted. A budget equal to
  total RAM is a budget that leaves nothing for the process doing the inference.

An unmeasurable host yields ``None`` (unknown), never 0 — "unknown" and "nothing fits" are
different answers and only one of them should hide models from the user.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .residency import memory_pressure

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024
_BYTES_PER_GB = 1024 * 1024 * 1024
_PROBE_TIMEOUT = 3

#: Held back for the OS, the desktop, and the inference runtime itself. The default is
#: deliberately generous: over-reserving downgrades a chip from green to yellow, while
#: under-reserving promises a fit that dies at load.
DEFAULT_RESERVE_GB = 3.0

#: A model needing more than this fraction of the budget is yellow rather than green —
#: it fits, but with no room for a second model, a browser, or a growing KV cache.
GREEN_HEADROOM = 0.7

#: KV-cache bytes per context token per GB of weights. The cache scales with the model's
#: layer count and hidden size, both of which track weight size closely enough for a
#: traffic light; this is an ESTIMATE for sizing, never a runtime allocation.
KV_BYTES_PER_TOKEN_PER_GB = 20_000

Verdict = Literal["green", "yellow", "red", "unknown"]

_GPU_PROBED: bool = False
_GPU_FACTS: dict[str, Any] = {}


@dataclass(frozen=True)
class HostCapacity:
    """The host facts a fit answer needs, collected once.

    ``memory_measured`` and ``disk_measured`` are explicit flags rather than an in-range
    sentinel: a machine that genuinely reports 0 free bytes and a machine whose filesystem
    could not be measured are different situations, and inferring one from the other
    silently mislabels the other.
    """

    total_ram_bytes: int = 0
    memory_measured: bool = False
    #: True when the GPU shares system RAM (Apple Silicon, integrated graphics) — its
    #: memory is already inside ``total_ram_bytes`` and must never be added again.
    unified_memory: bool = False
    #: A DISCRETE GPU's dedicated VRAM only. 0 on unified-memory and unknown hardware.
    discrete_vram_bytes: int = 0
    free_disk_bytes: int = 0
    disk_measured: bool = False
    gpu_vendor: str = ""
    gpu_model: str = ""


@dataclass(frozen=True)
class FitAssessment:
    """One model's fit against one budget."""

    verdict: Verdict
    #: Weights + estimated KV cache. 0 when the model's size is unknown.
    need_bytes: int
    #: The budget the verdict was taken against. ``None`` on an unmeasured host.
    budget_bytes: int | None
    reason: str


@dataclass(frozen=True)
class DiskPrecheck:
    """A pre-download free-space decision.

    ``ok`` is True both when the download comfortably fits AND when the filesystem could
    not be measured — an unmeasurable disk is not a reason to block a good download. The
    two cases are told apart by ``measured``, and the unmeasured one carries a warning.
    """

    ok: bool
    measured: bool
    need_bytes: int
    free_bytes: int
    reason: str = ""
    warning: str = ""


def _probe_gpu() -> dict[str, Any]:
    """``{unified, vram_bytes, vendor, model}`` — the ONE GPU probe.

    Cached after the first call: on a machine with no NVIDIA hardware the standing cost is
    a single :func:`shutil.which`. Classification is deliberately conservative — anything
    not positively identified as a discrete GPU contributes no second memory pool, because
    inflating the budget is the failure mode that OOMs.
    """
    global _GPU_PROBED, _GPU_FACTS
    if _GPU_PROBED:
        return _GPU_FACTS

    facts: dict[str, Any] = {"unified": False, "vram_bytes": 0, "vendor": "", "model": ""}
    if shutil.which("nvidia-smi"):
        try:
            out = (
                subprocess.check_output(  # noqa: S603,S607 — static host-fact probe, no shell
                    [
                        "nvidia-smi",
                        "--query-gpu=name,memory.total",
                        "--format=csv,noheader,nounits",
                    ],
                    timeout=_PROBE_TIMEOUT,
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            line = out.splitlines()[0] if out else ""
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                facts = {
                    "unified": False,
                    "vram_bytes": int(float(parts[1]) * _BYTES_PER_MB),
                    "vendor": "nvidia",
                    "model": parts[0],
                }
                _GPU_PROBED, _GPU_FACTS = True, facts
                return facts
        except Exception:  # noqa: BLE001 — a host-fact probe must never break the surface
            logger.debug("nvidia-smi probe failed", exc_info=True)

    if sys.platform == "darwin":
        # Apple Silicon: one unified pool. system_profiler can name the GPU but its memory
        # IS system memory, so vram_bytes stays 0 by design — the name is display-only.
        facts = {"unified": True, "vram_bytes": 0, "vendor": "apple", "model": ""}
        try:
            import json as _json

            blob = _json.loads(
                subprocess.check_output(  # noqa: S603,S607 — static host-fact probe, no shell
                    ["system_profiler", "SPDisplaysDataType", "-json"],
                    timeout=_PROBE_TIMEOUT,
                    stderr=subprocess.DEVNULL,
                ).decode()
            )
            cards = blob.get("SPDisplaysDataType", [])
            if cards:
                # `_name` is the fallback for a Mac whose system_profiler omits
                # `sppci_model`; without it the GPU tile loses its name entirely.
                facts["model"] = str(cards[0].get("sppci_model") or cards[0].get("_name") or "")
        except Exception:  # noqa: BLE001 — a host-fact probe must never break the surface
            logger.debug("system_profiler probe failed", exc_info=True)

    _GPU_PROBED, _GPU_FACTS = True, facts
    return facts


def configured_reserve_gb() -> float:
    """The OS/runtime reserve from config, falling back to :data:`DEFAULT_RESERVE_GB`.

    Mirrors :func:`personalclaw.local_models.residency._warn_pct_default`: a host-fact
    helper must never fail because config could not be read.
    """
    try:
        from personalclaw.config import AppConfig

        return float(AppConfig.load().local_models.memory_reserve_gb)
    except Exception:  # noqa: BLE001 — an unreadable config falls back, never raises
        logger.debug("memory_reserve_gb fell back to the default", exc_info=True)
        return DEFAULT_RESERVE_GB


def hide_unrunnable_default() -> bool:
    """Whether the browse filter hides models this device cannot run (config-backed)."""
    try:
        from personalclaw.config import AppConfig

        return bool(AppConfig.load().local_models.hide_unrunnable_models)
    except Exception:  # noqa: BLE001 — an unreadable config falls back, never raises
        logger.debug("hide_unrunnable_models fell back to the default", exc_info=True)
        return True


def reset_gpu_probe_cache() -> None:
    """Drop the cached GPU facts (tests; a probe result must not leak across cases)."""
    global _GPU_PROBED, _GPU_FACTS
    _GPU_PROBED, _GPU_FACTS = False, {}


def host_capacity(target_dir: str | Path | None = None) -> HostCapacity:
    """Collect every host fact a fit answer needs — the ONE helper.

    ``target_dir`` is the filesystem whose free space matters (a provider's cache root);
    the root filesystem is measured when it is omitted.
    """
    mem = memory_pressure()
    total_mb = int(mem.get("total_mb") or 0)
    measured = total_mb > 0 and mem.get("source") != "unavailable"

    gpu = _probe_gpu()
    free_bytes, disk_measured = 0, False
    try:
        free_bytes = shutil.disk_usage(str(target_dir) if target_dir else "/").free
        disk_measured = True
    except Exception:  # noqa: BLE001 — an unmeasurable disk is a state, not a failure
        logger.debug("free-space probe failed for %s", target_dir, exc_info=True)

    return HostCapacity(
        total_ram_bytes=total_mb * _BYTES_PER_MB,
        memory_measured=measured,
        unified_memory=bool(gpu.get("unified")),
        discrete_vram_bytes=int(gpu.get("vram_bytes") or 0),
        free_disk_bytes=free_bytes,
        disk_measured=disk_measured,
        gpu_vendor=str(gpu.get("vendor") or ""),
        gpu_model=str(gpu.get("model") or ""),
    )


def usable_memory_bytes(host: HostCapacity, *, reserve_gb: float | None = None) -> int | None:
    """The memory a model may actually occupy, or ``None`` when the host is unmeasured.

    System RAM minus the reserve, plus a DISCRETE GPU's VRAM only. A unified-memory GPU
    adds nothing: its bytes are already counted in system RAM. A machine smaller than the
    reserve yields 0 — a real answer ("nothing fits"), distinct from ``None``.
    """
    if not host.memory_measured or host.total_ram_bytes <= 0:
        return None
    reserve = DEFAULT_RESERVE_GB if reserve_gb is None else max(0.0, float(reserve_gb))
    budget = host.total_ram_bytes - int(reserve * _BYTES_PER_GB)
    if not host.unified_memory:
        budget += max(0, host.discrete_vram_bytes)
    return max(0, budget)


def kv_cache_bytes(context_tokens: int, weights_bytes: int) -> int:
    """Estimated KV-cache footprint at a full context window.

    Zero when either input is unknown — an unknown context window must not inflate the
    need and turn a green model red on a guess.
    """
    if context_tokens <= 0 or weights_bytes <= 0:
        return 0
    weights_gb = weights_bytes / _BYTES_PER_GB
    return int(context_tokens * weights_gb * KV_BYTES_PER_TOKEN_PER_GB)


def fit_verdict(
    *, size_mb: float, context_tokens: int = 0, budget_bytes: int | None
) -> FitAssessment:
    """One traffic-light verdict for one model against one budget.

    ``unknown`` when the host could not be measured or the model does not declare a size —
    the two inputs a verdict cannot be invented without.
    """
    weights = int(max(0.0, float(size_mb or 0)) * _BYTES_PER_MB)
    if budget_bytes is None:
        return FitAssessment("unknown", 0, None, "this machine's memory could not be measured")
    if weights <= 0:
        return FitAssessment("unknown", 0, budget_bytes, "this model does not publish a size")

    need = weights + kv_cache_bytes(context_tokens, weights)
    if need > budget_bytes:
        return FitAssessment(
            "red",
            need,
            budget_bytes,
            f"needs ~{_gb(need)} GB, this machine has ~{_gb(budget_bytes)} GB free for models",
        )
    if need > budget_bytes * GREEN_HEADROOM:
        return FitAssessment(
            "yellow",
            need,
            budget_bytes,
            f"fits, but uses most of the ~{_gb(budget_bytes)} GB available",
        )
    return FitAssessment(
        "green", need, budget_bytes, f"fits comfortably in ~{_gb(budget_bytes)} GB"
    )


def _gb(value: int) -> str:
    """Bytes → a one-decimal GB string for user-facing reasons."""
    return f"{value / _BYTES_PER_GB:.1f}"


def family_key(name: str) -> str:
    """The variant family a catalog name belongs to.

    Ollama-style ids are ``family:tag`` (``qwen3:8b``); a colonless name is its own family
    of one, for which the median variant is simply the model itself.
    """
    return (name or "").split(":", 1)[0]


def median_variant_size_mb(sizes: list[float]) -> float:
    """The size a family should QUOTE — its median variant, never its smallest.

    Quoting the smallest variant promises a fit the user will not get from the variant they
    actually pick. With an even number of variants the LARGER of the two middles is taken,
    for the same reason: a quote must not flatter the family.
    """
    known = sorted(s for s in sizes if s and s > 0)
    if not known:
        return 0.0
    return float(known[len(known) // 2])


def largest_that_fits(sizes: list[float], budget_bytes: int | None) -> float | None:
    """The largest variant size that still fits the budget, or ``None`` if none does.

    The download panel steps DOWN to this instead of offering a variant that cannot load.
    An unmeasured budget returns ``None``: with no budget there is nothing to step down to,
    and the caller must keep offering the user's own choice.
    """
    if budget_bytes is None:
        return None
    fitting = [
        s
        for s in sizes
        if s and s > 0 and fit_verdict(size_mb=s, budget_bytes=budget_bytes).verdict != "red"
    ]
    return max(fitting) if fitting else None


def disk_precheck(need_mb: float, target_dir: str | Path | None = None) -> DiskPrecheck:
    """Refuse a download that cannot land — but never on an unmeasurable filesystem.

    A refusal names BOTH numbers (needed and free) so the message is actionable without a
    second lookup. When the filesystem cannot be measured the check SKIPS with a warning:
    blocking a good download because a probe failed is the worse error.
    """
    need = int(max(0.0, float(need_mb or 0)) * _BYTES_PER_MB)
    try:
        free = shutil.disk_usage(str(target_dir) if target_dir else "/").free
    except Exception:  # noqa: BLE001 — an unmeasurable disk skips the check, never blocks
        logger.debug("free-space precheck could not measure %s", target_dir, exc_info=True)
        return DiskPrecheck(
            ok=True,
            measured=False,
            need_bytes=need,
            free_bytes=0,
            warning="Free space could not be checked, so the download was not verified to fit.",
        )
    if need > 0 and need > free:
        return DiskPrecheck(
            ok=False,
            measured=True,
            need_bytes=need,
            free_bytes=free,
            reason=f"insufficient_disk_space: needs {_gb(need)} GB, {_gb(free)} GB free",
        )
    return DiskPrecheck(ok=True, measured=True, need_bytes=need, free_bytes=free)


def assess(
    *,
    size_mb: float,
    context_tokens: int = 0,
    host: HostCapacity | None = None,
    reserve_gb: float | None = None,
) -> FitAssessment:
    """Convenience: probe the host (or take a given one) and return one verdict."""
    capacity = host_capacity() if host is None else host
    reserve = configured_reserve_gb() if reserve_gb is None else reserve_gb
    return fit_verdict(
        size_mb=size_mb,
        context_tokens=context_tokens,
        budget_bytes=usable_memory_bytes(capacity, reserve_gb=reserve),
    )
