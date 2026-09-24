"""A reset Repeatable list is actually ready to run again (issue 389).

Measured on a live repeatable list: after `POST /api/task-lists/{id}/reset` the tasks came back
`status=open` with both exit criteria `incomplete` and `execution_notes` empty — and
`action_plan = [('step one', True), ('step two', True)]`. So a weekly checklist reset for its
next run displayed every step ticked and struck through; the user either unticked each one by
hand or worked from a plan claiming it was already done.

The handler wrote three fields and its docstring named those same three. `action_plan` was in
neither, and it is not the only per-run field that was missing — so what a reset means now lives
in ONE table (`task_reset_payload`) with the exhaustiveness rail below, the same shape this
module already uses for field coercion: a per-run field nobody remembers is a red test rather
than the next silent omission.
"""

import dataclasses

import pytest

from personalclaw.tasks.models import (
    _RESET_CLEARED,
    _RESET_PRESERVED,
    REPEATABLE_PROJECT,
    Task,
    task_reset_payload,
)

# ── the table is exhaustive over Task ──────────────────────────────────────────────────────


def test_every_task_field_has_a_reset_verdict():
    """The rail that makes this extensible: add a field to `Task` and it must be classified
    cleared-or-preserved here, with a stated reason, or this reds."""
    fields = {f.name for f in dataclasses.fields(Task)}
    classified = set(_RESET_CLEARED) | set(_RESET_PRESERVED)
    assert fields - classified == set(), "a Task field carries no reset verdict"
    assert classified - fields == set(), "the reset table names a field Task does not have"


def test_no_field_is_both_cleared_and_preserved():
    assert set(_RESET_CLEARED) & set(_RESET_PRESERVED) == set()


def test_every_preserved_field_states_a_reason():
    # A bare "preserved" entry is how a per-run field sneaks into the wrong half.
    assert all(isinstance(v, str) and v.strip() for v in _RESET_PRESERVED.values())


# ── the payload ────────────────────────────────────────────────────────────────────────────


def _finished_task() -> Task:
    return Task(
        id="t1",
        title="Water the plants",
        status="done",
        exit_criteria=[{"description": "all pots damp", "status": "met", "met": True}],
        action_plan=[
            {"sequence": 1, "content": "step one", "completed": True},
            {"sequence": 2, "content": "step two", "completed": True},
        ],
        execution_notes=[{"content": "did it", "timestamp": "t"}],
        notes=[{"content": "the fern hates direct sun", "timestamp": "t"}],
        research_notes=[{"content": "watering cadence", "timestamp": "t"}],
        evidence=[{"kind": "note", "detail": "photo"}],
        attempts=[{"n": 1, "outcome": "ok"}],
        preview="watering the fern",
        blocked_kind="transient",
        blocked_reason_kind="auto",
        assignee="me",
        due="2026-09-10",
        labels=["home"],
    )


def test_action_plan_steps_come_back_unticked():
    # The reported defect, exactly.
    payload = task_reset_payload(_finished_task())
    assert [a["completed"] for a in payload["action_plan"]] == [False, False]


def test_the_steps_themselves_survive():
    # Clearing the flag must not clear the plan: the steps ARE the work.
    payload = task_reset_payload(_finished_task())
    assert [a["content"] for a in payload["action_plan"]] == ["step one", "step two"]
    assert [a["sequence"] for a in payload["action_plan"]] == [1, 2]


def test_exit_criteria_are_unmet_but_keep_their_wording():
    payload = task_reset_payload(_finished_task())
    assert payload["exit_criteria"] == [
        {"description": "all pots damp", "status": "incomplete", "met": False}
    ]


def test_last_run_evidence_and_attempts_do_not_carry_over():
    # "A done task with no evidence is a claim" — and evidence from a finished run is not
    # evidence about the next one.
    payload = task_reset_payload(_finished_task())
    assert payload["evidence"] == [] and payload["attempts"] == []


def test_a_reopened_task_is_not_still_blocked_or_mid_flight():
    payload = task_reset_payload(_finished_task())
    assert payload["status"] == "open"
    assert payload["preview"] == ""
    assert payload["blocked_kind"] == "" and payload["blocked_reason_kind"] == ""


def test_knowledge_and_definition_are_untouched():
    # The other half of the contract: a reset must not erase what the task IS.
    payload = task_reset_payload(_finished_task())
    for preserved in ("notes", "research_notes", "title", "assignee", "due", "labels"):
        assert preserved not in payload, f"reset must not write {preserved}"


def test_a_task_with_no_plan_resets_cleanly():
    # Vacuity guard: the common checklist has no action_plan at all.
    payload = task_reset_payload(Task(id="t2", title="Bare", status="done"))
    assert payload["action_plan"] == [] and payload["exit_criteria"] == []
    assert payload["status"] == "open"


# ── end to end through the endpoint ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_endpoint_unticks_the_action_plan(tmp_path):
    from tests.test_tasks_api import _client  # the suite's aiohttp harness

    async with _client(tmp_path) as client:
        projects = (await (await client.get("/api/projects")).json())["projects"]
        repeatable = next(p for p in projects if p["name"] == REPEATABLE_PROJECT)
        tl = await (
            await client.post(
                "/api/task-lists", json={"name": "Weekly", "project_id": repeatable["id"]}
            )
        ).json()
        task = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "Water the plants",
                    "task_list_id": tl["id"],
                    "action_plan": [
                        {"sequence": 1, "content": "step one"},
                        {"sequence": 2, "content": "step two"},
                    ],
                    "exit_criteria": [{"description": "all pots damp"}],
                },
            )
        ).json()
        # Finish it the way the UI does, in two steps: tick the steps and meet the criterion,
        # THEN complete. The completion gate reads the STORED criteria, so it correctly refuses
        # `done` in the same call that first meets them — and that refusal is what makes the
        # post-reset state below meaningful.
        ticked = await client.put(
            f"/api/tasks/{task['id']}",
            json={
                "exit_criteria": [{"description": "all pots damp", "status": "met", "met": True}],
                "action_plan": [
                    {"sequence": 1, "content": "step one", "completed": True},
                    {"sequence": 2, "content": "step two", "completed": True},
                ],
                "execution_notes": [{"content": "done"}],
            },
        )
        assert ticked.status == 200, await ticked.text()
        assert all(a["completed"] for a in (await ticked.json())["action_plan"])
        done = await client.put(f"/api/tasks/{task['id']}", json={"status": "done"})
        assert done.status == 200, await done.text()

        # `confirm: true` is the endpoint's intent gate — a reset empties `execution_notes`
        # and has no undo, so it refuses an unconfirmed call with 400 `confirm_required`.
        r = await client.post(f"/api/task-lists/{tl['id']}/reset", json={"confirm": True})
        assert r.status == 200, await r.text()
        after = await (await client.get(f"/api/tasks/{task['id']}")).json()
        assert after["status"] == "open"
        assert [a["completed"] for a in after["action_plan"]] == [
            False,
            False,
        ], "a repeated checklist must not come back already ticked"
        assert [a["content"] for a in after["action_plan"]] == ["step one", "step two"]
        assert after["exit_criteria"][0]["met"] is False
        assert after["execution_notes"] == []


@pytest.mark.asyncio
async def test_reset_defers_engine_owned_fields_on_a_managed_task(tmp_path):
    """A task a workflow run still owns must not have reset overwrite what the run established.
    `status`, `evidence`, `attempts`, `preview`, and `blocked_kind` stay with the run; the user's
    own progress — `execution_notes` — still clears, and the response discloses which task ids
    were only partially reset."""
    from personalclaw.tasks import registry
    from tests.test_tasks_api import _client

    async with _client(tmp_path) as client:
        projects = (await (await client.get("/api/projects")).json())["projects"]
        repeatable = next(p for p in projects if p["name"] == REPEATABLE_PROJECT)
        tl = await (
            await client.post(
                "/api/task-lists", json={"name": "Weekly", "project_id": repeatable["id"]}
            )
        ).json()
        created = await (
            await client.post(
                "/api/tasks",
                json={
                    "title": "engine-run step",
                    "task_list_id": tl["id"],
                    "workflow_binding": {
                        "run_id": "r-1",
                        "node_id": "impl",
                        "node_path": "root",
                        "managed": True,
                        "fingerprint": "abc123",
                    },
                    "execution_notes": [{"content": "did it"}],
                },
            )
        ).json()
        # The engine, not the HTTP door, settles a managed task — the same way
        # `loop/tasks_link.py` writes a run's outcome straight through the façade.
        await registry.update_task(
            created["id"],
            status="done",
            evidence=[{"kind": "note", "detail": "engine finding"}],
            attempts=[{"n": 1, "outcome": "ok"}],
            preview="engine preview",
        )

        r = await client.post(f"/api/task-lists/{tl['id']}/reset", json={"confirm": True})
        assert r.status == 200, await r.text()
        body = await r.json()
        assert body["partially_reset_task_ids"] == [created["id"]]

        after = await (await client.get(f"/api/tasks/{created['id']}")).json()
        assert after["status"] == "done", "the run's status must survive a reset"
        assert after["evidence"] == [{"kind": "note", "detail": "engine finding"}]
        assert after["attempts"] == [{"n": 1, "outcome": "ok"}]
        assert after["preview"] == "engine preview"
        assert after["workflow_binding"]["managed"] is True
        assert after["execution_notes"] == [], "user-owned progress must still clear"


@pytest.mark.asyncio
async def test_reset_still_clears_every_field_on_an_unmanaged_task(tmp_path):
    """🪤 The vacuity floor for the managed-task deferral above: an ordinary task in the same
    list must reset exactly as it always has — the deferral is scoped to `managed(t)`, not to
    reset as a whole."""
    from tests.test_tasks_api import _client

    async with _client(tmp_path) as client:
        projects = (await (await client.get("/api/projects")).json())["projects"]
        repeatable = next(p for p in projects if p["name"] == REPEATABLE_PROJECT)
        tl = await (
            await client.post(
                "/api/task-lists", json={"name": "Weekly", "project_id": repeatable["id"]}
            )
        ).json()
        task = await (
            await client.post("/api/tasks", json={"title": "plain step", "task_list_id": tl["id"]})
        ).json()
        done = await client.put(f"/api/tasks/{task['id']}", json={"status": "done"})
        assert done.status == 200, await done.text()

        r = await client.post(f"/api/task-lists/{tl['id']}/reset", json={"confirm": True})
        assert r.status == 200, await r.text()
        body = await r.json()
        assert body["reset_task_ids"] == [task["id"]]
        assert body["partially_reset_task_ids"] == []

        after = await (await client.get(f"/api/tasks/{task['id']}")).json()
        assert after["status"] == "open"
