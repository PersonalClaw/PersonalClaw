"""The native in-process agent loop — ``NativeAgentRuntime`` (E2-P4).

A ReAct-style tool-use loop that runs entirely inside the PersonalClaw process:

    user turn → INFERENCE (ModelProvider.complete) → if tool calls: execute
    (approval-gated) → feed results back → repeat; stop when a model turn makes
    no tool calls (or max_turns / cancel).

It emits the neutral :class:`~personalclaw.llm.events.AgentEvent` stream the chat
runner already consumes from ACP (text/thinking chunks, tool-call + tool-result
cards, a terminal ``EVENT_COMPLETE`` carrying *aggregated* usage), so the runner
needs no per-backend branching. History is owned **here** (``self._messages``) —
``ModelProvider.complete`` is stateless (E2-P2).

Decoupling: this module depends only on the ``ModelProvider`` /
``ToolProvider`` / ``AgentEvent`` contracts plus low-level ``security``. Hook
firing is an injected callable so the package stays free of any
``dashboard``/``chat_runner`` import.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.agents.native.approval import REJECT, ApprovalGate
from personalclaw.agents.native.tools import (
    format_tool_result,
    parse_tool_arguments,
    tool_definitions_to_openai_schema,
)
from personalclaw.agents.provider import AgentProvider
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.llm.prompt_cache import PromptCache, mark_cacheable_prefix
from personalclaw.tool_providers.base import RiskLevel

if TYPE_CHECKING:
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.llm.base import ModelProvider
    from personalclaw.tool_providers.base import ToolProvider

logger = logging.getLogger(__name__)

# Model-name-constraint sanitizer (provider-agnostic). Several providers must
# rewrite tool names to satisfy a naming constraint before sending them to the
# model — e.g. Bedrock Converse rejects "/" and caps names at 64 chars, matching
# the common OpenAI ``^[a-zA-Z0-9_-]{1,64}$`` shape. Providers reverse-map the
# name the model returns back to the real tool id, but if that round-trip ever
# fails (a name not in the turn's reverse map, or the model echoing the rewritten
# form) the sanitized name reaches dispatch and every exact-key lookup misses.
# The runtime keeps a sanitized(real)->real fallback so it can heal ANY provider.
_TOOL_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_TOOL_NAME_SANITIZE_MAX = 64


def _sanitized_tool_key(name: str) -> str:
    """Return the common model-safe form of ``name`` (illegal chars -> ``_``,
    capped at 64). Mirrors the constraint providers like Bedrock apply so the
    runtime can recognize a rewritten name and map it back to the real tool."""
    safe = _TOOL_NAME_SANITIZE_RE.sub("_", name or "")[:_TOOL_NAME_SANITIZE_MAX]
    return safe or "tool"


# Hook fire callback: (event_title, tool_input) -> awaitable[list[str]] of
# "BLOCKED:..." strings (non-empty ⇒ blocked). Mirrors chat_runner's fire shape.
HookFire = Callable[[str, str | None], Awaitable[list[str]]]

# Cap mid-turn steer injections (#37) so a message flood can't extend one turn
# forever — past this, further steers wait for the next turn.
_MAX_STEERS_PER_TURN = 4

# Sentinel: the tool passed deny-list + hooks but needs interactive approval,
# so the generator path (_execute_tool) must run the gated branch.
_NEEDS_APPROVAL: Any = object()

# Graduated per-(tool, params) failure thresholds for one run (OpenFang loop-guard
# shape). A tool failing the SAME way repeatedly burns an autonomous run's tokens
# + time before any other guard fires; this caps it. Params-aware so a tool that
# fails on input A but succeeds on input B isn't penalized for A's failures.
_BREAKER_WARN = 3  # ≥ this many same failures → warn the model, still allow
_BREAKER_BLOCK = 5  # ≥ this → refuse further identical calls this run
_BREAKER_CIRCUIT = 30  # > this total failures in a run → abort the whole run

# Structural loop detection (E3.1) — catches stuck-but-*successful* repetition the
# failure breaker misses, over (tool, params, result_digest) triples. Warn-only for
# the first release (§6 decision 3): looping is higher-variance than failure
# counting, so we observe-and-report before graduating to block.
_STRUCT_WINDOW = 16  # recent-call signatures kept for pattern matching
_STRUCT_REPEAT = 3  # ≥ this many identical triples in a row → no-progress
_STRUCT_PINGPONG_CYCLES = 3  # ≥ this many A↔B cycles (2× entries) → ping-pong


def _params_key(tool_name: str, args: dict) -> str:
    """Stable (tool, params) identity for breaker bucketing.

    Same tool + same args = same bucket, so repeated *identical* failing calls
    accumulate while genuinely different calls stay independent. Falls back to the
    tool name alone if args aren't JSON-serializable.
    """
    try:
        return f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
    except (TypeError, ValueError):
        return tool_name


# Volatile substrings that make two otherwise-identical results look different —
# timestamps, pids, durations, hex/uuid ids, memory addresses. Normalized out of
# the result digest so a call producing the "same" result each time is recognized
# as no-progress (result normalization). Order-independent.
_VOLATILE_PATTERNS = [
    re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    ),  # ISO ts
    re.compile(r"\b\d{10,13}\b"),  # epoch (s / ms)
    re.compile(r"0x[0-9a-fA-F]+"),  # hex / memory address
    re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    ),  # uuid
    re.compile(r"\b(?:pid|PID)[=: ]\s*\d+"),  # pid=NNN
    re.compile(r"\bin \d+(?:\.\d+)?\s*(?:ms|s|sec|seconds|m|min)\b"),  # "in 1.23s"
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|µs|us)\b"),  # bare durations
]


def _result_digest(result_str: str) -> str:
    """A normalized fingerprint of a tool result for structural loop detection.

    Strips volatile fields (timestamps / pids / durations / ids / addresses) so two
    runs of the *same* call that differ only in those don't look like progress, and
    bounds length so a huge identical output is cheap to compare. NOT used for the
    failure path — only the (tool, params, result_digest) structural triple.
    """
    s = result_str or ""
    for pat in _VOLATILE_PATTERNS:
        s = pat.sub("·", s)
    s = " ".join(s.split())  # collapse whitespace
    if len(s) > 512:
        s = s[:256] + "…" + s[-256:]
    return s


class _FailureBreaker:
    """Per-run progress tracker with graduated verdicts.

    Two parallel paths over the same call stream:

    * **failure path** — ``record(key, failed)`` counts consecutive *failures* per
      ``(tool, params)`` key; ``count(key)`` drives the BLOCK/WARN rungs and
      ``total_failures`` the run-wide circuit breaker. A success clears the key.
    * **structural path** (E3.1) — ``record_structural(sig)`` tracks recent
      ``(tool, params, result_digest)`` triples to catch stuck-but-*successful*
      repetition: the same triple N× in a row (no-progress), or an A↔B↔A↔B
      alternation (ping-pong). Returns a reason string on detection, else "".
      Warn-only: the runtime injects an observation; it does not block (yet).
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self.total_failures = 0
        # Recent structural signatures (most-recent last), bounded to the window.
        self._recent: deque[str] = deque(maxlen=_STRUCT_WINDOW)
        # Reasons already reported this run, so we warn once per distinct loop and
        # don't re-inject the same observation every subsequent identical call.
        self._struct_reported: set[str] = set()

    def reset(self) -> None:
        self._counts.clear()
        self.total_failures = 0
        self._recent.clear()
        self._struct_reported.clear()

    def reset_structural(self) -> None:
        """Re-arm structural detection (after a compaction) without touching the
        failure counts — a loop that resumes identically post-compaction should be
        caught fresh (post-compaction guard)."""
        self._recent.clear()
        self._struct_reported.clear()

    def record(self, key: str, failed: bool) -> int:
        if failed:
            self.total_failures += 1
            self._counts[key] = self._counts.get(key, 0) + 1
        else:
            self._counts.pop(key, None)  # a success clears this key's streak
        return self._counts.get(key, 0)

    def count(self, key: str) -> int:
        return self._counts.get(key, 0)

    def record_structural(self, sig: str) -> str:
        """Record a ``(tool, params, result_digest)`` signature; return a reason
        string when a structural loop is newly detected this run, else ``""``.

        Detects (a) no-progress: the same signature ``_STRUCT_REPEAT`` times in a
        row; (b) ping-pong: an A↔B alternation spanning ``_STRUCT_PINGPONG_CYCLES``
        cycles. Each distinct loop is reported once (dedup via ``_struct_reported``)
        so the warning fires on the turn the loop becomes evident, not every call.
        """
        self._recent.append(sig)
        recent = list(self._recent)

        # (a) no-progress: identical signature repeated at the tail.
        tail = recent[-_STRUCT_REPEAT:]
        if len(tail) == _STRUCT_REPEAT and len(set(tail)) == 1:
            reason = f"no-progress:{sig}"
            if reason not in self._struct_reported:
                self._struct_reported.add(reason)
                return (
                    f"the same tool call produced the same result "
                    f"{_STRUCT_REPEAT} times in a row"
                )

        # (b) ping-pong: A,B,A,B,… alternation at the tail spanning the cycle count.
        span = _STRUCT_PINGPONG_CYCLES * 2
        tailp = recent[-span:]
        if len(tailp) == span:
            a, b = tailp[0], tailp[1]
            if a != b and all(tailp[i] == (a if i % 2 == 0 else b) for i in range(span)):
                reason = f"ping-pong:{a}|{b}"
                if reason not in self._struct_reported:
                    self._struct_reported.add(reason)
                    return (
                        f"two tool calls are alternating without making progress "
                        f"({_STRUCT_PINGPONG_CYCLES}× A↔B with no new state)"
                    )
        return ""


class NativeAgentRuntime(AgentProvider):
    """In-process agent runtime for one session."""

    def __init__(
        self,
        *,
        definition: "AgentRuntimeDefinition",
        model_provider: "ModelProvider",
        tool_providers: list["ToolProvider"] | None = None,
        cwd: Path | None = None,
        session_key: str = "",
        max_turns: int = 100,
        hook_fire: HookFire | None = None,
        extra_deny_patterns: list[str] | None = None,
        unattended: bool = False,
        dry_run: bool = False,
        reasoning_effort: str = "",
        project_id: str = "",
        tool_groups: list[str] | None = None,
        surface: str = "",
    ) -> None:
        self._definition = definition
        self._model = model_provider
        # The Project this session's work scopes under ("" = none). Bound per-turn via
        # bind_tool_context so artifact_save can stamp its project_id (S5 — tie work
        # created during a project's session/loop back to that Project).
        self._project_id = project_id or ""
        # Per-turn reasoning effort ("" | low | medium | high | max) forwarded to
        # the model's complete() — providers whose model supports extended thinking
        # map it (Anthropic thinking budget / OpenAI reasoning_effort); others ignore.
        self._reasoning_effort = reasoning_effort or ""
        self._tool_providers = list(tool_providers or [])
        self._cwd = Path(cwd) if cwd else None
        self._session_key = session_key
        # Per-turn procedural-outcome accumulator (M5d): bounded list of
        # (tool, failed) the after-turn review drains into procedural memory.
        # Capped so a long run can't grow it unbounded.
        self._tool_outcomes: list[tuple[str, bool]] = []
        # Unattended run (scheduled run-prompt/run-workflow, Goal/Code loop cycle,
        # dry-run replay): no human is present. Interactive tools are stripped at
        # start() and the approval gate fails fast (recoverable denial, no 300s
        # park) so the turn can't wedge waiting for input it will never get.
        self._unattended = bool(unattended)
        # Dry-run replay (T9): observe-mode. Write-capable tools (any non-SAFE
        # risk level) are NOT executed — they return a synthetic "would have …"
        # observation so the run previews what WOULD happen with the current
        # prompt/workflow without side effects. Read-only SAFE tools still run so
        # the agent reasons over real state. A dry run is always unattended too.
        self._dry_run = bool(dry_run)
        if self._dry_run:
            self._unattended = True
        # The agent-scope binding id (workflow scope_ref form). For a native turn
        # this is the bare profile name (matching resolve_agent_id's native branch),
        # so an agent-scoped SOP this agent authors binds to itself.
        self._agent_id = getattr(definition, "name", "") or ""
        self._max_turns = max_turns
        self._hook_fire = hook_fire
        self._extra_deny = list(extra_deny_patterns or [])

        # Conversation history — owned by the loop (complete() is stateless).
        self._messages: list[dict] = []
        # Discovered tool surface, populated by start().
        self._tool_defs: list[Any] = []
        self._tool_schema: list[dict] = []
        self._tool_index: dict[str, "ToolProvider"] = {}
        # Fallback name resolver (provider-agnostic): sanitized(real_name)->real_name,
        # populated ONLY for names that need rewriting AND sanitize uniquely (no
        # collisions). Consulted when the exact _tool_index lookup misses, so a
        # provider whose reverse-map didn't round-trip a rewritten name still
        # dispatches. Exact match always stays the primary path (see _resolve_name).
        self._tool_sanitized_index: dict[str, str] = {}
        self._tool_retriever: Any = None  # built in start() (per-turn tool retrieval)
        self._tool_search_def: Any = None  # synthetic escape-hatch def (built in start())
        self._tool_schema_def: Any = None  # synthetic schema-expander def (built in start())
        self._reset_tools_def: Any = None  # synthetic group meta-tool (built in start())
        # ── tool groups (CONTEXT-ECONOMY §5) ──
        # The surface whose per-surface defaults seed activation ("" = chat, i.e.
        # everything active). The explicit tool_groups kwarg (the engine's
        # per-template seam) wins over the surface default when given.
        self._surface = surface or ""
        self._group_seed = list(tool_groups) if tool_groups is not None else None
        # Derived partition of the assembled catalog (built in start()).
        self._groups: list[Any] = []
        # ACTIVE group names, or None = every group active (grouping is a no-op —
        # the tool block is then byte-identical to having no groups at all).
        self._active_groups: set[str] | None = None
        # Newly-activated groups whose instructions the NEXT turn should carry
        # (group changes take effect at the turn boundary, §3 prefix corollary).
        self._pending_group_note = ""
        # tool name → group name, and the group-filtered defs (both built in
        # start()/_assemble_schema; the filtered set IS _tool_defs when ungrouped).
        self._group_of_name: dict[str, str] = {}
        self._active_defs: list[Any] = []
        # tool name → the provider key the disable gate resolved (grouping reuses it).
        self._provider_of: dict[str, str] = {}
        # Groups whose declared capability doesn't resolve (§5.5): not activatable,
        # not stub-listed. Recomputed on every schema assembly.
        self._unofferable: set[str] = set()
        self._last_result_meta: dict = {}  # typed meta of the last tool result (for TOOL_RESULT)
        self._approval = ApprovalGate()
        # Approval policy: "" / "default" prompt; "auto"/"yolo" auto-approve.
        self._approval_policy = ""
        # Task mode (agent/ask/plan/build) — ORTHOGONAL to approval. Gates WHICH
        # tools may run, enforced in _guard_and_invoke before approval is consulted
        # so a Trust/YOLO auto-approve can never bypass an ask/plan/build restriction.
        self._task_mode = "agent"
        self._cancelled = False
        self._last_context_pct = 0.0
        # Per-run consecutive-failure breaker (reset each stream() turn).
        self._breaker = _FailureBreaker()
        # Compaction save fractions (anti-thrashing across the session).
        self._compaction_saves: list[float] = []
        # Queue-steering (#37): a callback the loop drains at each model boundary
        # for mid-turn user messages. None = no steering (the default until wired).
        self._pull_steer: "Callable[[], list[str]] | None" = None
        self._steers_injected = 0
        # Prompt-cache prefix generation (PROMPT-CACHE-SUBSTRATE). Bumped whenever the
        # cached prefix is invalidated — i.e. compaction rewrites history. The agent
        # DEFINITION is immutable per loop (``self._definition`` is set once in __init__
        # and never reassigned), so the construction-time 0 is the definition baseline;
        # only compaction advances it. An EXPLICIT-cache provider's adapter reads it off
        # the cache hint to distinguish a fresh prefix from an invalidated one.
        self._cache_generation: int = 0

    # ── identity ──
    @property
    def provider_id(self) -> str:
        return "native"

    # ── lifecycle ──
    async def start(self) -> None:
        """Discover tools from every provider → model tool-schema + name index."""
        if not getattr(self._model, "supports_tools", False):
            # Tool-less model (e.g. some Ollama models): single-shot, no tools.
            logger.info("native: model has no tool support; running tool-less")
            self._tool_defs, self._tool_schema, self._tool_index = [], [], {}
            self._tool_sanitized_index = {}
            self._groups, self._active_defs, self._group_of_name = [], [], {}
            return
        # User-disabled tools/providers (PT3 + UT4): a harder gate than retrieval —
        # a disabled tool (individually OR via its whole provider being off) is
        # removed from BOTH the schema/catalog AND the dispatch index, so the model
        # can't see or call it. Core-locked tools + the locked platform provider are
        # never disabled (the tool_prefs guards ignore them). Load once; fail-open.
        from personalclaw.tool_providers import tool_prefs

        disabled_keys = tool_prefs.load_disabled()
        disabled_provs = tool_prefs.load_disabled_providers()
        defs: list[Any] = []
        index: dict[str, ToolProvider] = {}
        provider_of: dict[str, str] = {}  # tool name → resolved provider key (grouping)
        dropped: list[str] = []
        for prov in self._tool_providers:
            prov_name = getattr(prov, "name", "") or ""
            if prov_name in disabled_provs:
                logger.info(
                    "native: provider %r is user-disabled — skipping its toolset", prov_name
                )
                continue
            try:
                tools = await prov.list_tools()
            except Exception:  # noqa: BLE001 - a broken provider must not kill start
                logger.debug("native: tool provider %s list failed", prov_name, exc_info=True)
                continue
            for t in tools:
                # Prefer the tool's own provider tag; fall back to the provider
                # instance name (matches how GET /api/tools keys the disable set).
                pkey = getattr(t, "provider", "") or prov_name
                if tool_prefs.is_disabled(pkey, t.name, disabled_keys, disabled_provs):
                    dropped.append(t.name)
                    continue
                defs.append(t)
                index[t.name] = prov
                # Same provider key the disable gate resolved (tool tag, else the
                # instance name) — group derivation reuses it so a provider that
                # forgot to stamp its tools still groups correctly instead of
                # collapsing into "other".
                provider_of[t.name] = pkey
        if dropped:
            logger.info("native: %d user-disabled tool(s) excluded: %s", len(dropped), dropped)
        # Unattended runs strip option-prompt-shaped tools so a background turn
        # can't wedge waiting for a human (T5). A property of the run MODE, applied
        # here where the toolset is assembled — not per-tool, not per-loop.
        if self._unattended:
            from personalclaw.tool_providers.base import is_interactive_tool

            stripped = [t.name for t in defs if is_interactive_tool(t)]
            if stripped:
                defs = [t for t in defs if not is_interactive_tool(t)]
                index = {n: p for n, p in index.items() if n not in stripped}
                logger.info("native: unattended run — stripped interactive tools %s", stripped)
        self._tool_defs = defs
        self._tool_index = index
        # GROUP PARTITION (§5.1) — derived from the assembled catalog, so it
        # reflects exactly the providers this session actually has (post-disable,
        # post-strip). Seeds activation from the explicit kwarg or this surface's
        # default; None = every group active.
        from personalclaw.tool_providers import groups as _groups

        self._provider_of = provider_of
        self._groups = _groups.partition(defs, provider_of=provider_of)
        if self._group_seed is not None:
            self._active_groups = {_groups.CORE_GROUP, *self._group_seed}
        else:
            self._active_groups = _groups.resolve_default_groups(self._surface)
        # SCHEMA ASSEMBLY (group filter → serialization) — factored out so a group
        # change can re-run it without re-discovering providers.
        self._assemble_schema()
        # Sanitized-name fallback (provider-agnostic reverse-map insurance). For
        # each real name that a provider WOULD have to rewrite, remember the
        # rewritten form -> real name, but ONLY when that sanitized form is unique
        # across the toolset (ambiguous collisions are dropped so we never dispatch
        # the wrong tool) and only when it actually differs from the real name (an
        # already-legal name needs no fallback). Built once here so dispatch stays
        # a dict lookup. See _resolve_name for how it's consulted.
        sanitized: dict[str, str] = {}
        ambiguous: set[str] = set()
        for real in index:
            key = _sanitized_tool_key(real)
            if key == real or key in index:
                # Legal already, or would shadow a real exact name — never remap.
                continue
            if key in sanitized and sanitized[key] != real:
                ambiguous.add(key)
                continue
            sanitized[key] = real
        for key in ambiguous:
            sanitized.pop(key, None)
        self._tool_sanitized_index = sanitized
        # Risk-level map: dry-run observe-mode intercepts non-SAFE tools (T9), and
        # the permission-request event carries a tool's declared risk to the gate.
        # Built once here so the hot path is a dict lookup.
        self._tool_risk = {t.name: getattr(t, "risk_level", RiskLevel.SAFE) for t in defs}
        # Per-turn tool retrieval (TR2): a selector over the full catalog. K
        # defaults above the builtin count → behavioral no-op until MCP catalogs
        # grow; selection only changes the schema the model SEES (dispatch via
        # _tool_index is untouched). Fails open (returns the full set on any issue).
        from personalclaw.agents.native.tool_retrieval import ToolRetriever

        self._tool_retriever = ToolRetriever(defs)
        # Synthetic schema for the tool_search escape hatch — added to the surfaced
        # set only on a reduced turn (handled in _invoke, not a provider).
        from personalclaw.tool_providers.base import ToolDefinition as _TD

        self._tool_search_def = _TD(
            name="tool_search",
            provider="native",
            requires_approval=False,
            description=(
                "Find tools by capability. Searches the FULL catalog (incl. tools shown "
                "this turn only as a name in the catalog). Args: query (str), optional "
                "limit (int). Returns ranked name+description; then call tool_schema(name) "
                "to see a tool's inputs, or just call it by name."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
        )
        # Progressive disclosure: tools not in the per-turn full-schema set still
        # appear in a name+description CATALOG. tool_schema expands ONE of them to
        # its full input schema on demand, so the model can call any catalog tool
        # correctly without ever carrying every schema.
        self._tool_schema_def = _TD(
            name="tool_schema",
            provider="native",
            requires_approval=False,
            description=(
                "Get the full input schema for a tool by name — use when the catalog lists "
                "a tool you want but you need its exact arguments. Args: tool_name (str). "
                "Returns the tool's parameters/description; then call the tool by name."
            ),
            parameters={
                "type": "object",
                "properties": {"tool_name": {"type": "string"}},
                "required": ["tool_name"],
            },
        )
        # The group meta-tool (§5.2) — final-state semantics, core group, SAFE: it
        # changes what the model SEES, not what it can do. Only ever surfaced on a
        # session where grouping is actually in effect (see _assemble_schema).
        self._reset_tools_def = _TD(
            name="reset_tools",
            provider="native",
            requires_approval=False,
            description=(
                "Set which tool GROUPS are active, so unused groups don't spend context "
                "on their schemas. FINAL STATE, not a delta: pass every group you want "
                "active; any group you omit is deactivated. The 'core' group is always "
                "on. Returns the new active set plus usage guidance for each group you "
                "just activated. Changes apply from your NEXT turn (they rewrite the "
                "tool block, which costs a cache re-read) — so batch your changes into "
                "ONE call instead of toggling groups one at a time. Every tool stays "
                "callable by name even while its group is inactive; use tool_search to "
                "find one. Args: groups (object mapping group name → true/false)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "groups": {
                        "type": "object",
                        "description": (
                            "Group name → true (active) / false (inactive). Omitted "
                            "groups deactivate."
                        ),
                        "additionalProperties": {"type": "boolean"},
                    }
                },
                "required": ["groups"],
            },
        )
        logger.info(
            "native: discovered %d tools across %d providers", len(defs), len(self._tool_providers)
        )

    # ── tool groups (CONTEXT-ECONOMY §5) ──
    def _assemble_schema(self) -> None:
        """Build the model-facing tool schema from ``self._tool_defs``.

        The group filter lives HERE — after the hard gates (user-disable,
        unattended strip) that ran in :meth:`start`, before serialization:

            providers → disable → unattended strip → GROUP FILTER → schema

        When no grouping is in effect (``_active_groups is None`` — the default
        for interactive chat) this is exactly the old one-line serialization, so
        the tool block is BYTE-IDENTICAL to having no groups at all. Re-runnable:
        :meth:`refresh_toolset` calls it after an activation change.
        """
        from personalclaw.tool_providers import groups as _groups

        self._group_of_name = {
            (getattr(d, "name", "") or ""): _groups.group_of_tool(
                d, provider=self._provider_of.get(getattr(d, "name", "") or "", "")
            )
            for d in self._tool_defs
        }
        if self._active_groups is None:
            # No grouping in effect: nothing is filtered, so DON'T probe. The
            # capability probes read config + walk the provider registry, and this
            # runs on every runtime start (including every test's) — paying for an
            # answer that cannot change the outcome made the suite ~7x slower.
            self._unofferable = set()
            self._active_defs = list(self._tool_defs)
        else:
            # PER-CAPABILITY GATING (§5.5): a group whose declared capability doesn't
            # resolve is not offerable — neither active nor stub-listed, so the model
            # never sees tools that cannot work. Probed only for groups that actually
            # DECLARE a capability (the common case declares none, so this is usually
            # zero probes), and re-probed on refresh so binding a model mid-session
            # makes its group offerable. always_on groups are never gated.
            self._unofferable = {
                g.name
                for g in self._groups
                if g.capability and not g.always_on and not _groups.offerable(g)
            }
            self._active_groups -= self._unofferable
            self._active_defs = [
                d
                for d in self._tool_defs
                if self._group_of_name.get(getattr(d, "name", "") or "") in self._active_groups
            ]
        self._tool_schema = tool_definitions_to_openai_schema(self._active_defs)

    def refresh_toolset(self) -> None:
        """Re-run schema assembly after an activation change (no re-discovery).

        Provider discovery is untouched — only which of the already-discovered
        tools carry their schema. Called by ``reset_tools``; the new block reaches
        the model at the next turn boundary (§3 prefix corollary).
        """
        self._assemble_schema()

    def _group_stub_lines(self) -> list[str]:
        """One line per INACTIVE group: the capability stays visible at ~15 tokens
        instead of ~7 schemas, and names the activation step (fail-open triad)."""
        if self._active_groups is None:
            return []
        lines: list[str] = []
        for g in self._groups:
            if g.name in self._active_groups or g.name in self._unofferable:
                continue
            sample = ", ".join(g.tools[:4])
            more = f", +{len(g.tools) - 4} more" if len(g.tools) > 4 else ""
            lines.append(
                f"- {g.name} ({_n_tools(len(g.tools))}, INACTIVE): {sample}{more} "
                f'— reset_tools({{"{g.name}": true}}) to activate'
            )
        return lines

    def _reset_tools(self, args: dict) -> str:
        """Apply ``reset_tools`` — FINAL-STATE group activation (§5.2).

        One boolean per group; every non-``always_on`` group the caller omits (or
        sets false) deactivates. Final-state rather than delta semantics because
        deltas accumulate drift over a long session. Returns the new active set
        plus the instructions of each NEWLY activated group, so usage guidance
        arrives exactly when the tools do.
        """
        raw = args.get("groups")
        if not isinstance(raw, dict):
            return (
                "Error: `groups` must be an object mapping group name → true/false, "
                'e.g. {"groups": {"schedule": true, "memory": true}}.'
            )
        known = {g.name: g for g in self._groups}
        wanted = {str(k) for k, v in raw.items() if bool(v)}
        unknown = sorted(n for n in wanted if n not in known)
        wanted &= set(known)
        # A group whose capability doesn't resolve can't be activated — its tools
        # would be present but inert. Name it so the model learns WHY, instead of
        # silently re-reading a set that didn't change (§5.5).
        blocked = sorted(n for n in wanted if n in self._unofferable)
        wanted -= self._unofferable
        # always_on groups can never be deactivated.
        new_active = {g.name for g in self._groups if g.always_on} | wanted
        previous = self._active_groups if self._active_groups is not None else set(known)
        newly = sorted(new_active - previous)
        self._active_groups = new_active
        self.refresh_toolset()

        parts: list[str] = []
        if unknown:
            parts.append(
                f"Unknown group(s) ignored: {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(known))}."
            )
        if blocked:
            parts.append(
                f"Unavailable in this install (not activated): {', '.join(blocked)} — "
                "the capability these tools need isn't configured, so they would fail."
            )
        active_desc = ", ".join(
            f"{n} ({_n_tools(len(known[n].tools))})" for n in sorted(new_active) if n in known
        )
        parts.append(f"Active tool groups: {active_desc}.")
        inactive = sorted(set(known) - new_active - self._unofferable)
        if inactive:
            parts.append(f"Inactive: {', '.join(inactive)}.")
        for name in newly:
            instructions = known[name].instructions
            if instructions:
                parts.append(f"[{name}] {instructions}")
        parts.append(
            "This takes effect on your NEXT turn — the tools you just activated "
            "carry their schemas from then on. (Any tool remains callable by name "
            "even while its group is inactive.)"
        )
        note = " ".join(p for p in parts if p)
        # Carried into the next turn's context so the model sees the new state
        # alongside the rewritten tool block.
        self._pending_group_note = (
            f"[tool groups] {note}" if self._active_groups != previous else ""
        )
        logger.info(
            "native: reset_tools → active=%s (newly=%s)",
            sorted(new_active),
            newly or "none",
        )
        return note

    async def shutdown(self) -> None:
        self._cancelled = True
        self._approval.cancel_all()

    # ── the turn ──
    async def stream(self, message: str) -> AsyncIterator[AgentEvent]:
        """Run the ReAct loop for one user turn (``message`` is the full,
        context-built turn-0 prompt the chat runner already assembled)."""
        self._cancelled = False
        self._breaker.reset()
        self._steers_injected = 0
        self._messages.append({"role": "user", "content": message})

        tools_kwarg, turn_note = self._prepare_turn_tools(message)
        if turn_note:
            # SYSTEM role: this is runtime metadata, not something the user said.
            self._messages.append({"role": "system", "content": turn_note})
        agg_in = agg_out = 0
        agg_cost = 0.0
        turns = 0
        # Turn telemetry (parity with ACP's last_prompt_stats): events observed
        # this prompt + total tool calls made. Surfaced on the terminal
        # EVENT_COMPLETE so the chat runner renders the "Turn complete" line.
        agg_events = 0
        agg_tool_calls = 0

        while turns < self._max_turns:
            if self._cancelled:
                yield AgentEvent(
                    kind=EVENT_COMPLETE,
                    stop_reason="cancelled",
                    num_turns=turns,
                    event_count=agg_events,
                    tool_call_count=agg_tool_calls,
                )
                return
            turns += 1

            # 0) COMPACT — when context crosses the threshold, run structured
            # compaction (no-LLM tool-output pruning pre-pass → 4-region →
            # structured summary). Anti-thrashing skips it if recent passes
            # barely helped. ACP backends own their own compaction; this is the
            # native loop's.
            self._maybe_compact()

            assistant_text = ""
            tool_calls: list[AgentEvent] = []
            usage: AgentEvent | None = None

            # 1) INFERENCE — stream a stateless completion over full history.
            # Prompt-cache middleware (PROMPT-CACHE-SUBSTRATE): resolve the provider's
            # graded cache mode off the instance (mirrors the supports_tools getattr) and
            # mark a cacheable prefix. NONE → the same object is handed back (byte-identical
            # for an undeclared provider); AUTOMATIC → also unchanged (no marker needed);
            # EXPLICIT → a NEW list with one neutrally-hinted message, its own adapter
            # translating the hint (a later atom). self._messages itself is never mutated.
            mode = getattr(self._model, "prompt_cache", PromptCache.NONE)
            logger.debug("native: prompt-cache mode %s", getattr(mode, "value", mode))
            msgs = mark_cacheable_prefix(self._messages, mode, generation=self._cache_generation)
            async for ev in self._model.complete(
                msgs,
                tools=tools_kwarg,
                model=self._definition.model or None,
                reasoning_effort=self._reasoning_effort,
            ):
                if self._cancelled:
                    break
                agg_events += 1
                if ev.kind == EVENT_TEXT_CHUNK:
                    assistant_text += ev.text
                    yield ev
                elif ev.kind == EVENT_THINKING_CHUNK:
                    yield ev
                elif ev.kind == EVENT_TOOL_CALL:
                    tool_calls.append(ev)
                elif ev.kind == EVENT_COMPLETE:
                    usage = ev

            if usage is not None:
                agg_in += usage.input_tokens or 0
                agg_out += usage.output_tokens or 0
                agg_cost += usage.cost_usd or 0.0
                if usage.context_usage_pct:
                    self._last_context_pct = usage.context_usage_pct

            agg_tool_calls += len(tool_calls)

            # Record the assistant turn (text + any tool calls) into history.
            self._messages.append(self._assistant_msg(assistant_text, tool_calls))

            # 2) STOP — a model turn with no tool calls ends the agent turn…
            #    …UNLESS the user steered while that text was streaming. The steer
            #    drain used to live only at 3b (after the tool batch), so a turn that
            #    called NO tools — plain prose, the most common shape — returned here
            #    and the steer was silently discarded even though the API had already
            #    answered {"steered": true}. Draining here keeps the turn alive for one
            #    more inference so the steer lands inside the SAME answer, which is the
            #    entire promise of steering (PLATFORM-RESILIENCE S6.1).
            if tool_calls == [] and not self._cancelled and self._drain_steers_into_history():
                continue
            if not tool_calls or self._cancelled:
                # If we're stopping with tool calls still pending (cancelled
                # mid-turn — watchdog wedged-turn recovery / circuit-breaker),
                # the assistant message above carries unanswered tool_calls. Leave
                # them unpaired and the NEXT turn's history replay breaks every
                # tool-using provider (Bedrock Converse rejects an unanswered
                # toolUse outright). Pair each with a synthetic result so history
                # stays well-formed across cycles.
                if tool_calls and self._cancelled:
                    for call in tool_calls:
                        self._messages.append(
                            self._tool_result_msg(call, "Error: cancelled before this tool ran")
                        )
                yield AgentEvent(
                    kind=EVENT_COMPLETE,
                    stop_reason="cancelled" if self._cancelled else "end_turn",
                    input_tokens=agg_in,
                    output_tokens=agg_out,
                    cost_usd=agg_cost,
                    num_turns=turns,
                    context_usage_pct=self._last_context_pct,
                    event_count=agg_events,
                    tool_call_count=agg_tool_calls,
                )
                return

            # 3) TOOL EXECUTION — each call is a sub-generator that yields its
            #    UI card, (maybe) a permission request it parks on, the result
            #    card, and appends the tool-result message to history itself.
            for call in tool_calls:
                async for ev in self._execute_tool(call):
                    agg_events += 1
                    yield ev

            # 3b) STEER — drain any messages the user sent mid-turn (queue-steering
            #     #37). They land HERE, at the model boundary AFTER the tool batch
            #     (so tool-result pairing is intact), as fresh user input the next
            #     inference sees. Capped per turn so a flood can't extend one turn
            #     forever. Steer mode only; followup/collect/interrupt are handled
            #     by the runner before the turn even reaches the loop.
            if self._drain_steers_into_history():
                yield AgentEvent(
                    kind=EVENT_TEXT_CHUNK, text=""
                )  # keep stream warm; UI shows the steer via activity
            # 4) REPEAT — re-infer with tool results now in context.

        # max_turns exhausted
        yield AgentEvent(
            kind=EVENT_COMPLETE,
            stop_reason="max_turns",
            input_tokens=agg_in,
            output_tokens=agg_out,
            cost_usd=agg_cost,
            num_turns=turns,
            context_usage_pct=self._last_context_pct,
            event_count=agg_events,
            tool_call_count=agg_tool_calls,
        )

    def _prepare_turn_tools(self, message: str) -> tuple[list[dict] | None, str]:
        """Decide this turn's ``tools`` kwarg + any runtime note to inject.

        Two composable reductions, in order:

        * **GROUPS** (§5) — inactive groups' schemas are out of ``_active_defs``
          already (assembly-time); each contributes ONE stub line instead.
        * **RETRIEVAL** (TR2) — within the active set, surface the relevant
          projection this turn and defer the long tail's parameter schemas to a
          name+description catalog.

        Both fail open and neither touches ``_tool_index``, so every tool stays
        callable regardless of what this returns. When no grouping is in effect
        and retrieval doesn't reduce, the result is exactly ``_tool_schema`` — the
        byte-identical no-groups path.
        """
        grouped = self._active_groups is not None
        pool = self._active_defs if grouped else self._tool_defs
        stub_lines = self._group_stub_lines()
        # A group change from last turn announces itself here, at the boundary
        # where the rewritten tool block actually reaches the model.
        pending, self._pending_group_note = self._pending_group_note, ""

        # Per-turn tool retrieval (TR2): core ∪ top-K ∪ structural ∪ sticky, scoped
        # to the ACTIVE groups — retrieval composes with grouping rather than
        # competing, so the K budget is spent only on tools whose schemas can ride
        # this turn. No-op until the pool exceeds K; fails open to the full pool.
        restrict = {getattr(d, "name", "") for d in pool} if grouped else None
        selected_defs = (
            self._tool_retriever.select(message, restrict=restrict)
            if self._tool_retriever
            else pool
        )
        reduced = bool(self._tool_retriever) and len(selected_defs) < len(pool)

        notes: list[str] = []
        if pending:
            notes.append(pending)
        if not reduced:
            # No retrieval reduction: the assembled (group-filtered) schema stands.
            surfaced_defs = list(pool)
            if grouped:
                surfaced_defs.append(self._reset_tools_def)
                tools_kwarg = tool_definitions_to_openai_schema(surfaced_defs) or None
            else:
                tools_kwarg = self._tool_schema or None
        else:
            # PROGRESSIVE DISCLOSURE: nothing is hidden, only the (large) parameter
            # schemas of the long tail are deferred. Tier 1 = relevant tools' FULL
            # schemas + the discovery tools; Tier 2 = a compact name+description
            # CATALOG of everything else, in a system message. The model can call
            # tool_schema(name) to expand any catalog tool, or tool_search to rank
            # by capability — and dispatch via _tool_index works for ANY tool name,
            # surfaced or not. So the model can never conclude a capability is absent.
            surfaced = [*selected_defs, self._tool_search_def, self._tool_schema_def]
            if grouped:
                surfaced.append(self._reset_tools_def)
            tools_kwarg = tool_definitions_to_openai_schema(surfaced) or None
            exclude = {getattr(d, "name", "") for d in surfaced}
            if grouped:
                # Only ACTIVE-group tools belong in the deferred-schema catalog;
                # inactive groups are represented by their stub lines instead.
                exclude |= {n for n in self._group_of_name if n not in (restrict or set())}
            catalog = self._tool_retriever.catalog(exclude=exclude)
            notes.append(
                "[tool catalog] To save context, only the most relevant tools above carry their "
                "full input schema this turn. Every OTHER available tool is listed below by "
                'name + description. To use one: call tool_schema("name") to see its inputs, '
                'then call it — or call tool_search("capability") to rank the catalog. Every '
                "tool here is fully available; nothing is disabled.\n" + catalog
            )
            logger.debug(
                "native: tier-1 %d/%d tools (+tool_search,+tool_schema); catalog=%d tools",
                len(selected_defs),
                len(pool),
                len(pool) - len(selected_defs),
            )
        if stub_lines:
            notes.append(
                "[inactive tool groups] These capabilities exist but their schemas are not "
                "loaded this turn, to save context. Activate the ones you need in ONE "
                "reset_tools call (it takes final state — list every group you want active); "
                "or just call a tool by name, which still works.\n" + "\n".join(stub_lines)
            )
        return tools_kwarg, "\n\n".join(notes)

    async def _execute_tool(self, call: AgentEvent) -> AsyncIterator[AgentEvent]:
        """Run one tool call: deny-list → PreToolUse hook → approval → invoke.

        Yields the tool-call card, an ``EVENT_PERMISSION_REQUEST`` when approval
        is needed (parking on the gate until ``approve_tool``/``reject_tool``
        resolves it), then the tool-result card; appends the tool-result message
        to history so the next inference sees it.
        """
        from personalclaw import security

        tool_name = self._resolve_name(call.title or "")
        args = parse_tool_arguments(call.tool_input)

        # UI card for the call. Carry the tool's declared risk so the chat runner's
        # invoked-log records the authoritative risk (this event fires for EVERY tool
        # that runs, incl. runtime auto-approved ones that never reach the gate).
        yield AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id=call.tool_call_id,
            title=tool_name,
            tool_input=args,
            risk_level=self._tool_risk.get(tool_name, RiskLevel.SAFE).value,
        )

        # Consecutive-failure breaker: refuse a call that has already failed the
        # same way ≥ _BREAKER_BLOCK times this run, before wasting another invoke.
        _bkey = _params_key(tool_name, args)
        if self._breaker.count(_bkey) >= _BREAKER_BLOCK:
            blocked_str = (
                f"Error: tool `{tool_name}` was blocked — it has already failed "
                f"{self._breaker.count(_bkey)} times this run with these same "
                "arguments. Do NOT call it this way again; change your approach or "
                "stop and explain what's blocking you."
            )
            yield AgentEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id=call.tool_call_id,
                title=tool_name,
                tool_output=blocked_str,
            )
            self._messages.append(self._tool_result_msg(call, blocked_str))
            return

        result_str = await self._guard_and_invoke(call, tool_name, args)
        # If the tool needs approval, _guard_and_invoke returns a sentinel and we
        # do the gated path here so we can yield the permission request.
        if result_str is _NEEDS_APPROVAL:
            if self._unattended:
                # Unattended run with no auto-approve policy: no human will ever
                # answer, so don't surface a permission request and park on the
                # gate for 300s before it times out to reject — fail fast with the
                # same recoverable denial. (When the run carries an "auto"/"yolo"
                # policy, _requires_approval already returned False and we never
                # reach here.)
                _, result_str = security.classify_denial(
                    security.DENY_KIND_USER,
                    "this tool needs approval but the run is unattended (no human to "
                    "approve) — it was auto-declined",
                    tool_name,
                )
            else:
                request_id = call.tool_call_id or tool_name
                # Register the pending Future BEFORE surfacing the request, so an
                # approve/reject that arrives the instant the UI sees the prompt
                # (or synchronously, as in tests) is not lost to a race.
                fut = self._approval.register(request_id)
                yield AgentEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    request_id=request_id,
                    tool_call_id=call.tool_call_id,
                    title=tool_name,
                    tool_input=args,
                    # Declared risk of this tool (the gate resolves effective risk).
                    risk_level=self._tool_risk.get(tool_name, RiskLevel.SAFE).value,
                )
                decision = await self._approval.wait(request_id, fut)
                if self._cancelled:
                    result_str = "Error: cancelled"
                elif decision == REJECT:
                    # Recoverable: feed back WHY + adapt-don't-repeat guidance so
                    # the model doesn't silently stall on an unattended surface.
                    _, result_str = security.classify_denial(
                        security.DENY_KIND_USER, "the user declined this tool call", tool_name
                    )
                else:
                    result_str = await self._invoke(tool_name, args)

        # Record the outcome and apply graduated breaker verdicts. A failure is a
        # result the model reads as an error; a success clears this key's streak.
        failed = result_str.startswith("Error:")
        # Procedural-memory signal (M5d): accumulate this turn's tool outcomes for
        # the after-turn review to mine into how-to-work priors. Bounded.
        if len(self._tool_outcomes) < 200:
            self._tool_outcomes.append((tool_name, failed))
        streak = self._breaker.record(_bkey, failed)
        if failed and streak >= _BREAKER_WARN:
            result_str += (
                f"\n[note: this is failure #{streak} of `{tool_name}` with these "
                "arguments this run — stop repeating it and change approach.]"
            )
        elif not failed:
            # Structural loop detection (E3.1): a *successful* call going nowhere —
            # the same (tool, params, result) repeated, or A↔B ping-pong — never
            # trips the failure path (nothing failed). Warn-only: inject an
            # observation so the model breaks the loop itself; the failure breaker
            # still hard-blocks genuine error storms.
            sig = f"{_bkey}\x1f{_result_digest(result_str)}"
            loop_reason = self._breaker.record_structural(sig)
            if loop_reason:
                logger.info("native: structural loop detected (%s) — %s", tool_name, loop_reason)
                result_str += (
                    f"\n[note: {loop_reason}. You appear to be looping without "
                    "making progress — stop repeating this and change approach, or "
                    "stop and report what's blocking you.]"
                )

        yield AgentEvent(
            kind=EVENT_TOOL_RESULT,
            tool_call_id=call.tool_call_id,
            title=tool_name,
            tool_output=result_str,
            tool_meta=self._last_result_meta or {},
        )
        self._last_result_meta = {}  # consumed; reset for the next dispatch
        self._messages.append(self._tool_result_msg(call, result_str))

        # Run-wide circuit breaker: a turn drowning in failures (across all tools)
        # is pathological — abort it rather than burn the whole budget.
        if self._breaker.total_failures > _BREAKER_CIRCUIT:
            logger.warning(
                "native: circuit-breaking run after %d tool failures",
                self._breaker.total_failures,
            )
            self._cancelled = True

    async def _guard_and_invoke(self, call: AgentEvent, tool_name: str, args: dict):
        """Deny-list + PreToolUse hook; return a result string, or the
        ``_NEEDS_APPROVAL`` sentinel when the caller must run the gated path."""
        from personalclaw import security

        # Dry-run observe-mode (T9): a write-capable (non-SAFE) tool is NOT
        # executed — return a synthetic observation so the replay previews what
        # WOULD happen with no side effects. Read-only SAFE tools fall through and
        # run for real, so the agent reasons over actual state.
        if self._dry_run and self._tool_risk.get(tool_name, RiskLevel.SAFE) != RiskLevel.SAFE:
            return (
                f"[DRY RUN — observe mode] `{tool_name}` is a write-capable tool; "
                f"it was NOT executed. With args {_short_json(args)} it would have "
                "run here. Continue reasoning about what this run would do; do not "
                "retry it expecting a real effect."
            )

        # Hard deny-list (never prompts) — terminal, no retry invitation.
        deny = security.is_denied(tool_name, self._extra_deny)
        if deny:
            _, observation = security.classify_denial(security.DENY_KIND_POLICY, deny, tool_name)
            return observation

        # Task-mode gate (ask/plan/build) — runs HERE, before approval, so a
        # Trust/YOLO auto-approve can't slip a mutation past a read-only posture.
        # Recoverable denial: tells the model why + that the user can switch modes,
        # so it stops retrying and surfaces the SWITCH_TO_AGENT affordance instead.
        from personalclaw.task_modes import task_mode_denies

        tm_deny = task_mode_denies(self._task_mode, tool_name, "", call.tool_input)
        if tm_deny:
            _, observation = security.classify_denial(security.DENY_KIND_POLICY, tm_deny, tool_name)
            return observation

        # PreToolUse hooks (blocking) — recoverable: adapt, don't repeat.
        if self._hook_fire is not None:
            try:
                injected = await self._hook_fire(tool_name, _short_json(args))
            except Exception:  # noqa: BLE001
                injected = []
            blocked = [s for s in (injected or []) if str(s).startswith("BLOCKED:")]
            if blocked:
                _reason = blocked[0].removeprefix("BLOCKED:").strip() or "policy hook"
                _, observation = security.classify_denial(
                    security.DENY_KIND_HOOK, _reason, tool_name
                )
                return observation

        if self._requires_approval(tool_name):
            return _NEEDS_APPROVAL
        return await self._invoke(tool_name, args)

    def _resolve_name(self, name: str) -> str:
        """Map an incoming tool name to a real tool id, healing a provider's
        failed reverse-map. Exact match is ALWAYS the primary path: only when
        ``name`` is neither a real tool nor a runtime meta-tool do we fall back
        to the sanitized(real)->real map (unique names only). Any miss returns
        ``name`` unchanged so the existing "unknown tool" error still fires."""
        if name in self._tool_index or name in self._META_TOOLS:
            return name
        return self._tool_sanitized_index.get(name, name)

    async def _invoke(self, tool_name: str, args: dict) -> str:
        # tool_search (TR escape hatch): the retriever owns the full catalog, so
        # the runtime answers this directly rather than a provider. Lets the agent
        # discover any tool retrieval didn't surface this turn.
        if tool_name == "tool_search" and self._tool_retriever is not None:
            self._tool_retriever.mark_used("tool_search")
            hits = self._tool_retriever.search(
                str(args.get("query", "")), int(args.get("limit", 20) or 20)
            )
            if not hits:
                return "No tools matched. Try broader terms; all tools remain callable by name."
            # tool_search deliberately ranks the FULL catalog — including tools in
            # INACTIVE groups — and names the activation step for those, so search
            # is the discovery path INTO a group (§5.3 fail-open triad).
            lines = []
            for h in hits:
                suffix = ""
                if self._active_groups is not None:
                    grp = self._group_of_name.get(h["name"], "")
                    if grp and grp not in self._active_groups:
                        suffix = (
                            f" [in INACTIVE group '{grp}' — still callable by name, or "
                            f'reset_tools({{"{grp}": true}}) to load its schemas]'
                        )
                lines.append(f"- {h['name']}: {h['description']}{suffix}")
            return (
                "Matching tools (call tool_schema(name) for inputs, or call by name):\n"
                + "\n".join(lines)
            )
        # tool_schema (progressive disclosure): expand ONE catalog tool to its full
        # input schema so the model can call it correctly. Reads the def straight
        # from the catalog (not the per-turn surfaced set), so any tool resolves.
        if tool_name == "tool_schema":
            import json as _json

            want = str(args.get("tool_name", "")).strip()
            d = next((t for t in self._tool_defs if getattr(t, "name", "") == want), None)
            if d is None:
                return (
                    f"No tool named {want!r}. Use tool_search(query) to find the right name "
                    "(names are case-sensitive and exact)."
                )
            return _json.dumps(
                {
                    "name": d.name,
                    "description": getattr(d, "description", "") or "",
                    "parameters": getattr(d, "parameters", {})
                    or {"type": "object", "properties": {}},
                    "provider": getattr(d, "provider", ""),
                    "requires_approval": getattr(d, "requires_approval", True),
                },
                indent=2,
            )
        # reset_tools (group activation): answered by the runtime — it changes the
        # schema block, not any external state, so no provider and no gate.
        if tool_name == "reset_tools":
            return self._reset_tools(args)
        prov = self._tool_index.get(tool_name)
        if prov is None:
            return f"Error: unknown tool {tool_name!r}"
        # Sticky set (TR2): a tool the agent actually called stays surfaced for the
        # rest of the session, so a multi-step task can't lose a tool mid-task when
        # the query phrasing drifts. Cheap insurance against the cardinal failure.
        if self._tool_retriever is not None:
            self._tool_retriever.mark_used(tool_name)
        # Bind this turn's session key for in-process tools (e.g. subagent_run) so a
        # subagent spawned here resolves THIS session as its parent and inherits
        # its trust/auto-approve. The native loop runs inside the gateway with no
        # per-turn env var, so without this the spawn resolves a stale PID file
        # and the subagent's tool calls escalate to interactive approval —
        # breaking unattended goal loops.
        from personalclaw import mcp_core
        from personalclaw.agents.native import builtin_tools as _bt

        token = mcp_core.set_current_session_key(self._session_key)
        # Also publish the resolved agent id so workflow_create can auto-bind an
        # agent-scoped SOP to THIS agent (EVOLVE-WORKFLOWS, #28).
        agent_token = mcp_core.set_current_agent_id(self._agent_id)
        # Bind this turn's workspace for the native category providers (UT1): the
        # session-coupled app providers (knowledge/tasks/loops/inbox) are registry
        # singletons now, so cwd/agent flow via contextvars rather than a per-session
        # constructor. (The platform filesystem/shell provider is still built
        # per-session in provider_bridge with cwd+extra_roots, so its own confinement
        # is unaffected; this binding makes the singletons resolve THIS session too.)
        ctx_tokens = _bt.bind_tool_context(
            cwd=self._cwd, agent=self._agent_id, project_id=self._project_id
        )
        try:
            result = await prov.invoke(tool_name, args)
        finally:
            mcp_core.reset_current_session_key(token)
            mcp_core.reset_current_agent_id(agent_token)
            _bt.reset_tool_context(ctx_tokens)
        # Capture the result's typed metadata (content_type / raw_ref / truncated)
        # for the TOOL_RESULT event — the string return loses it otherwise. Single
        # slot, read+cleared at the emit site keyed to this dispatch.
        meta = dict(getattr(result, "metadata", {}) or {})
        if getattr(result, "truncated", False):
            meta["truncated"] = True
            if getattr(result, "original_length", None) is not None:
                meta["original_length"] = result.original_length
        # TC5: carry recovery_hints (concrete next-steps on failure) so the tool card
        # can surface them — the contract has them, they were dropped at the WS boundary.
        # Also carry the success flag so the card can color-code a failed call (a
        # green "done" check on a failed tool is misleading). Only stamp on FAILURE —
        # absence means success, so existing/ACP results render exactly as before.
        if not getattr(result, "success", True):
            meta["ok"] = False
            hints = getattr(result, "recovery_hints", None)
            if hints:
                meta["recovery_hints"] = list(hints)
            # PLATFORM-LEGIBILITY §2: carry the WHAT/WHY/FIX envelope structurally so
            # the tool card can render coded rows + did-you-mean suggestions (the
            # string form already went to the model via format_tool_result).
            agent_error = getattr(result, "agent_error", None)
            if agent_error is not None:
                meta["agent_error"] = agent_error.to_dict()
        self._last_result_meta = meta
        return format_tool_result(result)

    # Synthetic runtime meta-tools (not in _tool_defs): pure, side-effect-free
    # discovery answered by the runtime itself → never gated, never dispatched to a
    # provider. Without this they fall through to the `return True` default and the
    # loop parks on the approval gate forever.
    _META_TOOLS = frozenset({"tool_search", "tool_schema", "reset_tools"})

    def _requires_approval(self, tool_name: str) -> bool:
        if tool_name in self._META_TOOLS:
            return False
        if self._approval_policy in ("auto", "yolo", "acceptEdits"):
            return False
        for t in self._tool_defs:
            if t.name == tool_name:
                return bool(getattr(t, "requires_approval", True))
        return True

    # ── message shaping (OpenAI wire format; Anthropic provider re-maps) ──
    # Compact the native loop's history when context crosses this fraction of
    # the model's window (provider-reported context_usage_pct).
    _COMPACT_THRESHOLD_PCT = 70.0

    def _maybe_compact(self) -> None:
        """Run structured compaction on ``self._messages`` if over the threshold.

        Trigger = provider-reported context usage ≥ threshold. Anti-thrashing
        skips it when the last two passes each reclaimed <10%. Uses the no-LLM
        path (tool-output pruning pre-pass + structured digest) — cheap, safe,
        and synchronous; an LLM-summarized middle can layer on later. Records the
        save fraction for the anti-thrashing guard.
        """
        if self._last_context_pct < self._COMPACT_THRESHOLD_PCT:
            return
        from personalclaw import context_compaction as cc

        if not cc.should_compact(self._compaction_saves):
            return
        before = cc.total_chars(self._messages)
        if before <= 0:
            return
        compacted = cc.compact(self._messages)
        after = cc.total_chars(compacted)
        saved = (before - after) / before if before else 0.0
        if after < before:
            self._messages = compacted
            # Compaction rewrote history → any cached prompt prefix is now stale. Bump
            # the generation so an EXPLICIT-cache provider's next marker reads fresh.
            self._cache_generation += 1
            logger.debug("native: cache prefix invalidated → generation %d", self._cache_generation)
            # A compaction shrank context; the next provider turn re-measures, so
            # reset our gauge optimistically to avoid re-triggering immediately.
            self._last_context_pct = self._last_context_pct * (after / before)
            # Post-compaction guard (E3.1): re-arm structural detection so a loop
            # that resumes identically after the history was compacted is caught
            # fresh, instead of its pre-compaction signatures aging out silently.
            self._breaker.reset_structural()
            logger.info(
                "native: compacted context %d→%d chars (saved %.0f%%)",
                before,
                after,
                saved * 100,
            )
        self._compaction_saves.append(saved)

    @staticmethod
    def _assistant_msg(text: str, tool_calls: list[AgentEvent]) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": text or ""}
        if tool_calls:
            tc_list = []
            for c in tool_calls:
                tc_entry: dict[str, Any] = {
                    "id": c.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": c.title,
                        "arguments": (
                            c.tool_input
                            if isinstance(c.tool_input, str)
                            else _short_json(c.tool_input)
                        ),
                    },
                }
                # Gemini 3.x requires thought_signature echoed back on tool-call
                # turns in history; it arrives via tool_meta["extra_content"] from
                # the streaming response. Preserve it so the next API call doesn't
                # get rejected with "Function call is missing a thought_signature".
                extra = (c.tool_meta or {}).get("extra_content")
                if extra:
                    tc_entry["extra_content"] = extra
                tc_list.append(tc_entry)
            msg["tool_calls"] = tc_list
        return msg

    @staticmethod
    def _tool_result_msg(call: AgentEvent, result_str: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": call.tool_call_id,
            "content": result_str,
        }

    # ── permissions surface (chat runner calls these on the session provider) ──
    async def approve_tool(self, request_id: str | int) -> None:
        self._approval.approve(str(request_id))

    async def reject_tool(self, request_id: str | int) -> None:
        self._approval.reject(str(request_id))

    # ── status / control ──
    def context_usage_pct(self) -> float:
        return self._last_context_pct

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        self._cancelled = True
        self._approval.cancel_all()
        return "acked"

    def is_alive(self) -> bool:
        return True

    def drain_tool_outcomes(self) -> list[tuple[str, bool]]:
        """Return this run's accumulated (tool, failed) outcomes and clear them.

        The after-turn review drains this into procedural memory (M5d). Draining
        (not just reading) keeps the accumulator bounded across turns."""
        out = list(self._tool_outcomes)
        self._tool_outcomes.clear()
        return out

    def set_workspace(self, path: Path) -> None:
        self._cwd = Path(path)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self._session_key = session_key

    def set_steer_source(self, pull: "Callable[[], list[str]] | None") -> None:
        """Wire the queue-steering source (#37): a callable the loop drains at each
        model boundary for mid-turn user messages. None disables steering."""
        self._pull_steer = pull

    def _drain_steers_into_history(self) -> bool:
        """Append any pending steers to history as fresh user input.

        Returns True if at least one steer was appended, which tells the loop to run
        another inference so the steer affects the answer already in progress.

        Called at BOTH model boundaries — after a tool batch, and before a no-tool-call
        turn would end. The second call site is the one that matters in practice: most
        turns are plain prose and never execute a tool, and with the drain only after
        the tool batch those steers were dropped after the API had already reported
        success.

        Capped by ``_MAX_STEERS_PER_TURN`` so a flood cannot extend one turn forever;
        once the cap is hit this returns False and the turn ends normally, leaving any
        remainder for the session's turn-end clear.
        """
        if self._pull_steer is None or self._steers_injected >= _MAX_STEERS_PER_TURN:
            return False
        try:
            steers = self._pull_steer()
        except Exception:
            logger.debug("steer drain failed", exc_info=True)
            return False
        appended = False
        for s in steers:
            if self._steers_injected >= _MAX_STEERS_PER_TURN:
                break
            self._messages.append(
                {
                    "role": "user",
                    "content": f"[Steering — the user added this mid-task]\n{s}",
                }
            )
            self._steers_injected += 1
            appended = True
        return appended

    def set_approval_policy(self, policy: str) -> None:
        self._approval_policy = policy or ""

    def set_task_mode(self, mode: str) -> None:
        """Set the task mode (agent/ask/plan/build) enforced in _guard_and_invoke."""
        self._task_mode = mode or "agent"

    @property
    def agent_model(self) -> str:
        return self._definition.model or getattr(self._model, "_model", "") or ""

    @property
    def agent_name(self) -> str:
        return self._definition.name or ""


def _n_tools(n: int) -> str:
    return f"{n} tool" if n == 1 else f"{n} tools"


def _short_json(value: Any) -> str:
    import json

    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
