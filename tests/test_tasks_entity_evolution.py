"""Tests for the evolved Task entity: statused exit criteria, ordered action
plans, three note channels, the exit-criteria complete-gate, and project label
derivation from the task list."""

from unittest.mock import patch

import pytest

from personalclaw.tasks.models import (
    ExitCriteriaStatus,
    Task,
    TaskStatus,
    normalize_action_plan_item,
    normalize_exit_criterion,
    normalize_note,
)
from personalclaw.tasks.native import NativeTaskProvider


# ── Normalizers ──

class TestExitCriterionNormalize:
    def test_legacy_met_true_maps_to_complete(self):
        n = normalize_exit_criterion({"description": "tests pass", "met": True})
        assert n["status"] == ExitCriteriaStatus.COMPLETE.value
        assert n["met"] is True

    def test_legacy_met_false_maps_to_incomplete(self):
        n = normalize_exit_criterion({"description": "x", "met": False})
        assert n["status"] == ExitCriteriaStatus.INCOMPLETE.value
        assert n["met"] is False

    def test_canonical_status_kept_and_met_derived(self):
        n = normalize_exit_criterion({"description": "x", "status": "complete", "comment": "ok"})
        assert n["status"] == "complete"
        assert n["met"] is True
        assert n["comment"] == "ok"

    def test_plain_string(self):
        n = normalize_exit_criterion("just a string")
        assert n["description"] == "just a string"
        assert n["status"] == ExitCriteriaStatus.INCOMPLETE.value

    def test_criteria_key_alias(self):
        n = normalize_exit_criterion({"criteria": "via criteria key"})
        assert n["description"] == "via criteria key"


class TestActionPlanNormalize:
    def test_sequence_assigned_by_index(self):
        a = normalize_action_plan_item({"description": "step"}, 3)
        assert a["sequence"] == 3
        assert a["content"] == "step"
        assert a["description"] == "step"  # alias emitted

    def test_explicit_sequence_kept(self):
        a = normalize_action_plan_item({"content": "s", "sequence": 7}, 0)
        assert a["sequence"] == 7

    def test_completed_preserved(self):
        a = normalize_action_plan_item({"content": "s", "completed": True}, 0)
        assert a["completed"] is True


class TestNoteNormalize:
    def test_created_at_maps_to_timestamp(self):
        n = normalize_note({"content": "c", "created_at": "2026-01-01"})
        assert n["timestamp"] == "2026-01-01"

    def test_plain_string(self):
        assert normalize_note("hi")["content"] == "hi"


# ── can_mark_complete gate ──

class TestCompleteGate:
    def test_no_criteria_completable(self):
        assert Task(id="t", title="x").can_mark_complete() is True

    def test_all_complete_completable(self):
        t = Task(id="t", title="x", exit_criteria=[{"description": "a", "met": True}])
        assert t.can_mark_complete() is True

    def test_incomplete_blocks(self):
        t = Task(
            id="t",
            title="x",
            exit_criteria=[{"description": "a", "met": True}, {"description": "b", "met": False}],
        )
        assert t.can_mark_complete() is False
        assert t.incomplete_exit_criteria() == ["b"]


# ── Native provider: evolved fields + gate + derivation ──

@pytest.fixture()
def provider(tmp_path):
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path), \
         patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
        yield NativeTaskProvider()


class TestNativeEvolvedFields:
    @pytest.mark.asyncio
    async def test_note_channels_persist(self, provider):
        t = await provider.create_task(
            title="x",
            notes=[{"content": "general"}],
            research_notes=[{"content": "found something"}],
            execution_notes=[{"content": "did it"}],
        )
        reload = await provider.get_task(t.id)
        assert reload.notes[0]["content"] == "general"
        assert reload.research_notes[0]["content"] == "found something"
        assert reload.execution_notes[0]["content"] == "did it"

    @pytest.mark.asyncio
    async def test_exit_criteria_stored_statused(self, provider):
        t = await provider.create_task(
            title="x", exit_criteria=[{"description": "a", "met": True}]
        )
        d = (await provider.get_task(t.id)).to_dict()
        assert d["exit_criteria"][0]["status"] == "complete"
        assert d["exit_criteria"][0]["met"] is True

    @pytest.mark.asyncio
    async def test_complete_gate_blocks_done(self, provider):
        t = await provider.create_task(
            title="x", exit_criteria=[{"description": "ship it", "met": False}]
        )
        with pytest.raises(ValueError, match="unfinished exit criteria"):
            await provider.update_task(t.id, status="done")

    @pytest.mark.asyncio
    async def test_complete_gate_allows_when_met(self, provider):
        t = await provider.create_task(
            title="x", exit_criteria=[{"description": "ship it", "met": False}]
        )
        await provider.update_task(
            t.id, exit_criteria=[{"description": "ship it", "met": True}]
        )
        done = await provider.update_task(t.id, status="done")
        assert done.status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_project_label_is_derived_not_stored(self, provider):
        # project is a derived, read-only label. A task with no task list has NO
        # project label — an explicit `project` value (e.g. a stale loop id) is
        # never surfaced, on create or via a direct edit.
        t = await provider.create_task(title="x", project="ignored-loop-id")
        assert t.project == ""
        reloaded = await provider.get_task(t.id)
        assert reloaded.project == ""
        updated = await provider.update_task(t.id, project="hacked")
        assert updated.project == ""

    @pytest.mark.asyncio
    async def test_project_derived_from_task_list(self, provider):
        from personalclaw.tasks.hierarchy import HierarchyStore

        store = HierarchyStore()
        proj = store.create_project("Website")
        tl = store.create_task_list("Launch", project_id=proj.id)
        t = await provider.create_task(title="x", task_list_id=tl.id)
        assert t.project == "Website"
        # …and it self-heals on read: rename the project, re-read the task.
        store.update_project(proj.id, name="Website v2")
        assert (await provider.get_task(t.id)).project == "Website v2"

    @pytest.mark.asyncio
    async def test_stale_stored_project_id_does_not_leak(self, provider):
        # A legacy task whose JSON has a raw project id in `project` and no task
        # list must read back with an empty label (not the opaque id).
        t = await provider.create_task(title="legacy", task_list_id="")
        # simulate the pre-reform on-disk shape: a project id stamped in `project`
        import json
        p = provider._task_path(t.id)
        data = json.loads(p.read_text())
        data["project"] = "p-deadbeef-xy"
        p.write_text(json.dumps(data))
        assert (await provider.get_task(t.id)).project == ""
