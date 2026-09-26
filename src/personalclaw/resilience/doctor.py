"""Doctor — the tiered, read-only health-probe framework (PLATFORM-RESILIENCE §1).

Readiness is NOT boolean. Every diagnosis names the tier that failed:

    tier 0  process    — gateway alive; app-backend subprocesses alive (watchdog)
    tier 1  socket     — the gateway port is listening
    tier 2  cheap RPC  — the system-presence probe (status snapshot) succeeds
    tier 3  capability — per-capability probe packs (memory / channels /
                         local-models / apps / serving-fs / model-providers)

:func:`run_doctor` executes the tiers in order and **short-circuits downward**: a
tier-2 failure does not run tier-3 packs against a dead gateway — it reports a
core failure at tier 2. Probes are **read-only by contract** — an exception
becomes an ``ok=False`` result, never a 500 — and secrets are masked in
``detail``/``evidence`` before they leave a probe.

The doctrine (§1.3), enforced as tests: a tier-3 capability failure degrades ONLY
that capability's row. It never marks the gateway unhealthy and never justifies a
restart — restart is justified only when the tier-2 cheap-RPC probe itself fails.

This module owns the framework and the probe packs, and every probe is read-only. A failed
probe names its repair (``fix_id``, a confirm-gated fix in :mod:`~personalclaw.resilience.fixes`)
or says plainly it has none (``remedy``). Failed capability checks are ALSO the remediation
engine's input: :func:`failed_checks` is what `remediation.measure_deficits` reads, so the
Maintenance health score cannot read 100 while this page shows a failure — one authority.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from personalclaw.config import loader as config_loader
from personalclaw.security import redact


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


class Tier(enum.IntEnum):
    """Probe tiers (ClawX three-tier readiness, extended per-capability).

    Ordered so a lower tier gates the higher ones: if tier 2 (cheap RPC) fails,
    tier-3 capability packs are not run — the gateway itself is the problem.
    """

    PROCESS = 0
    SOCKET = 1
    CHEAP_RPC = 2
    CAPABILITY = 3


# Tiers 0-2 are the CORE ladder: a failure here IS a gateway failure and short-
# circuits everything above it. Tier 3 is per-capability and never gates the core.
_CORE_TIERS = (Tier.PROCESS, Tier.SOCKET, Tier.CHEAP_RPC)


@dataclass(frozen=True)
class ProbeResult:
    """The outcome of one probe run.

    ``ok`` is the only pass/fail signal. ``detail`` is a one-line human summary;
    ``evidence`` carries structured specifics (counts, paths, states) for the
    disclosure UI. ``fix_id`` names a registered confirm-gated fix
    (:mod:`personalclaw.resilience.fixes`) when one repairs this failure. ``remedy`` is the
    failure's other half when no fix does: one plain sentence saying there is no automatic fix
    and what to do instead. The Doctor page promises "a failed probe's Fix"; a failure that has
    none must say so rather than leave the row a dead end. Both ``detail`` and ``remedy`` (and
    string ``evidence`` values) are redacted before return.
    """

    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    fix_id: Optional[str] = None
    remedy: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok, "detail": self.detail, "evidence": self.evidence}
        if self.fix_id:
            d["fix_id"] = self.fix_id
        if self.remedy:
            d["remedy"] = self.remedy
        return d


# A probe body is an async callable taking the (optional) doctor context and
# returning a ProbeResult. Blocking work (sqlite, sockets, filesystem) is wrapped
# in asyncio.to_thread by the probe body so run_doctor never blocks the loop.
ProbeFn = Callable[["DoctorContext"], Awaitable[ProbeResult]]


@dataclass(frozen=True)
class Probe:
    """A registered health probe.

    ``id`` is stable (an agent/UI can branch on it); ``capability`` groups probes
    into the Doctor's capability cards; ``tier`` places it on the readiness ladder.
    """

    id: str
    capability: str
    tier: Tier
    run: ProbeFn
    title: str = ""


@dataclass
class DoctorContext:
    """What a probe may consult. Everything is optional so probes run both inside
    the gateway (with live ``state``) and standalone (tests / CLI), degrading to
    direct read-only file access under ``config_dir()`` when no live state exists.
    """

    state: Any = None
    port: int = 0
    home: Path = field(default_factory=config_dir)


# ── The flat probe registry ────────────────────────────────────────────────

_PROBES: list[Probe] = []


def register_probe(probe: Probe) -> None:
    """Register a probe. Re-registering the same id replaces the prior one (so a
    reimport in tests never duplicates a capability row)."""
    global _PROBES
    _PROBES = [p for p in _PROBES if p.id != probe.id]
    _PROBES.append(probe)


def all_probes() -> list[Probe]:
    """Every registered probe (a copy — callers must not mutate the registry)."""
    return list(_PROBES)


def _mask(text: str) -> str:
    """Redact secrets from a human/evidence string (the framework invariant)."""
    try:
        return redact(str(text))
    except Exception:
        return str(text)


#: The remedy for a check that could not run at all: its own exception, not a finding.
_CHECK_CRASHED_REMEDY = (
    "No automatic fix — the check itself failed to run, which is a defect in PersonalClaw rather "
    "than a problem with your data. Re-run it; if it fails again, Settings → Diagnostics → Live "
    "logs shows the error to report."
)

#: "Restart the gateway", for the core-tier failures: the one step that clears them.
_RESTART_REMEDY = "No automatic fix — restart the gateway: `personalclaw restart`."


async def _safe_run(probe: Probe, ctx: DoctorContext) -> ProbeResult:
    """Run one probe, converting ANY exception into an ``ok=False`` result.

    This is the AUTO-R15 rule restated as the framework invariant: a probe never
    raises out to the caller — a broken probe reports a failed capability row, it
    does not 500 the Doctor.
    """
    try:
        res = await probe.run(ctx)
    except Exception as exc:  # a probe's own bug must not break the Doctor
        return ProbeResult(
            ok=False,
            detail=_mask(f"probe raised: {type(exc).__name__}: {exc}"),
            evidence={"error": _mask(str(exc))},
            remedy=_CHECK_CRASHED_REMEDY,
        )
    # Defensively mask the human-facing strings even on the happy path.
    return ProbeResult(
        ok=res.ok,
        detail=_mask(res.detail),
        evidence=res.evidence,
        fix_id=res.fix_id,
        remedy=_mask(res.remedy) if res.remedy else "",
    )


def _grouped(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group probe rows by capability into the Doctor report shape.

    Report: ``{ok, core_ok, worst, capabilities: {cap: {ok, tier, probes: [...]}},
    generated_at, restart_suggested}``. ``core_ok`` is the doctrine signal — True
    unless a CORE-tier (0-2) probe failed. ``restart_suggested`` is True ONLY when
    the cheap-RPC tier itself failed (never for a capability failure).
    """
    caps: dict[str, dict[str, Any]] = {}
    core_ok = True
    restart_suggested = False
    for row in rows:
        cap = row["capability"]
        bucket = caps.setdefault(cap, {"ok": True, "tier": int(row["tier"]), "probes": []})
        bucket["probes"].append(row)
        if not row["ok"]:
            bucket["ok"] = False
            bucket["tier"] = int(row["tier"])
            if row["tier"] in _CORE_TIERS:
                core_ok = False
                if row["tier"] == Tier.CHEAP_RPC:
                    restart_suggested = True
    worst = next((c for c, b in caps.items() if not b["ok"]), "")
    return {
        "ok": all(b["ok"] for b in caps.values()),
        "core_ok": core_ok,
        "worst": worst,
        "restart_suggested": restart_suggested,
        "capabilities": caps,
    }


def _row(probe: Probe, res: ProbeResult) -> dict[str, Any]:
    return {
        "id": probe.id,
        "capability": probe.capability,
        "tier": int(probe.tier),
        "title": probe.title or probe.id,
        **res.to_dict(),
    }


async def run_doctor(
    ctx: Optional[DoctorContext] = None, *, probes: Optional[list[Probe]] = None
) -> dict[str, Any]:
    """Run the full doctor: tiers in order, short-circuiting downward.

    Core tiers (0-2) run first. If any CORE-tier probe fails, tier-3 capability
    packs are skipped entirely (they would only report noise against a dead
    gateway) and the report says so. Within a tier, probes run concurrently.
    """
    ctx = ctx or DoctorContext()
    pool = probes if probes is not None else all_probes()
    rows: list[dict[str, Any]] = []

    core_failed = False
    for tier in _CORE_TIERS:
        tier_probes = [p for p in pool if p.tier == tier]
        if not tier_probes:
            continue
        results = await asyncio.gather(*(_safe_run(p, ctx) for p in tier_probes))
        for p, res in zip(tier_probes, results):
            rows.append(_row(p, res))
            if not res.ok:
                core_failed = True
        if core_failed:
            break  # short-circuit: do not run higher tiers against a broken core

    skipped: list[str] = []
    if not core_failed:
        cap_probes = [p for p in pool if p.tier == Tier.CAPABILITY]
        results = await asyncio.gather(*(_safe_run(p, ctx) for p in cap_probes))
        for p, res in zip(cap_probes, results):
            rows.append(_row(p, res))
    else:
        skipped = sorted({p.capability for p in pool if p.tier == Tier.CAPABILITY})

    report = _grouped(rows)
    report["skipped_capabilities"] = skipped
    report["generated_at"] = time.time()
    return report


def failed_checks(ctx: Optional[DoctorContext] = None) -> list[dict[str, Any]]:
    """Every FAILED capability (tier-3) check, as report rows — what the health score reads.

    The remediation engine's `measure_deficits` is synchronous and runs on worker threads, the
    CLI's main thread and in tests, so the probes run on a fresh event loop in a helper thread:
    that works whether or not the calling thread already has a loop. Core tiers are not re-run —
    in-process they report the process this is running in.
    """
    import concurrent.futures

    probes = [p for p in all_probes() if p.tier == Tier.CAPABILITY]
    ctx = ctx or DoctorContext()

    async def _run() -> list[dict[str, Any]]:
        results = await asyncio.gather(*(_safe_run(p, ctx) for p in probes))
        return [_row(p, res) for p, res in zip(probes, results) if not res.ok]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _run()).result()


async def run_capability(capability: str, ctx: Optional[DoctorContext] = None) -> dict[str, Any]:
    """Run just one capability's probes (the ``GET /api/doctor/{capability}`` path).

    A single-capability run does NOT enforce the core ladder — it is a targeted
    re-probe of one card the user opened, so it runs that capability's probes
    directly (any tier) and reports them.
    """
    ctx = ctx or DoctorContext()
    cap_probes = [p for p in all_probes() if p.capability == capability]
    if not cap_probes:
        return {"capability": capability, "ok": True, "probes": [], "unknown": True}
    results = await asyncio.gather(*(_safe_run(p, ctx) for p in cap_probes))
    rows = [_row(p, res) for p, res in zip(cap_probes, results)]
    return {
        "capability": capability,
        "ok": all(r["ok"] for r in rows),
        "probes": rows,
    }


# ── Core-tier probes (0-2) ───────────────────────────────────────────────────


async def _probe_gateway_process(ctx: DoctorContext) -> ProbeResult:
    """Tier 0 — the gateway process is alive (we are running inside it)."""
    # This probe runs in-process; reaching it at all means the event loop is live.
    return ProbeResult(ok=True, detail="gateway process alive")


async def _probe_gateway_socket(ctx: DoctorContext) -> ProbeResult:
    """Tier 1 — the gateway port is listening on loopback.

    Skips (ok=True, "no port") when no port is known — a standalone doctor run
    without a bound gateway is not a socket failure.
    """
    port = ctx.port
    if not port:
        return ProbeResult(ok=True, detail="no gateway port to probe", evidence={"port": 0})

    def _connect() -> bool:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", int(port))) == 0

    listening = await asyncio.to_thread(_connect)
    return ProbeResult(
        ok=listening,
        detail=f"port {port} {'listening' if listening else 'not connectable'}",
        evidence={"port": int(port)},
        remedy="" if listening else _RESTART_REMEDY,
    )


async def _probe_status_snapshot(ctx: DoctorContext) -> ProbeResult:
    """Tier 2 — the cheap-RPC / system-presence analog: the live status snapshot
    is readable. This is the ONLY probe whose failure suggests a restart."""
    state = ctx.state
    if state is None:
        return ProbeResult(ok=True, detail="no live state (standalone run)")
    try:
        snap = state.status_snapshot()
    except Exception as exc:
        return ProbeResult(
            ok=False, detail=_mask(f"status snapshot failed: {exc}"), remedy=_RESTART_REMEDY
        )
    return ProbeResult(ok=True, detail="status snapshot ok", evidence={"keys": len(snap or {})})


# ── Capability probe packs (tier 3) ──────────────────────────────────────────


#: The Fix (and maintenance job) for a memory index that is missing embedded rows.
MEMORY_INDEX_FIX = "memory.rebuild-faiss-index"


def memory_index_gaps(home: Path) -> dict[str, Any]:
    """Which embedded memories the faiss index semantic recall reads does NOT hold. Read-only.

    "The index" is the one recall reads: in the gateway, the live store it registered
    (`vector_memory.recall_store`), whose in-memory copy is what a turn searches; in a process
    with none (the CLI) the persisted sidecar, which is what the next open loads after
    reconciling it. Reading another store instance's copy, or the file while a live index has
    moved on, would report on something recall does not use — in either direction. It never
    touches a live handle beyond reading its id list, so no embed_fn is wired as a side effect.

    Missing rows split two ways because their remedies differ: ``missing`` rows are at the width
    the current model produces and a rebuild indexes them; ``other_model`` rows were embedded by
    a different model and only a re-embed can make them searchable. A deleted row still in the
    index is not counted — search skips it.

    Shared by the ``memory.store`` probe and its Fix preview, so the row and the Fix cannot
    disagree about what is broken.
    """
    import json

    from personalclaw import vector_memory as vm

    db_path = home / "memory.db"
    ev: dict[str, Any] = {"db_present": db_path.exists(), "faiss_available": vm.faiss_available()}
    if not db_path.exists():
        return ev
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        ev["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        ev["integrity"] = str(conn.execute("PRAGMA integrity_check(1)").fetchone()[0])
        rows = conn.execute(
            "SELECT id, length(embedding) FROM episodic_memories "
            "WHERE is_deleted=0 AND embedding IS NOT NULL ORDER BY created_at, id"
        ).fetchall()
    finally:
        conn.close()
    ev["embedded_count"] = len(rows)
    live = vm.recall_store(db_path)
    if live is not None:
        indexed = set(live.index_state()["ids"])
        ev["index_source"] = "live"
    else:
        ids_path = home / "memory.ids.json"
        try:
            ids = json.loads(ids_path.read_text(encoding="utf-8")) if ids_path.exists() else []
        except (OSError, ValueError):
            ids = []
        indexed = set(ids) if isinstance(ids, list) else set()
        ev["index_source"] = "file"
    current = rows[-1][1] if rows else 0  # the newest row's byte width: the model bound now
    ev["faiss_ids"] = sum(1 for r in rows if r[0] in indexed)
    ev["missing"] = sum(1 for r in rows if r[1] == current and r[0] not in indexed)
    ev["other_model"] = sum(1 for r in rows if r[1] != current)
    ev["dim"] = current // 4
    return ev


async def _probe_memory(ctx: DoctorContext) -> ProbeResult:
    """memory — memory.db opens + WAL, and the index recall reads holds every embedded row.

    Read-only; the measurement is :func:`memory_index_gaps`. Without faiss there is no index to
    hold anything — recall searches the SQLite vectors directly — so that is a note, not a desync.
    """
    ev = await asyncio.to_thread(memory_index_gaps, ctx.home)
    if not ev.get("db_present"):
        return ProbeResult(ok=True, detail="no memory.db yet (fresh install)", evidence=ev)
    if ev.get("integrity") not in (None, "ok"):
        return ProbeResult(
            ok=False,
            detail="memory.db integrity check failed",
            evidence=ev,
            remedy=(
                "No automatic fix — restore memory.db from a snapshot under Settings → "
                "Durability, or copy the file aside before anything writes to it again."
            ),
        )
    if not ev.get("faiss_available"):
        return ProbeResult(
            ok=True,
            detail="faiss is not installed — semantic recall searches the stored vectors directly",
            evidence=ev,
        )
    embedded, missing, other = ev["embedded_count"], ev["missing"], ev["other_model"]
    if missing:
        return ProbeResult(
            ok=False,
            detail=f"faiss index desync: {ev['faiss_ids']} indexed vs {embedded} embedded rows",
            evidence=ev,
            fix_id=MEMORY_INDEX_FIX,
        )
    if other:
        detail = (
            f"{other} of {embedded} embedded memor{'y was' if other == 1 else 'ies were'} "
            "embedded by a different model — semantic recall cannot search "
            f"{'it' if other == 1 else 'them'}"
        )
        from personalclaw.providers.provider_bridge import can_resolve_use_case

        if await asyncio.to_thread(can_resolve_use_case, "embedding"):
            # The Fix re-embeds exactly these rows with the model bound now, then rebuilds.
            return ProbeResult(ok=False, detail=detail, evidence=ev, fix_id=MEMORY_INDEX_FIX)
        return ProbeResult(
            ok=False,
            detail=detail,
            evidence=ev,
            remedy=(
                "No automatic fix while no embedding model is bound — nothing can re-embed them. "
                "Bind one in Settings → Models: that re-embeds every memory. Keyword recall still "
                "finds them meanwhile."
            ),
        )
    return ProbeResult(ok=True, detail="memory.db healthy", evidence=ev)


async def _probe_channels(ctx: DoctorContext) -> ProbeResult:
    """channels — each registered transport's connected/health signal.

    Reads the transport registry directly and calls each transport's own
    read-only ``health()`` (no ``bind_state`` side effect — an unbound transport
    reporting ``offline`` is a truthful signal, not a probe failure).
    """
    from personalclaw.channel_transports import get_transport, list_transports

    names = list_transports()
    if not names:
        return ProbeResult(ok=True, detail="no channel transports registered", evidence={})

    transports: dict[str, Any] = {}
    for name in names:
        t = get_transport(name)
        if t is None:
            continue
        try:
            health = await t.health()
        except Exception as exc:
            health = {"state": "error", "detail": _mask(str(exc))}
        transports[name] = {
            "connected": bool(getattr(t, "connected", False)),
            "state": str(health.get("state", "")),
        }
    errored = [n for n, v in transports.items() if v["state"] == "error"]
    return ProbeResult(
        ok=not errored,
        detail=(
            f"{len(errored)} transport{'s' if len(errored) != 1 else ''} errored"
            if errored
            else f"{len(transports)} transport{'s' if len(transports) != 1 else ''} ok"
        ),
        evidence={"transports": transports},
        remedy=(
            "No automatic fix — check the connection and credentials of each channel this row's "
            "details mark `error` under Settings → Providers → Channel providers, or disable one "
            "you no longer use there."
            if errored
            else ""
        ),
    )


async def _probe_local_models(ctx: DoctorContext) -> ProbeResult:
    """local-models — per-provider availability + phantom-binding detection.

    Per registered local provider: ``is_available()`` reports whether that
    runtime's deps are importable. Phantom binding = a bound ``provider:model``
    ref whose provider IS a registered local provider (so this pack owns it) but
    whose model id is absent from that provider's own catalog. Refs to non-local
    providers (cloud/config) are NOT this pack's concern and are never flagged —
    that would false-alarm every ``bedrock:``/``openai:`` binding. A provider that
    is unavailable is skipped for the catalog check (its unavailability is the
    reported signal; an empty fail-soft catalog must not masquerade as phantoms).

    Scope note: the on-disk HF ``models--`` layout probe belongs to
    LOCAL-MODEL-MANAGER-V2 (``local_models/layouts.py``, unbuilt) — this pack uses
    provider-computed availability and binding-integrity, not a raw cache scan.
    """
    from personalclaw.local_models.registry import catalog_for, get_provider, registered
    from personalclaw.providers.use_cases import load_active_models, split_ref

    reg = dict(registered())  # {registry_key(app/ext name): provider}
    avail: dict[str, bool] = {}
    for key, prov in reg.items():
        try:
            avail[key] = bool(await prov.is_available())
        except Exception:
            avail[key] = False

    # Bound model ids per LOCAL provider key (cloud/config prefixes excluded here).
    bound_by_local: dict[str, set[str]] = {}
    for refs in load_active_models().values():
        for ref in refs:
            parsed = split_ref(ref)
            if not parsed:
                continue
            provider_name, model_id = parsed
            if provider_name in reg:
                bound_by_local.setdefault(provider_name, set()).add(model_id)

    # Phantom = bound-to-a-local-provider model absent from that AVAILABLE
    # provider's catalog.
    phantom: list[str] = []
    for key, model_ids in bound_by_local.items():
        if not avail.get(key):
            continue  # unavailable → skip; empty catalog would be a false phantom
        bound_prov = get_provider(key)
        if bound_prov is None:
            continue
        try:
            catalog = await catalog_for(bound_prov)
        except Exception:
            catalog = []
        catalog_ids = {m.name for m in catalog}
        for model_id in model_ids:
            if model_id not in catalog_ids:
                phantom.append(f"{key}:{model_id}")

    unavailable = [k for k, v in avail.items() if not v]
    ok = not phantom  # unavailable providers are a WARN, not a failure of this pack
    detail_parts = []
    if phantom:
        detail_parts.append(f"{len(phantom)} phantom binding{'s' if len(phantom) != 1 else ''}")
    if unavailable:
        detail_parts.append(
            f"{len(unavailable)} provider{'s' if len(unavailable) != 1 else ''} unavailable"
        )
    if not detail_parts:
        detail_parts.append(f"{len(reg)} local provider{'s' if len(reg) != 1 else ''} ok")
    return ProbeResult(
        ok=ok,
        detail="; ".join(detail_parts),
        evidence={
            "available": avail,
            "unavailable": unavailable,
            "phantom_bindings": sorted(phantom),
        },
        remedy=(
            "No automatic fix — each binding under `phantom_bindings` names a local model its "
            "provider no longer lists (deleted or renamed). Bind that use case to a model that "
            "exists in Settings → Models, or download the model again."
            if phantom
            else ""
        ),
    )


async def _probe_apps(ctx: DoctorContext) -> ProbeResult:
    """apps — per enabled backend app: subprocess alive (watchdog) + leftover
    ``.{name}.rollback`` dirs from interrupted updates.

    Read-only: consults the backend supervisor's live table (``get(name)`` returns
    None for a dead entry) and globs the apps dir for rollback leftovers (does NOT
    call ``recover_interrupted_updates`` — that mutates).

    Scope note: installed-copy-vs-repo manifest drift has no stored hash to diff
    (INTEGRATION recon) — deferred to the plan that adds a manifest checksum; this
    pack probes liveness + rollback leftovers, the real signals available today.
    """
    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.manager import apps_dir, list_apps

    def _read() -> dict[str, Any]:
        sup = get_backend_supervisor()
        backends: dict[str, Any] = {}
        for app in list_apps():
            if not app.get("enabled", False):
                continue
            manifest = app.get("manifest", {}) or {}
            if not (manifest.get("backend", {}) or {}).get("entryPoint"):
                continue
            name = app.get("name", "")
            rb = sup.get(name)
            backends[name] = {"alive": rb is not None}
        rollbacks: list[str] = []
        ad = apps_dir()
        if ad.exists():
            for child in ad.iterdir():
                if (
                    child.is_dir()
                    and child.name.startswith(".")
                    and child.name.endswith(".rollback")
                ):
                    rollbacks.append(child.name)
        return {"backends": backends, "rollback_leftovers": rollbacks}

    ev = await asyncio.to_thread(_read)
    dead = [n for n, v in ev["backends"].items() if not v["alive"]]
    problems = []
    if dead:
        problems.append(f"{len(dead)} backend{'s' if len(dead) != 1 else ''} not running")
    if ev["rollback_leftovers"]:
        problems.append(
            f"{len(ev['rollback_leftovers'])} interrupted "
            f"update{'s' if len(ev['rollback_leftovers']) != 1 else ''}"
        )
    return ProbeResult(
        ok=not problems,
        detail=(
            "; ".join(problems)
            if problems
            else f"{len(ev['backends'])} app backend{'s' if len(ev['backends']) != 1 else ''} ok"
        ),
        evidence=ev,
        # An interrupted update's leftover is what the orphan prune reconciles (restore or drop,
        # decided by the apps reconciler) — a working Fix for that half.
        fix_id="serving-fs.orphan-prune" if ev["rollback_leftovers"] else None,
        remedy=(
            "No automatic fix for a backend that is down: the app watchdog already relaunches "
            "one every 30 seconds, so a backend that stays down is failing to start — Settings → "
            "Diagnostics → Live logs shows why. Disable the app under Settings → Apps to stop "
            "the retries."
            if dead
            else ""
        ),
    )


async def _probe_serving_fs(ctx: DoctorContext) -> ProbeResult:
    """serving/fs — the static/dist symlink (the stale-SPA bug-class) + dead
    lock/PID leftovers.

    Replicates ``frontend.ensure_dev_dist_symlink``'s DETECTION logic read-only
    (never calls it — that mutates): flags a real-directory copy shadowing the
    runtime symlink, and a symlink whose target is gone. Also counts dead
    ``locks/*.lock`` and dead PID rows in ``session_pids.txt``/``agent_pids.txt``.

    Covers BOTH variants of the stale-SPA bug-class. The symlink checks above
    only see a shadowing copy or a broken link; a correct symlink pointing at an
    OUTDATED build passes every one of them, which is how a standing validation
    rig served a two-commit-old SPA while this probe reported healthy. The
    freshness check closes that blind spot via ``frontend.spa_dist_freshness``.
    """
    import os

    home = ctx.home

    def _read() -> dict[str, Any]:
        ev: dict[str, Any] = {}
        # static/dist — resolve the package dir the running gateway serves from.
        import personalclaw

        pkg_dir = Path(personalclaw.__file__).resolve().parent
        dist = pkg_dir / "static" / "dist"
        if dist.is_symlink():
            target = None
            with contextlib.suppress(OSError):
                target = dist.resolve(strict=True)
            ev["dist"] = {
                "kind": "symlink",
                "target_ok": bool(target and (target / "index.html").is_file()),
            }
        elif dist.is_dir():
            # 🔴 A PLAIN DIRECTORY IS NOT EVIDENCE OF A SHADOWING COPY — it is what the WHEEL
            # SHIPS. This branch classified every real directory as `copy`, and the detail below
            # turns `copy` into "serves a stale SPA", so EVERY pip-installed instance reported a
            # permanent serving-fs fault. Measured on a fresh container (0.1.3 wheel): `static/dist`
            # a real directory holding the very `index.html` whose `/assets/*.js` the gateway had
            # just served 200, no `web/` anywhere in the image — and the Doctor said "COPY shadowing
            # the runtime symlink (serves a stale SPA)", which put a degraded badge on a new user's
            # home screen for a fault that cannot exist there.
            #
            # 🔑 THE OTHER HALF OF THIS PROBE ALREADY GETS THIS RIGHT. `spa_dist_freshness` returns
            # `no-sources` for exactly this case and documents it as "an installed wheel. Not
            # checkable, never a fault." Same probe, same distinction, one half of it missing.
            #
            # And the FIX already knew too: `serving-fs.symlink-repair` refuses with "no web/dist
            # build found to link" on an installed layout — so the fault we reported was one whose
            # only offered remediation declines to run. `resolve_website_dist` is that same
            # discriminator, now shared: a symlink can only be shadowed where there is something
            # for it to point AT.
            from personalclaw.frontend import resolve_website_dist

            shadowable = resolve_website_dist(pkg_dir) is not None
            ev["dist"] = {
                "kind": "copy" if shadowable else "packaged",
                "target_ok": (dist / "index.html").is_file(),
            }
        else:
            ev["dist"] = {"kind": "missing", "target_ok": False}

        # Is the build BEHIND the sources? (the variant the symlink checks miss)
        from personalclaw.frontend import spa_dist_freshness

        state, freshness_ev = spa_dist_freshness(pkg_dir.parent.parent)
        ev["dist_freshness"] = {"state": state, **freshness_ev}

        # dead locks
        locks_dir = home / "locks"
        dead_locks = 0
        if locks_dir.exists():
            dead_locks = sum(1 for p in locks_dir.glob("*.lock") if _lock_is_stale(p))
        ev["dead_locks"] = dead_locks

        # dead PID rows
        dead_pids = 0
        for fname in ("session_pids.txt", "agent_pids.txt"):
            fp = home / fname
            if not fp.exists():
                continue
            for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
                pid = _last_pid(line)
                if pid and not _pid_alive(pid):
                    dead_pids += 1
        ev["dead_pids"] = dead_pids
        return ev

    def _lock_is_stale(path: Path) -> bool:
        # A lock file whose flock is unheld is stale; we approximate read-only by
        # age (a lock file older than a day whose owner is gone). We avoid taking
        # the flock here (that mutates lock state), so this is a soft signal.
        try:
            return (time.time() - path.stat().st_mtime) > 86400
        except OSError:
            return False

    def _last_pid(line: str) -> int:
        part = line.strip().split(":")[-1] if line.strip() else ""
        return int(part) if part.isdigit() else 0

    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, not ours
        except OSError:
            return False

    ev = await asyncio.to_thread(_read)
    dist = ev.get("dist", {})
    problems = []
    fix_id: Optional[str] = None
    remedy = ""
    if dist.get("kind") == "copy":
        problems.append("static/dist is a COPY shadowing the runtime symlink (serves a stale SPA)")
        fix_id = "serving-fs.symlink-repair"  # confirm-gated repair (§2)
    elif not dist.get("target_ok"):
        problems.append(f"static/dist {dist.get('kind')} — SPA not resolvable")
        remedy = (
            "No automatic fix — the dashboard's built files are missing. From a source checkout, "
            "build them with `make web-build`; an installed copy needs reinstalling."
        )
    if ev.get("dist_freshness", {}).get("state") == "stale":
        problems.append(
            "the built SPA is STALE — web/dist was built from different sources than the "
            "checked-out web/ (serves an old dashboard); rebuild with `make web-build`"
        )
        remedy = remedy or "No automatic fix — rebuild the dashboard with `make web-build`."
    if ev.get("dead_locks") or ev.get("dead_pids"):
        fix_id = fix_id or "serving-fs.orphan-prune"
    return ProbeResult(
        ok=not problems,
        detail=("; ".join(problems) if problems else "serving/fs healthy"),
        evidence=ev,
        fix_id=fix_id,
        remedy=remedy if problems else "",
    )


async def _probe_model_providers(ctx: DoctorContext) -> ProbeResult:
    """model-providers — COMPOSED from AUTONOMY-GUARDRAILS §2.5 provider health
    (breaker state + latency + failure modes derived from the model-call audit).

    The Doctor RENDERS this view; it never rebuilds the audit. An OPEN breaker is a
    degraded row, not a core failure.
    """
    from personalclaw.guardrails.health import provider_health

    health = await asyncio.to_thread(provider_health)
    providers = health.get("providers", [])
    open_breakers = [p["name"] for p in providers if p.get("breaker_state") == "open"]
    return ProbeResult(
        ok=not open_breakers,
        detail=(
            f"{len(open_breakers)} provider{'s' if len(open_breakers) != 1 else ''} "
            "with an open breaker"
            if open_breakers
            else f"{len(providers)} provider{'s' if len(providers) != 1 else ''}, no open breakers"
        ),
        evidence={"providers": providers, "generated_from": health.get("generated_from", 0)},
        remedy=(
            "No automatic fix, and none is needed to re-close a breaker: after its recovery "
            "window the next call goes through as a test, and a success closes it. If calls keep "
            "failing, check that provider's key and status under Settings → Providers."
            if open_breakers
            else ""
        ),
    )


async def _probe_crashes(ctx: DoctorContext) -> ProbeResult:
    """crashes — recent structured crash artifacts (PLATFORM-RESILIENCE §6.5).

    A crash file on disk is not a live failure — the gateway is running (we are
    probing from inside it). It's a WARN so the user (or the agent-run Doctor) sees
    that an unhandled failure was captured, with the most recent one summarized.
    """
    from personalclaw.resilience import crashes as _crashes

    recent = await asyncio.to_thread(_crashes.recent_crashes, 10)
    if not recent:
        return ProbeResult(ok=True, detail="no crash artifacts", evidence={"crashes": []})
    latest = recent[0]
    return ProbeResult(
        ok=False,
        detail=(
            f"{len(recent)} recent crash artifact{'s' if len(recent) != 1 else ''}; "
            f"latest: {latest.get('kind')} — "
            f"{latest.get('exception_type')}"
        ),
        evidence={"crashes": recent},
        remedy=(
            "No automatic fix — these record failures that already happened, not a live one. "
            "Each is a JSON file (named under `crashes` in the details) in "
            f"{ctx.home / 'crashes'}: read them, report one that keeps recurring, then remove "
            "them to clear this row."
        ),
    )


#: The window every memory-pipeline aggregate is read over. A week, because the batch
#: window is 15 minutes and the cadences that flush are per-turn: a day is short enough
#: that a weekend away reads as a dead pipeline.
_MEMORY_WINDOW_DAYS = 7

#: A run of consecutive ``FLUSH_OK`` records this long is the dead-read signature —
#: passes completing and finding nothing, over and over. Ten because a handful of turns
#: that teach nothing is the NORMAL case (most turns are not lessons); ten in a row on a
#: store that produced something earlier in the window is not.
_MEMORY_OK_STREAK_WARN = 10

#: Unconsumed staging entries above this are a drain that isn't running. Capture is
#: cheap and consumption is batched, so a backlog is expected — an unbounded one is the
#: consolidation pass never claiming a batch.
_MEMORY_BACKLOG_WARN = 200


async def _probe_memory_pipeline(ctx: DoctorContext) -> ProbeResult:
    """memory-pipeline — is memory extraction actually running? (PLATFORM-RESILIENCE §3.2.)

    Silent memory-pipeline death (the S05 bug-class) is invisible from outside precisely
    because the healthy case and the dead case both look like silence: a pass that ran and
    honestly found nothing is indistinguishable from a pass whose reader returns nothing,
    unless someone counted. LEARN-R19's ``flush_records`` are that count, and this probe is
    their first health consumer.

    Three WARN shapes, all read off :meth:`StagingStore.health` +
    :meth:`~personalclaw.learning.staging.StagingStore.cost_by_op`:

    * **flush errors** — a pass raised. That used to vanish into a ``debug`` log; now it is
      a ``FLUSH_ERROR`` row with its exception type, so it is a first-class WARN.
    * **a FLUSH_OK streak with nothing produced** — ``all_ok_streak`` is the signal the
      staging module itself names as "worth alarming on". Gated on the window having run
      real passes AND having produced nothing, so a quiet week cannot trip it.
    * **an unconsumed staging backlog** — capture works, the drain does not.

    Read-only by contract, and that includes not CREATING the log: ``StagingStore`` builds
    its schema on first cursor, so a home that never staged anything is answered from the
    absent file rather than by opening one. Per-op cost rides along as evidence — "was it
    expensive" is answerable from one total, but "expensive at WHAT" is the question that
    leads to a change, and it is the same split the flywheel's own cost panel reads.
    """

    def _read() -> dict[str, Any]:
        from personalclaw.learning.staging import DB_FILE, StagingStore

        home = ctx.home
        db_path = home / DB_FILE
        if not db_path.exists():
            # No staging log yet — a fresh home, not a broken pipeline. Never open the
            # store here: opening it would write the schema from a read-only probe.
            return {"staging_log": False}
        store = StagingStore(home)
        try:
            health = store.health(days=_MEMORY_WINDOW_DAYS)
            per_op = store.cost_by_op(days=_MEMORY_WINDOW_DAYS)
            backlog = store.pending_count()
        finally:
            store.close()
        by_outcome = dict(health.get("by_outcome") or {})
        return {
            "staging_log": True,
            "days": _MEMORY_WINDOW_DAYS,
            "passes": int(health.get("passes") or 0),
            "by_outcome": by_outcome,
            "errors": int(health.get("errors") or 0),
            "all_ok_streak": int(health.get("all_ok_streak") or 0),
            "produced": int(by_outcome.get("flush_produced") or 0),
            "staged_entries": int(health.get("staged_entries") or 0),
            "staging_backlog": int(backlog),
            "cost_usd": health.get("cost_usd"),
            # Capped: the "op" is a cadence, so this list is short by construction — the
            # cap is for a home that invented cadences, not for the shipped four.
            "cost_by_op": per_op[:8],
            "thresholds": {
                "ok_streak": _MEMORY_OK_STREAK_WARN,
                "backlog": _MEMORY_BACKLOG_WARN,
            },
        }

    try:
        ev = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return ProbeResult(
            ok=False,
            detail=f"staging log unreadable: {exc}",
            evidence={},
            remedy=_CHECK_CRASHED_REMEDY,
        )

    if not ev.get("staging_log"):
        return ProbeResult(
            ok=True, detail="no staging log yet (nothing captured on this home)", evidence=ev
        )

    reasons: list[str] = []
    if ev["errors"]:
        reasons.append(
            f"{ev['errors']} flush error{'s' if ev['errors'] != 1 else ''} in {ev['days']}d"
        )
    if ev["all_ok_streak"] >= _MEMORY_OK_STREAK_WARN and ev["passes"] and not ev["produced"]:
        reasons.append(
            f"{ev['all_ok_streak']} consecutive flush_ok passes and nothing produced in "
            f"{ev['days']}d"
        )
    if ev["staging_backlog"] >= _MEMORY_BACKLOG_WARN:
        reasons.append(f"{ev['staging_backlog']} staged entries unconsumed (drain not running)")
    if reasons:
        return ProbeResult(
            ok=False,
            detail="; ".join(reasons),
            evidence=ev,
            remedy=(
                "No automatic fix — this row's details count the week's passes by outcome, and "
                "Settings → Diagnostics → Live logs has each flush error in full."
            ),
        )
    return ProbeResult(
        ok=True,
        detail=(
            f"{ev['passes']} pass{'es' if ev['passes'] != 1 else ''} in {ev['days']}d, "
            f"{ev['produced']} produced, "
            f"{ev['staging_backlog']} awaiting consolidation, ${ev['cost_usd']}"
        ),
        evidence=ev,
    )


# ── Register the initial probe set ───────────────────────────────────────────


async def _probe_state_inventory(ctx: DoctorContext) -> ProbeResult:
    """durability — is every path under the home claimed by the state manifest? (S179)

    🔴 WHY THIS EXISTS. `durability.inventory.audit_home()` is the claims-everything guard — the
    thing that "keeps the manifest honest … which is precisely how nine directories silently escaped
    backup before the inventory existed". It had **no runtime caller**: the only invocations were in
    `test_durability_inventory.py`, against a hand-built eight-path fixture. A store added after the
    manifest was written therefore could not fail it.

    Pointed at a REAL home for the first time it reported **10 unclaimed paths and 5482 undeclared
    databases**, including `learning.db` (the learning staging log and usage counters, 135 KB of
    live
    state) — verified absent from a real archive. A guard that only ever runs against its own
    fixture
    is testing the fixture.

    So the Doctor runs it on the actual home. Read-only: `audit_home` only stats and globs. A gap
    FAILS this tier-3 check, which degrades the durability card and nothing else — unclaimed state
    is a backup-coverage gap the user should see and act on, never a reason to call the install
    broken. There is no automatic fix (claiming a path is a change to the manifest, which ships
    with a release), so the row says that and what to do meanwhile.
    """
    home = ctx.home

    def _read() -> dict[str, Any]:
        from personalclaw.durability.inventory import audit_home

        res = audit_home(home)
        return {
            "claimed": res.claimed,
            "ignored": res.ignored,
            # Capped: a `db_container` regression once produced 5478 rows, and an unreadable
            # evidence blob is the same failure as no evidence.
            "unclaimed": res.unclaimed[:20],
            "unclaimed_count": len(res.unclaimed),
            "undeclared_dbs": res.undeclared_dbs[:20],
            "undeclared_db_count": len(res.undeclared_dbs),
        }

    try:
        ev = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return ProbeResult(
            ok=False,
            detail=f"inventory audit failed: {exc}",
            evidence={},
            remedy=_CHECK_CRASHED_REMEDY,
        )

    gaps = ev["unclaimed_count"] + ev["undeclared_db_count"]
    if not gaps:
        return ProbeResult(ok=True, detail=f"all {ev['claimed']} state paths claimed", evidence=ev)
    return ProbeResult(
        ok=False,
        detail=(
            f"{ev['unclaimed_count']} unclaimed "
            f"path{'s' if ev['unclaimed_count'] != 1 else ''} and "
            f"{ev['undeclared_db_count']} undeclared "
            f"database{'s' if ev['undeclared_db_count'] != 1 else ''} — "
            f"{'these are' if gaps != 1 else 'this is'} in NO snapshot"
        ),
        evidence=ev,
        remedy=(
            "No automatic fix — a snapshot leaves out any path the state manifest does not "
            "claim, and claiming one ships with a release. The files are still on disk: copy the "
            "paths listed in this row's details somewhere safe before you restore a snapshot, "
            "and report them so the manifest claims them."
        ),
    )


async def _probe_remote_reachability(ctx: DoctorContext) -> ProbeResult:
    """remote — can this dashboard be reached from a phone, and safely? (MOBILE-COMPANION S1)

    Three outcomes, all read-only (no token minted, no network dialed beyond a
    stdlib address enumeration):

    * **tailnet detected** → ok. The machine holds a 100.64.0.0/10 address, so a
      phone on the same tailnet reaches ``http://<tailnet-ip>:<port>`` over the
      tailnet's own encryption. Evidence carries that phone-usable BASE url; the
      detail points at ``personalclaw token`` for the signed-in link. This probe
      NEVER mints or prints a live token — a read-only health check must not
      generate a secret, and evidence strings are redacted anyway.
    * **exposed without auth** → not ok. The bind host is non-loopback AND auth is
      off (``AuthMode.NONE`` / ``PERSONALCLAW_DEV_NO_AUTH``). That is the one
      genuine misconfiguration: anything that reaches the interface walks in.
      (``effective_bind`` forces NONE to loopback, so this only arises when
      ``PERSONALCLAW_BIND_HOST`` overrode the bind.)
    * **bypass behind a declared proxy** → not ok (RUA-5). The opt-in
      ``PERSONALCLAW_BYPASS_LOCAL_NETWORKS`` bypass is armed on an instance that
      also declares ``trusted_proxies`` or a ``public_url``. See the comment block
      at the branch itself for why the *combination* is the hazard when neither
      half alone is. **Reported, never enforced** — this row changes no admission
      decision.
    * **local-only** → ok. Normal local install, no tailnet — informational: see
      remote-access.md to reach it from a phone.

    CAPABILITY tier: a missing tailnet is not a failure and must never gate the
    core ladder.
    """
    from personalclaw.dashboard.origin import (
        auth_is_off,
        declared_proxy_front,
        is_local_bind,
        local_network_bypass_enabled,
        resolve_bind_host,
        tailnet_ip,
        tailscale_cli_present,
    )

    def _probe() -> dict[str, Any]:
        bind_host = resolve_bind_host()
        return {
            "bind_host": bind_host,
            "local_bind": is_local_bind(bind_host),
            "auth_off": auth_is_off(),
            "tailnet_ip": tailnet_ip(),
            "tailscale_cli": tailscale_cli_present(),
        }

    facts = await asyncio.to_thread(_probe)
    port = ctx.port or 0
    tnet = facts["tailnet_ip"]

    # The misconfiguration the contract names: reachable off-box with no auth.
    if not facts["local_bind"] and facts["auth_off"]:
        return ProbeResult(
            ok=False,
            detail=(
                f"bind {facts['bind_host']} exposes the dashboard beyond loopback with "
                "auth OFF — set a password (see docs/guides/remote-access.md) or bind loopback"
            ),
            evidence={
                "bind_host": facts["bind_host"],
                "auth_off": True,
                "guide": "docs/guides/remote-access.md",
            },
            remedy=(
                "No automatic fix — the gateway was started this way (PERSONALCLAW_BIND_HOST with "
                "auth off). Restart it (`personalclaw restart`) without PERSONALCLAW_BIND_HOST, or "
                "with auth on; docs/guides/remote-access.md covers both."
            ),
        )

    # ── The bypass-behind-a-proxy hazard (RUA-5) ──
    #
    # DIAGNOSTIC ONLY. This row reports; it does not gate. Who is admitted is decided
    # entirely by the token-auth middleware and is byte-identical with or without this
    # block. Whether that admission behaviour *should* change is an owner fork
    # (product-roadmap:structural_block:security-control-fork-…), not this probe's call —
    # so the probe makes the hazard VISIBLE and stops there.
    #
    # Why the COMBINATION is the hazard when neither half alone is. With the bypass armed
    # the middleware grants token-free access to any request whose resolved client address
    # is private (`is_private_network(_resolved_client_ip(request))`, token_auth.py:943-946).
    # That is defensible on a home LAN, where "private address" really does mean "someone in
    # my house". Behind a reverse proxy it stops meaning that: the address the middleware
    # resolves is the PROXY's — 127.0.0.1 for a local tunnel daemon, 172.18.x.x on a compose
    # bridge — and both are private. So every request the proxy forwards, from anywhere on
    # the internet, is admitted without a token. Declaring `trusted_proxies` or `public_url`
    # is precisely the operator saying "my traffic arrives through a proxy", which is what
    # makes the pair reportable while either half alone is not.
    #
    # MIRRORED, not re-derived (cli_doctor.py:344-349 asks for this, #2860): both facts come
    # from `dashboard/origin` — the module that already mirrors the middleware's short-circuits
    # for `loopback_requires_token` — the bypass via `local_network_bypass_enabled()` and the
    # config side via `declared_proxy_front()`, which reads `dashboard/exposure`, the single
    # module that owns the exposure signal. This row spells neither the env var nor the config
    # keys itself, so it cannot drift from the behaviour it reports on. Going through `origin`
    # rather than importing `exposure` here is also what keeps `structural-import-direction`
    # honest: core must not import the HTTP surface, `origin` is doctor's ONE grandfathered
    # `dashboard` edge, and `dashboard` importing itself is exempt by construction.
    #
    # Placed before the tailnet branch because a tailnet does not make the bypass safe behind
    # a proxy, and after the auth-OFF branch so that broader, already-shipped case keeps its
    # own message. Guarded on the bypass FIRST: not armed ⇒ no config is read and this row is
    # byte-identical to what it returned before this block existed.
    if local_network_bypass_enabled():
        declared_proxies, has_public_url = await asyncio.to_thread(declared_proxy_front)
        if declared_proxies or has_public_url:
            return ProbeResult(
                ok=False,
                detail=(
                    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1 on an instance that declares a "
                    "proxy in front of it: the bypass admits any client whose address is "
                    "private, and behind a proxy that address is the proxy's own — so "
                    "requests arriving through it are never asked for a token. Unset the "
                    "variable, or clear trusted_proxies/public_url "
                    "(see docs/guides/remote-access.md)"
                ),
                evidence={
                    "bypass_local_networks": True,
                    "trusted_proxies": declared_proxies,
                    "public_url_declared": has_public_url,
                    "guide": "docs/guides/remote-access.md",
                },
                remedy=(
                    "No automatic fix — restart the gateway (`personalclaw restart`) without "
                    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS, or clear trusted_proxies and public_url "
                    "from its config (docs/guides/remote-access.md)."
                ),
            )

    if tnet:
        base_url = f"http://{tnet}:{port}" if port else f"http://{tnet}"
        return ProbeResult(
            ok=True,
            detail=(
                f"tailnet {tnet} — open {base_url} on your phone "
                "(run `personalclaw token` for the signed-in link)"
            ),
            evidence={
                "tailnet_ip": tnet,
                "phone_url": base_url,
                "tailscale_cli": facts["tailscale_cli"],
                "token_hint": "personalclaw token",
            },
        )

    return ProbeResult(
        ok=True,
        detail="local-only; see docs/guides/remote-access.md to reach it from a phone",
        evidence={
            "bind_host": facts["bind_host"],
            "tailscale_cli": facts["tailscale_cli"],
            "guide": "docs/guides/remote-access.md",
        },
    )


async def _probe_knowledge_vector_index(ctx: DoctorContext) -> ProbeResult:
    """knowledge — is the chunk ANN index (sqlite-vec) live, and does it cover the chunks? (KL-11)

    🔴 WHY THIS EXISTS. KL-10 made knowledge search score every embedded CHUNK, measured at
    ~21 µs/row in Python — roughly 650 ms/query on a 5,000-item library. KL-11 puts a
    ``sqlite-vec`` ``vec0`` index in front of that, but SQLite extension loading depends on how
    the interpreter's SQLite was built, so on some installs the index cannot load and search
    silently reverts to that linear scan. A user whose search feels slow deserves to be told
    WHY here rather than concluding the product is broken.

    Reports **degraded, not failed** in both directions: an install with no extension has a
    correct-but-slower search, and an index whose row count has drifted from the live chunks is
    repaired by the next search's reconciliation. Neither is an outage, and failing hard on
    either would make a stripped SQLite build look like one. Read-only throughout: the
    capability probe runs on a throwaway in-memory connection and the coverage read opens
    ``knowledge.db`` with ``mode=ro``, so the probe can never create or rebuild an index.
    """
    from personalclaw.knowledge.store import knowledge_db_path
    from personalclaw.knowledge.vector_index import VEC_REMEDY, ChunkVectorIndex
    from personalclaw.knowledge.vector_index import probe as vec_probe
    from personalclaw.sqlite_compat import sqlite3 as store_sqlite3

    # Through the one helper that owns this path (a second copy of it once split the store's
    # brain), and with `create=False` so a health check never leaves a directory behind.
    db_path = knowledge_db_path(ctx.home, create=False)

    def _read() -> dict[str, Any]:
        cap = vec_probe()
        ev: dict[str, Any] = {
            "extension_available": cap.available,
            "db_present": db_path.exists(),
        }
        if cap.version:
            ev["sqlite_vec_version"] = cap.version
        if cap.reason:
            ev["reason"] = cap.reason
        if not (cap.available and db_path.exists()):
            return ev
        conn = store_sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            ev.update(ChunkVectorIndex(conn).coverage())
        finally:
            conn.close()
        return ev

    try:
        ev = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return ProbeResult(
            ok=False,
            detail=f"vector index probe failed: {exc}",
            evidence={},
            remedy=_CHECK_CRASHED_REMEDY,
        )

    if not ev.get("extension_available"):
        ev["degraded"] = True
        ev["remedy"] = VEC_REMEDY
        return ProbeResult(
            ok=True,
            detail=(
                "sqlite-vec could not load "
                f"({ev.get('reason', 'unknown reason')}) — knowledge vector search uses the "
                "exact scan: correct, but linear in library size"
            ),
            evidence=ev,
        )

    dims = ev.get("dimensions") or {}
    stale = sorted(d for d, c in dims.items() if c.get("indexed") != c.get("live"))
    indexed_total = sum(int(c.get("indexed") or 0) for c in dims.values())
    if stale:
        ev["degraded"] = True
        ev["stale_dimensions"] = stale
        return ProbeResult(
            ok=True,
            detail=(
                f"chunk ANN index active but out of step at {len(stale)} "
                f"dimension{'s' if len(stale) != 1 else ''} "
                f"({', '.join(stale)}) — the next search rebuilds it"
            ),
            evidence=ev,
        )
    return ProbeResult(
        ok=True,
        detail=f"chunk ANN index active ({indexed_total} chunk "
        f"vector{'s' if indexed_total != 1 else ''} indexed)",
        evidence=ev,
    )


async def _probe_baseline_denylist(_ctx: DoctorContext) -> ProbeResult:
    """security — is the enforced bash denylist still the baseline we shipped? (SH-6)

    Every ``denied_command_patterns()`` read already re-asserts the in-memory list, so
    in-process drift is healed continuously. This probe is the *periodic* half: it
    re-reads the packaged baseline data file and compares it to the fingerprint captured
    at import, which is the only way to notice an on-disk edit that rewrote the patterns
    and their digest together. A diverged file is never adopted — the verified
    baseline stays in force, so this reports the divergence rather than a shrunk denylist.
    """
    from personalclaw.security import verify_baseline_denylist

    report = await asyncio.to_thread(verify_baseline_denylist)
    ok = bool(report["file_verified"])
    detail = (
        f"baseline v{report['version']} verified — {report['count']} patterns enforced"
        if ok
        else f"{report['detail']} — enforcing the verified baseline ({report['count']} patterns)"
    )
    return ProbeResult(
        ok=ok,
        detail=detail,
        evidence={
            "version": report["version"],
            "sha256": report["sha256"][:16],
            "patterns": report["count"],
            "file_verified": ok,
        },
        remedy=(
            ""
            if ok
            else "No automatic fix — the denylist file on disk no longer matches the one that "
            "shipped (an edit, or a damaged install). The verified patterns stay enforced, so "
            "nothing is weaker; reinstall PersonalClaw to restore the file."
        ),
    )


async def _probe_credential_backend(_ctx: DoctorContext) -> ProbeResult:
    """security — which credential store is actually holding the secrets? (SH-1)

    Reports the RESOLVED backend, not the requested one. The distinction is the whole
    point: an install that sets ``PERSONALCLAW_CREDENTIAL_BACKEND=keychain`` on a headless
    box with no secret service keeps its credentials in ``.env`` at 0600, and a doctor line
    echoing the *request* would tell that user their secrets are in a keychain that does
    not exist. ``ok=False`` for exactly that mismatch — nothing was lost and nothing landed
    in a weaker location, but the operator asked for something they did not get.

    Also reports the ``.env`` mode when dotenv is the active backend — the mode it READ,
    which is a different claim from the 0600 the fallback promises. ``detail`` used to render
    ``mode or '0600'``, so a fresh install with no ``.env`` and no credentials reported
    "credentials stored in .env at mode 0600" while its own evidence carried ``env_mode: ""``
    (#2922). The file's absence is now in the evidence (``env_exists``) instead of being left
    to be inferred, and the sentence never states a mode nothing measured.

    Read-only: the probe never repairs the mode (the next ``load_credentials()`` does) and
    never reads a secret VALUE — only names, modes, states.
    """
    from personalclaw.config.credentials import (
        credential_backend_warning,
        credential_store_state,
        keychain_available,
    )

    def _facts() -> tuple[Any, str, bool]:
        # All three in the one thread hop — each touches the filesystem or the OS secret
        # service, and the warning must describe the same backend resolution the state does.
        return credential_store_state(), credential_backend_warning(), keychain_available()

    state, warning, keychain = await asyncio.to_thread(_facts)
    evidence: dict[str, Any] = {
        "backend": state.backend,
        "requested": state.requested,
        "keychain_available": keychain,
        "env_exists": state.env_exists,
        "env_mode": state.env_mode,
        "env_readable": state.env_readable,
    }

    if warning:
        return ProbeResult(
            ok=False,
            detail=warning,
            evidence=evidence,
            remedy=(
                "No automatic fix — make an OS keyring available to this process, or stop "
                'asking for one: turn off "Store credentials in the OS keychain" under '
                "Settings → Security, and unset PERSONALCLAW_CREDENTIAL_BACKEND if it is set."
            ),
        )

    if state.backend == "keychain":
        return ProbeResult(
            ok=True, detail="credentials stored in the OS keychain (keyring)", evidence=evidence
        )

    if not state.env_readable:
        return ProbeResult(
            ok=True,
            detail="the .env credential file could not be inspected, so its mode is unknown",
            evidence=evidence,
        )

    if not state.env_exists:
        return ProbeResult(
            ok=True,
            detail=(
                "no credentials stored yet — there is no .env file; the dotenv backend "
                "creates it at mode 0600 on the first write"
            ),
            evidence=evidence,
        )

    if state.env_group_or_world_readable:
        return ProbeResult(
            ok=False,
            detail=(
                f"credential file .env is mode {state.env_mode} — group/world readable; "
                "it is repaired to 0600 on the next credential read"
            ),
            evidence=evidence,
            remedy=(
                "No automatic fix is needed: the next credential read sets it back to 0600. "
                f"To close it now, run `chmod 600 {state.env_path}`."
            ),
        )
    return ProbeResult(
        ok=True,
        detail=f"credentials stored in .env at mode {state.env_mode}",
        evidence=evidence,
    )


async def _probe_knowledge_searchability(ctx: DoctorContext) -> ProbeResult:
    """knowledge — which ingested items can search NOT fully reach? (RET-2, RET-4)

    🔴 WHY THIS EXISTS. Measured before RET-2: an image-only PDF and a document ingested
    with no embedding provider both persisted ``processing_status='done'`` while part of
    retrieval could not see either — the AnythingLLM #6143 shape, where the app reports
    success and RAG returns no sources. The ingest runner now persists ``unsearchable`` + a
    typed reason instead; this is the surface that makes those items VISIBLE rather than a
    status value in a table nobody opens.

    🔴 AND WHAT IT MAY CLAIM ABOUT THEM. This row used to say "they are in the library and no
    query can reach them" — measured false in live validation, where keyword search found
    both notes in the library and through ``knowledge_search`` (whose note printed the same
    claim, then listed the match). Three of the four reasons remove only SEMANTIC reach. The
    sentence is therefore :attr:`~personalclaw.knowledge.searchability.Degradation.summary`,
    the one the search tool prints too; this probe composes no claim of its own.

    **Reports failed, not degraded**, and that is the deliberate half. Doctor's other
    knowledge probes report degraded because a slower-but-correct search is not an outage.
    An item half of search cannot see is a gap the user must act on (bind an embedder, add
    a text version, re-ingest), which is exactly what ``ok=False`` is for. Per §1.3 it still
    degrades only this capability: it never marks the gateway unhealthy and never justifies
    a restart.

    **One row per item, counted the way the library counts.** ``items`` carries a row per
    LIBRARY item, so the surface names WHICH document is affected and the number matches the
    list a user can check it against. Rows the library deliberately never lists — an
    artifact's search mirror, a report's finding — go under ``unlisted_items`` and are named
    apart in the sentence ("2 items and 1 artifact"), never folded into "items". Read-only
    throughout: ``knowledge.db`` is opened ``mode=ro`` with ``create=False``, so a health
    check on an install that has never used knowledge creates nothing.

    **RET-4 folds in one more gap**: an item whose PASSAGE vectors came from a different
    embedding model than the one bound now (``stale_index``). Keyword search still reaches
    it and semantic search skips it, the same user-visible shape as a missing embedding, so
    it belongs in this row rather than in a second probe a user has to correlate. Its remedy
    is different and the row says so: a re-index, not a re-ingest.
    """
    from personalclaw.knowledge.embedding_fingerprint import (
        active_fingerprint,
        count_stale_chunks,
        has_fingerprint_columns,
        stale_chunk_items,
        stale_rows,
    )
    from personalclaw.knowledge.searchability import (
        LIBRARY_SHELF,
        UNSEARCHABLE,
        degradations_from,
        row_select,
        rows_from,
    )
    from personalclaw.knowledge.store import knowledge_db_path
    from personalclaw.sqlite_compat import sqlite3 as store_sqlite3

    db_path = knowledge_db_path(ctx.home, create=False)

    def _read() -> dict[str, Any]:
        ev: dict[str, Any] = {"db_present": db_path.exists()}
        if not db_path.exists():
            return ev
        conn = store_sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        stale: list = []
        try:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
            ).fetchone()
            if not has:
                # Created by the store's schema block, so its absence means this install has
                # never opened the knowledge store — not that an ingest broke.
                ev["items_table"] = False
                return ev
            ev["items_table"] = True
            # The table's REAL columns: a ``mode=ro`` reader cannot migrate, so a column an
            # older build never added must read as NULL rather than fail the probe.
            present = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
            records = [
                tuple(r)
                for r in conn.execute(
                    f"SELECT {row_select(present)} FROM items "  # noqa: S608 — fixed columns
                    "WHERE processing_status = ? AND COALESCE(is_archived, 0) = 0 "
                    "ORDER BY created_at, id",
                    (UNSEARCHABLE,),
                ).fetchall()
            ]
            # RET-4 — items whose PASSAGE vectors came from a different embedding model.
            # Read here, on the same read-only connection, so one probe answers "what in my
            # library cannot be found" completely. `has_fingerprint_columns` is the guard a
            # ``mode=ro`` reader needs: it cannot run the store's migration, so a database
            # written by an older build must report "cannot tell" instead of raising.
            fp = active_fingerprint()
            if fp is not None and has_fingerprint_columns(conn):
                ev["active_embedding_model"] = str(fp)
                ev["stale_chunk_vectors"] = count_stale_chunks(conn, fp)
                stale = stale_rows(stale_chunk_items(conn, fp))
        finally:
            conn.close()
        rows = rows_from(records) + stale
        listed = [r for r in rows if r.shelf == LIBRARY_SHELF]
        unlisted = [r for r in rows if r.shelf != LIBRARY_SHELF]
        degradations = degradations_from(rows)
        ev["unsearchable"] = len(listed)
        ev["items"] = [r.to_dict() for r in listed]
        ev["unlisted"] = len(unlisted)
        ev["unlisted_items"] = [r.to_dict() for r in unlisted]
        ev["by_reason"] = {d.reason: d.item_count for d in degradations if d.item_count}
        ev["reasons"] = [d.detail for d in degradations]
        ev["summaries"] = [d.summary for d in degradations]
        return ev

    try:
        ev = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return ProbeResult(
            ok=False,
            detail=f"knowledge searchability probe failed: {exc}",
            evidence={},
            remedy=_CHECK_CRASHED_REMEDY,
        )

    if not ev.get("db_present") or not ev.get("items_table"):
        return ProbeResult(ok=True, detail="no knowledge library on disk", evidence=ev)
    if not (ev.get("unsearchable") or ev.get("unlisted")):
        return ProbeResult(
            ok=True, detail="every ingested item is fully reachable by search", evidence=ev
        )
    return ProbeResult(
        ok=False,
        detail="; ".join(ev["summaries"]),
        evidence=ev,
        remedy=(
            "Each row under `items` is in your library and missing from part of search; its "
            "reason names which part. `no_embedding_provider`/`not_indexed`: keyword search "
            "already finds the item, so bind an embedding model (Settings → Models) and re-index "
            "to add semantic search. `no_extractable_text`: the file is a scan, so add a text "
            "version or bind an OCR/vision model, then re-ingest the item. `stale_index`: the "
            "item is fine and its vectors are not — they came from a different embedding model, "
            "so run the embedding re-index; nothing needs re-ingesting. Rows under "
            "`unlisted_items` are search copies the library does not list (an artifact's mirror, "
            "a report's finding) and take the same fix."
        ),
    )


async def _probe_knowledge_vault(ctx: DoctorContext) -> ProbeResult:
    """knowledge — is any markdown projection waiting on the OWNER? (KL-20)

    The projection is two-way, so it has exactly two states only a human can clear: a page
    that changed HERE and in the app since the last sync (nothing was written on either side,
    the file is untouched) and a page the owner deleted while its item is still in the library
    (never re-created, never silently resurrected). Both are recorded in
    ``vault_projections``; this is the surface that makes them visible instead of a row in a
    table nobody reads.

    **Reports degraded, not failed.** A conflict is the projection working as designed — the
    alternative to surfacing it is resolving it silently toward the database, which is the one
    outcome the atom forbids. Failing the capability would make correct behaviour look like an
    outage, and Doctor's own doctrine is that a tier-3 row never justifies a restart.

    Read-only: opens ``knowledge.db`` with ``mode=ro`` and ``create=False``, so a health check
    on an install that has never used knowledge creates nothing and reports "no projection".
    """
    from personalclaw.knowledge.store import knowledge_db_path
    from personalclaw.sqlite_compat import sqlite3 as store_sqlite3

    db_path = knowledge_db_path(ctx.home, create=False)

    def _read() -> dict[str, Any]:
        ev: dict[str, Any] = {"db_present": db_path.exists()}
        if not db_path.exists():
            return ev
        conn = store_sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_projections'"
            ).fetchone()
            if not has:
                # The table is created by the store's schema block, so its absence means this
                # install has never opened the knowledge store — not that the projection broke.
                ev["projected"] = 0
                return ev
            row = conn.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(CASE WHEN COALESCE(conflict,'') != '' THEN 1 ELSE 0 END) AS conflicts, "
                "SUM(CASE WHEN COALESCE(owner_deleted,0) != 0 THEN 1 ELSE 0 END) AS deleted "
                "FROM vault_projections"
            ).fetchone()
            ev["projected"] = int(row[0] or 0)
            ev["conflicts"] = int(row[1] or 0)
            ev["owner_deleted"] = int(row[2] or 0)
            ev["pages"] = [
                str(r[0] or r[1] or "")
                for r in conn.execute(
                    "SELECT relpath, item_id FROM vault_projections "
                    "WHERE COALESCE(conflict,'') != '' OR COALESCE(owner_deleted,0) != 0 "
                    "ORDER BY item_id LIMIT 20"
                ).fetchall()
            ]
        finally:
            conn.close()
        return ev

    try:
        ev = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return ProbeResult(
            ok=False,
            detail=f"knowledge vault probe failed: {exc}",
            evidence={},
            remedy=_CHECK_CRASHED_REMEDY,
        )

    waiting = int(ev.get("conflicts") or 0) + int(ev.get("owner_deleted") or 0)
    if not ev.get("db_present") or not ev.get("projected"):
        return ProbeResult(ok=True, detail="no markdown projection on disk", evidence=ev)
    if waiting:
        ev["degraded"] = True
        ev["remedy"] = (
            "Open each page listed under `pages` in the vault: resolve the text you want, "
            "then delete the `sync_conflict:` line from its frontmatter. A page you meant to "
            "remove is removed by deleting its item in the app."
        )
        return ProbeResult(
            ok=True,
            detail=(
                f"{ev.get('projected')} page{'s' if ev.get('projected') != 1 else ''} projected; "
                f"{waiting} waiting on you "
                f"({ev.get('conflicts')} changed on both sides, "
                f"{ev.get('owner_deleted')} deleted here but still in the library)"
            ),
            evidence=ev,
        )
    return ProbeResult(
        ok=True,
        detail=f"{ev.get('projected')} page{'s' if ev.get('projected') != 1 else ''} projected, "
        "none in conflict",
        evidence=ev,
    )


async def _probe_sandbox_cgroup_scopes(ctx: DoctorContext) -> ProbeResult:
    """sandbox — does the cgroup v2 pids/RSS enforcement tier exist on THIS host?

    The NOFILE floor is a per-process rlimit and applies everywhere. The pids and RSS
    ceilings are only enforceable as a transient ``systemd-run --user --scope`` over a
    unified cgroup v2 hierarchy, so on macOS, a non-systemd Linux, or a container without a
    systemd user session they are simply not enforced. This row says that in plain words
    rather than simulating a bound that does not exist.

    **The ok=True vs ok=False call.** Unavailability is not a gateway failure — this is a
    tier-3 CAPABILITY probe, so a red here degrades only the sandbox row and never justifies
    a restart. The split is therefore by CONSEQUENCE, not by platform:

    * tier available → ``ok=True``; ``evidence.enforced`` names what is actually bounded.
    * tier unavailable and NO pids/RSS ceiling configured → ``ok=True``. A permanent red on
      every Mac trains operators to ignore the doctor, and nothing is being silently
      dropped: both ceilings are off (0). The ``detail`` still names what is unenforced, so
      the fact is on the row rather than hidden behind a green.
    * tier unavailable while a ceiling IS configured → ``ok=False``. Here a green would hide
      two configured controls that cannot do what the operator asked of them, which is
      exactly the dishonesty this probe exists to prevent.

    The availability decision itself is ``sandbox.probe_cgroup_scopes()`` — the same cached
    function the spawn path consults — so the doctor can never report a tier the spawn path
    does not actually use. A second copy of the detection here would drift from enforcement.

    Precision note for the wording: where the tier is missing the shim may still set
    ``RLIMIT_NPROC``/``RLIMIT_AS`` when configured, but those are a per-USER process count
    and an address-space cap, not a per-subtree pids/RSS bound — hence "not enforced".

    Never raises: a missing ``/sys/fs/cgroup``, an absent ``systemd-run``, an unreadable
    file, or a permission error all degrade to unavailable with the cause recorded in
    ``evidence.availability_detail``. Degrading toward "not enforced" is the honest
    direction — a probe that cannot prove enforcement must not claim it. The probe only
    REPORTS; the single loud warning belongs to the sandbox module, so re-running the doctor
    can never multiply it.
    """
    evidence: dict[str, Any] = {"platform": sys.platform}

    nofile = max_pids = max_rss_mb = 0
    try:
        from personalclaw.sandbox import ResourceCeilings

        ceilings = await asyncio.to_thread(ResourceCeilings.from_config)
        nofile, max_pids, max_rss_mb = ceilings.nofile, ceilings.max_pids, ceilings.max_rss_mb
    except Exception as exc:
        evidence["ceilings_detail"] = _mask(f"sandbox ceilings unreadable: {exc}")

    try:
        from personalclaw.sandbox import probe_cgroup_scopes

        available, why = await asyncio.to_thread(probe_cgroup_scopes)
    except Exception as exc:
        available = False
        why = (
            f"{sys.platform}: the cgroup availability check could not be completed "
            f"({type(exc).__name__})"
        )
        evidence["availability_error"] = _mask(str(exc))

    configured = {"nofile": nofile, "max_pids": max_pids, "max_rss_mb": max_rss_mb}
    requested = [name for name in ("max_pids", "max_rss_mb") if configured[name] > 0]
    reason = _mask(
        str(why) or f"{sys.platform}: no cgroup v2 unified hierarchy / systemd user session"
    )
    evidence.update(
        {
            "cgroup_scope_tier_available": bool(available),
            "availability_detail": reason,
            "configured_ceilings": configured,
            "enforced": ["NOFILE", "pids", "RSS"] if available else ["NOFILE"],
            "unenforced": [] if available else ["pids", "RSS"],
        }
    )

    if available:
        return ProbeResult(
            ok=True,
            detail=(
                f"cgroup v2 scope tier available — {reason}. pids and RSS ceilings are "
                "enforced per spawn subtree, and the NOFILE limit applies as always."
            ),
            evidence=evidence,
        )

    unenforced = (
        f"pids and RSS ceilings are NOT enforced on this host — {reason}. "
        "The NOFILE limit still applies to every spawn."
    )
    if requested:
        asked = " and ".join(f"sandbox.{name}={configured[name]}" for name in requested)
        return ProbeResult(
            ok=False,
            detail=(
                f"{unenforced} You have configured {asked}, which this host cannot enforce "
                "as a per-subtree scope — run on Linux with a systemd user session, or set "
                "it back to 0 so the config stops promising a bound nothing applies."
            ),
            evidence=evidence,
            remedy=(
                "No automatic fix — this host cannot apply the ceiling, so set it back to 0 ("
                + ", ".join(f"`personalclaw config set sandbox.{name} 0`" for name in requested)
                + ") or run the gateway on Linux with a systemd user session."
            ),
        )
    return ProbeResult(
        ok=True,
        detail=(
            f"{unenforced} No pids or RSS ceiling is configured, so nothing is being "
            "silently dropped."
        ),
        evidence=evidence,
    )


async def _probe_timezone(_ctx: DoctorContext) -> ProbeResult:
    """scheduling — which zone a timed trigger's wall clock is read in (#2520).

    A WARN (`ok=False` at tier 3, so it degrades this card and nothing else) for exactly two
    states, both of which silently relocate every reminder:

      * the machine's zone cannot be determined, so schedules fall back to **UTC**. The detail
        names the CONSEQUENCE in hours — "timed triggers will fire at UTC, which is 7 hour(s)
        off this host's local time" — rather than reporting the condition, because "timezone
        source: utc-fallback" is an informational line a user has no reason to act on;
      * `config.timezone` holds something that is not an IANA key (`CEST`, a typo), so it is
        being ignored. Reporting the RESOLVED zone and not the requested one is the same rule
        the credential-backend probe follows.

    A correctly resolved zone is `ok=True` and still reports the zone and where it came from,
    because "which timezone does this install think it is in" was previously unanswerable from
    any surface — `server_tz` said UTC on a PDT host.
    """
    from personalclaw.timezones import zone_report

    facts = await asyncio.to_thread(zone_report)
    evidence = {k: v for k, v in facts.items() if k != "warning"}
    if facts["warning"]:
        return ProbeResult(
            ok=False,
            detail=facts["warning"],
            evidence=evidence,
            remedy=(
                "No automatic fix — set the zone your schedules should use with "
                "`personalclaw setup`."
            ),
        )
    return ProbeResult(
        ok=True,
        detail=(
            f"timed triggers resolve to {facts['resolved']} "
            f"(from {facts['source']}, UTC{facts['utc_offset_hours']:+g})"
        ),
        evidence=evidence,
    )


async def _probe_resource_limits(_ctx: DoctorContext) -> ProbeResult:
    """sandbox — is the POSIX ``resource`` (rlimit) facility available on THIS host?

    The gateway raises its own ``RLIMIT_NOFILE`` soft cap at boot, and the sandbox spawn
    shim applies rlimit ceilings, both through the ``resource`` module. That module is
    POSIX-only and absent on native Windows — where it is missing, both degrade silently
    to a no-op. This probe surfaces that platform fact rather than leaving it invisible.

    **The ok=True call.** Absence is a platform CAPABILITY fact, not a gateway failure: a
    permanent red on every Windows host would train operators to ignore the doctor (the same
    reasoning the cgroup-scopes probe follows), and nothing is being *silently* dropped once
    the row states it. So both states are ``ok=True`` at tier 3; the ``detail`` carries the
    consequence loudly when the facility is missing. The decision is the SAME guarded helper
    the gateway and shim consult (``resource_limits_available``), so this can never claim a
    facility the boot path does not actually have.
    """
    from personalclaw.resource_limits import resource_limits_available

    available = await asyncio.to_thread(resource_limits_available)
    evidence = {"platform": sys.platform, "available": bool(available)}
    if available:
        return ProbeResult(
            ok=True,
            detail=(
                "POSIX resource limits (rlimit) available — the gateway raises its own "
                "NOFILE ceiling at boot and the sandbox shim can apply rlimit ceilings."
            ),
            evidence=evidence,
        )
    return ProbeResult(
        ok=True,
        detail=(
            f"POSIX resource limits (rlimit) are NOT available on this platform "
            f"({sys.platform}) — the `resource` module is absent, so the gateway's NOFILE "
            "ceiling is not raised at boot and the sandbox rlimit floor does not apply."
        ),
        evidence=evidence,
    )


def _register_builtin_probes() -> None:
    register_probe(
        Probe(
            "gateway.process", "core", Tier.PROCESS, _probe_gateway_process, "Gateway process alive"
        )
    )
    register_probe(
        Probe(
            "gateway.socket", "core", Tier.SOCKET, _probe_gateway_socket, "Gateway port listening"
        )
    )
    register_probe(
        Probe(
            "gateway.status",
            "core",
            Tier.CHEAP_RPC,
            _probe_status_snapshot,
            "Status snapshot readable",
        )
    )
    register_probe(
        Probe(
            "durability.inventory",
            "durability",
            Tier.CAPABILITY,
            _probe_state_inventory,
            "Every state path is claimed by the manifest",
        )
    )
    register_probe(
        Probe(
            "memory.store",
            "memory",
            Tier.CAPABILITY,
            _probe_memory,
            "Memory store + faiss consistency",
        )
    )
    register_probe(
        Probe(
            "channels.transports",
            "channels",
            Tier.CAPABILITY,
            _probe_channels,
            "Channel transports reachable",
        )
    )
    register_probe(
        Probe(
            "local-models.providers",
            "local-models",
            Tier.CAPABILITY,
            _probe_local_models,
            "Local model providers + bindings",
        )
    )
    register_probe(
        Probe(
            "apps.backends",
            "apps",
            Tier.CAPABILITY,
            _probe_apps,
            "App backends + interrupted updates",
        )
    )
    register_probe(
        Probe(
            "serving-fs.dist",
            "serving-fs",
            Tier.CAPABILITY,
            _probe_serving_fs,
            "SPA symlink + lock/PID leftovers",
        )
    )
    register_probe(
        Probe(
            "model-providers.health",
            "model-providers",
            Tier.CAPABILITY,
            _probe_model_providers,
            "Model provider health (breakers/latency)",
        )
    )
    register_probe(
        Probe(
            "memory-pipeline.freshness",
            "memory-pipeline",
            Tier.CAPABILITY,
            _probe_memory_pipeline,
            "Memory extraction pipeline",
        )
    )
    register_probe(
        Probe(
            "crashes.recent",
            "crashes",
            Tier.CAPABILITY,
            _probe_crashes,
            "Recent crash artifacts",
        )
    )
    register_probe(
        Probe(
            "remote.reachability",
            "remote",
            Tier.CAPABILITY,
            _probe_remote_reachability,
            "Remote reachability (tailnet / exposure)",
        )
    )
    register_probe(
        Probe(
            "knowledge.vector-index",
            "knowledge",
            Tier.CAPABILITY,
            _probe_knowledge_vector_index,
            "Knowledge chunk ANN index (sqlite-vec)",
        )
    )
    register_probe(
        Probe(
            "knowledge.searchability",
            "knowledge",
            Tier.CAPABILITY,
            _probe_knowledge_searchability,
            "Items search cannot fully reach",
        )
    )
    register_probe(
        Probe(
            "knowledge.vault",
            "knowledge",
            Tier.CAPABILITY,
            _probe_knowledge_vault,
            "Markdown projection: pages waiting on you",
        )
    )
    register_probe(
        Probe(
            "security.baseline_denylist",
            "security",
            Tier.CAPABILITY,
            _probe_baseline_denylist,
            "Baseline command denylist integrity",
        )
    )
    register_probe(
        Probe(
            "security.credential_backend",
            "security",
            Tier.CAPABILITY,
            _probe_credential_backend,
            "Active credential backend (keychain / .env 0600)",
        )
    )
    register_probe(
        Probe(
            "sandbox.cgroup_scopes",
            "sandbox",
            Tier.CAPABILITY,
            _probe_sandbox_cgroup_scopes,
            "Sandbox pids/RSS enforcement",
        )
    )
    register_probe(
        Probe(
            "sandbox.resource_limits",
            "sandbox",
            Tier.CAPABILITY,
            _probe_resource_limits,
            "POSIX resource-limits (rlimit) availability",
        )
    )
    register_probe(
        Probe(
            "scheduling.timezone",
            "scheduling",
            Tier.CAPABILITY,
            _probe_timezone,
            "Wall-clock timezone for timed triggers",
        )
    )


_register_builtin_probes()
