"""Tests for the evolved Task entity: statused exit criteria, ordered action
plans, three note channels, the exit-criteria complete-gate, and project label
derivation from the task list."""

import json
import re
import time
from unittest.mock import patch

import pytest

from personalclaw.tasks.models import (
    ExitCriteriaStatus,
    Task,
    TaskStatus,
    coerce_task_field,
    normalize_action_plan_item,
    normalize_exit_criterion,
    normalize_note,
)
from personalclaw.tasks.native import NativeTaskProvider

#: The `created_at` spelling — a note's time must be comparable to its task's.
_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_NOTE_LANES = ("notes", "research_notes", "execution_notes")

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

    def test_stamp_dates_an_undated_note(self):
        """#383: nothing ever supplied a note timestamp, so the detail panel's per-note
        relative time could never render."""
        for item in ("bare string", {"content": "c"}):
            assert _ISO_Z.match(normalize_note(item, stamp=True)["timestamp"]), item

    def test_stamp_leaves_a_key_present_empty_timestamp_undated(self):
        """#3265: key presence identifies a note the form read back from the server."""
        note = normalize_note({"content": "legacy", "timestamp": ""}, stamp=True)
        assert note["timestamp"] == ""

    def test_stamp_never_overwrites_an_incoming_timestamp(self):
        """The create/edit form sends the whole lane back, so a re-send must not re-date."""
        n = normalize_note({"content": "c", "timestamp": "2026-01-01T00:00:00Z"}, stamp=True)
        assert n["timestamp"] == "2026-01-01T00:00:00Z"
        assert (
            normalize_note({"content": "c", "created_at": "2026-01-02"}, stamp=True)["timestamp"]
            == "2026-01-02"
        )

    def test_default_does_not_stamp(self):
        """`to_dict` normalizes on every read; stamping there would mint a fresh "now" per
        response and date a legacy note with the moment it was first read."""
        assert normalize_note({"content": "c"})["timestamp"] == ""


class TestNoteCoercionStampsWritesOnly:
    """`strict` IS the write/read discriminator (`coerce_task_field`), and the stamp rides it."""

    @pytest.mark.parametrize("field_name", ["notes", "research_notes", "execution_notes"])
    def test_write_stamps_all_three_lanes(self, field_name):
        out = coerce_task_field(field_name, [{"content": "c"}], strict=True)
        assert _ISO_Z.match(out[0]["timestamp"])

    @pytest.mark.parametrize("field_name", ["notes", "research_notes", "execution_notes"])
    def test_read_leaves_a_legacy_note_undated(self, field_name):
        out = coerce_task_field(field_name, [{"content": "c", "timestamp": ""}], strict=False)
        assert out[0]["timestamp"] == ""

    def test_note_stamp_format_matches_the_task_clock(self):
        """Pinned because `_as_note_list`'s contract is that a note's time is comparable to its
        task's `created_at` and parses in `relTime` identically — two separate module-private
        `_now_iso` helpers, one format. Clock frozen so this pins the FORMAT, not the second.

        Imported INSIDE the test on purpose: at module scope a missing `models._now_iso` is a
        collection error that takes the whole file down, which would make a negative control over
        this file coarse enough to hide which tests actually discriminate.
        """
        from personalclaw.tasks.models import _now_iso as models_now_iso
        from personalclaw.tasks.native import _now_iso as native_now_iso

        with patch("time.gmtime", return_value=time.gmtime(0)):
            assert models_now_iso() == native_now_iso() == "1970-01-01T00:00:00Z"


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
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        yield NativeTaskProvider()


def _stored_task(provider, task_id):
    return json.loads(provider._task_path(task_id).read_text())


def _forge_legacy_note(provider, task_id, field_name):
    data = _stored_task(provider, task_id)
    data[field_name] = [{"content": f"legacy {field_name}", "timestamp": ""}]
    provider._task_path(task_id).write_text(json.dumps(data))


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
    async def test_note_channels_persist_a_timestamp(self, provider):
        """#383: the create form's three note lanes round-tripped content but never a time, so
        `TaskDetail`'s per-note `relTime` render was unreachable on every note ever written.
        """
        t = await provider.create_task(
            title="x",
            notes=["general"],
            research_notes=[{"content": "found something"}],
            execution_notes=[{"content": "did it"}],
        )
        d = (await provider.get_task(t.id)).to_dict()
        for lane in ("notes", "research_notes", "execution_notes"):
            assert _ISO_Z.match(d[lane][0]["timestamp"]), lane

    @pytest.mark.asyncio
    async def test_a_reread_does_not_re_date_a_note(self, provider):
        """A read must be idempotent: `to_dict`/`from_dict` normalize on every response, so a
        stamp that did not ride `strict` would hand two reads of one task different times.
        """
        t = await provider.create_task(title="x", notes=[{"content": "c"}])
        first = (await provider.get_task(t.id)).to_dict()["notes"][0]["timestamp"]
        with patch("time.gmtime", return_value=time.gmtime(0)):
            again = (await provider.get_task(t.id)).to_dict()["notes"][0]["timestamp"]
        assert again == first

    @pytest.mark.asyncio
    async def test_appending_a_note_dates_only_the_new_one(self, provider):
        """The edit form sends the whole lane back, existing timestamps included."""
        t = await provider.create_task(title="x", notes=[{"content": "first"}])
        kept = (await provider.get_task(t.id)).to_dict()["notes"][0]
        updated = await provider.update_task(t.id, notes=[kept, {"content": "second"}])
        notes = updated.to_dict()["notes"]
        assert notes[0]["timestamp"] == kept["timestamp"]
        assert _ISO_Z.match(notes[1]["timestamp"])

    @pytest.mark.asyncio
    async def test_a_plain_read_leaves_legacy_notes_undated(self, provider):
        """A: reading an old task must not invent dates for any note lane."""
        task = await provider.create_task(title="legacy")
        for field_name in _NOTE_LANES:
            _forge_legacy_note(provider, task.id, field_name)

        loaded = (await provider.get_task(task.id)).to_dict()

        for field_name in _NOTE_LANES:
            assert loaded[field_name][0]["timestamp"] == ""

    @pytest.mark.asyncio
    async def test_b_edit_without_note_fields_leaves_legacy_notes_undated(self, provider):
        """B: an edit that does not resend notes must leave their persisted dates alone."""
        task = await provider.create_task(title="legacy")
        for field_name in _NOTE_LANES:
            _forge_legacy_note(provider, task.id, field_name)

        await provider.update_task(task.id, title="renamed")

        stored = _stored_task(provider, task.id)
        for field_name in _NOTE_LANES:
            assert stored[field_name][0]["timestamp"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field_name", _NOTE_LANES)
    async def test_c_resending_a_legacy_lane_keeps_its_empty_timestamp(self, provider, field_name):
        """C: the edit form resends each existing note with ``timestamp: ""``."""
        task = await provider.create_task(title="legacy")
        _forge_legacy_note(provider, task.id, field_name)
        resent = (await provider.get_task(task.id)).to_dict()[field_name]

        await provider.update_task(task.id, **{field_name: resent})

        assert _stored_task(provider, task.id)[field_name][0]["timestamp"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field_name", _NOTE_LANES)
    async def test_d_append_dates_only_the_keyless_note(self, provider, field_name):
        """D: key-present legacy + keyless append is the old-vs-new discriminator."""
        task = await provider.create_task(title="legacy")
        _forge_legacy_note(provider, task.id, field_name)
        legacy = (await provider.get_task(task.id)).to_dict()[field_name][0]

        await provider.update_task(
            task.id,
            **{field_name: [legacy, {"content": f"new {field_name}"}]},
        )

        stored = _stored_task(provider, task.id)[field_name]
        assert stored[0]["timestamp"] == ""
        assert _ISO_Z.match(stored[1]["timestamp"])

    @pytest.mark.asyncio
    async def test_exit_criteria_stored_statused(self, provider):
        t = await provider.create_task(title="x", exit_criteria=[{"description": "a", "met": True}])
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
        await provider.update_task(t.id, exit_criteria=[{"description": "ship it", "met": True}])
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
