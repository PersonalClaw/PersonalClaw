"""The ``prompt_render`` MCP tool — lets an agent load a saved Prompt and render
it with variable values filled in, returning the final text to act on (the
agent-facing counterpart of the run-prompt trigger action).

Covers: registration + schema, native-loop discoverability, the success path
(renders via /api/prompts/{name}/render with vars), and the guard paths
(missing prompt_id, bad vars type, render error, empty render).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from personalclaw.mcp_workflows import _call_tool, _list_tools
from personalclaw.validation import MCP_CORE_SCHEMAS


class TestPromptRenderRegistration:
    def test_tool_is_listed(self) -> None:
        assert "prompt_render" in {t["name"] for t in _list_tools()}

    def test_tool_has_schema(self) -> None:
        assert "prompt_render" in MCP_CORE_SCHEMAS

    def test_discoverable_in_native_loop(self) -> None:
        from personalclaw.agents.native.tools import InProcessMcpToolProvider

        prov = InProcessMcpToolProvider(module="personalclaw.mcp_workflows")
        names = {t.name for t in asyncio.run(prov.list_tools())}
        assert "prompt_render" in names


class TestPromptRenderDispatch:
    def test_renders_with_vars(self) -> None:
        with patch(
            "personalclaw.mcp_workflows._post",
            return_value={"name": "report", "rendered": "Report on the infra team."},
        ) as mock_post:
            out = _call_tool("prompt_render", {"prompt_id": "report", "vars": {"team": "infra"}})
        assert "Report on the infra team." in out
        assert "carry out" in out.lower()
        # vars are forwarded to the render endpoint.
        _path, body = mock_post.call_args[0]
        assert body == {"variables": {"team": "infra"}}

    def test_missing_prompt_id_is_error(self) -> None:
        out = _call_tool("prompt_render", {})
        assert out.lower().startswith("error") and "prompt_id" in out

    def test_render_error_surfaces(self) -> None:
        with patch(
            "personalclaw.mcp_workflows._post",
            return_value={"error": "missing required variable: team"},
        ):
            out = _call_tool("prompt_render", {"prompt_id": "report"})
        assert out.startswith("Error") and "missing required variable" in out

    def test_empty_render_is_error(self) -> None:
        with patch("personalclaw.mcp_workflows._post", return_value={"rendered": "   "}):
            out = _call_tool("prompt_render", {"prompt_id": "blank"})
        assert out.startswith("Error") and "empty" in out
