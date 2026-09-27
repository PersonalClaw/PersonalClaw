"""Natural-language → cron, or one instant (#39)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from personalclaw.nl_to_cron import Schedule, nl_to_cron, parse_cron_response

#: Sunday 27 September 2026, 14:03 in Los Angeles.
ZONE = "America/Los_Angeles"
NOW = 1_790_542_980.0


def _run(coro):
    return asyncio.run(coro)


# ── parse_cron_response (pure, no LLM) ──


def test_parse_valid_cron():
    assert parse_cron_response("0 9 * * 1-5") == Schedule(expr="0 9 * * 1-5")


def test_parse_strips_code_fence():
    assert parse_cron_response("```\n*/30 * * * *\n```").expr == "*/30 * * * *"


def test_parse_strips_label_and_takes_first_line():
    assert parse_cron_response("0 0 1 * *\nthis runs monthly").expr == "0 0 1 * *"


def test_parse_none_sentinel_is_error():
    answer = parse_cron_response("NONE")
    assert not answer.expr and not answer.once and "say a time" in answer.error.lower()


def test_parse_invalid_cron_rejected():
    answer = parse_cron_response("99 99 99 99 99")
    assert answer.expr == "" and "invalid" in answer.error.lower()


def test_parse_non_cron_text_rejected():
    assert parse_cron_response("I think every weekday at 9").error


def test_a_ONCE_answer_is_the_instant_it_names_in_the_owners_zone():
    """🔴 The one-time answer shape. On main a one-off had no answer at all: NONE, refused."""
    answer = parse_cron_response("ONCE 2026-09-28T09:00", now=NOW, zone=ZONE)
    assert answer.once and answer.zone == ZONE
    assert answer.at == datetime.fromisoformat("2026-09-28T09:00-07:00").timestamp()


def test_a_ONCE_answer_with_an_offset_keeps_it():
    answer = parse_cron_response("ONCE 2026-09-28T17:00+00:00", now=NOW, zone=ZONE)
    assert answer.at == datetime.fromisoformat("2026-09-28T17:00+00:00").timestamp()


def test_a_ONCE_answer_in_the_past_is_refused_not_moved():
    """The model was told the clock, so a past answer is a wrong one."""
    answer = parse_cron_response("ONCE 2026-09-01T09:00", now=NOW, zone=ZONE)
    assert not answer.once and "passed" in answer.error


def test_a_garbled_ONCE_answer_is_refused():
    assert parse_cron_response("ONCE sometime soon", now=NOW, zone=ZONE).error


# ── nl_to_cron (injected ask) ──


def test_nl_to_cron_with_stub_ask():
    async def ask(_p):
        return "0 9 * * 1-5"

    assert _run(nl_to_cron("every weekday at 9am", ask=ask)).expr == "0 9 * * 1-5"


def test_the_model_is_told_the_clock_and_the_zone():
    """A one-time phrase can only be answered with the time it is now and where."""
    seen: list[str] = []

    async def ask(prompt):
        seen.append(prompt)
        return "ONCE 2026-09-28T09:00"

    answer = _run(nl_to_cron("tomorrow morning", ask=ask, now=NOW, zone=ZONE))
    assert answer.once, answer
    (prompt,) = seen
    assert "2026-09-27 14:03" in prompt and ZONE in prompt and "ONCE" in prompt, prompt
    assert "tomorrow morning" in prompt


def test_nl_to_cron_empty_request():
    assert _run(nl_to_cron("   ", ask=lambda p: None)) == Schedule(error="Empty request.")


def test_nl_to_cron_llm_failure():
    async def boom(_p):
        raise RuntimeError("no model")

    assert "model" in _run(nl_to_cron("every hour", ask=boom)).error.lower()


def test_no_model_is_said_as_no_model():
    """`one_shot_completion` answers "" when nothing resolves, rather than raising."""

    async def nothing(_p):
        return ""

    assert "reach a model" in _run(nl_to_cron("tomorrow morning", ask=nothing)).error


# ── tool dispatch (automation_create's `when` → validated cron → a store trigger) ──
#
# These drove `schedule_natural` until S109 retired the alias. The NL→cron bridge did not go away —
# it moved to `tools.create`'s injected `cadence_to_cron` seam, which is the same contract with a
# testable seam instead of a module-level monkeypatch.


def test_the_nl_cadence_bridge_is_reachable_from_automation_create(tmp_path):
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    # The injected converter stands in for the model, exactly as `_nl_to_cron_blocking` was stubbed.
    result = T.create(
        store,
        name="Standup",
        when="every weekday at 9am",
        message="post standup",
        created_by="user",
        cadence_to_cron=lambda cadence: Schedule(expr="0 9 * * 1-5"),
    )
    assert result.ok, result.text
    assert "0 9 * * 1-5" in result.text  # the derived cron is surfaced back to the caller
    assert store.load()[0].trigger.spec == {"kind": "cron", "expr": "0 9 * * 1-5"}


def test_a_conversion_error_is_surfaced_not_defaulted(tmp_path):
    """🔴 The reason this seam exists: defaulting an unconvertible cadence to `* * * * *` would turn
    it into a per-minute LLM turn."""
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    result = T.create(
        store,
        name="x",
        when="every 5 minutes",
        message="y",
        created_by="user",
        cadence_to_cron=lambda cadence: Schedule(error="could not read a cadence"),
    )
    assert not result.ok
    assert "could not read a cadence" in result.text
    assert store.load() == [], "a failed conversion must not persist a trigger"


def test_the_retired_alias_is_not_registered():
    from personalclaw.triggers.tools import TOOL_NAMES

    assert not [n for n in TOOL_NAMES if n.startswith("schedule_")]
    with pytest.raises(ModuleNotFoundError):
        __import__("personalclaw.mcp_schedule")
