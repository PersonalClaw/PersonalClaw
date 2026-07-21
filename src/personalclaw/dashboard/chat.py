"""Dashboard chat — facade module.

Re-exports all public symbols from the chat_*.py submodules so that
``from personalclaw.dashboard.chat import X`` resolves regardless of which
submodule defines ``X``.

The actual implementation lives in:
- chat_utils.py       — shared helpers, redaction, model normalization
- chat_persistence.py — session save/restore, history
- chat_runner.py      — _run_chat, streaming, prompt expansion
- chat_handlers.py    — HTTP API endpoints
- chat_title.py       — title generation, plan metadata
- chat_regenerate.py  — regenerate, variant switch, edit-resend
- chat_folders.py     — folder CRUD, pin, assignment
- chat_voice.py       — Piper TTS config + synthesis
- chat_channel.py     — channel link, handoff, channels
- chat_fork.py        — fork session
- chat_tags.py        — tag vocabulary + tag-column CRUD
"""

# Re-export names that tests monkeypatch on this module
import asyncio  # noqa: F401

from personalclaw.config.loader import (  # noqa: F401
    AppConfig,
    config_dir,
    resolve_agent_bindings,
)
from personalclaw.dashboard.chat_channel import (  # noqa: F401
    api_channel_reply_targets,
    api_chat_session_channel_link,
    api_chat_session_handoff,
)
from personalclaw.dashboard.chat_folders import (  # noqa: F401
    _generate_folder_icon,
    api_chat_folder_create,
    api_chat_folder_delete,
    api_chat_folder_update,
    api_chat_folders,
    api_chat_session_folder,
    api_chat_session_pin,
)
from personalclaw.dashboard.chat_fork import api_chat_session_fork  # noqa: F401
from personalclaw.dashboard.chat_handlers import (  # noqa: F401
    MAX_COLOR_INDEX,
    api_chat,
    api_chat_mode,
    api_chat_session_acp_agent,
    api_chat_session_agent,
    api_chat_session_approve,
    api_chat_session_color,
    api_chat_session_context,
    api_chat_session_create,
    api_chat_session_delete,
    api_chat_session_detail,
    api_chat_session_interrupt,
    api_chat_session_model,
    api_chat_session_queue_cancel,
    api_chat_session_reasoning_effort,
    api_chat_session_resume,
    api_chat_session_stop,
    api_chat_session_workspace_dir,
    api_chat_sessions,
    api_chat_sessions_cleanup,
    api_chat_task_mode,
    api_chat_tool_result,
    api_nav_resolve_links,
    api_recent_projects,
)
from personalclaw.dashboard.chat_persistence import (  # noqa: F401
    _attach_variants,
    _build_history_prefix,
    _rehydrate_session_from_history,
    _save_session_to_history,
    restore_recent_sessions,
    save_all_sessions_to_history,
)
from personalclaw.dashboard.chat_regenerate import (  # noqa: F401
    _MAX_VARIANTS,
    api_chat_session_edit_resend,
    api_chat_session_regenerate,
    api_chat_session_switch_variant,
)
from personalclaw.dashboard.chat_runner import (  # noqa: F401
    _expand_prompt_mention,
    _flush_segment,
    _run_chat,
)
from personalclaw.dashboard.chat_tags import (  # noqa: F401
    api_chat_session_drop,
    api_chat_session_tags,
    api_chat_tag_column_create,
    api_chat_tag_column_delete,
    api_chat_tag_column_update,
    api_chat_tag_columns,
    api_chat_tag_columns_reorder,
    api_chat_tag_create,
    api_chat_tag_delete,
    api_chat_tag_update,
    api_chat_tags,
)
from personalclaw.dashboard.chat_title import (  # noqa: F401
    _build_title_prompt,
    _extract_and_redact_plan_metadata,
    _generate_title_via_provider,
    _maybe_auto_title,
    _persist_title,
    _rephrase_plan_lite,
    _reset_auto_run_for_new_plan,
    api_chat_session_generate_title,
    api_chat_session_rename,
)
from personalclaw.dashboard.chat_undo import api_chat_session_undo  # noqa: F401
from personalclaw.dashboard.chat_utils import (  # noqa: F401
    _BLOCKED_SLASH_COMMANDS,
    _SLASH_COMMANDS,
    _apply_incognito_prefix,
    _broadcast_auto_tool,
    _broadcast_compaction_result,
    _build_stream_chunk,
    _cached_lumon_persona,
    _dequeue_next_message,
    _emit_agent_assignment,
    _extract_bash_command,
    _history_key_for,
    _maybe_consolidate,
    _maybe_inject_persona,
    _normalize_model,
    _prepare_messages,
    _redact_deep,
    _redact_for_display,
    _remove_queued_by_id,
    _sync_dashboard_sessions,
    _validate_tool_name,
    is_deprecated_model,
)
from personalclaw.dashboard.chat_voice import api_voice_synthesize  # noqa: F401
from personalclaw.dashboard.side import (  # noqa: F401
    api_side_close,
    api_side_open,
    api_side_turn,
)
from personalclaw.security import is_sensitive_path  # noqa: F401
from personalclaw.sel import sel  # noqa: F401
