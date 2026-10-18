"""The source-tree checks split from
``tests/test_task_engine_owned_guard.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

# Classified by relative path under src/personalclaw, counting non-definition lines that reach
# `.update_task(`. A count changing here — including to zero, i.e. a file disappearing — means a
# door was added, removed, or moved, and the person who caused that must re-classify it.
_UPDATE_TASK_CALL_SITES: dict[str, int] = {
    # the façade's own call into the provider implementation it routes to
    "tasks/registry.py": 1,
    # guarded doors: PUT /api/tasks/{id} and POST /api/tasks/bulk op:update, both behind
    # registry.engine_owned_refusal (#390) — plus the PUT door's `tick` form (`handlers._tick`),
    # which writes ONE checklist item onto the stored list and so only ever `exit_criteria` or
    # `action_plan`, neither of them an ENGINE_OWNED field
    "tasks/handlers.py": 3,
    # guarded door: the agent's task_update tool, behind the same refusal resolver
    "agents/native/builtin_tools.py": 1,
    # the non-engine door this issue closes: filtered by managed()/ENGINE_OWNED_FIELDS instead
    # of refused, because it is not a caller-supplied-field write (see the filter test above)
    "tasks/hierarchy_handlers.py": 1,
    # engine/loop writers: the workflow controller and its loop kinds settle a managed task's
    # own fields directly through the façade — the one writer `engine_owned_refusal` deliberately
    # never blocks (see registry.engine_owned_refusal's docstring)
    "loop/watchdog.py": 1,
    # …among them the loop carrying out its owner's answer to a task's conflicting work: redo
    # opens the task again, drop cancels it (`loop.conflicts`, behind the owner-only
    # POST /api/loops/{id}/conflict)
    "loop/conflicts.py": 2,
    # …one of them the release of a task no worker holds any more, which skips a run's own
    # (managed) task: that one's status is the run's projection
    "loop/tasks_link.py": 5,
    "loop/manager.py": 1,
}


def _update_task_call_line_counts() -> dict[str, int]:
    import re

    src_root = REPO / "src" / "personalclaw"
    call_re = re.compile(r"\.update_task\(")
    def_re = re.compile(r"^\s*(async\s+)?def\s+update_task\b")
    counts: dict[str, int] = {}
    for path in source_index.package_files():
        n = 0
        for line in source_index.text(path).splitlines():
            if def_re.match(line):
                continue
            if call_re.search(line):
                n += 1
        if n:
            counts[str(path.relative_to(src_root))] = n
    return counts


def test_every_update_task_call_site_is_classified():
    """The census the issue asked for: every non-test call site that reaches
    `registry.update_task` (or, for the façade itself, the provider's own `update_task`),
    classified into guarded doors, engine/loop writers, the façade, and the one non-engine
    door that defers field-wise. 15 sites total. A 16th — anywhere in the tree — reds here
    instead of silently joining "engine/loop writers" or disappearing from the count."""
    found = _update_task_call_line_counts()
    assert found == _UPDATE_TASK_CALL_SITES, (
        f"the update_task call-site census drifted: found {found}, classified "
        f"{_UPDATE_TASK_CALL_SITES}. Classify the new/removed site before updating this table."
    )
    assert sum(_UPDATE_TASK_CALL_SITES.values()) == 15
