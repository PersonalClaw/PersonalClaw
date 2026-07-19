"""Tests for tool-denial recovery observations + result-contract surfacing.

A recoverable denial feeds a model-visible observation that says WHY + adapt-
don't-repeat; a hard (security) denial is terminal + non-circumventable with no
recovery hint. format_tool_result surfaces the #7 result contract (recovery_hints
on failure, truncation notice on success) instead of dropping it.
"""

from personalclaw import security
from personalclaw.agents.native.tools import format_tool_result
from personalclaw.tool_providers.base import ToolResult


# ── classify_denial taxonomy ──


def test_policy_denial_is_hard_and_terminal():
    rec, obs = security.classify_denial(security.DENY_KIND_POLICY, "deny:rm -rf", "bash")
    assert rec is False
    assert "security policy" in obs
    assert "non-negotiable" in obs
    assert "circumvent" in obs


def test_sensitive_path_denial_is_hard():
    rec, obs = security.classify_denial(
        security.DENY_KIND_SENSITIVE, "sensitive path ~/.ssh", "read_file"
    )
    assert rec is False
    assert "non-negotiable" in obs


def test_hook_denial_is_recoverable_adapt_dont_repeat():
    rec, obs = security.classify_denial(security.DENY_KIND_HOOK, "no prod writes", "edit_file")
    assert rec is True
    assert "Do NOT retry" in obs
    assert "different approach" in obs


def test_readonly_denial_is_recoverable():
    rec, obs = security.classify_denial(security.DENY_KIND_READONLY, "read-only session", "write_file")
    assert rec is True
    assert "read-only alternative" in obs


def test_user_denial_is_recoverable():
    rec, obs = security.classify_denial(security.DENY_KIND_USER, "user declined", "bash")
    assert rec is True
    assert "Do NOT retry" in obs


def test_unknown_kind_defaults_recoverable():
    rec, obs = security.classify_denial("mystery", "whatever", "tool")
    assert rec is True


def test_tool_name_appears_in_observation():
    _, obs = security.classify_denial(security.DENY_KIND_USER, "declined", "subagent_run")
    assert "subagent_run" in obs


def test_hard_denial_has_no_recovery_hint():
    # Hard denials must NOT coach the model on alternatives (would invite bypass).
    _, obs = security.classify_denial(security.DENY_KIND_POLICY, "deny", "bash")
    assert "Hint:" not in obs
    assert "alternative" not in obs.lower()


# ── format_tool_result surfaces the result contract ──


def test_format_success_passthrough():
    assert format_tool_result(ToolResult(success=True, output="done")) == "done"


def test_format_failure_includes_recovery_hints():
    r = ToolResult(success=False, error="file not found", recovery_hints=["Use glob to locate it."])
    out = format_tool_result(r)
    assert "Error: file not found" in out
    assert "Hint: Use glob to locate it." in out


def test_format_failure_multiple_hints():
    r = ToolResult(success=False, error="boom", recovery_hints=["hint one", "hint two"])
    out = format_tool_result(r)
    assert "Hint: hint one" in out and "Hint: hint two" in out


def test_format_failure_no_hints_is_bare_error():
    assert format_tool_result(ToolResult(success=False, error="boom")) == "Error: boom"


def test_format_success_truncation_notice():
    r = ToolResult(success=True, output="partial", truncated=True, original_length=5000)
    out = format_tool_result(r)
    assert "truncated" in out
    assert "5000" in out


def test_format_success_no_truncation_when_not_flagged():
    r = ToolResult(success=True, output="full", truncated=False)
    assert format_tool_result(r) == "full"
