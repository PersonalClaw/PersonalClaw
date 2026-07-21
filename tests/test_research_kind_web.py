"""WS6 — the research loop kind drives the real web tools.

The research worker is a normal loop agent with the full tool catalog (web_search +
web_fetch are enabled out-of-box, verified elsewhere). WS6 makes the kind's brief /
nudge / directive name those concrete tools and surface the breadth×depth + max_uses
budget, so the worker runs a bounded deep-research sweep instead of reasoning from
memory. These pin that wiring.
"""

from __future__ import annotations

import pytest

from personalclaw.loop import kinds
from personalclaw.loop.loop import Loop


@pytest.fixture(autouse=True)
def _loaded():
    kinds.ensure_loaded()


def _research_loop(**cfg) -> Loop:
    base = kinds.get("research").default_kind_config()
    base.update(cfg)
    return Loop(id="r1", name="n", kind="research", task="research the thing", kind_config=base)


def test_default_kind_config_has_budget_axes():
    cfg = kinds.get("research").default_kind_config()
    assert cfg["goal_type"] == "open_ended"
    assert cfg["breadth"] >= 1
    assert cfg["depth"] >= 1
    assert cfg["max_uses_per_cycle"] >= 1
    assert cfg["primary_deliverable"] == "RESEARCH.md"


def test_brief_names_the_real_tools():
    brief = kinds.get("research").build_brief(_research_loop())
    assert "web_search" in brief
    assert "web_fetch" in brief
    # the recency use-case is surfaced for time-sensitive subtopics
    assert "search-news" in brief


def test_brief_surfaces_breadth_depth_and_max_uses():
    brief = kinds.get("research").build_brief(
        _research_loop(breadth=4, depth=3, max_uses_per_cycle=20)
    )
    assert "4" in brief and "3" in brief  # breadth / depth values
    assert "20" in brief  # max-uses ceiling
    assert "breadth" in brief.lower() and "depth" in brief.lower()


def test_brief_handles_non_int_budget_gracefully():
    # A malformed config value must not raise — falls back to the default.
    brief = kinds.get("research").build_brief(_research_loop(breadth="lots"))
    assert "web_search" in brief  # still renders


def test_cycle_nudge_names_real_tools():
    nudge = kinds.get("research").cycle_nudge(_research_loop(), "/loopdir")
    assert "web_search" in nudge and "web_fetch" in nudge
    assert "sources_checked" in nudge and "new_findings_count" in nudge


def test_turn_directive_names_real_tools():
    directive = kinds.get("research").turn_directive(_research_loop())
    assert "web_search" in directive and "web_fetch" in directive


def test_source_budget_still_surfaced_when_set():
    brief = kinds.get("research").build_brief(_research_loop(source_budget=50))
    assert "50" in brief and "Source budget" in brief


def test_brief_instructs_knowledge_persistence():
    # §5: fetched sources land as Knowledge items (via the existing knowledge_create
    # tool — no new mechanism) so the report cites back into them.
    brief = kinds.get("research").build_brief(_research_loop())
    assert "knowledge_create" in brief
    assert "bookmark" in brief
