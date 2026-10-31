"""The native loop's five phases over one state record (#4015).

A mixin of :class:`~personalclaw.agents.native.runtime.NativeAgentRuntime`, whose ``stream`` opens
the turn (:meth:`LoopPhases._open_turn`) and then runs the phases of each step in order, over the
turn's :class:`~personalclaw.agents.native.loop_state.LoopState`:

1. :meth:`~LoopPhases._compact_phase` opens the step: a stop that came between steps ends the turn,
   else the step counts and the history is compacted when it crossed the threshold;
2. :meth:`~LoopPhases._infer_phase`: one answer from the model, with the turn's one retry, the
   overflow's compaction and the first step's failover;
3. :meth:`~LoopPhases._end_phase`: an answer with no tool calls ends the turn, unless a steer or
   the reply it still owes asks for another step;
4. :meth:`~LoopPhases._dispatch_phase`: the step's tool calls run;
5. :meth:`~LoopPhases._steer_phase`: what the user sent meanwhile is taken.

Each phase yields its events as they happen and sets ``state.next``
(:class:`~personalclaw.agents.native.loop_state.Next`) as its last act: CONTINUE to the next phase,
BREAK to the next step, RETURN when it said the turn's ending. So each later change to the loop
(the record, the in-turn recovery, the named limits, a steer's skip, a model change at the next
step) changes one phase.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from personalclaw import run_processes, turn_streams
from personalclaw.agents.native.failover import FAILOVER_MODES, TurnFallback, resending_cannot_help
from personalclaw.agents.native.local_turn import take_local_turn
from personalclaw.agents.native.loop_state import CANCELLED_BEFORE_RUN, LoopState, Next
from personalclaw.agents.native.owed_reply import (
    CUT_CALLS_PER_TURN,
    OwedReply,
    answered_row,
    capped_at,
    cut_call_words,
    cut_in_a_call,
)
from personalclaw.cancellation import CancelScope
from personalclaw.guardrails.audit import now_ms
from personalclaw.guardrails.failure import (
    FailureMode,
    correction_note,
    inference_failure_mode,
    is_retryable,
)
from personalclaw.guardrails.loop_breaker import LoopBreaker
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_MODEL_SUBSTITUTION,
    EVENT_RETRYING,
    EVENT_STEER,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.llm.prompt_cache import (
    VOLATILE_KEY,
    PromptCache,
    effective_cache_mode,
    is_turn_note,
    turn_note_message,
)
from personalclaw.tool_providers.portable_schema import (
    ToolSchemaRejected,
    tools_named_in_rejection,
)
from personalclaw.workflows.compaction import is_context_overflow

if TYPE_CHECKING:
    from personalclaw.agents.native.failover import ModelFailover
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.context_headroom import Window

logger = logging.getLogger("personalclaw.agents.native.runtime")


def _retry_backoff() -> float:
    """The pause before the turn's one retry: the runtime module's constant, read when the retry is
    made, so whoever sets it there is heard."""
    from personalclaw.agents.native import runtime

    return runtime._INFERENCE_RETRY_BACKOFF_SECS


class LoopPhases:
    """The phases of a native turn's steps, and the opening of the turn."""

    # The runtime's own state, set in ``__init__`` and at each turn's start: declared here so the
    # phases are checked against the runtime's types, not against what their assignments imply.
    _messages: list[dict]
    _cancel: CancelScope
    _breaker: LoopBreaker
    _model: Any
    _definition: AgentRuntimeDefinition
    _fallback: TurnFallback
    failover: ModelFailover | None
    _turn_seq: int
    _turn_home: tuple[Any, str] | None
    _owed: OwedReply
    _stop_note: str
    _steers_injected: int
    _steer_pending: list[str]
    _staged_images: list[str]
    _turn_images: list[str]
    _staged_parts: dict[str, int] | None
    _message_parts: list[tuple[dict, dict[str, int], int]]
    _turn_message: dict | None
    _turn_window: Window | None
    _schema_budget: int | None
    _last_context_pct: float | None
    _last_tools: list | None
    _reasoning_effort: str
    _session_key: str
    _unattended: bool
    _surface: str

    if TYPE_CHECKING:

        @property
        def _cancelled(self) -> bool: ...

        @property
        def served_model_ref(self) -> str: ...

        async def _follow_tool_surface(self) -> None: ...

        def _tool_schema_budget(self, window: Window) -> int | None: ...

        async def _prepare_turn_tools(self, message: str) -> tuple[list[dict] | None, str]: ...

        def _stop_reason_for_cancel(self) -> str: ...

        def _final_stop_reason(self, usage: AgentEvent | None) -> str: ...

        def _maybe_compact(self) -> tuple[int, int] | None: ...

        def _compact_now(self, measured_pct: float | None) -> tuple[int, int]: ...

        def _compacted_on_its_own(self, before: int, after: int) -> AgentEvent: ...

        def _request_messages(self, mode: PromptCache) -> list[dict]: ...

        def _prompt_cache_enabled(self) -> bool: ...

        def _audit_inference_attempt(self, mode: FailureMode, **attempt: Any) -> str: ...

        def _resolve_name(self, name: str) -> str: ...

        async def _fail_over(self, exc: BaseException, *, tools: list | None) -> bool: ...

        def _tools_kept(self, tools: Any) -> Any: ...

        def _take_steers(self) -> list[str]: ...

        def _execute_tool_batch(
            self, tool_calls: list[AgentEvent], *, cut: str = ""
        ) -> AsyncIterator[AgentEvent]: ...

        @staticmethod
        def _assistant_msg(
            text: str, tool_calls: list[AgentEvent], reasoning: dict | None = None
        ) -> dict: ...

        @staticmethod
        def _tool_result_msg(call: AgentEvent, result_str: str) -> dict: ...

    # ── the turn's opening ──

    async def _open_turn(self, st: LoopState, message: str) -> None:
        """Begin the turn on *st*: its message into the history, its tools and their note, the
        window it is served with, and the model it starts on.

        ``message`` is the full, context-built turn-0 prompt the chat runner already assembled.
        """
        self._turn_seq += 1
        st.turn = self._turn_seq
        self._cancel.begin_turn(run_processes.turn_owner(self._session_key))
        self._breaker.reset()
        self._stop_note = ""
        self._steers_injected = 0
        # Cleared, not carried: the dispatcher reads `undelivered_steers()` at the END of
        # the previous turn and requeues what it finds, so replaying it here would deliver
        # a second copy of a steer the user can already see in the queue strip.
        self._steer_pending.clear()
        self._turn_images, self._staged_images = self._staged_images, []
        self._messages.append({"role": "user", "content": message})
        self._turn_message = self._messages[-1]
        if self._staged_parts:
            self._message_parts.append((self._turn_message, self._staged_parts, len(message)))
        self._staged_parts = None

        # A tool installed, removed or switched off since the last turn reaches this one.
        await self._follow_tool_surface()
        from personalclaw.context_headroom import resolve_window

        self._turn_window = await resolve_window(serving=self)
        self._schema_budget = self._tool_schema_budget(self._turn_window)
        st.tools, turn_note = await self._prepare_turn_tools(message)
        # The last turn's note leaves as this turn's arrives: it described the tools on offer
        # THEN, and kept in the history each turn carried every earlier turn's catalog as well
        # as its own — a request that grew by a whole catalog a turn and said nothing new.
        self._messages[:] = [m for m in self._messages if not is_turn_note(m)]
        if turn_note:
            # SYSTEM role: runtime metadata, not something the user said. VOLATILE because
            # the tool catalog + group stubs change every turn and prompt caches match an EXACT
            # prefix, so a provider whose system prompt leads the served prompt (Anthropic's
            # ``system=``) carries the note on the request's last user turn, after every cache
            # checkpoint, never as a turn of its own (a model answers that as the user's). Fenced
            # as the runtime's (``system_note_text``), so it reads as such wherever it lands.
            self._messages.append(turn_note_message(turn_note))
        # ONE request for the reply per turn (`owed_reply`): a model that goes quiet a second time
        # ends the turn, and the surface says the turn has no answer.
        self._owed = st.owed
        # The model this turn starts on, put back when it ends: a turn that fell back must not
        # leave the next one answering on the fallback with nothing saying so (nor a previous
        # turn whose cleanup has not run yet, see `stream`'s `finally`).
        if self._turn_home is not None:
            self._model, self._definition.model = self._turn_home
        st.home = self._turn_home = (self._model, self._definition.model)
        self._fallback.begin(self.failover)

    def _cache_mode(self) -> PromptCache:
        """The prompt-cache mode of the model the next call goes to.

        The provider's graded mode is read off the instance (mirroring the supports_tools getattr),
        and the user's switch folds into the SAME value (``effective_cache_mode``): off → NONE,
        which is the untouched-list path. No second code path, and no branch that skips the call.
        """
        return effective_cache_mode(
            getattr(self._model, "prompt_cache", PromptCache.NONE),
            enabled=self._prompt_cache_enabled(),
        )

    # ── 1. compact ──

    async def _compact_phase(self, st: LoopState) -> AsyncIterator[AgentEvent]:
        """Open a step. A stop that came between steps ends the turn here, before the step counts.

        Otherwise the step counts and, when context crosses the threshold, the history is
        compacted (no-LLM tool-output pruning pre-pass → 4-region → structured summary).
        Anti-thrashing skips it if recent passes barely helped. ACP backends own their own
        compaction; this is the native loop's. Said when it happens, in /compact's words: the
        history the rest of this conversation is answered from has just changed.
        """
        if self._cancelled:
            # Attribute what was ALREADY SPENT before the stop: a stop between ReAct cycles that
            # threw away every token the earlier cycles burned is a stop that hides spend, and
            # that is why users distrust the button. The breaker's sentence, when it is what
            # stopped the turn, is the ending's text.
            yield st.ending(
                self._stop_reason_for_cancel(),
                text=self._stop_note,
                context_usage_pct=self._last_context_pct,
                served_model_ref=self.served_model_ref,
            )
            st.next = Next.RETURN
            return
        st.steps += 1
        compacted = self._maybe_compact()
        if compacted is not None:
            yield self._compacted_on_its_own(*compacted)
        st.next = Next.CONTINUE

    # ── 2. infer ──

    async def _infer_phase(self, st: LoopState) -> AsyncIterator[AgentEvent]:
        """Stream one stateless completion over the full history, as the step's answer.

        The native loop's ONE correction-retry (#2287/#252): a transient inference failure
        (provider 5xx, dropped connection, timeout) that arrives BEFORE anything user-visible
        streamed is retried once per agent turn (:meth:`_recover`); everything else propagates
        unchanged:

          - a mid-stream failure after visible text/thinking (a retry would re-stream and
            duplicate what the user already read),
          - a failure after a tool call arrived (the model did work),
          - a second failure in the same turn,
          - every NON_RETRYABLE mode (open breaker, budget ceiling, injection/secret-leak —
            retrying defeats each guard),
          - a model that did not start answering within its timeout (FirstTokenTimeout: the
            same request is as slow the second time).

        max_tokens truncation is a SUCCESSFUL stream carrying a stop_reason, so it never enters
        this path: one that wrote nothing is asked again in another form (`owed_reply`), and
        recorded as `output_cap`. Deliberately NOT wired to the failure breaker, the structural-
        loop detector, or procedural-memory outcomes: an infrastructure blip is not evidence about
        the model's behaviour, mirroring the guard's own isolation contract.
        """
        st.text, st.calls, st.usage = "", [], None
        # Prompt-cache middleware: mark a cacheable prefix. NONE → the same object is handed
        # back (byte-identical for an undeclared provider); AUTOMATIC → also unchanged (no marker
        # needed); EXPLICIT → a NEW list with one neutrally-hinted message, its own adapter
        # translating the hint. self._messages itself is never mutated.
        st.mode = self._cache_mode()
        logger.debug("native: prompt-cache mode %s", getattr(st.mode, "value", st.mode))
        st.request = self._request_messages(st.mode)
        self._last_tools = st.tools
        while True:
            st.streamed = False
            st.attempt_started = now_ms()
            try:
                async with turn_streams.closing_stream(self._answer(st)) as answer:
                    async for ev in answer:
                        yield ev
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Either raises, or says what it does and leaves the next attempt's request.
                async with turn_streams.closing_stream(self._recover(st, exc)) as recovery:
                    async for ev in recovery:
                        yield ev
                continue
            st.answered_ms = now_ms()
            # A capped call is recorded here when no guard did (a turn a person watches), with the
            # tokens and cost its usage says, and the turn's usage row names its row
            # (``attempt_audit``).
            answered, passed, unrecorded = answered_row(st.usage, st.text, st.calls)
            if st.retried or st.fallbacks or unrecorded:
                row = self._audit_inference_attempt(
                    answered,
                    attempt=1 + int(st.retried) + st.fallbacks,
                    started_ms=st.attempt_started,
                    passed=passed,
                    fallback=st.fallbacks > 0,
                    usage=st.usage if unrecorded else None,
                )
                if unrecorded and row:
                    st.totals.audit_ids.append(row)
            break
        self._record_answer(st)
        st.next = Next.CONTINUE

    async def _answer(self, st: LoopState) -> AsyncIterator[AgentEvent]:
        """One attempt at the step's answer: what is visible streams as it comes, the calls and
        the usage are kept on *st*."""
        # Its place in line on a local model; only a first inference moves on.
        local_turn = await take_local_turn(
            self._model,
            served_ref=self.served_model_ref,
            session_key=self._session_key,
            unattended=self._unattended,
            next_ref=self._fallback.next_ref() if st.steps == 1 else "",
        )
        try:
            async with turn_streams.closing_stream(
                self._model.complete(
                    st.request,
                    tools=st.tools,
                    model=self._definition.model or None,
                    reasoning_effort=st.owed.effort(self._reasoning_effort),
                )
            ) as answer:
                async for ev in answer:
                    if self._cancelled:
                        break
                    if self._fallback.pending:
                        # A fallback is answering: said before anything it streams.
                        yield AgentEvent(
                            kind=EVENT_MODEL_SUBSTITUTION,
                            text=self._fallback.take_pending(),
                        )
                    st.totals.events += 1
                    if ev.kind in (EVENT_TEXT_CHUNK, EVENT_THINKING_CHUNK):
                        if ev.kind == EVENT_TEXT_CHUNK:
                            st.text += ev.text
                        st.streamed = True
                        yield ev
                    elif ev.kind == EVENT_TOOL_CALL:
                        st.calls.append(ev)
                    elif ev.kind == EVENT_COMPLETE:
                        st.usage = ev
        finally:
            if local_turn is not None:
                local_turn.release()

    async def _recover(self, st: LoopState, exc: Exception) -> AsyncIterator[AgentEvent]:
        """What a failed attempt does next: raise *exc*, or say what happens and leave the next
        attempt's request on *st* — the history compacted, the same model asked once more, or the
        next model of the turn's chain asked."""
        # ── OVERFLOW RECOVERY, ahead of the classifier ──
        # A LENGTH rejection is the one inference failure whose retry has to change the PROMPT,
        # and it must be caught before inference_failure_mode: that collapses a vendor 400 to
        # PROVIDER_ERROR, which is retryable, so the generic path below would spend the turn's
        # single retry re-sending the same oversized history — the identical failure, one
        # backoff later. The gate is the generic `can_retry` conjunction with
        # is_retryable(fmode) swapped for the overflow test, so no existing guard is relaxed: a
        # retry after visible text would re-stream what the user already read, and after a tool
        # call the model did real work.
        overflow = is_context_overflow(exc)
        if overflow and st.may_retry(self._cancelled):
            before, after = self._compact_now(self._last_context_pct)
            if after < before:
                self._audit_inference_attempt(
                    inference_failure_mode(exc),
                    attempt=1,
                    started_ms=st.attempt_started,
                    passed=False,
                )
                st.retried = True
                logger.warning(
                    "native: context overflow — compacted %d→%d chars, retrying once: %s",
                    before,
                    after,
                    exc,
                )
                # REBUILD from the compacted history, re-reading the generation _compact_now
                # just bumped. Never append to the existing request the way the correction-note
                # path below does: that retries with a LARGER prompt, which for this failure is a
                # guaranteed second failure. _compact_now owns the cache-prefix bump and the
                # structural re-arm; the anti-thrashing saves list is deliberately NOT appended
                # here (see its trap note — that list is the automatic trigger's own bookkeeping,
                # and polluting it latches threshold compaction off).
                st.request = self._request_messages(st.mode)
                st.text, st.usage = "", None
                yield self._compacted_on_its_own(before, after)
                return
            # after == before: the pass reclaimed nothing — a truthful outcome, not a failure.
            # Retrying an identical prompt would burn a call to learn what compaction already
            # proved, so fall through to the raise below.
        fmode = inference_failure_mode(exc)
        can_retry = (
            st.may_retry(self._cancelled)
            and is_retryable(fmode)
            # A LENGTH rejection never enters the BLIND retry. Narrowing here rather than
            # widening is_retryable/FailureMode to carry overflow keeps the generic path from
            # ever re-sending unchanged history: the only retry an overflow gets is the
            # compacting one above, and only when it reclaimed something.
            and not overflow
            # Nor a failure the same request fails again (a model that did not start answering
            # in time, a busy local model, a cut call): the next model of the chain may still
            # answer (below).
            and not resending_cannot_help(exc, can_move_on=bool(self._fallback.queue))
        )
        self._audit_inference_attempt(
            fmode,
            attempt=1 + int(st.retried) + st.fallbacks,
            started_ms=st.attempt_started,
            passed=False,
            fallback=st.fallbacks > 0,
        )
        self._refuse_rejected_tools(st, exc)
        if not can_retry:
            async with turn_streams.closing_stream(
                self._move_on(st, exc, fmode, overflow)
            ) as moved:
                async for ev in moved:
                    yield ev
            return
        st.retried = True
        # `%r`, not `%s`: httpx.ReadError and every timeout stringify to "", which logged
        # "retrying once: " with nothing after it.
        logger.warning(
            "native: inference attempt failed (%s) — retrying once: %r",
            fmode.value,
            exc,
        )
        note = correction_note(fmode)
        if note:
            # Same volatile-tail contract as the turn note: never ahead of the stable cacheable
            # prefix.
            st.request = [*st.request, {"role": "user", "content": note, VOLATILE_KEY: True}]
        # Said before the backoff and again as the retry's call is made, so a surface shows the
        # retry from what was measured, never from a timer.
        yield AgentEvent(
            kind=EVENT_RETRYING,
            tool_meta={"attempt": 2, "of": 2, "next_in": _retry_backoff(), "error": exc},
        )
        await asyncio.sleep(_retry_backoff())
        yield AgentEvent(kind=EVENT_RETRYING, tool_meta={"attempt": 2, "of": 2, "started": True})
        st.text, st.usage = "", None

    def _refuse_rejected_tools(self, st: LoopState, exc: Exception) -> None:
        """Raise ``ToolSchemaRejected`` when the provider refused one of THIS request's tool
        definitions: the identical request fails identically, so there is no retry, and the raw
        dump is replaced by a sentence naming the tool (the seam should have kept it out — so it
        is PersonalClaw's bug, and says so)."""
        named = tools_named_in_rejection(str(exc), st.tools or [])
        rejected = [self._resolve_name(n) for n in named]  # the real tools
        if rejected:
            from personalclaw.tool_providers import tool_prefs

            logger.warning(
                "native: the provider rejected the tool definition(s) %s — not retrying: %r",
                rejected,
                exc,
            )
            raise ToolSchemaRejected(
                rejected,
                can_turn_off=not any(tool_prefs.is_locked(n) for n in rejected),
            ) from exc

    async def _move_on(
        self, st: LoopState, exc: Exception, fmode: FailureMode, overflow: bool
    ) -> AsyncIterator[AgentEvent]:
        """The turn's retry is spent: the next model of its chain may answer instead, while
        nothing of the turn has been shown, for a failure another model could get past. Raises
        *exc* when none may."""
        if not (
            fmode in FAILOVER_MODES
            and not overflow
            and st.steps == 1
            and not st.streamed
            and not st.calls
            and not self._cancelled
            and await self._fail_over(exc, tools=st.tools)
        ):
            raise exc
        st.fallbacks += 1
        # Said while the next model is asked, as the measured event it is.
        attempt = 1 + int(st.retried) + st.fallbacks
        yield AgentEvent(
            kind=EVENT_RETRYING,
            tool_meta={
                "attempt": attempt,
                "of": attempt + (1 if self._fallback.next_ref() else 0),
                "model": self.served_model_ref.partition(":")[2] or self.served_model_ref,
            },
        )
        # The fallback's own cache mode, and the history without the correction note the failed
        # model's retry was given.
        st.mode = self._cache_mode()
        st.request = self._request_messages(st.mode)
        st.text, st.usage = "", None

    def _record_answer(self, st: LoopState) -> None:
        """Keep the step's answer: what it spent, and the assistant turn (text + any tool calls,
        and the reasoning record its model wrote, stamped with that model) in the history."""
        st.owed.answered(st.text)
        # A model that refused the tools this call offered it is offered none again.
        st.tools = self._tools_kept(st.tools)
        if st.usage is not None:
            st.totals.add(st.usage)
            # ``is not None``, not truthiness: a provider reporting a real 0% must update the
            # gauge, and only an absent report must not.
            if st.usage.context_usage_pct is not None:
                self._last_context_pct = st.usage.context_usage_pct
        st.totals.tool_calls += len(st.calls)
        self._messages.append(
            self._assistant_msg(
                st.text,
                st.calls,
                st.usage.reasoning if st.usage is not None else None,
            )
        )

    # ── 3. end ──

    async def _end_phase(self, st: LoopState) -> AsyncIterator[AgentEvent]:
        """A model turn with no tool calls ends the agent turn…"""
        # …UNLESS the user steered while that text was streaming. The steer drain used to live
        # only after the tool batch, so a turn that called NO tools — plain prose, the most
        # common shape — ended here and the steer was silently discarded even though the API had
        # already answered {"steered": true}. Draining here keeps the turn alive for one more
        # inference so the steer lands inside the SAME answer.
        if not st.calls and not self._cancelled and (taken := self._take_steers()):
            for steer in taken:
                st.steers.append(steer)
                yield AgentEvent(kind=EVENT_STEER, text=steer)
            st.next = Next.BREAK
            return
        # …or when the model ran tools this turn and has written nothing at all (or ran out of
        # output room first). It is asked once, with the results it already has; a second
        # silence ends the turn, and the surface says it has no answer.
        if not st.calls and st.owed.ask(
            tool_calls_run=st.totals.tool_calls,
            usage=st.usage,
            cancelled=self._cancelled,
            surface=self._surface,
        ):
            # Not kept: it says nothing, and a provider may refuse an empty assistant message in
            # the history the next request replays.
            self._messages.pop()
            logger.warning(
                "native: %s (session=%s)", st.owed.asking(st.totals.tool_calls), self._session_key
            )
            st.next = Next.BREAK
            return
        if not st.calls or self._cancelled:
            # If we're stopping with tool calls still pending (cancelled mid-turn — watchdog
            # wedged-turn recovery / circuit-breaker), the assistant message the infer phase kept
            # carries unanswered tool_calls. Leave them unpaired and the NEXT turn's history
            # replay breaks every tool-using provider (Bedrock Converse rejects an unanswered
            # toolUse outright). Pair each with a synthetic result so history stays well-formed
            # across cycles.
            if st.calls and self._cancelled:
                for call in st.calls:
                    self._messages.append(self._tool_result_msg(call, CANCELLED_BEFORE_RUN))
            yield st.ending(
                self._final_stop_reason(st.usage),
                text=self._stop_note if self._cancelled else "",
                output_cap=0 if self._cancelled else capped_at(st.usage),
                context_usage_pct=self._last_context_pct,
                # Read before `stream`'s `finally` puts the turn's own model back.
                served_model_ref=self.served_model_ref,
            )
            st.next = Next.RETURN
            return
        st.next = Next.CONTINUE

    # ── 4. dispatch ──

    async def _dispatch_phase(self, st: LoopState) -> AsyncIterator[AgentEvent]:
        """Run the step's calls.

        Each call is a sub-generator that yields its UI card, (maybe) a permission request it
        parks on, the result card, and appends the tool-result message to history itself. Calls
        whose resource sets are disjoint run CONCURRENTLY; everything else keeps the order the
        model asked for. The QUEUED-BUT-UNSTARTED drop lives one level down, in `_execute_wave`
        at the per-call dispatch decision — see that method. It cannot live here: a check around
        this call would only fire between BATCHES, which is exactly the hole the per-call check
        closes.
        """
        # A call the model was cut off in the middle of writing, at its output limit: its card says
        # so, and whether the turn tries again. The second such cut ends the turn there, saying
        # so, since asking again at the same limit would only meet it again.
        cut = ""
        if cut_in_a_call(st.calls):
            st.cut_calls += 1
            cut = cut_call_words(
                max(0.0, st.answered_ms - st.attempt_started) / 1000.0,
                again=st.cut_calls < CUT_CALLS_PER_TURN,
            )
        async for ev in self._execute_tool_batch(st.calls, cut=cut):
            st.totals.events += 1
            yield ev
        if cut and st.cut_calls >= CUT_CALLS_PER_TURN and not self._cancelled:
            yield st.ending(
                self._final_stop_reason(st.usage),
                output_cap=capped_at(st.usage),
                context_usage_pct=self._last_context_pct,
                served_model_ref=self.served_model_ref,
            )
            st.next = Next.RETURN
            return
        st.next = Next.CONTINUE

    # ── 5. steer ──

    async def _steer_phase(self, st: LoopState) -> AsyncIterator[AgentEvent]:
        """Take what the user sent mid-turn (queue-steering).

        It lands HERE, at the model boundary AFTER the tool batch (so tool-result pairing is
        intact), as fresh user input the next inference sees. Capped per turn so a flood can't
        extend one turn forever. Steer mode only; followup/collect/interrupt are handled by the
        runner before the turn even reaches the loop. Then the next step re-infers with the
        tool results in context.
        """
        for steer in self._take_steers():
            st.steers.append(steer)
            yield AgentEvent(kind=EVENT_STEER, text=steer)
        st.next = Next.CONTINUE
