"""Task modes — the per-session posture that gates *which* tools may run.

A task mode is orthogonal to the approval mode: task mode decides whether a tool
is *allowed at all*; approval decides whether an allowed tool *auto-approves*. The
four modes:

  - ``agent``: full execution — no restriction.
  - ``ask``:   read-only Q&A — reads/search/recall run; every mutation is blocked.
  - ``plan``:  produce a plan — read-only inspection runs (so the plan is grounded
               in real state), but no mutation/execution.
  - ``build``: scoped to producing an artifact/widget/skill — read-only tools plus
               the tools that declare they build it; other mutations blocked.

This module is the SINGLE source of truth for the gate. It is enforced in the
native runtime (``_guard_and_invoke``, before approval is consulted, so a
Trust/YOLO auto-approve can never bypass a task-mode restriction) AND in the
dashboard's permission handler (belt-and-suspenders for ACP runtimes that gate
via their own protocol path). It has no dashboard/agent dependencies so both
layers import it without a cycle.

"Is this call a read?" has two inputs and no others: the tool's DECLARATION
(``ToolDefinition.risk_level``; ``safe`` is the read-only declaration and a tool
that declares nothing is not one) and, for a shell call, the command it will run,
read by :mod:`personalclaw.command_effects` (a per-program allowlist of read-only forms).
No decision here reads a tool's name for what it does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from personalclaw.command_effects import CommandEffects, command_effects

# ── Read-only shell command classification ──


def is_read_only_bash(cmd: str) -> bool:
    """Whether every program in *cmd* runs in a form known to only read. Deny by default.

    The reading is :func:`personalclaw.command_effects.command_effects`: a per-program allowlist of
    read-only forms, where an unknown program, option, subcommand or piece of shell syntax means
    "not read-only".
    """
    return command_effects(cmd).reads_only


def declared_level(declared: object) -> str:
    """A tool's declared :class:`~personalclaw.tool_providers.base.RiskLevel` as its bare value,
    or ``""`` when the call carries no declaration (an ACP CLI's own tool).

    ``declared`` is a ``RiskLevel``, its string value, or ``None``/``""``; anything else is no
    declaration, never a read.
    """
    value = getattr(declared, "value", declared)
    text = str(value).strip().lower() if value else ""
    return text if text in _RISK_ORDER else ""


def is_shell_invocation(title: str, tool_kind: str, declared: object = "") -> bool:
    """Is this tool call a shell invocation — i.e. does a ``command`` key mean anything?

    The ONE answer to that question. It exists because ``command`` is an ordinary
    argument name: ``workflow_delete_def``, ``memory_forget`` and any MCP tool may carry
    one, and reading it as "the shell command this call will run" is how a destructive
    tool got classified by a string that was never going to be executed (#443).

    A call that carries its tool's DECLARATION is a native tool with a definition, and the only
    one of those that runs its ``command`` in a shell is the platform's own :data:`PLATFORM_SHELL`
    — a name the tool registry reserves for the platform provider, so no app or server can
    offer a tool under it. Any other declared tool is not a shell call, whatever it is named: an
    app's ``run_script`` that declares CAUTION must not turn into a read because its ``command``
    argument says ``ls``.

    A call with no declaration is an ACP CLI's own tool, and three positive signals cover the
    ways those arrive:

    1. the ACP ``tool_kind`` — an agent that declares ``execute``/``command``;
    2. the tool's name — :data:`SHELL_TOOL_NAMES`, the shell tools ACP CLIs send by name;
    3. the ``Running: `` display title — an ACP permission frame can carry the command
       inline with no preceding ``tool_call`` frame and no kind at all, so the title is
       the only signal there is.

    Anything else is not a shell call, and its arguments are data.
    """
    name = (title or "").lower()
    if declared_level(declared):
        return name == PLATFORM_SHELL
    return (
        (tool_kind or "").lower() in _COMMAND_TOOL_KINDS
        or name in SHELL_TOOL_NAMES
        or name.startswith(SHELL_TITLE_PREFIXES)
    )


def shell_command(title: str, tool_kind: str, tool_input: object, declared: object = "") -> str:
    """The shell command THIS call will run, or ``""`` if it is not a shell call.

    The scoped extractor every decision must use. :func:`extract_bash_command` is the
    raw parser — it answers "is there a ``command`` key here", which is a different
    question and not one any gate may act on, because the answer is yes for tools that
    run no shell at all.
    """
    if not tool_input or not is_shell_invocation(title, tool_kind, declared):
        return ""
    return extract_bash_command(tool_input)


def reads_only(title: str, tool_kind: str, tool_input: object, declared: object = "") -> bool:
    """Whether this ONE call is established as a read: what an approval surface publishes as
    ``is_read_only``.

    ``True`` when its tool declares it only reads, or when it runs a shell command screened
    read-only; ``False`` for every other call, which PersonalClaw treats as the change it may
    be: a tool that declares a change or nothing, a mutating command, a shell call whose command
    never arrived. Never a third state: a call is a read only on positive evidence, and "not
    established" is what ``False`` says.

    The answer is :func:`resolve_effective_risk` reaching ``safe``, the one owner of "is this
    call a read" (:func:`classify_invocation` composes the declaration with the screened
    command), so the two surfaces that publish it (the chat's approval card and the gateway's
    pending approvals) and the risk each shows beside it cannot disagree.
    """
    return resolve_effective_risk(declared, title, tool_kind, tool_input) == "safe"


def tool_input_to_str(value: object) -> str:
    """Coerce an event's ``tool_input`` to a display string.

    ``AgentEvent.tool_input`` is typed ``Any``: ACP agents pass the raw JSON argument
    *string*, the native loop passes the parsed *dict*, and other providers may pass
    anything. Display and REDACTION code needs a ``str``: dicts/lists are JSON-encoded,
    ``None`` becomes ``""``, everything else is ``str()``.

    🔴 LIVES HERE, BESIDE :func:`extract_bash_command`, BECAUSE THE APPROVAL STORE NEEDS IT.
    It used to live in ``dashboard/chat_utils.py``, which imports ``dashboard/state.py`` at
    module level — so ``state.py`` could not reach it, and ``DashboardState.request_approval``
    declared ``tool_input: str`` while the gateway handed it ``event.tool_input`` (``Any``).
    A native-loop dict therefore reached ``security.scan_exfiltration_urls``, whose
    ``_URL_RE.finditer(text)`` raised ``TypeError: expected string or bytes-like object, got
    'dict'`` from inside the approval path and killed the whole subagent. This module already
    owns the "``tool_input`` is ``Any``" problem for the screening half and imports nothing but
    ``json``/``re``, so it is the neutral home both callers can share.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


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

# Kinds that mean "this call runs a shell command". Neither mutating nor read-only by
# itself: the command TEXT decides, so a call of this kind with no readable command is
# UNCLASSIFIED, not safe and not destructive (see :func:`classify_invocation`).
_COMMAND_TOOL_KINDS = {"command", "execute"}

#: The platform's shell tool — the one DECLARED tool whose ``command`` argument is a shell
#: command, so the only one whose per-call risk the command text decides
#: (:func:`is_shell_invocation`). The tool registry reserves the name for the platform provider
#: (``PLATFORM_TOOL_NAMES``), which is what makes a name a fact here rather than a guess.
PLATFORM_SHELL = "bash"

#: Tool NAMES that mean "this call runs a shell command", for an ACP CLI's own tools, which
#: carry no declaration. The native loop's shell is :data:`PLATFORM_SHELL`, recognised by its
#: declaration instead.
#:
#: EXACT names, and deliberately not the substring hints
#: :data:`~personalclaw.approval_brief.SHELL_HINTS` uses. Those describe a tool to a
#: human ("this one can run things"), where over-matching is harmless. This set decides
#: whether a ``command`` string is *authoritative evidence about the call*, where
#: over-matching is the bug. Under-matching only costs an extra prompt.
#:
#: :mod:`personalclaw.guardrails.loop_breaker` imports this rather than keeping the
#: second copy it used to hold — one question, one answer.
SHELL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        PLATFORM_SHELL,
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

#: The ACP tool kinds a CLI REPORTS for a call that reads (``tool_call.kind``). A report, not a
#: declaration: nothing admits a call because of it — a CLI-labelled "read" must not turn a
#: deny-by-default gate into an allow. One question reads it, after the fact: when an
#: ACP CLI ran a tool without asking the host (``dashboard.ungated_calls.report_ungated_call``),
#: was that a mutation under a read-only posture? The call has already run, so the kind decides
#: only whether the turn stops, never whether anything runs.
REPORTED_READ_KINDS: frozenset[str] = frozenset({"read", "fetch", "search", "think"})


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
# command must not run under a read-only posture) while the risk names it for what it is,
# its declaration, else CAUTION (fails honest — never ``safe``, so it still raises a card, and
# never ``destructive``, which would assert something nobody measured). A command that did
# arrive but that the screen cannot vouch for is :data:`UNCHECKED`, for the same reason.
READ_ONLY = "read_only"
MUTATING = "mutating"
UNCLASSIFIED = "unclassified"


def classify_invocation(declared: object, title: str, tool_kind: str, tool_input: object) -> str:
    """Classify ONE tool call → ``READ_ONLY`` | ``MUTATING`` | ``UNCLASSIFIED``.

    The single source of truth for the read-only/mutating question, and it has two inputs
    only: what the tool DECLARES (``declared``, its ``RiskLevel``) and, for a shell call,
    the command it will run.

    1. A shell call (:func:`is_shell_invocation`): the command text decides
       (:func:`is_read_only_bash`, an allowlist), and a shell call whose text the host never
       received is ``UNCLASSIFIED``.
    2. Anything else is ``READ_ONLY`` only when its tool declares ``safe``. A tool that
       declares nothing is ``MUTATING`` — which is every ACP CLI's own tool and every
       external MCP tool whose server the owner does not trust.

    Neither the tool's NAME nor an ACP ``tool_kind`` is evidence of a read. The name was: a
    tool whose name carried no write-shaped fragment read as read-only, which passed 75 of
    the chat's 115 tools, ``computer_click``, ``workflow_start`` and ``memory_remember``
    among them, through Ask mode, Trust reads and ``personalclaw run`` alike.
    """
    if is_shell_invocation(title, tool_kind, declared):
        cmd = shell_command(title, tool_kind, tool_input, declared)
        if cmd:
            return READ_ONLY if is_read_only_bash(cmd) else MUTATING
        return UNCLASSIFIED
    return READ_ONLY if declared_level(declared) == "safe" else MUTATING


# ── Effective risk resolver (tool risk taxonomy) ──
#
# The single source of truth for "how risky is THIS tool call". The tool's DECLARED risk
# (ToolDefinition.risk_level) is per-tool and static; the EFFECTIVE risk is per-invocation
# — a `bash` tool is declared DESTRUCTIVE, but `cat file` is effectively SAFE. Consumed by
# the approval gates (Trust reads and `--approval reads` auto-approve EFFECTIVE-SAFE) and
# surfaced to the user as an indicator (card chip, tools UI, the SEL row's risk).

_RISK_ORDER = {"safe": 0, "caution": 1, "destructive": 2}

#: The effective risk of a shell command the screen could not vouch for: part of it is a
#: program, an option or a piece of syntax the screen does not read. It is no declaration (no
#: tool declares it) and it is shown as what it is: "Not checked". Every gate that asks more of a
#: destructive call asks the same of it — it can do anything the shell can.
UNCHECKED = "unchecked"

#: The effective risks a gate treats as "may be destructive": a named confirmation on the Tools
#: page, the card's standing grants withheld until the user unlocks them, and a channel prompt's
#: standing answer withheld (a prompt has no unlock, so such a call is answered once).
MAY_DESTROY: frozenset[str] = frozenset({"destructive", UNCHECKED})


@dataclass(frozen=True)
class CallReading:
    """One reading of one tool call: its effective risk and, for a shell call, what its command
    establishes it does. The risk an approval shows and the facets beside it both come from here,
    so they cannot disagree."""

    risk: str
    #: The shell command's effects, or ``None`` when the call runs no command the host received.
    effects: CommandEffects | None


def read_call(declared: object, title: str, tool_kind: str, tool_input: object) -> CallReading:
    """Read one tool call → its effective risk and its command's effects.

    ``declared`` is the tool's ``ToolDefinition.risk_level`` (a ``RiskLevel``, its string value,
    or ``None``/'' when the call carries no declaration — an ACP CLI's own tool).

    A shell call (:func:`is_shell_invocation`) is decided by its command, whatever the shell tool
    declares: the declaration says what the tool can reach (the platform's ``bash`` declares
    DESTRUCTIVE, because a shell can do anything), and the command says what THIS call does.

    * ``safe`` — every program runs in a read-only form;
    * ``destructive`` — it establishes a delete (``rm``, ``find -delete``, ``git branch -D``);
    * :data:`UNCHECKED` — part of it is something the screen could not vouch for;
    * ``caution`` — it establishes a write or a network call, and nothing it could not vouch for.

    A shell call whose command never reached the host, and any other call, is its declaration,
    else CAUTION: never ``safe`` (nobody read a command) and never ``destructive`` minted from a
    command's absence. So ``safe`` is reached only by a read-only command or a tool that declares
    it only reads; an undeclared tool always raises a card under Trust reads.
    """
    cmd = shell_command(title, tool_kind, tool_input, declared)
    if not cmd:
        return CallReading(declared_level(declared) or "caution", None)
    effects = command_effects(cmd)
    if effects.reads_only:
        risk = "safe"
    elif effects.deletes:
        risk = "destructive"
    elif effects.unread:
        risk = UNCHECKED
    else:
        risk = "caution"
    return CallReading(risk, effects)


def resolve_effective_risk(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
) -> str:
    """The effective risk of one tool call → 'safe'|'caution'|'destructive'|'unchecked'
    (:func:`read_call`).

    Returns a bare string (the ``RiskLevel`` value) so callers without the enum import
    (chat_runner event path, JSON APIs) use it directly.
    """
    return read_call(declared, title, tool_kind, tool_input).risk


def read_grant_admits(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    proposes: bool = False,
) -> bool:
    """Whether a ``read`` tool grant — a research-class leaf, subagent or room member — admits
    this call: a read (:func:`classify_invocation`), or a tool that declares its only effect is
    a proposal the owner reviews (``proposes``). Filing for review is what a research run is
    for; everything else it might do is a change it was not granted.

    The one answer every seam that enforces the tier asks (``mcp_shared.leaf_tool_denial``,
    ``subagent._run_inner``, ``rooms.posture``); ``guardrails.policy.tool_grant_denial`` owns
    what the tiers mean.
    """
    return proposes or classify_invocation(declared, title, tool_kind, tool_input) == READ_ONLY


def task_mode_denies(
    task_mode: str,
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    builds: bool = False,
) -> str:
    """Return a deny-reason for the task mode, or '' to allow the tool.

    Orthogonal to approval — this decides *which* tools may run:
      - ``agent``: everything allowed.
      - ``ask``:   read-only only (:func:`classify_invocation`).
      - ``plan``:  read-only inspection allowed (so the plan is grounded), but no
                   mutation/execution — same read-only test as ask, different reason.
      - ``build``: read-only + the tools that declare they build the deliverable
                   (``builds``), never a destructive one.
    Deny-by-default within ask/plan/build: a tool that declares nothing is denied.
    """
    if task_mode == "agent" or task_mode not in VALID_TASK_MODES:
        return ""

    if classify_invocation(declared, title, tool_kind, tool_input) == READ_ONLY:
        return ""  # reads run in ask/plan/build alike

    if task_mode == "build" and builds and declared_level(declared) != "destructive":
        # Build admits a declared PRODUCER of the deliverable — but not a destructive one
        # (producing is the point of build mode; deleting is not).
        return ""

    if task_mode == "ask":
        return "Ask mode — only read-only tools run (switch to Agent to make changes)"
    if task_mode == "plan":
        return "Plan mode — inspection only, nothing is executed (switch to Agent to run it)"
    return (
        "Build mode — only read-only + artifact-producing tools run (switch to Agent for the rest)"
    )
