"""The ACP connections a user's chats share — never started ahead of the user.

A runtime whose dialect supports session interleaving can serve several chats on ONE
process: the first chat that needs the runtime spawns it and ``initialize``-s it, and each
later chat opens its own session on that connection (``open_session``). Every spawn here
therefore happens for a chat the user started.

This module used to also keep a WARMED connection per installed runtime — spawned at
gateway start, respawned by a health loop whenever it died, re-spawned every ten minutes to
refresh its catalog, and re-spawned behind every chat that took one. That started another
agent's CLI with no user action, which PersonalClaw never does, so it is gone: a chat
cold-starts its runtime, and a runtime's agent catalog comes from the user's Test
(``agents/runtime_tests.py``). Pre-spawning stays available as an explicit choice — the
session warm pool (``session.pool_size``, off by default).

What the pool's loop still does is release runner leases whose holder went quiet, because a
lease whose holder died with the gateway must be dropped by something that runs without the
holder. That sweep reads and deletes lease files; it starts no process.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.acp.session import AcpConnection
    from personalclaw.llm.base import ModelProvider

logger = logging.getLogger(__name__)

# How often the lease sweep runs — the same cadence the runner leases' readers assume.
_LEASE_SWEEP_INTERVAL_SECS = 60.0


class _Slot:
    """One runtime's shared connection, and the lock that keeps two chats from spawning two."""

    __slots__ = ("lock", "_shared_conn")

    def __init__(self) -> None:
        # Guards against two racing sessions spawning two processes for one runtime.
        self.lock: asyncio.Lock = asyncio.Lock()
        # The per-runtime SHARED AcpConnection for concurrent sessions, spawned lazily by
        # open_session for the first chat that needs it.
        self._shared_conn: "AcpConnection | None" = None


class AcpConnectionPool:
    """The per-runtime shared connections a user's concurrent chats open sessions on.

    ``start_sem`` bounds concurrent cold-starts (shared with the session manager so shared
    connections and one-session cold starts don't oversubscribe CPU).
    """

    def __init__(self, *, start_sem: asyncio.Semaphore) -> None:
        self._start_sem = start_sem
        self._slots: dict[str, _Slot] = {}
        self._lock = asyncio.Lock()  # guards _slots membership
        self._sweep_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._closed = False

    # ── slot access ────────────────────────────────────────────────────────

    async def _slot(self, runtime_id: str) -> _Slot:
        async with self._lock:
            slot = self._slots.get(runtime_id)
            if slot is None:
                slot = _Slot()
                self._slots[runtime_id] = slot
            return slot

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
        session_meta: dict | None = None,
        compacts_itself: bool = False,
    ) -> "ModelProvider | None":
        """Open a NEW session on a shared, per-runtime :class:`AcpConnection`, returning
        an :class:`AcpSessionProvider`. The connection is spawned + ``initialize``-d once
        and reused; multiple calls = concurrent sessions on ONE process (the P9 win).

        Called for a chat the user started, never ahead of one. Callers gate on
        ``acp_session_provider.concurrent_sessions_enabled(dialect)`` before calling.
        Returns ``None`` on failure (caller cold-starts a one-session provider instead)."""
        if self._closed:
            return None
        try:
            from personalclaw.llm.acp_session_provider import open_acp_session_provider

            conn = await self._shared_connection(
                runtime_id,
                cwd=cwd,
                command=command,
                dialect=dialect,
                sandbox_mode=sandbox_mode,
                extra_env=extra_env,
                session_key=session_key,
                channel_id=channel_id,
                session_meta=session_meta,
            )
            if conn is None:
                return None
            return await open_acp_session_provider(  # type: ignore[return-value]  # CI-2
                conn,
                runtime_id=runtime_id,
                cwd=cwd,
                session_files_dir=session_files_dir,
                model=model,
                agent_name=agent_name,
                session_key=session_key,
                mcp_servers=mcp_servers,
                compacts_itself=compacts_itself,
            )
        except Exception:
            logger.warning("acp pool: open_session failed for %s", runtime_id, exc_info=True)
            return None

    async def _shared_connection(
        self,
        runtime_id: str,
        *,
        cwd,
        command,
        dialect,
        sandbox_mode,
        extra_env,
        session_key,
        channel_id,
        session_meta=None,
    ):
        """Get-or-spawn the per-runtime shared AcpConnection (guarded by the slot lock so
        two racing sessions don't spawn two processes). Spawns + ``initialize`` once."""
        from personalclaw.acp.client import CLIENT_NAME, CLIENT_VERSION
        from personalclaw.acp.dialect import get_dialect
        from personalclaw.acp.session import AcpConnection

        slot = await self._slot(runtime_id)
        async with slot.lock:
            conn = slot._shared_conn
            if conn is not None and conn.is_process_alive():
                return conn
            async with self._start_sem:
                conn = await AcpConnection.spawn(
                    command=command,
                    work_dir=cwd,
                    dialect=get_dialect(dialect),
                    sandbox_mode=sandbox_mode,
                    extra_env=extra_env,
                    session_key=session_key,
                    channel_id=channel_id,
                    session_meta=session_meta,
                )
                await conn.initialize(
                    {
                        "protocolVersion": get_dialect(dialect).protocol_version(),
                        # Read the shared constant rather than repeating the literal: this
                        # copy was left at 0.1.2 through the 0.1.3 bump, so a pooled ACP
                        # connection would have announced a version the release did not
                        # have. One source, one place to bump.
                        "clientInfo": get_dialect(dialect).client_info(
                            client_name=CLIENT_NAME, client_version=CLIENT_VERSION
                        ),
                    }
                )
            slot._shared_conn = conn
            logger.info(
                "acp pool: spawned shared connection for %s (concurrent sessions)", runtime_id
            )
            return conn

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start_lease_sweep(self) -> None:
        """Begin the periodic release of idle runner leases. Starts no process."""
        if self._sweep_task is not None or self._closed:
            return
        self._sweep_task = asyncio.ensure_future(self._lease_sweep_loop())

    async def _lease_sweep_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(_LEASE_SWEEP_INTERVAL_SECS)
                # Idle-release: a lease whose holder died with the gateway must be dropped by
                # SOMETHING that runs without the holder. Readers already drop an expired
                # lease at render; this makes the on-disk state agree with what they show.
                await self._release_idle_leases()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("acp pool: lease sweep error", exc_info=True)

    async def _release_idle_leases(self) -> None:
        """Drop runner leases whose idle window elapsed. Off the loop; never raises.

        The sweep reads the catalog and up to one small file per runner, which is cheap but
        is still disk I/O inside the loop's tick, so it runs in the default executor.
        """
        try:
            from personalclaw.agents import runner_lifecycle

            released = await asyncio.get_running_loop().run_in_executor(
                None, runner_lifecycle.sweep_idle_leases
            )
            if released:
                logger.info("acp pool: idle-released %d runner lease(s)", len(released))
        except Exception:
            logger.debug("acp pool: idle-release sweep failed", exc_info=True)

    async def shutdown(self) -> None:
        """Stop the lease sweep and close every shared connection (kills the process)."""
        self._closed = True
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sweep_task = None
        async with self._lock:
            shared_conns = [
                s._shared_conn for s in self._slots.values() if s._shared_conn is not None
            ]
            for s in self._slots.values():
                s._shared_conn = None
        for c in shared_conns:
            try:
                await c.close()
            except Exception:
                logger.debug("acp pool: shared connection close failed", exc_info=True)


# ── process-wide singleton ────────────────────────────────────────────────────
# Set once at gateway startup (by the lifecycle wiring) and read by the session manager.
# None when no pool is active (e.g. CLI/tests) — callers treat that as "no pool" and
# cold-start a one-session provider.
_pool: AcpConnectionPool | None = None


def get_acp_pool() -> AcpConnectionPool | None:
    """Return the process-wide ACP connection pool, or ``None`` if not started."""
    return _pool


def set_acp_pool(pool: AcpConnectionPool | None) -> None:
    """Install (or clear) the process-wide ACP connection pool."""
    global _pool
    _pool = pool


async def init_acp_pool(start_sem: "asyncio.Semaphore") -> AcpConnectionPool:
    """Construct + install the process-wide pool and start its lease sweep.

    Starts NO agent CLI: a connection is spawned only for a chat the user starts
    (:meth:`AcpConnectionPool.open_session`). Replaces any existing pool (shutting it
    down first). Returns the pool."""
    if _pool is not None:
        await _pool.shutdown()
    pool = AcpConnectionPool(start_sem=start_sem)
    set_acp_pool(pool)
    pool.start_lease_sweep()
    return pool
