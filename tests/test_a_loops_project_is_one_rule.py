"""A loop is in one project, by one rule, and everything that reads a loop's project reads it.

``loop_store.loop_project_id(loop, project_exists)``: the loop's ``project_id`` (the project it
was launched into) when that names a project that exists, else its ``tasks_project_id`` (the
project made for its tasks when it was launched into none) when that does, else ``""``. The
rule's own cases are ``test_library_a_loop_belongs_to_one_project.py``'s. Here, what reads it:

- a code session whose project is only in ``tasks_project_id`` (made for it at launch) gets that
  project's brief, its context folder and its sibling loops in the brief its worker reads; one
  launched into a project gets that project's, not the one its tasks were made in, and one
  launched into a project that is gone gets the one made for its tasks;
- ``list_for_project`` returns exactly the loops the rule puts in the project: a loop launched into
  another project is not listed under the one its tasks were made in, a loop is never under two,
  and an id that names no project lists nothing.
"""

from __future__ import annotations

from personalclaw.loop import files as loop_files
from personalclaw.loop import manager
from personalclaw.loop import store as loops
from personalclaw.loop.loop import Loop
from personalclaw.tasks.hierarchy import HierarchyStore


def _project(name: str, brief: str = "") -> str:
    store = HierarchyStore()
    project = store.create_project(name)
    if brief:
        store.update_project(project.id, brief=brief)
    return project.id


def _loop(name: str, kind: str = "goal", **fields) -> Loop:
    return loops.create(Loop(id="", name=name, kind=kind, task=name.lower(), **fields))


def test_a_code_session_whose_project_was_made_for_it_reads_its_brief_folder_and_siblings():
    garden = _project("Garden", brief="Grow tomatoes on the south bed.")
    _loop("Plan the spring beds", project_id=garden)
    session = _loop("Fix the sensor", kind="code", tasks_project_id=garden)

    manager.write_brief(loops.get(session.id))
    brief = (loop_files.loop_dir(session.id) / "brief.md").read_text()

    assert "Grow tomatoes on the south bed." in brief
    assert str(HierarchyStore().context_dir(garden)) in brief
    assert "Other loops on this project (1)" in brief and "Plan the spring beds" in brief


def test_the_brief_follows_the_project_a_loop_was_launched_into_while_it_exists():
    garden = _project("Garden", brief="Grow tomatoes on the south bed.")
    homelab = _project("Homelab", brief="Rack the switch before winter.")
    both = _loop("Wire the sensors", kind="code", project_id=homelab, tasks_project_id=garden)
    gone = _loop("Old idea", kind="code", project_id="p-deleted", tasks_project_id=garden)

    manager.write_brief(loops.get(both.id))
    brief = (loop_files.loop_dir(both.id) / "brief.md").read_text()
    assert "Rack the switch before winter." in brief
    assert str(HierarchyStore().context_dir(homelab)) in brief
    assert "Grow tomatoes on the south bed." not in brief
    assert str(HierarchyStore().context_dir(garden)) not in brief

    manager.write_brief(loops.get(gone.id))
    brief = (loop_files.loop_dir(gone.id) / "brief.md").read_text()
    assert "Grow tomatoes on the south bed." in brief
    assert str(HierarchyStore().context_dir(garden)) in brief


def test_the_project_list_holds_exactly_the_loops_the_rule_puts_in_it():
    garden, homelab = _project("Garden"), _project("Homelab")
    launched = _loop("Plan the spring beds", project_id=garden)
    made_for = _loop("Fix the sensor", kind="code", tasks_project_id=garden)
    elsewhere = _loop("Rack the switch", project_id=homelab, tasks_project_id=garden)
    gone = _loop("Old idea", project_id="p-deleted", tasks_project_id=garden)

    in_garden = {lp.id for lp in loops.list_for_project(garden)}
    in_homelab = {lp.id for lp in loops.list_for_project(homelab)}

    assert in_garden == {launched.id, made_for.id, gone.id}
    assert in_homelab == {elsewhere.id}
    assert not in_garden & in_homelab, "a loop is listed under two projects"
    assert loops.list_for_project("p-deleted") == []
    assert loops.list_for_project("") == []
