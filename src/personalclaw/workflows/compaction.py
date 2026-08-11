"""The two-layer context-compaction ladder for LLM-backed workflow nodes (WV-12).

A long-horizon node's prompt GROWS. `infer` and the judge branch of `gate` each send
one bounded prompt built from the template text plus everything the run has accumulated
by then — the carried handoff/carryover/decisions block (`_with_carried_context`), a
retry hint, and whatever sibling views and prior outputs its bindings resolve to. Cycle
50 of a loop carries 50 cycles of findings. Before this module the engine sent that
prompt as-is and let the provider decide: a node that had done real work died on a
context-length error the engine could have measured and avoided one call earlier.

**Two layers, in order.**

1. **Proactive**, before the call: measure the prompt against the bound model's window
   and compact at ~80% of it. Cheap, and it keeps the failure from happening.
2. **Error-triggered**, after a failed call: if the provider rejected the prompt for
   LENGTH specifically, re-compact aggressively and retry ONCE before the node fails.
   Layer 2 exists because layer 1's measurement is an APPROXIMATION (see
   `CHARS_PER_TOKEN`) — it is the backstop for the cases the estimate gets wrong, not a
   duplicate of it.

**Reuse, not reimplementation.** All the actual compaction is
`personalclaw.context_compaction.compact` — the same seam the native agent loop uses at
`agents/native/runtime.py`. That module owns the tool-output pruning pre-pass, the
4-region structure, the ``[CONTEXT COMPACTION — REFERENCE ONLY]`` prefix guard, tool-pair
integrity and the anti-thrashing rule. Re-deriving any of that here would be a second
implementation that drifts from the first, and its docstring is explicit that an orphaned
tool-result *breaks the provider* — so getting it wrong is a live failure, not a style
question.

**Why a prompt is segmented into messages.** `compact()` types on the native loop's
message list; a workflow node has ONE prompt string. So the string is split on its
paragraph boundaries into pseudo-messages, compacted, and rejoined. That is a real
structural match, not a workaround: a node prompt is assembled by CONCATENATION (carried
context first, the task instruction last), so protecting the head and the tail and folding
the middle keeps exactly the two things that must survive — the framing the reader needs
before the instruction, and the instruction itself — and digests the accumulated bulk
between them. A prompt with no middle left to fold is returned unchanged, because there is
then nothing that can be dropped safely.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from personalclaw.context_compaction import compact, should_compact, total_chars
from personalclaw.model_windows import model_context_window

logger = logging.getLogger(__name__)

#: Chars per token. The model window is denominated in TOKENS and `total_chars` counts
#: CHARACTERS, so the comparison needs a bridge and this is it — ~4 chars/token is the
#: standard English-prose ratio.
#:
#: 🔴 It is an APPROXIMATION and it is wrong in a knowable direction. Denser content
#: (code, JSON, paths — exactly what a workflow prompt carries) runs nearer 3 chars/token,
#: so 4 UNDER-estimates the real token count and the proactive layer therefore fires
#: LATER than it ideally would; prose runs 4-5 and it fires early, which costs only a
#: summarizer call. Deliberately not "solved" with a tokenizer: the right tokenizer is
#: per-model, loading one on the hot path of every node call to refine a threshold whose
#: whole job is to be approximately-early is a bad trade, and the aggressive
#: error-triggered layer below is precisely the backstop for the calls this estimate
#: mis-judges. The two layers are one mechanism: an estimate plus a certainty.
CHARS_PER_TOKEN = 4

#: Compact proactively at this fraction of the bound window ("~80%").
COMPACT_AT_FRACTION = 0.80

#: Proactive protection. Head keeps the opening framing (the carried-context header and
#: the first instruction block); tail keeps the live task, which is appended last.
_PROACTIVE_HEAD = 2
_PROACTIVE_TAIL = 4
#: Aggressive protection: the bare minimum that still leaves a prompt meaning anything —
#: the opening frame and the actual instruction.
_AGGRESSIVE_HEAD = 1
_AGGRESSIVE_TAIL = 1


def prompt_char_budget(
    model_ref: str, *, fraction: float = COMPACT_AT_FRACTION, chars_per_token: int = CHARS_PER_TOKEN
) -> int:
    """The prompt size, IN CHARS, at which the proactive layer should compact.

    `model_context_window` is the one shared reader for the window table (the provider
    adapters use it too), so this never hand-rolls a window per model. An unresolvable
    ref falls back to that module's conservative default rather than to "unbounded" — a
    missing entry must not disable the ladder.
    """
    window_tokens = model_context_window(model_ref)
    return int(window_tokens * chars_per_token * fraction)


def segment_prompt(prompt: str) -> list[dict]:
    """A prompt string → the pseudo-message list `compact()` types on.

    Split on blank lines: paragraph blocks are the boundary a concatenated prompt
    actually has, and they are separator-agnostic (they subsume the ``---`` fence
    `_with_carried_context` joins on without depending on it). Empty blocks are dropped
    so a run of blank lines cannot pad the head/tail protection with nothing.
    """
    blocks = [b.strip() for b in (prompt or "").split("\n\n")]
    return [{"role": "user", "content": b} for b in blocks if b]


def join_segments(messages: list[dict]) -> str:
    """The inverse of `segment_prompt`: pseudo-messages → one prompt string."""
    return "\n\n".join(str(m.get("content", "")) for m in messages if str(m.get("content", "")))


def _placeholder_body(middle: list[dict]) -> str:
    """The drop-with-placeholder body: what was removed, stated plainly.

    Reached only when a supplied summarizer RAISED. Deliberately a placeholder and not a
    silent fallback to `compact()`'s deterministic digest: the summarizer failing means
    something is wrong, and a loud marker naming the loss is honest where quietly
    substituting a weaker artifact would read, downstream, as a complete summary. The
    node survives either way — which is the whole point of this rung.
    """
    return (
        "## Earlier context DROPPED (not summarized)\n\n"
        f"[{len(middle)} section(s), {total_chars(middle)} chars were REMOVED to fit the "
        "model's context window. The summarizer failed, so this content was dropped rather "
        "than summarized: it is NOT recoverable from this prompt. Treat the earlier "
        "conversation as unavailable, and do not infer that it was empty.]"
    )


def _guarded_summarizer(
    summarize_fn: Callable[[list[dict]], str] | None,
) -> Callable[[list[dict]], str] | None:
    """Wrap a supplied summarizer so a raise degrades to the placeholder.

    `None` is passed through untouched: `compact(summarize_fn=None)` already produces its
    structured deterministic digest, so "no summarizer" is a working configuration and not
    a failure. "The summarizer failed" means specifically that a summarizer WAS supplied
    and raised — that is the case this catches, and wrapping it here means `compact()`
    still owns the slicing, the fencing and the tool-pair handling while only the BODY
    changes.
    """
    if summarize_fn is None:
        return None

    def _fn(middle: list[dict]) -> str:
        try:
            return summarize_fn(middle)
        except Exception:  # noqa: BLE001 — any summarizer failure degrades, never propagates
            logger.warning("workflow prompt summarizer failed; dropping middle", exc_info=True)
            return _placeholder_body(middle)

    return _fn


def compact_prompt(
    prompt: str,
    *,
    summarize_fn: Callable[[list[dict]], str] | None = None,
    aggressive: bool = False,
) -> tuple[str, float]:
    """Compact one prompt. Returns `(prompt, saved_fraction)`.

    `saved_fraction` is 0.0 when nothing changed — a prompt with too few paragraph blocks
    to have a middle is returned verbatim, because there is nothing droppable in it.
    """
    segments = segment_prompt(prompt)
    if not segments:
        return prompt, 0.0
    before = total_chars(segments)
    head = _AGGRESSIVE_HEAD if aggressive else _PROACTIVE_HEAD
    tail = _AGGRESSIVE_TAIL if aggressive else _PROACTIVE_TAIL
    compacted = compact(
        segments,
        summarize_fn=_guarded_summarizer(summarize_fn),
        protect_head=head,
        protect_tail=tail,
    )
    after = total_chars(compacted)
    if before <= 0 or after >= before:
        # No win. Hand back the ORIGINAL: a "compaction" that grew the prompt (a digest
        # longer than the two blocks it replaced) must not be shipped as an improvement.
        return prompt, 0.0
    return join_segments(compacted), (before - after) / before


def is_context_overflow(exc: BaseException) -> bool:
    """Did the provider reject this call for LENGTH?

    Delegates to `loop_middleware.classify_failure`, which already owns the pattern set
    (`context_length_exceeded`, `prompt is too long`, `maximum context`, …). Its
    `FailureClass` is a DIFFERENT enum from `models.FailureClass` and the two are never
    mixed here — only the boolean crosses the boundary. Feeding one vocabulary's values to
    the other's consumers is a known defect shape in this engine, so the vocabularies stay
    apart and only the ANSWER is shared.
    """
    from personalclaw.workflows.loop_middleware import FailureClass as MiddlewareFailureClass
    from personalclaw.workflows.loop_middleware import classify_failure

    return classify_failure(str(exc)) is MiddlewareFailureClass.CONTEXT_OVERFLOW


async def complete_with_compaction(
    fn: Any,
    prompt: str,
    *,
    use_case: str,
    output_type: type | None = None,
    model: str = "",
    summarize_fn: Callable[[list[dict]], str] | None = None,
    saves: list[float] | None = None,
    model_resolver: Callable[[str], str] | None = None,
) -> str:
    """Send `prompt` through `fn`, with the two-layer compaction ladder around it.

    Returns the model text. Raises whatever `fn` raised if the ladder could not save the
    call — the caller's existing `_classify_exception` handling is unchanged, so a node
    that was going to fail still fails with the same typed failure it always did.

    `saves` is the per-node compaction history the anti-thrashing rule reads
    (`should_compact`): a node whose last two compactions each freed <10% stops paying a
    summarizer that is demonstrably not helping. The controller owns the list and keys it
    per node id, so it survives the engine's retries and a loop body's iterations — the
    exact repetition the rule exists to stop.
    """
    resolve = model_resolver
    if resolve is None:
        from personalclaw.workflows.engine import resolve_axis_model

        resolve = resolve_axis_model
    # A pinned model is the one that will actually run; otherwise the axis head is, and
    # that is the same resolution `one_shot_completion` performs for this use case.
    bound = model or (resolve(use_case) or "")
    budget = prompt_char_budget(bound)

    # ── layer 1: proactive, at ~80% of the bound window ──
    if total_chars(segment_prompt(prompt)) > budget:
        if should_compact(saves if saves is not None else []):
            prompt, saved = compact_prompt(prompt, summarize_fn=summarize_fn)
            if saves is not None:
                saves.append(saved)
            logger.info(
                "workflow prompt compacted proactively: freed %.1f%% (budget %d chars, "
                "model %r)",
                saved * 100,
                budget,
                bound or "<unresolved>",
            )
        else:
            logger.info(
                "workflow prompt over budget but compaction is thrashing (last saves %r); "
                "sending as-is and letting the error-triggered layer handle a rejection",
                (saves or [])[-2:],
            )

    pin = {"model": model} if model else {}
    try:
        return await fn(prompt, use_case=use_case, output_type=output_type, **pin)
    except Exception as exc:
        # ── layer 2: error-triggered aggressive re-compaction, ONE retry ──
        # Only for a LENGTH rejection. Retrying a 429 or a credential error with a smaller
        # prompt would burn a second call on something compaction provably cannot fix.
        if not is_context_overflow(exc):
            raise
        retry_prompt, saved = compact_prompt(prompt, summarize_fn=summarize_fn, aggressive=True)
        if saves is not None:
            saves.append(saved)
        if saved <= 0.0:
            # Nothing left to drop — the prompt is one indivisible block. Re-raising is
            # honest; a retry of the identical prompt would fail identically and cost a
            # call to learn it.
            raise
        logger.warning(
            "model rejected a workflow prompt for length; re-compacted aggressively "
            "(freed %.1f%%) and retrying once",
            saved * 100,
        )
        return await fn(retry_prompt, use_case=use_case, output_type=output_type, **pin)
