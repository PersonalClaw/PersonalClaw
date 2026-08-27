"""The rail for the task write-path coercion class (#387, #386, #388, #818).

**The class.** A task field was coerced in some places and not others, in BOTH the read and the
write path, and the inconsistency was the bug rather than any single missing check. Three
different places enumerated the fields — ``Task.from_dict``, ``NativeTaskProvider.create_task``
and ``update_task`` — and they disagreed:

* ``from_dict`` coerced ``order`` with ``float()`` and ``preview`` with ``str()``, and took
  ``title``, ``description`` and ``labels`` verbatim;
* ``create_task`` had its own per-field rules, and its own comment recording that a new model
  field is silently dropped on create unless someone remembers to name it;
* ``update_task`` had typed branches for three fields and, for everything else, a catch-all
  ``setattr(task, key, val)`` behind a ``hasattr`` check — no type check at all, and
  ``__post_init__``'s normalization only ever runs at construction, so an update was the one
  write that never re-validated.

What that accepted, each its own report and each reproduced before this suite was written:

===================================  ===============================================================
``PUT {"order": "abc"}``             200, then every read raised inside a bare ``except`` — the task
                                     answered 404 on GET/DELETE/list while its file stayed on disk
                                     holding its id (#387)
``labels: "not-an-array"``           persisted bare through BOTH create and update;
                                     ``labels.slice(...).map`` took the whole Tasks page into an
                                     error boundary (#386)
``PUT {"description": 12345}``       ``(12345).lower()`` in the search scorer, so
                                     ``POST /api/tasks/search`` answered 500 for EVERY query, not
                                     only ones that would match the poisoned task (#388)
``PUT {"exit_criteria": "via put"}`` iterated the string CHARACTER BY CHARACTER — one un-meetable
                                     criterion per letter, so the task could never be completed
                                     (#818)
===================================  ===============================================================

**What this suite guards.**

1. The table is EXHAUSTIVE over ``Task``'s fields. This is what makes the fix extensible rather
   than a sweep: a field added to ``Task`` without a coercer reds here instead of becoming the
   next field nobody coerced.
2. Read salvages, write refuses — and both go through the same table, because two functions
   would drift.
3. Each reported value is refused or normalized at BOTH ends, create and update, so the
   asymmetry that let one path accept what the other rejected cannot come back.
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.tasks.models import (
    TASK_FIELD_COERCERS,
    Task,
    TaskPriority,
    TaskStatus,
    coerce_task_field,
)
from personalclaw.tasks.native import NativeTaskProvider


@pytest.fixture()
def provider(tmp_path):
    with patch("personalclaw.tasks.native.config_dir", return_value=tmp_path):
        yield NativeTaskProvider()


def _stored(tmp_path: Path, task_id: str) -> dict:
    return json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))


# ── 1. the table is exhaustive ────────────────────────────────────────────────


def test_every_task_field_has_a_coercer():
    """The property that makes this durable. Without it, the next field added to `Task` is the
    next field nobody coerces — which is exactly how this class opened."""
    declared = {f.name for f in dataclass_fields(Task)}
    assert set(TASK_FIELD_COERCERS) == declared, (
        f"missing a coercer: {sorted(declared - set(TASK_FIELD_COERCERS))}; "
        f"coercer for a field that no longer exists: "
        f"{sorted(set(TASK_FIELD_COERCERS) - declared)}"
    )


def test_the_table_is_not_vacuously_small():
    """Vacuity floor: an empty table would satisfy `set(x) == set(y)` if `Task` were empty, and
    would make every assertion below pass by never coercing anything."""
    assert len(TASK_FIELD_COERCERS) >= 25


def test_an_unknown_field_name_is_always_a_refusal():
    for strict in (True, False):
        with pytest.raises(ValueError, match="unknown task field"):
            coerce_task_field("not_a_field", "x", strict=strict)


# ── 2. read salvages, write refuses ──────────────────────────────────────────


class TestReadSalvages:
    """`from_dict` must NEVER raise on a persisted value it cannot use.

    "A broken row never disappears" is this store's stated rule, and a record that 404s is worse
    than one with a defaulted field: you cannot fix, or even see, what the API calls absent. This
    is also what recovers a home ALREADY poisoned by the bug, with no migration.
    """

    def test_an_unusable_order_loads_as_the_default_instead_of_raising(self):
        task = Task.from_dict({"id": "t1", "title": "T", "order": "abc"})
        assert task.order == 0.0

    def test_a_bare_string_labels_loads_as_one_label(self):
        assert Task.from_dict({"id": "t1", "title": "T", "labels": "one"}).labels == ["one"]

    def test_an_object_inside_labels_cannot_reach_a_renderer(self):
        """`[{"a": 1}, 42]` produced React error #31 — an object rendered as a child."""
        task = Task.from_dict({"id": "t1", "title": "T", "labels": [{"a": 1}, 42]})
        assert all(isinstance(x, str) for x in task.labels)

    def test_a_numeric_description_loads_as_text(self):
        """This is what un-poisons search: the scorer's `.lower()` gets a string."""
        assert Task.from_dict({"id": "t1", "title": "T", "description": 12345}).description == (
            "12345"
        )

    def test_a_bare_string_exit_criteria_is_ONE_criterion_not_one_per_letter(self):
        task = Task.from_dict({"id": "t1", "title": "T", "exit_criteria": "tests pass"})
        assert [c["description"] for c in task.exit_criteria] == ["tests pass"]

    def test_an_unusable_status_loads_as_open(self):
        assert (
            Task.from_dict({"id": "t1", "title": "T", "status": "nope"}).status is TaskStatus.OPEN
        )

    def test_a_container_where_text_belongs_loads_as_empty_not_as_its_repr(self):
        """`str({"a": 1})` is `"{'a': 1}"` — a string, and never what anyone meant."""
        assert Task.from_dict({"id": "t1", "title": {"a": 1}}).title == ""


class TestWriteRefuses:
    @pytest.mark.parametrize(
        "value", ["abc", "", None, {"a": 1}, [1], True, float("nan")], ids=repr
    )
    def test_order_accepts_only_a_number(self, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return  # nan IS a float; the type is what this guards
        if value in ("", None):
            assert coerce_task_field("order", value) == 0.0  # an omitted order is the default
            return
        with pytest.raises(ValueError):
            coerce_task_field("order", value)

    def test_text_refuses_a_container(self):
        for field in ("title", "description", "assignee", "due", "preview"):
            with pytest.raises(ValueError):
                coerce_task_field(field, {"a": 1})
            with pytest.raises(ValueError):
                coerce_task_field(field, ["a"])

    def test_text_accepts_a_number_because_that_slip_has_an_obvious_reading(self):
        assert coerce_task_field("description", 12345) == "12345"

    def test_an_invalid_status_names_the_valid_set(self):
        with pytest.raises(ValueError, match="in_progress"):
            coerce_task_field("status", "completed")

    def test_priority_normalizes_rather_than_refusing(self):
        """Priority has always had a total normalizer, and a wrong priority is not corrupting —
        so this one coerces where the others refuse, and the table records that difference."""
        assert coerce_task_field("priority", "nonsense") == TaskPriority.normalize("nonsense")


# ── 3. both ends, create and update ──────────────────────────────────────────


class TestBothEndsAgree:
    """The asymmetry is the thing to prevent: whatever one path does with a value, the other must
    do too. Each case is asserted through the PROVIDER, not the coercer, so "the provider actually
    calls it" is covered — which is the half that was missing."""

    @pytest.mark.asyncio
    async def test_create_and_update_both_normalize_labels(self, provider, tmp_path):
        created = await provider.create_task(title="t", labels="not-an-array")
        assert _stored(tmp_path, created.id)["labels"] == ["not-an-array"]
        await provider.update_task(created.id, labels="via-put")
        assert _stored(tmp_path, created.id)["labels"] == ["via-put"]

    @pytest.mark.asyncio
    async def test_create_and_update_both_refuse_an_unusable_order(self, provider):
        with pytest.raises(ValueError):
            await provider.create_task(title="t", order="abc")
        created = await provider.create_task(title="t")
        with pytest.raises(ValueError):
            await provider.update_task(created.id, order="abc")

    @pytest.mark.asyncio
    async def test_a_refused_update_leaves_the_task_readable(self, provider):
        """#387's real harm. The write was accepted and the task then 404'd on GET **and DELETE**
        while its file stayed on disk — unreachable and unremovable through any API."""
        created = await provider.create_task(title="t")
        with pytest.raises(ValueError):
            await provider.update_task(created.id, order="abc")
        assert await provider.get_task(created.id) is not None
        assert await provider.delete_task(created.id) is True

    @pytest.mark.asyncio
    async def test_create_and_update_both_normalize_exit_criteria(self, provider, tmp_path):
        created = await provider.create_task(title="t", exit_criteria="tests pass")
        assert [c["description"] for c in _stored(tmp_path, created.id)["exit_criteria"]] == [
            "tests pass"
        ]
        await provider.update_task(created.id, exit_criteria="via put")
        assert [c["description"] for c in _stored(tmp_path, created.id)["exit_criteria"]] == [
            "via put"
        ]

    @pytest.mark.asyncio
    async def test_notes_are_normalized_on_both_ends(self, provider, tmp_path):
        """#818's third clause: the note channels were uncoerced even on create."""
        created = await provider.create_task(title="t", notes="a thought")
        assert _stored(tmp_path, created.id)["notes"] == [{"content": "a thought", "timestamp": ""}]
        await provider.update_task(created.id, research_notes="found it")
        stored = _stored(tmp_path, created.id)["research_notes"]
        assert stored == [{"content": "found it", "timestamp": ""}]


class TestShapesThisModuleDoesNotOwn:
    """`evidence` and `attempts` carry whatever the ENGINE records, so they get list-ification and
    nothing more.

    Pinned because the first draft of the coercion table routed them through `normalize_note` and
    rewrote `{"kind": "gate", "node": "check"}` to `{"content": "", "timestamp": ""}` — destroying
    the record it was supposed to be protecting. `test_workflows_materialize` caught it, and this
    is the local statement of the rule so the next person reading the table sees why two entries
    differ: a coercion table is only safe where the shape is actually known.
    """

    def test_evidence_keeps_its_own_keys(self):
        task = Task.from_dict(
            {"id": "t1", "title": "T", "evidence": [{"kind": "gate", "node": "check"}]}
        )
        assert task.evidence == [{"kind": "gate", "node": "check"}]
        assert coerce_task_field("evidence", [{"kind": "gate"}]) == [{"kind": "gate"}]

    def test_attempts_keep_their_own_keys(self):
        assert coerce_task_field("attempts", [{"n": 2, "error": "timeout"}]) == [
            {"n": 2, "error": "timeout"}
        ]

    def test_a_bare_dict_is_still_wrapped(self):
        assert coerce_task_field("evidence", {"kind": "gate"}) == [{"kind": "gate"}]

    def test_a_non_object_item_is_refused_on_write_and_dropped_on_read(self):
        with pytest.raises(ValueError):
            coerce_task_field("evidence", ["not-an-object"])
        assert coerce_task_field("evidence", ["not-an-object"], strict=False) == []


class TestPoisonedRecordRecovery:
    """A record written by the bug, before the fix. The write guard cannot help it — only the read
    salvage can, and without that a user with a poisoned home stays stuck."""

    @pytest.mark.asyncio
    async def test_a_poisoned_task_on_disk_is_listable_editable_and_deletable(
        self, provider, tmp_path
    ):
        (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tasks" / "t-poison.json").write_text(
            json.dumps(
                {
                    "id": "t-poison",
                    "title": "poisoned",
                    "order": "abc",
                    "labels": "not-an-array",
                    "description": 12345,
                }
            )
        )
        listed, _total = await provider.list_tasks()
        assert "t-poison" in {t.id for t in listed}
        assert await provider.get_task("t-poison") is not None
        assert await provider.update_task("t-poison", title="fixed") is not None
        assert await provider.delete_task("t-poison") is True

    @pytest.mark.asyncio
    async def test_a_poisoned_task_does_not_break_search_for_every_query(self, provider, tmp_path):
        """#388: the query had nothing to do with the poisoned task. EVERY search 500'd."""
        from personalclaw.tasks import registry

        (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tasks" / "t-poison.json").write_text(
            json.dumps({"id": "t-poison", "title": "poisoned", "description": 12345})
        )
        await provider.create_task(title="cider tasting")
        registry._providers.clear()
        registry.register_provider(provider)
        try:
            found, _total = await registry.search_tasks("cider")
            assert [t.title for t in found] == ["cider tasting"]
        finally:
            registry._providers.clear()


class TestTheEditFormStillSaves:
    """The regression this fix could most easily have caused, caught before shipping.

    The dashboard's task form posts `project_id`, which is NOT a `Task` field:
    `_attach_project_general_list` resolves and pops it on the create path and is not called on
    update. So refusing unknown keys — tempting, since a typo'd field was a silent no-op — would
    have made Save on the task detail screen answer 400.

    That `project_id` is honored on create and ignored on update is its own gap, filed as #2142.
    """

    @pytest.mark.asyncio
    async def test_the_forms_full_payload_including_project_id_saves(self, provider):
        created = await provider.create_task(title="t")
        updated = await provider.update_task(
            created.id,
            title="renamed",
            description="",
            status="open",
            priority="medium",
            task_list_id="",
            assignee="",
            due="",
            labels=[],
            exit_criteria=[],
            action_plan=[],
            notes=[],
            research_notes=[],
            execution_notes=[],
            agent_instructions_template="",
            dependencies=[],
            project_id="p-123",  # not a Task field — must be ignored, not refused
        )
        assert updated is not None
        assert updated.title == "renamed"

    @pytest.mark.asyncio
    async def test_an_immutable_field_is_ignored_not_written(self, provider):
        created = await provider.create_task(title="t")
        updated = await provider.update_task(created.id, id="hijacked", provider="other")
        assert updated is not None
        assert updated.id == created.id
