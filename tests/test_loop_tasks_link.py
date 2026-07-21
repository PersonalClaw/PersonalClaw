"""Unified Loop ↔ Tasks provisioning (Slice 2c.iv.c) — provision the backing Tasks
Project + per-phase TaskLists, seed/decompose planner tasks, reconcile, teardown.
Operates on the Loop entity, phases keyed by the kind strategy's phase_key."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.loop import store, tasks_link
from personalclaw.loop.loop import Loop


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    # Point the loop store + the tasks hierarchy + native task provider at one tmp dir.
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


def _code(**over):
    base = dict(
        id="",
        name="OAuth login",
        kind="code",
        task="add oauth login",
        plan=[_stage("design"), _stage("implementation"), _stage("verification")],
        kind_config={"entry_stage": "design"},
    )
    base.update(over)
    return store.create(Loop(**base))


def _stage(stage, **over):
    base = {
        "stage": stage,
        "title": stage.title(),
        "objective": f"do {stage}",
        "exit_criteria": [],
        "task_list_name": stage.title(),
    }
    base.update(over)
    return base


class TestProvision:
    def test_provision_creates_project_and_per_phase_lists(self):
        c = _code()
        out = tasks_link.provision(c.id)
        assert out is not None and out.tasks_project_id
        # one TaskList per stage, keyed by the code phase_key (stage id)
        assert set(out.task_list_ids) == {"design", "implementation", "verification"}

    def test_provision_is_idempotent(self):
        c = _code()
        tasks_link.provision(c.id)
        first = store.get(c.id).task_list_ids
        tasks_link.provision(c.id)  # again → same lists, no dupes
        assert store.get(c.id).task_list_ids == first

    def test_provision_nests_under_user_chosen_project_id(self):
        # A loop the user scoped under an existing Project (project_id from the
        # composer's ProjectPicker) must provision its TaskLists UNDER that project —
        # not spawn a fresh auto-named one (the dropped-scoping bug). And it must NOT
        # rename the user's project to the loop's name.
        from personalclaw.tasks.hierarchy import HierarchyStore

        h = HierarchyStore()
        proj = h.create_project(name="Website Redesign")
        c = _code(project_id=proj.id)
        out = tasks_link.provision(c.id)
        assert out.tasks_project_id == proj.id  # nested under the chosen project
        assert h.get_project(proj.id).name == "Website Redesign"  # not renamed to the loop

    def test_two_loops_in_one_project_get_isolated_phase_lists(self):
        # The cross-loop collision bug: two Code loops under the SAME project both have
        # an 'implementation' phase. Matching TaskLists by bare name made the 2nd loop
        # REUSE the 1st loop's 'Implementation' list → it inherited the 1st loop's tasks
        # (a 'write a README' loop picked up a sibling app-build loop's Scaffold/AI/UI
        # tasks, blocking its gate). Per-loop list naming must keep them isolated.
        from personalclaw.tasks.hierarchy import HierarchyStore

        h = HierarchyStore()
        proj = h.create_project(name="Shared Project")
        a = _code(project_id=proj.id, plan=[_stage("implementation")])
        b = _code(project_id=proj.id, plan=[_stage("implementation")])
        out_a = tasks_link.provision(a.id)
        out_b = tasks_link.provision(b.id)
        assert out_a.tasks_project_id == proj.id and out_b.tasks_project_id == proj.id
        # Same project, same phase name — but DISTINCT TaskLists (no reuse).
        assert out_a.task_list_ids["implementation"] != out_b.task_list_ids["implementation"]
        # Seeding A's tasks must not appear in B's list.
        _run(tasks_link.decompose_phase(a.id, "implementation", [{"title": "A-only task"}]))
        from personalclaw.tasks import registry

        b_tasks, _ = _run(
            registry.list_all_tasks(task_list_id=out_b.task_list_ids["implementation"], limit=50)
        )
        assert b_tasks == [] or all(t.title != "A-only task" for t in b_tasks)

    def test_goal_kind_keys_phases_by_title(self):
        g = store.create(
            Loop(
                id="",
                name="G",
                kind="goal",
                task="research X",
                plan=[{"title": "Investigate"}, {"title": "Report"}],
                kind_config={"goal_type": "open_ended"},
            )
        )
        out = tasks_link.provision(g.id)
        assert set(out.task_list_ids) == {"Investigate", "Report"}


class TestSeedAndDecompose:
    def test_seed_materializes_planner_tasks(self):
        c = _code(
            plan=[
                _stage(
                    "implementation",
                    tasks=[{"title": "Build the handler"}, {"title": "Wire the route"}],
                )
            ]
        )
        tasks_link.provision(c.id)
        n = _run(tasks_link.seed_phase_tasks(c.id))
        assert n == 2
        # idempotent — a second seed doesn't duplicate (list now non-empty)
        assert _run(tasks_link.seed_phase_tasks(c.id)) == 0

    def test_decompose_resolves_backward_depends_on(self):
        c = _code(plan=[_stage("implementation")])
        tasks_link.provision(c.id)
        ids = _run(
            tasks_link.decompose_phase(
                c.id, "implementation", [{"title": "First"}, {"title": "Second", "depends_on": [0]}]
            )
        )
        from personalclaw.tasks import registry

        second = _run(registry.get_task(ids[1]))
        assert [d.depends_on_task_id for d in second.dependencies] == [ids[0]]


class TestReconcileAndTeardown:
    def test_reconcile_closes_open_tasks(self):
        c = _code(plan=[_stage("implementation")])
        tasks_link.provision(c.id)
        _run(tasks_link.decompose_phase(c.id, "implementation", [{"title": "A"}, {"title": "B"}]))
        closed = _run(tasks_link.reconcile_phase_done(c.id, "implementation"))
        assert closed == 2

    def test_reconcile_is_idempotent(self):
        # Safe to call again after tasks are already done (parallel mode marks them
        # done individually before the stage-advance reconcile runs — must not error
        # or re-close). Guards the cycle-30 stage-advance + parallel-mode interaction.
        c = _code(plan=[_stage("implementation")])
        tasks_link.provision(c.id)
        _run(tasks_link.decompose_phase(c.id, "implementation", [{"title": "A"}, {"title": "B"}]))
        assert _run(tasks_link.reconcile_phase_done(c.id, "implementation")) == 2
        # second pass: everything already done → closes nothing, no error
        assert _run(tasks_link.reconcile_phase_done(c.id, "implementation")) == 0

    def test_teardown_deletes_tasks_and_project(self):
        # No user project_id → the loop OWNS its auto-created backing project → teardown
        # deletes it.
        from personalclaw.tasks.hierarchy import HierarchyStore

        c = _code(plan=[_stage("implementation", tasks=[{"title": "A"}])])
        out = tasks_link.provision(c.id)
        auto_pid = out.tasks_project_id
        _run(tasks_link.seed_phase_tasks(c.id))
        removed = _run(tasks_link.teardown_tasks(c.id))
        assert removed >= 1
        assert HierarchyStore().get_project(auto_pid) is None  # owned → deleted

    def test_teardown_preserves_user_chosen_shared_project(self):
        # The loop was scoped under an existing user Project (project_id). Deleting the
        # loop must drop ONLY its TaskLists, NOT the shared Project (which may hold other
        # loops/chats/tasks) — the data-loss bug the project_id-scoping change exposed.
        from personalclaw.tasks.hierarchy import HierarchyStore

        h = HierarchyStore()
        proj = h.create_project(name="Shared Effort")
        c = _code(project_id=proj.id, plan=[_stage("implementation", tasks=[{"title": "A"}])])
        out = tasks_link.provision(c.id)
        assert out.tasks_project_id == proj.id  # nested under the shared project
        list_ids = list(out.task_list_ids.values())
        _run(tasks_link.seed_phase_tasks(c.id))
        _run(tasks_link.teardown_tasks(c.id))
        assert h.get_project(proj.id) is not None  # shared project SURVIVES
        assert h.get_project(proj.id).name == "Shared Effort"
        # and the loop's own TaskLists were removed from it
        remaining = {tl.id for tl in h.list_task_lists(project_id=proj.id)}
        assert not (set(list_ids) & remaining)
