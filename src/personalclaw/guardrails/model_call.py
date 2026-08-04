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

from personalclaw.guardrails.audit import AttemptRecord, now_ms, record_attempt
from personalclaw.guardrails.breaker import CircuitBreaker, get_breaker
from personalclaw.guardrails.budgets import (
    Budget,
    BudgetVerdict,
    SpendMeter,
    current_run_key,
    get_meter,
)
from personalclaw.guardrails.failure import (
    BudgetExceededError,
    CircuitOpenError,
    FailureMode,
    ModelCallTimeout,
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
        meter: "SpendMeter | None" = None,
        scan_mode: str = "warn",
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
        self._meter = meter if meter is not None else get_meter()
        # Outbound secret/PII scan mode: warn | redact | block. Forced to warn for
        # local providers by the wrap helper (content never leaves the machine).
        self._scan_mode = scan_mode if scan_mode in ("warn", "redact", "block") else "warn"
        # Mirror the wrapped provider's tool support so the loop treats the guard
        # exactly as it would the inner provider.
        self.supports_tools = getattr(inner, "supports_tools", False)

    # ── The intercepted generation paths ────────────────────────────────

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        message = self._prescan(message)
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
        inner = self._inner.complete(
            messages, tools=tools, model=model, reasoning_effort=reasoning_effort
        )
        async for event in self._guarded(inner, strategy="direct"):
            yield event

    async def stream_command(self, command: str) -> AsyncIterator[LLMEvent]:
        command = self._prescan(command)
        async for event in self._guarded(self._inner.stream_command(command), strategy="direct"):
            yield event

    def _prescan(self, text: str) -> str:
        """Scan an outbound prompt for secrets/PII and apply the mode ladder.

        Returns the (possibly redacted) text to send. Raises ``SecretLeakBlocked``
        in block mode when there are findings — audited as ``secret_leak`` and never
        retried (retrying would let a payload brute-force the scan)."""
        result = scan_outbound(text, mode=self._scan_mode)
        if result.blocked:
            self._audit(_new_audit_id(), 1, FailureMode.SECRET_LEAK, 0.0, 0, 0, False, "direct")
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
                    ),
                )
            except Exception:
                logger.debug("SEL scan-block audit failed", exc_info=True)
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
        record_attempt(
            AttemptRecord(
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
            )
        )

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

    def context_usage_pct(self) -> float:
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
    meter: SpendMeter | None = None,
    scan_mode: str = "warn",
    breaker: CircuitBreaker | None = None,
    timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
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
        meter=meter,
        scan_mode=effective_scan,
        breaker=breaker,
        timeout_secs=timeout_secs,
    )
