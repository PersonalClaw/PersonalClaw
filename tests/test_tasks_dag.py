"""P5a — typed task DAG: dependencies, cycle rejection, status reconciliation,
block-reason derivation, and dependency analysis (seam S3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import personalclaw.tasks.native as nat
from personalclaw.tasks import reconcile
from personalclaw.tasks.models import (
    DependencyType,
    Task,
    TaskDependency,
    TaskPriority,
    TaskStatus,
)


@pytest.fixture
def provider(tmp_path):
    with patch.object(nat, "config_dir", lambda: tmp_path):
        yield nat.NativeTaskProvider()


def _run(coro):
    return asyncio.run(coro)


# ── model ──

class TestTaskModel:
    def test_priority_normalizes_unknown(self):
        assert TaskPriority.normalize("nonsense") == TaskPriority.MEDIUM
        assert TaskPriority.normalize("CRITICAL") == TaskPriority.CRITICAL
        assert TaskPriority.normalize(TaskPriority.LOW) == TaskPriority.LOW

    def test_from_dict_migrates_flat_depends_on(self):
        t = Task.from_dict({"id": "t1", "title": "x", "depends_on": ["a", "b"]})
        assert [d.depends_on_task_id for d in t.dependencies] == ["a", "b"]
        assert all(d.dependency_type == DependencyType.BLOCKS for d in t.dependencies)

    def test_from_dict_prefers_typed_dependencies(self):
        t = Task.from_dict({
            "id": "t1", "title": "x",
            "dependencies": [{"depends_on_task_id": "a", "dependency_type": "REQUIRED_FOR"}],
            "depends_on": ["ignored"],
        })
        assert len(t.dependencies) == 1
        assert t.dependencies[0].dependency_type == DependencyType.REQUIRED_FOR

    def test_task_wraps_bare_scalar_exit_criteria_and_action_plan(self):
        # A bare-string exit_criteria/action_plan (LLM mistake) must become ONE item,
        # not N single-char items — char-criteria can never be 'met' so the task would
        # be permanently un-completable.
        t = Task(id="t1", title="x", exit_criteria="tests pass", action_plan="do it")
        ec = t.to_dict()["exit_criteria"]
        ap = t.to_dict()["action_plan"]
        assert [c["description"] for c in ec] == ["tests pass"]
        assert [a["content"] for a in ap] == ["do it"]
        # a proper list is untouched
        t2 = Task(id="t2", title="y", exit_criteria=["a", "b"])
        assert [c["description"] for c in t2.to_dict()["exit_criteria"]] == ["a", "b"]

    def test_coerce_dependencies_wraps_bare_scalar(self):
        # An LLM passing depends_on as a bare "task-123" (not ["task-123"]) must yield
        # ONE edge — not 8 garbage single-char edges from iterating the string, which
        # would block the task on nonexistent tasks "t","a","s",… forever.
        coerce = nat.NativeTaskProvider._coerce_dependencies
        assert [d.depends_on_task_id for d in coerce("task-123")] == ["task-123"]
        assert [d.depends_on_task_id for d in coerce({"depends_on_task_id": "t-9"})] == ["t-9"]
        assert [d.depends_on_task_id for d in coerce(["a", "b"])] == ["a", "b"]
        assert coerce(None) == []

    def test_to_dict_has_no_depends_on(self):
        t = Task(id="t1", title="x", dependencies=[TaskDependency("a")])
        d = t.to_dict()
        assert "depends_on" not in d
        assert d["dependencies"] == [{"depends_on_task_id": "a", "dependency_type": "BLOCKS"}]
        assert d["priority"] == "medium"

    def test_prerequisite_ids_only_blocks(self):
        t = Task(id="t1", title="x", dependencies=[
            TaskDependency("a", DependencyType.BLOCKS),
            TaskDependency("b", DependencyType.REQUIRED_FOR),
        ])
        assert t.prerequisite_ids() == ["a"]


# ── reconcile engine (pure) ──

class TestReconcile:
    def _tasks(self, *specs):
        return {s["id"]: Task.from_dict(s) for s in specs}

    def test_detect_cycle(self):
        tasks = self._tasks(
            {"id": "a", "title": "a", "depends_on": ["b"]},
            {"id": "b", "title": "b", "depends_on": ["a"]},
        )
        assert reconcile.detect_cycle(tasks)

    def test_no_cycle(self):
        tasks = self._tasks(
            {"id": "a", "title": "a"},
            {"id": "b", "title": "b", "depends_on": ["a"]},
        )
        assert reconcile.detect_cycle(tasks) == []

    def test_would_create_cycle(self):
        tasks = self._tasks(
            {"id": "a", "title": "a"},
            {"id": "b", "title": "b", "depends_on": ["a"]},
        )
        # giving a a prereq of b closes the loop
        assert reconcile.would_create_cycle(tasks, "a", ["b"])
        assert reconcile.would_create_cycle(tasks, "a", []) == []

    def test_auto_block_then_unblock_cascade(self):
        tasks = self._tasks(
            {"id": "a", "title": "a", "status": "open"},
            {"id": "b", "title": "b", "status": "open", "depends_on": ["a"]},
            {"id": "c", "title": "c", "status": "open", "depends_on": ["b"]},
        )
        # reconcile from a: b blocks (a open); c blocks (b not terminal)
        reconcile.reconcile_blocked_status(tasks, "a")
        assert tasks["b"].status == TaskStatus.BLOCKED
        assert tasks["c"].status == TaskStatus.BLOCKED
        # finish a → b unblocks, but c stays blocked (b still open)
        tasks["a"].status = TaskStatus.DONE
        reconcile.reconcile_blocked_status(tasks, "a")
        assert tasks["b"].status == TaskStatus.OPEN
        assert tasks["c"].status == TaskStatus.BLOCKED
        # finish b → c unblocks
        tasks["b"].status = TaskStatus.DONE
        reconcile.reconcile_blocked_status(tasks, "b")
        assert tasks["c"].status == TaskStatus.OPEN

    def test_manual_block_never_auto_cleared(self):
        tasks = self._tasks(
            {"id": "a", "title": "a", "status": "done"},
            {"id": "b", "title": "b", "status": "blocked", "blocked_reason_kind": "manual",
             "depends_on": ["a"]},
        )
        reconcile.reconcile_blocked_status(tasks, "a")
        assert tasks["b"].status == TaskStatus.BLOCKED  # manual wins

    def test_cancel_prerequisite_unblocks(self):
        tasks = self._tasks(
            {"id": "a", "title": "a", "status": "open"},
            {"id": "b", "title": "b", "status": "open", "depends_on": ["a"]},
        )
        reconcile.reconcile_blocked_status(tasks, "a")
        assert tasks["b"].status == TaskStatus.BLOCKED
        tasks["a"].status = TaskStatus.CANCELLED  # terminal → resolves the blocker
        reconcile.reconcile_blocked_status(tasks, "a")
        assert tasks["b"].status == TaskStatus.OPEN

    def test_classify_manual_block(self):
        tasks = self._tasks({"id": "a", "title": "a", "status": "open"})
        t = tasks["a"]
        t.status = TaskStatus.BLOCKED
        reconcile.classify_manual_block(t, tasks)
        assert t.blocked_reason_kind == "manual"  # no prereqs → manual

    def test_analyze_completion_and_critical_path(self):
        tasks = self._tasks(
            {"id": "a", "title": "a", "status": "done"},
            {"id": "b", "title": "b", "depends_on": ["a"]},
            {"id": "c", "title": "c", "depends_on": ["b"]},
        )
        analysis = reconcile.analyze(tasks)
        assert analysis.completion_pct == pytest.approx(33.3, abs=0.1)
        assert analysis.critical_path == ["a", "b", "c"]
        assert "a" in analysis.root_task_ids and "c" in analysis.leaf_task_ids
        assert analysis.cycles == []


# ── native provider integration ──

class TestNativeProviderDag:
    def test_create_auto_blocks_dependent(self, provider):
        async def go():
            a = await provider.create_task(title="A")
            b = await provider.create_task(
                title="B", dependencies=[{"depends_on_task_id": a.id}])
            b2 = await provider.get_task(b.id)
            assert b2.status == TaskStatus.BLOCKED and b2.blocked_reason_kind == "auto"
        _run(go())

    def test_create_rejects_cycle(self, provider):
        async def go():
            a = await provider.create_task(title="A")
            b = await provider.create_task(
                title="B", dependencies=[{"depends_on_task_id": a.id}])
            with pytest.raises(reconcile.DependencyCycleError):
                await provider.update_task(a.id, dependencies=[{"depends_on_task_id": b.id}])
        _run(go())

    def test_finish_prereq_returns_reconciled_set(self, provider):
        async def go():
            a = await provider.create_task(title="A")
            b = await provider.create_task(
                title="B", dependencies=[{"depends_on_task_id": a.id}])
            upd = await provider.update_task(a.id, status="done")
            rec = {t.id: t for t in upd._reconciled}
            assert b.id in rec and rec[b.id].status == TaskStatus.OPEN
        _run(go())

    def test_delete_prereq_unblocks(self, provider):
        async def go():
            a = await provider.create_task(title="A")
            b = await provider.create_task(
                title="B", dependencies=[{"depends_on_task_id": a.id}])
            await provider.delete_task(a.id)
            b2 = await provider.get_task(b.id)
            assert b2.status == TaskStatus.OPEN
            assert b2.prerequisite_ids() == []  # dangling edge cleaned up
        _run(go())

    def test_graph_edges_and_analysis(self, provider):
        async def go():
            a = await provider.create_task(title="A")
            await provider.create_task(title="B", dependencies=[{"depends_on_task_id": a.id}])
            g = provider.graph()
            assert len(g["edges"]) == 1
            assert g["edges"][0]["type"] == "BLOCKS"
            assert "completion_pct" in g["analysis"]
        _run(go())
