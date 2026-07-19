"""Task modes — the per-session posture that gates *which* tools may run.

A task mode is orthogonal to the approval mode: task mode decides whether a tool
is *allowed at all*; approval decides whether an allowed tool *auto-approves*. The
four modes:

  - ``agent``: full execution — no restriction.
  - ``ask``:   read-only Q&A — reads/search/recall run; every mutation is blocked.
  - ``plan``:  produce a plan — read-only inspection runs (so the plan is grounded
               in real state), but no mutation/execution.
  - ``build``: scoped to producing an artifact/widget/skill — read-only tools plus
               artifact-producing tools; other mutations blocked.

This module is the SINGLE source of truth for the gate. It is enforced in the
native runtime (``_guard_and_invoke``, before approval is consulted, so a
Trust/YOLO auto-approve can never bypass a task-mode restriction) AND in the
dashboard's permission handler (belt-and-suspenders for ACP runtimes that gate
via their own protocol path). It has no dashboard/agent dependencies so both
layers import it without a cycle.
"""

from __future__ import annotations

import json
import re

# ── Read-only bash command classification ──
# A conservative allowlist: a command is read-only only if every segment starts
# with a known read-only prefix and any pipe targets are read-only filters, with
# no redirections or command substitutions. Deny-by-default.

_READ_ONLY_BASH_PREFIXES: tuple[str, ...] = (
    "ls",
    "cat",
    "head",
    "tail",
    "find",
    "grep",
    "egrep",
    "fgrep",
    "wc",
    "which",
    "file",
    "stat",
    "du",
    "df",
    "tree",
    "diff",
    "pwd",
    "echo",
    "date",
    "whoami",
    "hostname",
    "uname",
    "readlink",
    "realpath",
    "basename",
    "dirname",
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git tag",
    "git remote",
    "git rev-parse",
    "git describe",
    "git ls-files",
    "git ls-tree",
    "git cat-file",
    "git blame",
    "python --version",
    "python3 --version",
    "node --version",
    "java -version",
    "javac -version",
)

_READ_ONLY_PIPE_RE = re.compile(
    r"^\s*(grep|egrep|fgrep|head|tail|wc|sort|uniq|cut|less|more|cat)\b"
)

# Reject redirections and command substitutions — conservative, may reject
# harmless patterns like 2>/dev/null but false positives are preferable.
_UNSAFE_SHELL_RE = re.compile(r">|`|\$\(|<\(|(?<!&)&(?!&)")


def is_read_only_bash(cmd: str) -> bool:
    """Check if a bash command is read-only. Deny-by-default."""
    if not cmd.strip():
        return False
    if _UNSAFE_SHELL_RE.search(cmd):
        return False
    parts = re.split(r"\s*(?:&&|\|\||;|\n)\s*", cmd.strip())
    for part in parts:
        if not part.strip():
            continue
        pipe_parts = [p.strip() for p in part.split("|") if p.strip()]
        if not pipe_parts:
            return False
        first = pipe_parts[0].strip().lower()
        if not (
            first.endswith("--help")
            or first.endswith("--version")
            or any(first == p or first.startswith(p + " ") for p in _READ_ONLY_BASH_PREFIXES)
        ):
            return False
        for target in pipe_parts[1:]:
            if not _READ_ONLY_PIPE_RE.match(target):
                return False
    return True


def extract_bash_command(tool_input: object) -> str:
    """Extract the command string from an execute_bash tool input.

    ``tool_input`` is ``Any``: ACP agents pass the raw JSON argument *string* (or
    a bare command string), the native loop passes the parsed *dict*. Always
    returns a ``str`` because callers feed the result to ``is_read_only_bash``,
    which requires string input.
    """
    # Native loop: already a parsed dict.
    if isinstance(tool_input, dict):
        cmd = tool_input.get("command", "")
        return cmd if isinstance(cmd, str) else ""
    if not isinstance(tool_input, str):
        return ""
    # ACP: JSON string (or a raw command string).
    try:
        data = json.loads(tool_input)
        if isinstance(data, dict):
            cmd = data.get("command", "")
            return cmd if isinstance(cmd, str) else ""
    except (json.JSONDecodeError, TypeError):
        pass
    return tool_input


# ── Task-mode tool gate ──

VALID_TASK_MODES: tuple[str, ...] = ("agent", "ask", "plan", "build")

# Tool-kind hints (ACP-style ``tool_kind``) used to classify a tool when its name
# isn't decisive. Mutating kinds are always blocked in restricted modes; read-only
# kinds always pass.
_MUTATING_TOOL_KINDS = {"edit", "delete", "move"}
_READONLY_TOOL_KINDS = {"read", "fetch", "search", "think"}

# Name fragments that signal a mutating/effectful tool when the kind is ambiguous.
# ``generate`` covers media producers (image_generate, future audio/video_generate):
# they create a persisted artifact + may spend a paid API call, so they are NOT
# read-only and must be blocked in ask/plan (and allowed in build via the hints below).
_MUTATING_NAME_HINTS = (
    "write", "edit", "create", "save", "update", "delete", "remove", "move",
    "rename", "append", "set_", "put_", "install", "deploy", "run", "exec",
    "spawn", "subagent", "schedule", "notify", "post_", "send", "commit", "push",
    "generate",
)

# Name fragments that mark a Build-mode producer (allowed in build even though
# they're "mutating": producing the deliverable IS the point of build mode).
# ``image`` admits image_generate — producing an image artifact is a build output.
_BUILD_NAME_HINTS = ("artifact", "widget", "skill", "prompt", "document", "infographic", "image")

# Destructive verbs that the Build producer-hint must NOT wave through: Build is
# scoped to *producing* a deliverable, so `delete_artifact`/`remove_widget` stay
# blocked even though they carry a build-hint token. (Producing = create/save/update.)
# ``forget`` covers memory_forget (a durable delete); ``remove_all`` is caught by remove.
_DESTRUCTIVE_NAME_HINTS = ("delete", "remove", "destroy", "drop_", "purge", "forget")

# Read verbs — a tool whose name is clearly a query/inspection. Used by
# infer_risk_from_name to short-circuit to 'safe' BEFORE the broad mutating hints
# (so `schedule_list`/`task_get`/`*_status` aren't mislabeled by a hint like
# "schedule"). Not used by the task-mode gate (which keys off tool_kind + input).
_READ_VERB_HINTS = ("list", "get", "search", "read", "status", "info", "find", "inspect", "show", "view")


def _is_read_only_tool(title: str, tool_kind: str, tool_input: object) -> bool:
    """Classify a tool call as read-only (no side effects) or not."""
    name = (title or "").lower()
    kind = (tool_kind or "").lower()
    cmd = extract_bash_command(tool_input) if tool_input else ""
    # A bash/command tool is read-only iff is_read_only_bash says so.
    if cmd or kind in ("command", "execute"):
        return bool(cmd) and is_read_only_bash(cmd)
    # Non-bash: read-only by ACP kind, or by name (no mutating verb hint).
    if kind in _MUTATING_TOOL_KINDS:
        return False
    if kind in _READONLY_TOOL_KINDS:
        return True
    return not any(h in name for h in _MUTATING_NAME_HINTS)


# ── Effective risk resolver (tool risk taxonomy) ──
#
# The single source of truth for "how risky is THIS tool call". The tool's
# DECLARED risk (ToolDefinition.risk_level) is per-tool and static; the EFFECTIVE
# risk is per-invocation — a `bash` tool is declared DESTRUCTIVE, but `cat file`
# is effectively SAFE. Consumed by the approval gate (trust-reads auto-approves
# EFFECTIVE-SAFE) and surfaced to the user as an indicator (card chip, tools UI).
#
# Resolution order (deny-by-default toward higher risk):
#   1. A read-only invocation (per _is_read_only_tool) is SAFE — this subsumes
#      and generalizes the old read-only-bash trust-reads path to every read.
#   2. Otherwise honor the declared risk when the tool carries one.
#   3. A non-read-only call with NO declared risk (external MCP / OpenAI-adapter
#      tools) is CAUTION — never SAFE. So trust-reads can't silently auto-approve
#      an unclassified external tool; the user still sees a card for it.

_RISK_ORDER = {"safe": 0, "caution": 1, "destructive": 2}


def resolve_effective_risk(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
) -> str:
    """Resolve the effective risk of one tool call → 'safe'|'caution'|'destructive'.

    ``declared`` is the tool's ``ToolDefinition.risk_level`` (a ``RiskLevel``, its
    string value, or ``None``/'' when the provider declared none — external tools).
    Returns a bare string (the ``RiskLevel`` value) so callers without the enum
    import (chat_runner event path, JSON APIs) use it directly.
    """
    declared_val = getattr(declared, "value", declared)  # RiskLevel → str; str → str
    declared_str = str(declared_val).lower() if declared_val else ""

    # 1. A read-only bash invocation is SAFE regardless of the (DESTRUCTIVE) bash
    #    declaration — this is the per-invocation downgrade that generalizes the
    #    old read-only-bash trust-reads path.
    cmd = extract_bash_command(tool_input) if tool_input else ""
    kind = (tool_kind or "").lower()
    if cmd or kind in ("command", "execute"):
        return "safe" if (cmd and is_read_only_bash(cmd)) else (declared_str or "destructive")

    # 2. Honor a declared risk (native tools set this per tool).
    if declared_str in _RISK_ORDER:
        return declared_str

    # 3. No declared risk (external MCP / OpenAI-adapter tools, or an event that
    #    didn't carry risk_level). A POSITIVE read-only ACP tool_kind is SAFE. Else
    #    consult name inference so this resolver AGREES with the tool's own declared
    #    risk (e.g. `memory_forget` → destructive, not a flat caution). But a name that
    #    inference can't positively classify as a read (→ 'safe') must NOT become safe
    #    here — an unknown external tool must not silently satisfy trust-reads — so it
    #    floors at CAUTION. A read-verb name (list/get/search) already returned safe
    #    at the tool_kind check only when the KIND says so; by name alone we stay
    #    conservative: caution unless inference flags a higher risk.
    if kind in _READONLY_TOOL_KINDS:
        return "safe"
    inferred = infer_risk_from_name(title)
    return inferred if inferred in ("caution", "destructive") else "caution"


def infer_risk_from_name(name: str) -> str:
    """Best-effort DECLARED risk for a tool that ships no explicit risk_level.

    For dict-defined MCP tools (personalclaw-core/schedule/artifacts) and external
    MCP/OpenAI-adapter tools, the ToolDefinition would otherwise default SAFE —
    understating a `*_delete`/`schedule_add`/`notify`. Classify by name verb:
    destructive verb → 'destructive'; other mutating verb → 'caution'; else 'safe'.
    Conservative: only a positive mutating signal raises risk, so read tools
    (search/get/list/read) stay 'safe'. Used at ToolDefinition construction where
    no risk is declared — the resolver still downgrades read-only invocations.
    """
    n = (name or "").lower()
    # Strip an `mcp/<server>/` prefix so the verb match sees the bare tool name.
    if n.startswith("mcp/"):
        n = n.rsplit("/", 1)[-1]
    # Destructive verbs win outright (delete/remove/forget/purge/drop).
    if any(h in n for h in _DESTRUCTIVE_NAME_HINTS):
        return "destructive"
    # Read-verb short-circuit BEFORE mutating hints: a broad hint like "schedule"
    # matches schedule_add (mutating) AND schedule_list (read). A tool whose verb
    # is clearly a read (list/get/search/read/status/info/find/inspect) is safe,
    # so schedule_list / task_list / *_status don't get mislabeled caution.
    if any(v in n for v in _READ_VERB_HINTS):
        return "safe"
    if any(h in n for h in _MUTATING_NAME_HINTS):
        return "caution"
    return "safe"


def task_mode_denies(task_mode: str, title: str, tool_kind: str, tool_input: object) -> str:
    """Return a deny-reason for the task mode, or '' to allow the tool.

    Orthogonal to approval — this decides *which* tools may run:
      - ``agent``: everything allowed.
      - ``ask``:   read-only only (bash must pass ``is_read_only_bash``).
      - ``plan``:  read-only inspection allowed (so the plan is grounded), but no
                   mutation/execution — same read-only test as ask, different reason.
      - ``build``: read-only + artifact/widget/skill producers; other mutations denied.
    Deny-by-default within ask/plan/build: an unrecognized mutating tool is denied.
    """
    if task_mode == "agent" or task_mode not in VALID_TASK_MODES:
        return ""

    read_only = _is_read_only_tool(title, tool_kind, tool_input)
    if read_only:
        return ""  # reads run in ask/plan/build alike

    if task_mode == "build":
        _name = (title or "").lower()
        # Build permits artifact/widget/skill PRODUCERS — but not destructive ops on
        # them (delete_artifact stays blocked; producing is the point of build mode).
        if any(h in _name for h in _BUILD_NAME_HINTS) and not any(
            d in _name for d in _DESTRUCTIVE_NAME_HINTS
        ):
            return ""

    if task_mode == "ask":
        return "Ask mode — only read-only tools run (switch to Agent to make changes)"
    if task_mode == "plan":
        return "Plan mode — inspection only, nothing is executed (switch to Agent to run it)"
    return "Build mode — only read-only + artifact-producing tools run (switch to Agent for the rest)"
