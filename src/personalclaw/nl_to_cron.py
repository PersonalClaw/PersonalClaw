"""Natural-language → cron expression (#39).

Turns "every weekday at 9am" into a croniter-valid 5-field cron expression via a
constrained one-shot LLM call, then **validates** the result (croniter) before it's
ever used — so a hallucinated/garbled expression is rejected rather than scheduled.

Pure + LLM-injected: :func:`parse_cron_response` (extract + validate, no LLM) is
unit-testable; :func:`nl_to_cron` wraps it around a one-shot completion. The schedule
tool calls ``nl_to_cron`` then hands the validated expr to the existing ``schedule_add``.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_CRON_RE = re.compile(r"^\s*([^\s]+\s+[^\s]+\s+[^\s]+\s+[^\s]+\s+[^\s]+)\s*$")


def parse_cron_response(raw: str) -> tuple[str, str]:
    """Extract + validate a cron expression from an LLM response.

    Returns ``(cron_expr, "")`` on success, or ``("", error)``. ``NONE`` (the
    not-recurring sentinel) → an error explaining a one-off should use ``at``/``delay``.
    Pure — no LLM, no scheduling side effects."""
    from personalclaw.schedule import validate_cron_expr

    text = (raw or "").strip()
    # Strip code fences / leading labels the model might add despite instructions.
    text = re.sub(r"```[a-z]*", "", text).replace("`", "").strip()
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first_line or first_line.upper() == "NONE":
        return "", "Not a recurring schedule — use a one-off time (at / delay) instead."
    m = _CRON_RE.match(first_line)
    if not m:
        return "", f"Could not parse a 5-field cron expression from: {first_line[:80]!r}"
    expr = m.group(1).strip()
    if not validate_cron_expr(expr):
        return "", f"Generated an invalid cron expression: {expr!r}"
    return expr, ""


async def nl_to_cron(request: str, *, ask=None) -> tuple[str, str]:
    """NL schedule request → ``(cron_expr, "")`` or ``("", error)``.

    *ask* is an optional ``(prompt) -> str`` coroutine (injected for tests);
    defaults to the background one-shot completion."""
    req = (request or "").strip()
    if not req:
        return "", "Empty request."
    if ask is None:
        from personalclaw.llm_helpers import one_shot_completion

        async def ask(p: str) -> str:  # noqa: ANN001
            return await one_shot_completion(p, use_case="background")

    # The conversion instruction lives in the prompt system (bundled
    # ``task-nl-to-cron``, bindable in Settings → Prompts), rendered with the request.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt("nl_to_cron", {"request": req})
    if not prompt:
        return "", "Could not load the schedule-interpretation prompt."
    try:
        raw = await ask(prompt)
    except Exception:
        logger.debug("nl_to_cron LLM call failed", exc_info=True)
        return "", "Could not reach a model to interpret the schedule."
    return parse_cron_response(raw)
