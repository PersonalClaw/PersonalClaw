"""Natural-language schedule → a cron expression, or ONE instant (#39).

Turns "every weekday at 9am" into a croniter-valid 5-field cron expression, and "tomorrow
morning" into the one instant it means, via a constrained one-shot LLM call — then **validates**
the answer (croniter for a cadence; a real, future instant for one time) before it is ever used,
so a hallucinated or garbled answer is refused rather than scheduled.

A one-time request used to have no answer here at all: the prompt told the model to say NONE for
one, and the parser turned NONE into "Not a recurring schedule", so "remind me tomorrow morning"
could only be refused — or, from a model that ignored the instruction, become a daily cron. Now
the model is handed the clock and the owner's zone and answers ``ONCE <local date and time>``.

An EXPLICIT time never gets here: `triggers.when` reads "at 5 pm" / "in 20 minutes" /
"2026-10-01 14:00" without a model, and only a phrase that leaves the time to judgement is asked.

Pure + LLM-injected: :func:`parse_cron_response` (extract + validate, no LLM) is unit-testable;
:func:`nl_to_cron` wraps it around a one-shot completion.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

_CRON_RE = re.compile(r"^\s*([^\s]+\s+[^\s]+\s+[^\s]+\s+[^\s]+\s+[^\s]+)\s*$")
_ONCE_RE = re.compile(r"^ONCE\s+(?P<when>\S+(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Schedule:
    """What a scheduling phrase means: a repeating cron expression, or one instant — or why not."""

    #: A validated 5-field cron expression, for a cadence.
    expr: str = ""
    #: Epoch seconds, for one time.
    at: float = 0.0
    #: The zone ``at`` was read in, when it is one time.
    zone: str = ""
    #: Why the phrase could not be read. Empty on success.
    error: str = ""

    @property
    def once(self) -> bool:
        return self.at > 0 and not self.error


def parse_cron_response(raw: str, *, now: float | None = None, zone: str = "") -> Schedule:
    """Extract + validate a model's answer: a cron expression, ``ONCE <local datetime>`` or NONE.

    A ``ONCE`` time with no offset is read in *zone* (the owner's when empty, through
    `personalclaw.timezones`); one that has already passed is refused, never rolled forward — the
    model was told the clock, so a past answer is a wrong one. Pure — no LLM, no side effects.
    """
    from personalclaw.schedule import validate_cron_expr

    text = (raw or "").strip()
    # Strip code fences / leading labels the model might add despite instructions.
    text = re.sub(r"```[a-z]*", "", text).replace("`", "").strip()
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first_line or first_line.upper() == "NONE":
        return Schedule(
            error=(
                "Could not tell when that should run. Say a time ('at 5pm', 'tomorrow at 9am', "
                "'in 20 minutes') or a cadence ('every weekday at 9')."
            )
        )
    once = _ONCE_RE.match(first_line)
    if once:
        return _one_time(once.group("when"), now=now, zone=zone)
    m = _CRON_RE.match(first_line)
    if not m:
        return Schedule(
            error=f"Could not parse a 5-field cron expression from: {first_line[:80]!r}"
        )
    expr = m.group(1).strip()
    if not validate_cron_expr(expr):
        return Schedule(error=f"Generated an invalid cron expression: {expr!r}")
    return Schedule(expr=expr)


def _one_time(stamp: str, *, now: float | None, zone: str) -> Schedule:
    from personalclaw.timezones import resolve_zone, resolve_zone_name

    text = stamp.strip().replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.upper().endswith("Z") else text
        )
    except ValueError:
        return Schedule(error=f"Could not read a date and time from: {stamp[:80]!r}")
    try:
        zone_name = resolve_zone_name(zone)[0]
        tz = resolve_zone(zone)
    except (ValueError, RuntimeError) as exc:  # a typo'd zone, or no tz database
        return Schedule(error=str(exc))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    at = parsed.timestamp()
    if at <= (time.time() if now is None else float(now)):
        return Schedule(error=f"That time has already passed: {stamp[:80]!r}.")
    return Schedule(at=at, zone=zone_name)


async def nl_to_cron(
    request: str, *, ask=None, now: float | None = None, zone: str = ""
) -> Schedule:
    """NL schedule request → a :class:`Schedule`: a cron expression, one instant, or an error.

    *ask* is an optional ``(prompt) -> str`` coroutine (injected for tests); defaults to the
    background one-shot completion. The model is told the clock and the owner's zone, so it can
    answer a one-time phrase ("tomorrow morning") with the instant it means.
    """
    req = (request or "").strip()
    if not req:
        return Schedule(error="Empty request.")
    if ask is None:
        from personalclaw.llm_helpers import one_shot_completion

        async def ask(p: str) -> str:  # noqa: ANN001
            # Attributed on the attempt ledger — otherwise a schedule translation
            # and an inbox triage are the same anonymous `background` row.
            from personalclaw.guardrails.audit import caller_scope

            with caller_scope("nl_to_cron"):
                return await one_shot_completion(p, use_case="background")

    from personalclaw.timezones import resolve_zone, resolve_zone_name

    try:
        zone_name = resolve_zone_name(zone)[0]
        tz = resolve_zone(zone)
    except (ValueError, RuntimeError) as exc:  # a typo'd zone, or no tz database
        return Schedule(error=str(exc))
    clock = datetime.fromtimestamp(time.time() if now is None else float(now), tz)

    # The conversion instruction lives in the prompt system (bundled
    # ``task-nl-to-cron``, bindable in Settings → Prompts), rendered with the request.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt(
        "nl_to_cron",
        {
            "request": req,
            "now": f"{clock:%A} {clock:%Y-%m-%d %H:%M} ({clock:%Z})",
            "timezone": zone_name,
        },
    )
    if not prompt:
        return Schedule(error="Could not load the schedule-interpretation prompt.")
    try:
        raw = await ask(prompt)
    except Exception:
        logger.debug("nl_to_cron LLM call failed", exc_info=True)
        raw = ""
    if not str(raw or "").strip():
        # The one-shot completion answers "" when no model resolves, rather than raising.
        return Schedule(error="Could not reach a model to interpret the schedule.")
    return parse_cron_response(raw, now=clock.timestamp(), zone=zone)
