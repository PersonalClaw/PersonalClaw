"""Natural-language → cron scheduling tool (#39)."""

from __future__ import annotations

import asyncio

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


# ── tool dispatch (schedule_natural → validated cron → schedule_add) ──


def test_schedule_natural_tool_registered():
    from personalclaw.mcp_schedule import _list_tools
    from personalclaw.validation import MCP_SCHEDULE_SCHEMAS

    assert "schedule_natural" in {t["name"] for t in _list_tools()}
    assert "schedule_natural" in MCP_SCHEDULE_SCHEMAS


def test_schedule_natural_dispatch(monkeypatch, tmp_path):
    import personalclaw.mcp_schedule as ms

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    # stub the NL→cron conversion (no LLM) + capture the delegated schedule_add
    monkeypatch.setattr(ms, "_nl_to_cron_blocking", lambda cadence: ("0 9 * * 1-5", ""))
    out = ms._call_tool_inner("schedule_natural", {"name": "Standup", "message": "post standup", "cadence": "weekdays 9am"})
    assert not out.startswith("Error")
    assert "0 9 * * 1-5" in out  # the derived cron is surfaced back


def test_schedule_natural_conversion_error(monkeypatch, tmp_path):
    import personalclaw.mcp_schedule as ms

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(ms, "_nl_to_cron_blocking", lambda cadence: ("", "Not a recurring schedule — use a one-off time instead."))
    out = ms._call_tool_inner("schedule_natural", {"name": "x", "message": "y", "cadence": "in 5 minutes"})
    assert out.startswith("Error") and "one-off" in out.lower()
