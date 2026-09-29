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

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw import cancellation
from personalclaw.acp.types import STOP_REASON_CANCELLED, STOP_REASON_STOPPED_BY_USER
from personalclaw.agents.native import dispatch_plan
from personalclaw.agents.native.approval import REJECT, ApprovalGate
from personalclaw.agents.native.compaction import InProcessCompaction, compaction_summary
from personalclaw.agents.native.failover import FAILOVER_MODES
from personalclaw.agents.native.tools import (
    ARGUMENTS_UNREADABLE,
    format_tool_result,
    read_tool_arguments,
    tool_definitions_to_openai_schema,
)
from personalclaw.agents.provider import AgentProvider
from personalclaw.cancellation import (
    CANCEL_INTERNAL,
    CANCEL_USER,
    REQUEST_NO_TURN,
    REQUEST_REPEAT,
    CancelScope,
)
from personalclaw.guardrails.audit import AttemptRecord, now_ms, record_attempt
from personalclaw.guardrails.failure import (
    FailureMode,
    FirstTokenTimeout,
    GuardError,
    NoModelAnswered,
    correction_note,
    is_retryable,
)
from personalclaw.guardrails.loop_breaker import (
    WARN_THRESHOLD,
    LoopBreaker,
    only_reads,
    params_key,
    result_digest,
    structural_note,
    warn_note,
)
from personalclaw.llm.events import (
    COMPACTION_AUTOMATIC,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_MODEL_SUBSTITUTION,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    STOP_MAX_TOKENS,
    TOOL_META_APPROVAL_WAIVED,
    TOOL_META_AUTO_DENIED,
    TOOL_META_NOT_RUN,
    TOOL_META_REFUSED_BY,
    AgentEvent,
    is_length_stop,
)
from personalclaw.llm.prompt_cache import (
    PromptCache,
    effective_cache_mode,
    mark_cacheable_prefix,
)
from personalclaw.tool_providers.base import RiskLevel, only_tells_the_owner
from personalclaw.tool_providers.portable_schema import (
    ToolSchemaRejected,
    tools_named_in_rejection,
)
from personalclaw.workflows.compaction import is_context_overflow

if TYPE_CHECKING:
    from personalclaw.agents.native.failover import ModelFailover
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.llm.base import ModelProvider, ModelSubstitution
    from personalclaw.providers.provider_bridge import ResolutionBasis
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


# Brief pause before the one loop-level inference retry (#2287/#252) — long enough
# to step over a same-instant transient, short enough that an interactive turn
# doesn't visibly stall. Deliberately a constant: config/loader.py is at its line
# budget, and a knob nobody tunes is a cost with no consumer.
_INFERENCE_RETRY_BACKOFF_SECS = 0.5


def _inference_failure_mode(exc: BaseException) -> FailureMode:
    """Classify a raised inference exception into the guard's taxonomy.

    Typed guard errors carry their mode; a bare timeout maps to ``TIMEOUT``; every
    vendor SDK exception collapses to ``PROVIDER_ERROR`` — same collapse rule as
    :mod:`personalclaw.guardrails.failure` documents for the guard itself, so the
    loop's audit rows and the guard's stay foldable in one taxonomy.
    """
    if isinstance(exc, GuardError):
        return exc.mode
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return FailureMode.TIMEOUT
    if isinstance(exc, MemoryError):
        # An allocation that just failed is not a transient: the identical prompt asks for the
        # identical memory. Measured on the bundled model, the blind retry re-ran a
        # (9, 27862, 27862) float32 allocation — and on a memory-capped host the second attempt
        # is the one the kernel kills the whole gateway for.
        return FailureMode.PROMPT_TOO_LARGE
    return FailureMode.PROVIDER_ERROR


def _sanitized_tool_key(name: str) -> str:
    """Return the common model-safe form of ``name`` (illegal chars -> ``_``,
    capped at 64). Mirrors the constraint providers like Bedrock apply so the
    runtime can recognize a rewritten name and map it back to the real tool."""
    safe = _TOOL_NAME_SANITIZE_RE.sub("_", name or "")[:_TOOL_NAME_SANITIZE_MAX]
    return safe or "tool"


def build_sanitized_index(names: Iterable[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The sanitized(real)->real healing map for a tool census, plus its losses.

    For each real name a provider WOULD have to rewrite, remember the rewritten
    form -> real name, but ONLY when that sanitized form is unique across the
    census (an ambiguous collision is dropped so we never dispatch the wrong
    tool) and only when it differs from the real name (an already-legal name
    needs no fallback). A key that would shadow a REAL exact name is likewise
    never remapped.

    Returns ``(healing_map, collisions)`` where ``collisions`` maps each dropped
    sanitized key to the real names that fought over it — the ONE lossy spot in
    the whole name wire (see docs/architecture/tool-name-wire.md), surfaced so a
    caller can report it instead of losing tools silently. SM-12's census rail
    (tests/test_tool_name_wire_fidelity.py) keeps the live census collision-free,
    which is what makes every transform on the wire reversible in practice.
    """
    census = set(names)
    healing: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for real in census:
        key = _sanitized_tool_key(real)
        if key == real or key in census:
            # Legal already, or would shadow a real exact name — never remap.
            continue
        if key in collisions:
            collisions[key].append(real)
            continue
        if key in healing and healing[key] != real:
            collisions[key] = [healing.pop(key), real]
            continue
        healing[key] = real
    return healing, {k: sorted(v) for k, v in collisions.items()}


# Hook fire callback: (event_title, tool_input) -> awaitable[list[str]] of
# "BLOCKED:..." strings (non-empty ⇒ blocked). Mirrors chat_runner's fire shape.
HookFire = Callable[[str, str | None], Awaitable[list[str]]]

# Cap mid-turn steer injections (#37) so a message flood can't extend one turn
# forever — past this, further steers wait for the next turn.
_MAX_STEERS_PER_TURN = 4

# Sentinel: the tool passed deny-list + hooks but needs interactive approval,
# so the generator path (_run_tool) must run the gated branch.
_NEEDS_APPROVAL: Any = object()

#: The observation a queued-but-unstarted call is answered with when a stop reaches it
#: before it was dispatched. One spelling, because two exits pair a dropped call
#: with a result — the pre-batch exit in :meth:`NativeAgentRuntime.stream` and the per-call
#: drop in :meth:`NativeAgentRuntime._execute_wave` — and a model reading the history back
#: must not see two different accounts of the same thing.
CANCELLED_BEFORE_RUN = "Error: cancelled before this tool ran"

#: A tool result's error state, the one bit the tool card, the loop breaker and procedural memory
#: all read: ``tool_meta["ok"]`` is present and False only on failure, absent on success (the
#: contract ``acp/outcomes.py`` states for both seams). Merged into a dispatch's meta wherever this
#: class writes a failure; ``_invoke`` sets it from a provider's own ``success``.
_FAILED: dict[str, Any] = {"ok": False}

# Sentinel: this call was dropped by a stop, so it must not be dispatched and must not
# reach _run_tool's accounting tail. A unique object rather than a string or None, both of
# which a legitimate result already occupies in the wave's `results` list.
_DROPPED: Any = object()


@dataclass(frozen=True, slots=True)
class _PreparedCall:
    """A requested tool call resolved far enough to be PLANNED but not yet run (HC-6).

    Exists because the concurrency decision needs every call's resource set before any
    of them executes, and the name/argument resolution that produces it must therefore
    happen once, up front, and be reused by whichever path ends up running the call.
    """

    call: AgentEvent
    tool_name: str
    args: dict
    card: AgentEvent
    reservations: tuple[dispatch_plan.Reservation, ...]
    bkey: str
    #: Set when the model's argument string could not be read at all. The call is then ANSWERED
    #: with this text instead of invoked — naming the real defect (a truncated response, or
    #: malformed JSON) rather than letting the tool report a missing argument (issue 1773).
    arg_error: str = ""
    #: Whether the call only reads (``loop_breaker.only_reads``): the one kind of call the loop
    #: breaker counts and refuses for repeating the same answer.
    reads: bool = False


# Graduated failure/loop thresholds + the standard notices live in the
# runtime-agnostic observer imported above (ACP-AGENT-PARITY §2.3 gap 5): the ACP
# host consumes the SAME counting over its neutral event stream, so a threshold or
# a notice's wording is defined once and cannot drift between the two runtimes.


class NativeAgentRuntime(InProcessCompaction, AgentProvider):
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
        max_tool_concurrency: int = dispatch_plan.MAX_CONCURRENT_CALLS,
        leaf_lineage: Mapping[str, str] | None = None,
    ) -> None:
        self._definition = definition
        self._model = model_provider
        # The workflow leaf this session is, when it is one: its run, depth and posture
        # (`mcp_shared.LEAF_KEYS`). An agent CLI's tool server gets them as its environment; this
        # runtime's tools run in the gateway process, so `_invoke` binds them for each call
        # instead — which is what makes the leaf's depth limit, its posture and
        # `resume_run_id: "self"` hold here as they do there. Empty for a session that is no leaf.
        from personalclaw.mcp_shared import leaf_lineage as _leaf_lineage

        self._leaf_lineage = _leaf_lineage(leaf_lineage)
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
        # (tool, outcome) the after-turn review drains into procedural memory, where
        # `outcome` is one of `memory_service.PROCEDURAL_OUTCOMES`. Capped so a long
        # run can't grow it unbounded.
        #
        # An OUTCOME rather than a `failed` bool: a denial and a failure
        # are both `Error: …` to the model but different priors — labelling the
        # user's refusal "failed" is what taught the failure-synthesis pass to
        # publish "this tool is unreliable — prefer an alternative" about a tool
        # that works fine and is merely not allowed here.
        self._tool_outcomes: list[tuple[str, str]] = []
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
        # Set by the builder (``provider_bridge._build_native_runtime``) when this runtime serves in
        # place of a model someone chose — the agent's pin or the chat's own pick could not run —
        # so the chat and a room can say which model answered instead of the one that was chosen.
        self.model_substitution: ModelSubstitution | None = None
        # The models a turn may fall back to when its model fails before any output — set by the
        # builder for an interactive runtime (``agents/native/failover.py``). Used only on a turn
        # whose caller called :meth:`announce_failover`, since a caller that does not show
        # EVENT_MODEL_SUBSTITUTION would present the fallback's reply as the chosen model's.
        self.failover: ModelFailover | None = None
        # What the builder resolved this runtime's model from (the Settings → Models chains it
        # read, and the provider entry it serves from). ``SessionManager`` rebuilds a cached
        # runtime whose basis no longer holds, so a rebind or an instance edit reaches the next
        # turn of every open session instead of waiting for an idle hour or a restart.
        self.resolved_from: ResolutionBasis | None = None
        self._failover_announced = False
        # Per turn: the models still to try, the (ref, why) of each one that failed, and the
        # sentence the fallback that answers says before its reply.
        self._failover_queue: list[str] = []
        self._turn_failures: list[tuple[str, str]] = []
        self._pending_substitution = ""
        self._extra_deny = list(extra_deny_patterns or [])

        # Conversation history — owned by the loop (complete() is stateless).
        self._messages: list[dict] = []
        # Images staged for the NEXT turn (`stage_image_part`), and the ones riding the turn
        # in flight with the user message they belong to. Pixels never enter `_messages`:
        # they are laid onto each inference's REQUEST copy (`_request_messages`), so every
        # model call of the turn sees them, compaction and the char backstop never count
        # base64, and a later turn is never handed an earlier turn's image.
        self._staged_images: list[str] = []
        self._turn_images: list[str] = []
        self._turn_message: dict | None = None
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
        # The characters of full tool schemas this turn's window affords (`_tool_schema_budget`),
        # or None for no bound. Set at the start of every turn.
        self._schema_budget: int | None = None
        self._tool_search_def: Any = None  # synthetic escape-hatch def (built in start())
        self._tool_schema_def: Any = None  # synthetic schema-expander def (built in start())
        self._reset_tools_def: Any = None  # synthetic group meta-tool (built in start())
        # ── tool groups ──
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
        # Groups whose declared capability doesn't resolve: not activatable,
        # not stub-listed. Recomputed on every schema assembly.
        self._unofferable: set[str] = set()
        # Ceiling on one wave's concurrent dispatch. 1 = the pre-HC-6 behaviour,
        # every call in its own wave; it is what the dispatch benchmark's baseline arm uses.
        self._max_tool_concurrency = max(1, int(max_tool_concurrency or 1))
        self._approval = ApprovalGate()
        # Approval policy: "" / "default" prompt; "auto"/"yolo" auto-approve. A live SOURCE, when
        # set, is read at each decision instead (`set_approval_source`).
        self._approval_policy = ""
        self._approval_source: Callable[[], str] | None = None
        # Task mode (agent/ask/plan/build) — ORTHOGONAL to approval. Gates WHICH
        # tools may run, enforced in _guard_and_invoke before approval is consulted
        # so a Trust/YOLO auto-approve can never bypass an ask/plan/build restriction.
        self._task_mode = "agent"
        # The host's own tool grants (`set_tool_grants`): which tools this run may use at all,
        # asked in the same place and for the same reason as the task mode.
        self._tool_grants: Callable[..., str] | None = None
        # tool name → what the tool DECLARES a call does (its RiskLevel; SAFE is the read-only
        # declaration), and the tools that declare they build a Build-mode deliverable or only
        # file a proposal, and the arguments a call to a tool may carry and still only tell the
        # owner something. Built in start(); a name missing from the map declares nothing, which
        # is CAUTION (`_declared`).
        self._tool_risk: dict[str, RiskLevel] = {}
        self._tool_builds: frozenset[str] = frozenset()
        self._tool_proposes: frozenset[str] = frozenset()
        self._tool_tells_owner: dict[str, tuple[str, ...]] = {}
        # The turn's ONE stop signal. Not a bool: cancellation has to carry a
        # cause (user vs internal), a live child-process registry a stop can reap, and
        # idempotence — so it is an object, and `self._cancelled` below is a read-only
        # view of it rather than a second place the truth can live.
        self._cancel = CancelScope()
        # ``None`` = no provider usage report seen yet. NOT 0.0: an unmeasured
        # context and a measured-empty one are different answers, and only one of
        # them may be rendered as a number (llm/base.context_usage_pct contract).
        self._last_context_pct: float | None = None
        # Per-run consecutive-failure breaker (reset each stream() turn).
        self._breaker = LoopBreaker()
        # Why the breaker ended this turn, when it did — carried on the turn's terminal event
        # (``text``) so the surface can say it. "" for every other ending.
        self._stop_note = ""
        # Compaction save fractions (anti-thrashing across the session).
        self._compaction_saves: list[float] = []
        # Queue-steering (#37): a callback the loop drains at each model boundary
        # for mid-turn user messages. None = no steering (the default until wired).
        self._pull_steer: "Callable[[], list[str]] | None" = None
        self._steers_injected = 0
        # Text PULLED from the session's buffer but not yet appended to history — the
        # per-turn cap's overflow. It has to live somewhere: the pull EMPTIES the session
        # deque, so a cap hit that simply stopped appending discarded steers this runtime
        # had already removed from the only place holding them, while the HTTP caller was
        # told ``{"steered": true}``. Whatever is left is named at turn end by
        # :meth:`undelivered_steers` — the same seam the ACP session exposes.
        self._steer_pending: list[str] = []
        # Prompt-cache prefix generation. Bumped whenever the
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
        from personalclaw.tool_providers.portable_schema import offered_tool_definitions
        from personalclaw.tool_providers.registry import app_of, serve

        disabled_keys = tool_prefs.load_disabled()
        disabled_provs = tool_prefs.load_disabled_providers()
        defs: list[Any] = []
        index: dict[str, ToolProvider] = {}
        provider_of: dict[str, str] = {}  # tool name → resolved provider key (grouping)
        dropped: list[str] = []
        for prov in self._tool_providers:
            if (getattr(prov, "name", "") or "") in disabled_provs:
                logger.info(
                    "native: provider %r is user-disabled — skipping its toolset", prov.name
                )
        # ONE NAME, ONE PROVIDER: the surface is read through the registry's rule, so a name maps
        # to the provider that serves it, never to whichever advertised it last (a registered app
        # that offered `bash` used to receive the agent's `bash` calls). A broken provider must not
        # kill start: its failure is logged and the rest still serve.
        served, failures = await serve(self._tool_providers, skip=disabled_provs)
        for prov, _exc in failures:
            logger.debug("native: tool provider %s list failed", prov.name, exc_info=_exc)
        for prov, tools in served:
            prov_name = getattr(prov, "name", "") or ""
            enabled = []
            for t in tools:
                # Prefer the tool's own provider tag; fall back to the provider
                # instance name (matches how GET /api/tools keys the disable set).
                pkey = getattr(t, "provider", "") or prov_name
                if tool_prefs.is_disabled(pkey, t.name, disabled_keys, disabled_provs):
                    dropped.append(t.name)
                    continue
                enabled.append(t)
            # THE TOOL SEAM: a provider validates the whole tool block, so one schema it
            # cannot accept fails every turn. Every tool — built-in or app — is brought inside
            # the portable profile here, where the request is assembled; one that cannot be
            # repaired stays out of the schema AND the index, with one log line naming it.
            for t in offered_tool_definitions(enabled, provider=prov_name, app=app_of(prov_name)):
                pkey = getattr(t, "provider", "") or prov_name
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
        # GROUP PARTITION — derived from the assembled catalog, so it
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
        # Sanitized-name fallback (provider-agnostic reverse-map insurance) — see
        # build_sanitized_index for the policy and the wire map
        # (docs/architecture/tool-name-wire.md) for the end-to-end picture. Built
        # once here so dispatch stays a dict lookup; consulted by _resolve_name.
        sanitized, collisions = build_sanitized_index(index)
        for key, reals in collisions.items():
            # The one lossy spot on the name wire, and it must never be silent:
            # these tools stay callable by their EXACT names, but a provider
            # rewrite of any of them cannot be healed. The census rail test
            # keeps shipped tool names out of this branch.
            logger.warning(
                "native: tool names %s collide under the model-safe form %r — "
                "a provider-rewritten call to it cannot be healed; rename one",
                reals,
                key,
            )
        self._tool_sanitized_index = sanitized
        # Risk-level map: dry-run observe-mode intercepts non-SAFE tools (T9), and
        # the permission-request event carries a tool's declared risk to the gate.
        # Built once here so the hot path is a dict lookup.
        self._tool_risk = {t.name: getattr(t, "risk_level", RiskLevel.CAUTION) for t in defs}
        self._tool_builds = frozenset(t.name for t in defs if getattr(t, "builds", False))
        self._tool_proposes = frozenset(t.name for t in defs if getattr(t, "proposes", False))
        self._tool_tells_owner = {t.name: tuple(getattr(t, "tells_owner", ()) or ()) for t in defs}
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
            risk_level=RiskLevel.SAFE,
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
            risk_level=RiskLevel.SAFE,
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
        # The group meta-tool — final-state semantics, core group, SAFE: it
        # changes what the model SEES, not what it can do. Only ever surfaced on a
        # session where grouping is actually in effect (see _assemble_schema).
        self._reset_tools_def = _TD(
            name="reset_tools",
            provider="native",
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
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
                        # One declared boolean per group this session HAS. An open map
                        # (`additionalProperties`) has no portable schema — a strict provider
                        # rejects the whole request over it (tool_providers.portable_schema).
                        "properties": {
                            g.name: {"type": "boolean", "description": f"{_n_tools(len(g.tools))}"}
                            for g in self._groups
                        },
                    }
                },
                "required": ["groups"],
            },
        )
        # The meta-tools are answered by this runtime and declare they only read: discovery, and
        # which groups' schemas the model is shown — nothing a call can change.
        for meta in (self._tool_search_def, self._tool_schema_def, self._reset_tools_def):
            self._tool_risk[meta.name] = meta.risk_level
        logger.info(
            "native: discovered %d tools across %d providers", len(defs), len(self._tool_providers)
        )

    # ── tool groups ──
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
            # PER-CAPABILITY GATING: a group whose declared capability doesn't
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

    def _reset_tools(self, args: dict, *, meta_sink: dict | None = None) -> str:
        """Apply ``reset_tools`` — FINAL-STATE group activation (§5.2).

        One boolean per group; every non-``always_on`` group the caller omits (or
        sets false) deactivates. Final-state rather than delta semantics because
        deltas accumulate drift over a long session. Returns the new active set
        plus the instructions of each NEWLY activated group, so usage guidance
        arrives exactly when the tools do. A call it cannot apply marks *meta_sink*
        failed, the one bit the tool card reads.
        """
        raw = args.get("groups")
        if not isinstance(raw, dict):
            if meta_sink is not None:
                meta_sink.update(_FAILED)
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
        # silently re-reading a set that didn't change.
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

    @property
    def _cancelled(self) -> bool:
        """This turn's stop signal — a VIEW of :attr:`_cancel`, never its own state.

        Read-only on purpose. Every writer has to name a cause
        (``self._cancel.request(...)``), because a bare ``= True`` is how the tree
        ended up unable to tell a user's stop from a circuit-breaker trip.
        """
        return self._cancel.cancelled

    def _stop_reason_for_cancel(self) -> str:
        """The turn's terminal stop reason for a cancelled turn.

        ``stopped_by_user`` when the user pressed stop, ``cancelled`` when something
        internal gave up (breaker, watchdog, shutdown). Distinct values, one family —
        see :func:`personalclaw.acp.types.is_cancelled_stop`.
        """
        if self._cancel.stopped_by_user:
            return STOP_REASON_STOPPED_BY_USER
        return STOP_REASON_CANCELLED

    def _final_stop_reason(self, usage: AgentEvent | None) -> str:
        """How the turn's last inference ended, as the chat surface needs to know it.

        A cancel wins, then the provider's own LENGTH stop: a reply cut at the model's output
        cap ends mid-sentence, and before this every provider stop reason was replaced with
        ``end_turn`` here, so the chat runner could not tell a cut reply from a finished one.
        Only the length stop is carried — it is the one the user must be told about, and every
        other provider spelling of "finished" keeps meaning ``end_turn`` to every consumer.
        """
        if self._cancelled:
            return self._stop_reason_for_cancel()
        if usage is not None and is_length_stop(usage.stop_reason):
            return STOP_MAX_TOKENS
        return "end_turn"

    def _audit_inference_attempt(
        self,
        mode: FailureMode,
        *,
        attempt: int,
        started_ms: float,
        passed: bool,
        fallback: bool = False,
    ) -> None:
        """Record one loop-level inference attempt in the guard's audit shape.

        ``fallback`` marks an attempt on a model the turn fell back to: strategy ``fallback``, and
        ``degraded`` (a fallback ref served it), which is what the guard's own rows say for it.

        Only exceptional attempts are recorded — every failed attempt, plus the
        outcome of a retry — so the audit trail gains the retry story (#252's
        "no audit" gap) without re-baselining the stats fold with a row for every
        healthy native inference. Best-effort by contract: an audit failure must
        never take down the turn it describes.
        """
        try:
            record_attempt(
                AttemptRecord(
                    audit_id=f"native-{id(self):x}-{time.time_ns():x}",
                    ts=now_ms(),
                    use_case="native_loop",
                    provider=type(self._model).__name__,
                    model=self._definition.model or "",
                    attempt=attempt,
                    failure_mode=mode.value,
                    latency_ms=max(0.0, now_ms() - started_ms),
                    passed=passed,
                    strategy="fallback" if fallback else ("retry" if attempt > 1 else "direct"),
                    degraded=fallback and passed,
                    # This row prices nothing and adds nothing: a failed attempt cost nothing,
                    # and the call a passing one reports is counted where it was made (the
                    # guard's own row on a metered axis, the turn's usage row otherwise).
                    priced=True,
                )
            )
        except Exception:
            logger.debug("native: inference attempt audit failed", exc_info=True)

    def last_stop_report(self) -> dict:
        """What the last stop actually reached — the shape PR2-13 consumes.

        Keys: ``reason``, ``model_request_aborted``, ``children_reaped``,
        ``children_escaped``, ``tool_calls_dropped``, ``subagents_stopped``.
        Read by the dashboard stop handler so the stop card can state what happened
        instead of implying it.
        """
        return self._cancel.report.to_dict()

    def note_subagents_stopped(self, count: int) -> None:
        """Record how many spawned subagents a stop reached (see above).

        The SessionManager owns that sweep — it holds the subagent stopper — so it
        reports the count inward rather than the scope guessing at it.
        """
        self._cancel.note_subagents_stopped(count)

    async def shutdown(self) -> None:
        self._cancel.request(reason=CANCEL_INTERNAL)
        self._approval.cancel_all()

    def _prompt_cache_enabled(self) -> bool:
        """Read the user's prompt-cache switch, ``agent.prompt_cache_enabled`` (§C6).

        Deferred import so this package keeps the config-free import surface its module
        docstring promises (the same deferral ``sdlc_tools`` and the ACP concurrency gate
        use). Read per turn rather than cached at construction: the switch exists for
        diagnosis, and a diagnosis switch that needs a session restart is not much of a
        switch. An unreadable config reads as ENABLED — that is the field's default, so a
        config the loop cannot parse must not silently change what gets served.
        """
        from personalclaw.config.loader import AppConfig

        try:
            return bool(AppConfig.load().agent.prompt_cache_enabled)
        except Exception:
            logger.debug(
                "native: prompt-cache switch unreadable — treating as ENABLED", exc_info=True
            )
            return True

    # ── the turn ──
    async def stream(self, message: str) -> AsyncIterator[AgentEvent]:
        """Run the ReAct loop for one user turn (``message`` is the full,
        context-built turn-0 prompt the chat runner already assembled)."""
        self._cancel.begin_turn()
        self._breaker.reset()
        self._stop_note = ""
        self._steers_injected = 0
        # Cleared, not carried: the dispatcher reads `undelivered_steers()` at the END of
        # the previous turn and requeues what it finds, so replaying it here would deliver
        # a second copy of a steer the user can already see in the queue strip.
        self._steer_pending.clear()
        self._turn_images, self._staged_images = self._staged_images, []
        from personalclaw.session import chore_prompt  # a chore's prompt is stored text: masked

        message = chore_prompt(self._session_key, message)
        self._messages.append({"role": "user", "content": message})
        self._turn_message = self._messages[-1]

        self._schema_budget = await self._tool_schema_budget()
        tools_kwarg, turn_note = self._prepare_turn_tools(message)
        if turn_note:
            # SYSTEM role: this is runtime metadata, not something the user said.
            # Tagged VOLATILE (PCS-1 / F1): the turn_note carries the per-turn tool
            # catalog + group stubs, so its content CHANGES every turn. Prompt caches
            # match on an EXACT prefix, so a provider that hoists system content to the
            # head of the served prompt (Anthropic's out-of-band ``system=``) would put
            # this volatile string AHEAD of the stable assembled context and break the
            # cacheable prefix. The neutral ``_volatile`` marker tells such a provider to
            # deliver this note at the TAIL of the message list instead, so the stable
            # context leads. Providers that don't cache simply ignore the extra key.
            self._messages.append({"role": "system", "content": turn_note, "_volatile": True})
        agg_in = agg_out = 0
        # The prompt-cache halves of the served prompt, accumulated over the turn's
        # inferences exactly like ``agg_in`` beside them. They have to travel together:
        # every consumer of cache telemetry reconstructs the whole served prompt as
        # ``input + cache_creation + cache_read`` (``stats.py:160``, ``pricing.py:169``,
        # ``llm/openai.py:444``), so a terminal event carrying an accumulated
        # ``input_tokens`` next to a zeroed or last-inference-only cache count would
        # divide a whole-turn numerator by a single-inference denominator. Omitting them
        # is what made every ledger row read a STRUCTURAL zero — indistinguishable from
        # "caching is not working" — for every provider, not just Bedrock.
        agg_cache_read = agg_cache_creation = 0
        agg_cost = 0.0
        # The guarded calls those sums came from (``AgentEvent.audit_ids``): the turn's usage row
        # names them, so the model-call census does not count this turn's inferences again.
        agg_audit_ids: list[str] = []
        turns = 0
        # Turn telemetry (parity with ACP's last_prompt_stats): events observed
        # this prompt + total tool calls made. Surfaced on the terminal
        # EVENT_COMPLETE so the chat runner renders the "Turn complete" line.
        agg_events = 0
        agg_tool_calls = 0
        # ONE loop-level correction-retry per agent turn (#2287/#252). Lives at
        # turn scope, not per-inference: two separate transients in one turn mean
        # the provider is genuinely unhealthy, and the second failure surfaces.
        inference_retried = False
        # The model this turn starts on, put back when it ends: a turn that fell back must not
        # leave the next one answering on the fallback with nothing saying so.
        home = (self._model, self._definition.model)
        announced = self._failover_announced and self.failover is not None
        self._failover_queue = list(self.failover.candidates) if announced and self.failover else []
        self._turn_failures = []
        self._pending_substitution = ""
        fallbacks = 0  # how many models this turn fell back to

        # end_turn() in a finally, not at each return: a stop arriving in the window
        # between a turn ending and the next beginning must answer "no_turn", and an
        # exception-terminated turn is exactly the case a per-return-site reset misses.
        try:
            while turns < self._max_turns:
                if self._cancelled:
                    yield AgentEvent(
                        kind=EVENT_COMPLETE,
                        stop_reason=self._stop_reason_for_cancel(),
                        # The breaker's sentence when it is what stopped the turn.
                        text=self._stop_note,
                        # Attribute what was ALREADY SPENT before the stop. These three
                        # were omitted here, so a stop between ReAct cycles silently threw
                        # away every token the earlier cycles burned — and a stop that
                        # hides spend is why users distrust the button.
                        input_tokens=agg_in,
                        output_tokens=agg_out,
                        cache_read_tokens=agg_cache_read,
                        cache_creation_tokens=agg_cache_creation,
                        cost_usd=agg_cost,
                        num_turns=turns,
                        context_usage_pct=self._last_context_pct,
                        event_count=agg_events,
                        tool_call_count=agg_tool_calls,
                        served_model_ref=self.served_model_ref,
                        audit_ids=tuple(agg_audit_ids),
                    )
                    return
                turns += 1

                # 0) COMPACT — when context crosses the threshold, run structured
                # compaction (no-LLM tool-output pruning pre-pass → 4-region →
                # structured summary). Anti-thrashing skips it if recent passes
                # barely helped. ACP backends own their own compaction; this is the
                # native loop's. Said when it happens, in /compact's words: the history
                # the rest of this conversation is answered from has just changed.
                compacted = self._maybe_compact()
                if compacted is not None:
                    yield self._compacted_on_its_own(*compacted)

                assistant_text = ""
                tool_calls: list[AgentEvent] = []
                usage: AgentEvent | None = None

                # 1) INFERENCE — stream a stateless completion over full history.
                # Prompt-cache middleware: resolve the provider's
                # graded cache mode off the instance (mirrors the supports_tools getattr) and
                # mark a cacheable prefix. NONE → the same object is handed back (byte-identical
                # for an undeclared provider); AUTOMATIC → also unchanged (no marker needed);
                # EXPLICIT → a NEW list with one neutrally-hinted message, its own adapter
                # translating the hint (a later change). self._messages itself is never mutated.
                # The user's switch folds into the SAME value the provider declares
                # (effective_cache_mode): off → NONE, which is already the untouched-list
                # path. No second code path, and no branch that skips the call.
                mode = effective_cache_mode(
                    getattr(self._model, "prompt_cache", PromptCache.NONE),
                    enabled=self._prompt_cache_enabled(),
                )
                logger.debug("native: prompt-cache mode %s", getattr(mode, "value", mode))
                msgs = self._request_messages(mode)
                # #2287/#252: the native loop's ONE correction-retry. A transient
                # inference failure (provider 5xx, dropped connection, timeout)
                # that arrives BEFORE anything user-visible streamed is retried
                # once per agent turn; everything else propagates unchanged:
                #   - a mid-stream failure after visible text/thinking (a retry
                #     would re-stream and duplicate what the user already read),
                #   - a failure after a tool call arrived (the model did work),
                #   - a second failure in the same turn,
                #   - every NON_RETRYABLE mode (open breaker, budget ceiling,
                #     injection/secret-leak — retrying defeats each guard),
                #   - a model that did not start answering within its timeout
                #     (FirstTokenTimeout: the same request is as slow the second time).
                # max_tokens truncation is a SUCCESSFUL stream carrying a
                # stop_reason, so it never enters this path — the deliberate cost
                # decision on #2287 (only #2286's attribution note applies there).
                # Deliberately NOT wired to the failure breaker, the structural-
                # loop detector, or procedural-memory outcomes: an infrastructure
                # blip is not evidence about the model's behaviour, mirroring the
                # guard's own isolation contract.
                while True:
                    visible_streamed = False
                    attempt_started = now_ms()
                    try:
                        async for ev in self._model.complete(
                            msgs,
                            tools=tools_kwarg,
                            model=self._definition.model or None,
                            reasoning_effort=self._reasoning_effort,
                        ):
                            if self._cancelled:
                                break
                            if self._pending_substitution:
                                # A fallback is answering: said before anything it streams.
                                yield AgentEvent(
                                    kind=EVENT_MODEL_SUBSTITUTION, text=self._pending_substitution
                                )
                                self._pending_substitution = ""
                            agg_events += 1
                            if ev.kind == EVENT_TEXT_CHUNK:
                                assistant_text += ev.text
                                visible_streamed = True
                                yield ev
                            elif ev.kind == EVENT_THINKING_CHUNK:
                                visible_streamed = True
                                yield ev
                            elif ev.kind == EVENT_TOOL_CALL:
                                tool_calls.append(ev)
                            elif ev.kind == EVENT_COMPLETE:
                                usage = ev
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # ── OVERFLOW RECOVERY, ahead of the classifier ──
                        # A LENGTH rejection is the one inference failure whose retry has
                        # to change the PROMPT, and it must be caught before
                        # _inference_failure_mode: that collapses a vendor 400 to
                        # PROVIDER_ERROR, which is retryable, so the generic path below
                        # would spend the turn's single retry re-sending the same
                        # oversized history — the identical failure, one backoff later.
                        # The gate is the generic `can_retry` conjunction with
                        # is_retryable(fmode) swapped for the overflow test, so no
                        # existing guard is relaxed: a retry after visible text would
                        # re-stream what the user already read, and after a tool call the
                        # model did real work.
                        overflow = is_context_overflow(exc)
                        if (
                            overflow
                            and not inference_retried
                            and not visible_streamed
                            and not tool_calls
                            and not self._cancelled
                        ):
                            before, after = self._compact_now(self._last_context_pct)
                            if after < before:
                                self._audit_inference_attempt(
                                    _inference_failure_mode(exc),
                                    attempt=1,
                                    started_ms=attempt_started,
                                    passed=False,
                                )
                                inference_retried = True
                                logger.warning(
                                    "native: context overflow — compacted %d→%d chars, "
                                    "retrying once: %s",
                                    before,
                                    after,
                                    exc,
                                )
                                # REBUILD from the compacted history, re-reading the
                                # generation _compact_now just bumped. Never append to
                                # the existing msgs the way the correction-note path
                                # below does: that retries with a LARGER prompt, which
                                # for this failure is a guaranteed second failure.
                                # _compact_now owns the cache-prefix bump and the
                                # structural re-arm; the anti-thrashing saves list is
                                # deliberately NOT appended here (see its trap note —
                                # that list is the automatic trigger's own bookkeeping,
                                # and polluting it latches threshold compaction off).
                                msgs = self._request_messages(mode)
                                assistant_text = ""
                                usage = None
                                yield self._compacted_on_its_own(before, after)
                                continue
                            # after == before: the pass reclaimed nothing — a truthful
                            # outcome, not a failure. Retrying an identical prompt would
                            # burn a call to learn what compaction already proved, so
                            # fall through to the raise below.
                        fmode = _inference_failure_mode(exc)
                        can_retry = (
                            not inference_retried
                            and not visible_streamed
                            and not tool_calls
                            and not self._cancelled
                            and is_retryable(fmode)
                            # A LENGTH rejection never enters the BLIND retry. Narrowing
                            # here rather than widening is_retryable/FailureMode to carry
                            # overflow keeps the generic path from ever re-sending
                            # unchanged history: the only retry an overflow gets is the
                            # compacting one above, and only when it reclaimed something.
                            and not overflow
                            # Nor does a model that did not START answering in time: the
                            # identical request is read again from its first token and takes
                            # as long again, so a resend only doubles the wait for the same
                            # failure. The next model of the chain may still answer (below).
                            and not isinstance(exc, FirstTokenTimeout)
                        )
                        self._audit_inference_attempt(
                            fmode,
                            attempt=1 + int(inference_retried) + fallbacks,
                            started_ms=attempt_started,
                            passed=False,
                            fallback=fallbacks > 0,
                        )
                        # A provider refusing one of THIS request's tool definitions: the
                        # identical request fails identically, so there is no retry, and the
                        # raw dump is replaced by a sentence naming the tool (the seam should
                        # have kept it out — so it is PersonalClaw's bug, and says so).
                        rejected = tools_named_in_rejection(str(exc), tools_kwarg or [])
                        if rejected:
                            from personalclaw.tool_providers import tool_prefs

                            logger.warning(
                                "native: the provider rejected the tool definition(s) %s — not "
                                "retrying: %r",
                                rejected,
                                exc,
                            )
                            raise ToolSchemaRejected(
                                rejected,
                                can_turn_off=not any(tool_prefs.is_locked(n) for n in rejected),
                            ) from exc
                        if not can_retry:
                            # The turn's retry is spent: the next model of its chain may answer
                            # instead, while nothing of the turn has been shown, for a failure
                            # another model could get past.
                            if not (
                                fmode in FAILOVER_MODES
                                and not overflow
                                and turns == 1
                                and not visible_streamed
                                and not tool_calls
                                and not self._cancelled
                                and await self._fail_over(exc, tools=tools_kwarg)
                            ):
                                raise
                            fallbacks += 1
                            # The fallback's own cache mode, and the history without the
                            # correction note the failed model's retry was given.
                            mode = effective_cache_mode(
                                getattr(self._model, "prompt_cache", PromptCache.NONE),
                                enabled=self._prompt_cache_enabled(),
                            )
                            msgs = self._request_messages(mode)
                            assistant_text = ""
                            usage = None
                            continue
                        inference_retried = True
                        # `%r`, not `%s`: httpx.ReadError and every timeout stringify to "",
                        # which logged "retrying once: " with nothing after it.
                        logger.warning(
                            "native: inference attempt failed (%s) — retrying once: %r",
                            fmode.value,
                            exc,
                        )
                        note = correction_note(fmode)
                        if note:
                            # Same volatile-tail contract as the turn note above:
                            # never ahead of the stable cacheable prefix.
                            msgs = [
                                *msgs,
                                {"role": "user", "content": note, "_volatile": True},
                            ]
                        await asyncio.sleep(_INFERENCE_RETRY_BACKOFF_SECS)
                        assistant_text = ""
                        usage = None
                        continue
                    if inference_retried or fallbacks:
                        self._audit_inference_attempt(
                            FailureMode.NONE,
                            attempt=1 + int(inference_retried) + fallbacks,
                            started_ms=attempt_started,
                            passed=True,
                            fallback=fallbacks > 0,
                        )
                    break

                if usage is not None:
                    agg_in += usage.input_tokens or 0
                    agg_out += usage.output_tokens or 0
                    agg_cache_read += usage.cache_read_tokens or 0
                    agg_cache_creation += usage.cache_creation_tokens or 0
                    agg_cost += usage.cost_usd or 0.0
                    agg_audit_ids.extend(getattr(usage, "audit_ids", ()) or ())
                    # ``is not None``, not truthiness: a provider reporting a real
                    # 0% must update the gauge, and only an absent report must not.
                    if usage.context_usage_pct is not None:
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
                #    entire promise of steering.
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
                            self._messages.append(self._tool_result_msg(call, CANCELLED_BEFORE_RUN))
                    yield AgentEvent(
                        kind=EVENT_COMPLETE,
                        stop_reason=self._final_stop_reason(usage),
                        text=self._stop_note if self._cancelled else "",
                        input_tokens=agg_in,
                        output_tokens=agg_out,
                        cache_read_tokens=agg_cache_read,
                        cache_creation_tokens=agg_cache_creation,
                        cost_usd=agg_cost,
                        num_turns=turns,
                        context_usage_pct=self._last_context_pct,
                        event_count=agg_events,
                        tool_call_count=agg_tool_calls,
                        # Read before the `finally` below puts the turn's own model back.
                        served_model_ref=self.served_model_ref,
                        audit_ids=tuple(agg_audit_ids),
                    )
                    return

                # 3) TOOL EXECUTION — each call is a sub-generator that yields its
                #    UI card, (maybe) a permission request it parks on, the result
                #    card, and appends the tool-result message to history itself.
                #    Calls whose resource sets are disjoint run CONCURRENTLY;
                #    everything else keeps the order the model asked for.
                #    The QUEUED-BUT-UNSTARTED drop lives one level down, in
                #    `_execute_wave` at the per-call dispatch decision — see that
                #    method. It cannot live here: a check around this call would only
                #    fire between BATCHES, which is exactly the hole PR2-12 closed.
                async for ev in self._execute_tool_batch(tool_calls):
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
                cache_read_tokens=agg_cache_read,
                cache_creation_tokens=agg_cache_creation,
                cost_usd=agg_cost,
                num_turns=turns,
                context_usage_pct=self._last_context_pct,
                event_count=agg_events,
                tool_call_count=agg_tool_calls,
                served_model_ref=self.served_model_ref,
                audit_ids=tuple(agg_audit_ids),
            )
        finally:
            # A fallback answered THIS turn only: the next one starts on the model it was chosen
            # for, and asks again whether it may fall back.
            self._model, self._definition.model = home
            self._failover_announced = False
            self._failover_queue = []
            self._pending_substitution = ""
            self._turn_images = []
            self._turn_message = None
            self._cancel.end_turn()

    async def _tool_schema_budget(self) -> int | None:
        """The characters of full tool schemas this turn's window affords, or ``None``.

        The window is ``context_headroom.resolve_window``'s, the one answer the chat's assembly
        and its budget check are bounded by, asked of this loop because this loop serves the
        turn (a subagent's or a loop's turn has no chat runner to ask). It never raises, and an
        unknown window leaves the count as the only bound (``schema_budget_chars``).
        """
        if not self._tool_retriever:
            return None
        from personalclaw.agents.native.tool_retrieval import schema_budget_chars
        from personalclaw.context_headroom import resolve_window

        window = await resolve_window(serving=self)
        # Read defensively: a window question must never be what costs a turn.
        return schema_budget_chars(getattr(window, "budget_tokens", None))

    def _prepare_turn_tools(self, message: str) -> tuple[list[dict] | None, str]:
        """Decide this turn's ``tools`` kwarg + any runtime note to inject.

        Two composable reductions, in order:

        * **GROUPS** (§5) — inactive groups' schemas are out of ``_active_defs``
          already (assembly-time); each contributes ONE stub line instead.
        * **RETRIEVAL** (TR2) — within the active set, surface the relevant
          projection this turn and defer the long tail's parameter schemas to a
          name+description catalog. Ranked against what the user asked
          (``context.user_request``) rather than the whole assembled prompt, and bounded by
          the turn's window (``_schema_budget``).

        Both fail open and neither touches ``_tool_index``, so every tool stays
        callable regardless of what this returns. When no grouping is in effect
        and retrieval doesn't reduce, the result is exactly ``_tool_schema`` — the
        byte-identical no-groups path.
        """
        from personalclaw.context import user_request

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
            self._tool_retriever.select(
                user_request(message), restrict=restrict, budget_chars=self._schema_budget
            )
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
                # `reset_tools` names the session's groups as declared properties, so it rides
                # only when there is a group to name (an object with none is not portable).
                if self._groups:
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
            if grouped and self._groups:
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

    # ── concurrent dispatch under resource reservations ──

    def _prepare_call(self, call: AgentEvent) -> "_PreparedCall":
        """Resolve a requested call's name, arguments, UI card and reservations.

        Pure and cheap — no I/O, no gate, no side effect — because the planner needs
        every call's resource set BEFORE any of them runs, and a planning step that
        could itself execute something would defeat the point.

        A call that needs interactive approval reserves EVERYTHING, i.e. it is planned
        as if it touched every resource and therefore runs alone. Not a resource claim:
        the gate is a round trip to a human, and two prompts racing each other would
        change the order the user is asked in — the one piece of a turn whose ordering
        is a promise to a person rather than to a file. This is also what keeps
        reservations and ADMISSION composable: any gate at the write seam (approval, a
        pre-write read gate) sees exactly the serial world it was written against,
        because a gated call never has a concurrent sibling.
        """
        tool_name = self._resolve_name(call.title or "")
        # 🔴 A HARD PARSE MISS IS REPORTED AS ITSELF. The old reader collapsed an unreadable
        # argument string to `{}`, so the tool's own validation then told the model it had omitted a
        # required argument — for a call the model had made correctly, and whose arguments the
        # provider had cut at `max_tokens`. The failure was misattributed, with no retry, no counter
        # and no event, which made it invisible in the ledger and in any transcript review
        # (issue 1773).
        #
        # The provider now carries `stop_reason` on the event, so the two causes are distinguishable
        # and the note names the one that actually happened. `FailureMode.TOKEN_OVERFLOW`'s
        # correction note has existed with zero writers since it was introduced; this is its first.
        raw_args = read_tool_arguments(call.tool_input)
        arg_error = ""
        if raw_args is ARGUMENTS_UNREADABLE:
            args = {}
            truncated = is_length_stop(getattr(call, "stop_reason", ""))
            arg_error = (
                correction_note(FailureMode.TOKEN_OVERFLOW)
                if truncated
                else "Your tool call's arguments were not valid JSON, so the call could not be "
                "made. Re-send the call with a complete, valid JSON arguments object."
            )
            logger.warning(
                "tool %s: arguments unreadable (stop_reason=%r, truncated=%s) — reporting the "
                "real defect rather than a missing argument",
                tool_name,
                getattr(call, "stop_reason", ""),
                truncated,
            )
        else:
            args = raw_args
        card = AgentEvent(
            # UI card for the call. Carry the tool's declared risk so the chat runner's
            # invoked-log records the authoritative risk (this event fires for EVERY tool
            # that runs, incl. runtime auto-approved ones that never reach the gate).
            kind=EVENT_TOOL_CALL,
            tool_call_id=call.tool_call_id,
            title=tool_name,
            tool_input=args,
            risk_level=self._declared(tool_name).value,
        )
        if self._requires_approval(tool_name):
            reservations: tuple[dispatch_plan.Reservation, ...] = (dispatch_plan.EVERYTHING,)
        else:
            reservations = dispatch_plan.reservations_for(
                tool_name, args, cwd=str(self._cwd) if self._cwd else None
            )
        return _PreparedCall(
            call=call,
            tool_name=tool_name,
            args=args,
            card=card,
            reservations=reservations,
            bkey=params_key(tool_name, args),
            arg_error=arg_error,
            reads=not arg_error and only_reads(tool_name, "", args, self._declared(tool_name)),
        )

    async def _execute_tool_batch(self, tool_calls: list[AgentEvent]) -> AsyncIterator[AgentEvent]:
        """Run one turn's requested calls, overlapping the ones that cannot collide.

        Partitions into ordered waves (:mod:`dispatch_plan`) and runs wave *k* only
        after wave *k-1*, so the relative order of every non-disjoint pair is the order
        the model asked for. A wave of one is dispatched through the ordinary serial
        path, byte-for-byte the pre-HC-6 behaviour — which is what makes
        ``max_tool_concurrency=1`` an exact baseline rather than an approximation of one.

        Failure semantics the atom names: a call that RAISES does not cancel its
        concurrent siblings (they are disjoint from it by construction, so their results
        are still valid and still reported), but it POISONS its own resource set — any
        later call that conflicts with it is not run, because "the file I was about to
        read was being written by something that blew up" is not a state to guess at.
        """
        t0 = time.perf_counter()
        prepped = [self._prepare_call(c) for c in tool_calls]
        plan = dispatch_plan.plan(
            [p.reservations for p in prepped], max_width=self._max_tool_concurrency
        )
        poisoned: list[tuple[dispatch_plan.Reservation, ...]] = []
        try:
            for wave in plan.waves:
                async for ev in self._execute_wave([prepped[i] for i in wave], poisoned):
                    yield ev
        finally:
            # The rule: the instrumentation ships regardless of what it measures, and
            # the benchmark reads THIS line rather than keeping its own stopwatch.
            logger.info(
                "%s mode=%s calls=%d waves=%d widest=%d ms=%d",
                dispatch_plan.TIMING_LOG_PREFIX,
                plan.mode,
                plan.call_count,
                len(plan.waves),
                plan.widest,
                round((time.perf_counter() - t0) * 1000),
            )

    async def _execute_wave(
        self,
        wave: list["_PreparedCall"],
        poisoned: list[tuple[dispatch_plan.Reservation, ...]],
    ) -> AsyncIterator[AgentEvent]:
        """Run one wave: the invocations overlap, everything observable stays in order.

        The split is deliberate. Only ``_guard_and_invoke`` — the part that actually
        touches the resource — runs concurrently; the tail (breaker verdicts, the result
        card, the history append) replays strictly in the order the model listed the
        calls. So a concurrent turn's audit trail carries the same events as the serial
        one, and ``self._messages`` ends up in the same order, which is the only reason
        the next inference sees an identical history.

        Cards for the whole wave are emitted up front, before anything runs: they are
        the UI's statement of intent, and a wave of six lookups appearing at once is
        both truthful and the visible difference from six calls trickling out serially.

        **The stop check lives here, at every dispatch decision** (PR2-12). This method is
        entered once per wave and decides, per call, whether that call is handed to an
        invocation at all — so a stop that lands anywhere in the batch drops every call it
        has not yet dispatched, in THIS wave and in every wave after it. Checking one level
        up (around ``_execute_tool_batch``, or around the wave loop inside it) would only
        fire between batches, and checking before the wave loop would leave the remaining
        waves to run; both are the hole the atom was written against. A dropped call is
        answered by :meth:`_drop_queued_call` rather than by ``_run_tool``, because it must
        NOT feed the failure breaker, the structural-loop detector or the procedural-memory
        outcome list: a cancellation is not evidence about the tool.
        """
        if len(wave) == 1:
            # The serial path — a wave of one is not a special case for cancellation.
            if self._cancelled:
                async for ev in self._drop_queued_call(wave[0]):
                    yield ev
                return
            async for ev in self._run_tool(
                wave[0], prefetched=self._unrunnable_result(wave[0], poisoned)
            ):
                yield ev
            return
        results: list[Any] = [None] * len(wave)
        pending: list[int] = []
        for i, prep in enumerate(wave):
            if self._cancelled:
                # QUEUED-BUT-UNSTARTED — dropped without executing. Decided BEFORE the
                # card, so a call that never ran never claims on screen that it did.
                results[i] = _DROPPED
                continue
            stopped = self._unrunnable_result(prep, poisoned)
            if stopped is not None:
                results[i] = stopped
            elif self._breaker.refusal(prep.tool_name, prep.bkey, reads=prep.reads):
                # Left for _run_tool's own refusal path — the breaker is a reason NOT to
                # invoke, so prefetching it would be the one thing it exists to prevent.
                results[i] = None
            else:
                pending.append(i)
        for prep, decided in zip(wave, results):
            if decided is not _DROPPED:
                yield prep.card
        if pending:
            # return_exceptions: a raising sibling must not cancel the others (the change's
            # "does not cancel its independent siblings"), which is exactly what a bare
            # gather would do.
            done = await asyncio.gather(
                *(self._prefetch(wave[i]) for i in pending), return_exceptions=True
            )
            for i, outcome in zip(pending, done):
                if isinstance(outcome, BaseException):
                    poisoned.append(wave[i].reservations)
                    results[i] = (
                        f"Error: {wave[i].tool_name} raised "
                        f"{type(outcome).__name__}: {outcome}",
                        dict(_FAILED),
                    )
                else:
                    results[i] = outcome
        for prep, prefetched in zip(wave, results):
            if prefetched is _DROPPED or (prefetched is None and self._cancelled):
                # Second half of the same per-call rule. `prefetched is None` means the
                # breaker classified this call as not-to-be-invoked; if the streak has
                # since been cleared by a successful sibling, `_run_tool` would INVOKE it
                # right here — after this wave's awaits, and so possibly after a stop.
                async for ev in self._drop_queued_call(prep):
                    yield ev
                continue
            async for ev in self._run_tool(prep, prefetched=prefetched, card_emitted=True):
                yield ev

    async def _drop_queued_call(self, prep: "_PreparedCall") -> AsyncIterator[AgentEvent]:
        """Answer a call a stop reached before it was dispatched, without running it.

        The drop is still PAIRED with a synthetic result: the assistant message carries
        every ``tool_call`` the model emitted, and an unanswered one breaks the next turn's
        history replay on every tool-using provider (Bedrock Converse rejects an unanswered
        ``toolUse`` outright). Same wording as the pre-batch drop in :meth:`stream`, and
        deliberately none of ``_run_tool``'s accounting tail.
        """
        self._cancel.note_tool_call_dropped()
        yield AgentEvent(
            kind=EVENT_TOOL_RESULT,
            tool_call_id=prep.call.tool_call_id,
            title=prep.tool_name,
            tool_output=CANCELLED_BEFORE_RUN,
            tool_meta={**_FAILED, TOOL_META_NOT_RUN: "stopped"},
        )
        self._messages.append(self._tool_result_msg(prep.call, CANCELLED_BEFORE_RUN))

    @staticmethod
    def _unrunnable_result(
        prep: "_PreparedCall", poisoned: list[tuple[dispatch_plan.Reservation, ...]]
    ) -> tuple[str, dict] | None:
        """The observation for a call that must NOT run, or None to run it.

        Two reasons, and this is the one seam both dispatch paths consult before invoking — the
        serial branch and the concurrent one — so a check here cannot be honoured on one and
        skipped on the other.

        1. Its arguments were unreadable, so there is nothing to invoke WITH. Answering names the
           real defect; the old path passed `{}` and let the tool report a missing argument for a
           call the model had made correctly (issue 1773).
        2. A failed predecessor in this turn touched the same resource, so its state is unknown.

        Either way the call is still ANSWERED, because the assistant message carries every
        `tool_call` the model emitted and an unanswered one breaks the next turn's history replay
        (Bedrock Converse rejects an unanswered `toolUse` outright).
        """
        if prep.arg_error:
            return (
                f"Error: {prep.tool_name} was not run. {prep.arg_error}",
                {**_FAILED, TOOL_META_NOT_RUN: "unreadable_arguments"},
            )
        if not any(dispatch_plan.conflicts(prep.reservations, p) for p in poisoned):
            return None
        return (
            f"Error: {prep.tool_name} was not run — an earlier call in this turn that "
            "touches the same resource failed, so the resource's state is unknown. "
            "Re-check that state before retrying.",
            {**_FAILED, TOOL_META_NOT_RUN: "failed_predecessor"},
        )

    async def _prefetch(self, prep: "_PreparedCall") -> tuple[Any, dict]:
        """Run one call's guarded invocation, returning its result AND its typed meta.

        The meta travels back as a return value rather than through an instance slot.
        That was a single shared field read immediately after the invoke — correct while
        exactly one call could be in flight, and a silent cross-contamination the moment
        two are, with call A's ``truncated``/``recovery_hints`` rendering on call B's
        card. Threading it makes the value belong to the dispatch that produced it.
        """
        meta: dict = {}
        result = await self._guard_and_invoke(prep.call, prep.tool_name, prep.args, meta=meta)
        return result, meta

    async def _run_tool(
        self,
        prep: "_PreparedCall",
        *,
        prefetched: tuple[Any, dict] | None,
        card_emitted: bool = False,
    ) -> AsyncIterator[AgentEvent]:
        """Run one tool call: deny-list → PreToolUse hook → approval → invoke.

        Yields the tool-call card, an ``EVENT_PERMISSION_REQUEST`` when approval is needed
        (parking on the gate until ``approve_tool``/``reject_tool`` resolves it), then the
        tool-result card; appends the tool-result message to history so the next inference
        sees it. ONE implementation for the serial and the concurrent paths.

        ``prefetched`` is ``(result, meta)`` when the invocation already ran (concurrently,
        in this call's wave) or when a predecessor's failure means it must not run at all;
        ``None`` means invoke here and now, which is the single-call path and the only one
        that can park on the approval gate.
        """
        from personalclaw import security

        call, tool_name, args = prep.call, prep.tool_name, prep.args
        if not card_emitted:
            yield prep.card

        # The breaker's refusals, before wasting another invoke: a call that has already
        # failed the same way ≥ BLOCK_THRESHOLD times this run, or a read that keeps giving
        # the same answer with nothing changed. Pre-execution refusal is the NATIVE half of
        # the breaker: this runtime owns dispatch. The ACP host consumes the same counter but
        # can only steer/abort between protocol frames (the stated boundary).
        _bkey = prep.bkey
        blocked_str = (
            self._breaker.refuse(tool_name, _bkey, reads=prep.reads) if prefetched is None else ""
        )
        if blocked_str:
            yield AgentEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id=call.tool_call_id,
                title=tool_name,
                tool_output=blocked_str,
                tool_meta={**_FAILED, TOOL_META_REFUSED_BY: "loop_breaker"},
            )
            self._messages.append(self._tool_result_msg(call, blocked_str))
            self._stop_if_breaker_says()
            return

        if prefetched is None:
            meta: dict = {}
            result_str = await self._guard_and_invoke(call, tool_name, args, meta=meta)
        else:
            result_str, meta = prefetched
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
                meta.update(_FAILED)
                # Said to whoever consumes this stream, which can reach the Inbox.
                meta[TOOL_META_AUTO_DENIED] = True
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
                    # What this tool declares (the gate resolves effective risk from it, and
                    # the dashboard's task-mode gate re-checks the call against it).
                    risk_level=self._declared(tool_name).value,
                    builds=tool_name in self._tool_builds,
                    proposes=tool_name in self._tool_proposes,
                    tells_owner=self._tells_owner(tool_name, args),
                )
                decision = await self._approval.wait(request_id, fut)
                if self._cancelled:
                    result_str = "Error: cancelled"
                    meta.update(_FAILED)
                elif decision == REJECT:
                    # Recoverable: feed back WHY + adapt-don't-repeat guidance so
                    # the model doesn't silently stall on an unattended surface.
                    _, result_str = security.classify_denial(
                        security.DENY_KIND_USER, "the user declined this tool call", tool_name
                    )
                    meta.update(_FAILED)
                else:
                    result_str = await self._invoke(tool_name, args, meta_sink=meta)

        # Record the outcome and apply graduated breaker verdicts; a success clears this key's
        # streak. Whether it failed is the ONE bit the result carries to the tool card
        # (`tool_meta["ok"] is False`), stamped where the failure was written: by `_invoke` from
        # the provider's own `success`, and by each refusal this class authors. It used to be
        # re-derived here from the text (`startswith("Error:")`), so a provider failure with a
        # WHAT/WHY/FIX envelope counted as a success, a successful `cat error.log` as a failure,
        # and every refusal the runtime wrote went to the card with no bit at all, a green check.
        failed = meta.get("ok") is False
        # Procedural-memory signal (M5d): accumulate this turn's tool outcomes for
        # the after-turn review to mine into how-to-work priors. Bounded.
        #
        # A DENIED call is not a FAILED one. Every denial path in this
        # class — the hard deny-list, the task-mode gate, a PreToolUse hook, the
        # user's reject, and the unattended auto-decline — returns an observation
        # authored by `security.classify_denial`, so that function's own recogniser
        # is the discriminator rather than a second copy of its wording here. The
        # breaker still sees `failed`: a denial IS a reason to stop repeating the
        # call, it is just not evidence about the tool.
        if len(self._tool_outcomes) < 200:
            if not failed:
                _outcome = "success"
            elif security.is_denial_observation(result_str):
                _outcome = "denied"
            else:
                _outcome = "failed"
            self._tool_outcomes.append((tool_name, _outcome))
        streak = self._breaker.record(_bkey, failed)
        if failed and streak >= WARN_THRESHOLD:
            result_str += warn_note(tool_name, streak)
        elif not failed:
            # Structural loop detection (E3.1): a *successful* call going nowhere — the same
            # answer to the same call again and again, or an A↔B ping-pong — never trips the
            # failure path (nothing failed). The note tells the model what it looks like from
            # outside; a read repeated past it is refused before it runs, and a turn that keeps
            # repeating is stopped (`_stop_if_breaker_says`).
            sig = f"{_bkey}\x1f{result_digest(result_str)}"
            loop_reason = self._breaker.record_structural(sig, reads=prep.reads)
            if loop_reason:
                logger.info("native: structural loop detected (%s) — %s", tool_name, loop_reason)
                result_str += structural_note(loop_reason)

        yield AgentEvent(
            kind=EVENT_TOOL_RESULT,
            tool_call_id=call.tool_call_id,
            title=tool_name,
            tool_output=result_str,
            tool_meta=meta or {},
        )
        self._messages.append(self._tool_result_msg(call, result_str))
        self._stop_if_breaker_says()

    def _stop_if_breaker_says(self) -> None:
        """End the turn when the breaker's run-wide rungs say so, once, keeping the sentence.

        A turn drowning in failures, or one that keeps getting the answers it already has
        (across all tools), is pathological — aborted rather than allowed to burn the whole
        budget. The sentence rides the turn's terminal event (``_stop_note``), so the surface
        showing the turn can say why it stopped; a log line alone left the chat reading as a
        turn that simply ended.
        """
        why = self._breaker.stop_sentence()
        if not why or self._stop_note:
            return
        logger.warning("native: %s", why)
        self._stop_note = why
        # INTERNAL, not user: the turn ends "cancelled", not "stopped_by_user" —
        # nobody pressed anything, we gave up.
        self._cancel.request(reason=CANCEL_INTERNAL)

    async def _guard_and_invoke(self, call: AgentEvent, tool_name: str, args: dict, *, meta: dict):
        """Deny-list + PreToolUse hook; return a result string, or the
        ``_NEEDS_APPROVAL`` sentinel when the caller must run the gated path.

        ``meta`` is the caller's sink for the result's typed metadata — see
        :meth:`_prefetch` on why it is threaded rather than parked on the instance."""
        from personalclaw import security

        # Dry-run observe-mode (T9): a tool that does not declare it only reads is NOT
        # executed — return a synthetic observation so the replay previews what
        # WOULD happen with no side effects. Declared-SAFE tools fall through and
        # run for real, so the agent reasons over actual state.
        if self._dry_run and self._declared(tool_name) != RiskLevel.SAFE:
            meta[TOOL_META_REFUSED_BY] = "dry_run"
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
            meta.update(_FAILED)
            meta[TOOL_META_REFUSED_BY] = "deny_list"
            return observation

        # Task-mode gate (ask/plan/build) — runs HERE, before approval, so a
        # Trust/YOLO auto-approve can't slip a mutation past a read-only posture.
        # Recoverable denial: tells the model why + that the user can switch modes,
        # so it stops retrying and surfaces the SWITCH_TO_AGENT affordance instead.
        from personalclaw.task_modes import task_mode_denies

        tm_deny = task_mode_denies(
            self._task_mode,
            self._declared(tool_name),
            tool_name,
            "",
            call.tool_input,
            builds=tool_name in self._tool_builds,
        )
        if tm_deny:
            _, observation = security.classify_denial(security.DENY_KIND_POLICY, tm_deny, tool_name)
            meta.update(_FAILED)
            meta[TOOL_META_REFUSED_BY] = "task_mode"
            return observation

        # The host's tool grants (`set_tool_grants`), for the same reason: an approval the policy
        # answers here must not admit a tool the run was never granted.
        if self._tool_grants is not None and tool_name not in self._META_TOOLS:
            try:
                grant_deny = self._tool_grants(
                    tool_name,
                    self._declared(tool_name),
                    "",
                    call.tool_input,
                    proposes=tool_name in self._tool_proposes,
                    tells_owner=self._tells_owner(tool_name, call.tool_input),
                )
            except Exception:  # noqa: BLE001 - a grant that cannot be read admits nothing
                logger.warning("native: tool grants could not be read; refusing", exc_info=True)
                grant_deny = "this run's tool grants could not be read"
            if grant_deny:
                _, observation = security.classify_denial(
                    security.DENY_KIND_POLICY, grant_deny, tool_name
                )
                meta.update(_FAILED)
                meta[TOOL_META_REFUSED_BY] = "tool_grants"
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
                meta.update(_FAILED)
                meta[TOOL_META_REFUSED_BY] = "hook"
                return observation

        # A tool this agent does not have (it was never offered, or it is switched off) is refused
        # before anything asks you about it: allowing the call could run nothing.
        if tool_name not in self._tool_index and tool_name not in self._META_TOOLS:
            return self._unknown_tool(tool_name, meta)
        if self._requires_approval(tool_name):
            return _NEEDS_APPROVAL
        if self._asks_first(tool_name):
            # The tool asks before it runs, and the session's approval policy answered for it.
            # Recorded HERE, past every refusal above, so only for a call that is about to run;
            # the host says whose switch set the policy (`chat_runner.auto_approval_reason`).
            meta[TOOL_META_APPROVAL_WAIVED] = True
        return await self._invoke(tool_name, args, meta_sink=meta)

    @staticmethod
    def _unknown_tool(tool_name: str, meta: dict) -> str:
        """The answer to a call naming a tool this agent does not have, marked failed in *meta*."""
        meta.update(_FAILED)
        meta[TOOL_META_REFUSED_BY] = "unknown_tool"
        return f"Error: unknown tool {tool_name!r}"

    def _resolve_name(self, name: str) -> str:
        """Map an incoming tool name to a real tool id, healing a provider's
        failed reverse-map. Exact match is ALWAYS the primary path: only when
        ``name`` is neither a real tool nor a runtime meta-tool do we fall back
        to the sanitized(real)->real map (unique names only). Any miss returns
        ``name`` unchanged so the existing "unknown tool" error still fires."""
        if name in self._tool_index or name in self._META_TOOLS:
            return name
        return self._tool_sanitized_index.get(name, name)

    async def _invoke(self, tool_name: str, args: dict, *, meta_sink: dict) -> str:
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
            return self._reset_tools(args, meta_sink=meta_sink)
        prov = self._tool_index.get(tool_name)
        if prov is None:
            return self._unknown_tool(tool_name, meta_sink)
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
        from personalclaw import mcp_core, mcp_shared
        from personalclaw.agents.native import builtin_tools as _bt

        token = mcp_core.set_current_session_key(self._session_key)
        # The leaf this session is (none for a chat), for the in-process tools' leaf readers —
        # the depth limit, the posture and `resume_run_id: "self"` (`mcp_shared.leaf_value`).
        lineage_token = mcp_shared.bind_leaf_lineage(self._leaf_lineage)
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
        # Bind this turn's stop signal for the dispatch, so a spawn site deep
        # inside a tool registers its child without every layer between here and there
        # growing a cancellation parameter. `cancel()` reaches the SAME scope object
        # directly off the instance — the contextvar only carries it downward.
        cancel_token = cancellation.bind_scope(self._cancel)
        try:
            result = await prov.invoke(tool_name, args)
        finally:
            cancellation.reset_scope(cancel_token)
            mcp_shared.reset_leaf_lineage(lineage_token)
            mcp_core.reset_current_session_key(token)
            mcp_core.reset_current_agent_id(agent_token)
            _bt.reset_tool_context(ctx_tokens)
        # Capture the result's typed metadata (content_type / raw_ref / truncated)
        # for the TOOL_RESULT event — the string return loses it otherwise. Filled into
        # the CALLER's sink so the value belongs to this dispatch and cannot be read by a
        # concurrent sibling's result card.
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
            # Carry the WHAT/WHY/FIX envelope structurally so
            # the tool card can render coded rows + did-you-mean suggestions (the
            # string form already went to the model via format_tool_result).
            agent_error = getattr(result, "agent_error", None)
            if agent_error is not None:
                meta["agent_error"] = agent_error.to_dict()
        meta_sink.update(meta)
        return format_tool_result(result)

    # Synthetic runtime meta-tools (not in _tool_defs): pure, side-effect-free
    # discovery answered by the runtime itself → never gated, never dispatched to a
    # provider. Without this they fall through to the `return True` default and the
    # loop parks on the approval gate forever.
    _META_TOOLS = frozenset({"tool_search", "tool_schema", "reset_tools"})

    def _declared(self, tool_name: str) -> RiskLevel:
        """What *tool_name* declares a call does. A tool this runtime has no definition for
        declares nothing, which is CAUTION — never a read."""
        return self._tool_risk.get(tool_name, RiskLevel.CAUTION)

    def _tells_owner(self, tool_name: str, tool_input: Any) -> bool:
        """Whether this call to *tool_name* does nothing but tell the owner something: the tool
        declares the arguments such a call carries, and the call sets no other
        (``ToolDefinition.tells_owner``)."""
        return only_tells_the_owner(self._tool_tells_owner.get(tool_name, ()), tool_input)

    def _requires_approval(self, tool_name: str) -> bool:
        if not self._asks_first(tool_name):
            return False
        return self._policy_now() not in ("auto", "yolo", "acceptEdits")

    def _policy_now(self) -> str:
        """The approval policy for THIS decision: the live source's answer when one is set.

        A subagent's grants (its chat's Trust, YOLO, the Auto-approve setting, the hook setting)
        are read when each call is decided, so one revoked while the agent runs stops waiving its
        next call. A source that fails reads as asking.
        """
        source = self._approval_source
        if source is None:
            return self._approval_policy
        try:
            return str(source() or "")
        except Exception:  # noqa: BLE001 - fail toward asking
            logger.warning("native: approval source failed; asking", exc_info=True)
            return ""

    def _asks_first(self, tool_name: str) -> bool:
        """Whether the tool asks before it runs by its own definition, before the session's
        approval policy is consulted."""
        if tool_name in self._META_TOOLS:
            return False
        for t in self._tool_defs:
            if t.name == tool_name:
                return bool(getattr(t, "requires_approval", True))
        return True

    # ── message shaping (OpenAI wire format; Anthropic provider re-maps) ──
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
    def context_usage_pct(self) -> float | None:
        return self._last_context_pct

    @property
    def keeps_cancelled_turns(self) -> bool:
        """True — a stopped turn stays in ``self._messages``: :meth:`stream` appends the
        user message before the first inference, and a stop breaks out of the stream into
        the same assistant-record path, so whatever was answered is kept too. Re-injecting
        the turn as a "[PREVIOUS TURN WAS CANCELLED]" preamble would send it twice."""
        return True

    async def stream_command(self, command: str) -> AsyncIterator[AgentEvent]:
        """Execute ``/compact`` as a real command; anything else is the base's plain prompt.

        Reached for ``/compact`` because :attr:`compacts_in_process` is True — see
        ``dashboard.chat_utils.stream_slash_command``, which owns the dispatch decision.
        The single ``EVENT_COMPACTION_STATUS`` event is the SAME shape an ACP backend's
        compaction frame arrives in (``acp/session.py``), so the chat runner's existing
        handler reports it with no second path: ``noop`` when the pass found nothing to
        reclaim, which is a truthful outcome rather than a failure.
        """
        if command.strip().split()[:1] != ["/compact"]:
            async for ev in super().stream_command(command):
                yield ev
            return
        before, after = self._compact_now(self._last_context_pct)
        if after < before:
            yield AgentEvent(
                kind=EVENT_COMPACTION_STATUS,
                text="completed",
                title=compaction_summary(before, after),
                context_usage_pct=self._last_context_pct,
            )
        else:
            yield AgentEvent(
                kind=EVENT_COMPACTION_STATUS,
                text="noop",
                context_usage_pct=self._last_context_pct,
            )

    def _compacted_on_its_own(self, before: int, after: int) -> AgentEvent:
        """The notice for a compaction this loop did on its own: ``/compact``'s sentence, with the
        status that tells the chat runner to keep what already streamed (``COMPACTION_AUTOMATIC``).

        Silent before: the threshold pass and the overflow retry rewrote the history mid-turn and
        said nothing, so a conversation lost its middle without a word — the notice the dashboard
        posts for a restarted session never reached a loop that compacts itself.
        """
        return AgentEvent(
            kind=EVENT_COMPACTION_STATUS,
            text=COMPACTION_AUTOMATIC,
            title=compaction_summary(before, after),
            context_usage_pct=self._last_context_pct,
        )

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        """Stop the WORK, not just the stream (PR2-12).

        The one seam a user's stop arrives on. It used to set a flag and return
        "acked" — which was true of the flag and false of everything the turn was
        doing: the model request was awaited-and-discarded, a bash child kept running
        to completion, and the tool calls already queued behind it all executed.

        Order matters. Approvals are released first so a turn parked on a permission
        prompt unblocks; then the provider request is ABORTED (the provider seam owns
        how — an HTTP disconnect, an ACP ``session/cancel``); then every child process
        this turn started is terminated AND reaped. Dropping the still-queued calls is
        the loop's job and happens as it observes the signal.
        """
        answer = self._cancel.request(reason=CANCEL_USER)
        if answer == REQUEST_NO_TURN:
            # Nothing in flight — a stop arriving after the turn already finished is a
            # NO-OP, not a failure. Reporting "acked" here would be a lie the caller
            # acts on: stop_turn would set prev_turn_cancelled and the NEXT turn would
            # open with a bogus "your previous turn was cancelled" preamble.
            return "no_turn"
        if answer == REQUEST_REPEAT:
            # Idempotent: the side effects below already ran on the first press. Still
            # "acked" — the turn IS stopping — but nothing is killed or recorded twice.
            return "acked"

        self._approval.cancel_all()

        inner_cancel = getattr(self._model, "cancel", None)
        if inner_cancel is not None:
            try:
                await inner_cancel(wait_ack_timeout=wait_ack_timeout)
                self._cancel.note_model_request_aborted()
            except Exception:
                logger.warning("native: aborting the in-flight model request failed", exc_info=True)

        try:
            await self._cancel.reap_children()
        except Exception:
            logger.warning("native: reaping tool children on stop failed", exc_info=True)
        return "acked"

    def is_alive(self) -> bool:
        return True

    def stage_image_part(self, data_url: str) -> bool:
        """Put *data_url* on the NEXT turn as a neutral image part (MI-4). See the ABC.

        The loop owns the turn, so it owns the image too: the part is laid onto the turn's
        user message in EVERY inference request of that turn (`_request_messages`), including
        the calls after a tool result — an image staged on the inner provider rode only the
        first. Translating the neutral part to a wire is the inner provider's job, and the
        caller has already asked the platform's record whether that provider's type carries
        images and whether the model reads them (``providers.image_input``).
        """
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            return False
        self._staged_images.append(data_url)
        return True

    def _request_messages(self, mode: PromptCache) -> list[dict]:
        """The message list one inference sends: the cache hint, then the turn's images.

        Never mutates ``_messages``. The images go onto the turn's own user message, found by
        identity (``mark_cacheable_prefix`` keeps positions), so a steer or a volatile note
        appended later in the turn never takes them. A turn message compaction folded away
        takes its images with it — there is nothing left for them to belong to.
        """
        msgs = mark_cacheable_prefix(self._messages, mode, generation=self._cache_generation)
        if not self._turn_images or self._turn_message is None:
            return msgs
        idx = next((i for i, m in enumerate(self._messages) if m is self._turn_message), -1)
        if idx < 0:
            return msgs
        out = list(msgs)
        target = out[idx]
        text = target.get("content")
        parts: list[dict] = [{"type": "text", "text": str(text or "")}]
        parts.extend({"type": "image_url", "image_url": {"url": u}} for u in self._turn_images)
        out[idx] = {**target, "content": parts}
        return out

    def announce_failover(self) -> None:
        """Let the next turn fall back down its model chain, for a caller that shows it.

        Called right before :meth:`stream` by a caller that shows ``EVENT_MODEL_SUBSTITUTION``
        (the chat runner: a live line, and the reply's meta). That turn may then move to the next
        model of its chain (:attr:`failover`) when its model fails before any output. Lasts one
        turn. A caller that never calls this keeps a turn that fails, failing, rather than a
        reply from another model presented as the chosen one's.
        """
        self._failover_announced = True

    async def _fail_over(self, exc: BaseException, *, tools: list | None) -> bool:
        """Move this turn to the next model of its chain that can take it; True when one was found.

        Called once the turn's model has failed before any output and its retry is spent. A model
        that cannot be built now, cannot use the tools this turn offers, or does not take images
        on a turn carrying some (the platform's record, as the turn's own model was asked) is
        passed over. False when there is nothing to try, and then the failure stands as it is.
        Raises :class:`NoModelAnswered` when models were tried in its place and failed too,
        because then no single provider's error says what happened.
        """
        failover = self.failover
        if failover is None or (not self._failover_queue and not self._turn_failures):
            return False
        self._turn_failures.append(
            (self.served_model_ref or failover.requested, failover.describe(exc))
        )
        while self._failover_queue:
            ref = self._failover_queue.pop(0)
            try:
                provider, model_id = failover.build(ref)
            except Exception as build_exc:  # noqa: BLE001 — a fallback that cannot build is passed
                logger.warning(
                    "native: fallback %s cannot be built, passed over: %r", ref, build_exc
                )
                continue
            if tools and not getattr(provider, "supports_tools", False):
                logger.info("native: fallback %s cannot use tools, passed over", ref)
                continue
            if self._turn_images and not await failover.takes_images(ref):
                logger.info("native: fallback %s does not take images, passed over", ref)
                continue
            logger.warning(
                "native: %s failed before any output (%r) — falling back to %s",
                self._turn_failures[-1][0],
                exc,
                ref,
            )
            self._model = provider
            self._definition.model = model_id
            served = self.served_model_ref or ref
            self._pending_substitution = failover.substitution(served, self._turn_failures).notice()
            return True
        if len(self._turn_failures) > 1:
            raise NoModelAnswered(self._turn_failures) from exc
        return False

    def drain_tool_outcomes(self) -> list[tuple[str, str]]:
        """Return this run's accumulated ``(tool, outcome)`` pairs and clear them.

        ``outcome`` is one of :data:`personalclaw.memory_service.PROCEDURAL_OUTCOMES`
        — ``success``, ``failed`` or ``denied``. The after-turn review drains this
        into procedural memory (M5d). Draining (not just reading) keeps the
        accumulator bounded across turns."""
        out = list(self._tool_outcomes)
        self._tool_outcomes.clear()
        return out

    def set_workspace(self, path: Path) -> None:
        self._cwd = Path(path)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self._session_key = session_key

    def set_steer_source(self, pull: "Callable[[], list[str]] | None") -> bool:
        """Wire the queue-steering source (#37): a callable the loop drains at each
        model boundary for mid-turn user messages. None disables steering.

        Returns whether a drain is now armed, so the dispatcher reads one answer from
        every runtime instead of inferring native's from the absence of a refusal
        (PR2-10 — the ACP seam CAN refuse, this one never does)."""
        self._pull_steer = pull
        return pull is not None

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
        once the cap is hit this returns False and the turn ends normally.

        The overflow is RETAINED on ``_steer_pending``, never dropped. The pull empties
        the session's buffer, so popping the whole deque and then breaking at the cap
        discarded steers that existed nowhere else — measured: with the cap at 4 and 7
        buffered, three vanished from history, from the session deque, and from this
        runtime in one call. The cap is a bound on how much redirection ONE turn absorbs,
        not a licence to lose the rest, so what does not fit stays owed and is reported at
        turn end by :meth:`undelivered_steers` for the dispatcher to requeue visibly.
        """
        if self._pull_steer is not None:
            try:
                pulled = self._pull_steer()
            except Exception:
                logger.debug("steer drain failed", exc_info=True)
                pulled = []
            self._steer_pending.extend(s for s in (pulled or []) if s and s.strip())
        appended = False
        while self._steer_pending and self._steers_injected < _MAX_STEERS_PER_TURN:
            s = self._steer_pending.pop(0)
            self._messages.append(
                {
                    "role": "user",
                    "content": f"[Steering — the user added this mid-task]\n{s}",
                }
            )
            self._steers_injected += 1
            appended = True
        return appended

    def undelivered_steers(self) -> list[str]:
        """Steers this turn owes the user: pulled from the session's buffer but never
        appended to history, because the per-turn cap was already spent. Empty on the
        happy path.

        Read by the dispatcher at turn end (the same duck-typed seam
        :class:`~personalclaw.acp.session.AcpSession` exposes) so an undeliverable steer is
        requeued onto the visible queue instead of vanishing. Deliberately NOT a new event
        kind: the frontend filters ``activity_event {kind:"status"}`` as noise, so
        announcing the loss that way would be an invisible fix.
        """
        return list(self._steer_pending)

    def set_approval_policy(self, policy: str) -> None:
        self._approval_policy = policy or ""
        self._approval_source = None

    def set_approval_source(self, source: Callable[[], str]) -> None:
        """Read the approval policy from *source* at every decision (`_policy_now`)."""
        self._approval_source = source

    def set_task_mode(self, mode: str) -> None:
        """Set the task mode (agent/ask/plan/build) enforced in _guard_and_invoke."""
        self._task_mode = mode or "agent"

    def set_tool_grants(self, denial: Callable[..., str] | None) -> None:
        """Set which tools this run may use at all: ``denial(tool, declared, tool_kind,
        tool_input, proposes=..., tells_owner=...)`` is why not, ``""`` if it may. It is given what
        the tool declares (:meth:`_declared`, whether it only files a proposal, and whether this
        call only tells the owner something), because a ``read`` grant admits a call by its
        declaration, never by its name.

        Asked in :meth:`_guard_and_invoke` before approval, like the task mode, because an approval
        this runtime answers itself (its policy says ``auto``) never reaches the host that holds
        the grants. A subagent's capability class and the operator ceiling's ``tools`` scope were
        enforced only where the host answers an ask, so a read-only research run with a standing
        approval grant ran its write tools.
        """
        self._tool_grants = denial

    @property
    def tool_grants(self) -> Callable[..., str] | None:
        """The check :meth:`set_tool_grants` set, or ``None`` when this run is granted every tool:
        what a host that holds one turn to other grants restores when the turn ends."""
        return self._tool_grants

    @property
    def agent_model(self) -> str:
        return self._definition.model or getattr(self._model, "_model", "") or ""

    @property
    def model_provider(self) -> "ModelProvider":
        """The inference provider this loop calls — what serves every turn it runs.

        Public because the window a turn is served with is the provider's own answer
        (``ModelProvider.served_context_window``), and the chat runner resolves that window
        before assembling the turn.
        """
        return self._model

    @property
    def served_model_ref(self) -> str:
        """The ``"<entry>:<model>"`` ref of the model this loop actually sends each turn to.

        The ENTRY comes from the provider's build stamp (``ModelProvider.served_ref``); the
        MODEL is the one this loop passes to ``complete(model=…)``, which overrides the entry's
        own default whenever the definition names one. ``""`` when the provider was not built
        through the resolution seam and the definition names nothing.
        """
        stamped = str(getattr(self._model, "served_ref", "") or "")
        entry, _, built_model = stamped.partition(":")
        model = self._definition.model or built_model
        if entry and model:
            return f"{entry}:{model}"
        return stamped

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
