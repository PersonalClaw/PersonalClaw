"""``ModelCallGuard`` — the model-call chokepoint adapter.

The LLM twin of ``net.fetch``: a :class:`~personalclaw.llm.base.ModelProvider`
that wraps the resolved provider for **non-interactive** calls and enforces, per
stream, the cheap-first pipeline this slice owns:

    circuit-breaker check  →  call with hard wall-clock timeout  →  attempt audit

A call to a model on this machine also takes its turn in that model's queue before it is sent
(``guardrails.local_queue``), inside the same clock, so a call somebody is waiting for goes ahead
of background work.

Later stages (secret/PII scan, spend metering, typed-output enforcement, ordered
fallback) compose in front of / behind this same seam in Sessions 2–4.

**Where it wraps (and where it must NOT):** the wrap happens inside the bridge's
single provider-build point (``_resolve_from_config_registry``) gated on the
non-interactive chat-text use case. That gate excludes, by construction, both the
interactive ``NativeAgentRuntime`` (returned before the build point for
``chat``/``code_tools``) and its inner model (resolved with ``chat``/``code_tools``
+ ``_force_model_axis``) — the interactive chat stream a human is watching is
explicitly out of scope for v1.

The guard is a faithful transparent proxy: every ``ModelProvider`` method
delegates to the wrapped provider; only :meth:`stream` / :meth:`complete` (the two
generation paths) are intercepted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.guardrails.audit import AttemptRecord, current_caller, now_ms, record_attempt
from personalclaw.guardrails.breaker import CircuitBreaker, get_breaker
from personalclaw.guardrails.budgets import (
    Budget,
    CallCost,
    Hold,
    SpendMeter,
    Waiting,
    current_run_budget,
    current_run_key,
    get_meter,
    prompt_tokens,
)
from personalclaw.guardrails.calls import ABANDONED, DONE, FAILED, OPEN, ModelCall, open_call
from personalclaw.guardrails.failure import (
    BudgetExceededError,
    CircuitOpenError,
    FailureMode,
    ModelCallTimeout,
    PromptInjectionBlocked,
    SecretLeakBlocked,
    answered_mode,
)
from personalclaw.guardrails.local_queue import Turn, queue_key, take_turn
from personalclaw.guardrails.scan import scan_outbound
from personalclaw.guardrails.wire import record_outbound
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    CancelOutcome,
    LLMEvent,
    ModelProvider,
    ModelSubstitution,
)
from personalclaw.llm.prompt_cache import PromptCache
from personalclaw.llm.registry import served_on_this_machine
from personalclaw.llm.stream_end import until_terminal
from personalclaw.llm.tool_use import uses_tools
from personalclaw.turn_streams import closing_stream

if TYPE_CHECKING:
    from personalclaw.routing.rates import CallPrice

logger = logging.getLogger(__name__)

# Generous default hard ceiling on a single non-interactive call to a provider that keeps no
# wait of its own. Chosen NOT to clip a legitimately slow reasoning call (high-effort o-series
# runs for minutes) — the breaker, not this timeout, is the fast-fail path during an outage. A
# provider whose instance keeps a Request Timeout (``ModelProvider.request_timeout_secs``) is
# bounded by that alone: it says how long a request waits to start and between the parts of its
# answer, and is deliberately not a cap on the whole answer.
_DEFAULT_TIMEOUT_SECS = 300.0


def new_audit_id() -> str:
    """The id one model-call attempt is recorded under (``model_calls.jsonl``)."""
    return uuid.uuid4().hex[:16]


class _WhatItProduced:
    """What one call's stream has produced so far, as its row is decided (:func:`answered_mode`):
    any text that is more than whitespace, or a tool call whose arguments read whole. A call cut
    at its cap mid-call carries a prefix of its arguments, which runs nothing."""

    __slots__ = ("calls", "wrote")

    def __init__(self) -> None:
        self.wrote = False
        self.calls: list[Any] = []

    def see(self, event: LLMEvent) -> None:
        if event.kind == EVENT_TEXT_CHUNK:
            self.wrote = self.wrote or bool(str(event.text or "").strip())
        elif event.kind == EVENT_TOOL_CALL:
            self.calls.append(event.tool_input)

    def anything(self) -> bool:
        if self.wrote or not self.calls:
            return self.wrote
        # Deferred, so a call that made no tool call never loads the native loop's package.
        from personalclaw.agents.native.tools import ARGUMENTS_UNREADABLE, read_tool_arguments

        return any(read_tool_arguments(raw) is not ARGUMENTS_UNREADABLE for raw in self.calls)


def _completes(event: LLMEvent) -> bool:
    """Whether *event* is a provider stream's terminal one, which ends its answer."""
    return event.kind == EVENT_COMPLETE


def naming_the_call(event: LLMEvent, audit_id: str, price: "CallPrice | None" = None) -> LLMEvent:
    """*event*, the call's terminal ``EVENT_COMPLETE``, naming the attempt that completed it
    (``LLMEvent.audit_ids``) and what the guard priced it at (``LLMEvent.charged``), the one figure
    the meter, the model-call log and the usage row written from the event all hold. A copy, so
    the provider's own event stays as it made it; an event of any other type passes through as it
    came."""
    if not isinstance(event, LLMEvent):
        return event
    return replace(event, audit_ids=(*event.audit_ids, audit_id), charged=price)


#: The longest a call waits for the calls running beside it to leave the room it needs under a
#: spend ceiling, before it is refused. A guarded call is bounded by its own clock, so what it
#: waits for ends within about this long.
ROOM_WAIT_SECS = _DEFAULT_TIMEOUT_SECS
#: How often a call that is waiting for room looks again.
_ROOM_POLL_SECS = 0.05

#: An image in a request, as the characters of text its tokens would be: about 1,600 tokens, the
#: order a large image costs a vision model.
_IMAGE_CHARS = 4_800


def call_cost(provider: str, model: str, *, prompt_chars: int) -> CallCost:
    """The call to *model* on the entry *provider* a request of *prompt_chars* characters is,
    as the spend ceilings weigh it before it starts (``SpendMeter.admit``): at the rate it is
    priced at when it settles (``routing.rates.effective_rate``), or unpriced when there is none.
    """
    from personalclaw.routing.rates import effective_rate

    rate = effective_rate(provider, model)
    return CallCost(
        ref=f"{provider}:{model}",
        prompt_tokens=prompt_tokens(prompt_chars),
        rate=rate.dearest_per_mtok() if rate is not None else None,
    )


def request_chars(messages: list[dict], tools: list[dict] | None = None) -> int:
    """How much a structured request sends, in characters: every message's text, the calls and
    results it carries, and the tool schemas. An image counts as :data:`_IMAGE_CHARS`, never as
    the length of its encoding."""

    def _content(content: Any) -> int:
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            total = 0
            for block in content:
                if isinstance(block, str):
                    total += len(block)
                elif isinstance(block, dict):
                    if str(block.get("type") or "").startswith("image"):
                        total += _IMAGE_CHARS
                    elif isinstance(block.get("text"), str):
                        total += len(block["text"])
                    else:
                        total += len(json.dumps(block, default=str))
            return total
        return len(json.dumps(content, default=str)) if content else 0

    total = 0
    for message in messages or []:
        if not isinstance(message, dict):
            total += len(str(message))
            continue
        for key, value in message.items():
            if key == "content":
                total += _content(value)
            elif key != "role" and value:
                total += len(json.dumps(value, default=str))
    if tools:
        total += len(json.dumps(tools, default=str))
    return total


async def admit_call(
    meter: SpendMeter,
    cost: CallCost,
    day: Budget,
    run: Budget,
    *,
    wait_secs: float = ROOM_WAIT_SECS,
) -> Hold | None:
    """Admit a call BEFORE it is made, against the day's ceiling and the ambient run's
    (``SpendMeter.admit``): what it set aside, which the caller settles or releases, or ``None``
    when no ceiling applies to it. A call the calls running beside it hold the room for waits for
    them, up to *wait_secs*; the refusal is raised (:class:`BudgetExceededError`).

    The guard admits each call through it, and an agent CLI's turn on a metered axis too
    (``acp.spend``), so the two share one set of ceilings and one account of what is set aside.

    The run's ceiling is read HERE, beside the day's, rather than as a ``firepath`` gate: run
    totals accrue in-process as the run spends, and the fire path binds a FRESH per-fire key
    before the first call — so a pre-fire gate would read 0.0 every time and be inert by
    construction. The AMBIENT ceiling wins when the run bound one: a per-trigger
    ``max_cost_usd_per_run`` is a tighter, run-specific promise than the operator's
    ``max_tokens_per_run`` default (*run*), and the run seam is the only place that knows it.
    """
    run_key = current_run_key()
    ceiling = current_run_budget()
    if ceiling.is_unlimited:
        ceiling = run
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(wait_secs))
    while True:
        verdict = meter.admit(cost, day=day, run=ceiling, run_key=run_key)
        if isinstance(verdict, BudgetExceededError):
            raise verdict
        if not isinstance(verdict, Waiting):
            return verdict
        if loop.time() >= deadline:
            raise verdict.refusal
        await asyncio.sleep(_ROOM_POLL_SECS)


def _mark(call: ModelCall | None, state: str) -> None:
    """Settle a published call that did not complete. A call already settled keeps its state."""
    if call is not None and call.state == OPEN:
        call.state = state


def _recheck_connection(provider_name: str) -> None:
    """Measure *provider_name*'s connection again, in the background, when its calls start or
    stop failing. Best-effort: the call path it rides must never fail because of it."""
    try:
        from personalclaw.providers.connection import recheck

        recheck(provider_name)
    except Exception:  # noqa: BLE001 — a status refresh never breaks a model call
        logger.debug("connection re-check for %s failed", provider_name, exc_info=True)


def _iso_now() -> str:
    """Wall-clock ISO-UTC stamp for the routing-stats fold's ``updated_at``."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _asdict_row(rec) -> dict:
    """The attempt as the SAME flat dict the JSONL carries, so the live fold and the
    rebuild-from-JSONL path (routing.stats) see byte-identical row shapes."""
    import json as _json

    return _json.loads(rec.to_json_line())


def _joined_content(messages: list[dict]) -> str:
    """The user-authored text of a structured message list, for query classification.

    A message ``content`` is either a plain string or a list of typed blocks
    (``{"type": "text", "text": ...}`` and friends). Join the text of the user turns —
    that's what the classifier's length/signal heuristics key on. Best-effort: an odd
    shape yields "" rather than raising (classification is telemetry, never load-bearing)."""
    parts: list[str] = []
    try:
        for msg in messages or []:
            if not isinstance(msg, dict) or msg.get("role") not in ("user", None, ""):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(parts)


class ModelCallGuard(ModelProvider):
    """Wraps ``inner`` with breaker + hard timeout + attempt-level audit."""

    #: Every call through the guard takes its own turn on a model on this machine
    #: (``guardrails.local_queue``), so a caller holding a guarded model takes none for it.
    takes_local_turns = True

    def __init__(
        self,
        inner: ModelProvider,
        *,
        use_case: str,
        provider_name: str,
        model: str,
        timeout_secs: float | None = None,
        breaker: CircuitBreaker | None = None,
        budget: "Budget | None" = None,
        run_budget: "Budget | None" = None,
        meter: "SpendMeter | None" = None,
        scan_mode: str = "warn",
        routed: bool = False,
        routed_fallback: bool = False,
        budget_source: "Callable[[], Budget] | None" = None,
        run_budget_source: "Callable[[], Budget] | None" = None,
        scan_mode_source: "Callable[[], str] | None" = None,
        counted: bool = True,
    ) -> None:
        self._inner = inner
        # The instance's own wait, mirrored so the guard reads as the provider it wraps.
        self.request_timeout_secs = getattr(inner, "request_timeout_secs", None)
        # The call's clock. A caller's own number is kept (a routed local attempt's short one,
        # which is what lets a stalled local model hand over to the next model quickly). With
        # none, an instance that keeps a Request Timeout is bounded by it alone and raises its own
        # sentence naming the setting (``FirstTokenTimeout``), so the guard adds no second clock;
        # a provider that keeps none gets the guard's default ceiling. 0 means no clock here.
        #
        # A Background call with no clock of its caller's is held to the Background time limit
        # either way (``background.call_timeout_secs``): nobody watches such a call, its chain has
        # a model to ask next, and a Request Timeout bounds only how long the answer takes to start
        # and between its parts. Measured: a memory consolidation on a local model streamed for
        # 1,224 s, ended with no answer, and held the model the whole time. That limit is read as
        # each call starts (:meth:`_clock_secs`), never here, so this guard keeps ``None`` for it:
        # the guard lives as long as the runtime holding it, and a change in Settings must bind
        # its next call.
        if timeout_secs is None and use_case != "background":
            timeout_secs = 0.0 if self.request_timeout_secs else _DEFAULT_TIMEOUT_SECS
        # Where the three settings above are read from at EACH call (`guardrails.budgets` and
        # `guardrails.scan_mode`), when the resolution seam hands them: a guard lives as long as
        # the runtime holding it (a heartbeat task's, a loop worker's), so values read when it
        # was built kept a lowered ceiling or a tightened scan from binding until a restart
        # (`approval_grants`, rule 1). The values above are the starting point, and what a read
        # that fails keeps.
        self._budget_source = budget_source
        self._run_budget_source = run_budget_source
        self._scan_mode_source = scan_mode_source
        self._use_case = use_case
        self._provider_name = provider_name
        self._model = model
        self._timeout_secs = None if timeout_secs is None else max(0.0, float(timeout_secs))
        self._breaker = breaker if breaker is not None else get_breaker(provider_name)
        # Day-scope spend ceiling + the meter that accumulates it. A None budget
        # means "unlimited" (the safe default so nothing is capped unexpectedly).
        self._budget = budget if budget is not None else Budget()
        # RUN-scope ceiling, checked against the AMBIENT run key. Separate
        # from the day budget because they answer different questions: the day
        # budget bounds the machine, a run budget bounds one unattended run. A
        # None run budget means unlimited, so an unscoped call behaves as before.
        self._run_budget = run_budget if run_budget is not None else Budget()
        self._meter = meter if meter is not None else get_meter()
        # Whether the day's and the run's ceilings count this provider's calls. Not for a session a
        # person answers on a metered axis (an Attended loop's planner and workers,
        # ``loop.posture``): its spend is its owner's, as a chat's is, so it is neither admitted
        # against those ceilings nor charged to them. Everything else here still applies to it:
        # the breaker, the clock, the attempt audit and the outbound scan.
        self._counted = bool(counted)
        # The outbound secret/PII scan mode the setting asks for: warn | redact | block. What a
        # prompt is scanned at is decided at each call (:meth:`_refresh_scan_mode`): ``warn`` for a
        # model that runs on this machine, whose prompt never leaves it, and this for any other.
        self._scan_setting = scan_mode if scan_mode in ("warn", "redact", "block") else "warn"
        self._scan_mode = self._scan_setting
        # The routing query class of the CURRENT call, set by the entry point that has
        # the prompt text (stream/complete/stream_command) and stamped onto each attempt
        # audit row. "" until a call classifies.
        self._query_class = ""
        # Routing provenance for EVERY attempt this provider makes. Set once at
        # wrap time by the resolution seam, not per call: the routing decision happened when the
        # ref ORDER was chosen, so it is a property of this resolved provider, not of the prompt.
        self._routed = bool(routed)
        self._routed_fallback = bool(routed_fallback)
        # Set when this provider serves IN PLACE OF the model that was asked for — a later chain
        # entry after the head could not serve (``provider_bridge.stamp_substitution``). Stamped
        # after the wrap, by whichever walk knew the head failed, and copied onto every call this
        # guard records, so a step and Introspect say "ran on X instead of Y", not X alone.
        self.substituted_for: ModelSubstitution | None = None

    # ── The intercepted generation paths ────────────────────────────────

    def _classify(self, text: str) -> None:
        """Set ``self._query_class`` for the current call from the pure classifier.

        Pure + fail-open: a classification failure must never break a model call, so any
        error leaves the class "" (the audit row simply carries no class). The value is
        stamped onto every attempt this call makes."""
        try:
            from personalclaw.routing.classifier import classify_query

            self._query_class = classify_query(text, self._use_case)
        except Exception:  # noqa: BLE001 — classification is telemetry, never load-bearing
            self._query_class = ""

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        message = self._prescan(message)
        self._classify(message)
        guarded = self._guarded(
            self._inner.stream(message), strategy="direct", prompt_chars=len(message)
        )
        async with closing_stream(guarded) as events:
            async for event in events:
                yield event

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        # complete() is the native-loop path (structured messages), out of scope
        # for the v1 wrap — but budget + breaker still apply if reached.
        self._classify(_joined_content(messages))
        inner = self._inner.complete(
            messages, tools=tools, model=model, reasoning_effort=reasoning_effort
        )
        guarded = self._guarded(
            inner,
            strategy="direct",
            prompt_chars=request_chars(messages, tools),
            model=model or "",
        )
        async with closing_stream(guarded) as events:
            async for event in events:
                yield event

    @property
    def supports_native_commands(self) -> bool:
        """Explicit pass-through, NOT ``__getattr__``. ``ModelProvider`` declares this
        property with a False default, so normal lookup finds the ABC's answer on the
        wrapper and the transparent-fallback hook below never fires — the guard would
        report "no commands" for an agent that has them, and every slash command would
        silently degrade to text (`G4`)."""
        return bool(getattr(self._inner, "supports_native_commands", False))

    @property
    def prompt_cache(self) -> PromptCache:  # type: ignore[override]
        """Explicit pass-through, the inner provider's own posture: the ABC's ``NONE`` default
        answered for every guarded model, so the native loop placed no cache marker on any
        metered turn (a loop's planner and workers, background work, workflows) and a model that
        caches only on a marker, Bedrock's or Anthropic's, re-read its whole prompt every call."""
        return getattr(self._inner, "prompt_cache", PromptCache.NONE)

    @property
    def supports_tools(self) -> bool:  # type: ignore[override]
        """The wrapped provider's own declaration, read at each ask (``llm.tool_use``): one that
        learns its model takes no tools (a server that refused a request for them) says so here
        too. A copy taken when the guard was built kept answering yes for it."""
        return uses_tools(self._inner)

    @property
    def compacts_in_process(self) -> bool:
        """Explicit pass-through for the same reason as ``supports_native_commands``."""
        return bool(getattr(self._inner, "compacts_in_process", False))

    @property
    def compacts_automatically(self) -> bool:
        """Explicit pass-through for the same reason as ``supports_native_commands``."""
        return bool(getattr(self._inner, "compacts_automatically", False))

    @property
    def keeps_cancelled_turns(self) -> bool:
        """Explicit pass-through for the same reason as ``supports_native_commands``."""
        return bool(getattr(self._inner, "keeps_cancelled_turns", False))

    @property
    def request_only(self) -> bool:  # type: ignore[override]
        """Explicit pass-through for the same reason as ``supports_native_commands``: the ABC
        declares a False default, so a wrapped request-only model would otherwise be assembled a
        full context it is never handed."""
        return getattr(self._inner, "request_only", False) is True

    @property
    def sampling_temperature(self) -> float | None:
        """Explicit pass-through for the same reason as ``supports_native_commands``: the ABC's
        ``None`` default would otherwise answer for every guarded provider."""
        return getattr(self._inner, "sampling_temperature", None)

    @property
    def unsent_options(self) -> dict[str, str]:
        """Explicit pass-through, for the same reason as ``sampling_temperature``."""
        return dict(getattr(self._inner, "unsent_options", None) or {})

    async def served_context_window(self) -> int | None:
        """Explicit pass-through: the ABC's ``None`` would hide the inner provider's served
        window from the window resolver whenever a non-interactive axis wraps it."""
        return await self._inner.served_context_window()

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        command = self._prescan(command)
        self._classify(command)
        guarded = self._guarded(
            self._inner.stream_command(command), strategy="direct", prompt_chars=len(command)
        )
        async with closing_stream(guarded) as events:
            async for event in events:
                yield event

    def _prescan(self, text: str) -> str:
        """Scan an outbound prompt for secrets/PII and apply the mode ladder.

        Returns the (possibly redacted) text to send. Raises in block mode when there are
        findings — audited and never retried (retrying would let a payload brute-force the
        scan).

        🔴 The failure mode is now CHOSEN, not assumed (S156). Every block recorded
        ``secret_leak``, so ``FailureMode.INJECTION_BLOCKED`` — declared, listed in
        ``NON_RETRYABLE``, and carrying its own retry semantics — could never be recorded by
        anything. §2.2's taxonomy separates the two deliberately: they are both non-retryable
        for *different* reasons, and an operator reading the audit trail cannot tell a
        credential slip from an attack if both say ``secret_leak``.

        🔴 The outcome is PUBLISHED, not just returned (#3166). This method is the last point
        before the provider, so the text it returns is definitionally what went on the wire — and
        the caller that journals a prompt (the workflow engine) holds the text from BEFORE the
        substitution. Measured on `c22f79660`: the recorded prompt artifact for a judge node still
        carried `127.0.0.1` while the model was handed `[REDACTED_PHONE]`, so every replay, eval
        and judge bench read text the model never saw, with nothing saying so. Publishing here
        rather than re-scanning at the recording seam is not a style choice: only this text is
        what went on the wire, and a second scan at another moment can differ from it, because
        the mode is re-read on every call (`_refresh_scan_mode`). One scan, one chokepoint, and
        the result carried forward."""
        self._refresh_scan_mode()
        result = scan_outbound(text, mode=self._scan_mode)
        record_outbound(
            result.text,
            original=text,
            findings=result.findings,
            categories=result.categories,
            blocked=result.blocked,
        )
        if result.blocked:
            mode = FailureMode.INJECTION_BLOCKED if result.injection else FailureMode.SECRET_LEAK
            self._audit(new_audit_id(), 1, mode, 0.0, 0, 0, False, "direct")
            from personalclaw.sel import sel

            try:
                sel().log_api_access(
                    caller=f"model_call:{self._use_case}",
                    operation="guardrails.scan_block",
                    outcome="blocked",
                    source="guardrails",
                    resources=(
                        f"provider={self._provider_name} "
                        f"categories={','.join(result.categories)}"
                        + (f" pattern={result.injection_group}" if result.injection else "")
                    ),
                )
            except Exception:
                logger.debug("SEL scan-block audit failed", exc_info=True)
            if result.injection:
                # Names the matched pattern: the rule for the fire-path screen applies here
                # too — a block nobody can appeal against is a block nobody can debug.
                raise PromptInjectionBlocked(result.findings, result.injection_group)
            raise SecretLeakBlocked(result.findings)
        return result.text

    def _refresh_budgets(self) -> None:
        """Read the day and run spend ceilings as they are now, from the sources given.

        A read that fails keeps what the guard last read: never a value nobody set.
        """
        for attr, source in (
            ("_budget", self._budget_source),
            ("_run_budget", self._run_budget_source),
        ):
            if source is None:
                continue
            try:
                setattr(self, attr, source())
            except Exception:  # noqa: BLE001 - keep the last ceiling read, never a looser one
                logger.warning("%s could not be re-read; keeping the last one", attr, exc_info=True)

    def _refresh_scan_mode(self) -> None:
        """Decide the mode this call's prompt is scanned at: ``warn`` for a model that runs on
        this machine (``llm.registry.served_on_this_machine``, the rule the rate table prices a
        local model by and routing orders one by), whose prompt never leaves it, else the setting
        as it reads now (a failed read keeps the last). An endpoint here that passes the prompt on
        (a proxy for a cloud API, a model a server here answers from a hosted service) is no model
        here. Asked at each call, not once: where a model runs is what its server last said about
        it, and the guard lives as long as the runtime holding it."""
        if served_on_this_machine(self._provider_name, self._model):
            self._scan_mode = "warn"
            return
        if self._scan_mode_source is not None:
            try:
                mode = str(self._scan_mode_source() or "")
            except Exception:  # noqa: BLE001 - keep the last mode read
                logger.warning(
                    "scan mode could not be re-read; keeping the last one", exc_info=True
                )
            else:
                if mode in ("warn", "redact", "block"):
                    self._scan_setting = mode
        self._scan_mode = self._scan_setting

    # ── The guard pipeline (breaker → hard timeout → audit) ──────────────

    async def _guarded(
        self,
        source: AsyncIterator[LLMEvent],
        *,
        strategy: str,
        prompt_chars: int,
        model: str = "",
    ) -> AsyncIterator[LLMEvent]:
        """Drive ``source`` under the breaker, the spend ceilings and a cumulative wall-clock
        deadline, recording exactly one attempt row for the whole stream.

        The ceilings admit the call before it is made (:func:`admit_call`), from the size of its
        request (*prompt_chars*): it sets aside what it may cost, and whatever ends it, that is
        replaced by what it cost or given back.

        A model on this machine is then asked for its turn (``local_queue.take_turn``), within the
        same deadline; the turn is held until the answer is complete.

        *model* is the model the call asks for when it names one (the native loop passes its
        agent's), else the one this guard was built for. That model is what the call is weighed,
        priced, charged and recorded as: it is the model that answers. The price is computed once,
        and the meter, the attempt row and the call's terminal event (``LLMEvent.charged``, which
        the usage row is written from) all take that one figure.

        A call that answered is recorded the moment ``EVENT_COMPLETE`` is observed, by how it
        stopped and what it produced (:func:`answered_mode`): one that ran into its output cap is
        ``output_cap``, and passed only when it wrote text or made a call that can run. It is
        recorded BEFORE the event is yielded, because the canonical consumer
        (``stream_and_collect``) ``break``s on ``EVENT_COMPLETE`` rather than draining to
        ``StopAsyncIteration``: a guard that only recorded after loop-exit would then be
        suspended at the terminal ``yield`` forever and never audit. A ``_recorded`` flag makes
        the outcome fire exactly once. A stream that ends with no ``EVENT_COMPLETE`` was cut off
        (``llm.stream_end.until_terminal``, the rule every adapter's reader keeps): the call failed,
        is recorded so, and raises ``AnswerCutOff``, whoever built the provider.
        """
        audit_id = new_audit_id()
        called = model or self._model
        self._refresh_budgets()

        # Breaker check BEFORE any prompt work: during an outage this refuses in
        # microseconds instead of stacking timeouts. HALF_OPEN admits one probe.
        if self._breaker.is_open():
            retry_after = self._breaker.retry_after()
            self._audit(
                audit_id, 1, FailureMode.CIRCUIT_OPEN, 0.0, 0, 0, False, strategy, model=called
            )
            # aclose the source we won't consume, so its resources release.
            await self._aclose(source)
            raise CircuitOpenError(self._provider_name, retry_after)

        # The day's and the run's ceilings, BEFORE the call (:func:`admit_call`): the call sets
        # aside what it may cost and starts only when that fits beside what is spent and what the
        # calls running now have set aside, waiting for them when they hold the room it needs.
        try:
            hold = (
                await admit_call(
                    self._meter,
                    call_cost(self._provider_name, called, prompt_chars=prompt_chars),
                    self._budget,
                    self._run_budget,
                )
                if self._counted
                else None
            )
        except BudgetExceededError:
            self._audit(
                audit_id, 1, FailureMode.BUDGET_EXCEEDED, 0.0, 0, 0, False, strategy, model=called
            )
            await self._aclose(source)
            raise
        except asyncio.CancelledError:
            await self._aclose(source)
            raise

        loop = asyncio.get_running_loop()
        # The clock starts before the call waits for its turn on a local model, so the wait is
        # part of the time the call is given, not extra.
        clock = self._clock_secs()
        deadline = loop.time() + clock if clock > 0 else None
        turn: Turn | None = None
        # The queue calls to the model this call asks for take turns in when it runs on this
        # machine (``guardrails.local_queue``): one call at a time, a call somebody is waiting for
        # first. ``""`` for a model anywhere else, a model its server here passes on included.
        queue = queue_key(self._provider_name, called)
        try:
            if queue:
                try:
                    turn = await take_turn(
                        queue,
                        provider=self._provider_name,
                        model=called,
                        within=self._turn_wait(deadline, loop.time()),
                    )
                except BaseException:
                    # Busy for as long as it could wait, or stopped while waiting: nothing was
                    # sent, so it is no model call and the breaker is not told.
                    await self._aclose(source)
                    raise
            started = now_ms()
            tokens_in = tokens_out = 0
            recorded = False
            produced = _WhatItProduced()
            # The call is published to whoever bound a `guardrails.calls` log — the workflow step
            # that is making it, a best-of-N candidate — only now, past every refusal above: a
            # breaker or budget refusal, or a local model too busy to take the call, sent nothing
            # to a provider and is not a model call.
            # Read at call time, not wrap time: a chain walk stamps the substitution on the
            # provider it resolved AFTER the resolution seam wrapped it, because only the walk
            # knows the entry before it could not serve.
            substituted = self.substituted_for
            call = open_call(
                self._provider_name,
                called,
                temperature=self.sampling_temperature,
                unsent=self.unsent_options,
                substitution=substituted.sentence() if substituted is not None else "",
            )
            # Read through the rule every adapter's reader keeps, so a provider whose stream ends
            # with no terminal event fails here as cut off, whoever built it.
            answer = until_terminal(
                source,
                ends=_completes,
                adapter=self._provider_name,
                missing="its terminal event",
                model=called,
            )
            try:
                while True:
                    if deadline is not None:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise TimeoutError
                        try:
                            event = await asyncio.wait_for(answer.__anext__(), remaining)
                        except StopAsyncIteration:
                            break
                    else:
                        try:
                            event = await answer.__anext__()
                        except StopAsyncIteration:
                            break
                    if call is not None:
                        # The provider is still talking: the workflow stall clock reads this, so a
                        # step whose model is generating slowly is not killed as a silent one.
                        call.last_event_at = time.time()
                    produced.see(event)
                    if event.kind == EVENT_COMPLETE and not recorded:
                        # Terminal signal: record success NOW (the consumer may break on
                        # this event without draining), then keep yielding any trailing
                        # events a provider might still emit.
                        tokens_in = int(getattr(event, "input_tokens", 0) or 0)
                        tokens_out = int(getattr(event, "output_tokens", 0) or 0)
                        price = self._price(event, called)
                        self._record_success()
                        # Charge the DAY scope always, and the ambient RUN scope when one is
                        # bound. `charge` has accepted `run_key=` since guardrails landed
                        # and this — its only production caller — never passed one, so
                        # `run_totals` was permanently empty and every run-scoped cap read zero.
                        # Read from a ContextVar rather than a parameter because the guard is
                        # built by `provider_bridge` from provider config and has no run identity;
                        # threading one in would touch all 33 call sites reaching the bridge.
                        self._charge(hold, tokens_in, tokens_out, price, called)
                        # The provider answered, so its breaker closes whatever the answer was;
                        # the row says what the answer was worth.
                        mode, passed = answered_mode(
                            event.stop_reason, produced=produced.anything()
                        )
                        self._audit(
                            audit_id,
                            1,
                            mode,
                            now_ms() - started,
                            tokens_in,
                            tokens_out,
                            passed,
                            strategy,
                            price=price,
                            model=called,
                        )
                        self._settle_call(call, tokens_in, tokens_out, price)
                        recorded = True
                        # The answer is complete, so the model is free for the next call: its turn
                        # is given back now, not whenever the consumer closes this stream.
                        if turn is not None:
                            turn.release()
                        # The usage this event carries is this call's, and the row a caller writes
                        # from it (`usage_ledger.record_from_event`) keeps the id: that is the join
                        # that keeps the model-call census from counting the call a second time.
                        # It keeps the price too, so the row holds the figure charged here.
                        event = naming_the_call(event, audit_id, price)
                    yield event
            except TimeoutError:
                self._record_failure()
                await self._aclose(answer)
                if not recorded:
                    self._audit(
                        audit_id,
                        1,
                        FailureMode.TIMEOUT,
                        now_ms() - started,
                        0,
                        0,
                        False,
                        strategy,
                        model=called,
                    )
                    _mark(call, FAILED)
                raise ModelCallTimeout(
                    use_case=self._use_case,
                    provider=self._provider_name,
                    model=self._model,
                    waited_secs=clock,
                    background_limit=self._timeout_secs is None,
                ) from None
            except (asyncio.CancelledError, GeneratorExit):
                # Cooperative cancellation / caller closed the guard mid-stream: not a
                # provider failure — don't trip the breaker. If the terminal COMPLETE was
                # already seen (the common case: consumer breaks then closes the gen), the
                # success was already recorded; otherwise record nothing (genuine abort) — except
                # on the call record, where an abandoned generation is exactly what a cancel's
                # accounting has to count.
                await self._aclose(answer)
                if not recorded:
                    _mark(call, ABANDONED)
                raise
            except Exception:
                # A cut-off answer lands here too (`AnswerCutOff`): the stream ended before its
                # `EVENT_COMPLETE`, so nothing recorded the call, and it failed.
                if not recorded:
                    self._record_failure()
                    self._audit(
                        audit_id,
                        1,
                        FailureMode.PROVIDER_ERROR,
                        now_ms() - started,
                        tokens_in,
                        tokens_out,
                        False,
                        strategy,
                        model=called,
                    )
                    _mark(call, FAILED)
                raise
        finally:
            if turn is not None:
                turn.release()
            # Whatever ended the call, nothing it set aside stays set aside: a settled call's hold
            # is already gone, and a failed or stopped one charged nothing.
            self._meter.release(hold)

    def _clock_secs(self) -> float:
        """This call's clock in seconds, 0 for none: the caller's or the guard's own, else, on a
        Background call, the Background time limit as Settings has it at this moment."""
        if self._timeout_secs is not None:
            return self._timeout_secs
        from personalclaw.config.loader import background_limits

        return max(0.0, float(background_limits().call_timeout_secs))

    def _turn_wait(self, deadline: float | None, now: float) -> float | None:
        """How long this call may wait for its turn on a local model: the shorter of what is left
        of its own clock and its provider's Request Timeout (how long a request may wait to
        start), else as long as it takes. The queue shortens it for a call somebody is waiting
        for."""
        limits = [max(0.0, deadline - now)] if deadline is not None else []
        if self.request_timeout_secs:
            limits.append(float(self.request_timeout_secs))
        return min(limits) if limits else None

    def _record_success(self) -> None:
        """Close the breaker; a provider that had tripped it is answering again, so its measured
        connection is re-checked and stops reading "not answering" (:func:`_recheck_connection`),
        and the chores no model answered meanwhile are tried now (``owed_chores.answered``)."""
        if self._breaker.record_success():
            _recheck_connection(self._provider_name)
            from personalclaw import owed_chores

            owed_chores.answered()

    def _record_failure(self) -> None:
        """Count a failed call; the failure that trips the breaker re-checks the provider's
        measured connection, which otherwise kept its last "Connected" for up to its TTL."""
        if self._breaker.record_failure():
            _recheck_connection(self._provider_name)

    def _charge(
        self,
        hold: Hold | None,
        tokens_in: int,
        tokens_out: int,
        price: "CallPrice",
        model: str,
    ) -> None:
        """Charge one call that completed, to *model*, to the day's meter, and to the ambient
        run's when one is bound, in place of what it set aside (``SpendMeter.settle``): a call
        nothing priced is charged as one the dollar caps could not count, never as a free one. A
        call the ceilings do not count (``counted``) is charged to neither."""
        if not self._counted:
            return
        self._meter.settle(
            hold,
            ref=f"{self._provider_name}:{model}",
            tokens=tokens_in + tokens_out,
            answer_tokens=tokens_out,
            dollars=price.dollars,
            priced=price.priced,
            run_key=current_run_key() or None,
        )

    def _price(self, event: LLMEvent | None, model: str) -> "CallPrice":
        """What this call to *model* cost (``routing.rates.price_event``, the one pricing
        function): the provider's reported cost, else its tokens at the effective rate for this
        entry and model, else unpriced. A stream that ended with no terminal event reported
        nothing to price."""
        from personalclaw.routing.rates import price_event

        return price_event(event, provider=self._provider_name, model=model)

    def _settle_call(
        self,
        call: ModelCall | None,
        tokens_in: int,
        tokens_out: int,
        price: "CallPrice",
    ) -> None:
        """Close the published call record with what the provider reported and what it cost.

        ``priced`` is the price's own (:class:`~personalclaw.routing.rates.CallPrice`): the one
        pricing function knows a local model's known zero from an unpriced blank.
        """
        if call is None:
            return
        call.state = DONE
        call.input_tokens = int(tokens_in)
        call.output_tokens = int(tokens_out)
        call.usage_reported = bool(tokens_in or tokens_out)
        call.cost_usd = float(price.dollars)
        call.priced = price.priced

    def _audit(
        self,
        audit_id: str,
        attempt: int,
        mode: FailureMode,
        latency_ms: float,
        tokens_in: int,
        tokens_out: int,
        passed: bool,
        strategy: str,
        *,
        price: "CallPrice | None" = None,
        model: str = "",
    ) -> None:
        """Record one attempt, of a call to *model* (this guard's own when it names none).
        ``price`` is what a call that completed cost; ``None`` for an attempt that sent nothing
        (a refusal) or failed, which added nothing: a known $0."""
        rec = AttemptRecord(
            audit_id=audit_id,
            ts=time.time(),
            use_case=self._use_case,
            provider=self._provider_name,
            model=model or self._model,
            attempt=attempt,
            failure_mode=mode.value,
            latency_ms=round(latency_ms, 1),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            dollars_est=round(price.dollars, 6) if price is not None else 0.0,
            # Estimated unless the provider reported a real cost_usd (which the price
            # prefers); a rate-derived value is flagged.
            estimated=price is None or price.source != "reported",
            passed=passed,
            strategy=strategy,
            query_class=self._query_class,
            routed=self._routed,
            routed_fallback=self._routed_fallback,
            # WHICH SUBSYSTEM asked. Read from a ContextVar for the same reason
            # `current_run_key()` above is: this guard is built by `provider_bridge` from
            # provider config and never sees its caller. "" when nothing bound one.
            caller=current_caller(),
            # Unknown only for a call that completed and that nothing priced (the routing fold
            # and every sum over this log leave its cost out rather than read it as $0).
            priced=price is None or price.priced,
        )
        record_attempt(rec)
        # Fold the same attempt into the rolling routing stats (MODEL-ROUTING-TELEMETRY
        # §1.3, MRT-1c) — the router reads that O(1) fold, never scans the JSONL per call.
        # Best-effort inside record_routing_stats; a fold failure never breaks a call.
        try:
            from personalclaw.config.loader import config_dir
            from personalclaw.routing.stats import record_routing_stats

            record_routing_stats(_asdict_row(rec), home=config_dir(), now=_iso_now())
        except Exception:  # noqa: BLE001 — observability, never load-bearing
            pass

    @staticmethod
    async def _aclose(source: AsyncIterator[LLMEvent]) -> None:
        aclose = getattr(source, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:
            logger.debug("guarded source aclose failed", exc_info=True)

    # ── Faithful transparent proxy for the rest of the contract ──────────

    async def start(self) -> None:
        await self._inner.start()

    async def shutdown(self) -> None:
        await self._inner.shutdown()

    async def approve_tool(self, request_id: str | int) -> None:
        await self._inner.approve_tool(request_id)

    async def reject_tool(self, request_id: str | int) -> None:
        await self._inner.reject_tool(request_id)

    def context_usage_pct(self) -> float | None:
        return self._inner.context_usage_pct()

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    async def cleanup_session(self, session_id: str) -> None:
        await self._inner.cleanup_session(session_id)

    async def compact(self, context: str = "") -> None:
        await self._inner.compact(context)

    async def wait_for_compaction(self, timeout: float = 120.0) -> dict:
        return await self._inner.wait_for_compaction(timeout)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> CancelOutcome:
        return await self._inner.cancel(wait_ack_timeout=wait_ack_timeout)

    def is_alive(self) -> bool:
        return self._inner.is_alive()

    def touch_activity(self) -> None:
        self._inner.touch_activity()

    def set_workspace(self, path: Path) -> None:
        self._inner.set_workspace(path)

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self._inner.set_session_key(session_key, channel_id)

    def __getattr__(self, item: str):
        # Transparent fallback for provider-specific attributes NOT on the
        # ModelProvider ABC (e.g. ``embed`` on an embedding provider, adapter-
        # specific helpers a consumer reaches for). ``__getattr__`` fires only
        # when normal attribute lookup misses, so the explicit ABC methods above
        # (and ``_inner`` itself) are never routed here. Guard against the pre-init
        # window where ``_inner`` isn't set yet.
        if item == "_inner":
            raise AttributeError(item)
        return getattr(self._inner, item)


def wrap_model_call_guard(
    provider: ModelProvider,
    *,
    use_case: str,
    provider_name: str,
    model: str,
    budget: Budget | None = None,
    run_budget: Budget | None = None,
    meter: SpendMeter | None = None,
    scan_mode: str = "warn",
    breaker: CircuitBreaker | None = None,
    timeout_secs: float | None = None,
    routed: bool = False,
    routed_fallback: bool = False,
    budget_source: Callable[[], Budget] | None = None,
    run_budget_source: Callable[[], Budget] | None = None,
    scan_mode_source: Callable[[], str] | None = None,
    counted: bool = True,
) -> ModelProvider:
    """Wrap ``provider`` in a :class:`ModelCallGuard` for a non-interactive call.

    Idempotent: an already-guarded provider is returned unchanged (defends against
    double-wrapping if two resolution layers both reach for the guard). A prompt to a model that
    runs on this machine is scanned at ``warn`` whatever ``scan_mode`` says: local is the model
    *model* the entry *provider_name* runs on this machine (``llm.registry.served_on_this_machine``,
    the rule pricing and routing ask too, decided by the guard at each call), so a prompt that
    leaves the machine gets the scan the setting asks for, one sent through an endpoint here that
    passes it on included, and one that never does is not redacted for a trip it does not take.
    """
    if isinstance(provider, ModelCallGuard):
        return provider
    return ModelCallGuard(
        provider,
        use_case=use_case,
        provider_name=provider_name,
        model=model,
        budget=budget,
        run_budget=run_budget,
        meter=meter,
        scan_mode=scan_mode,
        breaker=breaker,
        timeout_secs=timeout_secs,
        routed=routed,
        routed_fallback=routed_fallback,
        budget_source=budget_source,
        run_budget_source=run_budget_source,
        scan_mode_source=scan_mode_source,
        counted=counted,
    )
