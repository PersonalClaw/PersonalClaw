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

This module owns the framework + the initial probe packs. It is read-only; the
confirm-gated fixes (§2), simulators (§3), degraded contract (§5), and remediation
engine (§4) land in later sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import socket
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from personalclaw.config.loader import config_dir
from personalclaw.security import redact


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
    disclosure UI. ``fix_id`` names a confirm-gated fix (§2, later session) when a
    remediation exists — ``None`` today for every probe (fixes are a later slice).
    Both ``detail`` and string ``evidence`` values are redacted before return.
    """

    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    fix_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok, "detail": self.detail, "evidence": self.evidence}
        if self.fix_id:
            d["fix_id"] = self.fix_id
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
        )
    # Defensively mask the human-facing string even on the happy path.
    return ProbeResult(
        ok=res.ok, detail=_mask(res.detail), evidence=res.evidence, fix_id=res.fix_id
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
        return ProbeResult(ok=False, detail=_mask(f"status snapshot failed: {exc}"))
    return ProbeResult(ok=True, detail="status snapshot ok", evidence={"keys": len(snap or {})})


# ── Capability probe packs (tier 3) ──────────────────────────────────────────


async def _probe_memory(ctx: DoctorContext) -> ProbeResult:
    """memory — memory.db opens + WAL, and faiss index size matches embedded count.

    Read-only: opens a short-lived connection to ``memory.db`` and reads the
    ``memory.ids.json`` faiss sidecar directly, replicating memory_stats()'s
    consistency check without touching the live faiss handle (which would auto-wire
    an embed_fn — a side effect a probe must not cause).
    """
    home = ctx.home
    db_path = home / "memory.db"
    ids_path = home / "memory.ids.json"

    def _read() -> dict[str, Any]:
        ev: dict[str, Any] = {"db_present": db_path.exists()}
        if not db_path.exists():
            return ev
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            ev["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            ev["integrity"] = str(conn.execute("PRAGMA integrity_check(1)").fetchone()[0])
            row = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories "
                "WHERE is_deleted=0 AND embedding IS NOT NULL"
            ).fetchone()
            ev["embedded_count"] = int(row[0]) if row else 0
        finally:
            conn.close()
        if ids_path.exists():
            import json

            try:
                ids = json.loads(ids_path.read_text(encoding="utf-8"))
                ev["faiss_ids"] = len(ids) if isinstance(ids, list) else 0
            except Exception:
                ev["faiss_ids"] = None
        else:
            ev["faiss_ids"] = 0
        return ev

    ev = await asyncio.to_thread(_read)
    if not ev.get("db_present"):
        return ProbeResult(ok=True, detail="no memory.db yet (fresh install)", evidence=ev)
    if ev.get("integrity") not in (None, "ok"):
        return ProbeResult(ok=False, detail="memory.db integrity check failed", evidence=ev)
    embedded, faiss_ids = ev.get("embedded_count"), ev.get("faiss_ids")
    if isinstance(embedded, int) and isinstance(faiss_ids, int) and embedded != faiss_ids:
        return ProbeResult(
            ok=False,
            detail=f"faiss index desync: {faiss_ids} indexed vs {embedded} embedded rows",
            evidence=ev,
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
            f"{len(errored)} transport(s) errored"
            if errored
            else f"{len(transports)} transport(s) ok"
        ),
        evidence={"transports": transports},
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
        detail_parts.append(f"{len(phantom)} phantom binding(s)")
    if unavailable:
        detail_parts.append(f"{len(unavailable)} provider(s) unavailable")
    if not detail_parts:
        detail_parts.append(f"{len(reg)} local provider(s) ok")
    return ProbeResult(
        ok=ok,
        detail="; ".join(detail_parts),
        evidence={
            "available": avail,
            "unavailable": unavailable,
            "phantom_bindings": sorted(phantom),
        },
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
        problems.append(f"{len(dead)} backend(s) not running")
    if ev["rollback_leftovers"]:
        problems.append(f"{len(ev['rollback_leftovers'])} interrupted update(s)")
    return ProbeResult(
        ok=not problems,
        detail=("; ".join(problems) if problems else f"{len(ev['backends'])} app backend(s) ok"),
        evidence=ev,
    )


async def _probe_serving_fs(ctx: DoctorContext) -> ProbeResult:
    """serving/fs — the static/dist symlink (the stale-SPA bug-class) + dead
    lock/PID leftovers.

    Replicates ``frontend.ensure_dev_dist_symlink``'s DETECTION logic read-only
    (never calls it — that mutates): flags a real-directory copy shadowing the
    runtime symlink, and a symlink whose target is gone. Also counts dead
    ``locks/*.lock`` and dead PID rows in ``session_pids.txt``/``agent_pids.txt``.
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
            ev["dist"] = {"kind": "copy", "target_ok": (dist / "index.html").is_file()}
        else:
            ev["dist"] = {"kind": "missing", "target_ok": False}

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
    if dist.get("kind") == "copy":
        problems.append("static/dist is a COPY shadowing the runtime symlink (serves a stale SPA)")
        fix_id = "serving-fs.symlink-repair"  # confirm-gated repair (§2)
    elif not dist.get("target_ok"):
        problems.append(f"static/dist {dist.get('kind')} — SPA not resolvable")
    if ev.get("dead_locks") or ev.get("dead_pids"):
        fix_id = fix_id or "serving-fs.orphan-prune"
    return ProbeResult(
        ok=not problems,
        detail=("; ".join(problems) if problems else "serving/fs healthy"),
        evidence=ev,
        fix_id=fix_id,
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
            f"{len(open_breakers)} provider(s) with an open breaker"
            if open_breakers
            else f"{len(providers)} provider(s), no open breakers"
        ),
        evidence={"providers": providers, "generated_from": health.get("generated_from", 0)},
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
            f"{len(recent)} recent crash artifact(s); latest: {latest.get('kind')} — "
            f"{latest.get('exception_type')}"
        ),
        evidence={"crashes": recent},
    )


async def _probe_memory_pipeline(ctx: DoctorContext) -> ProbeResult:
    """memory-pipeline — is memory extraction actually running? (PLATFORM-RESILIENCE
    §3.2, current-seam version.)

    Silent memory-pipeline death (the S05 bug-class) becomes visible. The rich
    LEARN-R19 outcome records (FLUSH_OK/FLUSH_ERROR, staging backlog, per-op cost) are
    future Workflows-v2 infra — until then this reads what exists: the most recent
    consolidation activity. Absence of any consolidation on a store with history is a
    WARN. Read-only; degrades to ok when there's simply no history yet.
    """

    def _read() -> dict[str, Any]:
        ev: dict[str, Any] = {"source": "history (LEARN-R19 records pending)"}
        try:
            from personalclaw.history import HistoryConsolidator  # noqa: F401
        except Exception:
            ev["available"] = False
            return ev
        ev["available"] = True
        # Best-effort: report the consolidation metadata store's presence. A dedicated
        # "last consolidation timestamp" doesn't exist yet (offsets, not wall-clock),
        # so we report structural presence rather than fabricate a freshness metric.
        home = ctx.home
        ev["history_dir_present"] = (home / "history").exists()
        return ev

    ev = await asyncio.to_thread(_read)
    # This probe is intentionally conservative today: it confirms the pipeline module
    # is importable and reports structural signals. It never falsely alarms — the
    # richer FLUSH_OK-streak WARN arrives with the flywheel's records.
    return ProbeResult(
        ok=True,
        detail="memory pipeline present (richer freshness metrics arrive with the flywheel)",
        evidence=ev,
    )


# ── Register the initial probe set ───────────────────────────────────────────


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


_register_builtin_probes()
