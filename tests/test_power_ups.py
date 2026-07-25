"""Capability-discovery power-ups (§6) — the dashboard's untouched-capability proposal.

Covers the pure selection logic (deterministic, propose-don't-write), the
per-capability dismissal persistence (``entity_settings/legibility.json``), and
the ``compute_power_up`` wiring incl. the ``legibility.power_ups`` kill switch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.legibility import power_ups as pu


def _tool(name: str, *, description: str = "Does a thing.", examples=(("do it", {}),)) -> dict:
    return {
        "name": name,
        "provider": "native",
        "description": description,
        "examples": [{"summary": s, "args": a} for s, a in examples],
    }


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_first_sentence_splits_on_terminal_punct():
    assert pu._first_sentence("Add a note. Then more text.") == "Add a note."
    assert pu._first_sentence("No terminal punctuation here") == "No terminal punctuation here"


def test_first_sentence_truncates_long_text():
    long = "x" * 400
    out = pu._first_sentence(long, limit=50)
    assert len(out) <= 50 and out.endswith("…")


def test_lesson_is_two_sentences_and_names_the_tool():
    lesson = pu._lesson_for(_tool("knowledge_add", description="Store a fact in memory."))
    assert "Store a fact in memory." in lesson
    assert "knowledge_add" in lesson


def test_build_power_up_deep_links_into_tools_page():
    p = pu.build_power_up(_tool("knowledge_add"))
    assert p.id == "tool:knowledge_add"
    assert p.kind == "tool"
    assert p.try_it["route"] == "tools"
    assert p.try_it["query"] == {"open": "knowledge_add"}
    # round-trips to a plain dict for JSON
    assert p.to_dict()["id"] == "tool:knowledge_add"


def test_teachable_requires_description_and_example():
    tools = [
        _tool("good"),
        _tool("no_desc", description="   "),
        {"name": "no_examples", "description": "Has a description", "examples": []},
    ]
    names = [t["name"] for t in pu._teachable_tools(tools)]
    assert names == ["good"]


def test_teachable_sorted_by_name():
    tools = [_tool("zebra"), _tool("alpha"), _tool("mike")]
    assert [t["name"] for t in pu._teachable_tools(tools)] == ["alpha", "mike", "zebra"]


# ── selection ────────────────────────────────────────────────────────────────


def test_select_picks_first_untouched_nondismissed():
    tools = [_tool("alpha"), _tool("beta"), _tool("gamma")]
    chosen, untouched, total = pu.select_power_up(tools, used={"alpha"}, dismissed=set())
    assert chosen is not None and chosen.name == "beta"
    assert untouched == 2  # beta, gamma
    assert total == 3


def test_select_skips_dismissed():
    tools = [_tool("alpha"), _tool("beta")]
    chosen, untouched, total = pu.select_power_up(tools, used=set(), dismissed={"tool:alpha"})
    assert chosen is not None and chosen.name == "beta"
    # dismissed still counts as untouched (only 'used' subtracts from untouched)
    assert untouched == 2


def test_select_returns_none_when_all_touched():
    tools = [_tool("alpha"), _tool("beta")]
    chosen, untouched, total = pu.select_power_up(tools, used={"alpha", "beta"}, dismissed=set())
    assert chosen is None
    assert untouched == 0
    assert total == 2


def test_select_returns_none_when_all_dismissed():
    tools = [_tool("alpha")]
    chosen, _untouched, _total = pu.select_power_up(tools, used=set(), dismissed={"tool:alpha"})
    assert chosen is None


# ── dismissal persistence (entity_settings/legibility.json) ──────────────────


@pytest.fixture
def _entity_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: tmp_path)
    return tmp_path


def test_dismiss_persists_and_loads(_entity_home: Path):
    assert pu.load_dismissed() == set()
    pu.dismiss("tool:alpha")
    assert pu.load_dismissed() == {"tool:alpha"}
    # a second dismissal accumulates, no dupes
    pu.dismiss("tool:beta")
    pu.dismiss("tool:alpha")
    assert pu.load_dismissed() == {"tool:alpha", "tool:beta"}
    # persisted to the legibility entity file
    assert (_entity_home / "entity_settings" / "legibility.json").exists()


# ── compute_power_up wiring ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compute_respects_kill_switch(monkeypatch: pytest.MonkeyPatch):
    class _Cfg:
        class legibility:  # noqa: N801 - stub attr namespace
            power_ups = False

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg())
    )
    out = await pu.compute_power_up()
    assert out == {"enabled": False, "power_up": None, "untouched_count": 0, "total": 0}


@pytest.mark.asyncio
async def test_compute_returns_untouched_proposal(
    _entity_home: Path, monkeypatch: pytest.MonkeyPatch
):
    class _Cfg:
        class legibility:  # noqa: N801
            power_ups = True

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg())
    )

    async def _fake_manifest(_app):
        return {"tools": [_tool("alpha"), _tool("beta")]}

    monkeypatch.setattr("personalclaw.manifest.build_manifest", _fake_manifest)
    monkeypatch.setattr(
        "personalclaw.legibility.tool_usage.ToolUsageStore.used_names",
        lambda self: {"alpha"},
    )

    out = await pu.compute_power_up()
    assert out["enabled"] is True
    assert out["power_up"]["name"] == "beta"
    assert out["untouched_count"] == 1
    assert out["total"] == 2
