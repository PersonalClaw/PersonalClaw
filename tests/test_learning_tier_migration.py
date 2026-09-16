"""§3.5 (LEARN-R17): trajectory-variance tier migration — agentic ↔ fixed (WF2LEA-7 clause 7).

This is the clause the plan recorded BLOCKED three times, on two absent inputs that now exist. The
suite pins the producer against the SHAPE of that former block:

* tier is DERIVED from spec STRUCTURE (never a `WorkflowDef` field — that would collide with PP-16):
  a STAGE/INFER tree is agentic, a purely ACTION/TRANSFORM tree is fixed, else unknown;
* the pure decision fires with the RIGHT DIRECTION on the right variance signal — a low-variance
  agentic template distills, a repeatedly-failing fixed one promotes — and stays silent otherwise,
  the same discipline `introspection.trajectory_regression` uses below its floors;
* a draft is a PENDING `TIER_MIGRATION` proposal, deduped by the shared content fingerprint on
  re-file — never a self-install and never nagging;
* the WIRING itself: `run_end.capture` — the real terminal-run path — drives the producer, because a
  producer nothing calls is the exact "present but inert" defect this atom exists to close.

Every store/ledger/proposal test drives the REAL path under a tmp `PERSONALCLAW_HOME`; the stores
bind `config_dir` at import, so the env var is the isolation that actually holds.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.learning import proposals as P
from personalclaw.learning import run_end
from personalclaw.learning import tier_migration as tm
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    RunStatus,
    WorkflowRun,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate the proposal store, workflows store and staging ledger to a tmp home.

    `workflows.store` and the staging store bind `config_dir` at module IMPORT, so `config_dir()`
    re-reading `PERSONALCLAW_HOME` every call is what actually isolates the write path. The staging
    singleton is reset around each test — `get_store()` caches a process-global `_INSTANCE`, which
    leaks miss counts across tests in a worker otherwise.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)

    from personalclaw.learning import staging as staging_mod

    monkeypatch.setattr(staging_mod, "_INSTANCE", None)
    yield tmp_path
    staging_mod._INSTANCE = None


@pytest.fixture
def svc():
    """A live-vector MemoryService (no embedder needed — tier migration is pure ledger stats), so
    `run_end.capture` passes its `has_vector` guard and reaches the producer."""
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return MemoryService.over_vector_store(store)


# spec trees, addressed by the node `kind` values `classify_tier` reads off the structure.
_AGENTIC_SPEC = {
    "root": {
        "kind": "sequence",
        "children": [{"kind": "stage", "id": "plan"}, {"kind": "transform", "id": "shape"}],
    }
}
_FIXED_SPEC = {
    "root": {
        "kind": "sequence",
        "children": [{"kind": "action", "id": "run_cmd"}, {"kind": "transform", "id": "shape"}],
    }
}


def _run(name: str, *, status: RunStatus = RunStatus.COMPLETE) -> WorkflowRun:
    run = store_mod.create(WorkflowRun(id="", workflow_name=name))
    run.status = status
    return store_mod.save(run)


def _complete(run: WorkflowRun, node: str) -> None:
    journal_mod.Journal(run.id).step_completed(
        f"root.{node}", node, epoch=1, cache_key="", state=InstanceState.DONE
    )


def _fail(run: WorkflowRun, node: str) -> None:
    journal_mod.Journal(run.id).step_failed(
        f"root.{node}",
        node,
        epoch=1,
        failure=Failure(failure_class=FailureClass.INTERNAL, cause_plain="boom"),
        attempt=1,
        retries_exhausted=True,
    )


# ── classify_tier: tier from STRUCTURE, no stored field ──


def test_a_stage_bearing_tree_is_agentic():
    assert tm.classify_tier(_AGENTIC_SPEC) == tm.TIER_AGENTIC


def test_an_infer_node_is_also_agentic():
    assert tm.classify_tier({"root": {"kind": "infer", "id": "ask"}}) == tm.TIER_AGENTIC


def test_a_stage_nested_in_a_branch_case_is_found():
    spec = {"root": {"kind": "branch", "cases": {"a": {"kind": "stage", "id": "deep"}}}}
    assert tm.classify_tier(spec) == tm.TIER_AGENTIC


def test_a_deterministic_tree_is_fixed():
    assert tm.classify_tier(_FIXED_SPEC) == tm.TIER_FIXED


def test_a_lone_visualize_is_not_fixed():
    """`visualize` makes a model call, so a tree with one is not a purely deterministic procedure —
    and it is not an agentic STAGE either. Unknown, so nothing is migrated on a guess."""
    assert tm.classify_tier({"root": {"kind": "visualize", "id": "chart"}}) == tm.TIER_UNKNOWN


def test_a_container_only_or_missing_spec_is_unknown():
    assert tm.classify_tier({"root": {"kind": "sequence", "children": []}}) == tm.TIER_UNKNOWN
    assert tm.classify_tier(None) == tm.TIER_UNKNOWN


# ── the pure decision: right direction on the right signal, silent otherwise ──


def test_low_variance_agentic_distills():
    runs = [("same-path", False)] * 6
    mig = tm.tier_migration("summarize-inbox", tm.TIER_AGENTIC, runs, mean_cost_usd=0.10)
    assert mig is not None
    assert mig.direction == tm.DIRECTION_DISTILL
    assert (mig.from_tier, mig.to_tier) == (tm.TIER_AGENTIC, tm.TIER_FIXED)
    # cost estimate as evidence (§3.5): ~5× ratio ⇒ ~80% of the per-run LLM cost projected as saved.
    assert mig.projected_saving_usd == pytest.approx(0.10 * 0.8, abs=1e-6)


def test_a_flaky_agentic_template_is_not_distilled():
    """Freezing a failing agentic path into a rigid one bakes the failure in — §3.5 distills what
    already WORKS, so the failure ceiling must hold even at zero path variance."""
    runs = [("same-path", True)] * 3 + [("same-path", False)] * 3  # 50% failure, one path
    assert tm.tier_migration("flaky", tm.TIER_AGENTIC, runs) is None


def test_a_high_variance_agentic_template_is_not_distilled():
    runs = [(f"path-{i}", False) for i in range(6)]  # every run a different path
    assert tm.tier_migration("exploratory", tm.TIER_AGENTIC, runs) is None


def test_repeatedly_failing_fixed_template_promotes():
    runs = [("failed-path", True)] * 4 + [("clean-path", False)] * 2  # 4/6 fail, 2 path classes
    mig = tm.tier_migration("rigid-scrape", tm.TIER_FIXED, runs)
    assert mig is not None
    assert mig.direction == tm.DIRECTION_PROMOTE
    assert (mig.from_tier, mig.to_tier) == (tm.TIER_FIXED, tm.TIER_AGENTIC)
    assert mig.failing_runs == 4


def test_a_reliable_fixed_template_is_not_promoted():
    runs = [("clean-path", False)] * 5 + [("blip", True)] * 1  # one blip, below the failure floor
    assert tm.tier_migration("reliable", tm.TIER_FIXED, runs) is None


def test_below_the_sample_floor_nothing_fires():
    runs = [("same-path", False)] * 4  # textbook distill shape, but only 4 runs
    assert tm.tier_migration("young", tm.TIER_AGENTIC, runs) is None


def test_an_unknown_tier_never_migrates():
    runs = [("same-path", False)] * 8
    assert tm.tier_migration("mystery", tm.TIER_UNKNOWN, runs) is None


# ── filing: a PENDING proposal, deduped by fingerprint on re-file ──


def test_a_migration_files_a_pending_tier_migration_proposal(home):
    mig = tm.tier_migration(
        "summarize-inbox", tm.TIER_AGENTIC, [("s", False)] * 6, mean_cost_usd=0.2
    )
    pid = tm.file_tier_migration(mig, run_id="r1", evidence_refs=["r1", "r2"])
    assert pid, "the migration filed no draft"

    pending = P.list_pending(kind=P.Kind.TIER_MIGRATION.value)
    assert [p.id for p in pending] == [pid], "not a single PENDING TIER_MIGRATION row"
    row = pending[0]
    assert row.tags == ["tier_migration", tm.DIRECTION_DISTILL]
    assert "DISTILL" in row.body
    # evidence rides the manifest, not the fingerprinted body.
    assert row.change_manifest.get("evidence_refs") == ["r1", "r2"]


def test_refiling_the_same_finding_reinforces_rather_than_duplicates(home):
    mig = tm.tier_migration("rigid-scrape", tm.TIER_FIXED, [("f", True)] * 4 + [("ok", False)] * 2)
    first = tm.file_tier_migration(mig)
    second = tm.file_tier_migration(mig)  # identical body ⇒ identical fingerprint ⇒ REINFORCE
    assert first and second

    pending = P.list_pending(kind=P.Kind.TIER_MIGRATION.value)
    assert len(pending) == 1, "the fingerprint did not dedup a re-filed migration"
    assert pending[0].reinforcements >= 2


def test_a_rejected_migration_is_not_refiled(home):
    mig = tm.tier_migration("rigid-scrape", tm.TIER_FIXED, [("f", True)] * 4 + [("ok", False)] * 2)
    pid = tm.file_tier_migration(mig)
    assert P.reject(pid) is True
    # A prior REJECT is remembered by fingerprint — re-filing the same finding is silently skipped.
    assert tm.file_tier_migration(mig) == ""
    assert P.list_pending(kind=P.Kind.TIER_MIGRATION.value) == []


# ── the WIRING: run_end.capture drives the producer on a real terminal run ──


def test_capture_drives_the_tier_migration_producer(svc, home):
    """The clause that matters most: a producer nothing calls is the defect this atom closes. Seed a
    low-variance agentic template's siblings and drive the REAL terminal path end to end."""
    runs = [_run("distillable") for _ in range(6)]
    for run in runs:
        for node in ("plan", "summarize"):
            _complete(run, node)
    # The terminal run's own pinned spec is where the tier is read from.
    store_mod.write_spec(runs[-1].id, _AGENTIC_SPEC)

    report = run_end.capture(runs[-1], svc, journal=journal_mod)

    assert report["tier_migration"] == 1, report
    pending = P.list_pending(kind=P.Kind.TIER_MIGRATION.value)
    assert len(pending) == 1
    assert pending[0].tags == ["tier_migration", tm.DIRECTION_DISTILL]


def test_capture_promotes_a_repeatedly_failing_fixed_template(svc, home):
    ok = [_run("rigid") for _ in range(2)]
    bad = [_run("rigid", status=RunStatus.FAILED) for _ in range(4)]
    for run in ok:
        for node in ("run_cmd", "shape"):
            _complete(run, node)
    for run in bad:
        _complete(run, "run_cmd")
        _fail(run, "shape")
    store_mod.write_spec(bad[-1].id, _FIXED_SPEC)

    report = run_end.capture(bad[-1], svc, journal=journal_mod)

    assert report["tier_migration"] == 1, report
    pending = P.list_pending(kind=P.Kind.TIER_MIGRATION.value)
    assert pending and pending[0].tags == ["tier_migration", tm.DIRECTION_PROMOTE]


def test_capture_proposes_no_migration_for_an_unremarkable_template(svc, home):
    """A healthy agentic template that legitimately branches must not be nagged toward fixed."""
    runs = [_run("exploratory") for _ in range(6)]
    for i, run in enumerate(runs):
        _complete(run, "plan")
        _complete(run, f"branch_{i}")  # every run a different path ⇒ high variance
    store_mod.write_spec(runs[-1].id, _AGENTIC_SPEC)

    report = run_end.capture(runs[-1], svc, journal=journal_mod)

    assert report["tier_migration"] == 0
    assert P.list_pending(kind=P.Kind.TIER_MIGRATION.value) == []
