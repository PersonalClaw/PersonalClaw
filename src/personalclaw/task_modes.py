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


def is_shell_invocation(title: str, tool_kind: str) -> bool:
    """Is this tool call a shell invocation — i.e. does a ``command`` key mean anything?

    The ONE answer to that question. It exists because ``command`` is an ordinary
    argument name: ``workflow_delete_def``, ``memory_forget`` and any MCP tool may carry
    one, and reading it as "the shell command this call will run" is how a destructive
    tool got classified by a string that was never going to be executed (#443).

    Three positive signals, because no single one covers every caller:

    1. the ACP ``tool_kind`` — an agent that declares ``execute``/``command``;
    2. the tool's own name — the native loop declares no kind for its ``bash`` tool;
    3. the ``Running: `` display title — an ACP permission frame can carry the command
       inline with no preceding ``tool_call`` frame and no kind at all, so the title is
       the only signal there is.

    Anything else is not a shell call, and its arguments are data.
    """
    name = (title or "").lower()
    return (
        (tool_kind or "").lower() in _COMMAND_TOOL_KINDS
        or name in SHELL_TOOL_NAMES
        or name.startswith(SHELL_TITLE_PREFIXES)
    )


def shell_command(title: str, tool_kind: str, tool_input: object) -> str:
    """The shell command THIS call will run, or ``""`` if it is not a shell call.

    The scoped extractor every decision must use. :func:`extract_bash_command` is the
    raw parser — it answers "is there a ``command`` key here", which is a different
    question and not one any gate may act on, because the answer is yes for tools that
    run no shell at all.
    """
    if not tool_input or not is_shell_invocation(title, tool_kind):
        return ""
    return extract_bash_command(tool_input)


def extract_bash_command(tool_input: object) -> str:
    """Extract the command string from an execute_bash tool input.

    ``tool_input`` is ``Any``: ACP agents pass the raw JSON argument *string* (or
    a bare command string), the native loop passes the parsed *dict*. Always
    returns a ``str`` because callers feed the result to ``is_read_only_bash``,
    which requires string input.

    **Not scoped to shell tools, by design — so do not gate on it.** It reads a
    ``command`` key out of whatever it is handed, which is correct for a parser and
    wrong for a decision: ``command`` is an ordinary argument name. Any code deciding
    what a call is allowed to do wants :func:`shell_command`, which asks
    :func:`is_shell_invocation` first. The remaining direct callers are display and
    DENY-only paths, where an over-broad read cannot widen a permission.
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
# Kinds that mean "this call runs a shell command". Neither mutating nor read-only by
# itself: the command TEXT decides, so a call of this kind with no readable command is
# UNCLASSIFIED, not safe and not destructive (see :func:`classify_invocation`).
_COMMAND_TOOL_KINDS = {"command", "execute"}

#: Tool NAMES that mean "this call runs a shell command", for the callers that carry no
#: ACP kind. The native loop passes ``tool_kind=""`` for its own ``bash`` tool, so
#: :data:`_COMMAND_TOOL_KINDS` alone cannot recognise the product's primary shell tool —
#: which is why :func:`is_shell_invocation` consults both.
#:
#: EXACT names, and deliberately not the substring hints
#: :data:`~personalclaw.approval_brief.SHELL_HINTS` uses. Those describe a tool to a
#: human ("this one can run things"), where over-matching is harmless. This set decides
#: whether a ``command`` string is *authoritative evidence about the call*, where
#: over-matching is the bug: every extra name is another tool whose declared risk a
#: decoy ``command`` key could downgrade. Under-matching only costs an extra prompt.
#:
#: :mod:`personalclaw.guardrails.loop_breaker` imports this rather than keeping the
#: second copy it used to hold — one question, one answer.
SHELL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "bash",
        "shell",
        "execute_bash",
        "run-script",
        "run_script",
        "terminal",
    }
)

#: Title prefixes that mean "the rest of this title IS a shell command". An ACP agent
#: sends a humanized display title rather than a tool name, and for a shell call the hook
#: chain normalizes it to ``Running: <command>`` — which ``hooks.on_tool_call`` already
#: treats as ``execute_bash`` when it screens for sensitive paths, and which
#: ``acp/permission_authority.command_probe`` reconstructs for the deny check. So this is
#: not a new convention, it is the existing one, consulted by the gate that needs it.
#:
#: Load-bearing: an ACP permission frame can carry the command INLINE with no preceding
#: ``tool_call`` frame and no declared kind, so the title is the ONLY signal that the call
#: runs a shell. Dropping this reds ``test_acp_effective_risk_correlation`` with "a
#: read-only ls RUNS in ask mode", which is how it was found.
#:
#: ``Reading `` is deliberately NOT here: that prefix names a FILE, not a command, and
#: treating it as a shell call would hand ``is_read_only_bash`` a path to parse.
SHELL_TITLE_PREFIXES: tuple[str, ...] = ("running: ",)

# Destructive verbs — a tool whose name carries one only ever DESTROYS something.
#
# Declared BEFORE `_MUTATING_NAME_HINTS` because that set is built to contain this one.
# They were two independent literals until #2118, and they had drifted: `destroy`, `drop_`,
# `purge` and `forget` were destructive-but-not-mutating, so `memory_forget` inferred
# `destructive` for the approval card while classifying READ_ONLY for the task-mode gate —
# and therefore ran in ask AND plan mode with nothing to deny it. A destructive verb that is
# not also mutating is a contradiction, so the containment is now structural rather than a
# thing two lists have to agree about.
#
# Used for two distinct jobs, which is why it exists separately at all: the Build
# producer-hint must not wave these through (Build is scoped to *producing* a deliverable,
# so `delete_artifact`/`remove_widget` stay blocked despite carrying a build-hint token),
# and `infer_risk_from_name` grades them above ordinary mutation.
# ``forget`` covers memory_forget (a durable delete); ``remove_all`` is caught by remove.
_DESTRUCTIVE_NAME_HINTS = ("delete", "remove", "destroy", "drop_", "purge", "forget")

# Name fragments that signal a mutating tool WITHOUT being destructive — they change or
# create state rather than removing it.
# ``generate`` covers media producers (image_generate, future audio/video_generate):
# they create a persisted artifact + may spend a paid API call, so they are NOT
# read-only and must be blocked in ask/plan (and allowed in build via the hints below).
_NON_DESTRUCTIVE_MUTATING_NAME_HINTS = (
    "write",
    "edit",
    "create",
    "save",
    "update",
    "move",
    "rename",
    "append",
    "set_",
    "put_",
    "install",
    "deploy",
    "run",
    "exec",
    "spawn",
    "subagent",
    "schedule",
    "notify",
    "post_",
    "send",
    "commit",
    "push",
    "generate",
)

# Every fragment that makes an undeclared tool mutating. The destructive verbs are UNIONED
# in rather than re-listed, so the two sets cannot disagree again the way #2118 found them
# disagreeing — adding a verb to `_DESTRUCTIVE_NAME_HINTS` now widens the task-mode gate in
# the same edit, which is the property that was missing.
_MUTATING_NAME_HINTS = _NON_DESTRUCTIVE_MUTATING_NAME_HINTS + _DESTRUCTIVE_NAME_HINTS

# Name fragments that mark a Build-mode producer (allowed in build even though
# they're "mutating": producing the deliverable IS the point of build mode).
# ``image`` admits image_generate — producing an image artifact is a build output.
_BUILD_NAME_HINTS = ("artifact", "widget", "skill", "prompt", "document", "infographic", "image")

# (`_DESTRUCTIVE_NAME_HINTS` is declared above `_MUTATING_NAME_HINTS`, which is built to
# contain it — see the note there. It is what the Build producer-hint must NOT wave through.)

# Read verbs — a tool whose name is clearly a query/inspection. Used by
# infer_risk_from_name to short-circuit to 'safe' BEFORE the broad mutating hints
# (so `schedule_list`/`task_get`/`*_status` aren't mislabeled by a hint like
# "schedule"). Not used by the task-mode gate (which keys off tool_kind + input).
_READ_VERB_HINTS = (
    "list",
    "get",
    "search",
    "read",
    "status",
    "info",
    "find",
    "inspect",
    "show",
    "view",
)


# ── The one invocation vocabulary ──
#
# Three answers, not two. Everything that asks "what kind of thing is this call"
# — the task-mode gate, the effective-risk resolver, the approval card's chip and
# the SEL row's ``risk`` — derives from :func:`classify_invocation`, so a shell
# command is never classified one way for the gate and another for the label.
#
# UNCLASSIFIED is the third answer and it is the point: a shell/command tool whose
# command TEXT never reached the host cannot be classified at all. ACP agents open a
# tool call with ``rawInput: {}`` + ``status: pending`` and fill the input in a later
# ``tool_call_update`` (see ``acp.translate.extract_tool_update_events``), so the host
# routinely holds a ``kind: "execute"`` frame with no command. Before this existed, that
# state resolved to the literal ``"destructive"`` — a verdict about the command, minted
# from the command's ABSENCE, which is how a read-only ``pwd; ls`` was audited as
# destructive (`G10`/`O10`). Absence now has its own value, and each consumer decides
# what to do with it explicitly: the gate DENIES it (fails closed — an unreadable
# command must not run under a read-only posture) while the label floors it at CAUTION
# (fails honest — never ``safe``, so it still raises a card, and never ``destructive``,
# which would assert something nobody measured).
READ_ONLY = "read_only"
MUTATING = "mutating"
UNCLASSIFIED = "unclassified"


def classify_invocation(title: str, tool_kind: str, tool_input: object) -> str:
    """Classify ONE tool call → ``READ_ONLY`` | ``MUTATING`` | ``UNCLASSIFIED``.

    The single source of truth for the read-only/mutating question. Resolution order,
    most-evidence-first: for a SHELL call, the command text decides
    (:func:`is_read_only_bash`), and a shell call whose text the host never received is
    ``UNCLASSIFIED``; for anything else the declared ACP kind decides, then the tool's
    name. Deny-by-default at every step — only a positive read signal yields
    ``READ_ONLY``.

    **A ``command`` argument only speaks for a shell call.** It used to speak for every
    call: ``extract_bash_command`` reads a ``command`` key out of any tool's arguments,
    step 1 ran before the kind and the name were consulted, and ``command`` is an
    ordinary argument name. So ``workflow_delete_def`` with ``command: "ls"`` classified
    ``READ_ONLY`` — auto-approved under ``trust_reads`` and *allowed to run* in
    ask/plan/build mode, both from a string that was never going to be executed (#443).
    The scoping lives in :func:`shell_command`; the extra clause at the end is what
    stops the fallthrough quietly re-granting what step 1 no longer does.
    """
    name = (title or "").lower()
    kind = (tool_kind or "").lower()
    if is_shell_invocation(title, tool_kind):
        cmd = shell_command(title, tool_kind, tool_input)
        # 1. A readable shell command: the text decides, not the kind or the name.
        if cmd:
            return READ_ONLY if is_read_only_bash(cmd) else MUTATING
        # 2. A shell call whose command the host cannot see. Not read-only (nothing
        #    positively says so) and not knowably mutating either. Reached by NAME as
        #    well as by kind now: `bash` with an unreadable input used to fall all the
        #    way to step 4 and come back READ_ONLY, because "bash" carries no mutating
        #    name hint — the product's own shell tool, classified as a read.
        return UNCLASSIFIED
    # 3. Non-shell: the declared ACP kind.
    if kind in _MUTATING_TOOL_KINDS:
        verdict = MUTATING
    elif kind in _READONLY_TOOL_KINDS:
        verdict = READ_ONLY
    else:
        # 4. Nothing declared: the tool's name.
        verdict = MUTATING if any(h in name for h in _MUTATING_NAME_HINTS) else READ_ONLY
    # 5. A non-shell tool carrying a `command` string is not a call we understand. The
    #    kind/name path may well answer READ_ONLY (`memory_forget` carries no mutating
    #    hint), which would hand back exactly the verdict step 1 used to give — the same
    #    bypass through a different door. A decoy argument can no longer produce a read.
    if verdict == READ_ONLY and extract_bash_command(tool_input):
        return MUTATING
    return verdict


def _is_read_only_tool(title: str, tool_kind: str, tool_input: object) -> bool:
    """Classify a tool call as read-only (no side effects) or not.

    Thin projection of :func:`classify_invocation` onto a bool: ONLY a positive
    ``READ_ONLY`` passes, so ``UNCLASSIFIED`` reads as not-read-only here and the
    task-mode gate keeps failing closed on a command it cannot see.
    """
    return classify_invocation(title, tool_kind, tool_input) == READ_ONLY


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
    # SCOPED (`shell_command`, not `extract_bash_command`): this branch is the
    # per-invocation downgrade, so it must fire only for a call that actually runs a
    # shell. Reading a `command` key off any tool is what let a decoy argument reach the
    # downgrade at all (#443), and `classify_invocation` agreeing is not enough on its
    # own — this `if` is a second door to the same decision.
    cmd = shell_command(title, tool_kind, tool_input)
    kind = (tool_kind or "").lower()
    verdict = classify_invocation(title, tool_kind, tool_input)
    if cmd:
        return "safe" if verdict == READ_ONLY else (declared_str or "destructive")

    # 1b. A shell/command call whose command text the host never received
    #     (UNCLASSIFIED). There is nothing here to classify, so a verdict about the
    #     command would be fabricated. Honor a real declaration if the tool carries
    #     one — that is a fact about the TOOL, not a guess about this call — and
    #     otherwise floor at CAUTION: never `safe` (trust-reads must not auto-approve
    #     a command nobody read, so the user still sees a card) and never the old
    #     literal `destructive` (which audited a read-only `pwd; ls` as destructive —
    #     `G10`/`O10` — because the absence of the command, not the command, was what
    #     got measured).
    if verdict == UNCLASSIFIED:
        return declared_str if declared_str in _RISK_ORDER else "caution"

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
    understating a `*_delete`/`automation_create`/`notify`. Classify by name verb:
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
    # matches automation_create (mutating) AND automation_list (read). A tool whose verb
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
    return (
        "Build mode — only read-only + artifact-producing tools run (switch to Agent for the rest)"
    )
