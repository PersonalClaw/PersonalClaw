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


# ── layer 3: the mechanism FIRES — the run key the power floor counts ─────────


def _seed_skips(workflow: str, skips: list[tuple[str, str, str]]) -> None:
    """Real runs in the run store, real `step_skipped` rows through the real ledger writer.

    Nothing hand-built: the whole class of defect this section pins is a reader and a fixture
    agreeing on a key the ENGINE never emits, so a fixture is not admissible evidence here.
    """
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.journal import Journal
    from personalclaw.workflows.models import WorkflowRun

    for run_id, path, node_id in skips:
        if wf_store.get(run_id) is None:
            wf_store.create(WorkflowRun(id=run_id, workflow_name=workflow))
        Journal(run_id).step_skipped(path, node_id, epoch=0, actor="user")


def test_a_repeatedly_skipped_step_REACHES_the_agent_as_a_top_cluster() -> None:
    """The load-bearing end-to-end: the refiner's mechanism actually fires.

    `Cluster.distinct_runs` is a CROSS-RUN count, but a ledger record is run-scoped by directory
    and the writer never stamps `run_id` on a row. `gather_evidence` — which holds `run.id` in its
    own loop — has to supply it. It did not, so every event arrived as `run_id=""`, every cluster
    reported `distinct_runs=1`, `top_cluster` was permanently `None`, and the `refine-template`
    agent prompt's step 2 (*"If there is no top cluster, STOP and propose nothing"*) was the only
    branch reachable for any template, forever. Node attribution was necessary but not sufficient.

    Driven through `mcp_core._call_tool("refiner_evidence", ...)` — the tool the agent actually
    calls (declared `mcp_core.py`, held by `agents/defaults.TEMPLATE_REFINER_TOOLS`, driven by
    `workflows/bundled/refine-template/workflow.json`) — so this asserts the CALL SITE, not the
    arithmetic of a helper the shipped path might not reach.
    """
    from personalclaw import mcp_core
    from personalclaw.workflows.journal import ledger

    workflow = "daily-digest"
    _seed_skips(
        workflow,
        [
            ("skipfire0", "root.children[0]", "summarize"),
            ("skipfire1", "root.children[0]", "summarize"),
            ("skipfire2", "root.children[0]", "summarize"),
            ("skipfire3", "root.children[1]", "translate"),
        ],
    )

    # ── the vacuity floor ──
    # The injection is only load-bearing because the WRITER omits `run_id`. Assert that against a
    # real row, so a regression that drops the injection cannot pass on rows that carry it anyway.
    raw = ledger("skipfire0")
    assert raw and "run_id" not in raw[0], f"writer already stamps run_id: {raw[0]}"
    assert raw[0]["node_id"] == "summarize"  # the attribution half, still holding

    out = json.loads(mcp_core._call_tool("refiner_evidence", {"workflow_name": workflow}))
    assert out["ok"] is True

    top = out["top_cluster"]
    assert top is not None, f"the mechanism did not fire; clusters were {out['clusters']}"
    # It NAMES the step — the whole point of a cluster is that a template op can target it.
    assert top["node"] == "summarize"
    assert top["signature"] == "skipped summarize"
    assert top["count"] == 3
    # Real run ids, not the anonymous bucket: this is what `distinct_runs` was counting wrong.
    assert top["distinct_runs"] == 3
    assert set(top["run_ids"]) == {"skipfire0", "skipfire1", "skipfire2"}
    assert "" not in set(top["run_ids"])


def test_one_run_is_an_ANECDOTE_and_still_proposes_nothing() -> None:
    """The other direction. §3.1's power discipline: `MIN_RUNS_FOR_EVIDENCE = 3` because below it
    "a 'pattern' is one bad afternoon, and a template edited from it is a template edited from
    noise". Injecting the run key must not smuggle a one-run cluster past that floor, so the same
    surface is asserted to STAY silent — three skips of one node inside a SINGLE run.

    Note the floor is distinct RUNS, not occurrences: `count == 3` here and it is still refused.
    """
    from personalclaw import mcp_core

    workflow = "one-run-only"
    _seed_skips(
        workflow,
        [
            ("anecdote0", "root.children[0]", "summarize"),
            ("anecdote0", "root.children[0]", "summarize"),
            ("anecdote0", "root.children[0]", "summarize"),
        ],
    )

    out = json.loads(mcp_core._call_tool("refiner_evidence", {"workflow_name": workflow}))

    # ── the vacuity floor ──
    # `None` must come from the FLOOR, not from an empty evidence set: a test where nothing was
    # clustered at all would assert `None` for the wrong reason and pass a broken reader.
    assert len(out["clusters"]) == 1
    only = out["clusters"][0]
    assert only["node"] == "summarize" and only["count"] == 3 and only["rank"] > 0
    assert only["distinct_runs"] == 1 and only["run_ids"] == ["anecdote0"]

    assert out["top_cluster"] is None, "one run's evidence cleared the power floor"


def test_an_abandoned_run_is_attributed_like_every_other_event() -> None:
    """`run_abandoned` stamped `at_node_id` — the ledger's only divergent spelling of the node
    field — so an abandoned run clustered under the anonymous `""` node even after the
    `node_id`/`instance_path` read landed. Reconciled at the EMITTER rather than by teaching the
    reader a third spelling, which would be the dual path the clean-break tenet forbids.
    """
    from personalclaw.learning import refiner
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.journal import Journal, ledger
    from personalclaw.workflows.models import WorkflowRun

    wf_store.create(WorkflowRun(id="abandoned0", workflow_name="stalled"))
    Journal("abandoned0").run_abandoned("review", elapsed_secs=12.5)

    row = next(r for r in ledger("abandoned0") if r["kind"] == "run_abandoned")
    # ── the vacuity floor ──
    # The rename is only meaningful if the OLD spelling is gone from the row; a writer stamping
    # both would let the reader keep working while the vocabulary stayed split.
    assert "at_node_id" not in row, f"the third spelling is still emitted: {row}"
    assert row["node_id"] == "review"

    clusters = refiner.cluster_failures([{**row, "run_id": "abandoned0"}])
    assert [c.node for c in clusters] == ["review"]
