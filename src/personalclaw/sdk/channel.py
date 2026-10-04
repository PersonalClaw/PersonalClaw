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

# ── The deny-list, before any approval ──
# A channel that runs a conversation itself asks the screen every approval path asks first
# (`screen_tool_call`): a call the hook chain refuses, read on the command that would run as well
# as on its title, is refused there, never approved and never asked about.
from personalclaw.acp.permission_authority import screen_tool_call
from personalclaw.acp.types import (  # noqa: F401
    CANCELLED_STOP_REASONS,
    STOP_REASON_CANCELLED,
    STOP_REASON_END_TURN,
    STOP_REASON_STOPPED_BY_USER,
    is_cancelled_stop,
)

# ── The approval prompt's content (what will run, masked) ──
# A channel's `request_approval` renders from this alone: the tool, its arguments, the purpose,
# the summary line and the reach line under it (a host off the allowed hosts), as the dashboard's
# card shows them, every string already masked. `chat=` asks for the answers a channel's own turn
# offers in a conversation the channel runs itself.
from personalclaw.approval_brief import approval_brief_for

# ── How long an approval waits for a person ──
# One window for every approval that waits (Settings → Agent defaults), read per approval. A
# channel that asks on its own, for a turn it runs itself, waits exactly this long too.
from personalclaw.approval_grants import approval_window_secs
from personalclaw.atomic_write import atomic_write

# ── A file an inbound message came with (`ChannelMessage.files`) ──
from personalclaw.attachments import Attachment

# ── Auth posture (#3511) ──
# `resolve_bind_host(auth_cfg)` is published, so its parameter type is too: an app that
# resolves a bind address has to be able to CONSTRUCT the config it passes, and `AuthMode`
# comes with it because `AuthConfig.mode` is the field the resolution actually turns on.
from personalclaw.auth.modes import AuthConfig, AuthMode
from personalclaw.channel_delivery import ChannelDelivery

# ── Transport ABC + data types ──
from personalclaw.channel_transports.base import (
    ChannelCapabilities,
    ChannelMessage,
    ChannelTransportProvider,
    OutboundMessage,
)

# ── Sender trust — the core seam every channel binds to ──
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
    redeem_owner_pairing_code,
    redeem_pairing_code,
    track,
    trust_policies,
    untrack,
)

# ── The chat's Trust, for a conversation the channel runs itself ──
# A channel that runs a conversation itself keeps no trust of its own: Allow for this chat on its
# own prompt is that chat's Trust in PersonalClaw (`answer_in_chat`), shown and switched off there,
# and each later call asks who approves it without asking (`chat_grant`): the chat runner's
# decision, held to the allowed hosts and the operator ceiling. The channel approves no call itself.
from personalclaw.chat_trust import answer_in_chat, chat_grant

# ── A chore a channel asks a model for itself (a thread's title) ──
# One fresh call on the Background chain, sent its own prompt and nothing else, metered and
# recorded for whose spend it is (`chore_usage`, an `Attribution`): the way every chore reaches
# a model.
from personalclaw.chores import chore_usage, run_chore

# ── Config + credentials ──
# (Channel activation modes are the channel APP's own concept now —
# slack_runtime.settings owns ACTIVATION_* for the Slack app.)
# CRED_SLACK_* are the slack app's credential KEYS in the generic cred store;
# they are defined in config/loader.py (the store's home) and re-exported here
# as the surface apps import — see the definition site for the layering note.
#
# The owner's id is PER CHANNEL: a channel stores it under `owner_id_credential(<provider>)`
# and reads it with `owner_id_for(<provider>)`. `CRED_OWNER_ID` is the one key Slack, Telegram
# and Discord all wrote, so a second channel's setup overwrote the first one's owner. It stays
# on this surface because the released channel apps still read and write it, and
# `owner_id_for` falls back to it so such a channel keeps its owner. Taking it off is an
# apps-repo release first, then core — the order the `run_chat` removal below followed.
from personalclaw.config.credentials import owner_id_credential, owner_id_for, save_credential
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
# `save_session_to_history` was `_save_session_to_history` — an underscore-named core
# internal on a published surface, which is a contradiction the docstring above cannot
# hold: an app CANNOT be insulated from a name whose spelling says "may move without
# notice". One bundled channel app drives it, so it was a public contract by use; it is
# now public by name, at its definition site.
#
# `run_chat` is OFF this facade. It was a SECOND route past the
# sender-trust gate — the exact defect :mod:`personalclaw.channel_inbound` exists to
# close — and the chokepoint is only a chokepoint now that the door is the only route:
# an app starts a channel-originated turn through `services.deliver_channel_inbound`,
# never by driving the turn itself. `run_chat` could not carry the guard (it has no
# sender to check; it legitimately serves the owner's own dashboard turns, cron,
# heartbeat and the CLI, where there is no channel identity and nothing to deny), so
# removal was the fix. The export could only go once all four bundled channel apps had
# migrated onto the door and stopped importing it — discord/email/telegram transports
# and slack's handler, landed as PersonalClawApps #72 and #73 — because the apps repo
# is a separate release artifact that cannot land atomically with core.
# `tests/test_channel_inbound_chokepoint.py` now asserts the ABSENCE, so the second
# route cannot quietly return, and the `personalclaw.sdk.*`-only import boundary
# (`tests/test_apps_import_boundary.py`) makes the door structural rather than
# conventional.
from personalclaw.dashboard.chat import save_session_to_history
from personalclaw.dashboard.handlers import get_update_info
from personalclaw.dashboard.origin import (
    dashboard_origin,
    devspaces_proxy_url,
    is_local_bind,
    parse_dashboard_url,
    resolve_bind_host,
    resolve_dashboard_host,
)

# `DashboardState` — the first parameter of the published `save_session_to_history` — and the
# `SseRegistry`/`SseHub` its `*_sse()` accessors return are NOT exported, and that is a declared
# closure exemption rather than an oversight: see `CLOSURE_EXEMPT_PREFIX` in
# `scripts/sdk_surface_closure.py`. Importing them here would add the FIFTH and SIXTH
# `core-must-not-import-the-http-surface` edge to this file, and that rule grandfathers the four
# above precisely so a fifth cannot be added ("an allowlist is a thing that rots, a measured
# floor is not"). The HTTP surface is deliberately not part of the app type contract.
# `owner_sign_in_token` is the mint a channel's "open the dashboard" link uses: it signs in as the
# owner, so it is minted for the channel's own owner id and refused, with a sentence, for anyone
# else. It is the only mint here. `generate_token`, which mints a sign-in for any id it is handed,
# left this facade once no channel app minted with it (PersonalClawApps #155 moved Slack's
# dashboard link onto `owner_sign_in_token`) — the order the `run_chat` removal above followed.
from personalclaw.dashboard.token_auth import (
    LINK_WINDOW_SECS,
    MAX_SESSION_TTL_SECS,
    NOT_THE_OWNER_SENTENCE,
    owner_sign_in_token,
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

# The status of a compaction the agent did on its own, between two steps of a turn: a channel that
# streams the turn itself says so, in the words it gives a `/compact` that did the same.
from personalclaw.llm.events import COMPACTION_AUTOMATIC
from personalclaw.llm_helpers import save_conversation_turn
from personalclaw.mcp_discovery import McpServerInfo, list_servers
from personalclaw.memory_service import MemoryService, QueryVector
from personalclaw.prompt_providers.runtime import render_use_case_prompt
from personalclaw.providers.settings import ProviderSettings
from personalclaw.providers.use_cases import (
    load_use_case_settings,
    save_use_case_settings,
)

# ── Automations (the unified trigger store) ──
#
# `ScheduleService` is GONE. A channel app's `/cron` surface reads and mutates automations
# through the same store, projection and tool functions the API and the chat tools use, so there is
# exactly one behaviour to reason about — a channel that kept its own scheduler view would drift
# from the Automations page the moment either changed.
#
# `describe_cadence` replaces `format_schedule(job.schedule)`: it takes a `Trigger` and delegates to
# the same shipped formatter, so the wording stays identical while the input becomes the store's.
# `to_schedule_row` is the wire projection (id, enabled, message, next_run_ts, last_status) that the
# API already publishes, which is what a list command needs.
from personalclaw.schedule import (
    ScheduleDefinition,
    ScheduleJob,
    compute_next_run_ts,
    format_schedule,
)

# ── Security + audit ──
from personalclaw.security import (
    is_sensitive_path,
    redact,
    redact_and_truncate,
    redact_credentials,
    redact_exfiltration_urls,
    should_record_observe_history,
)
from personalclaw.sel import SecurityEvent, SecurityEventLog, sel

# ── Session + conversation runtime ──
from personalclaw.session import (
    SessionManager,
    SessionMap,
)
from personalclaw.skills import SkillsLoader
from personalclaw.skills.loader import (
    AutoSkillProvenance,
    ResourceRead,
    SkillResource,
)
from personalclaw.stats import Stats
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.task import Task, TaskState

# ── Conformance kit — the one executable channel contract ──
# Lives in the INSTALLED package, not core's `tests/`: `tests/` ships in neither the
# wheel nor the sdist (pyproject `packages.find where = ["src"]`; MANIFEST.in grafts only
# web/dist), and the apps repo's CI installs core as a distribution — so a kit under
# `tests/` would be unimportable exactly where the four apps have to call it. Re-exported
# here because this facade is the only import path an app is allowed to use.
from personalclaw.testing.channel_conformance import (
    CapturedSession,
    CapturingState,
    ChannelContractError,
    assert_channel_contract,
)

# `parse_title` — THE title-generation-reply parser (#3590), for a channel that names its threads.
# The Slack app mirrored it (#124) because it was private to the dashboard module.
from personalclaw.textfmt import extract_options, parse_title, strip_thinking_tags

# ── Media + prompts + discovery ──
from personalclaw.transcribe import is_available as stt_available
from personalclaw.transcribe import transcribe_audio
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.schedule_view import (
    describe_cadence,
    to_schedule_row,
)
from personalclaw.triggers.store import TriggerStore
from personalclaw.triggers.tools import AutomationToolResult
from personalclaw.triggers.tools import delete as delete_automation
from personalclaw.triggers.tools import delete_all as delete_all_automations
from personalclaw.triggers.tools import set_paused as set_automation_paused
from personalclaw.tts.registry import active_voice_params

# Where a line a channel takes into a chat itself came from (a thread it imports), recorded as the
# door records each message it hands a chat: the thread, the sender and the channel. Memory takes a
# line as the owner's own words only when its sender is that channel's owner.
from personalclaw.turn_source import arrived_on
from personalclaw.usage_ledger import Attribution
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
    "Attachment",
    "Attribution",
    "AuthConfig",
    "AuthMode",
    "AutoSkillProvenance",
    "AutomationToolResult",
    "CANNED_PAIRING_REPLY",
    "COMPACTION_AUTOMATIC",
    "CRED_OWNER_ID",
    "CRED_SLACK_APP_TOKEN",
    "CRED_SLACK_BOT_TOKEN",
    "CapturedSession",
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
    "McpServerInfo",
    "MemoryService",
    "ModelProvider",
    "NOT_THE_OWNER_SENTENCE",
    "OutboundMessage",
    "ProviderSettings",
    "QueryVector",
    "ResourceRead",
    "STOP_REASON_CANCELLED",
    "STOP_REASON_END_TURN",
    "CANCELLED_STOP_REASONS",
    "STOP_REASON_STOPPED_BY_USER",
    "is_cancelled_stop",
    "ScheduleDefinition",
    "ScheduleJob",
    "SecurityEvent",
    "SecurityEventLog",
    "SessionManager",
    "SessionMap",
    "SkillResource",
    "SkillsLoader",
    "Stats",
    "SubagentInfo",
    "SubagentManager",
    "TOOL_AUTO_APPROVE",
    "TOOL_DENY",
    "Task",
    "TaskState",
    "Trigger",
    "TriggerStore",
    "TrustVerdict",
    "__version__",
    "active_voice_params",
    "allow_sender",
    "answer_in_chat",
    "apply_trust_action",
    "approval_brief_for",
    "approval_window_secs",
    "arrived_on",
    "assert_channel_contract",
    "atomic_write",
    "build_cancelled_turn_preamble",
    "chat_grant",
    "chore_usage",
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
    "owner_id_credential",
    "owner_id_for",
    "owner_sign_in_token",
    "parse_dashboard_url",
    "parse_duration",
    "parse_title",
    "redact",
    "redact_and_truncate",
    "redact_credentials",
    "redact_exfiltration_urls",
    "redeem_owner_pairing_code",
    "redeem_pairing_code",
    "render_use_case_prompt",
    "resolve_bind_host",
    "resolve_dashboard_host",
    "run_chore",
    "safe_read_file",
    "save_conversation_turn",
    "save_credential",
    "save_session_to_history",
    "save_use_case_settings",
    "screen_tool_call",
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
