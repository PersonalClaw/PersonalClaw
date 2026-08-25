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
import sys
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
        return ProbeResult(ok=False, detail=f"staging log unreadable: {exc}", evidence={})

    if not ev.get("staging_log"):
        return ProbeResult(
            ok=True, detail="no staging log yet (nothing captured on this home)", evidence=ev
        )

    reasons: list[str] = []
    if ev["errors"]:
        reasons.append(f"{ev['errors']} flush error(s) in {ev['days']}d")
    if ev["all_ok_streak"] >= _MEMORY_OK_STREAK_WARN and ev["passes"] and not ev["produced"]:
        reasons.append(
            f"{ev['all_ok_streak']} consecutive flush_ok passes and nothing produced in "
            f"{ev['days']}d"
        )
    if ev["staging_backlog"] >= _MEMORY_BACKLOG_WARN:
        reasons.append(f"{ev['staging_backlog']} staged entries unconsumed (drain not running)")
    if reasons:
        return ProbeResult(ok=False, detail="; ".join(reasons), evidence=ev)
    return ProbeResult(
        ok=True,
        detail=(
            f"{ev['passes']} pass(es) in {ev['days']}d, {ev['produced']} produced, "
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

    So the Doctor runs it on the actual home. Read-only: `audit_home` only stats and globs. Reports
    **degraded, not failed** — unclaimed state is a backup-coverage gap the user should see and act
    on, not a reason to declare the install broken, and failing hard here would make an unrelated
    new file look like an outage.
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
        return ProbeResult(ok=False, detail=f"inventory audit failed: {exc}", evidence={})

    gaps = ev["unclaimed_count"] + ev["undeclared_db_count"]
    if not gaps:
        return ProbeResult(ok=True, detail=f"all {ev['claimed']} state paths claimed", evidence=ev)
    return ProbeResult(
        ok=False,
        detail=(
            f"{ev['unclaimed_count']} unclaimed path(s) and {ev['undeclared_db_count']} "
            "undeclared database(s) — these are in NO snapshot"
        ),
        evidence=ev,
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
    * **local-only** → ok. Normal local install, no tailnet — informational: see
      remote-access.md to reach it from a phone.

    CAPABILITY tier: a missing tailnet is not a failure and must never gate the
    core ladder.
    """
    from personalclaw.dashboard.origin import (
        auth_is_off,
        is_local_bind,
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
        return ProbeResult(ok=False, detail=f"vector index probe failed: {exc}", evidence={})

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
                f"chunk ANN index active but out of step at {len(stale)} dimension(s) "
                f"({', '.join(stale)}) — the next search rebuilds it"
            ),
            evidence=ev,
        )
    return ProbeResult(
        ok=True,
        detail=f"chunk ANN index active ({indexed_total} chunk vector(s) indexed)",
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
    )


async def _probe_credential_backend(_ctx: DoctorContext) -> ProbeResult:
    """security — which credential store is actually holding the secrets? (SH-1)

    Reports the RESOLVED backend, not the requested one. The distinction is the whole
    point: an install that sets ``PERSONALCLAW_CREDENTIAL_BACKEND=keychain`` on a headless
    box with no secret service keeps its credentials in ``.env`` at 0600, and a doctor line
    echoing the *request* would tell that user their secrets are in a keychain that does
    not exist. ``ok=False`` for exactly that mismatch — nothing was lost and nothing landed
    in a weaker location, but the operator asked for something they did not get.

    Also reports the ``.env`` mode when dotenv is the active backend, because 0600 is the
    floor the fallback promises. Read-only: the probe never repairs the mode (the next
    ``load_credentials()`` does) and never reads a secret VALUE — only names, modes, states.
    """
    from personalclaw.config.loader import (
        credential_backend,
        credential_backend_warning,
        env_path,
        keychain_available,
        requested_credential_backend,
    )

    def _facts() -> dict[str, Any]:
        ep = env_path()
        mode = ""
        with contextlib.suppress(OSError):
            if ep.exists():
                mode = format(ep.stat().st_mode & 0o777, "04o")
        return {
            "backend": credential_backend(),
            "requested": requested_credential_backend(),
            "keychain_available": keychain_available(),
            "warning": credential_backend_warning(),
            "env_mode": mode,
        }

    facts = await asyncio.to_thread(_facts)
    evidence = {k: v for k, v in facts.items() if k != "warning"}

    if facts["warning"]:
        return ProbeResult(ok=False, detail=facts["warning"], evidence=evidence)

    if facts["backend"] == "keychain":
        return ProbeResult(
            ok=True, detail="credentials stored in the OS keychain (keyring)", evidence=evidence
        )

    mode = facts["env_mode"]
    if mode and int(mode, 8) & 0o077:
        return ProbeResult(
            ok=False,
            detail=(
                f"credential file .env is mode {mode} — group/world readable; "
                "it is repaired to 0600 on the next credential read"
            ),
            evidence=evidence,
        )
    return ProbeResult(
        ok=True,
        detail=f"credentials stored in .env at mode {mode or '0600'}",
        evidence=evidence,
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
        return ProbeResult(ok=False, detail=f"knowledge vault probe failed: {exc}", evidence={})

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
                f"{ev.get('projected')} page(s) projected; {waiting} waiting on you "
                f"({ev.get('conflicts')} changed on both sides, "
                f"{ev.get('owner_deleted')} deleted here but still in the library)"
            ),
            evidence=ev,
        )
    return ProbeResult(
        ok=True, detail=f"{ev.get('projected')} page(s) projected, none in conflict", evidence=ev
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
        )
    return ProbeResult(
        ok=True,
        detail=(
            f"{unenforced} No pids or RSS ceiling is configured, so nothing is being "
            "silently dropped."
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


_register_builtin_probes()
