"""The native in-process agent loop — ``NativeAgentRuntime``.

A ReAct-style tool-use loop that runs entirely inside the PersonalClaw process:

    user turn → INFERENCE (ModelProvider.complete) → if tool calls: execute
    (approval-gated) → feed results back → repeat; stop when a model turn makes
    no tool calls (or max_turns / cancel).

It emits the neutral :class:`~personalclaw.llm.events.AgentEvent` stream the chat
runner already consumes from ACP (text/thinking chunks, tool-call + tool-result
cards, a terminal ``EVENT_COMPLETE`` carrying *aggregated* usage), so the runner
needs no per-backend branching. History is owned **here** (``self._messages``) —
``ModelProvider.complete`` is stateless.

Decoupling: this module depends only on the ``ModelProvider`` /
``ToolProvider`` / ``AgentEvent`` contracts plus low-level ``security``. Hook
firing is an injected callable so the package stays free of any
``dashboard``/``chat_runner`` import.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw import cancellation, run_bounds, turn_streams
from personalclaw.acp.types import STOP_REASON_CANCELLED, STOP_REASON_STOPPED_BY_USER
from personalclaw.agents.native import dispatch_plan
from personalclaw.agents.native.approval import REJECT, ApprovalGate, deny_list_refusal, refusal_of
from personalclaw.agents.native.attempt_audit import inference_attempt
from personalclaw.agents.native.catalog_refresh import CatalogRefresh
from personalclaw.agents.native.compaction import InProcessCompaction, compaction_summary
from personalclaw.agents.native.failover import TurnFallback
from personalclaw.agents.native.loop_phases import LoopPhases
from personalclaw.agents.native.loop_state import CANCELLED_BEFORE_RUN, LoopState, Next
from personalclaw.agents.native.owed_reply import OwedReply
from personalclaw.agents.native.steer_intake import (  # noqa: F401 — the cap, read by tests
    _MAX_STEERS_PER_TURN,
    SteerIntake,
)
from personalclaw.agents.native.tool_names import name_census
from personalclaw.agents.native.tools import (
    ARGUMENTS_UNREADABLE,
    declared_consequences,
    observed,
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
from personalclaw.file_scope import file_places_note
from personalclaw.guardrails.audit import record_attempt
from personalclaw.guardrails.failure import FailureMode, correction_note
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
    EVENT_PERMISSION_REQUEST,
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
from personalclaw.llm.prompt_cache import PromptCache, mark_cacheable_prefix
from personalclaw.llm.reasoning_record import REASONING_KEY, without_reasoning
from personalclaw.llm.tool_use import uses_tools
from personalclaw.pre_tool_hooks import HOOK_FAILED, HooksSaid
from personalclaw.safety_flags import yes_or_no
from personalclaw.tool_providers.arguments import missing_arguments, missing_arguments_note
from personalclaw.tool_providers.base import RiskLevel, ToolResult, only_tells_the_owner
from personalclaw.triggers.standing import automations_note

if TYPE_CHECKING:
    from personalclaw.agents.native.failover import ModelFailover
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context_headroom import Window
    from personalclaw.llm.base import ModelProvider, ModelSubstitution
    from personalclaw.providers.provider_bridge import ResolutionBasis
    from personalclaw.tool_providers.base import ToolProvider

logger = logging.getLogger(__name__)

# Brief pause before the one loop-level inference retry (#2287/#252) — long enough
# to step over a same-instant transient, short enough that an interactive turn
# doesn't visibly stall. Deliberately a constant: config/loader.py is at its line
# budget, and a knob nobody tunes is a cost with no consumer.
_INFERENCE_RETRY_BACKOFF_SECS = 0.5


# The operator's blocking hooks on one call: (tool name, its arguments) -> what they said
# (`pre_tool_hooks.on_tool`, which the bridge binds to the agent the runtime runs as).
HookFire = Callable[[str, Any], Awaitable[HooksSaid]]

# Sentinel: the tool passed deny-list + hooks but needs interactive approval,
# so the generator path (_run_tool) must run the gated branch.
_NEEDS_APPROVAL: Any = object()

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
    """A requested tool call resolved far enough to be PLANNED but not yet run.

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
    #: What the call's card says in place of what the model is told, when that is not for the
    #: person reading the chat: the words for a call cut at the output limit
    #: (``owed_reply.cut_call_words``). ``""``: the card shows the observation itself.
    shown: str = ""


# Graduated failure/loop thresholds + the standard notices live in the
# runtime-agnostic observer imported above: the ACP
# host consumes the SAME counting over its neutral event stream, so a threshold or
# a notice's wording is defined once and cannot drift between the two runtimes.


class NativeAgentRuntime(
    InProcessCompaction, CatalogRefresh, SteerIntake, LoopPhases, AgentProvider
):
    """In-process agent runtime for one session."""

    def __init__(
        self,
        *,
        definition: "AgentRuntimeDefinition",
        model_provider: "ModelProvider",
        tool_providers: list["ToolProvider"] | None = None,
        tool_surface: Callable[[], list["ToolProvider"]] | None = None,
        cwd: Path | None = None,
        session_key: str = "",
        max_turns: int = 100,
        hook_fire: HookFire | None = None,
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
        # bind_tool_context so artifact_save can stamp its project_id (to tie work
        # created during a project's session/loop back to that Project).
        self._project_id = project_id or ""
        # Per-turn reasoning effort ("" | low | medium | high | xhigh | max) forwarded to
        # the model's complete() — a provider whose model reasons maps it to the shape that
        # model takes (an effort level, or a thinking token budget); others ignore it.
        self._reasoning_effort = reasoning_effort or ""
        self._tool_providers = list(tool_providers or [])
        self._tool_surface = tool_surface  # read again when the tools change (`catalog_refresh`)
        self._cwd = Path(cwd) if cwd else None
        self._session_key = session_key
        # Per-turn procedural-outcome accumulator: bounded list of
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
        # Dry-run replay: observe-mode. Write-capable tools (any non-SAFE
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
        self._fallback = TurnFallback()
        # The current turn and the model it started on: a turn's cleanup restores only its own.
        self._turn_seq = 0
        self._turn_home: tuple[Any, str] | None = None

        # Conversation history — owned by the loop (complete() is stateless).
        self._messages: list[dict] = []
        # Images staged for the NEXT turn (`stage_image_part`), and the ones riding the turn
        # in flight with the user message they belong to. Pixels never enter `_messages`:
        # they are laid onto each inference's REQUEST copy (`_request_messages`), so every
        # model call of the turn sees them, compaction and the char backstop never count
        # base64, and a later turn is never handed an earlier turn's image.
        self._staged_images: list[str] = []
        # What fills the context, part by part (`context_parts`): the assembly's split of the NEXT
        # turn's message (`stage_context_parts`), each turn's message with its split and length as
        # measured, and the tool definitions the last request sent.
        self._staged_parts: dict[str, int] | None = None
        self._message_parts: list[tuple[dict, dict[str, int], int]] = []
        self._last_tools: list | None = None
        self._turn_images: list[str] = []
        self._turn_message: dict | None = None
        # The reply the turn in flight owes (`owed_reply`): `_request_messages` lays the note that
        # asks for it on one request's tail and nowhere in the history.
        self._owed = OwedReply()
        # Discovered tool surface, populated by start().
        self._tool_defs: list[Any] = []
        self._tool_schema: list[dict] = []
        self._tool_index: dict[str, "ToolProvider"] = {}
        # tool name → the input schema its provider declared, before the portable repair the model
        # is shown (`_preflight` checks a call against what the tool requires).
        self._tool_input_schemas: dict[str, Any] = {}
        # Fallback name resolver (provider-agnostic): sanitized(real_name)->real_name,
        # populated ONLY for names that need rewriting AND sanitize uniquely (no
        # collisions). Consulted when the exact _tool_index lookup misses, so a
        # provider whose reverse-map didn't round-trip a rewritten name still
        # dispatches. Exact match always stays the primary path (see _resolve_name).
        self._tool_sanitized_index: dict[str, str] = {}
        self._tool_retriever: Any = None  # built in start() (per-turn tool retrieval)
        # The window this turn is served with (`context_headroom.resolve_window`), resolved once
        # as the turn starts and read by both of its consumers: the characters of full tool
        # schemas it affords (`_tool_schema_budget`, None for no bound) and, while no usage has
        # been reported, the compaction estimate (`_estimated_context_pct`).
        self._turn_window: Window | None = None
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
        # (group changes take effect at the turn boundary).
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
        # Ceiling on one wave's concurrent dispatch. 1 = serial dispatch,
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
        # Which tools this run's model is shown at all (`set_tool_offer`); None shows every one.
        self._tool_offer: Callable[..., str] | None = None
        # tool name → what the tool DECLARES a call does (its RiskLevel; SAFE is the read-only
        # declaration), and the tools that declare they build a Build-mode deliverable or only
        # file a proposal, and the arguments a call to a tool may carry and still only tell the
        # owner something. Built in start(); a name missing from the map declares nothing, which
        # is CAUTION (`_declared`).
        self._tool_risk: dict[str, RiskLevel] = {}
        self._tool_builds: frozenset[str] = frozenset()
        self._tool_proposes: frozenset[str] = frozenset()
        self._tool_tells_owner: dict[str, tuple[str, ...]] = {}
        # What each tool declares a call may do past this machine (`ToolDefinition.consequences`),
        # with PersonalClaw's own classified: a name missing from the map declares nothing.
        self._tool_consequences: dict[str, tuple[str, ...]] = {}
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
        # Queue-steering: a callback the loop drains at each model boundary
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
        if not uses_tools(self._model):
            self._run_without_tools()
            return
        await self._build_catalog(carry=False)

    async def _build_catalog(self, *, carry: bool) -> None:
        """Build the catalog; a *carry* rebuild keeps the session's choices (`catalog_refresh`)."""
        self._stamp_surface()
        # User-disabled tools/providers: a harder gate than retrieval —
        # a disabled tool (individually OR via its whole provider being off) is
        # removed from BOTH the schema/catalog AND the dispatch index, so the model
        # can't see or call it. Core-locked tools + the locked platform provider are
        # never disabled (the tool_prefs guards ignore them). Read at every build; fail-open.
        from personalclaw.tool_providers import tool_prefs
        from personalclaw.tool_providers.portable_schema import offered_tool_definitions
        from personalclaw.tool_providers.registry import app_of, serve

        disabled_keys = tool_prefs.load_disabled()
        disabled_provs = tool_prefs.load_disabled_providers()
        # The agent's tool list (`agents.tool_list`), as hard a gate as a switched-off tool: a tool
        # it does not allow is left out of the schema, the catalog and the dispatch index alike,
        # so the model is never shown it, and `_guard_and_invoke` refuses a call to it by name.
        held = self._definition.tools
        withheld: list[str] = []
        defs: list[Any] = []
        index: dict[str, ToolProvider] = {}
        declared: dict[str, Any] = {}
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
        #
        # Listed for this session's run, held to its egress tier (egress only: nothing else reads
        # the hold): listing a remote MCP server's tools starts its connection, and a run whose
        # tier does not reach the server must not reach it to learn what the server offers.
        from personalclaw.net.policy import egress_held_to

        with egress_held_to(self._session_key):
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
                if not held.allows(t.name):
                    withheld.append(t.name)
                    continue
                enabled.append(t)
                declared[t.name] = t.parameters
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
        # can't wedge waiting for a human. A property of the run MODE, applied
        # here where the toolset is assembled — not per-tool, not per-loop.
        if self._unattended:
            from personalclaw.tool_providers.base import is_interactive_tool

            stripped = [t.name for t in defs if is_interactive_tool(t)]
            if stripped:
                defs = [t for t in defs if not is_interactive_tool(t)]
                index = {n: p for n, p in index.items() if n not in stripped}
                logger.info("native: unattended run — stripped interactive tools %s", stripped)
        # THE NAME CENSUS (docs/architecture/tool-name-wire.md): a request names each tool by its
        # model-safe form, and _resolve_name maps that form back. A tool whose form another tool,
        # or one of the runtime's own, already has is left out, with a line naming it.
        sanitized, unroutable = name_census(index, taken=self.META_TOOLS)
        if unroutable:
            defs = [t for t in defs if t.name not in unroutable]
            index = {n: p for n, p in index.items() if n not in unroutable}
            provider_of = {n: p for n, p in provider_of.items() if n not in unroutable}
        if held.listed:
            held.say_narrowed(kept=[t.name for t in defs], withheld=withheld)
        self._tool_sanitized_index = sanitized
        self._tool_defs = defs
        self._tool_index = index
        self._tool_input_schemas = declared
        # GROUP PARTITION — derived from the assembled catalog, so it
        # reflects exactly the providers this session actually has (post-disable,
        # post-strip). Seeds activation from the explicit kwarg or this surface's
        # default; None = every group active.
        from personalclaw.tool_providers import groups as _groups

        previous = ({g.name for g in self._groups}, self._active_groups) if carry else None
        self._provider_of = provider_of
        self._groups = _groups.partition(defs, provider_of=provider_of)
        self._active_groups = self._session_groups(previous)
        # SCHEMA ASSEMBLY (group filter → serialization) — factored out so a group
        # change can re-run it without re-discovering providers.
        self._assemble_schema()
        # Risk-level map: dry-run observe-mode intercepts non-SAFE tools, and
        # the permission-request event carries a tool's declared risk to the gate.
        # Built once here so the hot path is a dict lookup.
        self._tool_risk = {t.name: getattr(t, "risk_level", RiskLevel.CAUTION) for t in defs}
        self._tool_builds = frozenset(t.name for t in defs if getattr(t, "builds", False))
        self._tool_proposes = frozenset(t.name for t in defs if getattr(t, "proposes", False))
        self._tool_tells_owner = {t.name: tuple(getattr(t, "tells_owner", ()) or ()) for t in defs}
        self._tool_consequences = declared_consequences(defs, index)
        # Per-turn tool retrieval: a selector over the full catalog. K
        # defaults above the builtin count → behavioral no-op until MCP catalogs
        # grow; selection only changes the schema the model SEES (dispatch via
        # _tool_index is untouched). Fails open (returns the full set on any issue).
        from personalclaw.agents.native.tool_retrieval import (
            ToolRetriever,
            catalog_groups,
            tool_schema_definition,
            tool_search_definition,
        )

        self._tool_retriever = ToolRetriever(
            defs,
            carry_from=self._tool_retriever if carry else None,
            groups=catalog_groups(served),  # which groups are PersonalClaw's own, and their purpose
        )
        # The catalog's vectors are asked of the process's index now, so they are ready before a
        # turn ranks with them (a restart finds them saved). Off the loop: resolving the bound
        # embedding model and reading the index's file are this thread's, not the gateway's.
        await asyncio.to_thread(self._tool_retriever.warm)
        # The retriever's two discovery tools, added to the surfaced set only on a reduced turn and
        # answered in _invoke, not by a provider.
        self._tool_search_def = tool_search_definition()
        self._tool_schema_def = tool_schema_definition()
        from personalclaw.tool_providers.base import ToolDefinition as _TD

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
        the model at the next turn boundary.
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
            tools = [t for t in g.tools if self._offered(t)]
            if not tools:
                continue
            sample = ", ".join(tools[:4])
            more = f", +{len(tools) - 4} more" if len(tools) > 4 else ""
            lines.append(
                f"- {g.name} ({_n_tools(len(tools))}, INACTIVE): {sample}{more} "
                f'— reset_tools({{"{g.name}": true}}) to activate'
            )
        return lines

    def _reset_tools(self, args: dict, *, meta_sink: dict | None = None) -> str:
        """Apply ``reset_tools`` — FINAL-STATE group activation.

        One boolean per group; every non-``always_on`` group the caller omits (or
        sets to anything but a yes) deactivates. Final-state rather than delta semantics because
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
        wanted = {str(k) for k, v in raw.items() if yes_or_no(v) is True}
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

    def _audit_inference_attempt(self, mode: FailureMode, **attempt: Any) -> str:
        """Record one exceptional inference attempt (``attempt_audit.inference_attempt``): every
        failed attempt, the outcome of a retry, and a call that ran into its output cap and no
        guard recorded. Returns the row's ``audit_id``, ``""`` when it was not written.
        Best-effort by contract: an audit failure must never take down the turn it describes."""
        try:
            row = inference_attempt(
                mode,
                served_ref=self.served_model_ref,
                model=self._definition.model or "",
                runtime_id=id(self),
                **attempt,
            )
            record_attempt(row)
            return row.audit_id
        except Exception:
            logger.debug("native: inference attempt audit failed", exc_info=True)
            return ""

    def last_stop_report(self) -> dict:
        """What the last stop actually reached — the shape the dashboard stop handler consumes.

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
        """Read the user's prompt-cache switch, ``agent.prompt_cache_enabled``.

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
        """Run the ReAct loop for one user turn (``message`` is the full, context-built turn-0
        prompt the chat runner already assembled).

        The turn is one :class:`~personalclaw.agents.native.loop_state.LoopState`, built here and
        never kept, and each step runs the five phases over it (:mod:`.loop_phases`): compact,
        infer, end, dispatch, steer. Each says what comes next (``Next``): the next phase, the next
        step, or the turn's end, which it has already said.
        """
        st = LoopState()
        await self._open_turn(st, message)
        # end_turn() in a finally, not at each return: a stop arriving in the window
        # between a turn ending and the next beginning must answer "no_turn", and an
        # exception-terminated turn is exactly the case a per-return-site reset misses.
        try:
            while st.steps < self._max_turns:
                for phase in (
                    self._compact_phase,
                    self._infer_phase,
                    self._end_phase,
                    self._dispatch_phase,
                    self._steer_phase,
                ):
                    st.next = None
                    async with turn_streams.closing_stream(phase(st)) as events:
                        async for ev in events:
                            yield ev
                    if st.next is None:
                        raise RuntimeError(f"native: {phase.__name__} said nothing of what next")
                    if st.next is Next.RETURN:
                        return
                    if st.next is Next.BREAK:
                        break
            # max_turns exhausted
            yield st.ending(
                "max_turns",
                context_usage_pct=self._last_context_pct,
                served_model_ref=self.served_model_ref,
            )
        except Exception:
            # A turn ending in an error has still spent what its finished calls cost (a dollar cap
            # refusing the next call): said first, so its usage row is written for them too.
            if st.totals.charged:
                yield st.spent(served_model_ref=self.served_model_ref)
            raise
        finally:
            # Undo this turn's fallback unless the next began, as when an unclosed stream ends late.
            if self._turn_seq == st.turn:
                if st.home is not None:
                    self._model, self._definition.model = st.home
                self._turn_home = None
                self._fallback.end()
                self._turn_images = []
                self._turn_message = None
                self._owed.pending = False
                self._cancel.end_turn()

    def _tool_schema_budget(self, window: Window) -> int | None:
        """The characters of full tool schemas this turn's *window* affords, or ``None``.

        The window is ``context_headroom.resolve_window``'s, the one answer the chat's assembly
        and its budget check are bounded by, asked of this loop because this loop serves the
        turn (a subagent's or a loop's turn has no chat runner to ask). An unknown window leaves
        the count as the only bound (``schema_budget_chars``).
        """
        if not self._tool_retriever:
            return None
        from personalclaw.agents.native.tool_retrieval import schema_budget_chars

        # Read defensively: a window question must never be what costs a turn.
        return schema_budget_chars(getattr(window, "budget_tokens", None))

    async def _prepare_turn_tools(self, message: str) -> tuple[list[dict] | None, str]:
        """Decide this turn's ``tools`` kwarg + any runtime note to inject.

        Two composable reductions, in order:

        * **GROUPS** — inactive groups' schemas are out of ``_active_defs``
          already (assembly-time); each contributes ONE stub line instead.
        * **RETRIEVAL** — within the active set, surface the relevant
          projection this turn and defer the long tail's parameter schemas to a
          name+description catalog. Ranked against what the user asked
          (``context.user_request``) rather than the whole assembled prompt, and bounded by
          the turn's window (``_schema_budget``).

        Both fail open and neither touches ``_tool_index``, so every tool stays
        callable regardless of what this returns. When no grouping is in effect
        and retrieval doesn't reduce, the result is exactly ``_tool_schema`` — the
        byte-identical no-groups path.
        """
        from personalclaw.agents.native.tool_retrieval import CATALOG_NOTE
        from personalclaw.context import user_request

        grouped = self._active_groups is not None
        pool = self._active_defs if grouped else self._tool_defs
        # A host that narrowed what this run is shown (`set_tool_offer`) narrows the pool before
        # either reduction, so neither the tool block nor the catalog names a tool left out.
        narrowed = self._tool_offer is not None
        from personalclaw.guardrails.policy import shown_of

        pool, unshown = shown_of(pool, self._offered) if narrowed else (pool, "")
        stub_lines = self._group_stub_lines()
        # A group change from last turn announces itself here, at the boundary
        # where the rewritten tool block actually reaches the model.
        pending, self._pending_group_note = self._pending_group_note, ""

        # Per-turn tool retrieval: core ∪ top-K ∪ structural ∪ sticky, scoped
        # to the ACTIVE groups — retrieval composes with grouping rather than
        # competing, so the K budget is spent only on tools whose schemas can ride
        # this turn. No-op until the pool exceeds K; fails open to the full pool.
        # On a worker thread: ranking embeds the request, and the gateway's loop serves every
        # other request while that call is out.
        restrict = {getattr(d, "name", "") for d in pool} if grouped or narrowed else None
        selected_defs = (
            await asyncio.to_thread(
                self._tool_retriever.select,
                user_request(message),
                restrict=restrict,
                budget_chars=self._schema_budget,
            )
            if self._tool_retriever
            else pool
        )
        reduced = bool(self._tool_retriever) and len(selected_defs) < len(pool)

        notes: list[str] = [note for note in (pending, unshown) if note]
        if not reduced:
            # No retrieval reduction: the assembled (group-filtered) schema stands.
            surfaced_defs = list(pool)
            if grouped:
                # `reset_tools` names the session's groups as declared properties, so it rides
                # only when there is a group to name (an object with none is not portable).
                if self._groups:
                    surfaced_defs.append(self._reset_tools_def)
                tools_kwarg = tool_definitions_to_openai_schema(surfaced_defs) or None
            elif narrowed:
                tools_kwarg = tool_definitions_to_openai_schema(pool) or None
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
            if grouped or narrowed:
                # Only ACTIVE-group tools belong in the deferred-schema catalog (inactive groups
                # are represented by their stub lines instead), and only the ones it is shown.
                exclude |= {n for n in self._group_of_name if n not in (restrict or set())}
            if catalog := self._tool_retriever.catalog(exclude=exclude):
                notes.append(f"{CATALOG_NOTE}\n{catalog}")
            logger.debug(
                "native: tier-1 %d/%d tools (+tool_search,+tool_schema); catalog=%d tools",
                len(selected_defs),
                len(pool),
                len(pool) - len(selected_defs),
            )
        if places := file_places_note(self._tool_index):  # where the file tools reach, read now
            notes.append(places)
        if told := await automations_note(self._tool_index, self._offered):
            notes.append(told)  # how the user's automations stand, read now
        if stub_lines:
            notes.append(
                "[inactive tool groups] These capabilities exist but their schemas are not "
                "loaded this turn, to save context. Activate the ones you need in ONE "
                "reset_tools call (it takes final state — list every group you want active); "
                "or just call a tool by name, which still works.\n" + "\n".join(stub_lines)
            )
        return tools_kwarg, "\n\n".join(notes)

    # ── concurrent dispatch under resource reservations ──

    def _prepare_call(self, call: AgentEvent, *, cut: str = "") -> "_PreparedCall":
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
        arg_error = shown = ""
        if raw_args is ARGUMENTS_UNREADABLE:
            args = {}
            truncated = is_length_stop(getattr(call, "stop_reason", ""))
            arg_error = (
                correction_note(FailureMode.TOKEN_OVERFLOW)
                if truncated
                else "Your tool call's arguments were not valid JSON, so the call could not be "
                "made. Re-send the call with a complete, valid JSON arguments object."
            )
            # The card says it for the person (*cut*, from the loop); the note is the model's.
            shown = cut if truncated else ""
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
            risk_level=self.declared(tool_name).value,
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
            reads=not arg_error and only_reads(tool_name, "", args, self.declared(tool_name)),
            shown=shown,
        )

    async def _execute_tool_batch(
        self, tool_calls: list[AgentEvent], *, cut: str = ""
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn's requested calls, overlapping the ones that cannot collide.

        *cut* is what the card of a call the model was cut off writing says
        (``owed_reply.cut_call_words``), when this inference stopped at its output limit.

        Partitions into ordered waves (:mod:`dispatch_plan`) and runs wave *k* only
        after wave *k-1*, so the relative order of every non-disjoint pair is the order
        the model asked for. A wave of one is dispatched through the ordinary serial
        path, byte-for-byte the old serial behaviour — which is what makes
        ``max_tool_concurrency=1`` an exact baseline rather than an approximation of one.

        Failure semantics: a call that RAISES does not cancel its
        concurrent siblings (they are disjoint from it by construction, so their results
        are still valid and still reported), but it POISONS its own resource set — any
        later call that conflicts with it is not run, because "the file I was about to
        read was being written by something that blew up" is not a state to guess at.
        """
        t0 = time.perf_counter()
        prepped = [self._prepare_call(c, cut=cut) for c in tool_calls]
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

        **The stop check lives here, at every dispatch decision**. This method is
        entered once per wave and decides, per call, whether that call is handed to an
        invocation at all — so a stop that lands anywhere in the batch drops every call it
        has not yet dispatched, in THIS wave and in every wave after it. Checking one level
        up (around ``_execute_tool_batch``, or around the wave loop inside it) would only
        fire between batches, and checking before the wave loop would leave the remaining
        waves to run; both are the hole this check exists to close. A dropped call is
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
        result = await self._guard_and_invoke(prep.tool_name, prep.args, meta=meta)
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
            result_str = await self._guard_and_invoke(tool_name, args, meta=meta)
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
                    risk_level=self.declared(tool_name).value,
                    builds=tool_name in self._tool_builds,
                    proposes=tool_name in self._tool_proposes,
                    tells_owner=self._tells_owner(tool_name, args),
                    annotations=self._annotations_of(tool_name),
                    hooks_asked=self._hook_fire is not None,
                    consequences=self._tool_consequences.get(tool_name),
                )
                decision = await self._approval.wait(request_id, fut)
                if self._cancelled:
                    result_str = "Error: cancelled"
                    meta.update(_FAILED)
                elif decision == REJECT:
                    # Recoverable: WHY + adapt-don't-repeat guidance. A host's own refusal
                    # (`refuse_tool`) is told as its reason, never as the user's decline.
                    _, result_str = security.classify_denial(*refusal_of(decision), tool_name)
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
        # Procedural-memory signal: accumulate this turn's tool outcomes for
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
            # Structural loop detection: a *successful* call going nowhere — the same
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
            tool_output=prep.shown or result_str,
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

    async def _guard_and_invoke(self, tool_name: str, args: dict, *, meta: dict):
        """Deny-list + PreToolUse hook; return a result string, or the
        ``_NEEDS_APPROVAL`` sentinel when the caller must run the gated path.

        ``meta`` is the caller's sink for the result's typed metadata — see
        :meth:`_prefetch` on why it is threaded rather than parked on the instance."""
        from personalclaw import security

        # The agent's tool list, FIRST: a tool it may not use is refused whatever else would
        # answer the call (its approval policy, a dry run's "would have run", a hook), because
        # its model was never shown the tool, and a name it has from elsewhere must not reach it.
        if tool_name not in self.META_TOOLS and not self._definition.tools.allows(tool_name):
            return self._definition.tools.refuse(tool_name, meta)

        # Dry-run observe-mode: a tool that does not declare it only reads is NOT
        # executed — return a synthetic observation so the replay previews what
        # WOULD happen with no side effects. Declared-SAFE tools fall through and
        # run for real, so the agent reasons over actual state.
        if self._dry_run and self.declared(tool_name) != RiskLevel.SAFE:
            meta[TOOL_META_REFUSED_BY] = "dry_run"
            return (
                f"[DRY RUN — observe mode] `{tool_name}` is a write-capable tool; "
                f"it was NOT executed. With args {_short_json(args)} it would have "
                "run here. Continue reasoning about what this run would do; do not "
                "retry it expecting a real effect."
            )

        # The deny-list (never prompts) — terminal, no retry invitation, whatever this run's
        # approval policy would answer: on the tool's name and on a command it would run.
        refused, asked = await deny_list_refusal(tool_name, args, meta, self._cwd, self._preflight)
        if refused is not None:
            meta.update(_FAILED)
            return refused

        # Task-mode gate (ask/plan/build) — runs HERE, before approval, so a
        # Trust/YOLO auto-approve can't slip a mutation past a read-only posture.
        # Recoverable denial: tells the model why + that the user can switch modes,
        # so it stops retrying and surfaces the SWITCH_TO_AGENT affordance instead. It and the
        # grants judge `args`, never the raw `tool_input` a streaming provider sends as JSON text.
        from personalclaw.task_modes import task_mode_denies

        tm_deny = task_mode_denies(
            self._task_mode,
            self.declared(tool_name),
            tool_name,
            "",
            args,
            builds=tool_name in self._tool_builds,
        )
        if tm_deny:
            _, observation = security.classify_denial(security.DENY_KIND_POLICY, tm_deny, tool_name)
            meta.update(_FAILED)
            meta[TOOL_META_REFUSED_BY] = "task_mode"
            return observation

        # The host's tool grants (`set_tool_grants`), for the same reason: an approval the policy
        # answers here must not admit a tool the run was never granted.
        if self._tool_grants is not None and tool_name not in self.META_TOOLS:
            try:
                grant_deny = self._tool_grants(
                    tool_name,
                    self.declared(tool_name),
                    "",
                    args,
                    proposes=tool_name in self._tool_proposes,
                    tells_owner=self._tells_owner(tool_name, args),
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

        # The blocking hooks (`pre_tool_hooks`), before every approval: adapt, don't repeat.
        if self._hook_fire is not None:
            try:
                said = await self._hook_fire(tool_name, args)
            except Exception:  # noqa: BLE001 - a hook that cannot run refuses, as one that blocks
                logger.warning("native: a pre-tool hook failed to run; refusing", exc_info=True)
                said = HooksSaid(refusal=HOOK_FAILED)
            if said.refused:
                _, observation = security.classify_denial(
                    security.DENY_KIND_HOOK, said.refusal, tool_name
                )
                meta.update(_FAILED)
                meta[TOOL_META_REFUSED_BY] = "hook"
                return observation

        # A tool this agent does not have (it was never offered, or it is switched off) is refused
        # before anything asks you about it: allowing the call could run nothing.
        if tool_name not in self._tool_index and tool_name not in self.META_TOOLS:
            return self._unknown_tool(tool_name, meta)
        if (gone := self._no_longer_offered(tool_name, meta)) is not None:
            return gone
        bounds = run_bounds.native_check(self, tool_name, args)  # where it reaches past its bounds
        if bounds or self._requires_approval(tool_name):
            # A call its tool will refuse is answered with why before anyone is asked (and before
            # an unattended run is refused it for its bounds): no answer could let it run. A call
            # whose command the tool was already asked about above is not asked again.
            own = None if asked else await self._preflight(tool_name, args, meta)
            refused = own or bounds.refuse(meta)
            return _NEEDS_APPROVAL if refused is None else refused
        if self._asks_first(tool_name):
            # The tool asks before it runs, and the session's approval policy answered for it.
            # Recorded HERE, past every refusal above, so only for a call that is about to run;
            # the host says whose switch set the policy (`chat_runner.auto_approval_reason`).
            meta[TOOL_META_APPROVAL_WAIVED] = True
        return await self._invoke(tool_name, args, meta_sink=meta)

    async def _preflight(self, tool_name: str, args: dict, meta: dict) -> str | None:
        """The answer to a call refused whatever the owner answers, given before anyone is asked
        and marked not run in *meta*; None to ask. The one place every approval passes, so no
        surface is asked about a call that cannot run: the arguments its declared input schema
        requires (``missing_arguments``), then what the tool declares it refuses
        (``ToolProvider.preflight``). A check that cannot be made refuses nothing."""
        refused, why = None, "missing_arguments"
        if problems := missing_arguments(args, self._tool_input_schemas.get(tool_name)):
            shown = next((t for t in self._tool_defs if t.name == tool_name), None)
            note = missing_arguments_note(tool_name, problems, getattr(shown, "parameters", None))
            refused = ToolResult(success=False, error=note)
        elif (prov := self._tool_index.get(tool_name)) is not None:
            why = "refused_by_tool"
            try:
                with self._dispatch_scope():
                    refused = await prov.preflight(tool_name, args)
            except Exception:  # noqa: BLE001 - a check that cannot be made refuses nothing
                logger.warning("native: %s's pre-flight failed; asking", tool_name, exc_info=True)
        if not isinstance(refused, ToolResult) or refused.success:
            return None
        observation = observed(refused, meta)
        meta[TOOL_META_NOT_RUN] = why
        return observation

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
        if name in self._tool_index or name in self.META_TOOLS:
            return name
        return self._tool_sanitized_index.get(name, name)

    async def _invoke(self, tool_name: str, args: dict, *, meta_sink: dict) -> str:
        # tool_search (TR escape hatch): the retriever owns the full catalog, so
        # the runtime answers this directly rather than a provider. Lets the agent
        # discover any tool retrieval didn't surface this turn.
        if tool_name == "tool_search" and self._tool_retriever is not None:
            self._tool_retriever.mark_used("tool_search")
            # Off the loop, for the reason the turn's selection is: ranking embeds the query.
            hits = await asyncio.to_thread(
                self._tool_retriever.search,
                str(args.get("query", "")),
                int(args.get("limit", 20) or 20),
                self._offered,  # what this run is not shown is left out before the cut
            )
            if not hits:
                return "No tools matched. Try broader terms; all tools remain callable by name."
            # tool_search deliberately ranks the FULL catalog — including tools in
            # INACTIVE groups — and names the activation step for those, so search
            # is the discovery path INTO a group.
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
            d = next(
                (
                    t
                    for t in self._tool_defs
                    if getattr(t, "name", "") == want and self._offered(want)
                ),
                None,
            )
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
        if (gone := self._no_longer_offered(tool_name, meta_sink)) is not None:
            return gone
        # Sticky set: a tool the agent actually called stays surfaced for the
        # rest of the session, so a multi-step task can't lose a tool mid-task when
        # the query phrasing drifts. Cheap insurance against the cardinal failure.
        if self._tool_retriever is not None:
            self._tool_retriever.mark_used(tool_name)
        with self._dispatch_scope():
            result = await prov.invoke(tool_name, args)
        return observed(result, meta_sink)

    @contextlib.contextmanager
    def _dispatch_scope(self) -> Iterator[None]:
        """This session, its workspace and its leaf, bound for a provider answering a call
        (``_invoke``) and its pre-flight check (``_preflight``) alike."""
        # Bind this turn's session key for in-process tools (e.g. subagent_run) so a
        # subagent spawned here resolves THIS session as its parent and inherits
        # its trust/auto-approve. The native loop runs inside the gateway with no
        # per-turn env var, so without this the spawn resolves a stale PID file
        # and the subagent's tool calls escalate to interactive approval —
        # breaking unattended goal loops.
        from personalclaw import mcp_core, mcp_shared
        from personalclaw.agents import skill_list
        from personalclaw.agents.native import builtin_tools as _bt

        token = mcp_core.set_current_session_key(self._session_key)
        # The agent's skill list, which its skill tools are held to (`agents.skill_list`).
        skills_token = skill_list.hold(self._definition.skills)
        # The leaf this session is (none for a chat), for the in-process tools' leaf readers —
        # the depth limit, the posture and `resume_run_id: "self"` (`mcp_shared.leaf_value`).
        lineage_token = mcp_shared.bind_leaf_lineage(self._leaf_lineage)
        # Bind this turn's workspace for the native category providers: the
        # session-coupled app providers (knowledge/tasks/loops/inbox) are registry
        # singletons now, so cwd/agent flow via contextvars rather than a per-session
        # constructor. (The platform filesystem/shell provider is still built
        # per-session in provider_bridge with cwd+extra_roots, so its own confinement
        # is unaffected; this binding makes the singletons resolve THIS session too.)
        scratch = run_bounds.runtime_scratch(self)
        ctx_tokens = _bt.bind_tool_context(
            cwd=self._cwd, agent=self._agent_id, project_id=self._project_id, scratch=scratch
        )
        # Bind this turn's stop signal for the dispatch, so a spawn site deep
        # inside a tool registers its child without every layer between here and there
        # growing a cancellation parameter. `cancel()` reaches the SAME scope object
        # directly off the instance — the contextvar only carries it downward.
        cancel_token = cancellation.bind_scope(self._cancel)
        try:
            yield
        finally:
            cancellation.reset_scope(cancel_token)
            mcp_shared.reset_leaf_lineage(lineage_token)
            mcp_core.reset_current_session_key(token)
            skill_list.let_go(skills_token)
            _bt.reset_tool_context(ctx_tokens)

    # Synthetic runtime meta-tools (not in _tool_defs): pure, side-effect-free
    # discovery answered by the runtime itself → never gated, never dispatched to a
    # provider. Without this they fall through to the `return True` default and the
    # loop parks on the approval gate forever. Public: a run's host counts them as no work.
    META_TOOLS = frozenset({"tool_search", "tool_schema", "reset_tools"})

    def declared(self, tool_name: str) -> RiskLevel:
        """What *tool_name* declares a call does, as this runtime's gates read it. A tool this
        runtime has no definition for declares nothing, which is CAUTION — never a read. Public:
        the run's bounds check and a Retry of a turn it ran (``dashboard.repeated_steps``) read
        it too."""
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

        A subagent's grants (its chat's Trust, YOLO, the owner's Approval mode "Auto", the hook
        setting) are read when each call is decided, so one revoked while the agent runs stops
        waiving its next call. A source that fails reads as asking.
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
        if tool_name in self.META_TOOLS:
            return False
        for t in self._tool_defs:
            if t.name == tool_name:
                return bool(getattr(t, "requires_approval", True))
        return True

    # ── message shaping (OpenAI wire format; Anthropic provider re-maps) ──
    @staticmethod
    def _assistant_msg(
        text: str, tool_calls: list[AgentEvent], reasoning: dict | None = None
    ) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": text or ""}
        if reasoning:
            # Opaque model data, never content (`llm.reasoning_record`): kept for the provider that
            # wrote it, stripped from every request to one that does not read it.
            msg[REASONING_KEY] = reasoning
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

    carries_refusal_reasons = True

    async def refuse_tool(
        self, request_id: str | int, reason: str, *, kind: str = "policy"
    ) -> None:
        """Refuse a pending call for the host's own *reason*, which is what the model is told."""
        self._approval.refuse(str(request_id), reason, kind)

    # ── status / control ──
    def context_usage_pct(self) -> float | None:
        return self._last_context_pct

    def stage_context_parts(self, parts: dict[str, int] | None) -> None:
        """Split the NEXT turn's message as its assembly measured it (``context_parts``)."""
        self._staged_parts = dict(parts) if parts else None

    def context_parts(self) -> list[dict] | None:
        """What fills the context, part by part, as this loop's history would be sent now: each
        turn's message split as its assembly measured it, the replies, the tool results and the
        tool definitions (:func:`personalclaw.context_parts.history_parts`)."""
        from personalclaw.context_parts import history_parts

        held = {id(m) for m in self._messages}
        self._message_parts[:] = [mark for mark in self._message_parts if id(mark[0]) in held]
        return history_parts(self._messages, self._message_parts, self._last_tools)

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
            async with turn_streams.closing_stream(super().stream_command(command)) as events:
                async for ev in events:
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
        """Stop the WORK, not just the stream.

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
                # Only what the provider says it closed: a provider with nothing to abort answers
                # "no_turn", and the stop report counted that as an aborted request too.
                if await inner_cancel(wait_ack_timeout=wait_ack_timeout) == "acked":
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
        """Put *data_url* on the NEXT turn as a neutral image part. See the ABC.

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
        """The message list one inference sends: the cache hint, the turn's images, and the
        request for a reply the turn still owes.

        Never mutates ``_messages``. The images go onto the turn's own user message, found by
        identity (``mark_cacheable_prefix`` keeps positions), so a steer or a volatile note
        appended later in the turn never takes them, and compaction keeps that message (it is
        the pass's ``request``), so they ride every inference of the turn. The request for
        the reply (`owed_reply`) is a volatile note at the tail, like the correction note, so it
        rides every attempt of that one inference and never enters the history.
        """
        out = mark_cacheable_prefix(self._messages, mode, generation=self._cache_generation)
        if self._turn_images and self._turn_message is not None:
            idx = next((i for i, m in enumerate(self._messages) if m is self._turn_message), -1)
            if idx >= 0:
                out = list(out)
                target = out[idx]
                text = target.get("content")
                parts: list[dict] = [{"type": "text", "text": str(text or "")}]
                parts.extend(
                    {"type": "image_url", "image_url": {"url": u}} for u in self._turn_images
                )
                out[idx] = {**target, "content": parts}
        if self._owed.pending:
            out = [*out, self._owed.note_message()]
        if getattr(self._model, "reads_reasoning", False) is not True:
            # The reasoning record is opaque data of the provider that wrote it: a provider that
            # does not declare it reads one (an app's, a fallback of another kind) never sees it.
            out = without_reasoning(out)
        return out

    def announce_failover(self) -> None:
        """Let the next turn fall back down its model chain, for a caller that shows it.

        Called right before :meth:`stream` by a caller that shows ``EVENT_MODEL_SUBSTITUTION``
        (the chat runner: a live line, and the reply's meta). That turn may then move to the next
        model of its chain (:attr:`failover`) when its model fails before any output. Lasts one
        turn. A caller that never calls this keeps a turn that fails, failing, rather than a
        reply from another model presented as the chosen one's.
        """
        self._fallback.announced = True

    async def _fail_over(self, exc: BaseException, *, tools: list | None) -> bool:
        """Move this turn to the next model of its chain that can take it
        (:meth:`TurnFallback.move_on`); True when one was found."""
        found = await self._fallback.move_on(
            self.failover,
            exc,
            current=self.served_model_ref,
            tools=bool(tools),
            images=bool(self._turn_images),
        )
        if found is not None:
            self._model, self._definition.model = found
        return found is not None

    def drain_tool_outcomes(self) -> list[tuple[str, str]]:
        """Return this run's accumulated ``(tool, outcome)`` pairs and clear them.

        ``outcome`` is one of :data:`personalclaw.memory_service.PROCEDURAL_OUTCOMES`
        — ``success``, ``failed`` or ``denied``. The after-turn review drains this
        into procedural memory. Draining (not just reading) keeps the
        accumulator bounded across turns."""
        out = list(self._tool_outcomes)
        self._tool_outcomes.clear()
        return out

    def set_workspace(self, path: Path) -> None:
        self._cwd = Path(path)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self._session_key = session_key

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

    def set_tool_offer(self, offer: Callable[..., str] | None) -> None:
        """Set which tools this run's model is shown: ``offer(tool, declared, proposes=...,
        tells_owner=...)`` is why a tool is not, ``""`` if it is. A tool it is not shown is left out
        of the tool block, the deferred catalog, ``tool_search`` and ``tool_schema``, so the model
        is never handed a tool the run would refuse.

        Only what is SHOWN: a call to a tool by a name the model has from elsewhere is still
        decided by :meth:`set_tool_grants`, which a host that narrows the offer holds too.
        """
        self._tool_offer = offer

    @property
    def tool_offer(self) -> Callable[..., str] | None:
        """The check :meth:`set_tool_offer` set, or ``None`` when every tool is shown."""
        return self._tool_offer

    def _offered(self, tool_name: str) -> bool:
        """Whether this run's model is shown *tool_name* (:meth:`set_tool_offer`). The runtime's
        own meta-tools always are; an offer that cannot be read shows nothing else."""
        if self._tool_offer is None or tool_name in self.META_TOOLS:
            return True
        try:
            declared, proposes = self.declared(tool_name), tool_name in self._tool_proposes
            tells = bool(self._tool_tells_owner.get(tool_name))  # calls that only tell the owner
            return not self._tool_offer(tool_name, declared, proposes=proposes, tells_owner=tells)
        except Exception:  # noqa: BLE001 - an offer that cannot be read shows nothing
            logger.warning("native: tool offer could not be read; not showing %s", tool_name)
            return False

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
