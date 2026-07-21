"""Non-chat HTTP handlers — re-export façade over the per-domain submodules.

Aggregates the public handler API from the ``handlers/`` submodules (agents,
schedule, files, hooks, mcp, memory, messaging, prompts, sessions,
terminal, updates, usage, core, optimizer, portability) under the flat
``personalclaw.dashboard.handlers.X`` import path used by ``server.py``.

System metrics (CPU, memory, network, disk) live in ``handlers_system.py``;
``api_status`` and ``api_system`` are re-exported here for convenience.
"""

import logging

# Imports accessed by submodules via late-binding (_h.X pattern)
from personalclaw.config.loader import AppConfig, config_dir, config_path  # noqa: F401
from personalclaw.dashboard.handlers_system import (  # noqa: F401
    api_auth_status,
    api_healthz,
    api_onboarding,
    api_status,
    api_system,
)
from personalclaw.dashboard.origin import is_loopback  # noqa: F401
from personalclaw.security import (  # noqa: F401
    is_sensitive_path,
    redact_credentials,
    redact_exfiltration_urls,
)
from personalclaw.session import _sync_kill_provider  # noqa: F401


def sel():
    """Dynamic sel() that always resolves from personalclaw.sel for test patching."""
    from personalclaw.sel import sel as _s

    return _s()


logger = logging.getLogger(__name__)


# ── Shared helpers (handlers/_shared.py) ──
from personalclaw.dashboard.handlers._shared import (  # noqa: E402, F401
    _blocks_reads_session,
    _get_active_workspace,
    _get_lessons,
    _get_memory,
    _get_skills,
    _is_restricted_session,
    _list_marketplace_skills,
    _resolve_skill_path,
)

# ── Agents & Themes (handlers/agents.py) ──
from personalclaw.dashboard.handlers.agents import (  # noqa: E402, F401
    _CSS_VALUE_ALLOWED_RE,
    _THEME_CSS_VARS_SET,
    _auto_install_agent,
    _find_agent_config,
    _get_config_lock,
    _installed_agent_config,
    _sanitize_css_value,
    _slugify_theme_name,
    _strip_to_allowed_vars,
    _validate_theme_data,
    api_agent_config,
    api_agent_detail,
    api_agent_metadata_delete,
    api_agent_metadata_get,
    api_agent_metadata_put,
    api_agents_installed,
    api_config_schema,
    api_default_agent,
    api_personalclaw_agent_delete,
    api_personalclaw_agent_update,
    api_personalclaw_agents,
    api_personalclaw_agents_create,
    api_personalclaw_agents_sync,
    api_slash_commands,
    api_theme_detail,
    api_themes,
    api_themes_create,
)

# ── Core (handlers/core.py) ──
from personalclaw.dashboard.handlers.core import (  # noqa: E402, F401
    _DIST_DIR,
    api_logout,
    api_personalclaw_config,
    api_personalclaw_config_patch,
    api_security_denied_commands,
    api_security_egress,
    api_security_stats,
    api_sel_events,
    api_sel_rotate,
    api_sel_verify,
    api_session_agent_result,
    api_session_agent_stream,
    api_session_agents_list,
    api_stt_transcribe,
    api_token_local,
    favicon,
    index,
)

# ── Files & Workspaces (handlers/files.py) ──
from personalclaw.dashboard.handlers.files import (  # noqa: E402, F401
    _validate_dashboard_path,
    _write_file_restricted,
    api_attachment_extract,
    api_browse_dirs,
    api_channel_upload_file,
    api_config_fs_watch,
    api_create_dir,
    api_dashboard_config,
    api_file_complete,
    api_file_content_search,
    api_file_create,
    api_file_delete,
    api_file_git_commit,
    api_file_git_log,
    api_file_git_original,
    api_file_git_status,
    api_file_list,
    api_file_move,
    api_file_raw,
    api_file_read,
    api_file_search,
    api_file_upload,
    api_file_watch,
    api_file_write,
    api_outbox_download,
    api_outbox_list,
    api_outbox_notify,
    api_reveal_path,
    api_screenshot,
    api_upload,
    api_upload_file,
)

# ── Actions + webhook runner (handlers/hooks.py — lifecycle CRUD moved to
# triggers.py; this keeps the action catalog, the agent-scoped view, and the
# external-webhook→agent runner). ──
from personalclaw.dashboard.handlers.hooks import (  # noqa: E402, F401
    _get_hook_store,
    _load_hook_context,
    _run_hook_agent,
    _run_hook_inner,
    _verify_hook_token,
    api_action_providers,
    api_agent_hooks,
    api_hooks_agent,
)

# ── MCP (handlers/mcp.py) ──
from personalclaw.dashboard.handlers.mcp import (  # noqa: E402, F401
    _bg_mcp_probe,
    _sync_mcp_to_agent,
    api_mcp_active,
    api_mcp_apply,
    api_mcp_importable,
    api_mcp_pool_stats,
    api_mcp_probe,
    api_mcp_probe_cached,
    api_mcp_probe_one,
    api_mcp_remove,
    api_mcp_server_detail,
    api_mcp_servers,
    api_mcp_sync,
    api_mcp_toggle,
    api_mcp_toggle_all,
    api_mcp_toggle_tool,
)
from personalclaw.dashboard.handlers.memory import (  # noqa: E402, F401
    _get_provider,
    _redact_memory_field,
    _set_migrated,
    api_memory_activate_model,
    api_memory_consolidate,
    api_memory_context_preview,
    api_memory_daily_digests,
    api_memory_delete_model,
    api_memory_disable_embeddings,
    api_memory_embedding_models,
    api_memory_embedding_status,
    api_memory_enable_embeddings,
    api_memory_episodic_delete,
    api_memory_episodic_list,
    api_memory_episodic_search,
    api_memory_event_undo,
    api_memory_events,
    api_memory_graph,
    api_memory_history,
    api_memory_import,
    api_memory_lint,
    api_memory_migrate,
    api_memory_observability,
    api_memory_preferences,
    api_memory_projects,
    api_memory_promote,
    api_memory_recall,
    api_memory_semantic,
    api_memory_semantic_delete,
    api_memory_semantic_write,
    api_memory_settings,
    api_memory_stats,
    api_memory_vault_status,
    api_memory_vault_sync,
)

# ── Messaging (handlers/messaging.py) ──
from personalclaw.dashboard.handlers.messaging import (  # noqa: E402, F401
    _redact,
    _resolve_session_target,
    _sanitize_blocks,
    api_channel_profile,
    api_notification_ack,
    api_notification_delete,
    api_notification_unack,
    api_notifications,
    api_notifications_ack_all,
    api_notifications_clear,
    api_send_message,
    api_spawn,
    api_spawn_clear,
    api_spawn_delete,
    api_spawn_list,
    api_spawn_status,
)
from personalclaw.dashboard.handlers.optimizer import (  # noqa: E402, F401
    handle_optimize,
)

# ── Portability (export/import as zip) ──
from personalclaw.dashboard.handlers.portability import (  # noqa: E402, F401
    api_portability_export,
    api_portability_import,
    api_portability_preview,
)
from personalclaw.dashboard.handlers.prompts import (  # noqa: E402, F401
    MAX_PROMPT_BYTES,
    _list_provider_prompts,
    api_campaign_template_launch,
    api_prompt_bindings,
    api_prompt_bindings_save,
    api_prompt_create,
    api_prompt_delete,
    api_prompt_detail,
    api_prompt_preview,
    api_prompt_render,
    api_prompt_save,
    api_prompt_syntax,
    api_prompts,
    api_skill_detail,
    api_skills_create,
    api_snippet_create,
    api_snippet_delete,
    api_snippet_detail,
    api_snippet_render,
    api_snippet_save,
    api_snippets,
)

# ── Lessons (handlers/schedule.py — schedule CRUD moved to triggers.py) ──
from personalclaw.dashboard.handlers.schedule import (  # noqa: E402, F401
    api_lessons,
    api_lessons_create,
    api_lessons_delete,
)

# ── Sessions (handlers/sessions.py) ──
from personalclaw.dashboard.handlers.sessions import (  # noqa: E402, F401
    _SHUTDOWN_TIMEOUT_SECS,
    _remove_session_for_history_key,
    _reset_all_sessions,
    api_approval_resolve,
    api_approvals,
    api_session_archive_list,
    api_session_archive_read,
    api_session_delete,
    api_session_detail,
    api_session_keepalive,
    api_session_tool_policy,
    api_sessions,
    api_sessions_clear,
    api_sessions_context,
    api_sessions_health,
    api_sessions_restart,
    api_sessions_search,
)
from personalclaw.dashboard.handlers.terminal import (  # noqa: E402, F401
    api_terminal_create,
    api_terminal_delete,
    api_terminal_list,
    api_terminal_ws,
    reap_orphaned_terminals,
)

# ── Updates & Logs (handlers/updates.py) ──
from personalclaw.dashboard.handlers.updates import (  # noqa: E402, F401
    _UPDATE_CHECK_INTERVAL,
    _do_update_check,
    _log_ring,
    _QueueLogHandler,
    _RingLogHandler,
    _update_info,
    _version_tuple,
    api_changelog,
    api_log_level,
    api_log_level_get,
    api_logs,
    api_restart,
    api_update_apply,
    api_update_auto,
    api_update_cancel,
    api_update_check,
    api_update_simulate,
    get_update_info,
    install_log_ring_handler,
)
