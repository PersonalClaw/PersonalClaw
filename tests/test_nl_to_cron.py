"""Natural-language → cron scheduling tool (#39)."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.nl_to_cron import nl_to_cron, parse_cron_response


def _run(coro):
    return asyncio.run(coro)


# ── parse_cron_response (pure, no LLM) ──


def test_parse_valid_cron():
    expr, err = parse_cron_response("0 9 * * 1-5")
    assert expr == "0 9 * * 1-5" and err == ""


def test_parse_strips_code_fence():
    expr, err = parse_cron_response("```\n*/30 * * * *\n```")
    assert expr == "*/30 * * * *" and not err


def test_parse_strips_label_and_takes_first_line():
    expr, _ = parse_cron_response("0 0 1 * *\nthis runs monthly")
    assert expr == "0 0 1 * *"


def test_parse_none_sentinel_is_error():
    expr, err = parse_cron_response("NONE")
    assert expr == "" and "one-off" in err.lower()


def test_parse_invalid_cron_rejected():
    expr, err = parse_cron_response("99 99 99 99 99")
    assert expr == "" and "invalid" in err.lower()


def test_parse_non_cron_text_rejected():
    expr, err = parse_cron_response("I think every weekday at 9")
    assert expr == ""


# ── nl_to_cron (injected ask) ──


def test_nl_to_cron_with_stub_ask():
    async def ask(_p):
        return "0 9 * * 1-5"

    expr, err = _run(nl_to_cron("every weekday at 9am", ask=ask))
    assert expr == "0 9 * * 1-5" and not err


def test_nl_to_cron_one_off_rejected():
    async def ask(_p):
        return "NONE"

    expr, err = _run(nl_to_cron("in 5 minutes", ask=ask))
    assert expr == "" and "one-off" in err.lower()


def test_nl_to_cron_empty_request():
    expr, err = _run(nl_to_cron("   ", ask=lambda p: None))
    assert expr == "" and err == "Empty request."


def test_nl_to_cron_llm_failure():
    async def boom(_p):
        raise RuntimeError("no model")

    expr, err = _run(nl_to_cron("every hour", ask=boom))
    assert expr == "" and "model" in err.lower()


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
        cadence_to_cron=lambda cadence: ("0 9 * * 1-5", ""),
    )
    assert result.ok, result.text
    assert "0 9 * * 1-5" in result.text  # the derived cron is surfaced back to the caller
    assert store.load()[0].trigger.spec == {"kind": "cron", "expr": "0 9 * * 1-5"}


def test_a_conversion_error_is_surfaced_not_defaulted(tmp_path):
    """🔴 The reason this seam exists: defaulting an unconvertible cadence to `* * * * *` would turn
    "in 5 minutes" into a per-minute LLM turn."""
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    result = T.create(
        store,
        name="x",
        when="every 5 minutes",
        message="y",
        created_by="user",
        cadence_to_cron=lambda cadence: ("", "Not a recurring schedule — use a one-off time."),
    )
    assert not result.ok
    assert "one-off" in result.text.lower()
    assert store.load() == [], "a failed conversion must not persist a trigger"


def test_the_retired_alias_is_not_registered():
    from personalclaw.triggers.tools import TOOL_NAMES

    assert not [n for n in TOOL_NAMES if n.startswith("schedule_")]
    with pytest.raises(ModuleNotFoundError):
        __import__("personalclaw.mcp_schedule")
