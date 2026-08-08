"""Generative-UI catalog + the agency-free ``visualize`` primitive (AMBIENT-SURFACES §5).

Covers the three server-side surfaces AS-4 adds:

* the genui component CATALOG + its mechanically-derived authoring prompt (never
  hand-maintained — it is generated from ``CORE_COMPONENTS``);
* the ``visualize(data, hint)`` primitive resolving through ``one_shot_completion``
  on the REASONING axis with no tools (agency-free), stripping fences/wrapper tags;
* the ``visualize`` MCP tool in mcp_artifacts (honest no-model degrade).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from personalclaw import genui
from personalclaw.mcp_artifacts import _call_tool
from personalclaw.visualize import visualize

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ── the catalog + mechanical prompt (§5.1/§5.2) ──────────────────────────────


class TestGenUiCatalog:
    def test_prompt_lists_every_core_component(self) -> None:
        prompt = genui.library_prompt()
        for comp in genui.CORE_COMPONENTS:
            assert comp.name in prompt
            for arg in comp.args:
                if arg.required:
                    assert arg.key in prompt

    def test_manifest_is_derived_from_the_catalog(self) -> None:
        man = genui.library_manifest()
        names = {c["name"] for c in man["components"]}
        assert names == {c.name for c in genui.CORE_COMPONENTS}
        assert man["prompt"] == genui.library_prompt()
        # Every group in the small bundled set is represented.
        assert {c["group"] for c in man["components"]} == {"Layout", "Data", "Charts", "Feedback"}


# ── the visualize primitive (§5.3) ───────────────────────────────────────────


class TestVisualizePrimitive:
    async def test_resolves_on_the_reasoning_axis_with_no_tools(self) -> None:
        seen = {}

        async def fake(prompt, *, use_case="background", output_type=None, model=""):
            seen["use_case"] = use_case
            seen["prompt"] = prompt
            return 'stat = StatTile(label: "Rev", value: "$1M")'

        with patch("personalclaw.llm_helpers.one_shot_completion", fake):
            result = await visualize({"rev": 1_000_000}, "as a tile", title="Q3")
        # NEVER chat/code_tools (those return the agent runtime — not agency-free).
        assert seen["use_case"] == "reasoning"
        # The current registry vocabulary is embedded in the prompt (mechanical, not hand-written).
        assert "StatTile" in seen["prompt"]
        assert result.dsl.startswith("stat = StatTile")
        # The widget wraps the DSL in a kind="genui" block carrying the title.
        assert result.widget.startswith('<widget kind="genui" title="Q3">')
        assert result.dsl in result.widget
        assert result.widget.rstrip().endswith("</widget>")

    async def test_strips_code_fences_and_wrapper_tags(self) -> None:
        async def fenced(prompt, *, use_case="background", output_type=None, model=""):
            return '```\n<widget kind="genui">\na = List(items: ["x"])\n</widget>\n```'

        with patch("personalclaw.llm_helpers.one_shot_completion", fenced):
            result = await visualize([1, 2], "list them")
        assert result.dsl == 'a = List(items: ["x"])'


# ── the visualize MCP tool ───────────────────────────────────────────────────


class TestVisualizeTool:
    def test_returns_an_embeddable_widget_block(self) -> None:
        async def fake(prompt, *, use_case="background", output_type=None, model=""):
            return "b = Bar(data: [1, 2, 3])"

        with patch("personalclaw.llm_helpers.one_shot_completion", fake):
            out = _call_tool("visualize", {"data": {"a": 1}, "hint": "chart it"})
        assert '<widget kind="genui"' in out
        assert "b = Bar(data: [1, 2, 3])" in out

    def test_missing_data_is_refused(self) -> None:
        out = _call_tool("visualize", {"hint": "chart it"})
        assert out.lower().startswith("error")

    def test_no_model_degrades_honestly_without_fabricating(self) -> None:
        async def boom(prompt, *, use_case="background", output_type=None, model=""):
            raise RuntimeError("no reasoning model configured")

        with patch("personalclaw.llm_helpers.one_shot_completion", boom):
            out = _call_tool("visualize", {"data": {"a": 1}})
        assert out.lower().startswith("error")
        assert "<widget" not in out  # never a fabricated widget on the degrade path
