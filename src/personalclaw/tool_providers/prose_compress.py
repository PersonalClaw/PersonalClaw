"""Prose-model compressor (Context Economy §2.4) — background paths ONLY.

An LLM summarizer for long natural-language text, used exclusively where latency is
already tolerable (the background compression service, the subagent-result path). It
is never wired into ``project_output``'s synchronous dispatch — the tool-dispatch
path cannot await an LLM.

Contract (guard-the-guard):
  * output = bounded summary + the raw_ref line when one exists (the road back to
    the raw bytes survives the summarization);
  * ANY failure — no resolvable model, a provider error, an over-budget response —
    degrades to the deterministic ``log`` projector, so a caller always gets a
    bounded, useful result;
  * savings are recorded under the ``prose`` compressor key (§1.3).

When AUTONOMY-GUARDRAILS lands, the ``one_shot_completion`` call inherits its
chokepoint (breaker/metering) for free — no bespoke resilience built here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The summarizer's output budget. A summary approaching the input's size is not a
# compression; the deterministic fallback also caps to this.
DEFAULT_SUMMARY_CAP = 2_000

# Never send an unbounded body to the model: the summarizer reads head + tail of a
# very long input (the shape/outcome usually live at the edges; the raw stays
# recoverable via the caller's raw_ref).
_PROMPT_INPUT_CAP = 24_000

_PROMPT = """Summarize the following tool/agent output for an AI agent's context \
window. Keep: the outcome, key findings, names/paths/ids/numbers that later steps \
may need, and any errors. Drop: repetition, boilerplate, progress noise. Reply with \
ONLY the summary (no preamble), at most {max_chars} characters.

OUTPUT:
{body}"""


def _deterministic_fallback(text: str, cap: int) -> str:
    """The guard's guard: the log projector (head + error lines + tail) — always
    available, always bounded."""
    from personalclaw.tool_providers.projection import project_output

    return project_output(text, cap=cap, content_type="log").text


async def compress_prose(
    text: str,
    *,
    cap: int = DEFAULT_SUMMARY_CAP,
    raw_ref: str = "",
) -> str:
    """Compress long prose via the background model; degrade to the ``log``
    projector on any failure. Small input passes through untouched.

    ``raw_ref``, when given, is appended as a recovery line naming
    ``tool_result_get`` — every lossy step keeps the road back to the raw bytes.
    """
    if len(text) <= cap:
        return text

    summary = ""
    try:
        from personalclaw.llm_helpers import one_shot_completion

        body = text
        if len(body) > _PROMPT_INPUT_CAP:
            head = _PROMPT_INPUT_CAP * 2 // 3
            tail = _PROMPT_INPUT_CAP - head
            body = body[:head] + "\n…[middle elided for summarization]…\n" + body[-tail:]
        summary = (
            await one_shot_completion(
                _PROMPT.format(max_chars=cap, body=body), use_case="background"
            )
        ).strip()
    except Exception:  # noqa: BLE001 — the fallback IS the contract
        logger.debug("prose compressor model call failed — deterministic fallback", exc_info=True)
        summary = ""

    # An empty or over-budget "summary" is a failed compression → fallback.
    if not summary or len(summary) > cap * 2:
        summary = _deterministic_fallback(text, cap)
    else:
        _record(len(text), len(summary))

    if raw_ref:
        summary += f'\n[full output: tool_result_get(result_id="{raw_ref}")]'
    return summary


def _record(chars_in: int, chars_out: int) -> None:
    """Savings accounting under the ``prose`` compressor key (§1.3). Never raises."""
    try:
        from datetime import datetime

        from personalclaw.tool_providers import savings

        savings.record_saving(
            month=datetime.now().strftime("%Y-%m"),
            model="unknown",
            compressor="prose",
            chars_in=chars_in,
            chars_out=chars_out,
        )
    except Exception:  # noqa: BLE001
        logger.debug("prose savings accounting failed", exc_info=True)
