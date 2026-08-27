"""SDK: the channel-transport contract + the runtime surface a channel app needs.

A channel app (Slack, and future Telegram/Discord) owns a full inbound receiver +
outbound renderer, so it needs more of the platform than a leaf provider: session
routing, conversation history, cron/schedule, context building, transcription,
security redaction, audit (SEL), and the gateway-services / channel-delivery
contracts. Rather than let the app reach into core internals (which would freeze
those internals), every symbol it needs is re-exported here — the single stable
channel SDK facade. Core can move the underlying modules without breaking apps.

Grouped by concern below. All names are re-exports; see the owning core module for
the authoritative docs.
"""

# ── Process-global trust + session-restriction state (shared by all surfaces) ──
from personalclaw import __version__, session_restrictions, trust_mode
from personalclaw.acp.errors import (
    AcpError,
    AcpProcessDied,
    AcpTimeoutError,
)
from personalclaw.acp.types import (  # noqa: F401
    CANCELLED_STOP_REASONS,
    STOP_REASON_CANCELLED,
    STOP_REASON_END_TURN,
    STOP_REASON_STOPPED_BY_USER,
    is_cancelled_stop,
)
from personalclaw.atomic_write import atomic_write
from personalclaw.channel_delivery import ChannelDelivery

# ── Transport ABC + data types ──
from personalclaw.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)

# ── Sender trust (CE-1) — the core seam every channel binds to ──
# Provider-agnostic: `provider` is an opaque key the transport picks; no vendor lives
# in core. A channel app consumes the whole trust API through here so its allow/deny,
# pairing, fencing and unknown-sender flow can never drift per channel.
from personalclaw.channel_trust import (
    CANNED_PAIRING_REPLY,
    TrustVerdict,
    allow_sender,
    apply_trust_action,
    create_pairing_code,
    deny_sender,
    fence_channel_content,
    guard_inbound,
    is_allowed_sender,
    is_tracked_channel,
    note_unknown_sender,
    redeem_pairing_code,
    track,
    trust_policies,
    untrack,
)

# ── Config + credentials ──
# (Channel activation modes are the channel APP's own concept now —
# slack_runtime.settings owns ACTIVATION_* for the Slack app.)
# CRED_SLACK_* are the slack app's credential KEYS in the generic cred store;
# they are defined in config/loader.py (the store's home) and re-exported here
# as the surface apps import — see the definition site for the layering note.
from personalclaw.config.credentials import save_credential
from personalclaw.config.loader import (
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
    AppConfig,
    config_dir,
    config_path,
)
from personalclaw.context import (
    ContextBuilder,
    build_cancelled_turn_preamble,
    compress_thread_history,
)

# ── Dashboard integration (link/handoff/mirror/update surfaces a channel drives) ──
# These two were `_run_chat` / `_save_session_to_history` — underscore-named core
# internals on a published surface, which is a contradiction the docstring above cannot
# hold: an app CANNOT be insulated from a name whose spelling says "may move without
# notice". Three bundled channel apps drive the first and one drives the second, so they
# were a public contract by use; they are now public by name, at their definition sites.
from personalclaw.dashboard.chat import run_chat, save_session_to_history
from personalclaw.dashboard.handlers import get_update_info
from personalclaw.dashboard.origin import (
    dashboard_origin,
    devspaces_proxy_url,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from personalclaw.dashboard.token_auth import (
    LINK_WINDOW_SECS,
    MAX_SESSION_TTL_SECS,
    generate_token,
    parse_duration,
)
from personalclaw.doc_parser import extract_text, is_parseable_document

# ── The core↔channel seams ──
from personalclaw.gateway_services import GatewayServices
from personalclaw.history import ConversationLog, HistoryConsolidator

# ── Hooks + LLM streaming events + ACP ──
from personalclaw.hooks import (
    HOOK_REPLY,
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    safe_read_file,
    validate_file_path,
)
from personalclaw.llm.base import (
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
    ModelProvider,
)
from personalclaw.llm_helpers import save_conversation_turn
from personalclaw.mcp_discovery import list_servers
from personalclaw.memory_service import MemoryService
from personalclaw.prompt_providers.runtime import render_use_case_prompt
from personalclaw.providers.settings import ProviderSettings
from personalclaw.providers.use_cases import (
    load_use_case_settings,
    save_use_case_settings,
)

# ── Automations (the unified trigger store) ──
#
# `ScheduleService` is GONE (S112). A channel app's `/cron` surface reads and mutates automations
# through the same store, projection and tool functions the API and the chat tools use, so there is
# exactly one behaviour to reason about — a channel that kept its own scheduler view would drift
# from the Automations page the moment either changed.
#
# `describe_cadence` replaces `format_schedule(job.schedule)`: it takes a `Trigger` and delegates to
# the same shipped formatter, so the wording stays identical while the input becomes the store's.
# `to_schedule_row` is the wire projection (id, enabled, message, next_run_ts, last_status) that the
# API already publishes, which is what a list command needs.
from personalclaw.schedule import compute_next_run_ts, format_schedule

# ── Security + audit ──
from personalclaw.security import (
    is_sensitive_path,
    redact,
    redact_and_truncate,
    redact_credentials,
    redact_exfiltration_urls,
    should_record_observe_history,
)
from personalclaw.sel import sel

# ── Session + conversation runtime ──
from personalclaw.session import (
    BACKGROUND_KEY,
    SessionManager,
    SessionMap,
)
from personalclaw.skills import SkillsLoader
from personalclaw.stats import Stats
from personalclaw.subagent import SubagentManager
from personalclaw.task import Task

# ── Conformance kit (CE-6) — the one executable channel contract ──
# Lives in the INSTALLED package, not core's `tests/`: `tests/` ships in neither the
# wheel nor the sdist (pyproject `packages.find where = ["src"]`; MANIFEST.in grafts only
# web/dist), and the apps repo's CI installs core as a distribution — so a kit under
# `tests/` would be unimportable exactly where the four apps have to call it. Re-exported
# here because this facade is the only import path an app is allowed to use.
from personalclaw.testing.channel_conformance import (
    CapturingState,
    ChannelContractError,
    assert_channel_contract,
)
from personalclaw.textfmt import extract_options, strip_thinking_tags

# ── Media + prompts + discovery ──
from personalclaw.transcribe import is_available as stt_available
from personalclaw.transcribe import transcribe_audio
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.schedule_view import (
    describe_cadence,
    to_schedule_row,
)
from personalclaw.triggers.store import TriggerStore
from personalclaw.triggers.tools import delete as delete_automation
from personalclaw.triggers.tools import delete_all as delete_all_automations
from personalclaw.triggers.tools import set_paused as set_automation_paused
from personalclaw.tts.registry import active_voice_params
from personalclaw.voice_reply import voice_reply

# The published surface, declared in ONE place. This module is the only `sdk/` module that
# used to have no `__all__` — and it is the one that leaked two underscore-prefixed core
# internals (`_run_chat`, `_save_session_to_history`) onto the app surface for the life of
# the facade. A name absent from this list is not part of the channel SDK contract;
# `tests/test_sdk_surface_is_public.py` makes both halves of that a failing build.
__all__ = [
    "AcpError",
    "AcpProcessDied",
    "AcpTimeoutError",
    "AppConfig",
    "BACKGROUND_KEY",
    "CANNED_PAIRING_REPLY",
    "CRED_OWNER_ID",
    "CRED_SLACK_APP_TOKEN",
    "CRED_SLACK_BOT_TOKEN",
    "CapturingState",
    "ChannelCapabilities",
    "ChannelContractError",
    "ChannelDelivery",
    "ChannelMessage",
    "ChannelTransportProvider",
    "ContextBuilder",
    "ConversationLog",
    "EVENT_COMPACTION_STATUS",
    "EVENT_COMPLETE",
    "EVENT_PERMISSION_REQUEST",
    "EVENT_TEXT_CHUNK",
    "EVENT_THINKING_CHUNK",
    "EVENT_TOOL_CALL",
    "GatewayServices",
    "HOOK_REPLY",
    "HistoryConsolidator",
    "LINK_WINDOW_SECS",
    "LLMEvent",
    "MAX_SESSION_TTL_SECS",
    "MemoryService",
    "ModelProvider",
    "OutboundMessage",
    "ProviderSettings",
    "STOP_REASON_CANCELLED",
    "STOP_REASON_END_TURN",
    "CANCELLED_STOP_REASONS",
    "STOP_REASON_STOPPED_BY_USER",
    "is_cancelled_stop",
    "SessionManager",
    "SessionMap",
    "SkillsLoader",
    "Stats",
    "SubagentManager",
    "TOOL_AUTO_APPROVE",
    "TOOL_DENY",
    "Task",
    "Trigger",
    "TriggerStore",
    "TrustVerdict",
    "__version__",
    "active_voice_params",
    "allow_sender",
    "apply_trust_action",
    "assert_channel_contract",
    "atomic_write",
    "build_cancelled_turn_preamble",
    "compress_thread_history",
    "compute_next_run_ts",
    "config_dir",
    "config_path",
    "create_pairing_code",
    "dashboard_origin",
    "delete_all_automations",
    "delete_automation",
    "deny_sender",
    "describe_cadence",
    "devspaces_proxy_url",
    "extract_options",
    "extract_text",
    "fence_channel_content",
    "format_schedule",
    "generate_token",
    "get_update_info",
    "guard_inbound",
    "is_allowed_sender",
    "is_local_bind",
    "is_parseable_document",
    "is_sensitive_path",
    "is_tracked_channel",
    "list_servers",
    "load_use_case_settings",
    "note_unknown_sender",
    "parse_dashboard_url",
    "parse_duration",
    "redact",
    "redact_and_truncate",
    "redact_credentials",
    "redact_exfiltration_urls",
    "redeem_pairing_code",
    "render_use_case_prompt",
    "resolve_bind_host",
    "resolve_dashboard_host",
    "run_chat",
    "safe_read_file",
    "save_conversation_turn",
    "save_credential",
    "save_session_to_history",
    "save_use_case_settings",
    "sel",
    "session_restrictions",
    "set_automation_paused",
    "should_record_observe_history",
    "strip_thinking_tags",
    "stt_available",
    "to_schedule_row",
    "track",
    "transcribe_audio",
    "trust_mode",
    "trust_policies",
    "untrack",
    "validate_file_path",
    "voice_reply",
]
