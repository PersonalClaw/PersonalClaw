"""The run-side PLURAL tasks projection (PP-16 seam 4e, OWNER RULING 1).

The ruling's operative sentence: "a run that projects to tasks carries the PLURAL shape,
matching the live field." Seam 4c did the destructive half (the inert singular
`WorkflowRun.task_list_id` is gone); this rails the constructive half —
`materialize.task_list_ids_for_run`, the DERIVED `{node_id: task_list_id}` map that gives the
loop row's `task_list_ids` column a destination when the row retires.

**Why derived and not stored.** Both halves of every entry already persist on the Task rows
themselves: the `workflow_binding` carries `(run_id, node_id)` and `Task.task_list_id` is the
structural parent. A stored run-side column would be a second copy of the truth — the exact
shape whose singular form seam 4c deleted so it could never be mis-filled.

**Railed in both directions, plus the measured reality.** The projection returns the plural
mapping from REAL persisted bindings (written and re-read through the actual native store, not
in-memory stand-ins), and an empty / unbound / foreign-run query projects `{}`. The middle
truth is pinned too: the engine's own write (`controller._write_projected_task`) passes no
`task_list_id`, so a fresh run honestly projects `{}` until its tasks are filed into lists —
a move the write façade permits because `task_list_id` is not engine-owned.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from personalclaw.tasks import registry
from personalclaw.tasks.models import Task, WorkflowTaskBinding
from personalclaw.workflows import loop_run_map, materialize


@contextmanager
def _isolated_tasks(tmp_path):
    """The real per-task JSON store, rooted in the test's tmp dir (test_tasks_api's idiom).

    Real on purpose: the seam's claim is that the plural map derives from PERSISTED state, and
    an in-memory Task proves nothing about what survives the write/read round trip.
    """
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        yield
    registry._providers.clear()


def _bound(node_id: str, list_id: str, *, run_id: str = "run-a", managed: bool = True) -> Task:
    """An in-memory bound task, for the legs where the RULE (not persistence) is under test."""
    return Task(
        id=f"t-{node_id}-{list_id}-{managed}",
        title=node_id,
        task_list_id=list_id,
        workflow_binding=WorkflowTaskBinding(run_id=run_id, node_id=node_id, managed=managed),
    )


# ── the constructive direction: real bindings project the plural map ─────────────────────────


@pytest.mark.asyncio
async def test_the_plural_mapping_derives_from_persisted_bindings(tmp_path):
    """The whole ruling, end to end: tasks written through the real store, re-read by a FRESH
    provider (so every fact crossed the JSON files), project to the plural `{node_id: list_id}`
    map — and a second run's task does not leak into the first run's answer, because the run id
    filter is the projection's whole reason to exist.
    """
    with _isolated_tasks(tmp_path):
        for node, lst in (("design", "tl-design"), ("build", "tl-build")):
            await registry.create_task(
                "native",
                title=f"step {node}",
                task_list_id=lst,
                workflow_binding={"run_id": "run-a", "node_id": node, "managed": True},
            )
        await registry.create_task(
            "native",
            title="someone else's step",
            task_list_id="tl-foreign",
            workflow_binding={"run_id": "run-b", "node_id": "design", "managed": True},
        )
        # A standalone task in a list must not appear either: no binding, no run, no entry.
        await registry.create_task("native", title="groceries", task_list_id="tl-personal")

        # Fresh provider → the read below reconstructs everything from the persisted JSON.
        registry._providers.clear()
        tasks, _ = await registry.list_all_tasks(limit=50)

        assert materialize.task_list_ids_for_run("run-a", tasks) == {
            "design": "tl-design",
            "build": "tl-build",
        }
        assert materialize.task_list_ids_for_run("run-b", tasks) == {"design": "tl-foreign"}


@pytest.mark.asyncio
async def test_the_engines_own_write_shape_projects_nothing_until_filed(tmp_path):
    """The measured middle truth. `controller._write_projected_task` sends title, description
    and the binding — and NO `task_list_id` — so a fresh run's projection is honestly `{}`:
    no list holds that work yet, the same reading as a phase absent from `Loop.task_list_ids`.
    The entry appears the moment the task is FILED into a list, and that filing is a
    legitimate move: `task_list_id` is deliberately not an engine-owned field, so the façade
    that rejects user status writes lets this one through.
    """
    assert "task_list_id" not in materialize.ENGINE_OWNED_FIELDS, (
        "task_list_id became engine-owned — filing a run's task into a list is no longer a "
        "permitted user move, and this projection's non-empty case just lost its only writer"
    )
    with _isolated_tasks(tmp_path):
        # The exact field shape the controller writes (measured against _write_projected_task).
        task = await registry.create_task(
            "native",
            title="Implement the parser",
            description="**What to build**\n\nA parser.",
            workflow_binding={
                "run_id": "run-a",
                "node_id": "implement",
                "node_path": "root.implement",
                "managed": True,
                "fingerprint": "abc123",
            },
        )
        tasks, _ = await registry.list_all_tasks(limit=50)
        assert materialize.task_list_ids_for_run("run-a", tasks) == {}

        await registry.update_task(task.id, provider_name="native", task_list_id="tl-board")
        registry._providers.clear()
        tasks, _ = await registry.list_all_tasks(limit=50)
        assert materialize.task_list_ids_for_run("run-a", tasks) == {"implement": "tl-board"}


def test_a_managed_binding_wins_over_produced_provenance() -> None:
    """One node can both project its own managed task and attribute PRODUCED output to itself
    (managed=False with a binding — the provenance case the binding model names explicitly).
    "Where does this node's tracking live" is the managed task's list, whichever order the
    store happens to return them in; within a class, first wins — `plan_materialization`'s
    set-add idiom, so the answer cannot depend on dict last-writer accidents.
    """
    managed_task = _bound("build", "tl-managed", managed=True)
    produced = _bound("build", "tl-produced", managed=False)
    for ordering in ((managed_task, produced), (produced, managed_task)):
        assert materialize.task_list_ids_for_run("run-a", ordering) == {"build": "tl-managed"}
    # Within a class, first wins.
    first = _bound("build", "tl-first", managed=True)
    second = _bound("build", "tl-second", managed=True)
    assert materialize.task_list_ids_for_run("run-a", (first, second)) == {"build": "tl-first"}


# ── the empty direction: nothing is manufactured ─────────────────────────────────────────────


def test_a_run_with_no_bindings_projects_empty() -> None:
    """The other direction of the ruling. No tasks, no iterable at all, only standalone tasks,
    or only ANOTHER run's tasks — every one is `{}`, never a partial or invented mapping. A
    projection that manufactured entries would be a board pointing at lists that hold nothing
    of the run's.
    """
    assert materialize.task_list_ids_for_run("run-a", None) == {}
    assert materialize.task_list_ids_for_run("run-a", []) == {}
    standalone = Task(id="t1", title="groceries", task_list_id="tl-personal")
    assert materialize.task_list_ids_for_run("run-a", [standalone]) == {}
    assert materialize.task_list_ids_for_run("run-a", [_bound("n", "tl", run_id="run-b")]) == {}
    # A bound task not yet filed into any list contributes nothing — absent, not "".
    assert materialize.task_list_ids_for_run("run-a", [_bound("n", "", run_id="run-a")]) == {}


def test_an_empty_run_id_never_harvests_malformed_bindings() -> None:
    """A tolerant reader can hand back a binding whose own run id is empty. An equally empty
    QUERY must not match it: `"" == ""` is the classic accidental join, and the projection
    refuses it outright rather than trusting every caller to pre-validate.
    """
    malformed = _bound("n", "tl-x", run_id="")
    assert materialize.task_list_ids_for_run("", [malformed]) == {}


# ── the declaration keeps up with the shipped code ───────────────────────────────────────────


def test_the_field_map_row_names_this_projection() -> None:
    """`loop_run_map`'s `task_list_ids` row must stay PROJECTION and must name the shipped
    function — the map is the retirement plan's source of truth, and a row pointing at a
    destination that was renamed or deleted is a migration instruction that sends the reader
    to nothing. Both drift directions: the note names the symbol, and the symbol must exist.
    """
    rows = [r for r in loop_run_map.LOOP_FIELD_MAP if r.field == "task_list_ids"]
    assert len(rows) == 1, "the task_list_ids row vanished from LOOP_FIELD_MAP"
    row = rows[0]
    assert row.dest_kind == loop_run_map.PROJECTION
    assert not row.dest, "PROJECTION rows carry no destination path"
    assert "task_list_ids_for_run" in row.note, (
        "the map's task_list_ids row no longer names the shipped projection function — "
        "whoever retires the loop row inherits a note with no destination"
    )
    assert callable(getattr(materialize, "task_list_ids_for_run", None)), (
        "the note names materialize.task_list_ids_for_run but no such callable exists — "
        "rename the function and the map row in the same change"
    )
