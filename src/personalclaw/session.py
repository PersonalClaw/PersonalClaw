"""Session manager — maps channel thread_ts to LLM provider sessions.

Each channel thread gets its own ModelProvider instance. Sessions are
cleaned up after idle timeout (default 30 min).

Warm session pool: ``start_pool()`` pre-spawns ACP agent processes so
``get_or_create()`` returns instantly.  After handing out a warm session,
a replacement is created in the background to maintain the target count.

Background session: ``BACKGROUND_KEY`` is a persistent shared session for
lightweight background work (cron, heartbeat, lesson extraction).  It
stays alive between uses, serialized by the per-session semaphore.

At >= 90% context usage, fires a background task that sends /compact
to the ACP agent (which natively summarizes older turns), then resets the
session. The user's response is never blocked — compaction is fully
fire-and-forget.

Circuit breaker: after 5 consecutive failures on a session, the session
is force-reset instead of retrying forever.

Per-session semaphore: serializes prompts on the same session key so
concurrent channel messages on the same thread don't interleave.

Process Sweep Architecture
--------------------------
Four mechanisms clean up processes. They are complementary — not redundant.

1. ``cleanup_orphaned_sessions()`` — **startup + shutdown only**.
   Reads ``session_pids.txt`` (bare sandbox root PIDs from the previous
   gateway run). Validates each with ``_is_managed_agent_process``, kills descendants
   bottom-up, then kills the root. Truncates the file afterward.
   *Cannot be replaced by the periodic sweep* because sandbox roots are
   independent processes with no idle timeout — they survive indefinitely
   unless explicitly killed.

2. ``_cleanup_orphaned_mcp_servers()`` — **periodic** (every ~5 min).
   Reads ``agent_pids.txt`` (child:parent pairs). Kills children whose parent
   is confirmed dead. PPid-based reuse guard prevents killing recycled PIDs.
   Also prunes dead bare PIDs. *Depends on (1)* — children are only orphaned
   after their sandbox root is killed.

3. ``_expire_idle()`` — **periodic** (every ~5 min).
   Kills sessions idle for >``timeout_secs`` (default 30 min) via
   ``reset()`` → ``provider.shutdown()`` → SIGKILL process tree.
   Protected keys: ``_PERSISTENT_KEYS`` (``_bg`` only).
   **Known limitation**: ``last_used`` is only bumped on ``get_or_create()``,
   not on every LLM round-trip. A task runner step doing continuous work for
   >30 min without a new ``get_or_create()`` call could be swept. This is
   accepted for now to prevent runaway tasks, but may need a heartbeat or
   persistent-key mechanism if longer steps become common.

"""

import asyncio
import logging
import os
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from personalclaw import shutdown_event
from personalclaw.config import AppConfig
from personalclaw.config.loader import default_workspace_dir
from personalclaw.llm.base import CancelOutcome, ModelProvider
from personalclaw.session_map import SessionMap as SessionMap  # noqa: F401
from personalclaw.session_pid import (
    _cleanup_orphaned_mcp_servers,
    _collect_active_pids,
    _kill_confirmed_and_writeback,
    _periodic_pid_sweep,
    _sync_kill_provider,
)
from personalclaw.session_pid import _track_child_pids as _track_child_pids  # noqa: F401
from personalclaw.session_pid import _track_pid as _track_pid  # noqa: F401
from personalclaw.session_pid import _track_session_pid as _track_session_pid  # noqa: F401
from personalclaw.session_pid import _untrack_child_pids as _untrack_child_pids  # noqa: F401
from personalclaw.session_pid import _untrack_pid as _untrack_pid  # noqa: F401
from personalclaw.session_pid import _untrack_session_pid as _untrack_session_pid  # noqa: F401
from personalclaw.session_pid import (  # noqa: F401
    cleanup_orphaned_sessions as cleanup_orphaned_sessions,
)
from personalclaw.stats import Stats

logger = logging.getLogger(__name__)

# "No model configured yet" is signalled by ProviderResolutionError. Two distinct
# classes carry it (the bridge's and the LLM registry's); catch both so the
# background-session deferral is robust to whichever resolver raised. Imported at
# module load (provider_bridge does not import session, so no cycle).
try:
    from personalclaw.llm.registry import ProviderResolutionError as _LLMResolutionError
    from personalclaw.providers.provider_bridge import (
        ProviderResolutionError as _BridgeResolutionError,
    )

    _PROVIDER_RESOLUTION_ERRORS: tuple[type[Exception], ...] = (
        _BridgeResolutionError,
        _LLMResolutionError,
    )
except Exception:  # pragma: no cover - defensive: never block module import
    _PROVIDER_RESOLUTION_ERRORS = ()

_MAX_POOL = 10

_SUBAGENT_PREFIX = "subagent:"
_CHANNEL_PREFIX = "channel:"
# Goal loop worker sessions are HEADLESS (no UI tab) and long-running — a single
# cycle can fan out to subagents and run well past the idle-sweep window. They
# are NOT dashboard chat tabs, so the "tab closed → orphan" heuristic must never
# reap them; their lifecycle is owned by the loop watchdog (unresponsive /
# loop-exhaustion), not the idle sweep. Reaping one mid-turn kills the in-flight
# cycle (and its subagents) before it can record a finding.
_LOOP_WORKER_PREFIX = "dashboard:loop-"

# Session key prefixes that are stateless (reset after each use) — skip resume
_STATELESS_PREFIXES = ("cron:", _SUBAGENT_PREFIX, _CHANNEL_PREFIX, "inbox:", "side:")

# Background session key — cron, heartbeat, lessons share this session
BACKGROUND_KEY = "_bg"


# Context usage thresholds
_CONTEXT_WARN_PCT = 70.0
_CONTEXT_COMPACT_PCT = 80.0

# Circuit breaker: force-reset after this many consecutive failures
_CIRCUIT_BREAKER_THRESHOLD = 5

# Background session recycle thresholds (more aggressive than chat compaction)
_BG_RECYCLE_PCT = 70.0  # recycle at 70% — well before overflow
_BG_BLIND_RECYCLE_PROMPTS = 40  # recycle after 40 prompts if no metadata

# Persistent session keys — never expired by idle cleanup
_PERSISTENT_KEYS = frozenset({BACKGROUND_KEY})

# Type alias for provider factory — accepts optional session key
ProviderFactory = Callable[..., ModelProvider]

StopOutcome = Literal["soft", "hard", "idle"]


@dataclass
class _Session:
    provider: ModelProvider
    last_used: float = field(default_factory=time.monotonic)
    is_new: bool = True
    prompt_count: int = 0
    consecutive_failures: int = 0
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    approval_policy: str = ""  # "" (interactive) | "auto" (auto-approve all tools)
    agent: str = ""  # ACP agent name used for this session
    # Channel message queue: FIFO of (msg_ts, text, kwargs) waiting for the semaphore
    queue: deque[tuple[str, str, dict]] = field(default_factory=deque)
    # Queue-steering (#37): mid-turn messages in `steer` mode buffer here and the
    # native loop drains them at the next model boundary (vs `queue` = followup).
    steers: deque[str] = field(default_factory=deque)
    # Set when this session's last turn was cancelled via soft-stop.
    # ACP agent discards cancelled turns from its conversation log, so callers
    # must re-inject the cancelled turn (user prompt + partial assistant) as a
    # preamble on the next prompt. One-shot: consumers clear after use.
    prev_turn_cancelled: bool = False
    # Set of msg_ts values cancelled (message deleted while processing)
    cancelled: set[str] = field(default_factory=set)


class SessionManager:
    """Thread-keyed LLM provider pool with warm session pre-spawning."""

    def has_session(self, key: str) -> bool:
        """Return ``True`` if an active session exists for *key*."""
        return key in self._sessions

    def get_provider(self, key: str) -> ModelProvider | None:
        """Return the LLM provider for *key*, or ``None``."""
        sess = self._sessions.get(key)
        return sess.provider if sess else None

    def mark_new(self, key: str) -> None:
        """Force the next turn for *key* to be treated as a NEW conversation.

        Used after an ACP session is reset to a fresh ``session/new`` (which
        wipes the agent's conversation context) so the chat runner re-injects
        the agent system prompt + context onto the fresh session instead of
        sending a bare follow-up the contextless agent can't act on.
        """
        sess = self._sessions.get(key)
        if sess is not None:
            sess.is_new = True

    def get_pid(self, key: str) -> int | None:
        """Return the ACP agent PID for a session, or None."""
        sess = self._sessions.get(key)
        if not sess:
            return None
        try:
            return sess.provider.client._pid  # type: ignore[attr-defined]
        except AttributeError:
            return None

    def __init__(
        self,
        cfg: AppConfig,
        provider_factory: ProviderFactory | None = None,
    ):
        self._cfg = cfg
        self._provider_factory = provider_factory
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()
        self._start_sem = asyncio.Semaphore(4)  # max 4 concurrent cold-starts
        self._cleanup_task: asyncio.Task | None = None
        self._compacting: set[str] = set()
        self._background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]
        self._on_compacted: Callable[[str, float], Awaitable[None]] | None = None
        # Fired with the session key just before an idle session is reset,
        # so a consumer (the consolidator) can extract skills from the ending
        # session. Wired at gateway startup to consolidator.consolidate_session.
        self._on_session_expire: Callable[[str], Awaitable[object]] | None = None
        self._pool_started = False
        self._session_map = SessionMap()
        self._active_dashboard_sessions: set[str] | None = (
            None  # None = uninitialized; empty set = all tabs closed
        )

        # ── Warm Pool ──
        self._pool_size: int = min(_MAX_POOL, max(0, cfg.session.pool_size))
        if cfg.session.pool_size > _MAX_POOL:
            logger.warning(
                "pool_size %d exceeds max %d, clamping", cfg.session.pool_size, _MAX_POOL
            )
        self._pool_agent: str = cfg.session.pool_agent or cfg.default_agent
        self._pool_ttl_secs: int = max(0, cfg.session.pool_ttl_secs)
        # Default cwd used by pool processes — matches the workspace-dir
        # fallback in chat_handlers so sessions that didn't pick an explicit
        # project can still claim from the pool.
        self._pool_cwd: str = default_workspace_dir()
        # Queue stores (provider, spawn_time) tuples for TTL tracking
        self._warm_pool: asyncio.Queue[tuple[ModelProvider, float]] = asyncio.Queue()
        self._pool_fill_lock = asyncio.Lock()
        self._pool_health_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._pool_sweep_pids: set[int] = set()  # PIDs temporarily out of queue during health sweep

    async def reload_provider_factory(self) -> None:
        """Reload provider factory from current config (after provider switch)."""
        cfg = AppConfig.load()
        stale: list[tuple[str, Any]] = []
        async with self._pool_fill_lock:
            async with self._lock:
                self._cfg = cfg
                self._provider_factory = cfg.create_provider_factory()
                self._pool_size = min(_MAX_POOL, max(0, cfg.session.pool_size))
                self._pool_agent = cfg.session.pool_agent or cfg.default_agent
                self._pool_cwd = default_workspace_dir()
                # Drain warm pool
                while not self._warm_pool.empty():
                    try:
                        provider, _ = self._warm_pool.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    try:
                        await provider.shutdown()
                    except Exception:
                        logger.debug("Failed to shut down stale pool provider", exc_info=True)
                # Clear all existing sessions (they use the old provider)
                stale = list(self._sessions.items())
                self._sessions.clear()
        # Shut down old sessions outside locks to avoid blocking
        for key, sess in stale:
            try:
                await sess.provider.shutdown()
            except Exception:
                logger.debug(
                    "Failed to shut down session %s on provider switch", key, exc_info=True
                )
        # Reset pool state so start_pool() actually refills
        self._pool_started = False
        if self._pool_health_task and not self._pool_health_task.done():
            self._pool_health_task.cancel()
            self._pool_health_task = None
        await self.start_pool(blocking=False)
        logger.info(
            "Provider factory reloaded: provider=%s, cleared %d sessions",
            cfg.agent.provider,
            len(stale),
        )

    # ── Background Session ──

    async def start_pool(self, *, blocking: bool = True) -> None:
        """Create the background session for cron/heartbeat.

        Chat sessions cold-start on first message via get_or_create().
        """
        if self._pool_started or not self._provider_factory:
            return

        # Prune stale session map entries on startup
        self._session_map.prune()
        self._pool_started = True

        if not blocking:

            async def _start_bg_and_pool() -> None:
                await self._ensure_background()
                await self._fill_warm_pool()
                if self._pool_size:
                    self._pool_health_task = asyncio.create_task(self._pool_health_loop())
                    self._background_tasks.add(self._pool_health_task)
                    self._pool_health_task.add_done_callback(self._background_tasks.discard)

            t = asyncio.create_task(_start_bg_and_pool())
            self._background_tasks.add(t)
            t.add_done_callback(self._background_tasks.discard)
            logger.info("Background session starting (non-blocking)")
            return

        await self._ensure_background()
        logger.info("Background session ready")

        # Fill warm pool after background session is ready
        if self._pool_size:
            t = asyncio.create_task(self._fill_warm_pool())
            self._background_tasks.add(t)
            t.add_done_callback(self._background_tasks.discard)
            self._pool_health_task = asyncio.create_task(self._pool_health_loop())
            self._background_tasks.add(self._pool_health_task)
            self._pool_health_task.add_done_callback(self._background_tasks.discard)

    async def _ensure_background(self) -> None:
        """Create the persistent background session if it doesn't exist."""
        async with self._lock:
            if BACKGROUND_KEY in self._sessions:
                return
        # Create outside lock
        if not self._provider_factory:
            return
        try:
            provider = self._provider_factory(BACKGROUND_KEY, agent="personalclaw-lite")
            async with self._start_sem:
                await provider.start()
        except _PROVIDER_RESOLUTION_ERRORS:
            # Expected first-run state: no chat ModelProvider is configured yet, so
            # the lite background agent can't resolve one. Defer quietly (INFO, not a
            # WARNING traceback) — the factory itself is the single source of truth
            # for "is a model resolvable", so we never diverge from it. Self-healing:
            # reload_provider_factory() (fired when a provider is added in Settings)
            # re-runs start_pool -> _ensure_background, bringing it up automatically.
            logger.info(
                "Background session deferred: no chat model resolves yet "
                "(add a model in Settings → Providers; it will start on reload)."
            )
            return
        except Exception:
            logger.warning("Failed to create background session", exc_info=True)
            return
        async with self._lock:
            if BACKGROUND_KEY not in self._sessions:
                sess = _Session(provider=provider, is_new=False)
                self._sessions[BACKGROUND_KEY] = sess
                logger.info("Background session created")
            else:
                await provider.shutdown()

    # ── Warm Pool ──

    async def _fill_warm_pool(self) -> None:
        """
        Spawn providers up to ``_pool_size`` and enqueue them.
        Pool fill stops on first failure and does not retry until next claim.
        """
        if not self._pool_size or not self._provider_factory:
            return
        async with self._pool_fill_lock:
            while self._warm_pool.qsize() < self._pool_size:
                p = None
                try:
                    p = self._provider_factory(
                        "",
                        agent=self._pool_agent or None,
                        cwd=self._pool_cwd or None,
                    )
                    async with self._start_sem:
                        await p.start()
                    self._warm_pool.put_nowait((p, time.monotonic()))
                    p = None  # successfully enqueued — nothing to clean up
                    logger.info(
                        "Warm pool: spawned process (pool=%d/%d agent=%s)",
                        self._warm_pool.qsize(),
                        self._pool_size,
                        self._pool_agent or "default",
                    )
                except Exception:
                    logger.warning("Warm pool: failed to spawn process", exc_info=True)
                    break
                finally:
                    if p is not None:
                        try:
                            await p.shutdown()
                        except Exception:
                            pass
                        except BaseException:
                            _sync_kill_provider(p)
                            raise

    def _claim_from_pool(self, agent: str | None) -> tuple[ModelProvider, float] | None:
        """Try to claim a pre-warmed provider if the agent matches.
        Deny-by-default: normalize both sides and positively compare.
        None/empty agent means "use default" → promoted to pool_agent.
        """
        if self._warm_pool.empty():
            return None
        requested = agent if agent else (self._pool_agent or "")
        pool = self._pool_agent or ""
        if requested != pool:
            return None
        try:
            return self._warm_pool.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _claim_acp_pool(
        self,
        key: str,
        channel_id: str | None,
        agent: str | None,
        model: str | None,
        extra_factory_kwargs: dict[str, Any],
    ) -> "ModelProvider | None":
        """Claim a warmed connection from the ACP live-connection pool and
        specialize it for this session, or ``None`` (caller cold-starts).

        Only fires for ``acp:<cli>`` runtimes (read from the resolved
        ``provider_kind`` the chat runner threads through). The claimed connection
        is already started; we rekey it to this session and apply the per-session
        agent persona (default-dialect ``set_mode``) + model live, mirroring the
        single-agent warm-pool claim path."""
        provider_kind = str(extra_factory_kwargs.get("provider_kind") or "")
        if not provider_kind.startswith("acp:"):
            return None
        try:
            from personalclaw.acp.connection_pool import get_acp_pool

            pool = get_acp_pool()
            if pool is None:
                return None
            provider = await pool.claim(provider_kind)
        except Exception:
            logger.debug("ACP pool claim failed for %s", key, exc_info=True)
            return None
        if provider is None:
            return None
        try:
            from personalclaw.agents.provider import AgentProvider

            if isinstance(provider, AgentProvider):
                provider.set_session_key(key, channel_id)
                # Specialize live: bind the chosen persona (ACP modeId) + model +
                # permission mode. The pooled connection was warmed generic
                # (default agent/model/mode), so a session needing a non-default
                # mode (e.g. an unattended goal loop's bypassPermissions) MUST
                # re-apply it here or it silently stays "default". Mode follows
                # model (adapters clamp an out-of-range mode to the active model).
                _acp_agent = str(extra_factory_kwargs.get("agent") or agent or "")
                if _acp_agent:
                    await provider.set_agent(_acp_agent)
                if model:
                    await provider.set_model(model)
                _acp_mode = str(extra_factory_kwargs.get("acp_mode") or "")
                if _acp_mode and hasattr(provider, "set_mode"):
                    await provider.set_mode(_acp_mode)
                # Reasoning effort follows model too (effort granularity can be
                # model-dependent). A pooled connection warms at the adapter default,
                # so re-apply the session's effort live on claim.
                _acp_effort = str(extra_factory_kwargs.get("reasoning_effort_override") or "")
                if _acp_effort and hasattr(provider, "set_reasoning_effort"):
                    await provider.set_reasoning_effort(_acp_effort)
            logger.info("Claimed ACP pool connection for %s (runtime=%s)", key, provider_kind)
            return provider
        except (asyncio.CancelledError, Exception):
            _sync_kill_provider(provider)
            raise

    async def _open_acp_concurrent(
        self,
        key: str,
        channel_id: str | None,
        agent: str | None,
        model: str | None,
        cwd: str | None,
        extra_factory_kwargs: dict[str, Any],
    ) -> "ModelProvider | None":
        """P9: open a session on a SHARED per-runtime AcpConnection when concurrent
        sessions are enabled (double-gated). Returns ``None`` when the gate is off or the
        runtime isn't an ``acp:<cli>`` (caller falls back to the one-session path)."""
        provider_kind = str(extra_factory_kwargs.get("provider_kind") or "")
        if not provider_kind.startswith("acp:"):
            return None
        try:
            from personalclaw.acp.connection_pool import get_acp_pool
            from personalclaw.llm.acp_session_provider import concurrent_sessions_enabled
            from personalclaw.llm.registry import get_default_registry

            entry = get_default_registry().get_entry(provider_kind)
            options = dict(entry.options or {})
            dialect = options.get("dialect")
            if not concurrent_sessions_enabled(str(dialect) if dialect else None):
                return None
            pool = get_acp_pool()
            if pool is None:
                return None
            command = options.get("command")
            if not isinstance(command, list) or not command:
                return None
            sfd = options.get("session_files_dir")
            from pathlib import Path as _Path

            effective_cwd = _Path(str(cwd)) if cwd else default_workspace_dir()
            provider = await pool.open_session(
                provider_kind,
                cwd=effective_cwd,
                command=[str(c) for c in command],
                dialect=str(dialect) if dialect else None,
                session_files_dir=_Path(str(sfd)) if sfd else None,
                sandbox_mode=str(options.get("sandbox_mode") or "auto"),
                extra_env=(_env if isinstance((_env := options.get("env")), dict) else None),
                session_key=key,
                channel_id=channel_id,
                model=model or "",
                agent_name=agent or "",
            )
            if provider is None:
                return None
            # Specialize live (persona / model / mode / effort) exactly like the claim path.
            from personalclaw.agents.provider import AgentProvider

            if isinstance(provider, AgentProvider):
                if agent:
                    await provider.set_agent(agent)
                if model:
                    await provider.set_model(model)
                _mode = str(extra_factory_kwargs.get("acp_mode") or "")
                if _mode and hasattr(provider, "set_mode"):
                    await provider.set_mode(_mode)
                _effort = str(extra_factory_kwargs.get("reasoning_effort_override") or "")
                if _effort and hasattr(provider, "set_reasoning_effort"):
                    await provider.set_reasoning_effort(_effort)
            logger.info("Opened concurrent ACP session for %s (runtime=%s)", key, provider_kind)
            return provider
        except Exception:
            logger.debug(
                "ACP concurrent open_session failed for %s — falling back", key, exc_info=True
            )
            return None

    async def _drain_and_claim(self, agent: str | None) -> ModelProvider | None:
        """Claim a live, non-stale provider from the warm pool."""
        discarded = False
        claimed = self._claim_from_pool(agent)
        while claimed is not None:
            provider, spawn_time = claimed
            # Check TTL (0 = disabled)
            age = time.monotonic() - spawn_time
            if self._pool_ttl_secs and age > self._pool_ttl_secs:
                logger.warning(
                    "Warm pool: %.0fs old provider exceeds TTL %ds, discarding",
                    age,
                    self._pool_ttl_secs,
                )
                discarded = True
                try:
                    await provider.shutdown()
                except Exception:
                    pass
                except BaseException:
                    _sync_kill_provider(provider)
                    raise
                claimed = self._claim_from_pool(agent)
                continue
            # Check liveness — use process-level check, not is_alive/is_responsive
            # which has a 600s stale-activity threshold.  Pool processes are
            # expected to be idle (no I/O after init) so the stale check would
            # falsely discard healthy processes after ~10 min.
            alive = hasattr(provider, "is_process_alive") and provider.is_process_alive()
            if not alive:
                rc = provider.exit_code if hasattr(provider, "exit_code") else None
                logger.warning(
                    "Warm pool: claimed provider is dead (returncode=%s), discarding", rc
                )
                discarded = True
                try:
                    await provider.shutdown()
                except Exception:
                    pass
                except BaseException:
                    _sync_kill_provider(provider)
                    raise
                claimed = self._claim_from_pool(agent)
                continue
            return provider
        # No healthy provider found — replenish if we discarded any
        if discarded:
            self._schedule_replenish()
        return None

    def _schedule_replenish(self) -> None:
        """Fire-and-forget task to refill the warm pool after a claim."""
        if not self._pool_size:
            return
        t = asyncio.create_task(self._fill_warm_pool())
        self._background_tasks.add(t)
        t.add_done_callback(self._background_tasks.discard)

    def _pool_pids(self) -> set[int]:
        """Return PIDs of all providers currently in the warm pool (non-destructive peek)."""
        pids: set[int] = set()
        # Drain and re-enqueue to peek without losing entries
        items: list[tuple[ModelProvider, float]] = []
        while not self._warm_pool.empty():
            try:
                entry = self._warm_pool.get_nowait()
                items.append(entry)
            except asyncio.QueueEmpty:
                break
        for provider, spawn_time in items:
            pid = getattr(getattr(provider, "client", None), "_pid", None)
            if isinstance(pid, int):
                pids.add(pid)
            self._warm_pool.put_nowait((provider, spawn_time))
        pids.update(self._pool_sweep_pids)
        return pids

    _POOL_HEALTH_INTERVAL = 30  # seconds between health sweeps

    async def _pool_health_loop(self) -> None:
        """Periodically sweep the warm pool, discard dead/expired providers, and refill."""
        while True:
            await asyncio.sleep(self._POOL_HEALTH_INTERVAL)
            try:
                if not self._pool_size:
                    continue
                qsize = self._warm_pool.qsize()
                if not qsize:
                    continue
                logger.debug(
                    "Pool health: sweeping %d providers (target=%d, ttl=%ds)",
                    qsize,
                    self._pool_size,
                    self._pool_ttl_secs,
                )
                # Drain entire queue, keep healthy entries, discard the rest
                healthy: list[tuple[ModelProvider, float]] = []
                to_shutdown: list[ModelProvider] = []
                now = time.monotonic()
                try:
                    for _ in range(qsize):
                        try:
                            provider, spawn_time = self._warm_pool.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        age = now - spawn_time
                        pid = getattr(getattr(provider, "client", None), "_pid", None)
                        if isinstance(pid, int):
                            self._pool_sweep_pids.add(pid)
                        if self._pool_ttl_secs and age > self._pool_ttl_secs:
                            logger.warning(
                                "Pool health: %.0fs old provider (pid=%s) exceeds TTL %ds, discarding",  # noqa: E501
                                age,
                                pid,
                                self._pool_ttl_secs,
                            )
                            to_shutdown.append(provider)
                            continue
                        try:
                            alive = (
                                hasattr(provider, "is_process_alive")
                                and provider.is_process_alive()
                            )
                        except Exception:
                            alive = False
                        if not alive:
                            rc = provider.exit_code if hasattr(provider, "exit_code") else None
                            logger.warning(
                                "Pool health: dead provider (pid=%s, returncode=%s, age=%.0fs), discarding",  # noqa: E501
                                pid,
                                rc,
                                age,
                            )
                            to_shutdown.append(provider)
                            continue
                        logger.debug("Pool health: provider pid=%s alive (age=%.0fs)", pid, age)
                        healthy.append((provider, spawn_time))
                finally:
                    # Re-enqueue survivors first, then shut down dead providers.
                    # This avoids an empty-queue window where _drain_and_claim()
                    # would fall back to cold start.  CancelledError during
                    # shutdown may skip remaining providers in to_shutdown —
                    # acceptable because they're already dead/expired and their
                    # PIDs are tracked in session_pids.txt for startup
                    # cleanup.  Sweep PIDs are cleared in a nested finally so
                    # they can't go stale regardless of how we exit.
                    try:
                        for entry in healthy:
                            self._warm_pool.put_nowait(entry)
                        for p in to_shutdown:
                            try:
                                await p.shutdown()
                            except Exception:
                                pass
                    finally:
                        self._pool_sweep_pids.clear()
                removed = qsize - len(healthy)
                if removed:
                    logger.info(
                        "Pool health: removed %d dead/expired, %d healthy remain",
                        removed,
                        len(healthy),
                    )
                    self._schedule_replenish()
                else:
                    logger.debug("Pool health: all %d providers healthy", len(healthy))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pool health sweep failed")

    def context_info(self) -> list[dict[str, object]]:
        """Return context usage for all active sessions."""
        from personalclaw.agents.provider import AgentProvider

        result: list[dict[str, object]] = []
        for key, sess in self._sessions.items():
            pct = sess.provider.context_usage_pct()
            model = "unknown"
            agent = ""
            if isinstance(sess.provider, AgentProvider):
                model = sess.provider.agent_model or "auto"
                agent = sess.provider.agent_name or ""
                if model == "auto" and agent and agent != "personalclaw":
                    model = self._resolve_agent_model(agent)
                model = model or "auto"
            # Human-readable name
            if key == BACKGROUND_KEY:
                name = "Background (titles, cron, heartbeat)"
            elif key.startswith("dashboard:"):
                name = f"Chat ({key.split(':', 1)[1]})"
            else:
                name = key
            result.append(
                {
                    "key": key,
                    "name": name,
                    "model": model,
                    "agent": agent,
                    "context_pct": round(pct, 1),
                    "prompts": sess.prompt_count,
                }
            )
        return result

    @staticmethod
    def _resolve_agent_model(agent: str) -> str:
        """Resolve model from agent config file. Cached at class level."""
        if not hasattr(SessionManager, "_agent_model_cache"):
            SessionManager._agent_model_cache = {}  # type: ignore[attr-defined]
        cache = SessionManager._agent_model_cache  # type: ignore[attr-defined]
        if agent in cache:
            return cache[agent]
        try:
            import json as _json

            from personalclaw.agent import AGENTS_DIR as AGENTS_DIR

            for af in AGENTS_DIR.glob("*.json"):
                try:
                    ad = _json.loads(af.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                if ad.get("name") == agent or af.stem == agent:
                    model = ad.get("model", "auto")
                    cache[agent] = model
                    return model
        except Exception:
            pass
        cache[agent] = "auto"
        return "auto"

    async def recycle_background(self) -> None:
        """Check background session context and recycle if too full.

        Background tasks are stateless (cron, heartbeat, lessons), so we
        don't need compaction — just kill the old session and create a fresh
        one.  Called after each background task completes.

        Thresholds are more aggressive than chat compaction:
        - At ≥ 70% context → recycle
        - After 40 prompts with no metadata → recycle (blind fallback)
        """
        session = self._sessions.get(BACKGROUND_KEY)
        if not session:
            return

        pct = session.provider.context_usage_pct()
        needs_recycle = pct >= _BG_RECYCLE_PCT
        if not needs_recycle and pct == 0.0:
            # Blind fallback: recycle after N prompts if metadata never reports %
            needs_recycle = session.prompt_count >= _BG_BLIND_RECYCLE_PROMPTS

        if not needs_recycle:
            return

        reason = f"context at {pct:.0f}%" if pct > 0 else f"blind ({session.prompt_count} prompts)"
        logger.info("Recycling background session — %s", reason)

        # Kill old session
        async with self._lock:
            old = self._sessions.pop(BACKGROUND_KEY, None)
        if old:
            await old.provider.shutdown()

        # Create fresh replacement
        await self._ensure_background()

    async def get_or_create(
        self,
        key: str,
        agent: str | None = None,
        channel_id: str | None = None,
        approval_policy: str = "",
        model: str | None = None,
        cwd: str | None = None,
        extra_env: dict[str, str] | None = None,
        **extra_factory_kwargs: Any,
    ) -> tuple[ModelProvider, bool, bool]:
        """Return ``(ModelProvider, is_new, resumed)`` for *key*, creating if needed.

        ``resumed`` is True when the session was restored via ACP session/load
        (ACP agent has full native history — skip thread history injection).

        For new sessions, tries the warm pool first for instant startup.
        If the session is mid-compaction, creates a fresh one instead.
        Acquires the per-session semaphore before returning — caller MUST
        call ``release(key)`` when done.

        Args:
            agent: Optional agent name for ``session/set_mode``.  Non-default
                agents skip the warm pool (cold start only).
            model: Optional model override for the session.
        """
        # Fast path: existing session — hold lock only briefly
        stale_provider = None
        # A live existing session to return; its per-session semaphore is acquired
        # AFTER the global lock is released (see below). Acquiring it under the lock
        # would deadlock every other get_or_create: if that session is mid-turn its
        # semaphore is held, so `await acquire()` would block while holding _lock.
        reuse: tuple[ModelProvider, bool, "_Session"] | None = None
        try:
            async with self._lock:
                if key in self._sessions and key not in self._compacting:
                    sess = self._sessions[key]
                    # If the provider's process died (crash, SIGKILL, etc.),
                    # remove the stale entry so we fall through to cold-start
                    # with is_new=True — ensuring full context re-injection.
                    # Use process-level check, not is_alive() which has a 600s
                    # stale-activity threshold that falsely kills idle sessions.
                    if hasattr(sess.provider, "is_process_alive"):
                        _alive = sess.provider.is_process_alive()
                    else:
                        _alive = sess.provider.is_alive()
                    if not _alive:
                        logger.warning("Session %s has dead provider — removing stale entry", key)
                        stale_provider = sess.provider
                        del self._sessions[key]
                        # Preserve session_map entry: the ACP agent session
                        # files survive on disk, enabling lossless resume
                        # via session/load on the next get_or_create().
                    if _alive:
                        # agent is not updated: subagent session keys are unique
                        # per spawn so a key collision with a different agent
                        # cannot happen in practice.
                        sess.last_used = time.monotonic()
                        was_new = sess.is_new
                        sess.is_new = False
                        # Defer the (potentially blocking) semaphore acquire until
                        # after the lock is released — do NOT await it here.
                        reuse = (sess.provider, was_new, sess)

                if reuse is None:
                    if not self._provider_factory:
                        raise RuntimeError("No provider factory configured")

                    factory = self._provider_factory
        finally:
            # Kill orphaned child processes (MCP servers, ACP agent children)
            # outside the lock — shutdown() may involve signals/waitpid.
            if stale_provider is not None:
                try:
                    await stale_provider.shutdown()
                except Exception:
                    logger.warning("Failed to shut down stale provider for %s", key, exc_info=True)

        # Reuse an existing live session: acquire its per-session semaphore OUTSIDE
        # the global lock (a mid-turn session holds it, so this can block — which
        # must never happen under _lock, or all other get_or_create calls wedge).
        provider: "ModelProvider | None"
        if reuse is not None:
            provider, was_new, sess = reuse
            await sess.semaphore.acquire()
            return provider, was_new, False

        # Check session map for resume — only for long-lived sessions
        resume_sid: str | None = None
        is_stateless = key == BACKGROUND_KEY or any(key.startswith(p) for p in _STATELESS_PREFIXES)
        if not is_stateless:
            resume_sid = self._session_map.get(key)

        # Try warm pool first (no resume — pooled processes have no prior session)
        logger.info(
            "Pool decision: key=%s resume_sid=%s model=%s agent=%s pool_size=%d pool_qsize=%d cwd=%s pool_cwd=%s",  # noqa: E501
            key,
            resume_sid,
            model,
            agent,
            self._pool_size,
            self._warm_pool.qsize(),
            cwd,
            self._pool_cwd,
        )
        # Only bypass pool for cwd if it's a user-chosen project that differs
        # from the default workspace dir (which pool processes already use).
        cwd_blocks_pool = bool(cwd and cwd != self._pool_cwd)
        pooled = (
            None
            if resume_sid or is_stateless or not self._pool_size or cwd_blocks_pool or extra_env
            else await self._drain_and_claim(agent)
        )
        if pooled is not None:
            provider = pooled
            try:
                # Re-key pooled provider with actual session parameters via the
                # AgentProvider capability surface (set_session_key/set_model) so
                # any stateful runtime — ACP today, native in P4 — claims cleanly.
                from personalclaw.agents.provider import AgentProvider

                if isinstance(provider, AgentProvider):
                    provider.set_session_key(key, channel_id)
                    # Switch model post-claim if caller requested non-default
                    if model:
                        _pool_model = (
                            self._resolve_agent_model(self._pool_agent)
                            if self._pool_agent
                            else None
                        )
                        if model and _pool_model and model != _pool_model:
                            await provider.set_model(model)
                            logger.info("Pool post-claim: switched model to %s", model)
                logger.info(
                    "Claimed warm-pool process for %s (agent=%s)", key, agent or self._pool_agent
                )
                self._schedule_replenish()
            except (asyncio.CancelledError, Exception):
                _sync_kill_provider(provider)
                raise
        else:
            # ACP live-connection pool: when the resolved runtime is an
            # ``acp:<cli>`` and there's no resume to honor (a pooled connection has
            # no prior session), try to claim a WARMED connection so the first turn
            # is instant instead of paying the ~15-20s cold start. On a hit we
            # specialize it live (agent persona / model) and use it WITHOUT
            # calling start() (already started). Misses fall through to cold-start.
            provider = None
            claimed_from_acp_pool = False
            if not resume_sid and not is_stateless:
                # P9: when concurrent sessions are enabled for this (proven-concurrent)
                # ACP runtime, open a session on a SHARED connection instead of claiming
                # a whole one-session-per-process connection. Gated (both flags off →
                # this returns None and we fall through to the one-session path).
                provider = await self._open_acp_concurrent(
                    key, channel_id, agent, model, cwd, extra_factory_kwargs
                )
                if provider is None:
                    provider = await self._claim_acp_pool(
                        key, channel_id, agent, model, extra_factory_kwargs
                    )
                claimed_from_acp_pool = provider is not None

            if not claimed_from_acp_pool:
                # Cold start: start provider OUTSIDE the lock so other sessions
                # can proceed in parallel.  Semaphore limits concurrent cold-starts
                # to avoid CPU saturation from multiple ACP agent processes.
                # On resume, use the CWD stored in session_map so CC CLI finds
                # its conversation in the correct project directory.
                effective_cwd = cwd
                if not effective_cwd and resume_sid:
                    stored_cwd = self._session_map.get_cwd(key)
                    if stored_cwd and Path(stored_cwd).is_dir():
                        effective_cwd = stored_cwd
                        logger.info("Resume CWD override for %s: %s", key, stored_cwd)
                provider = factory(
                    key,
                    agent=agent,
                    channel_id=channel_id,
                    model_override=model,
                    cwd=effective_cwd,
                    extra_env=extra_env,
                    **extra_factory_kwargs,
                )
                # Set resume ID before start() triggers _initialize_session
                if resume_sid:
                    from personalclaw.agents.provider import AgentProvider

                    if isinstance(provider, AgentProvider):
                        provider.set_resume(resume_sid)
                        logger.info("Attempting session/load for %s (sid=%s)", key, resume_sid)
                async with self._start_sem:
                    try:
                        await provider.start()
                    except (asyncio.CancelledError, Exception):
                        # Provider process may have spawned before the cancel/error —
                        # shut it down so it doesn't leak.  Use synchronous kill as
                        # a last resort since asyncio.shield is unreliable during
                        # cancellation (the awaited future raises CancelledError
                        # immediately, leaving shutdown fire-and-forget).
                        _sync_kill_provider(provider)
                        raise

        # Everything after start() must be wrapped so that a CancelledError
        # between start() and session registration doesn't orphan the process.
        # Invariant: by here provider is set — either claimed from the ACP pool or
        # cold-started via factory() above (both branches assign it).
        assert provider is not None
        try:
            # Check if session was resumed
            resumed = False
            from personalclaw.agents.provider import AgentProvider

            if isinstance(provider, AgentProvider):
                resumed = provider.resumed

            race_loser: "_Session | None" = None
            async with self._lock:
                # Re-check: another coroutine may have created this key while we
                # were starting the provider (race on same key).
                if key in self._sessions and key not in self._compacting:
                    # Another task won the race — shut down our provider, use theirs.
                    await provider.shutdown()
                    sess = self._sessions[key]
                    sess.last_used = time.monotonic()
                    if approval_policy:
                        sess.approval_policy = approval_policy
                    if agent:
                        sess.agent = agent
                    # Defer the (possibly blocking) semaphore acquire until after
                    # the lock is released — the winner's session may be mid-turn.
                    race_loser = sess
                else:
                    sess = _Session(
                        provider=provider,
                        is_new=False,
                        approval_policy=approval_policy,
                        agent=agent or "",
                    )
                    self._sessions[key] = sess
                    logger.info(
                        "New session: %s agent=%s resumed=%s (total=%d)",
                        key,
                        agent or "personalclaw",
                        resumed,
                        len(self._sessions),
                    )

                    # Save session mapping for long-lived sessions. session_id is an
                    # AgentProvider capability (ACP: the CLI's session UUID; native:
                    # its own id) — read it through the public accessor.
                    _cwd_str = str(provider._work_dir) if hasattr(provider, "_work_dir") else ""
                    if not is_stateless and isinstance(provider, AgentProvider):
                        sid = provider.session_id
                        if sid:
                            self._session_map.set(key, sid, cwd=_cwd_str)

                    if self._cleanup_task is None or self._cleanup_task.done():
                        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

                    # Freshly-created semaphore (count 1, never held) — acquire is
                    # instant and cannot block, so it's safe under the lock.
                    await sess.semaphore.acquire()
                    Stats().inc_session_created()

                    result = (provider, True, resumed)
        except BaseException:
            # CancelledError or any other exception after provider.start()
            # succeeded — provider is running but never registered.  Kill it.
            _sync_kill_provider(provider)
            raise

        # Race-loser reuse: acquire the winner's per-session semaphore OUTSIDE the
        # lock (it may be mid-turn) so we never block other get_or_create callers.
        if race_loser is not None:
            await race_loser.semaphore.acquire()
            return race_loser.provider, False, False

        return result

    async def reset(self, key: str) -> None:
        """Kill and recreate a session (context overflow recovery)."""
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session:
            # Capture PID and child tree before shutdown clears them
            client = getattr(session.provider, "_client", None)
            raw_pid = getattr(client, "_pid", None) if client else None
            # CC provider: PID from long-lived _proc or ephemeral _active_proc
            if raw_pid is None:
                _cc_proc = getattr(session.provider, "_proc", None)
                if _cc_proc is not None and _cc_proc.returncode is None:
                    raw_pid = _cc_proc.pid
            if raw_pid is None:
                _cc_proc = getattr(session.provider, "_active_proc", None)
                if _cc_proc is not None and _cc_proc.returncode is None:
                    raw_pid = _cc_proc.pid
            pid = raw_pid if isinstance(raw_pid, int) else None
            raw_children = getattr(client, "_child_pids", None) if client else None
            child_pids: dict[int, int | None] = (
                dict(raw_children) if isinstance(raw_children, dict) else {}
            )
            if pid:
                # Lazy import to avoid circular dependency with acp.client
                from personalclaw.acp.client import (
                    _get_child_pids,
                    _get_start_time,
                    _kill_escaped_children,
                )

                # Snapshot child tree before shutdown.  PIDs may be recycled
                # between snapshot and kill, but _kill_escaped_children uses
                # start-time comparison to skip recycled PIDs safely.
                for p in _get_child_pids(pid):
                    if p not in child_pids:
                        child_pids[p] = _get_start_time(p)
            await session.provider.shutdown()
            # Verify process is actually dead; force-kill entire tree if not
            if pid:
                try:
                    os.kill(pid, 0)
                    # Still alive after shutdown — force kill process group
                    logger.warning("Reset %s: PID %d survived shutdown, force-killing", key, pid)
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # dead as expected
                except OSError:
                    pass
                # Sweep children in different PGIDs (MCP servers) even when
                # root is dead — children in separate process groups may
                # outlive the root.
                if child_pids:
                    try:
                        _kill_escaped_children(child_pids)
                    except Exception:
                        logger.exception("Reset %s: child sweep failed", key)
            logger.debug("Reset session: %s (pid=%s)", key, pid)

    def check_context_usage(self, key: str, provider: ModelProvider) -> float:
        """Check context usage and fire background compaction at >= 90%.

        Falls back to prompt-count compaction if metadata never reports %.
        Returns context usage percentage immediately — never blocks.
        """
        pct = provider.context_usage_pct()

        # Track prompts for background session recycle fallback
        session = self._sessions.get(key)
        if session:
            session.prompt_count += 1

        if pct >= self._cfg.session.autocompact_pct:
            self._trigger_compaction(key, f"context at {pct:.0f}%", pct)
        elif pct >= _CONTEXT_WARN_PCT:
            logger.warning("Session %s context at %.0f%%", key, pct)
        elif pct > 0:
            logger.info("Session %s context at %.0f%%", key, pct)
        return pct

    def set_compact_callback(self, cb: Callable[[str, float], Awaitable[None]] | None) -> None:
        """Register a callback fired after a session is compacted.

        The callback receives the session key and the context pct that
        triggered the compaction. Used by the dashboard to post a visible
        notice and reset the context indicator after an otherwise silent
        auto-compaction.
        """
        if self._on_compacted is not None and cb is not None:
            logger.warning("Compact callback already registered; replacing existing handler")
        self._on_compacted = cb

    def set_session_expire_callback(self, cb: Callable[[str], Awaitable[object]] | None) -> None:
        """Register a callback fired with the session key just before an idle
        session is reset. The consolidator wires this to ``consolidate_session``
        so an ending session gets one last skill-extraction pass before its
        transcript is reset.
        """
        self._on_session_expire = cb

    def _trigger_compaction(self, key: str, reason: str, pct: float) -> None:
        if key not in self._compacting:
            logger.warning("Session %s compacting — %s", key, reason)
            self._compacting.add(key)
            t = asyncio.create_task(self._compact_session(key, pct))
            self._background_tasks.add(t)
            t.add_done_callback(self._background_tasks.discard)
        else:
            logger.info("Session %s compaction already in progress", key)

    async def _compact_session(self, key: str, pct: float) -> None:
        """Kill and replace a session that hit the context threshold.

        The fresh session gets context re-injected via build_session_context()
        on the next user message, so no /compact call is needed.
        Deletes the session_map entry to prevent false resumes with stale data.
        """
        try:
            async with self._lock:
                session = self._sessions.pop(key, None)
            if session:
                self._session_map.delete(key)
                await session.provider.shutdown()
                logger.info("Recycled session %s (context overflow)", key)
                if self._on_compacted is not None:
                    try:
                        await self._on_compacted(key, pct)
                    except Exception:
                        logger.exception("Compact callback failed for %s", key)
        except Exception:
            logger.exception("Session recycle failed for %s", key)
        finally:
            self._compacting.discard(key)

    async def remove(self, key: str) -> None:
        """Shut down a session but preserve session_map for future resume.

        Use when the session can be revived later (tab close, agent switch,
        idle kill).  The ACP agent session files remain on disk, so
        ``session/load`` can restore the full conversation losslessly.
        """
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session:
            await session.provider.shutdown()
            logger.info("Removed session (map preserved): %s", key)

    async def destroy(self, key: str) -> None:
        """Permanently destroy a session — no resume possible.

        Use for irreversible actions: permanent history deletion, bulk
        clear, or error recovery where the session state is corrupt.
        """
        async with self._lock:
            session = self._sessions.pop(key, None)
        try:
            if session:
                await session.provider.shutdown()
        finally:
            self._session_map.delete(key)
            logger.info("Destroyed session (map deleted): %s", key)

    async def close_all(self) -> None:
        """Shut down every session (called on shutdown)."""
        if self._cleanup_task:
            self._cleanup_task.cancel()

        # Cancel background spawn tasks (may be blocked in _INIT_TIMEOUT waits)
        # _pool_health_task is included via _background_tasks registration.
        for t in list(self._background_tasks):
            t.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        # Drain warm pool — shut down pre-spawned processes
        pool_providers: list[ModelProvider] = []
        while not self._warm_pool.empty():
            try:
                provider, _ = self._warm_pool.get_nowait()
                pool_providers.append(provider)
            except asyncio.QueueEmpty:
                break

        async with self._lock:
            # Save session mappings before killing processes
            from personalclaw.agents.provider import AgentProvider

            for key, sess in self._sessions.items():
                _cwd_str = (
                    str(sess.provider._work_dir) if hasattr(sess.provider, "_work_dir") else ""
                )
                if isinstance(sess.provider, AgentProvider):
                    sid = sess.provider.session_id
                    if (
                        sid
                        and key != BACKGROUND_KEY
                        and not any(key.startswith(p) for p in _STATELESS_PREFIXES)
                    ):
                        self._session_map.set(key, sid, cwd=_cwd_str)

            sessions = dict(self._sessions)
            self._sessions.clear()

        async def _close_one(provider: ModelProvider) -> None:
            try:
                await provider.shutdown()
            except Exception:
                pass

        all_providers = [s.provider for s in sessions.values()] + pool_providers
        if not all_providers:
            return

        try:
            await asyncio.wait_for(
                asyncio.gather(*[_close_one(p) for p in all_providers], return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout closing %d sessions — orphan cleanup at next startup", len(all_providers)
            )
        logger.info("All sessions closed (active=%d)", len(sessions))

    # ── Circuit breaker ──

    def record_success(self, key: str) -> None:
        """Reset consecutive failure counter on success."""
        session = self._sessions.get(key)
        if session:
            session.consecutive_failures = 0

    async def record_failure(self, key: str) -> bool:
        """Increment failure counter. Returns True if circuit tripped (session reset)."""
        session = self._sessions.get(key)
        if not session:
            return False
        session.consecutive_failures += 1
        if session.consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            logger.error(
                "Circuit breaker tripped for %s (%d consecutive failures) — resetting",
                key,
                session.consecutive_failures,
            )
            await self.reset(key)
            return True
        return False

    # ── Per-session semaphore ──

    def release(self, key: str, *, cleanup: bool = False) -> None:
        """Release the per-session semaphore acquired by ``get_or_create``.

        If *cleanup* is True and the key is a subagent session, schedule
        best-effort deletion of the provider's on-disk session files.
        """
        session = self._sessions.get(key)
        if session:
            if cleanup and key.startswith(_SUBAGENT_PREFIX):
                try:
                    session_id = session.provider.session_id
                    if session_id:
                        asyncio.ensure_future(self._safe_cleanup(session.provider, session_id))
                except Exception:
                    logger.debug("Failed to get session_id for cleanup", exc_info=True)
            session.semaphore.release()

    async def _safe_cleanup(self, provider: ModelProvider, session_id: str) -> None:
        """Best-effort session file cleanup."""
        try:
            await provider.cleanup_session(session_id)
            logger.debug("Cleaned up session files for %s", session_id)
        except Exception:
            logger.warning("Failed to clean up session files for %s", session_id, exc_info=True)

    # ── Message queue (channel thread serialization) ──

    def enqueue(
        self, key: str, msg_ts: str, text: str, *, force: bool = False, **kwargs: object
    ) -> bool:
        """Append a message to the session queue. Returns True if queued (session busy).

        If *force* is True, queue even when the semaphore isn't locked yet
        (covers the startup race where a task exists but hasn't acquired the lock).
        """
        session = self._sessions.get(key)
        if not session:
            return False
        if force or session.semaphore.locked():
            session.queue.append((msg_ts, text, kwargs))
            return True
        return False

    def dequeue(self, key: str) -> tuple[str, str, dict] | None:
        """Pop the next queued message, skipping cancelled ones."""
        session = self._sessions.get(key)
        if not session:
            return None
        while session.queue:
            msg_ts, text, kwargs = session.queue.popleft()
            if msg_ts not in session.cancelled:
                return msg_ts, text, kwargs
            session.cancelled.discard(msg_ts)
        return None

    def add_steer(self, key: str, text: str) -> bool:
        """Buffer a mid-turn steering message (#37). Returns True if a turn is
        in-flight (so the loop will drain it); else the caller should queue it."""
        session = self._sessions.get(key)
        if not session or not text.strip():
            return False
        if not session.semaphore.locked():
            return False  # nothing in-flight to steer — caller queues normally
        session.steers.append(text.strip())
        return True

    def drain_steers(self, key: str) -> list[str]:
        """Pop all buffered steering messages for a session (the loop's pull source)."""
        session = self._sessions.get(key)
        if not session or not session.steers:
            return []
        out = list(session.steers)
        session.steers.clear()
        return out

    def cancel_queued(self, key: str, msg_ts: str) -> bool:
        """Remove a queued message or mark an in-flight message as cancelled.

        Returns True if the msg_ts was found in the queue and removed.
        Returns False if not queued (may be in-flight — added to cancelled set).
        """
        session = self._sessions.get(key)
        if not session:
            return False
        for i, (ts, _, _) in enumerate(session.queue):
            if ts == msg_ts:
                del session.queue[i]
                return True
        # Not in queue — only mark cancelled if something is actually in-flight
        if session.semaphore.locked():
            session.cancelled.add(msg_ts)
        return False

    def is_cancelled(self, key: str, msg_ts: str) -> bool:
        """Check if a message was cancelled (deleted while processing)."""
        session = self._sessions.get(key)
        if not session:
            return False
        if msg_ts in session.cancelled:
            session.cancelled.discard(msg_ts)
            return True
        return False

    def clear_queue(self, key: str) -> None:
        """Clear all queued messages and cancelled set for a session."""
        session = self._sessions.get(key)
        if session:
            session.queue.clear()
            session.cancelled.clear()

    async def is_provider_alive(self, key: str) -> bool | None:
        """Return True/False for provider liveness, or None if no session exists."""
        async with self._lock:
            sess = self._sessions.get(key)
        if sess is None:
            return None
        # Use process-level check, not is_alive() which has a 600s
        # stale-activity threshold that falsely kills idle sessions.
        if hasattr(sess.provider, "is_process_alive"):
            return sess.provider.is_process_alive()
        return sess.provider.is_alive()

    def get_approval_policy(self, key: str) -> str:
        """Return the approval policy for a session, or empty string."""
        session = self._sessions.get(key)
        return session.approval_policy if session else ""

    def get_agent(self, key: str) -> str:
        """Return the agent name for a session, or empty string."""
        session = self._sessions.get(key)
        return session.agent if session else ""

    def set_approval_policy(self, key: str, policy: str) -> None:
        """Set the approval policy for an existing session."""
        session = self._sessions.get(key)
        if session:
            old = session.approval_policy
            session.approval_policy = policy
            # Push to the provider if it gates tools itself (native runtime);
            # ACP enforces approval via its own protocol path and has no setter.
            prov_set = getattr(session.provider, "set_approval_policy", None)
            if callable(prov_set):
                prov_set(policy)
            if old != policy:
                from personalclaw.sel import sel

                sel().log_tool_invocation(
                    session_key=key,
                    source="session",
                    tool_name="set_approval_policy",
                    outcome=policy or "default",
                    metadata={"old_policy": old, "new_policy": policy},
                )

    def set_task_mode(self, key: str, mode: str) -> None:
        """Push the task mode (agent/ask/plan/build) to a session's provider.

        The task-mode tool gate must hold regardless of approval, so the native
        runtime enforces it in _guard_and_invoke. This forwards the mode to the
        provider when it supports the gate (native); ACP runtimes are gated in the
        dashboard permission handler instead (they have no such setter)."""
        session = self._sessions.get(key)
        if session:
            prov_set = getattr(session.provider, "set_task_mode", None)
            if callable(prov_set):
                prov_set(mode or "agent")

    # ── Channel thread linking (persisted via SessionMap) ──

    def set_channel_link(self, key: str, thread_ts: str, channel_id: str | None) -> None:
        """Link a session to a channel thread. Persists to session map."""
        self._session_map.set_channel_link(key, thread_ts, channel_id)

    def get_channel_link(self, key: str) -> tuple[str | None, str | None]:
        """Return (thread_ts, channel_id) for a session."""
        return self._session_map.get_channel_link(key)

    def get_session_for_thread(self, thread_ts: str) -> str | None:
        """Return the session key linked to a channel thread, or None."""
        return self._session_map.get_session_for_thread(thread_ts)

    # Convenience aliases over the channel-link helpers
    async def set_channel(self, key: str, channel_id: str) -> None:
        """Update only the channel of a session's channel link, keeping its thread_ts."""
        thread_ts, _ = self.get_channel_link(key)
        self.set_channel_link(key, thread_ts or "", channel_id)

    def get_channel(self, key: str) -> str | None:
        """Return the channel ID for a session key, or None."""
        _, channel_id = self.get_channel_link(key)
        return channel_id

    # ── Additional session map helpers ──

    def find_key_by_sid(self, sid: str) -> str | None:
        return self._session_map.find_key_by_sid(sid)

    def delete_session_map_entry(self, key: str) -> None:
        self._session_map.delete(key)

    async def set_thread(self, key: str, thread_ts: str) -> None:
        """Update only the thread_ts of a session's channel link, keeping its channel."""
        _, channel_id = self.get_channel_link(key)
        self.set_channel_link(key, thread_ts, channel_id)

    def get_thread(self, key: str) -> str | None:
        """Return the channel thread_ts for a session key, or None."""
        thread_ts, _ = self.get_channel_link(key)
        return thread_ts

    # ── Cancel ──

    async def cancel_current(self, key: str, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        """Cancel the in-flight operation for *key* without destroying the session."""
        session = self._sessions.get(key)
        if not session:
            return "no_turn"
        outcome = await session.provider.cancel(wait_ack_timeout=wait_ack_timeout)
        logger.info("Cancelled in-flight operation for %s: %s", key, outcome)
        return outcome

    async def stop_turn(
        self,
        key: str,
        *,
        force: bool = False,
        preserve_queue: bool = False,
        on_soft: Callable[[], Awaitable[None]] | None = None,
        on_hard: Callable[[], Awaitable[None]] | None = None,
    ) -> StopOutcome:
        """Cooperative stop with kill fallback + eager respawn.

        Sequence:
          1. clear_queue(key)  — unless preserve_queue (the /interrupt path)
          2. if force: go straight to hard kill
          3. else: send session/cancel, wait up to budget
             - acked → call on_soft hook → return "soft"
             - timeout/error → fall through to hard kill
             - no_turn → return "idle"
          4. hard kill: reset(key) → fire-and-forget respawn → on_hard → "hard"

        ``preserve_queue=True`` (the /interrupt verb) skips the queue clear so the
        _run_chat finally-block dequeue immediately picks up the next queued
        message. ``/stop`` keeps the default (clears the queue).
        """
        session = self._sessions.get(key)
        if not session:
            return "idle"

        if not preserve_queue:
            self.clear_queue(key)
        budget: float = self._cfg.agent.soft_stop_budget_secs

        if not force:
            outcome = await session.provider.cancel(wait_ack_timeout=budget)
            logger.debug("stop_turn: provider.cancel outcome=%r for %s", outcome, key)
            if outcome == "acked":
                # ACP agent discards cancelled turns from its conversation log,
                # so the next prompt must re-inject the cancelled turn context.
                session.prev_turn_cancelled = True
                if on_soft:
                    try:
                        await on_soft()
                    except Exception:
                        logger.warning("on_soft hook failed for %s", key, exc_info=True)
                return "soft"
            if outcome == "no_turn":
                return "idle"
            # timeout or error → escalate to hard kill

        await self.reset(key)
        # Keep a strong reference — the event loop holds only a weak ref,
        # and without this the task could be GC'd mid-respawn.
        t = asyncio.create_task(self._eager_respawn(key))
        self._background_tasks.add(t)
        t.add_done_callback(self._background_tasks.discard)
        if on_hard:
            try:
                await on_hard()
            except Exception:
                logger.warning("on_hard hook failed for %s", key, exc_info=True)
        return "hard"

    async def _eager_respawn(self, key: str) -> None:
        """Fire-and-forget respawn after hard kill.

        ``get_or_create`` acquires the per-session semaphore on every return
        path; release it here so the next real user message can run.
        """
        try:
            await self.get_or_create(key)
            self.release(key)
        except Exception:
            logger.debug("Eager respawn failed for %s", key, exc_info=True)

    @property
    def count(self) -> int:
        return len(self._sessions)

    async def drain_all_providers(self) -> list:
        """Pop all sessions and return their providers. Thread-safe."""
        providers = []
        async with self._lock:
            keys = list(self._sessions.keys())
            for key in keys:
                sess = self._sessions.pop(key, None)
                if sess:
                    providers.append(sess.provider)
        return providers

    async def drain_warm_pool(self) -> list:
        """Drain all pre-spawned providers from the warm pool.

        Returns providers for the caller to shut down. Must be called
        when MCP config changes so stale pool processes (which loaded
        the old config at spawn time) are discarded.
        """
        drained = []
        while not self._warm_pool.empty():
            try:
                provider, _ = self._warm_pool.get_nowait()
                drained.append(provider)
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.info("Drained %d provider(s) from warm pool", len(drained))
        return drained

    # ── Idle cleanup ──

    async def _cleanup_loop(self) -> None:
        timeout = self._cfg.session.timeout_secs
        # Defensive clamp: the dashboard validator allows 0 (disable
        # sentinel) but also accepts 1–59 syntactically. Any positive
        # value below 60 would cause _expire_idle() to aggressively reap
        # active sessions, which is never the intent. Clamp such values
        # up to the minimum of 60.
        if 0 < timeout < 60:
            logger.warning(
                "session.timeout_secs=%d is below minimum 60; clamping to 60",
                timeout,
            )
            timeout = 60
        idle_sweep_enabled = timeout > 0
        if not idle_sweep_enabled:
            logger.info(
                "Idle session sweep disabled (session.timeout_secs=%d); "
                "MCP/PID sweeps still run at default cadence",
                timeout,
            )
        # When idle sweep is disabled we still run the maintenance sweeps
        # (orphaned MCP servers, leaked ACP agent PIDs, deniedCommands) on a
        # fixed cadence so operators who set timeout_secs=0 don't also lose
        # process hygiene.
        interval = max(timeout // 6, 60) if idle_sweep_enabled else 300
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
                return  # shutdown signaled
            except asyncio.TimeoutError:
                pass  # normal wake-up
            if idle_sweep_enabled:
                await self._expire_idle(timeout)

            # Sweep MCP servers orphaned by crashed/expired sessions
            try:
                mcp_killed = _cleanup_orphaned_mcp_servers()
                if mcp_killed:
                    logger.info("Periodic sweep: cleaned %d orphaned MCP servers", mcp_killed)
            except Exception:
                pass

            # Sweep ACP agent processes tracked in session_pids.txt
            # but no longer in self._sessions or self._warm_pool (leaked by
            # failed reset/shutdown).  Warm pool PIDs are included in the
            # active set to prevent healthy pooled processes from being
            # killed as orphans.
            # Offloaded to a thread to avoid blocking the event loop with
            # os.kill, subprocess calls, and file I/O.
            try:
                active_pids, ok = _collect_active_pids(self._sessions)
                active_pids.update(self._pool_pids())
                if ok:
                    my_gw_pid = os.getpid()
                    # Phase 1 (thread): identify dead entries and orphan candidates.
                    # No killing happens here — keeps blocking I/O off the event loop.
                    killed_or_dead, candidates = await asyncio.to_thread(
                        _periodic_pid_sweep, my_gw_pid, active_pids
                    )
                    # Phase 2a (event loop): re-check candidates against
                    # live sessions and warm pool.  Deny-by-default: if any
                    # PID extraction fails, skip the kill phase (still prune
                    # dead entries).
                    confirmed: list[int] = []
                    if candidates:
                        current_pids, phase2_safe = _collect_active_pids(self._sessions)
                        current_pids.update(self._pool_pids())
                        if phase2_safe:
                            confirmed = [pid for pid in candidates if pid not in current_pids]
                    # Phase 2b (thread): kill confirmed orphans + writeback.
                    # Keeps blocking I/O (subprocess, fcntl.flock) off the
                    # event loop.
                    if confirmed or killed_or_dead:
                        orphan_killed = await asyncio.to_thread(
                            _kill_confirmed_and_writeback, my_gw_pid, confirmed, killed_or_dead
                        )
                        if orphan_killed:
                            logger.warning(
                                "Periodic sweep: killed %d orphaned ACP agent processes",
                                orphan_killed,
                            )
            except Exception:
                logger.debug("Orphan PID sweep failed", exc_info=True)

    def set_active_dashboard_sessions(self, session_keys: set[str]) -> None:
        """Update the set of active dashboard session keys.

        Called by the dashboard layer on session create/delete/resume/restore
        so that ``_expire_idle`` can immediately reap orphaned sessions
        whose UI tab no longer exists.
        """
        self._active_dashboard_sessions = set(session_keys)

    async def _expire_idle(self, timeout_secs: int) -> None:
        now = time.monotonic()
        expired: list[tuple[str, bool]] = []  # (key, is_orphan)
        total_checked = 0
        async with self._lock:
            for key, sess in self._sessions.items():
                if key in _PERSISTENT_KEYS:
                    continue
                if key.startswith(_CHANNEL_PREFIX):
                    continue
                # Goal loop workers are headless + supervised by the watchdog;
                # never idle/orphan-reap them (a long cycle would be killed
                # mid-turn). The watchdog ends them deterministically instead.
                if key.startswith(_LOOP_WORKER_PREFIX):
                    continue
                total_checked += 1
                idle = now - sess.last_used > timeout_secs
                orphaned = (
                    key.startswith("dashboard:")
                    and self._active_dashboard_sessions is not None
                    and key not in self._active_dashboard_sessions
                )
                if idle or orphaned:
                    expired.append((key, orphaned))
        if expired:
            logger.warning("Idle sweep: %d checked, %d expired", total_checked, len(expired))
        elif total_checked:
            logger.debug("Idle sweep: %d checked, 0 expired", total_checked)
        for key, is_orphan in expired:
            # NOTE: Small TOCTOU window — session could be re-activated between
            # orphan check (under lock) and reset() here. Accepted as benign:
            # worst case is session re-created on next user interaction.
            if is_orphan:
                logger.warning("Expiring orphaned dashboard session (session gone): %s", key)
            else:
                logger.warning("Expiring idle session: %s", key)
            Stats().inc_session_cleaned()
            # Give the ending session one last auto-skill-extraction pass
            # BEFORE reset wipes its transcript. Only for genuinely-idle sessions
            # (an orphaned tab-closed session is the same conversation a still-open
            # tab may resume; the idle poll already covers it). Never let a
            # consolidation failure block the cleanup that frees the session.
            if not is_orphan and self._on_session_expire is not None:
                try:
                    await self._on_session_expire(key)
                except Exception:
                    logger.warning("on_session_expire failed for %s", key, exc_info=True)
            # Use reset() instead of remove() to preserve session_map entry.
            # The ACP agent session file persists on disk — next get_or_create
            # can try session/load to restore full conversation history.
            await self.reset(key)
