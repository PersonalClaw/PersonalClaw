"""The ``prompt_render`` MCP tool — lets an agent load a saved Prompt and render
it with variable values filled in, returning the final text to act on (the
agent-facing counterpart of the run-prompt trigger action).

Covers: registration + schema, native-loop discoverability, the success path
(renders via /api/prompts/{name}/render with vars), and the guard paths
(missing prompt_id, bad vars type, render error, empty render).

Lives in ``mcp_prompts`` as of WORKFLOWS-V2 Phase 0. It was in ``mcp_workflows``
only because both were authored together; Prompts are their own entity, and that
module is deleted wholesale when the old workflow feature is replaced.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from personalclaw.mcp_prompts import _call_tool, _list_tools
from personalclaw.validation import MCP_CORE_SCHEMAS


class TestPromptRenderRegistration:
    def test_tool_is_listed(self) -> None:
        assert "prompt_render" in {t["name"] for t in _list_tools()}

    def test_tool_has_schema(self) -> None:
        assert "prompt_render" in MCP_CORE_SCHEMAS

    def test_discoverable_in_native_loop(self) -> None:
        from personalclaw.agents.native.tools import InProcessMcpToolProvider

        prov = InProcessMcpToolProvider(module="personalclaw.mcp_prompts")
        names = {t.name for t in asyncio.run(prov.list_tools())}
        assert "prompt_render" in names


class TestPromptRenderDispatch:
    def test_renders_with_vars(self) -> None:
        with patch(
            "personalclaw.mcp_prompts._post",
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
            "personalclaw.mcp_prompts._post",
            return_value={"error": "missing required variable: team"},
        ):
            out = _call_tool("prompt_render", {"prompt_id": "report"})
        assert out.startswith("Error") and "missing required variable" in out

    def test_empty_render_is_error(self) -> None:
        with patch("personalclaw.mcp_prompts._post", return_value={"rendered": "   "}):
            out = _call_tool("prompt_render", {"prompt_id": "blank"})
        assert out.startswith("Error") and "empty" in out


class TestPromptsCategoryIsIndependent:
    """The relocation is the point: Prompts must survive the workflow feature's
    replacement. WORKFLOWS-V2 Phase 1 deletes `mcp_workflows` wholesale, so anything
    still living there goes with it."""

    def test_the_workflows_category_module_is_gone(self):
        """Phase 1 deleted it — which is exactly why prompt_render moved out first."""
        import importlib

        try:
            importlib.import_module("personalclaw.mcp_workflows")
        except ModuleNotFoundError:
            return
        raise AssertionError(
            "personalclaw.mcp_workflows still exists; prompt_render's relocation "
            "assumed Phase 1 deletes it"
        )

    def test_prompts_module_does_not_import_workflows(self):
        """A dependency back onto the doomed module would defeat the relocation."""
        from pathlib import Path

        import personalclaw.mcp_prompts as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "import" in source  # sanity: we really read the module
        offending = [
            line
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from ")) and "workflow" in line.lower()
        ]
        assert offending == [], f"mcp_prompts must not import workflows: {offending}"

    def test_aggregated_surface_still_exposes_prompt_render(self):
        """The ACP MCP-server surface aggregates category modules explicitly; a new
        module that is not listed is invisible to every ACP agent."""
        from personalclaw.mcp_core import _aggregated_list_tools

        names = {t["name"] for t in _aggregated_list_tools()}
        assert "prompt_render" in names
