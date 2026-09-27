"""Abstract base for tool providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.errors import AgentError


class RiskLevel(str, Enum):
    """What a tool DECLARES a call to it does. ``SAFE`` is the one read-only declaration.

    - ``SAFE``: the tool only reads. A call changes nothing but PersonalClaw's own record of it
      — a usage counter, the audit log, a retained copy of its result, an index or cache
      derived from what it read, a one-time upgrade of its own store's layout: it creates,
      edits and deletes nothing the owner keeps, sends nothing anywhere, starts, stops and
      steers no run, and does not act on the desktop. Every "reads run without asking"
      decision admits exactly these: Ask and
      Plan mode, Trust reads, ``personalclaw run`` without ``--allow``, ``--approval reads``, a
      research-class leaf or subagent, a read-only room member, a dry run.
    - ``CAUTION``: it changes something (write_file, task_create, memory_remember,
      workflow_start, computer_click). It asks, unless the owner's posture answers for it.
    - ``DESTRUCTIVE``: arbitrary shell exec or a delete (bash, *_delete, memory_forget).

    A tool that declares nothing is ``CAUTION`` (:class:`ToolDefinition`'s default): read-only
    is an allowlist a tool opts into, never a conclusion drawn from its name.
    """

    SAFE = "safe"
    CAUTION = "caution"
    DESTRUCTIVE = "destructive"


#: The ``_meta`` key an MCP-shaped tool dict sets to declare it a Build-mode producer
#: (:attr:`ToolDefinition.builds`). MCP has no annotation for it, and ``_meta`` is the spec's
#: place for an implementation's own keys.
BUILDS_META_KEY = "personalclaw/builds"

#: The ``_meta`` key an MCP-shaped tool dict sets to declare that its only effect is a proposal
#: the owner accepts or dismisses (:attr:`ToolDefinition.proposes`).
PROPOSES_META_KEY = "personalclaw/proposes"


def risk_from_annotations(annotations: Any, *, trusted: bool) -> RiskLevel:
    """What an MCP tool's ``annotations`` declare, as a :class:`RiskLevel`.

    ``readOnlyHint: true`` is the read-only declaration, and it counts only when *trusted*:
    PersonalClaw's own tool modules always are, an external server only when the owner said so
    (``security.mcp_read_only_servers``). The MCP spec says the same thing — annotations from a
    server you do not trust are hints, not facts. ``destructiveHint: true`` counts from anyone,
    because believing it can only add a question. Anything else, including no annotations at
    all, is CAUTION.
    """
    hints = annotations if isinstance(annotations, dict) else {}
    read_only = hints.get("readOnlyHint") is True
    if read_only and trusted:
        return RiskLevel.SAFE
    if hints.get("destructiveHint") is True and not read_only:
        return RiskLevel.DESTRUCTIVE
    return RiskLevel.CAUTION


@dataclass
class ToolDefinition:
    """Schema for a tool exposed by a provider."""

    name: str
    description: str
    provider: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = True
    # What a call does (see RiskLevel). Undeclared is CAUTION: only a tool that says it only
    # reads is treated as a read.
    risk_level: RiskLevel = RiskLevel.CAUTION
    # Option-prompt-shaped tool that stalls without a human (AskUserQuestion and
    # kin). Stripped from the toolset for unattended runs (scheduled run-prompt/
    # run-workflow, Goal/Code loop cycles) so a background turn can't wedge
    # waiting for input it will never get. See ``INTERACTIVE_TOOL_NAME_HINTS``
    # for the name-pattern fallback that catches external-MCP interactive tools
    # which never set this flag.
    interactive: bool = False
    # Per-tool output cap (chars) for the shared truncation helper; None = no cap.
    max_output: int | None = None
    # A Build-mode producer: it creates or edits the deliverable a Build session is making (an
    # artifact, a document, a deck, a sheet, an image, a video, a skill). Build mode runs reads
    # and these, and never a DESTRUCTIVE one.
    builds: bool = False
    # Its only effect is a proposal the owner accepts or dismisses before anything changes (a
    # skill promotion, a template diff, a tile). Not a read — it writes the proposal, so Ask
    # mode, Trust reads and a dry run treat it as the change it is — but a research-class run's
    # `read` tool grant admits it, because filing for the owner's review is what those runs do.
    proposes: bool = False


# Name fragments that mark a tool as option-prompt-shaped even when its provider
# never set ``ToolDefinition.interactive`` (external MCP servers we don't own).
# Matched case-insensitively against the bare tool name. Conservative on purpose:
# only tools whose *whole job* is to block for a human answer (ask the user a
# question, request their input/confirmation, prompt for a choice).
INTERACTIVE_TOOL_NAME_HINTS: tuple[str, ...] = (
    "askuserquestion",
    "ask_user",
    "request_user_input",
    "request_input",
    "prompt_user",
    "user_prompt",
    "user_confirm",
    "confirm_with_user",
)


def is_interactive_tool(tool: "ToolDefinition") -> bool:
    """True if ``tool`` blocks for a human answer (explicit flag or name hint).

    Used to strip interactive tools from unattended runs. The explicit
    ``interactive`` flag is authoritative for tools we own; the name-hint
    fallback catches option-prompt-shaped tools from external MCP servers that
    never declared themselves interactive.
    """
    if tool.interactive:
        return True
    name = (tool.name or "").lower().replace("-", "_")
    compact = name.replace("_", "")
    return any(h.replace("_", "") in compact for h in INTERACTIVE_TOOL_NAME_HINTS)


@dataclass
class ToolResult:
    """Result of a tool invocation.

    Beyond pass/fail, carries the structured contract the model reads to recover
    and to decide whether to fetch more:
    - ``recovery_hints``: concrete next steps on failure ("file not found → try
      glob to locate it"). The model acts on these instead of guessing.
    - ``truncated`` + ``original_length``: set when ``output`` was capped, so the
      model knows content was cut and how much (can paginate / narrow / refetch).
    """

    success: bool
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    recovery_hints: list[str] = field(default_factory=list)
    truncated: bool = False
    original_length: int | None = None
    # The WHAT/WHY/FIX envelope for a failure an agent can
    # recover from. When set, its ``render()`` supplies the model-facing text and
    # its structural fields flow to the FE tool card + external clients. Additive:
    # existing results (``None``) render exactly as before via ``error``/hints.
    agent_error: "AgentError | None" = None


class ToolFailure(str):
    """A tool handler's answer for a failure it HANDLED rather than raised (#3487).

    The in-process MCP handlers (``mcp_*._call_tool``) are ``str``-returning by design and
    deliberately do not raise for expected failures — an unknown slug, a missing required
    argument, a service error code. Before this type, the only trace of such a failure was
    the PROSE of the message, so every consumer had to re-derive the verdict by reading it:
    ``InProcessMcpToolProvider.invoke`` did not try at all and answered ``success=True`` for
    every non-raising call, so ``POST /api/tools/invoke`` returned ``200 {ok: true,
    error: ""}`` and the hash-chained Security Event Log recorded four refused DESTRUCTIVE
    operations as ``outcome: "completed"``. An audit log that answers "did an agent delete
    anything?" wrongly is worse than the wire lie it came with.

    A predicate over the text was rejected rather than tuned: ``artifact_list`` answering
    ``"No artifacts found."`` is a success while ``artifact_delete`` answering
    ``"Artifact not found: X"`` is a failure, and a leading-``Error`` sniff calls both
    successes. It also stops working the moment a handler rewords a message — a protection
    that reads as present and cannot be relied on.

    Why a ``str`` SUBCLASS is the carrier, rather than a richer return type or a contextvar:

    * The verdict has to travel WITH the value. ``InProcessMcpToolProvider.invoke`` runs the
      handler through ``run_in_executor`` under ``contextvars.copy_context().run``, which
      isolates every mutation to the copy — so a contextvar a handler set cannot come back
      out of the worker thread at all.
    * Being a ``str``, it needs no signature change and no second code path: the MCP stdio
      loop, ``format_tool_result``, the staged-spec echo and every existing test read it
      exactly as before. The TYPE is the new information; the text is unchanged.

    Construct these through :func:`tool_failure`, which owns the package's failure
    convention (``Error: …`` / ``Error [CODE]: …``) in one place instead of at ~90 call
    sites. ``reason`` is the same message without that prefix, so a consumer that adds its
    own (``format_tool_result``) renders exactly one.
    """

    reason: str

    def __new__(cls, text: str, *, reason: str = "") -> "ToolFailure":
        self = super().__new__(cls, text)
        self.reason = reason or str(text)
        return self


def tool_failure(reason: str, *, code: str = "") -> ToolFailure:
    """The one owner of this package's model-facing tool-failure convention.

    Renders ``Error: {reason}``, or ``Error [{code}]: {reason}`` when the handler has a
    machine-readable code for the model to branch on. Returns a :class:`ToolFailure`, so the
    verdict is structural and no consumer has to read the prose to recover it.
    """
    head = f"Error [{code}]" if code else "Error"
    return ToolFailure(f"{head}: {reason}", reason=reason)


def maybe_truncate(text: str, cap: int | None) -> tuple[str, bool, int | None]:
    """Cap ``text`` to ``cap`` chars, signalling whether it was truncated.

    Returns ``(text, truncated, original_length)``. ``original_length`` is set
    only when truncation occurred (else None), so callers can populate the
    matching :class:`ToolResult` fields without re-measuring. A ``None`` cap (or
    text already within it) passes through untouched. Keeps a head + tail so the
    model sees both the start and the end of a long output rather than a hard cut.
    """
    if cap is None or len(text) <= cap:
        return text, False, None
    original_length = len(text)
    if cap <= 0:
        return "", True, original_length
    # Keep a head + tail with a marker, so structure at both ends survives.
    head = cap * 2 // 3
    tail = cap - head
    notice = f"\n…[truncated: {original_length} chars total, showing {cap}]…\n"
    truncated_text = text[:head] + notice + (text[-tail:] if tail > 0 else "")
    return truncated_text, True, original_length


class ToolProvider(ABC):
    """Provider interface for tool execution backends.

    Wraps both native PersonalClaw tools and MCP server tools in a common
    interface for discovery and invocation.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def list_tools(self) -> list[ToolDefinition]:
        """List all tools available from this provider."""
        ...

    @abstractmethod
    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool with the given arguments."""
        ...

    @property
    def connected(self) -> bool:
        return True

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "connected": self.connected,
        }
