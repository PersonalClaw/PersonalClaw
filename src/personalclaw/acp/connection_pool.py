"""Live ACP connection pool — one warmed connection per ready ``acp:<cli>`` runtime.

The lists (agents/models/modes) and the FIRST chat turn of an ACP runtime are both
expensive: each needs a live process that has completed ``initialize`` +
``session/new`` (an ``npx`` fetch + MCP load, ~15-20s cold). Discovery alone threw
that connection away after reading the snapshot, so the first chat still paid the
cold start. This pool keeps ONE live connection per ready runtime, warmed at
startup, that serves BOTH:

* the **discovery snapshot** (``session_snapshot`` → modes/models/configOptions),
  read without a second spawn; and
* the **first chat turn** — ``claim()`` hands the live connection to a session and
  a replacement is re-warmed in the background, so the next ACP chat is instant too.

Design notes:
* Keyed by runtime id (``acp:<cli>``). At most one live connection per runtime.
* Warming is gated on the same readiness the discovery layer uses (a runtime whose
  delegate engine is absent — e.g. codex ``not_found`` — is never pooled).
* The pool is vendor-neutral: it builds providers through the injected
  ``provider_builder`` (the model registry's ``acp_agent`` factory) and never
  names a CLI. Per-session specialization (default-dialect ``set_mode`` persona, claude
  ``set_config_option`` model/mode/effort) happens on the CLAIM side
  (``SessionManager.get_or_create``), not here — the pooled connection is generic.
* Respawn-on-death: a periodic health check re-warms a dead/empty slot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from personalclaw.llm.base import ModelProvider

logger = logging.getLogger(__name__)

# How long a cached snapshot is served before a re-warm refreshes it (the default
# dialect's agent catalog is account-dynamic, so the live connection's snapshot is
# periodically refreshed by re-warming).
_SNAPSHOT_TTL_SECS = 600.0
# Health-check cadence — respawn dead/empty slots.
_HEALTH_INTERVAL_SECS = 60.0
# Backoff for a runtime that keeps failing to warm. Without it, the health loop
# re-warms a permanently-broken runtime (e.g. a delegate CLI that can't start on
# this machine) every interval forever — each failed spawn leaves npx/CLI
# descendant process trees behind, so hundreds accumulate over a session. Skip a
# failing runtime for an exponentially-growing window (capped), reset on success.
_WARM_BACKOFF_BASE_SECS = 60.0
_WARM_BACKOFF_MAX_SECS = 1800.0  # 30 min ceiling


class _Slot:
    """One runtime's pooled connection + its discovery snapshot."""

    __slots__ = (
        "provider", "snapshot", "warmed_at", "warming", "fail_count", "next_retry_at",
        "_shared_conn",
    )

    def __init__(self) -> None:
        self.provider: ModelProvider | None = None
        self.snapshot: dict = {}
        self.warmed_at: float = 0.0
        # Guards against concurrent warms of the same runtime.
        self.warming: asyncio.Lock = asyncio.Lock()
        # Consecutive warm failures + the monotonic time before which the health
        # loop should not retry (exponential backoff). Reset to 0 on success.
        self.fail_count: int = 0
        self.next_retry_at: float = 0.0
        # The per-runtime SHARED AcpConnection for concurrent sessions (P9), spawned
        # lazily by open_session. Distinct from ``provider`` (the one-session warm slot).
        self._shared_conn = None


class AcpConnectionPool:
    """One live ACP connection per ready runtime; serves discovery + first turn.

    ``provider_builder(runtime_id) -> ModelProvider`` constructs a GENERIC provider
    for the runtime (entry defaults — no per-session agent/model). ``start_sem``
    bounds concurrent cold-starts (shared with the session manager so warming and
    user cold-starts don't oversubscribe CPU). ``readiness_check(runtime_id)``
    returns whether the runtime is usable right now (the discovery readiness gate);
    a not-ready runtime is skipped.
    """

    def __init__(
        self,
        *,
        provider_builder: Callable[[str], "ModelProvider"],
        start_sem: asyncio.Semaphore,
        readiness_check: Callable[[str], Awaitable[bool]] | None = None,
        snapshot_ttl_secs: float = _SNAPSHOT_TTL_SECS,
    ) -> None:
        self._build = provider_builder
        self._start_sem = start_sem
        self._readiness_check = readiness_check
        self._ttl = snapshot_ttl_secs
        self._slots: dict[str, _Slot] = {}
        self._lock = asyncio.Lock()  # guards _slots membership
        self._runtimes: set[str] = set()  # runtimes the pool manages
        self._health_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._closed = False

    # ── slot access ────────────────────────────────────────────────────────

    async def _slot(self, runtime_id: str) -> _Slot:
        async with self._lock:
            slot = self._slots.get(runtime_id)
            if slot is None:
                slot = _Slot()
                self._slots[runtime_id] = slot
            self._runtimes.add(runtime_id)
            return slot

    # ── warming ──────────────────────────────────────────────────────────────

    async def warm(self, runtime_id: str) -> bool:
        """Ensure a live connection exists for *runtime_id*. Returns True if one is
        live (already or newly warmed). Gated on readiness; never raises."""
        if self._closed:
            return False
        if self._readiness_check is not None:
            try:
                if not await self._readiness_check(runtime_id):
                    logger.debug("acp pool: %s not ready — not warming", runtime_id)
                    return False
            except Exception:
                logger.debug("acp pool: readiness check failed for %s", runtime_id, exc_info=True)
                return False

        slot = await self._slot(runtime_id)
        async with slot.warming:
            if slot.provider is not None and _alive(slot.provider):
                return True
            provider = None
            try:
                provider = self._build(runtime_id)
                async with self._start_sem:
                    await provider.start()
                slot.provider = provider
                slot.snapshot = dict(getattr(provider, "session_snapshot", {}) or {})
                slot.warmed_at = time.monotonic()
                slot.fail_count = 0  # recovered — clear backoff
                slot.next_retry_at = 0.0
                provider = None  # owned by the slot now
                logger.info("acp pool: warmed %s (agents in snapshot: %s)", runtime_id,
                            _snapshot_agent_count(slot.snapshot))
                return True
            except Exception:
                # Exponential backoff so a permanently-broken runtime isn't
                # re-spawned every health interval (the source of npx/CLI process
                # leakage). next_retry_at gates the health loop; an explicit warm
                # (claim/invalidate) still proceeds.
                slot.fail_count += 1
                delay = min(_WARM_BACKOFF_BASE_SECS * (2 ** (slot.fail_count - 1)),
                            _WARM_BACKOFF_MAX_SECS)
                slot.next_retry_at = time.monotonic() + delay
                logger.warning("acp pool: failed to warm %s (attempt %d) — backing off %.0fs",
                               runtime_id, slot.fail_count, delay, exc_info=True)
                return False
            finally:
                if provider is not None:
                    await _safe_shutdown(provider)

    async def warm_all(self, runtime_ids: list[str]) -> int:
        """Warm every given runtime in parallel. Returns the count now live."""
        results = await asyncio.gather(
            *(self.warm(r) for r in runtime_ids), return_exceptions=True
        )
        return sum(1 for r in results if r is True)

    # ── discovery snapshot ─────────────────────────────────────────────────────

    def snapshot(self, runtime_id: str) -> dict | None:
        """Return the live connection's ``session/new`` snapshot if fresh, else
        ``None`` (caller falls back to a throwaway probe). Does not spawn."""
        slot = self._slots.get(runtime_id)
        if (
            slot is not None
            and slot.provider is not None
            and slot.snapshot
            and _alive(slot.provider)
            and (time.monotonic() - slot.warmed_at) < self._ttl
        ):
            return dict(slot.snapshot)
        return None

    def is_warmed(self, runtime_id: str) -> bool:
        """True when a live, alive connection is held for *runtime_id*.

        A warmed connection completed ``initialize`` + ``session/new``, so the
        runtime is provably ready WITHOUT a fresh probe — this lets
        ``/api/agent-providers`` answer readiness instantly instead of re-spawning
        a handshake per runtime on every call."""
        slot = self._slots.get(runtime_id)
        return slot is not None and slot.provider is not None and _alive(slot.provider)

    # ── concurrent sessions (P9): one shared AcpConnection, N sessions ──────────

    async def open_session(
        self,
        runtime_id: str,
        *,
        cwd,
        command: list[str],
        dialect: str | None,
        session_files_dir=None,
        sandbox_mode: str = "auto",
        extra_env: dict | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
        model: str = "",
        agent_name: str = "",
        mcp_servers: list | None = None,
    ) -> "ModelProvider | None":
        """Open a NEW session on a shared, per-runtime :class:`AcpConnection`, returning
        an :class:`AcpSessionProvider`. The connection is spawned + ``initialize``-d once
        and reused; multiple calls = concurrent sessions on ONE process (the P9 win).

        This is the concurrent path, distinct from :meth:`claim` (which hands out a whole
        one-session-per-process connection). Callers gate on
        ``acp_session_provider.concurrent_sessions_enabled(dialect)`` before calling.
        Returns ``None`` on failure (caller cold-starts a one-session provider instead)."""
        if self._closed:
            return None
        try:
            from personalclaw.llm.acp_session_provider import open_acp_session_provider

            conn = await self._shared_connection(
                runtime_id, cwd=cwd, command=command, dialect=dialect,
                sandbox_mode=sandbox_mode, extra_env=extra_env,
                session_key=session_key, channel_id=channel_id,
            )
            if conn is None:
                return None
            return await open_acp_session_provider(
                conn, runtime_id=runtime_id, cwd=cwd, session_files_dir=session_files_dir,
                model=model, agent_name=agent_name, mcp_servers=mcp_servers,
            )
        except Exception:
            logger.warning("acp pool: open_session failed for %s", runtime_id, exc_info=True)
            return None

    async def _shared_connection(
        self, runtime_id: str, *, cwd, command, dialect, sandbox_mode, extra_env,
        session_key, channel_id,
    ):
        """Get-or-spawn the per-runtime shared AcpConnection (guarded by the slot lock so
        two racing sessions don't spawn two processes). Spawns + ``initialize`` once."""
        from personalclaw.acp.dialect import get_dialect
        from personalclaw.acp.session import AcpConnection

        slot = await self._slot(runtime_id)
        async with slot.warming:
            conn = getattr(slot, "_shared_conn", None)
            if conn is not None and conn.is_process_alive():
                return conn
            async with self._start_sem:
                conn = await AcpConnection.spawn(
                    command=command, work_dir=cwd, dialect=get_dialect(dialect),
                    sandbox_mode=sandbox_mode, extra_env=extra_env,
                    session_key=session_key, channel_id=channel_id,
                )
                await conn.initialize({
                    "protocolVersion": get_dialect(dialect).protocol_version(),
                    "clientInfo": get_dialect(dialect).client_info(
                        client_name="personalclaw", client_version="0.1.2"),
                })
            slot._shared_conn = conn  # type: ignore[attr-defined]
            logger.info("acp pool: spawned shared connection for %s (concurrent sessions)", runtime_id)
            return conn

    # ── claim (first chat turn) ────────────────────────────────────────────────

    async def claim(self, runtime_id: str) -> "ModelProvider | None":
        """Hand the live connection to a session (it leaves the pool) and re-warm a
        replacement in the background. Returns ``None`` if none is available — the
        caller then cold-starts normally."""
        if self._closed:
            return None
        slot = self._slots.get(runtime_id)
        if slot is None or slot.provider is None or not _alive(slot.provider):
            return None
        async with self._lock:
            provider = slot.provider
            if provider is None or not _alive(provider):
                return None
            # Detach from the pool — this connection is now a user session.
            slot.provider = None
            slot.snapshot = {}
            slot.warmed_at = 0.0
        logger.info("acp pool: claimed live connection for %s", runtime_id)
        # Re-warm a replacement so the next ACP chat is instant too.
        self._spawn_bg(self.warm(runtime_id))
        return provider

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start_health_loop(self) -> None:
        """Begin periodic respawn of dead/empty slots for managed runtimes."""
        if self._health_task is not None or self._closed:
            return
        self._health_task = asyncio.ensure_future(self._health_loop())

    async def _health_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(_HEALTH_INTERVAL_SECS)
                now = time.monotonic()
                for runtime_id in list(self._runtimes):
                    slot = self._slots.get(runtime_id)
                    if slot is None or slot.provider is None or not _alive(slot.provider):
                        # Respect warm backoff: a runtime that keeps failing is
                        # skipped until its retry window opens, so a broken
                        # delegate CLI can't be re-spawned every interval.
                        if slot is not None and slot.next_retry_at > now:
                            continue
                        await self.warm(runtime_id)
                    elif (now - slot.warmed_at) >= self._ttl:
                        # Refresh a stale snapshot by re-warming (dynamic catalog).
                        await self._refresh(runtime_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("acp pool: health loop error", exc_info=True)

    async def _refresh(self, runtime_id: str) -> None:
        """Re-warm a runtime, shutting down the prior live connection first."""
        old = None
        slot = self._slots.get(runtime_id)
        if slot is not None:
            async with self._lock:
                old = slot.provider
                slot.provider = None
                slot.snapshot = {}
        if old is not None:
            await _safe_shutdown(old)
        await self.warm(runtime_id)

    async def invalidate(self, runtime_id: str) -> None:
        """Drop a runtime's pooled connection (enable/disable/auth change)."""
        slot = self._slots.get(runtime_id)
        if slot is None:
            return
        async with self._lock:
            old = slot.provider
            slot.provider = None
            slot.snapshot = {}
            slot.warmed_at = 0.0
            # An explicit invalidate (enable/disable/auth change) means state
            # changed — clear backoff so the next warm retries immediately.
            slot.fail_count = 0
            slot.next_retry_at = 0.0
        if old is not None:
            await _safe_shutdown(old)

    async def shutdown(self) -> None:
        """Stop the health loop and shut down all pooled connections."""
        self._closed = True
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
            self._health_task = None
        async with self._lock:
            providers = [s.provider for s in self._slots.values() if s.provider is not None]
            shared_conns = [s._shared_conn for s in self._slots.values() if s._shared_conn is not None]
            for s in self._slots.values():
                s.provider = None
                s.snapshot = {}
                s._shared_conn = None
        for p in providers:
            await _safe_shutdown(p)
        for c in shared_conns:  # close shared concurrent-session connections (kills the process)
            try:
                await c.close()
            except Exception:
                logger.debug("acp pool: shared connection close failed", exc_info=True)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _spawn_bg(self, coro: Awaitable) -> None:
        async def _runner() -> None:
            try:
                await coro
            except Exception:
                logger.debug("acp pool: background task failed", exc_info=True)

        asyncio.ensure_future(_runner())


# ── process-wide singleton ────────────────────────────────────────────────────
# Set once at gateway startup (by the lifecycle wiring) and read by the discovery
# API + session manager. None when no pool is active (e.g. CLI/tests) — callers
# treat that as "no pool" and fall back to throwaway-probe / cold-start.
_pool: AcpConnectionPool | None = None


def get_acp_pool() -> AcpConnectionPool | None:
    """Return the process-wide ACP connection pool, or ``None`` if not started."""
    return _pool


def set_acp_pool(pool: AcpConnectionPool | None) -> None:
    """Install (or clear) the process-wide ACP connection pool."""
    global _pool
    _pool = pool


def _ready_acp_runtime_ids() -> list[str]:
    """All registered ``acp:<cli>`` runtime ids (readiness gated per-warm)."""
    try:
        from personalclaw.llm.registry import get_default_registry

        return [e.name for e in get_default_registry().list_entries() if e.type == "acp_agent"]
    except Exception:
        logger.debug("acp pool: failed to enumerate runtimes", exc_info=True)
        return []


def _build_acp_provider(runtime_id: str) -> "ModelProvider":
    """Build a GENERIC ACP provider for a runtime — NO agent/model bound. The pool
    warms it bare and specialization (persona + model) happens on claim.

    Critically the warm does NOT push the entry's curated default model (e.g.
    claude's ``claude-opus-4-8``), because that catalog id is NOT a value the live
    ``session/set_config_option`` accepts (the adapter only takes
    ``default``/``opus``/``sonnet``) — pushing it during init errored. Warm with
    ``model="auto"`` so the dialect skips the model verb and the adapter uses its
    own default; the real model is applied live on claim via ``set_model``."""
    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    entry = registry.get_entry(runtime_id)
    config = dict(entry.options or {})
    config["model"] = "auto"  # bare warm — no curated default pushed at init
    return registry.build(runtime_id, **config)


async def _acp_runtime_ready(runtime_id: str) -> bool:
    """Readiness gate for warming (mirrors the discovery readiness check)."""
    try:
        from personalclaw.agents.registry import get_agent_provider_class
        from personalclaw.llm.registry import get_default_registry

        entry = get_default_registry().get_entry(runtime_id)
        cls = get_agent_provider_class("acp")
        if cls is None:
            return False
        status = await cls.probe_readiness(dict(entry.options or {}))
        return bool(status.ready)
    except Exception:
        logger.debug("acp pool: readiness check failed for %s", runtime_id, exc_info=True)
        return False


async def init_acp_pool(start_sem: "asyncio.Semaphore") -> AcpConnectionPool:
    """Construct + install the process-wide pool, warm all ready ACP runtimes in
    the background, and start the health/respawn loop. Idempotent-ish: replaces
    any existing pool (shutting it down first). Returns the pool."""
    if _pool is not None:
        await _pool.shutdown()
    pool = AcpConnectionPool(
        provider_builder=_build_acp_provider,
        start_sem=start_sem,
        readiness_check=_acp_runtime_ready,
    )
    set_acp_pool(pool)
    runtimes = _ready_acp_runtime_ids()
    if runtimes:
        logger.info("acp pool: warming %d runtime(s) at startup", len(runtimes))
        # Background — each warm is a ~15-20s live session; don't block startup.
        pool._spawn_bg(pool.warm_all(runtimes))
    pool.start_health_loop()
    return pool


def _alive(provider: "ModelProvider") -> bool:
    try:
        fn = getattr(provider, "is_process_alive", None)
        if callable(fn):
            return bool(fn())
        return bool(getattr(provider, "is_alive", lambda: True)())
    except Exception:
        return False


async def _safe_shutdown(provider: "ModelProvider") -> None:
    try:
        await provider.shutdown()
    except Exception:
        try:
            from personalclaw.session_pid import _sync_kill_provider

            _sync_kill_provider(provider)
        except Exception:
            logger.debug("acp pool: shutdown failed", exc_info=True)


def _snapshot_agent_count(snapshot: dict) -> int:
    modes = (snapshot.get("modes") or {}).get("availableModes", []) if isinstance(snapshot, dict) else []
    return len(modes) if isinstance(modes, list) else 0
