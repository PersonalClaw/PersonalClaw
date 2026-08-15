"""WF2LEA-6 — the template-refiner agent is propose-only, and its tools file, never apply.

Two enforcement layers must BOTH hold and are both asserted here: (1) the refiner's tool set
contains no write or orchestration tool, checked against the SAME classifiers the workflow leaf
posture enforces (`batch_compile.is_write_tool` + `ORCHESTRATION_TOOLS`), so adding a direct
template-write tool reds this; (2) a research-class leaf — which is what the `refine-template`
stage runs as — denies every write tool (and `workflow_author`) at the handler while admitting
the refiner's read/propose tools. And the propose tool FILES a proposal and mutates no template.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.agents import defaults as agent_defaults
from personalclaw.learning import proposals, refiner_tools
from personalclaw.workflows import versions
from personalclaw.workflows.batch_compile import (
    ORCHESTRATION_TOOLS,
    Capability,
    is_write_tool,
    leaf_tool_posture,
)
from personalclaw.workflows.bundled_defs import bundled_root


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    # The learning proposal store resolves config_dir() live, which reads PERSONALCLAW_HOME each
    # call — so the env set above is enough to keep every write inside this tmp home.
    return home


# ── layer 1: the declared tool set carries no writer or orchestrator ─────────


def test_the_refiner_tool_set_is_propose_only() -> None:
    """Every tool the refiner holds is a read or a propose — none is a write or orchestration
    tool. Uses the classifiers the runtime leaf posture actually enforces, so adding a direct
    template-write tool (e.g. `workflow_author`, in ORCHESTRATION_TOOLS, or any `*_write`) reds."""
    assert refiner_tools.REFINER_TOOL_NAMES  # non-empty: an empty set would vacuously "pass"
    for tool in refiner_tools.REFINER_TOOL_NAMES:
        assert not is_write_tool(tool), f"{tool} looks like a writer"
        assert tool not in ORCHESTRATION_TOOLS, f"{tool} is an orchestration tool"


def test_the_profile_is_seeded_and_declares_the_propose_only_set(tmp_path, monkeypatch) -> None:
    """The profile is SEEDED on load (not merely buildable) — proven by co-seeding a known
    reserved agent on the same path — and its declared tools are exactly the propose-only set."""
    import personalclaw.config.loader as _loader

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"agents": {"PersonalClaw": {}}, "default_agent": "PersonalClaw"})
    )
    monkeypatch.setattr(_loader, "config_path", lambda: cfg_path)
    monkeypatch.setattr(_loader, "config_dir", lambda: tmp_path)

    cfg = _loader.AppConfig.load()
    name = agent_defaults.TEMPLATE_REFINER_AGENT_NAME
    # Co-seeded beside the other reserved agents — proves the add-if-missing wiring ran.
    assert agent_defaults.LOOP_WORKER_AGENT_NAME in cfg.agents
    assert name in cfg.agents, "the template-refiner profile was not seeded on load"
    assert agent_defaults.is_reserved_agent(name)
    assert set(cfg.agents[name].tools) == set(refiner_tools.REFINER_TOOL_NAMES)


# ── layer 2: a research leaf denies writers but admits the refiner's tools ───


def test_a_research_leaf_denies_writers_yet_admits_the_refiner_tools() -> None:
    posture = leaf_tool_posture(Capability.RESEARCH)
    assert posture["read_only"] is True
    # workflow_author — the direct template-write path — is denied at every leaf depth.
    assert "workflow_author" in posture["denied_tools"]
    # The refiner's own tools survive a read-only leaf (neither write nor orchestration).
    for tool in refiner_tools.REFINER_TOOL_NAMES:
        assert not is_write_tool(tool)
        assert tool not in set(posture["denied_tools"])


def test_the_refine_template_stage_runs_the_refiner_agent_read_only() -> None:
    spec = json.loads((bundled_root() / "refine-template" / "workflow.json").read_text())
    stage = spec["root"]["children"][0]
    assert stage["config"]["agent"] == agent_defaults.TEMPLATE_REFINER_AGENT_NAME
    # No `capability: mutating` — so the stage is a research leaf (read-only by default).
    assert stage["config"].get("capability", "research") != "mutating"


# ── the propose tool files, and applies nothing ─────────────────────────────


def _legal_ops() -> list[dict]:
    return [{"op": "update_node", "node_id": "build", "fields": {"retries": 2}}]


def test_propose_template_diff_files_a_proposal_and_mutates_no_template() -> None:
    result = refiner_tools.file_template_diff(
        "code-project",
        ops=_legal_ops(),
        rationale="The build step fails transiently; a bounded retry clears it.",
        run_ids=["r1", "r2", "r3"],
    )
    assert result["filed"] is True
    pending = proposals.list_pending()
    assert any(p.kind == proposals.Kind.TEMPLATE_DIFF.value for p in pending)
    # It filed a PROPOSAL — no template version was written by the mere act of proposing.
    assert versions.list_versions("code-project") == []


def test_propose_template_diff_refuses_a_frozen_region_op() -> None:
    """An op touching the frozen region (what makes a template FIRE) is refused, whole-diff —
    nothing is filed. This is the S73 gate the propose tool runs before enqueue."""
    frozen = [{"op": "update_node", "node_id": "root", "fields": {"triggers": ["never"]}}]
    result = refiner_tools.file_template_diff(
        "code-project", ops=frozen, rationale="x", run_ids=["r1", "r2", "r3"]
    )
    assert result["filed"] is False
    assert result["rejected"]
    assert proposals.list_pending() == []


def test_gather_evidence_is_read_only_and_never_raises_on_empty() -> None:
    out = refiner_tools.gather_evidence("code-project")
    assert out["workflow"] == "code-project"
    assert out["clusters"] == [] and out["top_cluster"] is None
    assert out["evidence"] == []
