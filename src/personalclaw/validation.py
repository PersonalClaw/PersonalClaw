"""Centralized input/output validation for MCP tools and API endpoints.

All tool inputs from untrusted sources (LLM, end user, other MCP tools)
are validated here before execution.  Responses are sanitized and
truncated before returning to callers.

Provides:
- Schema validation with type enforcement
- Length and size limits
- Unicode normalization and hidden character stripping
- Allow-list approach for enums and key patterns
- Semantic/business logic checks (positive numbers, valid timestamps, etc.)
- Response truncation to prevent resource exhaustion
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# ── Constants ──

# Max lengths for string inputs
MAX_TOOL_NAME_LEN = 256
MAX_SHORT_STRING = 500  # names, IDs, categories
MAX_MEDIUM_STRING = 5_000  # messages, rules
MAX_LONG_STRING = 50_000  # task specs, inline content
MAX_RESPONSE_LEN = 100_000  # truncate tool responses

# Allowed categories for lessons
ALLOWED_LESSON_CATEGORIES = frozenset({"tool", "preference", "knowledge"})

# Allowed cron schedule kinds
ALLOWED_SCHEDULE_KINDS = frozenset({"every", "cron", "at"})

# Allowed hook events — must match HOOK_EVENTS in backend/hooks.py.
# All events are accepted at the API layer so the UI dropdown matches reality.
# Some events have firing sites in backend/dashboard/chat_runner.py
# (SessionStart, AgentSpawn, UserPromptSubmit, PreToolUse, PostToolUse, Stop, Error);
# the rest are reserved for future firing sites and currently never trigger
# but are valid to register.
ALLOWED_HOOK_EVENTS = frozenset({
    "AgentSpawn",
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PreResponse",
    "PostResponse",
    "MemoryWrite",
    "ContextCompact",
    "SubagentSpawn",
    "TaskComplete",
    "ApprovalRequest",
    "Error",
    "SessionEnd",
    "Stop",
})

# Valid agent name pattern (alphanumeric, hyphens, underscores)
_AGENT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}[a-zA-Z0-9]$|^[a-zA-Z0-9]$")

# Valid channel ID pattern (exported for reuse in handlers/CLI).
# Provider wire format: C = standard channels, D = DM channels,
# G = legacy private channels, W = cross-org shared channels
CHANNEL_ID_RE = re.compile(r"^[CDGW][A-Z0-9]+$")
CHANNEL_MAX_LEN = 20
# Valid channel user ID pattern (U or W prefix, max 20 chars total)
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{1,19}$")
USER_MAX_LEN = 20

# Channel-thread message timestamp: digits.digits (the abstract thread
# addressing format used across channel tool schemas)
_MESSAGE_TS_RE = re.compile(r"^\d+\.\d+$")

# Valid cron job ID pattern (hex)
_JOB_ID_RE = re.compile(r"^[a-f0-9]{1,16}$")

# Hidden Unicode categories to strip (control chars, format chars, etc.)
# Keeps: letters, numbers, punctuation, symbols, separators (space/newline)
_HIDDEN_CATEGORIES = frozenset(
    {
        "Cc",  # control (except \n \r \t)
        "Cf",  # format (zero-width, BOM, directional overrides)
        "Co",  # private use
        "Cs",  # surrogate
    }
)

# Specific chars to always allow even if in a hidden category
_ALLOWED_CONTROL = frozenset({"\n", "\r", "\t"})


# ── Exceptions ──


class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


# ── Field Validators ──


@dataclass
class FieldSpec:
    """Declarative field specification for validation."""

    name: str
    type: type | tuple[type, ...]  # expected Python type(s)
    required: bool = False
    max_len: int = 0  # 0 = no limit
    min_val: float | None = None  # for numeric fields
    max_val: float | None = None
    allowed: frozenset[str] | None = None  # enum allow-list
    pattern: re.Pattern[str] | None = None  # regex pattern
    default: Any = None
    item_type: type | None = None  # type: ignore[valid-type]  # for list fields: expected type of each element
    item_max_len: int = 0  # for list fields: max length of each string element
    item_pattern: re.Pattern[str] | None = None  # for list fields: regex for each string element
    max_items: int = 0  # for list fields: max number of items (0 = no limit)


@dataclass
class ToolSchema:
    """Schema for a tool's input arguments."""

    tool_name: str
    fields: list[FieldSpec] = field(default_factory=list)


def validate_field(value: Any, spec: FieldSpec) -> Any:
    """Validate and normalize a single field value. Returns cleaned value."""
    if value is None:
        if spec.required:
            raise ValidationError(spec.name, "required")
        return spec.default

    # Numeric coercion for int/float-typed fields. Models and ACP dialects emit
    # numbers inconsistently — a JSON number may deserialize to float ("seconds":
    # 300.0), or a model may quote it ("seconds": "300"). A strict isinstance(int)
    # check then rejects a perfectly valid value ("expected int, got float"), which
    # is what made the `wait` tool reject integer arguments. Coerce a value that
    # cleanly represents the target numeric type before the type check; leave
    # everything else to fail the check below.
    _wants_int = spec.type is int or (isinstance(spec.type, tuple) and spec.type == (int,))
    _wants_num = _wants_int or spec.type in (float, (int, float), (float, int))
    if _wants_num and not isinstance(value, bool):
        if isinstance(value, float) and _wants_int and value.is_integer():
            value = int(value)
        elif isinstance(value, str):
            _s = value.strip()
            try:
                if _wants_int:
                    value = int(_s, 10) if _s.lstrip("-").isdigit() else int(float(_s))
                else:
                    value = float(_s)
            except (ValueError, TypeError):
                pass  # fall through to the type check, which will raise cleanly

    # Type check
    if not isinstance(value, spec.type):
        raise ValidationError(
            spec.name,
            f"expected {spec.type.__name__ if isinstance(spec.type, type) else spec.type}, "
            f"got {type(value).__name__}",
        )

    # String validation
    if isinstance(value, str):
        value = sanitize_string(value)
        if not value and spec.required:
            raise ValidationError(spec.name, "required (empty after sanitization)")
        if spec.max_len and len(value) > spec.max_len:
            raise ValidationError(spec.name, f"exceeds max length {spec.max_len}")
        if spec.allowed and value not in spec.allowed:
            raise ValidationError(spec.name, f"must be one of: {', '.join(sorted(spec.allowed))}")
        if spec.pattern and value and not spec.pattern.match(value):
            raise ValidationError(spec.name, "invalid format")

    # Numeric validation
    if isinstance(value, (int, float)):
        if spec.min_val is not None and value < spec.min_val:
            raise ValidationError(spec.name, f"must be >= {spec.min_val}")
        if spec.max_val is not None and value > spec.max_val:
            raise ValidationError(spec.name, f"must be <= {spec.max_val}")

    # List item validation
    if isinstance(value, list):
        if spec.max_items and len(value) > spec.max_items:
            raise ValidationError(spec.name, f"exceeds max items {spec.max_items}")
        if spec.item_type:
            for i, item in enumerate(value):
                if not isinstance(item, spec.item_type):
                    raise ValidationError(
                        spec.name,
                        f"item[{i}]: expected {spec.item_type.__name__}, got {type(item).__name__}",
                    )
                if isinstance(item, str):
                    item = sanitize_string(item)
                    value[i] = item
                    if spec.item_max_len and len(item) > spec.item_max_len:
                        raise ValidationError(
                            spec.name, f"item[{i}]: exceeds max length {spec.item_max_len}"
                        )
                    if spec.item_pattern and item and not spec.item_pattern.fullmatch(item):
                        raise ValidationError(spec.name, f"item[{i}]: invalid format")

    return value


def validate_tool_args(args: dict[str, Any], schema: ToolSchema) -> dict[str, Any]:
    """Validate all tool arguments against a schema. Returns cleaned args dict."""
    if not isinstance(args, dict):
        raise ValidationError("args", "must be a JSON object")

    cleaned: dict[str, Any] = {}
    known_fields = {s.name for s in schema.fields}

    # Reject unknown fields
    for key in args:
        if key not in known_fields:
            raise ValidationError(key, f"unknown field for tool '{schema.tool_name}'")

    for spec in schema.fields:
        # Only process fields that are explicitly in args OR are required
        if spec.name in args:
            raw = args[spec.name]
            cleaned[spec.name] = validate_field(raw, spec)
        elif spec.required:
            # Required field missing - validate_field will raise error
            cleaned[spec.name] = validate_field(None, spec)
        elif spec.default is not None:
            # Field not in args, but has a default - include it
            cleaned[spec.name] = spec.default

    return cleaned


# ── String Sanitization ──


def strip_hidden_unicode(text: str) -> str:
    """Remove hidden Unicode characters (zero-width, directional overrides, etc.).

    Preserves normal whitespace (\\n, \\r, \\t) and all visible characters.
    """
    return "".join(
        ch
        for ch in text
        if ch in _ALLOWED_CONTROL or unicodedata.category(ch) not in _HIDDEN_CATEGORIES
    )


def normalize_unicode(text: str) -> str:
    """NFC-normalize Unicode text to canonical form."""
    return unicodedata.normalize("NFC", text)


def sanitize_string(text: str) -> str:
    """Full sanitization pipeline: normalize → strip hidden chars → strip edges."""
    text = normalize_unicode(text)
    text = strip_hidden_unicode(text)
    return text.strip()


# ── Response Sanitization ──


def sanitize_response(text: str, max_len: int = MAX_RESPONSE_LEN) -> str:
    """Sanitize and truncate a tool response before returning to caller."""
    text = sanitize_string(text)
    if len(text) > max_len:
        text = text[:max_len] + "\n…[response truncated]"
    return text


# ── JSON-RPC Envelope Validation ──


def validate_jsonrpc_request(req: dict[str, Any]) -> tuple[str, Any, dict[str, Any]]:
    """Validate a JSON-RPC 2.0 request envelope.

    Returns (method, id, params). Raises ValidationError on invalid structure.
    """
    if not isinstance(req, dict):
        raise ValidationError("request", "must be a JSON object")
    if req.get("jsonrpc") not in ("2.0", None):
        raise ValidationError("jsonrpc", "must be '2.0'")

    method = req.get("method")
    if method is not None and not isinstance(method, str):
        raise ValidationError("method", "must be a string")

    req_id = req.get("id")
    params = req.get("params", {})
    if not isinstance(params, dict):
        params = {}

    return method or "", req_id, params


# ── Tool Schemas (MCP Core) ──

SPAWN_RUN_SCHEMA = ToolSchema(
    tool_name="subagent_run",
    fields=[
        FieldSpec("task", str, max_len=MAX_MEDIUM_STRING),
        FieldSpec("tasks", list, item_type=str, item_max_len=MAX_MEDIUM_STRING),
        FieldSpec("agent", str, max_len=MAX_SHORT_STRING, pattern=_AGENT_NAME_RE),
        FieldSpec(
            "agents",
            list,
            item_type=str,
            item_max_len=MAX_SHORT_STRING,
            item_pattern=_AGENT_NAME_RE,
        ),
        # 0 = "not set" → falls through to config default via `0 or config_value`
        FieldSpec("max_turns", int, min_val=0, max_val=200),
        # Optional working directory for the subagent subprocess. Must be
        # absolute, exist, and be under subagent_cwd_allowed_roots. Validated
        # in SubagentManager.spawn.
        FieldSpec("cwd", str, max_len=MAX_MEDIUM_STRING),
    ],
)

LEARN_ADD_SCHEMA = ToolSchema(
    tool_name="memory_remember",
    fields=[
        FieldSpec("rule", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("category", str, allowed=ALLOWED_LESSON_CATEGORIES, default="knowledge"),
        FieldSpec("negative", str, max_len=MAX_SHORT_STRING),
    ],
)

LEARN_REMOVE_SCHEMA = ToolSchema(
    tool_name="memory_forget",
    fields=[
        FieldSpec("query", str, required=True, max_len=MAX_SHORT_STRING),
    ],
)

MEMORY_RECALL_SCHEMA = ToolSchema(
    tool_name="memory_recall",
    fields=[
        FieldSpec("query", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("deep", bool),
    ],
)

SPAWN_STATUS_SCHEMA = ToolSchema(
    tool_name="subagent_status",
    fields=[
        FieldSpec("agent_id", str, required=True, max_len=64),
    ],
)

SPAWN_LIST_SCHEMA = ToolSchema(tool_name="subagent_list")

FILE_SEND_SCHEMA = ToolSchema(
    tool_name="notify_attachment",
    fields=[
        FieldSpec("path", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("description", str, max_len=MAX_SHORT_STRING),
    ],
)

AUTONUDGE_STOP_SCHEMA = ToolSchema(
    tool_name="loop_nudge_stop",
    fields=[
        FieldSpec("reason", str, max_len=MAX_SHORT_STRING),
    ],
)

# ── Tool Schemas (MCP Artifacts) ──

# Derive from the artifact model's own ALLOWED_KINDS so the tool validator can never
# drift from what the store actually accepts (artifacts.models is stdlib-only — no cycle).
from personalclaw.artifacts.models import ALLOWED_KINDS as _ARTIFACT_KINDS

ARTIFACT_SAVE_SCHEMA = ToolSchema(
    tool_name="artifact_save",
    fields=[
        FieldSpec("name", str, required=True, max_len=200),
        # content OR content_file (the handler enforces one is present).
        FieldSpec("content", str, max_len=MAX_LONG_STRING),
        FieldSpec("kind", str, max_len=20, allowed=_ARTIFACT_KINDS),
        FieldSpec("slug", str, max_len=80),
        FieldSpec("description", str, max_len=2000),
        FieldSpec("tags", list, item_type=str, item_max_len=64, max_items=16),
        FieldSpec("content_file", str, max_len=MAX_SHORT_STRING),
    ],
)

ARTIFACT_GET_SCHEMA = ToolSchema(
    tool_name="artifact_get",
    fields=[
        FieldSpec("slug", str, required=True, max_len=80),
        FieldSpec("version", int, min_val=1),
    ],
)

ARTIFACT_UPDATE_SCHEMA = ToolSchema(
    tool_name="artifact_update",
    fields=[
        FieldSpec("slug", str, required=True, max_len=80),
        FieldSpec("content", str, max_len=MAX_LONG_STRING),
        FieldSpec("description", str, max_len=2000),
        FieldSpec("tags", list, item_type=str, item_max_len=64, max_items=16),
        FieldSpec("content_file", str, max_len=MAX_SHORT_STRING),
    ],
)

ARTIFACT_LIST_SCHEMA = ToolSchema(
    tool_name="artifact_list",
    fields=[
        FieldSpec("tag", str, max_len=64),
        FieldSpec("kind", str, max_len=20, allowed=_ARTIFACT_KINDS),
        FieldSpec("q", str, max_len=MAX_SHORT_STRING),
    ],
)

# ── Image generation (image_generate tool over the image_gen capability) ──
IMAGE_GENERATE_SCHEMA = ToolSchema(
    tool_name="image_generate",
    fields=[
        FieldSpec("prompt", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("size", str, max_len=32),  # "1024x1024" / "auto" — provider validates
        FieldSpec("name", str, max_len=200),  # artifact display name (else derived from prompt)
        # Edit mode: a prior kind:image artifact slug to edit in place.
        FieldSpec("edit_artifact", str, max_len=80),
    ],
)

_WORKFLOW_SCOPES = frozenset({"global", "workspace", "agent", "session"})

WORKFLOW_LIST_SCHEMA = ToolSchema(
    tool_name="workflow_list",
    fields=[
        FieldSpec("scope", str, max_len=20, allowed=_WORKFLOW_SCOPES),
        FieldSpec("tag", str, max_len=64),
    ],
)

WORKFLOW_GET_SCHEMA = ToolSchema(
    tool_name="workflow_get",
    fields=[
        FieldSpec("workflow_id", str, required=True, max_len=64),
    ],
)

PROMPT_RENDER_SCHEMA = ToolSchema(
    tool_name="prompt_render",
    fields=[
        FieldSpec("prompt_id", str, required=True, max_len=128),
        FieldSpec("vars", dict, default={}),  # variable name → value
    ],
)

SKILL_INVOKE_SCHEMA = ToolSchema(
    tool_name="skill_invoke",
    fields=[
        FieldSpec("name", str, required=True, max_len=128),
    ],
)

WORKFLOW_CREATE_SCHEMA = ToolSchema(
    tool_name="workflow_create",
    fields=[
        FieldSpec("name", str, required=True, max_len=64),
        FieldSpec("description", str, max_len=MAX_SHORT_STRING),
        FieldSpec("steps", list, item_type=dict, max_items=50),
        FieldSpec("scope", str, max_len=20, allowed=_WORKFLOW_SCOPES),
        FieldSpec("scope_ref", str, max_len=512),
        FieldSpec("match_text", str, max_len=MAX_SHORT_STRING),
        FieldSpec("tags", list, item_type=str, item_max_len=64, max_items=20),
    ],
)

WORKFLOW_PROMOTE_SCHEMA = ToolSchema(
    tool_name="workflow_promote",
    fields=[
        FieldSpec("workflow_id", str, required=True, max_len=64),
        FieldSpec("scope", str, required=True, max_len=20, allowed=_WORKFLOW_SCOPES),
        FieldSpec("scope_ref", str, max_len=512),
    ],
)

ARTIFACT_VERSIONS_SCHEMA = ToolSchema(
    tool_name="artifact_versions",
    fields=[FieldSpec("slug", str, required=True, max_len=80)],
)

ARTIFACT_DELETE_SCHEMA = ToolSchema(
    tool_name="artifact_delete",
    fields=[FieldSpec("slug", str, required=True, max_len=80)],
)

# ── Tool Schemas (MCP Cron) ──

SCHEDULE_ADD_SCHEMA = ToolSchema(
    tool_name="schedule_add",
    fields=[
        FieldSpec("name", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("message", str, required=True, max_len=MAX_MEDIUM_STRING),
        FieldSpec("every", int, min_val=60, max_val=86400 * 30),
        FieldSpec("cron_expr", str, max_len=100),
        FieldSpec("at", (int, float), min_val=0, max_val=4102444800),  # up to 2100
        FieldSpec("delay", (int, float), min_val=1, max_val=86400 * 30),  # 1s to 30 days
        FieldSpec("at_time", str, max_len=100),
        FieldSpec("agent", str, max_len=MAX_SHORT_STRING, pattern=_AGENT_NAME_RE),
        FieldSpec("silent", bool),
        FieldSpec("channel", str, max_len=CHANNEL_MAX_LEN, pattern=CHANNEL_ID_RE),
        FieldSpec("thread_ts", str, max_len=30, pattern=_MESSAGE_TS_RE),
        FieldSpec("approval_mode", str, max_len=10, pattern=re.compile(r"^(auto)?$")),
        FieldSpec("skip_dates", list, item_type=str, item_max_len=10, max_items=366, item_pattern=re.compile(r"^\d{4}-\d{2}-\d{2}$")),
        FieldSpec("timezone", str, max_len=50, pattern=re.compile(r"^[A-Za-z0-9_/+-]+$")),
        FieldSpec("persistent_session", bool),
        FieldSpec("strict_schedule", bool),
        # Zero-token execution modes (mutually exclusive with each other).
        FieldSpec("script", str, max_len=MAX_SHORT_STRING),
        FieldSpec("command", str, max_len=MAX_MEDIUM_STRING),
        FieldSpec("zt_timeout", int, min_val=0, max_val=86400),
    ],
)

CRON_REMOVE_SCHEMA = ToolSchema(
    tool_name="schedule_remove",
    fields=[
        FieldSpec("job_id", str, required=True, max_len=16, pattern=_JOB_ID_RE),
    ],
)

CRON_PAUSE_SCHEMA = ToolSchema(
    tool_name="schedule_pause",
    fields=[
        FieldSpec("job_id", str, required=True, max_len=16, pattern=_JOB_ID_RE),
    ],
)

CRON_RESUME_SCHEMA = ToolSchema(
    tool_name="schedule_resume",
    fields=[
        FieldSpec("job_id", str, required=True, max_len=16, pattern=_JOB_ID_RE),
    ],
)

# ── Tool Schemas (Hooks) ──

# bash/webhook/run-script are self-contained; the native actions reach in-process
# services via the action service accessor. Mirrors the registered action-provider
# catalog (action_providers.registry) — every provider a schedule trigger can run,
# a lifecycle trigger can run too. run-prompt/run-workflow (T1/T2) MUST be here or
# the lifecycle-trigger create path rejects them even though the UI offers them.
ALLOWED_HOOK_PROVIDERS = frozenset(
    {
        "bash", "webhook", "run-script", "notify", "send-message",
        "create-task", "invoke-agent", "run-prompt", "run-workflow",
    }
)

HOOK_CREATE_SCHEMA = ToolSchema(
    tool_name="hook_create",
    fields=[
        FieldSpec("name", str, required=True, max_len=200),
        FieldSpec("provider", str, required=True, allowed=ALLOWED_HOOK_PROVIDERS),
        FieldSpec("provider_config", dict, required=True),
        FieldSpec("event", str, required=True, allowed=ALLOWED_HOOK_EVENTS),
        FieldSpec("matcher", str, max_len=500, default=""),  # optional: empty = match all
        FieldSpec("timeout", int, min_val=1, max_val=300, default=30),
        FieldSpec("enabled", bool, default=True),
    ],
)

HOOK_UPDATE_SCHEMA = ToolSchema(
    tool_name="hook_update",
    fields=[
        FieldSpec("name", str, max_len=200),  # optional on update
        FieldSpec("provider", str, allowed=ALLOWED_HOOK_PROVIDERS),
        FieldSpec("provider_config", dict),
        FieldSpec("event", str, allowed=ALLOWED_HOOK_EVENTS),
        FieldSpec("matcher", str, max_len=500),  # optional: empty = match all
        FieldSpec("timeout", int, min_val=1, max_val=300),
        FieldSpec("enabled", bool),
    ],
)

# ── Tool Schemas (File I/O) ──

FILE_READ_SCHEMA = ToolSchema(
    tool_name="file_read",
    fields=[
        FieldSpec(
            "path", str, required=True, max_len=4096, pattern=re.compile(r"^[~/][-\w.@~/ ]+$")
        ),
    ],
)

FILE_WRITE_SCHEMA = ToolSchema(
    tool_name="file_write",
    fields=[
        FieldSpec(
            "path", str, required=True, max_len=4096, pattern=re.compile(r"^[~/][-\w.@~/ ]+$")
        ),
        FieldSpec("content", str, required=True, max_len=512000),
    ],
)

SEND_MESSAGE_SCHEMA = ToolSchema(
    tool_name="notify",
    fields=[
        FieldSpec("text", str, required=True, max_len=MAX_MEDIUM_STRING),
        FieldSpec("title", str, max_len=MAX_SHORT_STRING),
        FieldSpec("blocks", list, item_type=dict, max_items=50),
        FieldSpec("channel", str, max_len=CHANNEL_MAX_LEN, pattern=CHANNEL_ID_RE),
        FieldSpec("user", str, max_len=USER_MAX_LEN, pattern=USER_ID_RE),
        FieldSpec("unfurl_links", bool),
        FieldSpec("unfurl_media", bool),
        FieldSpec("thread_ts", str, max_len=30, pattern=_MESSAGE_TS_RE),
        FieldSpec("reply_broadcast", bool),
        FieldSpec("session", str, max_len=MAX_SHORT_STRING, pattern=re.compile(r"^(origin|channel)$")),
        FieldSpec("caller_session", str, max_len=MAX_SHORT_STRING, pattern=re.compile(r"^(cron:[a-zA-Z0-9]+)?$")),
    ],
)

WAIT_SCHEMA = ToolSchema(
    tool_name="wait",
    fields=[
        FieldSpec("seconds", int, required=True, min_val=60, max_val=1800),
        FieldSpec("reason", str, required=True, max_len=MAX_SHORT_STRING),
    ],
)

REGISTER_HOOK_SCHEMA = ToolSchema(
    tool_name="hook_register",
    fields=[
        FieldSpec("hook_id", str, required=True, max_len=MAX_SHORT_STRING),
        FieldSpec("context_summary", str, required=True, max_len=MAX_MEDIUM_STRING),
    ],
)

# ── Tool Schemas (Channel Reactions) ──

# Channel emoji names: alphanumeric, underscores, hyphens, and plus signs
_EMOJI_NAME_RE = re.compile(r"^[a-zA-Z0-9+][a-zA-Z0-9_+\-]{0,98}[a-zA-Z0-9]$|^[a-zA-Z0-9+]$")

ADD_REACTION_SCHEMA = ToolSchema(
    tool_name="add_reaction",
    fields=[
        FieldSpec("channel", str, required=True, max_len=CHANNEL_MAX_LEN, pattern=CHANNEL_ID_RE),
        FieldSpec("timestamp", str, required=True, max_len=30, pattern=_MESSAGE_TS_RE),
        FieldSpec("reaction", str, required=True, max_len=100, pattern=_EMOJI_NAME_RE),
    ],
)

# ── Schema Registry ──

MCP_CORE_SCHEMAS: dict[str, ToolSchema] = {
    "subagent_run": SPAWN_RUN_SCHEMA,
    "subagent_list": SPAWN_LIST_SCHEMA,
    "subagent_status": SPAWN_STATUS_SCHEMA,
    "memory_remember": LEARN_ADD_SCHEMA,
    "memory_forget": LEARN_REMOVE_SCHEMA,
    "memory_recall": MEMORY_RECALL_SCHEMA,
    "notify": SEND_MESSAGE_SCHEMA,
    "wait": WAIT_SCHEMA,
    "hook_register": REGISTER_HOOK_SCHEMA,
    "notify_attachment": FILE_SEND_SCHEMA,
    "loop_nudge_stop": AUTONUDGE_STOP_SCHEMA,
    "artifact_save": ARTIFACT_SAVE_SCHEMA,
    "artifact_get": ARTIFACT_GET_SCHEMA,
    "artifact_update": ARTIFACT_UPDATE_SCHEMA,
    "artifact_list": ARTIFACT_LIST_SCHEMA,
    "artifact_versions": ARTIFACT_VERSIONS_SCHEMA,
    "artifact_delete": ARTIFACT_DELETE_SCHEMA,
    "image_generate": IMAGE_GENERATE_SCHEMA,
    "workflow_list": WORKFLOW_LIST_SCHEMA,
    "workflow_get": WORKFLOW_GET_SCHEMA,
    "workflow_run": WORKFLOW_GET_SCHEMA,
    "prompt_render": PROMPT_RENDER_SCHEMA,
    "workflow_create": WORKFLOW_CREATE_SCHEMA,
    "workflow_promote": WORKFLOW_PROMOTE_SCHEMA,
    "skill_invoke": SKILL_INVOKE_SCHEMA,
}

# Keyed by the live MCP tool names (schedule_*). The schema objects already
# carry tool_name="schedule_*"; the dict keys must match so _validate_args'
# MCP_SCHEDULE_SCHEMAS.get(name) lookup actually finds them.
MCP_SCHEDULE_SCHEMAS: dict[str, ToolSchema] = {
    "schedule_add": SCHEDULE_ADD_SCHEMA,
    "schedule_update": ToolSchema(
        tool_name="schedule_update",
        fields=[
            FieldSpec("job_id", str, required=True, max_len=16, pattern=_JOB_ID_RE),
            FieldSpec("name", str, max_len=MAX_SHORT_STRING),
            FieldSpec("message", str, max_len=MAX_MEDIUM_STRING),
            FieldSpec("cron_expr", str, max_len=100),
            FieldSpec("every", int, min_val=60, max_val=86400 * 30),
            FieldSpec("agent", str, max_len=MAX_SHORT_STRING, pattern=_AGENT_NAME_RE),
            FieldSpec("channel", str, max_len=CHANNEL_MAX_LEN, pattern=CHANNEL_ID_RE),
            FieldSpec("thread_ts", str, max_len=30, pattern=_MESSAGE_TS_RE),
            FieldSpec("approval_mode", str, max_len=10, pattern=re.compile(r"^(auto)?$")),
            FieldSpec("silent", bool),
            FieldSpec("strict_schedule", bool),
            FieldSpec("skip_dates", list, item_type=str, item_max_len=10, max_items=366, item_pattern=re.compile(r"^\d{4}-\d{2}-\d{2}$")),
            FieldSpec("timezone", str, max_len=50, pattern=re.compile(r"^[A-Za-z0-9_/+-]+$")),
            FieldSpec("script", str, max_len=MAX_SHORT_STRING),
            FieldSpec("command", str, max_len=MAX_MEDIUM_STRING),
            FieldSpec("zt_timeout", int, min_val=0, max_val=86400),
        ],
    ),
    "schedule_remove": CRON_REMOVE_SCHEMA,
    "schedule_pause": CRON_PAUSE_SCHEMA,
    "schedule_resume": CRON_RESUME_SCHEMA,
    "schedule_natural": ToolSchema(
        tool_name="schedule_natural",
        fields=[
            FieldSpec("name", str, required=True, max_len=MAX_SHORT_STRING),
            FieldSpec("message", str, required=True, max_len=MAX_MEDIUM_STRING),
            FieldSpec("cadence", str, required=True, max_len=MAX_SHORT_STRING),
            FieldSpec("channel", str, max_len=CHANNEL_MAX_LEN, pattern=CHANNEL_ID_RE),
            FieldSpec("silent", bool),
        ],
    ),
}

MCP_HUB_SCHEMAS: dict[str, ToolSchema] = {}


# ── Response Schemas ──


@dataclass
class McpTextContent:
    """Type-safe MCP TextContent response — the only content type our tools return."""

    type: str  # always "text"
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "text": self.text}


def build_tool_response(text: str, max_len: int = MAX_RESPONSE_LEN) -> dict[str, Any]:
    """Build a validated, sanitized MCP tools/call response.

    Returns the ``result`` payload for a JSON-RPC response:
    ``{"content": [{"type": "text", "text": "..."}]}``

    This is the single exit point for all tool responses — ensures every
    response conforms to the MCP TextContent schema and is sanitized.
    """
    text = sanitize_response(text, max_len)
    content = McpTextContent(type="text", text=text)
    return {"content": [content.to_dict()]}


def validate_jsonrpc_response(resp: dict[str, Any]) -> dict[str, Any]:
    """Validate a JSON-RPC 2.0 response envelope before writing to stdout.

    Ensures: has ``jsonrpc``, ``id``, and either ``result`` or ``error``.
    """
    if not isinstance(resp, dict):
        raise ValidationError("response", "must be a JSON object")
    if "id" not in resp:
        raise ValidationError("response", "missing id")
    if "result" not in resp and "error" not in resp:
        raise ValidationError("response", "must have result or error")
    resp["jsonrpc"] = "2.0"
    return resp


# ── Dashboard API Validation Helpers ──


def validate_api_body(body: Any, max_size: int = 100_000) -> dict[str, Any]:
    """Validate a parsed JSON request body from aiohttp."""
    if not isinstance(body, dict):
        raise ValidationError("body", "must be a JSON object")
    raw = str(body)
    if len(raw) > max_size:
        raise ValidationError("body", f"exceeds max size {max_size}")
    return body


def validate_string_field(
    body: dict[str, Any],
    field_name: str,
    *,
    required: bool = False,
    max_len: int = MAX_MEDIUM_STRING,
    allowed: frozenset[str] | None = None,
) -> str:
    """Extract and validate a string field from a request body."""
    val = body.get(field_name)
    if val is None:
        if required:
            raise ValidationError(field_name, "required")
        return ""
    if not isinstance(val, str):
        raise ValidationError(field_name, "must be a string")
    val = sanitize_string(val)
    if not val and required:
        raise ValidationError(field_name, "required (empty after sanitization)")
    if max_len and len(val) > max_len:
        raise ValidationError(field_name, f"exceeds max length {max_len}")
    if allowed and val not in allowed:
        raise ValidationError(field_name, f"must be one of: {', '.join(sorted(allowed))}")
    return val


# ── AskUserQuestion (interactive question cards) ──

# Defensive caps so a hostile/garbled tool payload can't blow up the card UI.
_AUQ_MAX_QUESTIONS = 10
_AUQ_MAX_OPTIONS = 12
_AUQ_TEXT_CAP = 2000
_AUQ_LABEL_CAP = 400


def validate_ask_user_question(tool_input: Any) -> list[dict[str, Any]]:
    """Normalize a Claude Code ``AskUserQuestion`` tool input into a render list.

    Input schema (top level): ``{"questions": [{"question": str, "header"?: str,
    "multiSelect"?: bool, "options": [{"label": str, "description"?: str}]}]}``.
    Returns ``[{question, header, multiSelect, options: [{label, description}]}]``
    with all strings truncated and counts capped. Raises :class:`ValidationError`
    on a structurally unusable payload (no question with options).
    """
    if not isinstance(tool_input, dict):
        raise ValidationError("ask_user_question", "tool input must be an object")
    raw_questions = tool_input.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValidationError("ask_user_question", "questions must be a non-empty list")

    out: list[dict[str, Any]] = []
    for rq in raw_questions[:_AUQ_MAX_QUESTIONS]:
        if not isinstance(rq, dict):
            continue
        q_text = str(rq.get("question", "")).strip()[:_AUQ_TEXT_CAP]
        if not q_text:
            continue
        header = str(rq.get("header", "")).strip()[:_AUQ_LABEL_CAP]
        multi = bool(rq.get("multiSelect", False))
        raw_options = rq.get("options")
        options: list[dict[str, str]] = []
        if isinstance(raw_options, list):
            for ro in raw_options[:_AUQ_MAX_OPTIONS]:
                if isinstance(ro, dict):
                    label = str(ro.get("label", "")).strip()[:_AUQ_LABEL_CAP]
                    desc = str(ro.get("description", "")).strip()[:_AUQ_TEXT_CAP]
                elif isinstance(ro, str):
                    label, desc = ro.strip()[:_AUQ_LABEL_CAP], ""
                else:
                    continue
                if label:
                    options.append({"label": label, "description": desc})
        if not options:
            continue  # a question with no usable options can't be answered via the card
        out.append({"question": q_text, "header": header, "multiSelect": multi, "options": options})

    if not out:
        raise ValidationError("ask_user_question", "no usable questions in payload")
    return out
