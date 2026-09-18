"""Workflow tools go through the shared boundary, and the stat counts tools (issue 592).

Three defects, one seam. ``mcp_workflows._call_tool`` jumped straight to its dispatcher,
so (a) the 19 ``MCP_WORKFLOW_SCHEMAS`` were dead — defined and key-tested but never
consulted; (b) no workflow tool call was SEL-logged; (c) ``leaf_tool_denial`` never ran,
so a compiled batch leaf could call ``workflow_start`` past the orchestration denial.
Meanwhile the Security panel's "Tools with enforced argument validation" counted
``*_SCHEMA`` module constants via ``dir()`` — not tools, and not enforcement.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw import mcp_workflows as W
from personalclaw.validation import (
    MCP_AUTOMATION_SCHEMAS,
    MCP_CORE_SCHEMAS,
    MCP_WORKFLOW_SCHEMAS,
    validated_tool_names,
)


class TestValidatedToolNames:
    def test_union_of_the_three_dispatch_maps(self) -> None:
        names = validated_tool_names()
        for m in (MCP_CORE_SCHEMAS, MCP_WORKFLOW_SCHEMAS, MCP_AUTOMATION_SCHEMAS):
            assert set(m) <= names
        assert names == set(MCP_CORE_SCHEMAS) | set(MCP_WORKFLOW_SCHEMAS) | set(
            MCP_AUTOMATION_SCHEMAS
        )

    @pytest.mark.asyncio
    async def test_security_stat_reports_the_enforcement_union(self) -> None:
        """The wire number IS len(validated_tool_names()) — tools, not constants."""
        from personalclaw.dashboard.handlers.core import api_security_stats

        resp = await api_security_stats(MagicMock())
        body = json.loads(resp.body.decode())
        assert body["tool_schemas"] == len(validated_tool_names())


class TestWorkflowToolBoundary:
    def test_an_unknown_field_is_refused_not_dispatched(self) -> None:
        """Schema enforcement is live: validate_tool_args rejects unknown fields, so a
        call carrying one must come back as the boundary's error string — before the
        old code would have handed it to the dispatcher untouched."""
        out = W._call_tool("workflow_list_defs", {"no_such_field": 1})
        assert out.startswith("Error:")
        assert "no_such_field" in out

    def test_a_batch_leaf_cannot_start_a_workflow(self, monkeypatch) -> None:
        """The orchestration denial reaches workflow tools now that the funnel runs."""
        from personalclaw.workflows.engine import WF_DEPTH_KEY

        monkeypatch.setenv(WF_DEPTH_KEY, "1")
        out = W._call_tool("workflow_start", {"name": "anything"})
        assert out.startswith("Error:")
        assert "orchestration" in out

    def test_a_valid_call_still_dispatches(self) -> None:
        """Vacuity guard: the boundary must not refuse well-formed calls."""
        out = W._call_tool("workflow_list_defs", {})
        assert not out.startswith("Error:")
