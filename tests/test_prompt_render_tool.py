"""The ``prompt_render`` MCP tool — lets an agent load a saved Prompt and render
it with variable values filled in, returning the final text to act on (the
agent-facing counterpart of the run-prompt trigger action).

Covers: registration + schema, native-loop discoverability, the success path
(renders via /api/prompts/{name}/render with vars), and the guard paths
(missing prompt_id, bad vars type, render error, empty render).

Lives in ``mcp_prompts``. It was in ``mcp_workflows``
only because both were authored together; Prompts are their own entity, and that
module was deleted wholesale when the old workflow feature was replaced.
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


#: Run in a process of its own: refuse every import of a workflow module, then use the Prompts
#: category as an agent would, and print what it answered and which workflow modules it loaded.
_WITHOUT_THE_WORKFLOW_FEATURE = """
import json, sys
from unittest.mock import patch


def _workflow_module(name):
    return name.startswith("personalclaw.") and "workflow" in name.split(".", 1)[1]


class _Refuse:
    def find_spec(self, fullname, path=None, target=None):
        if _workflow_module(fullname):
            raise ImportError("Prompts reached the workflow feature: " + fullname)
        return None


sys.meta_path.insert(0, _Refuse())
import personalclaw.mcp_prompts as prompts

seen = {"listed": [tool["name"] for tool in prompts._list_tools()]}
answer = {"name": "report", "rendered": "Report on the infra team."}
with patch.object(prompts, "_post", return_value=answer) as post:
    call = {"prompt_id": "report", "vars": json.dumps({"team": "infra"})}
    seen["rendered"] = str(prompts._call_tool("prompt_render", call))
    seen["asked"] = [list(call.args) for call in post.call_args_list]
seen["missing"] = str(prompts._call_tool("prompt_render", {}))
seen["loaded"] = sorted(name for name in sys.modules if _workflow_module(name))
print(json.dumps(seen))
"""


class TestPromptsCategoryIsIndependent:
    """The relocation is the point: Prompts must survive the workflow feature's
    replacement. The rebuild deleted `mcp_workflows` wholesale (anything still living there
    went with it), and later restored it as the v2 engine's tool surface. What the
    relocation guarantees is not that the module is absent — it is back — but that
    `prompt_render` no longer lives in it and Prompts does not depend on it."""

    def test_prompt_render_does_not_live_in_the_workflows_category(self):
        """The relocation's actual invariant, and it survives the module's return: a
        rebuilt `mcp_workflows` must not re-absorb the Prompts surface."""
        import importlib

        try:
            wf = importlib.import_module("personalclaw.mcp_workflows")
        except ModuleNotFoundError:
            return  # deleted (mid-rebuild) — trivially satisfied
        names = {t["name"] for t in wf._list_tools()}
        assert "prompt_render" not in names
        assert all(n.startswith("workflow_") for n in names)

    def test_prompts_work_where_the_workflow_feature_cannot_be_imported(self, tmp_path):
        """A dependency back onto the doomed module would defeat the relocation. In a process
        that refuses every import of a workflow module (`personalclaw.workflows.*`,
        `personalclaw.mcp_workflows`), the Prompts category imports, lists `prompt_render`,
        renders a saved prompt with its variables and refuses a call that names none, and no
        workflow module is loaded."""
        import json
        import os
        import subprocess
        import sys

        env = {**os.environ, "PERSONALCLAW_HOME": str(tmp_path)}
        done = subprocess.run(
            [sys.executable, "-c", _WITHOUT_THE_WORKFLOW_FEATURE],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        seen = json.loads(done.stdout.strip().splitlines()[-1])
        assert seen["listed"] == ["prompt_render"]
        assert seen["asked"] == [["/api/prompts/report/render", {"variables": {"team": "infra"}}]]
        assert seen["rendered"].endswith("carry out the following:\n\nReport on the infra team.")
        assert seen["missing"].lower().startswith("error") and "prompt_id" in seen["missing"]
        assert seen["loaded"] == [], "Prompts must not reach the workflow feature"

    def test_aggregated_surface_still_exposes_prompt_render(self):
        """The ACP MCP-server surface aggregates category modules explicitly; a new
        module that is not listed is invisible to every ACP agent."""
        from personalclaw.mcp_core import _aggregated_list_tools

        names = {t["name"] for t in _aggregated_list_tools()}
        assert "prompt_render" in names
