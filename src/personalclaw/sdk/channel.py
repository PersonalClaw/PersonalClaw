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
from personalclaw import __version__  # noqa: F401
from personalclaw import session_restrictions, trust_mode  # noqa: F401
from personalclaw.acp.errors import (  # noqa: F401
    AcpError,
    AcpProcessDied,
    AcpTimeoutError,
)
from personalclaw.acp.types import (  # noqa: F401
    STOP_REASON_CANCELLED,
    STOP_REASON_END_TURN,
)
from personalclaw.atomic_write import atomic_write  # noqa: F401
from personalclaw.channel_delivery import ChannelDelivery  # noqa: F401

# ── Transport ABC + data types ──
from personalclaw.channel_transports.base import (  # noqa: F401
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)

# ── Config + credentials ──
# (Channel activation modes are the channel APP's own concept now —
# slack_runtime.settings owns ACTIVATION_* for the Slack app.)
# CRED_SLACK_* are the slack app's credential KEYS in the generic cred store;
# they are defined in config/loader.py (the store's home) and re-exported here
# as the surface apps import — see the definition site for the layering note.
from personalclaw.config.loader import (  # noqa: F401
    CRED_OWNER_ID,
    CRED_SLACK_APP_TOKEN,
    CRED_SLACK_BOT_TOKEN,
    AppConfig,
    config_dir,
    config_path,
    save_credential,
)
from personalclaw.context import (  # noqa: F401
    ContextBuilder,
    build_cancelled_turn_preamble,
    compress_thread_history,
)

# ── Dashboard integration (link/handoff/mirror/update surfaces a channel drives) ──
from personalclaw.dashboard.chat import _run_chat, _save_session_to_history  # noqa: F401
from personalclaw.dashboard.handlers import get_update_info  # noqa: F401
from personalclaw.dashboard.origin import (  # noqa: F401
    dashboard_origin,
    devspaces_proxy_url,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)
from personalclaw.dashboard.token_auth import (  # noqa: F401
    LINK_WINDOW_SECS,
    MAX_SESSION_TTL_SECS,
    generate_token,
    parse_duration,
)
from personalclaw.doc_parser import extract_text, is_parseable_document  # noqa: F401

# ── The core↔channel seams ──
from personalclaw.gateway_services import GatewayServices  # noqa: F401
from personalclaw.history import ConversationLog, HistoryConsolidator  # noqa: F401

# ── Hooks + LLM streaming events + ACP ──
from personalclaw.hooks import (  # noqa: F401
    HOOK_REPLY,
    TOOL_AUTO_APPROVE,
    TOOL_DENY,
    safe_read_file,
    validate_file_path,
)
from personalclaw.llm.base import (  # noqa: F401
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    LLMEvent,
    ModelProvider,
)
from personalclaw.llm_helpers import save_conversation_turn  # noqa: F401
from personalclaw.mcp_discovery import list_servers  # noqa: F401
from personalclaw.memory_service import MemoryService  # noqa: F401
from personalclaw.prompt_providers.runtime import render_use_case_prompt  # noqa: F401
from personalclaw.providers.settings import ProviderSettings  # noqa: F401
from personalclaw.providers.use_cases import (  # noqa: F401
    load_use_case_settings,
    save_use_case_settings,
)

# ── Scheduling ──
from personalclaw.schedule import (  # noqa: F401
    ScheduleService,
    compute_next_run_ts,
    format_schedule,
)

# ── Security + audit ──
from personalclaw.security import (  # noqa: F401
    is_sensitive_path,
    redact,
    redact_and_truncate,
    redact_credentials,
    redact_exfiltration_urls,
    should_record_observe_history,
)
from personalclaw.sel import sel  # noqa: F401

# ── Session + conversation runtime ──
from personalclaw.session import (  # noqa: F401
    BACKGROUND_KEY,
    SessionManager,
    SessionMap,
)
from personalclaw.skills import SkillsLoader  # noqa: F401
from personalclaw.stats import Stats  # noqa: F401
from personalclaw.subagent import SubagentManager  # noqa: F401
from personalclaw.task import Task  # noqa: F401
from personalclaw.textfmt import extract_options, strip_thinking_tags  # noqa: F401

# ── Media + prompts + discovery ──
from personalclaw.transcribe import is_available as stt_available  # noqa: F401
from personalclaw.transcribe import transcribe_audio  # noqa: F401
from personalclaw.tts.registry import active_voice_params  # noqa: F401
from personalclaw.voice_reply import voice_reply  # noqa: F401
