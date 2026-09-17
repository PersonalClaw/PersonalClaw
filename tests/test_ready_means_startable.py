""" "Ready" means startable, not merely unfinished (issue 467).

`reconcile.ready_task_ids` gated on ``status not in TERMINAL_STATUSES``, i.e. it read "not
finished" as "startable". `TERMINAL_STATUSES` is documented one line from its own definition as
answering a different question — *"a task in a terminal state satisfies any dependency that points
at it"* — and it has exactly two members, so BLOCKED and SKIPPED both fell through as work.

Measured before the fix, by calling the real function once per status:

    open → READY    in_progress → READY    done → not ready    cancelled → not ready
    blocked → READY                        skipped → READY

The issue names the BLOCKED half. SKIPPED is the same defect and, going by the comment that
introduced that status, the same defect *for the second time*: it exists because `from_dict` used
to coerce an unknown status to OPEN, so skipped work "read back as work still to do — silently, on
the board the user plans from".

It matters because of the single consumer. `registry.ready_work` calls this, and its own docstring
describes itself as "the one projection every work-selection surface reads" — so a parked task was
not merely listed, it was ranked and offered as the next thing to do.

The fix names the missing concept (`STARTABLE_STATUSES`) and leaves `TERMINAL_STATUSES` alone, so
the dependency rule is untouched. These tests hold both halves, because collapsing them is exactly
what went wrong.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.tasks import reconcile, registry
from personalclaw.tasks.handlers import register_task_routes
from personalclaw.tasks.models import (
    HELD_STATUSES,
    STARTABLE_STATUSES,
    TERMINAL_STATUSES,
    DependencyType,
    Task,
    TaskDependency,
    TaskStatus,
)


def _solo(status: TaskStatus) -> dict[str, Task]:
    return {"t1": Task(id="t1", title="solo", status=status)}


def _chain(prereq_status: TaskStatus) -> dict[str, Task]:
    """`child` depends on `prereq` through a BLOCKS edge."""
    prereq = Task(id="p", title="prereq", status=prereq_status)
    child = Task(id="c", title="child", status=TaskStatus.OPEN)
    child.dependencies = [
        TaskDependency(depends_on_task_id="p", dependency_type=DependencyType.BLOCKS)
    ]
    return {"p": prereq, "c": child}


# ── startability ─────────────────────────────────────────────────────────────────────────────


def test_a_blocked_task_is_not_offered_as_work():
    """🔑 The filed defect. A user who blocks a task has said "not this one"; handing it back is
    the surface overruling them."""
    assert reconcile.ready_task_ids(_solo(TaskStatus.BLOCKED)) == []


def test_a_MANUALLY_blocked_task_is_not_offered_either():
    """The issue's exact wording. `reconcile_blocked_status` distinguishes a manual block (never
    auto-cleared) from an auto one; neither is startable, so the fix must not key on the kind."""
    tasks = _solo(TaskStatus.BLOCKED)
    tasks["t1"].blocked_reason_kind = "manual"
    assert reconcile.ready_task_ids(tasks) == []


def test_a_skipped_task_is_not_offered_as_work():
    """The instance the issue does not name — a branch the run declined, or work a rewind made
    unnecessary. Offering it re-proposes work the engine already decided against."""
    assert reconcile.ready_task_ids(_solo(TaskStatus.SKIPPED)) == []


def test_the_startable_statuses_still_are():
    """Vacuity floor. A fix that narrowed too far would empty the work funnel — a far louder
    failure, and one no test above would catch."""
    assert reconcile.ready_task_ids(_solo(TaskStatus.OPEN)) == ["t1"]
    assert reconcile.ready_task_ids(_solo(TaskStatus.IN_PROGRESS)) == ["t1"]


def test_terminal_tasks_are_still_excluded():
    assert reconcile.ready_task_ids(_solo(TaskStatus.DONE)) == []
    assert reconcile.ready_task_ids(_solo(TaskStatus.CANCELLED)) == []


def test_every_status_is_answered_the_same_way_by_the_set_and_the_function():
    """The whole matrix in one assertion, derived from the set rather than restated — so a status
    moved between sets cannot leave this file agreeing with the old answer."""
    for status in TaskStatus:
        expected = ["t1"] if status in STARTABLE_STATUSES else []
        assert reconcile.ready_task_ids(_solo(status)) == expected, status


# ── the dependency rule is a DIFFERENT question, and is unchanged ─────────────────────────────


def test_a_finished_prerequisite_still_releases_its_dependent():
    """`TERMINAL_STATUSES` keeps answering dependency satisfaction. Cancelled counts, per
    `reconcile`'s module docstring: "a cancelled blocker is 'resolved'"."""
    for status in TERMINAL_STATUSES:
        assert reconcile.ready_task_ids(_chain(status)) == ["c"], status


def test_an_unfinished_prerequisite_still_gates_its_dependent():
    ready = reconcile.ready_task_ids(_chain(TaskStatus.OPEN))
    assert "c" not in ready, "a task whose prerequisite is unfinished became startable"
    assert ready == ["p"], "and the prerequisite itself is the work to offer"


def test_a_BLOCKED_prerequisite_offers_nothing_at_all():
    """🔑 The compound case, and the one a user actually meets: pre-fix the blocked prerequisite
    was itself offered as the next task (`['p']`). Now neither side is — which is correct and is
    the honest consequence of the fix, not a regression: the work really is parked."""
    assert reconcile.ready_task_ids(_chain(TaskStatus.BLOCKED)) == []
    assert reconcile.ready_task_ids(_chain(TaskStatus.SKIPPED)) == []


def test_a_skipped_prerequisite_still_does_not_satisfy_its_dependent():
    """🪤 Scope line, asserted so it is a decision rather than an oversight. Folding SKIPPED into
    `TERMINAL_STATUSES` would make a declined branch release the work behind it — a real product
    question (does a declined branch unblock its dependents?) that must not be answered under
    cover of a startability fix."""
    assert TaskStatus.SKIPPED not in TERMINAL_STATUSES
    assert "c" not in reconcile.ready_task_ids(_chain(TaskStatus.SKIPPED))


# ── the rail: the three sets must PARTITION the status vocabulary ─────────────────────────────


def test_the_three_sets_partition_every_status():
    """🪤 What stops this recurring. Adding a status previously meant inheriting whichever set was
    written as a denylist — which is how BLOCKED and SKIPPED became "startable" without anyone
    deciding that. Now a new member fails here until it is classified.

    Exhaustive AND disjoint: a status in two sets is as broken as one in none.
    """
    classified = [*STARTABLE_STATUSES, *TERMINAL_STATUSES, *HELD_STATUSES]
    assert sorted(s.value for s in classified) == sorted(s.value for s in TaskStatus), (
        "a TaskStatus is unclassified or double-classified — decide whether it is startable, "
        "dependency-satisfying, or held"
    )
    assert len(classified) == len(set(classified)), "a status appears in two sets"


def test_startable_is_an_allowlist_not_a_denylist():
    """The direction matters, and the reason is recorded at `SKIPPED`'s definition: the default
    that bit before was 'unknown reads as work to do'. With an allowlist an unclassified status is
    simply not offered — survivable for a planning surface in a way the opposite is not.

    Pinned by size: a set that grew to cover most of the vocabulary would be a denylist wearing
    the other name.
    """
    assert len(STARTABLE_STATUSES) < len(list(TaskStatus)) / 2


def test_ready_task_ids_reads_the_startable_set():
    """A source rail. The two conditions in that function look interchangeable — both are `status
    in <a set>` — so a future edit could swap one back to `TERMINAL_STATUSES` and every
    behavioural test above would still pass for the DEPENDENCY half while the startability half
    regressed. This pins which set answers which question.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "tasks" / "reconcile.py"
    ).read_text()
    body = src[src.index("def ready_task_ids") : src.index("# ── Dependency analysis")]
    assert (
        "status not in STARTABLE_STATUSES" in body
    ), "ready_task_ids is gating startability on something other than STARTABLE_STATUSES"
    assert (
        "in TERMINAL_STATUSES for p in prereqs" in body
    ), "the prerequisite rule no longer reads TERMINAL_STATUSES — dependency semantics moved"


# ── end to end, through the endpoint the issue names ─────────────────────────────────────────


@asynccontextmanager
async def _client(tmp_path):
    """The task routes over isolated filesystem stores — same harness as `test_tasks_api`."""
    registry._providers.clear()
    with (
        patch("personalclaw.tasks.native.config_dir", return_value=tmp_path),
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path),
    ):
        app = web.Application()
        register_task_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client
    registry._providers.clear()


async def _ready_ids(client) -> set[str]:
    return {t["id"] for t in (await (await client.get("/api/tasks/ready")).json())["tasks"]}


@pytest.mark.asyncio
async def test_GET_api_tasks_ready_drops_a_status_blocked_task(tmp_path):
    """🔑 The issue's own title, driven over HTTP.

    `test_tasks_api.test_ready_excludes_blocked_then_includes_after_done` already existed and
    passed throughout — but it blocks task B by an unfinished PREREQUISITE, never by setting a
    status. Reading the suite, "ready excludes blocked" looked covered; the status half had no
    test at all, which is how this survived. Both paths are asserted here.
    """
    async with _client(tmp_path) as client:
        solo = await (await client.post("/api/tasks", json={"title": "Parked"})).json()
        assert solo["id"] in await _ready_ids(client), "precondition: it starts out ready"

        await client.put(f"/api/tasks/{solo['id']}", json={"status": "blocked"})
        assert solo["id"] not in await _ready_ids(
            client
        ), "a task the user blocked is still offered as ready work"


@pytest.mark.asyncio
async def test_GET_api_tasks_ready_drops_a_skipped_task(tmp_path):
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "Declined branch"})).json()
        await client.put(f"/api/tasks/{t['id']}", json={"status": "skipped"})
        assert t["id"] not in await _ready_ids(client)


@pytest.mark.asyncio
async def test_GET_api_tasks_ready_still_returns_open_work(tmp_path):
    """Vacuity floor at the HTTP layer: the endpoint must not have become an empty list."""
    async with _client(tmp_path) as client:
        t = await (await client.post("/api/tasks", json={"title": "Real work"})).json()
        assert t["id"] in await _ready_ids(client)
