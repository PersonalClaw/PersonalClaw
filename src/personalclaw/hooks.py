"""Config-driven hook system for PersonalClaw's message pipeline.

Hooks intercept messages and tool calls based on rules in config.json.
Supports declarative rules and executable script hooks with timeout/sandboxing.
"""

import asyncio
import fnmatch
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.security import is_denied, is_sensitive_bash_command, is_sensitive_path

logger = logging.getLogger(__name__)


# ── Hook Results ──

# Message hook action constants
HOOK_PASSTHROUGH = "passthrough"
HOOK_REPLY = "reply"
HOOK_MODIFY = "modify"
HOOK_INJECT_CONTEXT = "inject_context"

# Tool hook action constants
TOOL_ALLOW = "allow"
TOOL_AUTO_APPROVE = "auto_approve"
TOOL_DENY = "deny"

# Script hook events — agent loop lifecycle
HOOK_EVENT_AGENT_SPAWN = "AgentSpawn"
HOOK_EVENT_USER_PROMPT_SUBMIT = "UserPromptSubmit"
HOOK_EVENT_PRE_TOOL_USE = "PreToolUse"
HOOK_EVENT_POST_TOOL_USE = "PostToolUse"
HOOK_EVENT_STOP = "Stop"
HOOK_EVENT_PRE_RESPONSE = "PreResponse"
HOOK_EVENT_POST_RESPONSE = "PostResponse"
HOOK_EVENT_SESSION_START = "SessionStart"
HOOK_EVENT_SESSION_END = "SessionEnd"
HOOK_EVENT_MEMORY_WRITE = "MemoryWrite"
HOOK_EVENT_ERROR = "Error"
HOOK_EVENT_CONTEXT_COMPACT = "ContextCompact"
HOOK_EVENT_SUBAGENT_SPAWN = "SubagentSpawn"
HOOK_EVENT_TASK_COMPLETE = "TaskComplete"
HOOK_EVENT_APPROVAL_REQUEST = "ApprovalRequest"

HOOK_EVENTS = (
    HOOK_EVENT_AGENT_SPAWN,
    HOOK_EVENT_SESSION_START,
    HOOK_EVENT_USER_PROMPT_SUBMIT,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_POST_TOOL_USE,
    HOOK_EVENT_PRE_RESPONSE,
    HOOK_EVENT_POST_RESPONSE,
    HOOK_EVENT_MEMORY_WRITE,
    HOOK_EVENT_CONTEXT_COMPACT,
    HOOK_EVENT_SUBAGENT_SPAWN,
    HOOK_EVENT_TASK_COMPLETE,
    HOOK_EVENT_APPROVAL_REQUEST,
    HOOK_EVENT_ERROR,
    HOOK_EVENT_SESSION_END,
    HOOK_EVENT_STOP,
)


# ── Lifecycle event catalog ──
# The authoritative description of each lifecycle event and the ``$variables`` an
# action templated on it can interpolate. This lives next to ``_fire`` (which
# assembles the event payload below) so the catalog and the payload it documents
# can never drift. ``GET /api/triggers/variables`` serves this to both UIs — they
# do NOT mirror it. Each var is a ``$NAME`` placeholder substituted by
# ``action_providers.template.render_template`` (``$EVENT``/``$CONTEXT`` plus any
# payload key). ``blocking`` marks events whose action can short-circuit the loop.

# Every event carries these (the shared base every fire assembles + the matcher's
# context string).
_LIFECYCLE_BASE_VARS = ("$EVENT", "$CONTEXT", "$cwd")

LIFECYCLE_EVENT_CATALOG: tuple[dict, ...] = (
    {"event": HOOK_EVENT_AGENT_SPAWN, "label": "Agent spawn",
     "desc": "A new agent session is created.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_SESSION_START, "label": "Session start",
     "desc": "A chat/agent session begins.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_USER_PROMPT_SUBMIT, "label": "User prompt submit",
     "desc": "The user submits a turn — before the agent runs.",
     "vars": (*_LIFECYCLE_BASE_VARS, "$prompt")},
    {"event": HOOK_EVENT_PRE_TOOL_USE, "label": "Pre tool use",
     "desc": "Before a tool runs — can block it.",
     "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name", "$tool_input"), "blocking": True},
    {"event": HOOK_EVENT_POST_TOOL_USE, "label": "Post tool use",
     "desc": "After a tool runs.",
     "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name", "$tool_input", "$tool_response")},
    {"event": HOOK_EVENT_PRE_RESPONSE, "label": "Pre response",
     "desc": "Before the agent streams its reply.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_POST_RESPONSE, "label": "Post response",
     "desc": "After the agent finishes its reply.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_MEMORY_WRITE, "label": "Memory write",
     "desc": "The agent writes a memory/lesson.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_CONTEXT_COMPACT, "label": "Context compact",
     "desc": "The conversation context is summarized.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_SUBAGENT_SPAWN, "label": "Subagent spawn",
     "desc": "A subagent is spawned.",
     "vars": (*_LIFECYCLE_BASE_VARS, "$subagent_id", "$parent_session_key", "$agent_role")},
    {"event": HOOK_EVENT_TASK_COMPLETE, "label": "Task complete",
     "desc": "A task finishes.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_APPROVAL_REQUEST, "label": "Approval request",
     "desc": "A tool needs approval.", "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name")},
    {"event": HOOK_EVENT_ERROR, "label": "Error",
     "desc": "An error occurs in the loop.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_SESSION_END, "label": "Session end",
     "desc": "A session ends.", "vars": _LIFECYCLE_BASE_VARS},
    {"event": HOOK_EVENT_STOP, "label": "Stop",
     "desc": "The agent loop stops.", "vars": _LIFECYCLE_BASE_VARS},
)


@dataclass
class HookResult:
    """Result of running message hooks."""

    action: str  # HOOK_PASSTHROUGH, HOOK_REPLY, HOOK_MODIFY, HOOK_INJECT_CONTEXT
    text: str = ""

    @staticmethod
    def passthrough() -> "HookResult":
        return HookResult(action=HOOK_PASSTHROUGH)

    @staticmethod
    def reply(text: str) -> "HookResult":
        return HookResult(action=HOOK_REPLY, text=text)

    @staticmethod
    def modify(text: str) -> "HookResult":
        return HookResult(action=HOOK_MODIFY, text=text)

    @staticmethod
    def inject_context(text: str) -> "HookResult":
        return HookResult(action=HOOK_INJECT_CONTEXT, text=text)


@dataclass
class ToolHookResult:
    action: str  # TOOL_ALLOW, TOOL_AUTO_APPROVE, TOOL_DENY
    reason: str = ""

    @staticmethod
    def allow() -> "ToolHookResult":
        return ToolHookResult(action=TOOL_ALLOW)

    @staticmethod
    def auto_approve() -> "ToolHookResult":
        return ToolHookResult(action=TOOL_AUTO_APPROVE)

    @staticmethod
    def deny(reason: str) -> "ToolHookResult":
        return ToolHookResult(action=TOOL_DENY, reason=reason)


# ── Config Types ──


@dataclass
class ContextRule:
    """Inject context when any trigger keyword matches."""

    triggers: list[str] = field(default_factory=list)
    context: str = ""


@dataclass
class AutoReplyHook:
    """Auto-reply without LLM for pattern matches."""

    pattern: str = ""
    reply: str = ""
    exact: bool = False


@dataclass
class TransformHook:
    """Transform message before sending to LLM."""

    pattern: str = ""
    prefix: str = ""
    suffix: str = ""


@dataclass
class HooksConfig:
    """Loaded from config.json ``hooks`` section."""

    auto_approve_tools: list[str] = field(default_factory=list)
    auto_approve_sources: list[str] = field(default_factory=list)
    auto_approve_subagent_spawn: bool = False
    auto_approve_subagent_tools: bool = False
    auto_deny_tools: list[str] = field(default_factory=list)
    auto_replies: list[AutoReplyHook] = field(default_factory=list)
    transforms: list[TransformHook] = field(default_factory=list)
    context_rules: list[ContextRule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "HooksConfig":
        """Parse hooks config from a dict (config.json ``hooks`` section)."""
        auto_replies = [
            AutoReplyHook(
                pattern=h.get("pattern", ""),
                reply=h.get("reply", ""),
                exact=h.get("exact", False),
            )
            for h in data.get("auto_replies", [])
        ]
        transforms = [
            TransformHook(
                pattern=h.get("pattern", ""),
                prefix=h.get("prefix", ""),
                suffix=h.get("suffix", ""),
            )
            for h in data.get("transforms", [])
        ]
        context_rules = [
            ContextRule(
                triggers=r.get("triggers", []),
                context=r.get("context", ""),
            )
            for r in data.get("context_rules", [])
        ]
        return cls(
            auto_approve_tools=data.get("auto_approve_tools", []),
            auto_approve_sources=data.get("auto_approve_sources", []),
            auto_approve_subagent_spawn=bool(data.get("auto_approve_subagent_spawn", False)),
            auto_approve_subagent_tools=bool(data.get("auto_approve_subagent_tools", False)),
            auto_deny_tools=data.get("auto_deny_tools", []),
            auto_replies=auto_replies,
            transforms=transforms,
            context_rules=context_rules,
        )


# ── HookManager ──


class HookManager:
    """Process messages and tool calls through config-driven rules."""

    def __init__(self, config: HooksConfig | None = None):
        self._config = config or HooksConfig()

    def reload(self, config: HooksConfig) -> None:
        """Hot-reload hooks config."""
        self._config = config

    @property
    def auto_approve_subagent_spawn(self) -> bool:
        return self._config.auto_approve_subagent_spawn

    @property
    def auto_approve_subagent_tools(self) -> bool:
        return self._config.auto_approve_subagent_tools

    # ── Message hooks ──

    def on_message(self, text: str) -> HookResult:
        """Run message hooks. Returns first match or passthrough."""
        lower = text.lower()

        # Auto-replies (first match wins)
        for ar_hook in self._config.auto_replies:
            if ar_hook.exact:
                if lower == ar_hook.pattern.lower():
                    return HookResult.reply(ar_hook.reply)
            else:
                if ar_hook.pattern.lower() in lower:
                    return HookResult.reply(ar_hook.reply)

        # Transforms (first match wins)
        for tf_hook in self._config.transforms:
            if tf_hook.pattern.lower() in lower:
                modified = text
                if tf_hook.prefix:
                    modified = f"{tf_hook.prefix}\n{modified}"
                if tf_hook.suffix:
                    modified = f"{modified}\n{tf_hook.suffix}"
                return HookResult.modify(modified)

        # Context injection (all matching rules)
        injected: list[str] = []
        for rule in self._config.context_rules:
            if any(t.lower() in lower for t in rule.triggers):
                injected.append(rule.context)
        if injected:
            return HookResult.inject_context("\n\n".join(injected))

        return HookResult.passthrough()

    # ── Tool hooks ──

    def on_tool_call(self, tool_name: str) -> ToolHookResult:
        """Check if a tool should be auto-approved, denied, or handled normally."""
        # Strip display prefixes (e.g. "Running: ls *" → "ls *") so config
        # patterns like "ls" or "rm *" match without the prefix.
        normalized = _normalize_tool_name(tool_name)

        # Sensitive path protection (always enforced, before all other checks)
        if tool_name.startswith("Reading "):
            # fs_read / ReadFile — check the path
            if is_sensitive_path(normalized):
                return ToolHookResult.deny(f"Blocked: access to sensitive path: {normalized}")
        elif tool_name.startswith("Running: "):
            # execute_bash — check for reads of sensitive paths
            reason = is_sensitive_bash_command(normalized)
            if reason:
                return ToolHookResult.deny(reason)

        # Built-in security deny list (always enforced)
        reason = is_denied(normalized, self._config.auto_deny_tools) or is_denied(
            tool_name, self._config.auto_deny_tools
        )
        if reason:
            return ToolHookResult.deny(reason)

        # Match against both original title (preserves prefixes like
        # "Running: ") and the normalized stripped name.
        for pattern in self._config.auto_approve_tools:
            if _tool_matches(pattern, tool_name) or _tool_matches(pattern, normalized):
                return ToolHookResult.auto_approve()

        return ToolHookResult.allow()


# Display prefixes that the ACP agent adds to tool titles
_TOOL_TITLE_PREFIXES = ("Running: ", "Reading ")


def _normalize_tool_name(tool_name: str) -> str:
    """Strip display prefixes so hook patterns match the actual tool/command name."""
    for prefix in _TOOL_TITLE_PREFIXES:
        if tool_name.startswith(prefix):
            return tool_name[len(prefix) :]
    return tool_name


def _tool_matches(pattern: str, tool_name: str) -> bool:
    """Match a tool pattern against a tool name.

    Supports: exact, ``prefix*``, ``*suffix``, ``*contains*``, ``*`` (all).
    Case-insensitive.
    """
    if pattern == "*":
        return True
    return fnmatch.fnmatch(tool_name.lower(), pattern.lower())


def validate_file_path(raw: str) -> str | None:
    """Validate and canonicalize a file path for dashboard file I/O.

    Enforces: is_sensitive_path(), realpath canonicalization.
    Returns the canonical path or None if rejected.
    """
    import os

    if not raw:
        return None
    path = os.path.realpath(os.path.expanduser(raw))
    if is_sensitive_path(path):
        return None
    return path


def safe_read_file(path: str) -> str:
    """Read a file after enforcing ``is_sensitive_path``.

    Raises ``PermissionError`` if the path is sensitive.
    """
    from pathlib import Path

    resolved = str(Path(path).expanduser().resolve())
    if is_sensitive_path(resolved):
        raise PermissionError(f"Blocked: access to sensitive path: {resolved}")
    return Path(resolved).read_text(encoding="utf-8")


MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB safety cap


class FileTooLargeError(Exception):
    """Raised when a file exceeds MAX_FILE_BYTES."""


def safe_read_file_bytes(raw: str) -> bytes | None:
    """Read file bytes through centralized is_sensitive_path() enforcement.

    Returns file content as bytes, or None if path is rejected or unreadable.
    """
    path = validate_file_path(raw)
    if path is None:
        return None
    from pathlib import Path

    p = Path(path)
    try:
        with p.open("rb") as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise FileTooLargeError(f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB safety cap")
        return data
    except OSError:
        return None


# ── Script Hooks ──


@dataclass
class ScriptHook:
    """Executable hook that runs on a trigger event via a registered ActionProvider.

    Each hook record names its provider (``bash``, ``webhook``, …) and stores
    provider-specific config (e.g. ``{"command": "..."}`` for bash,
    ``{"url": "...", "method": "POST"}`` for webhook). The provider's
    ``execute()`` method handles the actual side-effect.

    Bash provider follows the ACP agent hook semantics:
    - exit 0: success (stdout → context for AgentSpawn/UserPromptSubmit)
    - exit 2: block tool (PreToolUse only, stderr → LLM)
    - other: warning (stderr shown to user)
    """

    id: str = ""
    name: str = ""
    event: str = HOOK_EVENT_USER_PROMPT_SUBMIT
    matcher: str = ""  # tool matcher for PreToolUse/PostToolUse (empty = all tools)
    provider: str = "bash"
    provider_config: dict = field(default_factory=dict)
    timeout: int = 30  # seconds
    enabled: bool = True
    last_run: float = 0.0
    last_status: str = ""  # "ok", "error", "timeout", "blocked"
    run_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ScriptHook":
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            event=data.get("event", HOOK_EVENT_USER_PROMPT_SUBMIT),
            matcher=data.get("matcher", ""),
            provider=data.get("provider", "bash"),
            provider_config=dict(data.get("provider_config") or {}),
            timeout=data.get("timeout", 30),
            enabled=data.get("enabled", True),
            last_run=data.get("last_run", 0.0),
            last_status=data.get("last_status", ""),
            run_count=data.get("run_count", 0),
        )


@dataclass
class ScriptHookResult:
    """Result of executing a script hook."""

    hook_id: str
    hook_name: str
    event: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    error: str = ""
    duration_ms: int = 0

    @property
    def blocked(self) -> bool:
        """PreToolUse exit code 2 = block tool."""
        return self.exit_code == 2

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


async def run_script_hook(
    hook: ScriptHook, context: str = "", hook_event: dict | None = None
) -> ScriptHookResult:
    """Dispatch hook execution through its registered ActionProvider."""
    import os

    from personalclaw.action_providers import get_action_provider
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()

    if hook_event is None:
        hook_event = {"hook_event_name": hook.event, "cwd": os.getcwd()}

    provider = get_action_provider(hook.provider)
    if provider is None:
        hook.last_run = time.time()
        hook.last_status = "error"
        hook.run_count += 1
        return ScriptHookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            error=f"Unknown action provider {hook.provider!r}",
        )

    ctx = ActionContext(event=hook.event, context=context, payload=hook_event)
    result = await provider.execute(hook.provider_config, ctx, timeout=hook.timeout)

    hook.last_run = time.time()
    if result.blocked:
        hook.last_status = "blocked"
    elif result.success:
        # Honest "started ≠ succeeded" (T7): a fire-and-forget action (run-prompt/
        # run-workflow/invoke-agent) only LAUNCHED a background turn — record
        # "launched" so the lifecycle-trigger badge doesn't overstate it as a
        # verified success, matching the schedule path's run-record status.
        hook.last_status = "launched" if result.outcome == "launched" else "ok"
    elif result.error and "Timed out" in result.error:
        hook.last_status = "timeout"
    else:
        hook.last_status = "error"
    hook.run_count += 1

    return ScriptHookResult(
        hook_id=hook.id,
        hook_name=hook.name,
        event=hook.event,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code if result.exit_code is not None else -1,
        error=result.error,
        duration_ms=result.duration_ms,
    )


# ── Script Hook Store (persistence) ──

_HOOKS_FILE = "hooks.json"


class ScriptHookStore:
    """Persist script hooks to ~/.personalclaw/hooks.json."""

    def __init__(self, config_dir: Path | None = None):
        from personalclaw.config.loader import config_dir as _cfg_dir

        self._dir = config_dir or _cfg_dir()
        self._path = self._dir / _HOOKS_FILE
        self._hooks: dict[str, ScriptHook] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for h in data.get("hooks", []):
                hook = ScriptHook.from_dict(h)
                self._hooks[hook.id] = hook
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load hooks: %s", exc)

    def _save(self) -> None:
        data = {"hooks": [h.to_dict() for h in self._hooks.values()]}
        atomic_write(self._path, json.dumps(data, indent=2))

    def list_all(self) -> list[ScriptHook]:
        return list(self._hooks.values())

    def get(self, hook_id: str) -> ScriptHook | None:
        return self._hooks.get(hook_id)

    def create(self, data: dict) -> ScriptHook:
        hook = ScriptHook.from_dict(data)
        if not hook.id:
            hook.id = str(uuid.uuid4())[:8]
        self._hooks[hook.id] = hook
        self._save()
        return hook

    def update(self, hook_id: str, data: dict) -> ScriptHook | None:
        hook = self._hooks.get(hook_id)
        if not hook:
            return None
        if "event" in data and data["event"] not in HOOK_EVENTS:
            raise ValueError(f"invalid event: {data['event']}")
        if "timeout" in data:
            t = data["timeout"]
            if not isinstance(t, int) or not (1 <= t <= 300):
                raise ValueError("timeout must be an integer between 1 and 300")
        if "provider_config" in data and not isinstance(data["provider_config"], dict):
            raise ValueError("provider_config must be an object")
        for k in ("name", "event", "matcher", "provider", "provider_config", "timeout", "enabled"):
            if k in data:
                setattr(hook, k, data[k])
        self._save()
        return hook

    def delete(self, hook_id: str) -> bool:
        if hook_id in self._hooks:
            del self._hooks[hook_id]
            self._save()
            return True
        return False

    def toggle(self, hook_id: str) -> ScriptHook | None:
        hook = self._hooks.get(hook_id)
        if not hook:
            return None
        hook.enabled = not hook.enabled
        self._save()
        return hook

    async def fire(
        self,
        event: str,
        context: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
        tool_response: dict | None = None,
        subagent_id: str = "",
        parent_session_key: str = "",
        agent_role: str = "",
    ) -> list[ScriptHookResult]:
        """Fire all enabled hooks matching the given event. Returns results.

        For PreToolUse/PostToolUse, matcher filters by tool name.
        For AgentSpawn/UserPromptSubmit/Stop, all hooks for that event fire.

        ``subagent_id``/``parent_session_key``/``agent_role`` (E11-P3) attribute a
        fire to the subagent that triggered it; they ride the event payload and
        are absent for top-level fires.
        """
        return await self._fire(
            event,
            context=context,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
            hook_ids=None,
            subagent_id=subagent_id,
            parent_session_key=parent_session_key,
            agent_role=agent_role,
        )

    async def fire_for_ids(
        self,
        event: str,
        hook_ids: "set[str] | list[str] | None",
        context: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
        tool_response: dict | None = None,
        depth: int = 0,
        subagent_id: str = "",
        parent_session_key: str = "",
        agent_role: str = "",
    ) -> list[ScriptHookResult]:
        """Fire only the hooks in ``hook_ids`` that match ``event`` (agent-scoped).

        The agent-scoped firing primitive (E3): an agent references a subset of the
        hook library and only those hooks fire for it — global hooks are NOT run.
        ``hook_ids=None`` is treated as "no scoped hooks" → fires nothing (use
        :meth:`fire` for the global set). An empty collection likewise fires nothing.
        Matcher/enabled/event filtering is identical to :meth:`fire`.

        ``depth`` is the recursion depth of the originating agent (0 = the user's
        top-level agent); it is injected into the event payload as
        ``__hook_depth`` so the ``invoke-agent`` action can bound spawn recursion.

        ``subagent_id``/``parent_session_key``/``agent_role`` (E11-P3) attribute the
        fire to a subagent; additive optional payload fields, absent at top level.
        """
        if not hook_ids:
            return []
        allow = set(hook_ids)
        return await self._fire(
            event,
            context=context,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
            hook_ids=allow,
            depth=depth,
            subagent_id=subagent_id,
            parent_session_key=parent_session_key,
            agent_role=agent_role,
        )

    async def _fire(
        self,
        event: str,
        *,
        context: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
        tool_response: dict | None = None,
        hook_ids: "set[str] | None" = None,
        depth: int = 0,
        subagent_id: str = "",
        parent_session_key: str = "",
        agent_role: str = "",
    ) -> list[ScriptHookResult]:
        """Shared firing core. ``hook_ids`` (when not None) restricts firing to
        that allow-set of hook ids; None fires every enabled matching hook."""
        import os

        results = []
        # Build base hook event
        hook_event: dict = {"hook_event_name": event, "cwd": os.getcwd()}
        # Recursion depth for invoke-agent's spawn bound (E3-P3). Reserved key.
        hook_event["__hook_depth"] = depth
        # Subagent attribution (E11-P3): present only when a subagent fires, so a
        # top-level chat fire's payload omits them. Hook scripts read these to
        # attribute a tool call to the subagent that made it.
        if subagent_id:
            hook_event["subagent_id"] = subagent_id
        if parent_session_key:
            hook_event["parent_session_key"] = parent_session_key
        if agent_role:
            hook_event["agent_role"] = agent_role
        if event == HOOK_EVENT_USER_PROMPT_SUBMIT and context:
            hook_event["prompt"] = context
        if tool_name:
            hook_event["tool_name"] = tool_name
        if tool_input is not None:
            hook_event["tool_input"] = tool_input
        if tool_response is not None:
            hook_event["tool_response"] = tool_response

        for hook in list(self._hooks.values()):
            if not hook.enabled or hook.event != event:
                continue
            if hook_ids is not None and hook.id not in hook_ids:
                continue  # agent-scoped: only fire referenced hooks
            # Matcher filtering: for tool hooks, match tool name; for others, match context
            if hook.matcher:
                if event in (HOOK_EVENT_PRE_TOOL_USE, HOOK_EVENT_POST_TOOL_USE):
                    if not _tool_matches(hook.matcher, tool_name):
                        continue
                elif context and not fnmatch.fnmatch(context.lower(), hook.matcher.lower()):
                    continue
            result = await run_script_hook(hook, context, hook_event)
            results.append(result)
            logger.info(
                "Hook %s (%s): %s in %dms (exit=%d)",
                hook.name,
                event,
                hook.last_status,
                result.duration_ms,
                result.exit_code,
            )
        hooks_snapshot = [h.to_dict() for h in self._hooks.values()]
        await asyncio.to_thread(self._save_snapshot, hooks_snapshot)
        return results

    def _save_snapshot(self, hooks_data: list[dict]) -> None:
        """Thread-safe save using pre-captured hook snapshot."""
        data = {"hooks": hooks_data}
        atomic_write(self._path, json.dumps(data, indent=2))


# -- Global script hook store accessor --
# Set by dashboard server.py / handlers.py when the store is initialized.
# Allows any module (llm_helpers, subagent) to fire script hooks
# without needing a reference to DashboardState.

_global_script_hook_store: ScriptHookStore | None = None


def set_global_hook_store(store: ScriptHookStore) -> None:
    """Register the global script hook store."""
    global _global_script_hook_store
    _global_script_hook_store = store


def get_global_hook_store() -> ScriptHookStore | None:
    """Get the global script hook store, or None if not initialized."""
    return _global_script_hook_store


async def fire_tool_hooks(
    hook_store: ScriptHookStore | None,
    event_title: str,
    event_tool_input: str | None = None,
    *,
    subagent_id: str = "",
    parent_session_key: str = "",
    agent_role: str = "",
) -> None:
    """Fire PreToolUse hooks for an EVENT_TOOL_CALL event.

    PostToolUse is NOT fired here because EVENT_TOOL_CALL is a notification
    that the tool is starting - the tool hasn't completed yet. PostToolUse
    should be fired on EVENT_TOOL_RESULT when available.

    Note: For EVENT_TOOL_CALL, hooks are informational only. The tool is
    already running (auto-approved by ACP agent), so hook results cannot
    block execution. Hook scripts can log, audit, or trigger side effects.

    ``subagent_id``/``parent_session_key``/``agent_role`` (E11-P3) attribute the
    fire to the subagent that ran the tool; omitted for top-level tool calls.
    """
    if hook_store is None:
        return
    tool_name = event_title or ""
    if tool_name.startswith("Running: "):
        tool_name = tool_name[9:]
    tool_input = None
    if event_tool_input:
        try:
            tool_input = json.loads(event_tool_input)
        except Exception:
            pass
    try:
        await hook_store.fire(
            HOOK_EVENT_PRE_TOOL_USE, tool_name=tool_name, tool_input=tool_input,
            subagent_id=subagent_id, parent_session_key=parent_session_key,
            agent_role=agent_role,
        )
    except Exception:
        logger.debug("PreToolUse hook error", exc_info=True)
