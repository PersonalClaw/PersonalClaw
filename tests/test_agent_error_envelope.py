"""The WHAT/WHY/FIX error envelope wiring (PLATFORM-LEGIBILITY §2).

Proves the envelope surfaces at each seam the plan names: the tool-result string
boundary, the action-provider failure wrapper, provider-resolution, and the
hook-provider / enum validation rejection — and that when an ``AgentError`` is
present its ``render()`` IS the surfaced message (one source of truth, no
divergence between the string an agent reads and the structure the FE branches on).
"""

from __future__ import annotations

import pytest

from personalclaw.action_providers.base import ActionResult, provider_failure
from personalclaw.agents.native.tools import format_tool_result
from personalclaw.errors import AgentError
from personalclaw.providers.provider_bridge import ProviderResolutionError
from personalclaw.tool_providers.base import ToolResult
from personalclaw.validation import (
    HOOK_CREATE_SCHEMA,
    ValidationError,
    validate_tool_args,
)

# ── the envelope itself ──


def test_render_is_three_labeled_lines():
    e = AgentError(code="ERR_X", what="the what", why="the why", fix="the fix")
    assert e.render() == "WHAT: the what\nWHY: the why\nFIX: the fix"


def test_render_appends_suggestions_when_present():
    e = AgentError(code="ERR_X", what="w", why="y", fix="f", suggestions=("alpha", "beta"))
    assert e.render().endswith("DID YOU MEAN: alpha, beta")


def test_to_dict_is_the_structural_carrier():
    e = AgentError(code="ERR_X", what="w", why="y", fix="f", suggestions=("a",))
    assert e.to_dict() == {
        "code": "ERR_X",
        "what": "w",
        "why": "y",
        "fix": "f",
        "suggestions": ["a"],
    }


def test_agent_error_is_frozen():
    e = AgentError(code="ERR_X", what="w", why="y", fix="f")
    with pytest.raises(Exception):
        e.code = "ERR_Y"  # type: ignore[misc]


# ── ToolResult → the LLM string boundary ──


def test_format_tool_result_renders_envelope_over_bare_error():
    e = AgentError(code="ERR_TOOL_ARG_INVALID", what="w", why="y", fix="f")
    out = format_tool_result(ToolResult(success=False, error="ignored bare error", agent_error=e))
    assert out == e.render()
    assert "ignored bare error" not in out


def test_format_tool_result_envelope_then_recovery_hints():
    e = AgentError(code="ERR_X", what="w", why="y", fix="f")
    out = format_tool_result(
        ToolResult(success=False, error="x", agent_error=e, recovery_hints=["do the thing"])
    )
    assert out == e.render() + "\nHint: do the thing"


def test_format_tool_result_falls_back_to_bare_error_without_envelope():
    assert format_tool_result(ToolResult(success=False, error="boom")) == "Error: boom"


def test_tool_result_agent_error_defaults_none():
    assert ToolResult(success=True, output="ok").agent_error is None


# ── action-provider failure wrapper (the three dispatch seams share it) ──


def test_provider_failure_builds_the_generic_envelope():
    e = provider_failure("webhook", RuntimeError("connection refused"))
    assert e.code == "ERR_ACTION_PROVIDER_FAILED"
    assert "webhook" in e.what
    assert "RuntimeError" in e.what and "connection refused" in e.what
    assert e.render().startswith("WHAT: action provider 'webhook' failed")


def test_action_result_agent_error_defaults_none():
    assert ActionResult(success=False).agent_error is None


# ── provider resolution ──


def test_provider_resolution_error_message_is_the_envelope_render():
    e = AgentError(code="ERR_MODEL_UNRESOLVED", what="w", why="y", fix="f")
    exc = ProviderResolutionError("human fallback text", e)
    assert str(exc) == e.render()
    assert exc.agent_error is e


def test_provider_resolution_error_without_envelope_is_the_plain_message():
    exc = ProviderResolutionError("no provider configured")
    assert str(exc) == "no provider configured"
    assert exc.agent_error is None


# ── validation rejections ──


def test_hook_provider_rejection_carries_the_hook_code_and_allowed_set():
    with pytest.raises(ValidationError) as ei:
        validate_tool_args(
            {"name": "n", "provider": "nope", "provider_config": {}, "event": "PreToolUse"},
            HOOK_CREATE_SCHEMA,
        )
    err = ei.value.agent_error
    assert err is not None
    assert err.code == "ERR_HOOK_PROVIDER_UNKNOWN"
    # did-you-mean = the allowed set, so an agent self-corrects next turn
    assert "bash" in err.suggestions and "webhook" in err.suggestions
    # the exception's string form IS the render (one message)
    assert str(ei.value) == err.render()


def test_generic_enum_rejection_uses_the_tool_arg_code():
    with pytest.raises(ValidationError) as ei:
        # event is an enum field with no override → generic code
        validate_tool_args(
            {"name": "n", "provider": "bash", "provider_config": {}, "event": "NotAnEvent"},
            HOOK_CREATE_SCHEMA,
        )
    err = ei.value.agent_error
    assert err is not None and err.code == "ERR_TOOL_ARG_INVALID"


def test_validation_without_envelope_is_the_plain_field_message():
    from personalclaw.validation import FieldSpec, validate_field

    with pytest.raises(ValidationError) as ei:
        validate_field(None, FieldSpec("thing", str, required=True))
    assert ei.value.agent_error is None
    assert str(ei.value) == "thing: required"
