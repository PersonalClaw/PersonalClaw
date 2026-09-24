"""What is occupying this machine's RAM right now (LOCAL-MODEL-MANAGER-V2 §7).

Models outlive the bindings that loaded them, and a sidecar adds a whole child process
to the picture, so "why is 6 GB gone?" had no answer anywhere in the product. This module
is that answer: every resident occupant, with attribution, plus a system memory-pressure
snapshot and the unload lever.

Two facts make the surface honest rather than decorative:

* **``is_active`` is attribution, not liveness.** A model stays loaded after the user
  binds a different one; that row is resident AND inactive, which is exactly the case
  worth showing, because it is the reclaimable one.
* **A sidecar's RSS is CHILD-REPORTED.** The gateway cannot see inside another process's
  heap, so the number comes from the child's own ``stat`` frame (§3.1). An in-process
  model has no separately attributable RSS at all — the honest value is ``None``, never a
  fabricated split of the gateway's own footprint.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

#: Fallback page size when ``vm_stat`` doesn't name one (Apple silicon uses 16 KiB).
_DEFAULT_PAGE_SIZE = 16384

#: Timeout for the two host-fact probes. A wedged ``vm_stat`` must degrade the widget,
#: never hang the request.
_PROBE_TIMEOUT = 2


def _warn_pct_default() -> int:
    """``local_models.pressure_warn_pct`` from config, fail-open to 85."""
    try:
        from personalclaw.config.loader import AppConfig

        return int(AppConfig.load().local_models.pressure_warn_pct)
    except Exception:
        logger.debug("pressure_warn_pct fell back to the default", exc_info=True)
        return 85


def _darwin_memory() -> tuple[int, int]:
    """``(total_mb, available_mb)`` on macOS via ``sysctl`` + ``vm_stat``."""
    total = int(
        subprocess.check_output(  # noqa: S603,S607 — static host-fact probe, no shell
            ["sysctl", "-n", "hw.memsize"], timeout=_PROBE_TIMEOUT
        )
        .decode()
        .strip()
    )
    vm = subprocess.check_output(  # noqa: S603,S607 — static host-fact probe, no shell
        ["vm_stat"], timeout=_PROBE_TIMEOUT
    ).decode()
    page_size = _DEFAULT_PAGE_SIZE
    free_pages = 0
    for line in vm.splitlines():
        if "page size of" in line:
            page_size = int(line.split()[-2])
        elif "Pages free" in line or "Pages inactive" in line:
            free_pages += int(line.split()[-1].rstrip("."))
    return total // (1024 * 1024), free_pages * page_size // (1024 * 1024)


def _linux_memory() -> tuple[int, int]:
    """``(total_mb, available_mb)`` on Linux from ``/proc/meminfo``.

    ``MemAvailable`` is the kernel's own estimate of what a new allocation can actually
    get — strictly better than ``MemFree`` for a pressure read, since page cache is
    reclaimable.
    """
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0].rstrip(":")] = int(parts[1])
    total_kb = values.get("MemTotal", 0)
    available_kb = values.get(
        "MemAvailable",
        values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0),
    )
    return total_kb // 1024, available_kb // 1024


def memory_pressure(warn_pct: int | None = None) -> dict[str, Any]:
    """A system memory snapshot with the warn threshold applied.

    ``{total_mb, used_mb, available_mb, used_pct, warn_pct, warn, source}``. On a host
    whose memory can't be read, every number is 0, ``warn`` is False and ``source`` is
    ``"unavailable"`` — the widget then renders an honest "unknown" instead of a bar at a
    made-up position. ``warn`` is never True on unknown data: a false alarm about memory
    is worse than no alarm.
    """
    threshold = _warn_pct_default() if warn_pct is None else int(warn_pct)
    total = available = 0
    source = "unavailable"
    try:
        if sys.platform == "darwin":
            total, available = _darwin_memory()
            source = "vm_stat"
        else:
            total, available = _linux_memory()
            source = "meminfo"
    except Exception:  # noqa: BLE001 — a host-fact probe must never break the surface
        logger.debug("memory pressure probe failed", exc_info=True)
        total = available = 0
        source = "unavailable"
    used = max(0, total - available)
    used_pct = round(used / total * 100, 1) if total > 0 else 0.0
    return {
        "total_mb": total,
        "used_mb": used,
        "available_mb": available,
        "used_pct": used_pct,
        "warn_pct": threshold,
        "warn": bool(total > 0 and used_pct >= threshold),
        "source": source,
    }


def _bound_refs() -> set[str]:
    """Every ``provider:model`` ref bound to a use case right now (fail-soft to empty).

    Empty means "nothing is bound" AND "we could not tell" — so ``is_active`` degrades to
    False, which reads as "reclaimable". That is the safe direction: it offers an Unload
    the user can decline, rather than hiding a resident model behind a false Active badge.
    """
    try:
        from personalclaw.providers.use_cases import load_active_models

        refs: set[str] = set()
        for chain in (load_active_models() or {}).values():
            refs.update(str(ref) for ref in chain or [])
        return refs
    except Exception:  # noqa: BLE001
        logger.debug("active-model read failed for residency attribution", exc_info=True)
        return set()


def _is_active(provider_key: str, provider: Any, model: str, bound: set[str]) -> bool:
    """Whether ``model`` is still bound under EITHER spelling of the provider name.

    The registry keys on the APP name (``sentence-transformers``) while the provider's own
    ``.name`` may differ (``native``), and a stored ref can carry either — so a resident
    model must be checked against both or a bound model would show as reclaimable.
    """
    names = {provider_key, str(getattr(provider, "name", "") or "")}
    return any(f"{name}:{model}" in bound for name in names if name)


def loaded_occupants() -> list[dict[str, Any]]:
    """Every resident model across every registered local provider.

    Rows are ``{provider, model, kind, rss_mb, is_active, generation?, pid?}``. A sidecar
    contributes its child's rows (RSS from the child's own stat frame); an in-process
    provider contributes what its :meth:`~.provider.LocalModelProvider.loaded_models`
    reports, with ``rss_mb`` ``None`` because the gateway's heap cannot be attributed
    per-model.
    """
    from personalclaw.local_models.registry import registered
    from personalclaw.local_models.sidecar import get_runner

    bound = _bound_refs()
    rows: list[dict[str, Any]] = []
    for key, provider in registered():
        runner = get_runner(key)
        if runner is not None:
            rows.extend(_sidecar_rows(key, provider, runner, bound))
            continue
        try:
            resident = provider.loaded_models()
        except Exception:  # noqa: BLE001 — one broken provider must not blank the widget
            logger.debug("loaded_models failed for %s", key, exc_info=True)
            continue
        for entry in resident or []:
            model = str(entry.get("model") or "")
            rows.append(
                {
                    "provider": key,
                    "model": model,
                    "kind": "in-process",
                    "rss_mb": None,
                    "is_active": _is_active(key, provider, model, bound),
                }
            )
    return rows


def _sidecar_rows(key: str, provider: Any, runner: Any, bound: set[str]) -> list[dict[str, Any]]:
    """The rows for one sidecar provider: its child's models, its child's RSS.

    A child that is not running holds nothing, and says so with an empty list rather than
    a stale row from the last generation.
    """
    if not runner.is_alive():
        return []
    stat = runner.last_stat
    rss = float(stat.get("rss_mb", 0.0) or 0.0)
    health = runner.health()
    try:
        resident = provider.loaded_models()
    except Exception:  # noqa: BLE001
        resident = []
    if not resident:
        # The child is up but holds no named model yet — still worth a row, because the
        # process itself is occupying RAM and the user is asking what is.
        resident = [{"model": ""}]
    return [
        {
            "provider": key,
            "model": str(entry.get("model") or ""),
            "kind": "sidecar",
            "rss_mb": rss,
            "is_active": _is_active(key, provider, str(entry.get("model") or ""), bound),
            "generation": health.get("generation", 0),
            "pid": health.get("pid", 0),
        }
        for entry in resident
    ]


async def unload_provider(key: str) -> dict[str, Any]:
    """Free whatever the named provider holds. Idempotent.

    A sidecar is unloaded via its child's ``unload`` verb (the model goes, the process
    stays warm); if the child is unreachable the process is stopped instead, because a
    child that cannot be talked to is exactly the thing to reclaim. An in-process provider
    goes through the ABC's :meth:`~.provider.LocalModelProvider.unload`.

    Returns ``{ok, provider, kind, freed, pressure}`` — the post-unload pressure snapshot
    is what makes "Unload actually frees RSS" verifiable from the surface rather than
    asserted (Success Criterion 8).
    """
    from personalclaw.local_models.registry import get_provider
    from personalclaw.local_models.sidecar import SidecarCrashed, SidecarWorkerError, get_runner

    provider = get_provider(key)
    if provider is None:
        return {"ok": False, "provider": key, "error": f"Unknown provider {key!r}"}
    runner = get_runner(key)
    if runner is not None:
        freed = False
        if runner.is_alive():
            try:
                await runner.acall("unload", timeout=30.0)
                freed = True
            except (SidecarCrashed, SidecarWorkerError) as exc:
                logger.info("sidecar %s unload failed (%s) — stopping the child", key, exc)
                runner.stop()
                freed = True
        return {
            "ok": True,
            "provider": key,
            "kind": "sidecar",
            "freed": freed,
            "pressure": memory_pressure(),
        }
    try:
        freed = bool(provider.unload())
    except Exception as exc:  # noqa: BLE001 — report the failure, never 500 the widget
        return {"ok": False, "provider": key, "error": str(exc)[:200]}
    return {
        "ok": True,
        "provider": key,
        "kind": "in-process",
        "freed": freed,
        "pressure": memory_pressure(),
    }


async def residency_snapshot() -> dict[str, Any]:
    """The whole ``GET /api/models/loaded`` payload: occupants + pressure + readiness.

    ``providers`` carries each provider's :meth:`ensure_ready` state so a model paging in
    from disk reads as ``loading`` rather than as a hung request.
    """
    from personalclaw.local_models.registry import registered
    from personalclaw.local_models.sidecar import get_runner

    states: list[dict[str, Any]] = []
    for key, provider in registered():
        try:
            ok, state = await provider.ensure_ready()
        except Exception:  # noqa: BLE001
            ok, state = False, "unavailable"
        runner = get_runner(key)
        states.append(
            {
                "provider": key,
                "display_name": str(getattr(provider, "display_name", "") or key),
                "ok": bool(ok),
                "state": state,
                "kind": "sidecar" if runner is not None else "in-process",
                "sidecar": runner.health() if runner is not None else None,
            }
        )
    return {
        "loaded": loaded_occupants(),
        "providers": states,
        "pressure": memory_pressure(),
    }
