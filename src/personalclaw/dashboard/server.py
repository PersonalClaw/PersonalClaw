"""Dashboard aiohttp application factory and startup."""

import asyncio
import errno
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from personalclaw.config import config_dir
from personalclaw.dashboard import (
    chat,
    handlers,
    handlers_inbox,
    ws,
)
from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes
from personalclaw.dashboard.origin import build_allowed_origins, check_origin, resolve_bind_host
from personalclaw.dashboard.state import _DEFAULT_PORT, DashboardState
from personalclaw.dashboard.token_auth import token_auth_middleware
from personalclaw.hooks import ScriptHookStore, set_global_hook_store
from personalclaw.suggestions import api_suggestions

if TYPE_CHECKING:
    from personalclaw.dashboard._types import (  # noqa: F401
        ContextBuilder,
        ConversationLog,
        HistoryConsolidator,
        LessonStore,
        ScheduleService,
        SessionManager,
        SubagentManager,
    )

logger = logging.getLogger(__name__)


def _single_post_ceiling() -> int:
    """Body-size ceiling for the MAIN + API apps.

    These apps carry only small single-POST uploads (≤ the policy's single-POST
    threshold) + every non-upload endpoint, so their ceiling tracks the threshold
    + multipart overhead — kept deliberately tight. Large media uploads go through
    the resumable protocol on the dedicated 2 GB upload sub-app, never these apps."""
    from personalclaw.uploads import single_post_threshold

    return single_post_threshold() + 16 * 1024 * 1024  # threshold + multipart overhead


_DIST_DIR = Path(__file__).resolve().parent.parent / "static" / "dist"

# How often to trim the security-event log (append-only + high-rate). Runs once
# at startup, then on this cadence, off the event loop (prune rewrites the file).
_SEL_PRUNE_INTERVAL_SECS = 6 * 60 * 60  # 6 hours


_UPLOAD_SWEEP_INTERVAL_SECS = 60 * 60  # hourly


async def _upload_sweep_loop() -> None:
    """Periodically delete abandoned resumable-upload session dirs (partial parts).

    A partial 2 GB upload the client never finishes would otherwise pin disk
    forever. Sweeps sessions idle past the store TTL, at startup then hourly."""
    from personalclaw import shutdown_event
    from personalclaw.dashboard.handlers.files import _UPLOAD_DIR
    from personalclaw.uploads.store import UploadStore

    store = UploadStore(Path(_UPLOAD_DIR) / ".parts")
    first = True
    while not shutdown_event.is_set():
        if not first:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=_UPLOAD_SWEEP_INTERVAL_SECS)
                return
            except asyncio.TimeoutError:
                pass
        first = False
        try:
            swept = await asyncio.get_running_loop().run_in_executor(None, store.sweep)
            if swept:
                logger.info("Upload sweep removed %d abandoned session(s)", swept)
        except Exception:
            logger.debug("upload sweep skipped", exc_info=True)


async def _sel_prune_loop() -> None:
    """Periodically trim the SEL audit log so it can't grow unbounded.

    Every gateway/channel/mcp action (incl. dashboard polls) appends an entry, so
    without this the file grows to millions of lines and the audit reads/verify
    crawl. Prunes once at startup, then every few hours, on an executor thread
    (the prune rewrites the whole file)."""
    from personalclaw import shutdown_event

    first = True
    while not shutdown_event.is_set():
        if not first:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=_SEL_PRUNE_INTERVAL_SECS)
                return  # shutdown signalled
            except asyncio.TimeoutError:
                pass
        first = False
        try:
            from personalclaw.sel import SecurityEventLog

            removed = await asyncio.get_running_loop().run_in_executor(
                None, SecurityEventLog().prune
            )
            if removed:
                logger.info("SEL prune removed %d entries", removed)
        except Exception:
            logger.debug("SEL prune skipped", exc_info=True)


def _precompute_telemetry(state: "DashboardState") -> None:
    """Pre-compute telemetry data (blocking I/O — call before server starts)."""
    from personalclaw.dashboard.handlers_system import _get_owner_hash, _get_static_system_info

    _log = logging.getLogger(__name__)
    try:
        _get_owner_hash(state)
    except Exception:
        _log.warning("Failed to pre-compute owner hash", exc_info=True)
    try:
        _get_static_system_info()
    except Exception:
        _log.warning("Failed to pre-compute system info", exc_info=True)


def _register_upload_routes(app: web.Application) -> None:
    """Register the resumable large-file upload protocol routes."""
    from personalclaw.dashboard.handlers import uploads as _up

    app.router.add_get("/api/uploads/limits", _up.api_uploads_limits)
    app.router.add_post("/api/uploads/init", _up.api_uploads_init)
    app.router.add_put("/api/uploads/{id}/part", _up.api_uploads_part)
    app.router.add_get("/api/uploads/{id}", _up.api_uploads_status)
    app.router.add_post("/api/uploads/{id}/complete", _up.api_uploads_complete)


def _register_mcp_routes(app: web.Application) -> None:
    """Register API routes used by MCP tools (spawn, lessons, crons, etc.)."""
    app.router.add_post("/api/spawn", handlers.api_spawn)
    app.router.add_get("/api/spawn", handlers.api_spawn_list)
    app.router.add_get("/api/spawn/{agent_id}", handlers.api_spawn_status)
    app.router.add_delete("/api/spawn/{agent_id}", handlers.api_spawn_delete)
    app.router.add_delete("/api/spawn", handlers.api_spawn_clear)
    app.router.add_get("/api/lessons", handlers.api_lessons)
    app.router.add_post("/api/lessons", handlers.api_lessons_create)
    app.router.add_delete("/api/lessons", handlers.api_lessons_delete)
    # Unified Triggers (schedule + lifecycle) — facade over the schedule service
    # + the script-hook store (see dashboard/handlers/triggers.py).
    from personalclaw.dashboard.handlers.triggers import register_trigger_routes

    register_trigger_routes(app)
    app.router.add_post("/api/send-message", handlers.api_send_message)
    app.router.add_post("/api/session-keepalive", handlers.api_session_keepalive)
    app.router.add_get("/api/session-tool-policy", handlers.api_session_tool_policy)
    app.router.add_post("/api/channel/profile", handlers.api_channel_profile)
    app.router.add_get("/api/notifications", handlers.api_notifications)
    app.router.add_post("/api/notifications/clear", handlers.api_notifications_clear)

    # Auto-nudge (feature-flagged ON by default — returns 503 when PERSONALCLAW_AUTONUDGE=0)
    from personalclaw.dashboard.handlers.autonudge import (
        api_autonudge_delete,
        api_autonudge_get,
        api_autonudge_list,
        api_autonudge_start,
        api_autonudge_update,
    )

    app.router.add_get("/api/autonudge", api_autonudge_list)
    app.router.add_post("/api/autonudge", api_autonudge_start)
    app.router.add_get("/api/autonudge/session/{session_name}", api_autonudge_get)
    app.router.add_patch("/api/autonudge/{loop_id}", api_autonudge_update)
    app.router.add_delete("/api/autonudge/{loop_id}", api_autonudge_delete)


async def _start_site(site: web.TCPSite, port: int) -> None:
    """Start *site*, translating EADDRINUSE into an actionable message."""
    try:
        await site.start()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            hint = (
                f"Port {port} already in use — is another PersonalClaw gateway running?\n"
                f"Stop it with: personalclaw stop  or  sudo systemctl stop personalclaw"
            )
            logger.error(hint)
            raise SystemExit(1) from exc
        raise


def _write_secret_file(secret_path: Path, secret: str) -> None:
    """Write *secret* to *secret_path* with mode 0o600.

    On failure the (possibly truncated) file is removed and the original
    ``OSError`` is re-raised.  Caller is responsible for any further
    cleanup (e.g. tearing down the app runner).
    """
    try:
        fd = os.open(str(secret_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # enforce perms even if file already exists
        with os.fdopen(fd, "w") as f:
            f.write(secret)
    except OSError:
        try:
            secret_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _apply_startup_yolo(state: DashboardState, cfg: Any) -> None:
    """Enable dashboard YOLO at startup if ``agent.yolo=true`` in config.

    Mirrors the channel gateway's startup behavior and
    emits an SEL audit event so config-driven permission changes are captured
    in the audit trail, matching the UI-toggle path in ``chat.py``.
    """
    if not cfg.agent.yolo:
        return
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="dashboard:startup",
            operation="mode_change:yolo",
            outcome="enabled",
            resources="config:agent.yolo",
        )
    except Exception:
        logger.error("SEL audit failed; refusing to enable YOLO mode from config", exc_info=True)
        return
    state.enable_yolo(from_config=True)
    logger.info("YOLO mode enabled at startup (agent.yolo=true)")


async def start_dashboard(
    sessions: "SessionManager",
    crons: "ScheduleService",
    lessons: "LessonStore",
    port: int = _DEFAULT_PORT,
    subagents: "SubagentManager | None" = None,
    context_builder: "ContextBuilder | None" = None,
    conversation_log: "ConversationLog | None" = None,
    consolidator: "HistoryConsolidator | None" = None,
    local_only: bool = True,
    configured_host: str = "",
    dashboard_url: str = "",
    owner_id: str = "",
) -> tuple[web.AppRunner, DashboardState]:
    """Start the dashboard web server.  Returns ``(runner, state)``."""
    # Auto-create consolidator if conversation_log available but no consolidator
    if consolidator is None and conversation_log is not None:
        try:
            from personalclaw import history as _hist_mod
            from personalclaw.memory import MemoryStore

            memory = context_builder.memory if context_builder else MemoryStore()
            if not context_builder:
                memory.init()
            consolidator = _hist_mod.HistoryConsolidator(
                log=conversation_log,
                memory=memory,
                sessions=sessions,
                lesson_store=lessons,
            )
            logger.info("Auto-created HistoryConsolidator for dashboard")
        except Exception:
            logger.debug("Could not create consolidator", exc_info=True)

    # Extract skills from a session one last time when it idles out, then sweep
    # its ephemeral session-scoped workflows (EVOLVE-WORKFLOWS #28), then evict its
    # per-session MCP connections (rel-mcp-server-pooling #46). Composed so each
    # step runs alongside the others, none replacing consolidation.
    if sessions is not None:
        from personalclaw.mcp_client import with_mcp_session_eviction
        from personalclaw.workflows.lifecycle import with_session_workflow_cleanup

        prior = consolidator.consolidate_session if consolidator is not None else None
        sessions.set_session_expire_callback(
            with_mcp_session_eviction(with_session_workflow_cleanup(prior))
        )

    state = DashboardState(
        sessions=sessions,
        crons=crons,
        lessons=lessons,
        start_time=time.time(),
        subagents=subagents,
        context_builder=context_builder,
        conversation_log=conversation_log,
        consolidator=consolidator,
        owner_id=owner_id,
    )

    # Initialize script hook store
    state._hook_store = ScriptHookStore()
    set_global_hook_store(state._hook_store)

    # Wire the always-on native inbox sink so agent post_to_inbox writes + pushes
    # through this state (works even with no polling inbox service).
    from personalclaw.inbox_providers.native_source import set_dashboard_state as _set_inbox_state

    _set_inbox_state(state)

    # Wire the native hook providers' service accessor (notify/send-message/
    # create-task reach DashboardState + a tracked background-spawn through it).
    def _spawn_background(coro: Any) -> Any:
        import asyncio as _asyncio

        task = _asyncio.ensure_future(coro)
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)
        return task

    from personalclaw.action_providers.services import ActionServices, set_action_services

    set_action_services(
        ActionServices(
            state=state,
            spawn_background=_spawn_background,
            subagents=state.subagents,
        )
    )

    # Wire script hooks into subagent tool execution path
    if state.subagents is not None:
        state.subagents.hook_store = state._hook_store

    # Visible notice + pct reset when auto-compaction fires on a dashboard session
    state.wire_session_compact_callback()

    app = web.Application(
        client_max_size=_single_post_ceiling()
    )  # small single-POST uploads only; large media → resumable upload sub-app
    app["state"] = state
    state.load_folders()
    state.load_tags()
    app["port"] = port
    from personalclaw.auth.modes import AuthConfig as _AuthConfig

    app["auth_cfg"] = _AuthConfig.from_env()

    _precompute_telemetry(state)

    # MCP tool routes (shared with start_api_server)
    _register_mcp_routes(app)

    # Install persistent log ring buffer (captures logs even when Logs page is closed)
    ring_handler = handlers.install_log_ring_handler()
    if ring_handler:
        ring_handler.set_state(state)

    # Page routes
    app.router.add_get("/", handlers.index)
    app.router.add_get("/claw.svg", handlers.favicon)

    # WebSocket (multiplexed real-time events)
    app.router.add_get("/api/ws", ws.api_ws)

    # Status / system
    app.router.add_get("/api/healthz", handlers.api_healthz)
    app.router.add_get("/api/status", handlers.api_status)
    app.router.add_get("/api/system", handlers.api_system)
    app.router.add_get("/api/auth-status", handlers.api_auth_status)
    app.router.add_get("/api/onboarding", handlers.api_onboarding)
    # Skills marketplace
    from personalclaw.dashboard.handlers.skills import (
        api_ephemeral_skill_discard,
        api_ephemeral_skill_promote,
        api_ephemeral_skills_list,
        api_skill_files,
        api_skill_proposal_accept,
        api_skill_proposal_detail,
        api_skill_proposal_reject,
        api_skill_proposals_list,
        api_skill_verify,
        api_skills_delete,
        api_skills_install,
        api_skills_list,
        api_skills_marketplace_detail,
        api_skills_marketplaces,
        api_skills_search,
    )

    app.router.add_get("/api/skills", api_skills_list)
    app.router.add_get("/api/skills/marketplaces", api_skills_marketplaces)
    app.router.add_get("/api/skills/search", api_skills_search)
    app.router.add_get("/api/skills/marketplace/detail", api_skills_marketplace_detail)
    app.router.add_post("/api/skills/install", api_skills_install)
    # Ephemeral session-skill drafts (skill-ephemeral-promotion) — literal
    # 'ephemeral' segment precedes the catch-all /{name} routes below.
    app.router.add_get("/api/skills/ephemeral/{session}", api_ephemeral_skills_list)
    app.router.add_post("/api/skills/ephemeral/{session}/promote", api_ephemeral_skill_promote)
    app.router.add_delete("/api/skills/ephemeral/{session}/{slug}", api_ephemeral_skill_discard)
    # Skill-proposals inbox (skill-evolution-proposal-only) — propose-only review.
    app.router.add_get("/api/skills/proposals", api_skill_proposals_list)
    app.router.add_get("/api/skills/proposals/{id}", api_skill_proposal_detail)
    app.router.add_post("/api/skills/proposals/{id}/accept", api_skill_proposal_accept)
    app.router.add_delete("/api/skills/proposals/{id}", api_skill_proposal_reject)
    # Provider-backed file browser — must precede the catch-all skill-detail GET.
    app.router.add_get("/api/skills/{name}/files", api_skill_files)
    app.router.add_post("/api/skills/{name}/verify", api_skill_verify)
    app.router.add_delete("/api/skills/{name}", api_skills_delete)

    # App Platform (A4) — lifecycle REST + backend reverse-proxy.
    from personalclaw.dashboard.handlers.apps import register_app_routes

    register_app_routes(app)
    from personalclaw.dashboard.handlers.providers import (
        api_agent_provider_agents,
        api_agent_providers_list,
        api_provider_create,
        api_provider_delete,
        api_provider_model_delete,
        api_provider_model_pull,
        api_provider_model_search,
        api_provider_model_show,
        api_provider_models,
        api_provider_test,
        api_provider_types,
        api_provider_update,
        api_providers_list,
    )

    app.router.add_get("/api/model-providers", api_providers_list)
    app.router.add_get("/api/model-provider-types", api_provider_types)
    app.router.add_get("/api/agent-providers", api_agent_providers_list)
    app.router.add_get("/api/agent-providers/{id}/agents", api_agent_provider_agents)
    app.router.add_post("/api/model-providers", api_provider_create)
    app.router.add_put("/api/model-providers/{name}", api_provider_update)
    app.router.add_delete("/api/model-providers/{name}", api_provider_delete)
    app.router.add_post("/api/model-providers/{name}/test", api_provider_test)
    app.router.add_get("/api/model-providers/{name}/models", api_provider_models)
    app.router.add_get("/api/model-providers/{name}/search", api_provider_model_search)
    app.router.add_get("/api/model-providers/{name}/show", api_provider_model_show)
    app.router.add_post("/api/model-providers/{name}/pull", api_provider_model_pull)
    app.router.add_post("/api/model-providers/{name}/models/delete", api_provider_model_delete)

    # Model registry (unified model discovery + active model assignments)
    from personalclaw.dashboard.handlers.model_registry import register_model_registry_routes

    register_model_registry_routes(app)

    # Search registry (the Search entity — providers + per-use-case bindings)
    from personalclaw.dashboard.handlers.search_registry import register_search_registry_routes

    register_search_registry_routes(app)

    # Async bundled-model downloads (embedding/STT/TTS) — one job/SSE path for all
    from personalclaw.dashboard.handlers.model_downloads import register_model_download_routes

    register_model_download_routes(app)

    # Embedding re-index jobs (triggered when the active embedding model changes)
    from personalclaw.dashboard.handlers.embedding_reindex import register_embedding_reindex_routes

    register_embedding_reindex_routes(app)

    # Suggestions (pre-computed contextual prompts)
    app.router.add_get("/api/suggestions", api_suggestions)

    # Memory
    app.router.add_get("/api/memory/preferences", handlers.api_memory_preferences)
    app.router.add_put("/api/memory/preferences", handlers.api_memory_preferences)
    app.router.add_get("/api/memory/projects", handlers.api_memory_projects)
    app.router.add_put("/api/memory/projects", handlers.api_memory_projects)
    app.router.add_get("/api/memory/history", handlers.api_memory_history)
    app.router.add_put("/api/memory/history", handlers.api_memory_history)
    app.router.add_get("/api/memory/settings", handlers.api_memory_settings)
    app.router.add_put("/api/memory/settings", handlers.api_memory_settings)

    # STT (Speech-to-Text) — the active model is set via /api/models/active; this
    # endpoint transcribes uploaded audio with it. Behavior lives in
    # use_case_settings/stt.json.
    app.router.add_post("/api/stt/transcribe", handlers.api_stt_transcribe)

    # STT provider management (list/delete/activate models)
    from personalclaw.stt.handlers import register_stt_routes

    register_stt_routes(app)

    # Lexicon / Vocabulary (LEX.6): terms + learned corrections
    from personalclaw.lexicon.handlers import register_lexicon_routes

    register_lexicon_routes(app)

    # Vector Memory (Semantic)
    app.router.add_get("/api/memory/semantic", handlers.api_memory_semantic)
    app.router.add_put("/api/memory/semantic", handlers.api_memory_semantic_write)
    app.router.add_delete("/api/memory/semantic/{key:.+}", handlers.api_memory_semantic_delete)
    app.router.add_get("/api/memory/events", handlers.api_memory_events)
    app.router.add_post("/api/memory/events/{event_id}/undo", handlers.api_memory_event_undo)
    app.router.add_get("/api/memory/lint", handlers.api_memory_lint)
    app.router.add_get("/api/memory/embedding-status", handlers.api_memory_embedding_status)
    app.router.add_post("/api/memory/enable-embeddings", handlers.api_memory_enable_embeddings)
    app.router.add_post("/api/memory/disable-embeddings", handlers.api_memory_disable_embeddings)
    app.router.add_get("/api/memory/embedding-models", handlers.api_memory_embedding_models)
    app.router.add_post("/api/memory/delete-model", handlers.api_memory_delete_model)
    app.router.add_post("/api/memory/activate-model", handlers.api_memory_activate_model)
    app.router.add_get("/api/memory/episodic/search", handlers.api_memory_episodic_search)
    app.router.add_get("/api/memory/recall", handlers.api_memory_recall)
    app.router.add_get("/api/memory/episodic", handlers.api_memory_episodic_list)
    app.router.add_delete("/api/memory/episodic/{id}", handlers.api_memory_episodic_delete)
    app.router.add_get("/api/memory/stats", handlers.api_memory_stats)
    app.router.add_get("/api/memory/vault", handlers.api_memory_vault_status)
    app.router.add_post("/api/memory/vault/sync", handlers.api_memory_vault_sync)
    app.router.add_get("/api/memory/daily-digests", handlers.api_memory_daily_digests)
    app.router.add_post("/api/memory/migrate", handlers.api_memory_migrate)
    app.router.add_post("/api/memory/import", handlers.api_memory_import)
    app.router.add_get("/api/memory/context-preview", handlers.api_memory_context_preview)
    app.router.add_post("/api/memory/consolidate", handlers.api_memory_consolidate)
    app.router.add_get("/api/session/archive", handlers.api_session_archive_list)
    app.router.add_get("/api/session/archive/{name}", handlers.api_session_archive_read)
    app.router.add_get("/api/memory/observability", handlers.api_memory_observability)
    app.router.add_get("/api/memory/graph", handlers.api_memory_graph)
    app.router.add_post("/api/memory/promote", handlers.api_memory_promote)

    # Crons, lessons, spawn, send-message, notifications
    # are registered via _register_mcp_routes() above.

    # Action providers (the action catalog) + agent-scoped lifecycle view. The
    # lifecycle-trigger CRUD lives under /api/triggers now (registered above).
    app.router.add_get("/api/action-providers", handlers.api_action_providers)
    app.router.add_get("/api/agent-hooks", handlers.api_agent_hooks)

    # Prompts (Agent SOPs)
    app.router.add_get("/api/prompts", handlers.api_prompts)
    app.router.add_post("/api/prompts", handlers.api_prompt_create)
    # Bindings routes registered BEFORE the {name:.+} catch-all so the literal
    # path isn't swallowed by the prompt-detail matcher.
    app.router.add_get("/api/prompts/bindings", handlers.api_prompt_bindings)
    app.router.add_put("/api/prompts/bindings", handlers.api_prompt_bindings_save)
    # Live authoring helpers — literal paths registered BEFORE the {name:.+}
    # catch-all so they aren't swallowed by the prompt-detail matcher.
    app.router.add_post("/api/prompts/preview", handlers.api_prompt_preview)
    app.router.add_get("/api/prompts/syntax", handlers.api_prompt_syntax)
    app.router.add_post("/api/prompts/{name:.+}/render", handlers.api_prompt_render)
    # Runnable "campaign template" launch (#17) — render + create + start a loop. Sits
    # with the other {name:.+}/<verb> routes, BEFORE the bare {name:.+} catch-all.
    app.router.add_post("/api/prompts/{name:.+}/launch", handlers.api_campaign_template_launch)
    app.router.add_put("/api/prompts/{name:.+}", handlers.api_prompt_save)
    app.router.add_delete("/api/prompts/{name:.+}", handlers.api_prompt_delete)
    app.router.add_get("/api/prompts/{name:.+}", handlers.api_prompt_detail)

    # Prompt snippets — reusable {{> name}} fragments. A distinct path tree so it's
    # not swallowed by the /api/prompts/{name:.+} catch-all above.
    app.router.add_get("/api/prompt-snippets", handlers.api_snippets)
    app.router.add_post("/api/prompt-snippets", handlers.api_snippet_create)
    app.router.add_post("/api/prompt-snippets/{name:.+}/render", handlers.api_snippet_render)
    app.router.add_put("/api/prompt-snippets/{name:.+}", handlers.api_snippet_save)
    app.router.add_delete("/api/prompt-snippets/{name:.+}", handlers.api_snippet_delete)
    app.router.add_get("/api/prompt-snippets/{name:.+}", handlers.api_snippet_detail)

    # Skills (CRUD detail — list/search/install are handled by the marketplace routes above)
    app.router.add_post("/api/skills", handlers.api_skills_create)
    app.router.add_get("/api/skills/{name:.+}", handlers.api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", handlers.api_skill_detail)

    # Custom Themes (CRUD)
    app.router.add_get("/api/themes", handlers.api_themes)
    app.router.add_post("/api/themes", handlers.api_themes_create)
    app.router.add_get("/api/themes/{slug}", handlers.api_theme_detail)
    app.router.add_put("/api/themes/{slug}", handlers.api_theme_detail)
    app.router.add_delete("/api/themes/{slug}", handlers.api_theme_detail)

    # Agent config
    app.router.add_get("/api/agent/config", handlers.api_agent_config)
    app.router.add_put("/api/agent/config", handlers.api_agent_config)
    app.router.add_get("/api/config/default-agent", handlers.api_default_agent)
    app.router.add_put("/api/config/default-agent", handlers.api_default_agent)
    app.router.add_get("/api/config/schema", handlers.api_config_schema)
    app.router.add_get("/api/config/personalclaw", handlers.api_personalclaw_config)
    app.router.add_put("/api/config/personalclaw", handlers.api_personalclaw_config)
    app.router.add_patch("/api/config/personalclaw", handlers.api_personalclaw_config_patch)
    app.router.add_get("/api/dashboard/config", handlers.api_dashboard_config)
    app.router.add_put("/api/dashboard/config", handlers.api_dashboard_config)

    # MCP servers
    app.router.add_get("/api/mcp", handlers.api_mcp_servers)
    app.router.add_get("/api/mcp/active", handlers.api_mcp_active)
    app.router.add_post("/api/mcp/probe", handlers.api_mcp_probe)
    app.router.add_get("/api/mcp/probe", handlers.api_mcp_probe_cached)
    app.router.add_post("/api/mcp/probe/{name}", handlers.api_mcp_probe_one)
    app.router.add_get("/api/mcp/pool-stats", handlers.api_mcp_pool_stats)
    app.router.add_get("/api/mcp/importable", handlers.api_mcp_importable)
    app.router.add_post("/api/mcp/sync", handlers.api_mcp_sync)
    app.router.add_post("/api/mcp/apply", handlers.api_mcp_apply)
    app.router.add_post("/api/mcp/toggle", handlers.api_mcp_toggle)
    app.router.add_post("/api/mcp/toggle-tool", handlers.api_mcp_toggle_tool)
    app.router.add_post("/api/mcp/toggle-all", handlers.api_mcp_toggle_all)
    app.router.add_post("/api/mcp/remove", handlers.api_mcp_remove)
    # REST-style MCP server registration
    app.router.add_put("/api/mcp/servers/{name}", handlers.api_mcp_server_detail)
    app.router.add_delete("/api/mcp/servers/{name}", handlers.api_mcp_server_detail)
    # Skills marketplace integration

    # Chat
    app.router.add_post("/api/chat", chat.api_chat)
    app.router.add_get("/api/chat/sessions", chat.api_chat_sessions)
    app.router.add_post("/api/chat/sessions", chat.api_chat_session_create)
    app.router.add_post("/api/chat/sessions/cleanup", chat.api_chat_sessions_cleanup)
    app.router.add_get("/api/chat/sessions/{session}", chat.api_chat_session_detail)
    app.router.add_get("/api/chat/sessions/{session}/tool-result/{rid}", chat.api_chat_tool_result)
    app.router.add_post("/api/chat/sessions/{session}/stop", chat.api_chat_session_stop)
    app.router.add_post("/api/chat/sessions/{session}/interrupt", chat.api_chat_session_interrupt)
    app.router.add_delete(
        "/api/chat/sessions/{session}/queue/{queue_id}", chat.api_chat_session_queue_cancel
    )
    app.router.add_delete("/api/chat/sessions/{session}", chat.api_chat_session_delete)
    app.router.add_post("/api/chat/sessions/{session}/agent", chat.api_chat_session_agent)
    app.router.add_post("/api/chat/sessions/{session}/acp-agent", chat.api_chat_session_acp_agent)

    # Optimizer
    app.router.add_post("/api/optimizer/optimize", handlers.handle_optimize)
    app.router.add_post("/api/chat/sessions/{session}/model", chat.api_chat_session_model)
    app.router.add_post(
        "/api/chat/sessions/{session}/reasoning-effort", chat.api_chat_session_reasoning_effort
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir", chat.api_chat_session_workspace_dir
    )
    app.router.add_get("/api/recent-projects", chat.api_recent_projects)
    app.router.add_patch("/api/chat/sessions/{session}/color", chat.api_chat_session_color)
    # Context injection (App Kit — silent background context)
    app.router.add_post("/api/chat/sessions/{session}/context", chat.api_chat_session_context)
    app.router.add_post("/api/chat/sessions/{session}/fork", chat.api_chat_session_fork)
    app.router.add_post("/api/chat/sessions/{session}/undo", chat.api_chat_session_undo)
    # Side chat (ephemeral, isolated Q&A against a frozen parent snapshot)
    app.router.add_post("/api/chat/sessions/{session}/side/open", chat.api_side_open)
    app.router.add_post("/api/chat/sessions/{session}/side/turn", chat.api_side_turn)
    app.router.add_post("/api/chat/sessions/{session}/side/close", chat.api_side_close)
    # Agents
    app.router.add_get("/api/agents/installed", handlers.api_agents_installed)
    app.router.add_get("/api/slash-commands", handlers.api_slash_commands)
    app.router.add_get("/api/agents/detail/{name}", handlers.api_agent_detail)
    app.router.add_patch("/api/agents/detail/{name}", handlers.api_agent_detail)
    app.router.add_delete("/api/agents/detail/{name}", handlers.api_agent_detail)
    # PersonalClaw Agent CRUD
    app.router.add_get("/api/agents", handlers.api_personalclaw_agents)
    app.router.add_post("/api/agents", handlers.api_personalclaw_agents_create)
    app.router.add_post("/api/agents/sync", handlers.api_personalclaw_agents_sync)
    app.router.add_put("/api/agents/{name}", handlers.api_personalclaw_agent_update)
    app.router.add_delete("/api/agents/{name}", handlers.api_personalclaw_agent_delete)
    # Agent marketplace — local filesystem + extensible registry
    from personalclaw.dashboard.handlers.agent_marketplace import (
        api_agent_marketplace_activate,
        api_agent_marketplace_create,
        api_agent_marketplace_delete,
        api_agent_marketplace_get,
        api_agent_marketplace_list,
        api_agent_marketplace_list_marketplaces,
        api_agent_marketplace_test,
        api_agent_marketplace_update,
    )

    app.router.add_get(
        "/api/agent-marketplace/marketplaces", api_agent_marketplace_list_marketplaces
    )
    app.router.add_get("/api/agent-marketplace/agents", api_agent_marketplace_list)
    app.router.add_post("/api/agent-marketplace/agents", api_agent_marketplace_create)
    app.router.add_get("/api/agent-marketplace/agents/{name}", api_agent_marketplace_get)
    app.router.add_put("/api/agent-marketplace/agents/{name}", api_agent_marketplace_update)
    app.router.add_delete("/api/agent-marketplace/agents/{name}", api_agent_marketplace_delete)
    app.router.add_post(
        "/api/agent-marketplace/agents/{name}/activate", api_agent_marketplace_activate
    )
    app.router.add_post("/api/agent-marketplace/agents/{name}/test", api_agent_marketplace_test)
    # Agent metadata
    app.router.add_get("/api/agent-metadata/{name}", handlers.api_agent_metadata_get)
    app.router.add_put("/api/agent-metadata/{name}", handlers.api_agent_metadata_put)
    app.router.add_delete("/api/agent-metadata/{name}", handlers.api_agent_metadata_delete)
    # Session workspace (Orchestrated Chat)
    app.router.add_get("/api/sessions/{id}/agents", handlers.api_session_agents_list)
    app.router.add_get("/api/sessions/{id}/agents/{agent_id}", handlers.api_session_agent_result)
    app.router.add_get(
        "/api/sessions/{id}/agents/{agent_id}/stream", handlers.api_session_agent_stream
    )
    app.router.add_post("/api/chat/sessions/{session}/resume", chat.api_chat_session_resume)
    app.router.add_post("/api/chat/sessions/{session}/approve", chat.api_chat_session_approve)
    app.router.add_post("/api/chat/mode", chat.api_chat_mode)
    app.router.add_post("/api/chat/task-mode", chat.api_chat_task_mode)
    app.router.add_post("/api/chat/nav/resolve-links", chat.api_nav_resolve_links)
    app.router.add_post(
        "/api/chat/sessions/{session}/generate-title", chat.api_chat_session_generate_title
    )
    app.router.add_patch("/api/chat/sessions/{session}/title", chat.api_chat_session_rename)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", chat.api_chat_session_regenerate)
    app.router.add_post(
        "/api/chat/sessions/{session}/switch-variant", chat.api_chat_session_switch_variant
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/edit-resend", chat.api_chat_session_edit_resend
    )
    # Folders
    app.router.add_get("/api/chat/folders", chat.api_chat_folders)
    app.router.add_post("/api/chat/folders", chat.api_chat_folder_create)
    app.router.add_patch("/api/chat/folders/{id}", chat.api_chat_folder_update)
    app.router.add_delete("/api/chat/folders/{id}", chat.api_chat_folder_delete)
    app.router.add_patch("/api/chat/sessions/{session}/folder", chat.api_chat_session_folder)
    app.router.add_patch("/api/chat/sessions/{session}/pin", chat.api_chat_session_pin)
    # Tags
    app.router.add_get("/api/chat/tags", chat.api_chat_tags)
    app.router.add_post("/api/chat/tags", chat.api_chat_tag_create)
    app.router.add_patch("/api/chat/tags/{id}", chat.api_chat_tag_update)
    app.router.add_delete("/api/chat/tags/{id}", chat.api_chat_tag_delete)
    app.router.add_put("/api/chat/sessions/{session}/tags", chat.api_chat_session_tags)
    app.router.add_post("/api/chat/sessions/{session}/drop", chat.api_chat_session_drop)
    # Magic re-tag — batch AI re-evaluation of every session's tags (board's
    # sparkle button). Progress streams over /api/ws (retag_progress/retag_done).
    from personalclaw.dashboard import chat_retag

    app.router.add_post("/api/sessions/retag-all", chat_retag.api_retag_all)
    app.router.add_get("/api/sessions/retag-all", chat_retag.api_retag_status)
    app.router.add_post("/api/sessions/retag-all/cancel", chat_retag.api_retag_cancel)
    app.router.add_get("/api/chat/tag-columns", chat.api_chat_tag_columns)
    app.router.add_post("/api/chat/tag-columns", chat.api_chat_tag_column_create)
    app.router.add_put("/api/chat/tag-columns/order", chat.api_chat_tag_columns_reorder)
    app.router.add_patch("/api/chat/tag-columns/{id}", chat.api_chat_tag_column_update)
    app.router.add_delete("/api/chat/tag-columns/{id}", chat.api_chat_tag_column_delete)
    app.router.add_post("/api/voice/synthesize", chat.api_voice_synthesize)
    app.router.add_post("/api/chat/sessions/{session}/handoff", chat.api_chat_session_handoff)
    app.router.add_post(
        "/api/chat/sessions/{session}/channel-link", chat.api_chat_session_channel_link
    )
    app.router.add_get("/api/channels/reply-targets", chat.api_channel_reply_targets)

    app.router.add_post("/api/reveal", handlers.api_reveal_path)
    app.router.add_get("/api/file-read", handlers.api_file_read)
    app.router.add_get("/api/file-raw", handlers.api_file_raw)
    app.router.add_get("/api/file-watch", handlers.api_file_watch)
    app.router.add_get("/api/config-fs/stream", handlers.api_config_fs_watch)
    app.router.add_post("/api/file-write", handlers.api_file_write)
    app.router.add_get("/api/file-search", handlers.api_file_search)
    app.router.add_get("/api/file-list", handlers.api_file_list)
    app.router.add_get("/api/file-git-status", handlers.api_file_git_status)
    app.router.add_get("/api/file-git-log", handlers.api_file_git_log)
    app.router.add_get("/api/file-git-commit", handlers.api_file_git_commit)
    app.router.add_get("/api/file-git-original", handlers.api_file_git_original)
    app.router.add_get("/api/file-content-search", handlers.api_file_content_search)
    app.router.add_get("/api/file-complete", handlers.api_file_complete)
    app.router.add_post("/api/file-create", handlers.api_file_create)
    app.router.add_post("/api/file-move", handlers.api_file_move)
    app.router.add_post("/api/file-delete", handlers.api_file_delete)
    app.router.add_post("/api/file-upload", handlers.api_file_upload)
    app.router.add_get("/api/browse-dirs", handlers.api_browse_dirs)
    app.router.add_post("/api/create-dir", handlers.api_create_dir)
    app.router.add_post("/api/upload", handlers.api_upload)
    app.router.add_post("/api/upload/file", handlers.api_upload_file)
    # Resumable large-file upload protocol (init/part/status/complete). The part
    # bodies stream via request.content, which bypasses client_max_size (that only
    # gates buffered .read()/.post()) — so a 2 GB upload flows through the tight
    # main-app ceiling without relaxing it for any buffered endpoint. Registered
    # here rather than on a sub-app because an aiohttp sub-app's client_max_size is
    # ignored (the request is created with the TOP app's limit); streaming is the
    # real isolation, not a sub-app.
    _register_upload_routes(app)
    app.router.add_get("/api/attachment-extract", handlers.api_attachment_extract)
    app.router.add_post("/api/channel/upload-file", handlers.api_channel_upload_file)
    app.router.add_post("/api/outbox/notify", handlers.api_outbox_notify)
    app.router.add_get("/api/outbox", handlers.api_outbox_list)
    app.router.add_get("/api/outbox/{filename}", handlers.api_outbox_download)
    app.router.add_post("/api/screenshot", handlers.api_screenshot)

    # Portability (export/import config+memory as zip)
    app.router.add_get("/api/portability/export", handlers.api_portability_export)
    app.router.add_post("/api/portability/import", handlers.api_portability_import)
    app.router.add_post("/api/portability/preview", handlers.api_portability_preview)

    # Terminal (CLI panel)
    app.router.add_get("/api/ws/terminal/{session_id}", handlers.api_terminal_ws)
    app.router.add_post("/api/terminal/sessions", handlers.api_terminal_create)
    app.router.add_get("/api/terminal/sessions", handlers.api_terminal_list)
    app.router.add_delete("/api/terminal/sessions/{session_id}", handlers.api_terminal_delete)

    # Channels (comms transports) — management surface over registered transports
    from personalclaw.dashboard.handlers.channels import (
        api_channel_connect,
        api_channel_disconnect,
        api_channel_get,
        api_channel_test,
        api_channels_list,
    )

    app.router.add_get("/api/channels", api_channels_list)
    app.router.add_get("/api/channels/{name}", api_channel_get)
    app.router.add_post("/api/channels/{name}/connect", api_channel_connect)
    app.router.add_post("/api/channels/{name}/disconnect", api_channel_disconnect)
    app.router.add_post("/api/channels/{name}/test", api_channel_test)

    # Tools — aggregated listing from all tool providers
    from personalclaw.dashboard.handlers.tools import (
        api_providers_toggle,
        api_tool_invoke,
        api_tools_list,
        api_tools_toggle,
    )

    app.router.add_get("/api/tools", api_tools_list)
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    app.router.add_post("/api/tools/toggle", api_tools_toggle)
    app.router.add_post("/api/tools/provider-toggle", api_providers_toggle)

    # Tasks — first-class entity with provider-based aggregation
    from personalclaw.tasks.handlers import register_task_routes

    register_task_routes(app)

    # Workflows — stateless scoped SOP definitions
    from personalclaw.workflows.handlers import register_workflow_routes

    register_workflow_routes(app)

    # The unified Loop engine — ONE /api/loops route family for every kind
    # (general/goal/code/design). Replaces the legacy /api/loops + /api/code routes
    # at the cutover (Slice 2e): the legacy loops/ + code/ packages are deleted.
    from personalclaw.dashboard.handlers.loop_routes import register_unified_loop_routes

    register_unified_loop_routes(app)

    # Artifacts — first-class entity (named/versioned LLM content) over a provider
    from personalclaw.artifacts.handlers import register_artifact_routes

    register_artifact_routes(app)

    # Inbox
    app.router.add_get("/api/inbox", handlers_inbox.api_inbox_list)
    app.router.add_get("/api/inbox/pending", handlers_inbox.api_inbox_pending)
    app.router.add_get("/api/inbox/status", handlers_inbox.api_inbox_status)
    app.router.add_post("/api/inbox/restart", handlers_inbox.api_inbox_restart)
    app.router.add_post("/api/inbox/dismiss-all", handlers_inbox.api_inbox_dismiss_all)
    app.router.add_post("/api/inbox/send", handlers_inbox.api_inbox_send)
    app.router.add_put("/api/inbox/{id}", handlers_inbox.api_inbox_update)
    app.router.add_post("/api/inbox/{id}/draft", handlers_inbox.api_inbox_draft)
    app.router.add_post("/api/inbox/{id}/open", handlers_inbox.api_inbox_open)
    app.router.add_post("/api/inbox/{id}/favorite", handlers_inbox.api_inbox_favorite)
    app.router.add_get("/api/inbox/digest", handlers_inbox.api_inbox_digest)
    app.router.add_get("/api/inbox/providers", handlers_inbox.api_inbox_providers)

    # Notifications (GET/clear registered in _register_mcp_routes; the rest here)
    app.router.add_delete("/api/notifications", handlers.api_notification_delete)
    app.router.add_post("/api/notifications/ack", handlers.api_notification_ack)
    app.router.add_post("/api/notifications/unack", handlers.api_notification_unack)
    app.router.add_post("/api/notifications/ack-all", handlers.api_notifications_ack_all)
    app.router.add_get("/api/update/check", handlers.api_update_check)
    app.router.add_get("/api/changelog", handlers.api_changelog)
    app.router.add_post("/api/update", handlers.api_update_apply)
    app.router.add_post("/api/update/auto", handlers.api_update_auto)
    app.router.add_post("/api/update/dev-mode", handlers.api_update_dev_mode)
    app.router.add_post("/api/update/cancel", handlers.api_update_cancel)
    # Restart-only (no git pull) — apply committed backend changes. GET-less:
    # ?probe=1 returns the active-work snapshot for the confirm gate.
    app.router.add_post("/api/system/restart", handlers.api_restart)
    # Only expose the simulation endpoint in dev/debug environments
    _truthy = {"1", "true", "yes", "on"}
    if (
        os.environ.get("PERSONALCLAW_HOME", "").endswith("-dev")
        or os.environ.get("PERSONALCLAW_DEV_MODE", "").lower() in _truthy
    ):
        app.router.add_post("/api/update/simulate", handlers.api_update_simulate)
    app.router.add_get("/api/sessions", handlers.api_sessions)
    app.router.add_delete("/api/sessions", handlers.api_sessions_clear)
    app.router.add_get("/api/sessions/context", handlers.api_sessions_context)
    app.router.add_get("/api/sessions/health", handlers.api_sessions_health)
    app.router.add_post("/api/sessions/restart", handlers.api_sessions_restart)
    # NOTE: /search must be registered before /{key} to avoid the path param catching "search"
    app.router.add_get("/api/sessions/search", handlers.api_sessions_search)
    app.router.add_get("/api/sessions/{key}", handlers.api_session_detail)
    app.router.add_delete("/api/sessions/{key}", handlers.api_session_delete)
    app.router.add_get("/api/logs", handlers.api_logs)
    app.router.add_get("/api/logs/level", handlers.api_log_level_get)
    app.router.add_post("/api/logs/level", handlers.api_log_level)
    app.router.add_get("/api/sel/events", handlers.api_sel_events)
    app.router.add_get("/api/sel/verify", handlers.api_sel_verify)
    app.router.add_post("/api/sel/rotate", handlers.api_sel_rotate)
    app.router.add_get("/api/security/stats", handlers.api_security_stats)
    app.router.add_get("/api/security/denied-commands", handlers.api_security_denied_commands)
    app.router.add_get("/api/security/egress", handlers.api_security_egress)
    app.router.add_get("/api/approvals", handlers.api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", handlers.api_approval_resolve)

    # Local token bootstrap (file-based secret auth in handler, bypasses middleware)
    app.router.add_get("/api/token/local", handlers.api_token_local)

    # Session revocation (called by `personalclaw logout` CLI)
    app.router.add_post("/api/logout", handlers.api_logout)

    # Webhook hooks (external triggers)
    app.router.add_post("/api/hooks/agent", handlers.api_hooks_agent)

    # Extension system — discover and register provider extensions
    from personalclaw.providers.entity_routes import register_entity_routes
    from personalclaw.providers.instance_routes import register_instance_routes
    from personalclaw.providers.loader import load_all_extensions
    from personalclaw.providers.routes import register_routes as register_extension_routes

    load_all_extensions()
    # Sync config.json provider entries into the LLM registry IMMEDIATELY after
    # extensions load (types are now registered). Must happen BEFORE any handler
    # resolves a provider (e.g. embedding/knowledge auto-embed at boot).
    from personalclaw.llm.registry import sync_entries_from_config
    from personalclaw.providers.use_cases import migrate_legacy_bindings

    try:
        migrate_legacy_bindings()
    except Exception:
        pass
    sync_entries_from_config()
    register_extension_routes(app)
    register_instance_routes(app)
    register_entity_routes(app)

    # Knowledge Library
    setup_knowledge_routes(app)

    async def _transports_startup(app_: web.Application) -> None:
        """Register the always-present in-app Web UI transport at boot.

        Extension-backed transports (Slack, and future Telegram/Discord) are
        registered by the provider registry's ChannelTypeHandler when their
        extension is enabled — one source of truth, no parallel startup path.
        """
        from personalclaw.channel_transports import register_default_transports

        try:
            register_default_transports()
        except Exception:
            logger.exception("Failed to register the Web UI channel transport")

    app.on_startup.append(_transports_startup)

    async def _mcp_migrate_startup(app_: web.Application) -> None:
        """UT3: fold any legacy ``settings/mcp.json`` content into the canonical
        ``~/.personalclaw/mcp.json`` once, so the dual store can't re-diverge."""
        from personalclaw.dashboard.handlers.mcp import _migrate_legacy_mcp_json

        try:
            _migrate_legacy_mcp_json()
        except Exception:
            logger.exception("Failed to migrate legacy mcp.json")

    app.on_startup.append(_mcp_migrate_startup)

    async def _action_providers_startup(app_: web.Application) -> None:
        """Register the bundled action providers (bash, webhook, run-script, …)."""
        from personalclaw.action_providers.registry import _ensure_default_providers_registered

        try:
            _ensure_default_providers_registered()
        except Exception:
            logger.exception("Failed to register action providers")

    app.on_startup.append(_action_providers_startup)

    async def _prompt_providers_startup(app_: web.Application) -> None:
        """Register the bundled native filesystem prompt provider."""
        from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

        try:
            _ensure_default_providers_registered()
        except Exception:
            logger.exception("Failed to register prompt providers")

    app.on_startup.append(_prompt_providers_startup)

    async def _projection_rules_startup(app_: web.Application) -> None:
        """Install the user's tool-output projection rules (TokenJuice OP6) into the
        projection engine so a large output of a user-taught type keeps its salient
        slice instead of a blunt cut. Fail-soft — a bad rule is skipped, never fatal."""
        try:
            from personalclaw.config.loader import AppConfig
            from personalclaw.tool_providers import projection

            projection.set_user_rules(
                [
                    projection.ProjectionRule(
                        name=r.name, match_regex=r.match_regex, strategy=r.strategy
                    )
                    for r in AppConfig.load().tools.projection_rules
                ]
            )
        except Exception:
            logger.exception("Failed to install tool-output projection rules")

    app.on_startup.append(_projection_rules_startup)

    async def _model_providers_startup(app_: web.Application) -> None:
        """Replay config.json providers[] into the model ProviderRegistry.

        Provider entries are persisted by the create/update handlers but are
        not re-registered on a fresh start, so without this a configured model
        provider is invisible to chat resolution until re-created via the API.
        """
        from personalclaw.llm.registry import sync_entries_from_config
        from personalclaw.providers.use_cases import migrate_legacy_bindings

        try:
            migrate_legacy_bindings()
        except Exception:
            logger.exception("Failed to migrate legacy use-case bindings")
        try:
            sync_entries_from_config()
        except Exception:
            logger.exception("Failed to sync model providers from config")
        try:
            from personalclaw.local_models.registry import register_config_model_managers

            register_config_model_managers()
        except Exception:
            logger.exception("Failed to register config model-managers as local providers")

    app.on_startup.append(_model_providers_startup)

    async def _resume_interrupted_reindex_startup(app_: web.Application) -> None:
        """Auto-resume an INTERRUPTED or model-swap-orphaned embedding re-index.

        Switching the embedding model nulls the old (incompatible) vectors, then
        re-embeds every item. If the gateway died mid-re-index (crash/kill/OOM), items
        are left with text but no embedding OR — if it died after a model SWAP but
        before re-embed — with an old WRONG-DIMENSION vector. Either way the store sits
        silently unsearchable against the active model (retrieval skips dim mismatches)
        with no recovery. On boot, once the active embedding model is resolvable, detect
        BOTH states (missing OR stale-dim vectors) and finish the re-index automatically.
        Runs AFTER _model_providers_startup so the embedder is wired; fully best-effort —
        never blocks or crashes startup."""
        try:
            state = app_["state"]
            ks = getattr(state, "knowledge_store", None)
            if ks is None:
                return
            # Need the active model's dim to detect STALE (wrong-dim) vectors, not just
            # missing ones — so resolve the embedder first, then count.
            from personalclaw.dashboard.handlers.embedding_reindex import _resolve_embed

            embedder, embed_fn, model = _resolve_embed(app_)
            _dim = getattr(embedder, "dim", None) if embedder is not None else None
            active_dim = _dim() if callable(_dim) else None
            needing = ks.count_items_needing_reembed(active_dim)
            if needing <= 0:
                return  # store is whole (or empty) — nothing to resume
            if embed_fn is None:
                logger.warning(
                    "Embedding re-index needed: %d knowledge item(s) missing/stale "
                    "vectors, but the active embedding model (%s) isn't ready — the "
                    "store stays keyword-searchable; re-run once the model is available.",
                    needing,
                    model or "none",
                )
                return
            from personalclaw.dashboard.handlers.memory import _get_provider

            vector_store = _get_provider(state)
            job, error = state.embedding_reindex().start(
                model=model,
                knowledge_store=ks,
                vector_store=vector_store,
                embedder=embedder,
                embed_fn=embed_fn,
            )
            if error:
                logger.warning("Auto-resume re-index refused: %s", error)
            else:
                logger.info(
                    "Auto-resuming embedding re-index (%d item(s) missing/stale "
                    "vectors) with model %s [job %s]",
                    needing,
                    model,
                    getattr(job, "id", "?"),
                )
        except Exception:
            logger.exception("Failed to check/resume interrupted embedding re-index")

    app.on_startup.append(_resume_interrupted_reindex_startup)

    async def _warm_acp_pool_startup(app_: web.Application) -> None:
        """Start the ACP live-connection pool: one warmed connection per ready
        runtime, serving BOTH the discovery snapshot (instant lists) AND the first
        chat turn (instant first turn — claimed in get_or_create). Warming runs in
        the BACKGROUND (each is a ~15-20s live session); the pool also starts a
        health loop that respawns dead connections. Runs after
        _model_providers_startup so the acp_agent entries are registered.
        Best-effort — failures never affect the gateway."""
        try:
            import asyncio as _asyncio

            from personalclaw.acp.connection_pool import init_acp_pool
            from personalclaw.dashboard.handlers.providers import warm_readiness_cache

            st = app_.get("state")
            start_sem = getattr(getattr(st, "sessions", None), "_start_sem", None)
            if start_sem is None:
                start_sem = _asyncio.Semaphore(4)
            await init_acp_pool(start_sem)

            # Also warm the readiness-probe cache for runtimes the pool can't warm
            # (e.g. codex's slow-failing npx probe), in the background, so the first
            # /api/agent-providers call the chat picker makes isn't blocked on it.
            async def _warm_readiness() -> None:
                try:
                    await warm_readiness_cache()
                except Exception:
                    logger.debug("ACP readiness warm failed", exc_info=True)

            _asyncio.ensure_future(_warm_readiness())
        except Exception:
            logger.debug("ACP pool startup failed", exc_info=True)

    app.on_startup.append(_warm_acp_pool_startup)

    async def _acp_pool_shutdown(app_: web.Application) -> None:
        """Drain + shut down all pooled ACP connections on gateway stop."""
        try:
            from personalclaw.acp.connection_pool import get_acp_pool, set_acp_pool

            pool = get_acp_pool()
            if pool is not None:
                await pool.shutdown()
                set_acp_pool(None)
        except Exception:
            logger.debug("ACP pool shutdown failed", exc_info=True)

    app.on_cleanup.append(_acp_pool_shutdown)

    async def _mcp_client_shutdown(app_: web.Application) -> None:
        """Stop the idle sweeper + drain all live MCP connections on gateway stop
        (rel-mcp-server-pooling #46)."""
        try:
            from personalclaw.mcp_client import get_mcp_client_registry

            reg = get_mcp_client_registry()
            if reg is not None:
                await reg.shutdown_all()
        except Exception:
            logger.debug("MCP client shutdown failed", exc_info=True)

    app.on_cleanup.append(_mcp_client_shutdown)

    async def _app_backends_shutdown(app_: web.Application) -> None:
        """Terminate every app-backend subprocess on gateway stop. Without this the
        backends (snippet-lab/standup-notes/… server.py) were spawned on enable but
        never reaped on shutdown — so each gateway restart ORPHANED another set
        (reparented to init), leaking dozens of processes over a dev session."""
        try:
            from personalclaw.apps.backend_runtime import get_backend_supervisor

            get_backend_supervisor().stop_all()
        except Exception:
            logger.debug("app-backend shutdown failed", exc_info=True)

    app.on_cleanup.append(_app_backends_shutdown)

    # Static files — React build under /assets, packaged static assets under /static
    if _DIST_DIR.is_dir():
        app.router.add_static(
            "/assets",
            _DIST_DIR / "assets" if (_DIST_DIR / "assets").is_dir() else _DIST_DIR,
            show_index=False,
            append_version=True,
        )
        if (_DIST_DIR / "sprites").is_dir():
            app.router.add_static("/sprites", _DIST_DIR / "sprites", show_index=False)
        # Web fonts referenced at the absolute path /fonts/*.woff2 by fonts.css. Without
        # this route they fell through to the SPA catch-all (→ index.html, decoded as a
        # font → "invalid sfntVersion"), so the app silently rendered in system-font
        # fallbacks instead of Google Sans Flex/Code (incl. the code editor's mono).
        if (_DIST_DIR / "fonts").is_dir():
            app.router.add_static("/fonts", _DIST_DIR / "fonts", show_index=False)
        # Vendor shims for the app import map (react, react-dom, react/jsx-runtime)
        if (_DIST_DIR / "vendor").is_dir():
            app.router.add_static(
                "/vendor",
                _DIST_DIR / "vendor",
                show_index=False,
                append_version=False,  # stable URLs, no cache-busting
            )
        logger.info("Serving React build from %s", _DIST_DIR)

    # ── Middleware ────────────────────────────────────────────────────────────

    # No-cache: prevents Chrome from caching stale assets
    @web.middleware  # type: ignore[misc]
    async def no_cache_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        resp = await handler(request)  # type: ignore[operator]
        if hasattr(resp, "headers"):
            resp.headers.setdefault(
                "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"
            )
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
            # CSP: defense-in-depth layer. Primary XSS protection is rehypeSanitize
            # (strips script/iframe/form/foreignObject at HAST level before rendering).
            # CSP must allow 'unsafe-inline' because widget iframes (blob: sandbox)
            # inherit parent CSP per W3C spec — inline scripts in widgets need it.
            # Widget isolation is enforced by sandbox="allow-scripts" (no parent DOM
            # access) + widget-level CSP meta (connect-src 'none').
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; "
                # blob: in script-src enables dynamic ESM module loading for contributed
                # app UI bundles: the host rewrites a bundle's bare import specifiers
                # (react / @personalclaw/app-sdk / …) to same-origin-derived blob modules
                # that re-export the host's singletons. Blobs are origin-scoped; apps are
                # still gated by the permission system + SkillScanner at install.
                "script-src 'self' 'unsafe-inline' blob: "
                "https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "  # noqa: E501
                "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "  # noqa: E501
                "img-src 'self' data: blob: https:; "
                # Monaco (locally bundled) inlines its codicon icon font as a data: URI;
                # without font-src the default-src 'self' fallback blocks it.
                "font-src 'self' data:; "
                "connect-src 'self' ws://localhost:* ws://127.0.0.1:*; "
                "frame-src 'self' blob:; "
                "worker-src 'self' blob:; "
                "object-src 'none'; base-uri 'self'",
            )
        return resp  # type: ignore[return-value]

    # SPA fallback: serve index.html for client-side React Router paths
    @web.middleware  # type: ignore[misc]
    async def spa_fallback(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        try:
            return await handler(request)  # type: ignore[operator]
        except web.HTTPNotFound:
            if request.method == "GET" and not request.path.startswith(
                ("/api/", "/assets/", "/sprites/", "/vendor/")
            ):
                return await handlers.index(request)
            raise

    # CSRF: block state-mutating requests from cross-origin pages
    _safe_methods = {"GET", "HEAD", "OPTIONS"}

    # SEL: log mutating API operations
    _sel_log_methods = {"POST", "PUT", "DELETE", "PATCH"}

    @web.middleware  # type: ignore[misc]
    async def sel_audit_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method in _sel_log_methods and request.path.startswith("/api/"):
            from personalclaw.sel import sel

            try:
                resp = await handler(request)  # type: ignore[operator]
                sel().log_api_access(
                    caller="dashboard_user",
                    operation=f"{request.method} {request.path}",
                    outcome="ok" if resp.status < 400 else "error",
                    resources=request.path,
                )
                return resp  # type: ignore[return-value]
            except Exception as exc:
                sel().log_api_access(
                    caller="dashboard_user",
                    operation=f"{request.method} {request.path}",
                    outcome="error",
                    resources=request.path,
                    error=str(exc)[:200],
                )
                raise
        return await handler(request)  # type: ignore[operator]

    app["allowed_origins"] = build_allowed_origins(port, local_only, configured_host)

    @web.middleware  # type: ignore[misc]
    async def csrf_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method not in _safe_methods:
            if not check_origin(request, require=True, fallback_header="Referer"):
                raise web.HTTPForbidden(
                    text="CSRF check failed: request origin not allowed.",
                    content_type="text/plain",
                )
        return await handler(request)  # type: ignore[operator]

    @web.middleware  # type: ignore[misc]
    async def app_permission_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        """Enforce an app's declared ``permissions.api`` allowlist (A5).

        Only acts on requests carrying an app identity (``request["app"]`` set
        from an app-scoped token). A path the app didn't declare is rejected
        403 before the handler runs — the server-side, bypass-proof half of the
        permission boundary. Owner/dashboard requests (no app identity) pass."""
        app_name = request.get("app", "")
        if app_name and request.path.startswith(("/api/", "/apps/")):
            from personalclaw.apps.permissions import checker_for

            checker = checker_for(app_name)

            def _deny(reason: str) -> web.StreamResponse:
                from personalclaw.sel import sel

                try:
                    sel().log_api_access(
                        caller=f"app:{app_name}",
                        operation=f"{request.method} {request.path}",
                        outcome="denied",
                        source="app_permissions",
                        resources=request.path,
                        error=reason,
                    )
                except Exception:
                    pass
                raise web.HTTPForbidden(
                    text=f"app {app_name!r} not permitted to access {request.path}",
                    content_type="text/plain",
                )

            if checker is not None and not checker.can_use_api(request.path):
                return _deny("api path not in declared permissions")
            # A memory API path additionally requires the ``memory`` capability
            # (sandbox P3) — declaring the /api/memory path in permissions.api is
            # necessary but not sufficient; the app must also hold memory access.
            if (
                checker is not None
                and request.path.startswith("/api/memory")
                and not checker.can_use_memory("shared")
            ):
                return _deny("memory access not declared (permissions.memory)")
        return await handler(request)  # type: ignore[operator]

    # Generate per-session secret for local app / IPC authentication.
    # NOTE: file write deferred until after port bind succeeds to avoid
    # poisoning the secret file when a second instance fails to start.
    _secret_path = config_dir() / ".local_secret"
    _secret_path.parent.mkdir(parents=True, exist_ok=True)
    _internal_secret = os.urandom(16).hex()
    app["local_secret"] = _internal_secret

    # AuthMode.NONE (PERSONALCLAW_AUTH_MODE=none) — dev convenience: skip the CSRF +
    # token-auth middlewares so localhost needs no token. effective_bind() forces the
    # bind to loopback in this mode, so the gateway stays unreachable off-host.
    from personalclaw.auth.modes import AuthMode as _AuthMode

    _no_auth = app["auth_cfg"].mode == _AuthMode.NONE
    if _no_auth:
        logger.warning("PERSONALCLAW_AUTH_MODE=none — token auth DISABLED (loopback only)")

    @web.middleware
    async def _dev_user_middleware(request: web.Request, handler: object) -> web.StreamResponse:
        # In AuthMode.NONE the token-auth middleware is skipped, but many handlers
        # (terminal, loops, portability, core) authenticate by reading request["user"]
        # which that middleware normally sets. Populate it so they don't 401.
        request["user"] = request.get("user") or "dev-local"
        # App identity must survive none-mode too: token_auth normally adopts the
        # ``app`` claim from an app-scoped token (Authorization: Bearer for fetch,
        # ?app_token= for the WS handshake) so app_permission_middleware + the WS
        # event filter can scope the request. Skipping this here silently DISABLED
        # the entire app permission sandbox in none-mode (an app-scoped request
        # reached ANY /api path). The app token only NARROWS the dev owner's reach.
        if not request.get("app"):
            from personalclaw.dashboard.token_auth import validate_token_with_app

            app_token = ""
            _auth = request.headers.get("Authorization", "")
            if _auth.startswith("Bearer "):
                app_token = _auth[7:].strip()
            if not app_token:
                app_token = request.query.get("app_token", "")
            if app_token:
                a_valid, _a_user, _reason, a_app = validate_token_with_app(app_token)
                if a_valid and a_app:
                    request["app"] = a_app
        return await handler(request)  # type: ignore[operator]

    # Explicit middleware ordering — self-documenting and immune to future insertions
    app.middlewares[:] = [
        no_cache_middleware,
        *(
            [_dev_user_middleware]
            if _no_auth
            else [
                csrf_middleware,
                token_auth_middleware(
                    internal_paths=frozenset(
                        {
                            "/api/send-message",
                            "/api/session-keepalive",
                            "/api/session-tool-policy",
                            "/api/hooks/agent",
                            "/api/outbox/notify",
                            "/api/channel/upload-file",
                            "/api/mcp/servers",
                            "/api/tools/invoke",
                        }
                    ),
                    mixed_internal_paths=frozenset(
                        {
                            # Called by MCP (loopback + secret) AND browser polling
                            # (DCV/SSH-forwarded cookie auth).  See token_auth.py.
                            "/api/spawn",
                            "/api/lessons",
                            # Trigger routes: browser (cookie) for the UI, plus the
                            # internal on-demand fire (cron trigger / schedule_trigger
                            # MCP tool) POSTs /api/triggers/{id}/run with the secret.
                            "/api/triggers",
                        }
                    ),
                    internal_secret=_internal_secret,
                    port=port,
                    local_only=local_only,
                ),
            ]
        ),
        app_permission_middleware,
        sel_audit_middleware,
        spa_fallback,
    ]

    # Verify security invariant: if dashboard_url expands the CSRF origin
    # set for a remote URL, token auth middleware MUST be active.
    if dashboard_url:
        _has_token_auth = any(getattr(mw, "_is_token_auth", False) for mw in app.middlewares)
        if _has_token_auth:
            app["allowed_origins"] = build_allowed_origins(
                port, local_only, configured_host, dashboard_url
            )
            logger.info(
                "dashboard_url=%s: added to CSRF allowed origins (token auth verified)",
                dashboard_url,
            )
        else:
            logger.error(
                "dashboard_url=%s requires token auth — refusing to start without it. "
                "Connect a channel or remove dashboard.url from config.",
                dashboard_url,
            )
            raise RuntimeError("dashboard_url requires token auth middleware")

    runner = web.AppRunner(app)
    await runner.setup()
    # Bind decision: prefer the explicit PERSONALCLAW_BIND_HOST env var
    # (corp-host / DevSpaces escape hatch); otherwise derive from the
    # caller's local_only flag (the loopback invariant in effective_bind()
    # makes AuthMode.NONE override this).
    _bind_host = resolve_bind_host()
    if _bind_host == "127.0.0.1" and not local_only:
        _bind_host = "0.0.0.0"
    # AuthMode.NONE invariant: an unauthenticated gateway must never leave loopback.
    if _no_auth:
        _bind_host = "127.0.0.1"
    site = web.TCPSite(runner, _bind_host, port)
    await _start_site(site, port)

    # Port bind succeeded — now safe to write the secret file
    try:
        _write_secret_file(_secret_path, _internal_secret)
    except OSError:
        await runner.cleanup()
        raise

    # Fire background MCP probe at startup (non-blocking)
    asyncio.create_task(handlers._bg_mcp_probe())

    # Start the MCP idle-connection sweeper (rel-mcp-server-pooling #46): reaps
    # connections unused past the TTL so resident MCP memory tracks active use.
    try:
        from personalclaw.mcp_client import get_mcp_client_registry

        _mcp_reg = get_mcp_client_registry()
        if _mcp_reg is not None:
            _mcp_reg.start_sweeper()
    except Exception:
        logger.debug("MCP idle sweeper start skipped", exc_info=True)

    # Start terminal orphan reaper (kills PTYs with no WS for >5 min)
    _reaper = asyncio.create_task(handlers.reap_orphaned_terminals(app))
    _reaper.add_done_callback(lambda t: t.result() if not t.cancelled() else None)
    state._terminal_reaper = _reaper  # prevent GC

    # Trim the append-only security-event log at startup + periodically so audit
    # reads/verify stay fast (the chain is otherwise unbounded).
    state._sel_prune_task = asyncio.create_task(_sel_prune_loop())  # prevent GC

    # Sweep abandoned resumable-upload session dirs (partial parts) so a never-
    # finished large upload can't pin disk forever.
    state._upload_sweep_task = asyncio.create_task(_upload_sweep_loop())  # prevent GC

    # Start periodic flush loop for crash protection (saves dirty sessions every 5s)
    state.start_flush_loop()

    # Restore sessions — always restore foldered/pinned sessions; optionally restore recent ones.
    # NOTE: Even with restore_sessions=false, foldered and pinned sessions are restored
    # so the Explorer tree stays populated.  Users can unpin or remove from folder to dismiss.
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    _apply_startup_yolo(state, cfg)
    restored = chat.restore_recent_sessions(
        state,
        cfg.dashboard.restore_window_minutes if cfg.dashboard.restore_sessions else 0,
        folders_only=not cfg.dashboard.restore_sessions,
    )
    if restored:
        logger.info("Restored %d session(s)", restored)

    return runner, state


async def start_api_server(
    sessions: "SessionManager",
    crons: "ScheduleService",
    lessons: "LessonStore",
    port: int = _DEFAULT_PORT,
    subagents: "SubagentManager | None" = None,
    owner_id: str = "",
) -> tuple[web.AppRunner, DashboardState]:
    """Start a minimal API-only server for MCP tool transport (no UI)."""
    state = DashboardState(
        sessions=sessions,
        crons=crons,
        lessons=lessons,
        start_time=time.time(),
        subagents=subagents,
        owner_id=owner_id,
    )
    state._hook_store = ScriptHookStore()
    set_global_hook_store(state._hook_store)

    from personalclaw.inbox_providers.native_source import set_dashboard_state as _set_inbox_state

    _set_inbox_state(state)

    # Wire script hooks into subagent tool execution path
    if state.subagents is not None:
        state.subagents.hook_store = state._hook_store

    # Visible notice + pct reset when auto-compaction fires on a dashboard session
    state.wire_session_compact_callback()

    app = web.Application(
        client_max_size=_single_post_ceiling()
    )  # small single-POST uploads only; large media → resumable upload sub-app
    app["state"] = state
    state.load_folders()
    state.load_tags()
    app["port"] = port
    from personalclaw.auth.modes import AuthConfig as _AuthConfig

    app["auth_cfg"] = _AuthConfig.from_env()

    _precompute_telemetry(state)

    # SEL audit middleware — log mutating MCP tool calls
    _sel_methods = {"GET", "POST", "PUT", "DELETE"}

    @web.middleware  # type: ignore[misc]
    async def sel_audit_middleware(
        request: web.Request,
        handler: object,
    ) -> web.StreamResponse:
        if request.method in _sel_methods and request.path.startswith("/api/"):
            from personalclaw.sel import sel

            try:
                resp = await handler(request)  # type: ignore[operator]
                sel().log_api_access(
                    caller="mcp_tool",
                    operation=f"{request.method} {request.path}",
                    outcome="ok" if resp.status < 400 else "error",
                    resources=request.path,
                )
                return resp  # type: ignore[return-value]
            except Exception as exc:
                sel().log_api_access(
                    caller="mcp_tool",
                    operation=f"{request.method} {request.path}",
                    outcome="error",
                    resources=request.path,
                    error=str(exc)[:200],
                )
                raise
        return await handler(request)  # type: ignore[operator]

    app.middlewares.append(sel_audit_middleware)

    _register_mcp_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await _start_site(site, port)
    logger.info("API-only server listening on 127.0.0.1:%d", port)

    return runner, state
