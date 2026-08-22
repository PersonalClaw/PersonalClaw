"""``ModelCallGuard`` — the model-call chokepoint adapter (§2.1).

The LLM twin of ``net.fetch``: a :class:`~personalclaw.llm.base.ModelProvider`
that wraps the resolved provider for **non-interactive** calls and enforces, per
stream, the cheap-first pipeline this slice owns:

    circuit-breaker check  →  call with hard wall-clock timeout  →  attempt audit

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
import logging
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from personalclaw.guardrails.audit import AttemptRecord, current_caller, now_ms, record_attempt
from personalclaw.guardrails.breaker import CircuitBreaker, get_breaker
from personalclaw.guardrails.budgets import (
    Budget,
    BudgetVerdict,
    SpendMeter,
    current_run_budget,
    current_run_key,
    get_meter,
)
from personalclaw.guardrails.failure import (
    BudgetExceededError,
    CircuitOpenError,
    FailureMode,
    ModelCallTimeout,
    PromptInjectionBlocked,
    SecretLeakBlocked,
)
from personalclaw.guardrails.scan import scan_outbound
from personalclaw.llm.base import EVENT_COMPLETE, CancelOutcome, LLMEvent, ModelProvider

logger = logging.getLogger(__name__)

# Generous default hard ceiling on a single non-interactive call. Chosen NOT to
# clip a legitimately slow reasoning call (high-effort o-series / large local
# models run for minutes) — the breaker, not this timeout, is the fast-fail path
# during an outage. Config wiring (per-use-case override) lands in Session 2.
_DEFAULT_TIMEOUT_SECS = 300.0


def _new_audit_id() -> str:
    return uuid.uuid4().hex[:16]


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

    def __init__(
        self,
        inner: ModelProvider,
        *,
        use_case: str,
        provider_name: str,
        model: str,
        timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
        breaker: CircuitBreaker | None = None,
        budget: "Budget | None" = None,
        run_budget: "Budget | None" = None,
        meter: "SpendMeter | None" = None,
        scan_mode: str = "warn",
        routed: bool = False,
        routed_fallback: bool = False,
    ) -> None:
        self._inner = inner
        self._use_case = use_case
        self._provider_name = provider_name
        self._model = model
        self._timeout_secs = max(0.0, float(timeout_secs))
        self._breaker = breaker if breaker is not None else get_breaker(provider_name)
        # Day-scope spend ceiling + the meter that accumulates it. A None budget
        # means "unlimited" (the safe default so nothing is capped unexpectedly).
        self._budget = budget if budget is not None else Budget()
        # RUN-scope ceiling, checked against the AMBIENT run key (S154). Separate
        # from the day budget because they answer different questions: the day
        # budget bounds the machine, a run budget bounds one unattended run. A
        # None run budget means unlimited, so an unscoped call behaves as before.
        self._run_budget = run_budget if run_budget is not None else Budget()
        self._meter = meter if meter is not None else get_meter()
        # Outbound secret/PII scan mode: warn | redact | block. Forced to warn for
        # local providers by the wrap helper (content never leaves the machine).
        self._scan_mode = scan_mode if scan_mode in ("warn", "redact", "block") else "warn"
        # Mirror the wrapped provider's tool support so the loop treats the guard
        # exactly as it would the inner provider.
        self.supports_tools = getattr(inner, "supports_tools", False)
        # The routing query class of the CURRENT call, set by the entry point that has
        # the prompt text (stream/complete/stream_command) and stamped onto each attempt
        # audit row (MODEL-ROUTING-TELEMETRY §2, MRT-1b). "" until a call classifies.
        self._query_class = ""
        # Routing provenance for EVERY attempt this provider makes (§3.3, MRT-4). Set once at
        # wrap time by the resolution seam, not per call: the routing decision happened when the
        # ref ORDER was chosen, so it is a property of this resolved provider, not of the prompt.
        self._routed = bool(routed)
        self._routed_fallback = bool(routed_fallback)

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
        async for event in self._guarded(self._inner.stream(message), strategy="direct"):
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
        async for event in self._guarded(inner, strategy="direct"):
            yield event

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        command = self._prescan(command)
        self._classify(command)
        async for event in self._guarded(self._inner.stream_command(command), strategy="direct"):
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
        credential slip from an attack if both say ``secret_leak``."""
        result = scan_outbound(text, mode=self._scan_mode)
        if result.blocked:
            mode = FailureMode.INJECTION_BLOCKED if result.injection else FailureMode.SECRET_LEAK
            self._audit(_new_audit_id(), 1, mode, 0.0, 0, 0, False, "direct")
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
                # Names the matched pattern: §1.3's rule for the fire-path screen applies here
                # too — a block nobody can appeal against is a block nobody can debug.
                raise PromptInjectionBlocked(result.findings, result.injection_group)
            raise SecretLeakBlocked(result.findings)
        return result.text

    # ── The guard pipeline (breaker → hard timeout → audit) ──────────────

    async def _guarded(
        self, source: AsyncIterator[LLMEvent], *, strategy: str
    ) -> AsyncIterator[LLMEvent]:
        """Drive ``source`` under the breaker + a cumulative wall-clock deadline,
        recording exactly one attempt row for the whole stream.

        Success is recorded the moment ``EVENT_COMPLETE`` is observed — BEFORE it is
        yielded — because the canonical consumer (``stream_and_collect``) ``break``s
        on ``EVENT_COMPLETE`` rather than draining to ``StopAsyncIteration``: a guard
        that only recorded after loop-exit would then be suspended at the terminal
        ``yield`` forever and never audit. A ``_recorded`` flag makes the outcome
        fire exactly once; a stream that ends via ``StopAsyncIteration`` with no
        COMPLETE event still records once at loop-exit.
        """
        audit_id = _new_audit_id()

        # Breaker check BEFORE any prompt work: during an outage this refuses in
        # microseconds instead of stacking timeouts. HALF_OPEN admits one probe.
        if self._breaker.is_open():
            retry_after = self._breaker.retry_after()
            self._audit(audit_id, 1, FailureMode.CIRCUIT_OPEN, 0.0, 0, 0, False, strategy)
            # aclose the source we won't consume, so its resources release.
            await self._aclose(source)
            raise CircuitOpenError(self._provider_name, retry_after)

        # Day-scope budget check BEFORE the call: a run that has already crossed the
        # day ceiling gets its next unattended LLM call refused (§1.1 mid-run pause).
        # Cheap (reads spend.json) and skipped entirely when the budget is unlimited.
        if not self._budget.is_unlimited:
            verdict, reason = self._meter.check_day(self._budget)
            if verdict is BudgetVerdict.EXCEEDED:
                self._audit(audit_id, 1, FailureMode.BUDGET_EXCEEDED, 0.0, 0, 0, False, strategy)
                await self._aclose(source)
                totals = self._meter.day_totals()
                dim = "tokens" if "token" in reason else "dollars"
                limit = self._budget.max_tokens if dim == "tokens" else self._budget.max_dollars
                spent = totals.tokens if dim == "tokens" else totals.dollars
                raise BudgetExceededError("day", dim, float(limit), float(spent))

        # 🔴 RUN-scope budget check — the ENFORCEMENT READ S153 left open (§3.6).
        # S153 made a fire's spend ATTRIBUTABLE (`charge(run_key=…)`), and measured here:
        # `check_run` answered "exceeded (200/150)" from the second call onward while four
        # calls sailed through, because no code asked. `check_run` and
        # `run_budget_from_config` were both implemented with zero production callers, and
        # `BudgetExceededError` has always declared a "run" scope — every piece present,
        # nothing connected.
        #
        # It lives HERE, beside the day check, rather than as a `firepath` gate: run totals
        # accrue in-process as the run spends, and the fire path binds a FRESH per-fire key
        # before the first call — so a pre-fire gate would read 0.0 every time and be inert
        # by construction, the exact shape this program keeps finding.
        run_key = current_run_key()
        # The AMBIENT ceiling wins when the run bound one: a per-trigger
        # `max_cost_usd_per_run` is a tighter, run-specific promise than the operator's
        # `max_tokens_per_run` default, and the run seam is the only place that knows it.
        rb = current_run_budget()
        if rb.is_unlimited:
            rb = self._run_budget
        if run_key and not rb.is_unlimited:
            verdict, reason = self._meter.check_run(run_key, rb)
            if verdict is BudgetVerdict.EXCEEDED:
                self._audit(audit_id, 1, FailureMode.BUDGET_EXCEEDED, 0.0, 0, 0, False, strategy)
                await self._aclose(source)
                totals = self._meter.run_totals(run_key)
                dim = "tokens" if "token" in reason else "dollars"
                lim = rb.max_tokens if dim == "tokens" else rb.max_dollars
                spent = totals.tokens if dim == "tokens" else totals.dollars
                raise BudgetExceededError("run", dim, float(lim), float(spent))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_secs if self._timeout_secs > 0 else None
        started = now_ms()
        tokens_in = tokens_out = 0
        recorded = False

        try:
            while True:
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    try:
                        event = await asyncio.wait_for(source.__anext__(), remaining)
                    except StopAsyncIteration:
                        break
                else:
                    try:
                        event = await source.__anext__()
                    except StopAsyncIteration:
                        break
                if event.kind == EVENT_COMPLETE and not recorded:
                    # Terminal signal: record success NOW (the consumer may break on
                    # this event without draining), then keep yielding any trailing
                    # events a provider might still emit.
                    tokens_in = int(getattr(event, "input_tokens", 0) or 0)
                    tokens_out = int(getattr(event, "output_tokens", 0) or 0)
                    dollars = self._estimate_dollars(event, tokens_in, tokens_out)
                    self._breaker.record_success()
                    # Charge the DAY scope always, and the ambient RUN scope when one is
                    # bound (S153). `charge` has accepted `run_key=` since guardrails landed
                    # and this — its only production caller — never passed one, so
                    # `run_totals` was permanently empty and every run-scoped cap read zero.
                    # Read from a ContextVar rather than a parameter because the guard is
                    # built by `provider_bridge` from provider config and has no run identity;
                    # threading one in would touch all 33 call sites reaching the bridge.
                    self._meter.charge(
                        tokens_in + tokens_out, dollars, run_key=current_run_key() or None
                    )
                    self._audit(
                        audit_id,
                        1,
                        FailureMode.NONE,
                        now_ms() - started,
                        tokens_in,
                        tokens_out,
                        True,
                        strategy,
                        dollars=dollars,
                    )
                    recorded = True
                yield event
        except TimeoutError:
            self._breaker.record_failure()
            await self._aclose(source)
            if not recorded:
                self._audit(
                    audit_id, 1, FailureMode.TIMEOUT, now_ms() - started, 0, 0, False, strategy
                )
            raise ModelCallTimeout(
                f"model call for use case {self._use_case!r} (provider "
                f"{self._provider_name!r}) exceeded {self._timeout_secs:.0f}s"
            ) from None
        except (asyncio.CancelledError, GeneratorExit):
            # Cooperative cancellation / caller closed the guard mid-stream: not a
            # provider failure — don't trip the breaker. If the terminal COMPLETE was
            # already seen (the common case: consumer breaks then closes the gen), the
            # success was already recorded; otherwise record nothing (genuine abort).
            await self._aclose(source)
            raise
        except Exception:
            if not recorded:
                self._breaker.record_failure()
                self._audit(
                    audit_id,
                    1,
                    FailureMode.PROVIDER_ERROR,
                    now_ms() - started,
                    tokens_in,
                    tokens_out,
                    False,
                    strategy,
                )
            raise

        # Stream ended via StopAsyncIteration. If no COMPLETE event ever arrived,
        # record the (clean) outcome once here so a provider that omits COMPLETE is
        # still audited exactly once.
        if not recorded:
            self._breaker.record_success()
            self._audit(
                audit_id,
                1,
                FailureMode.NONE,
                now_ms() - started,
                tokens_in,
                tokens_out,
                True,
                strategy,
            )

    def _estimate_dollars(self, event: LLMEvent, tokens_in: int, tokens_out: int) -> float:
        """Dollar estimate for one completed call. Provider-reported ``cost_usd``
        wins when present (non-zero); otherwise the static pricing table
        (``pricing.estimate_cost``) derives it — 0.0 for an unpriced model, an
        honest 'unknown', never a guess."""
        reported = float(getattr(event, "cost_usd", 0.0) or 0.0)
        if reported > 0.0:
            return reported
        try:
            from personalclaw.pricing import estimate_cost

            return estimate_cost(
                self._model,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cache_read_tokens=int(getattr(event, "cache_read_tokens", 0) or 0),
                cache_creation_tokens=int(getattr(event, "cache_creation_tokens", 0) or 0),
            )
        except Exception:
            return 0.0

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
        dollars: float = 0.0,
    ) -> None:
        rec = AttemptRecord(
            audit_id=audit_id,
            ts=time.time(),
            use_case=self._use_case,
            provider=self._provider_name,
            model=self._model,
            attempt=attempt,
            failure_mode=mode.value,
            latency_ms=round(latency_ms, 1),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            dollars_est=round(dollars, 6),
            # Estimated unless the provider reported a real cost_usd (which
            # _estimate_dollars prefers); a heuristic-derived value is flagged.
            estimated=True,
            passed=passed,
            strategy=strategy,
            query_class=self._query_class,
            routed=self._routed,
            routed_fallback=self._routed_fallback,
            # WHICH SUBSYSTEM asked (`G47`). Read from a ContextVar for the same reason
            # `current_run_key()` above is: this guard is built by `provider_bridge` from
            # provider config and never sees its caller. "" when nothing bound one.
            caller=current_caller(),
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


def _is_local_provider(provider: ModelProvider) -> bool:
    """Best-effort: is ``provider`` local-only (content never leaves the machine)?

    Ollama and a base_url pointing at loopback/private are local — their outbound
    scan is forced to ``warn`` (§2.2: local content stays on the machine, so a hard
    block/redact would be pointless friction). Unknown → treat as REMOTE (the
    conservative default: a hosted provider gets the real scan mode)."""
    base_url = str(getattr(provider, "_base_url", "") or "").lower()
    if base_url:
        if any(h in base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1")):
            return True
    type_name = type(provider).__name__.lower()
    return "ollama" in type_name


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
    timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
    routed: bool = False,
    routed_fallback: bool = False,
) -> ModelProvider:
    """Wrap ``provider`` in a :class:`ModelCallGuard` for a non-interactive call.

    Idempotent: an already-guarded provider is returned unchanged (defends against
    double-wrapping if two resolution layers both reach for the guard). A local
    provider's scan mode is forced to ``warn`` regardless of ``scan_mode``.
    """
    if isinstance(provider, ModelCallGuard):
        return provider
    effective_scan = "warn" if _is_local_provider(provider) else scan_mode
    return ModelCallGuard(
        provider,
        use_case=use_case,
        provider_name=provider_name,
        model=model,
        budget=budget,
        run_budget=run_budget,
        meter=meter,
        scan_mode=effective_scan,
        breaker=breaker,
        timeout_secs=timeout_secs,
        routed=routed,
        routed_fallback=routed_fallback,
    )
