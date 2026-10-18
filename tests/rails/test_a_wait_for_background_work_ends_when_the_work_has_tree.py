"""The source-tree checks split from
``tests/test_a_wait_for_background_work_ends_when_the_work_has.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from a_wait_for_background_work_ends_when_the_work_has_shared import _waits_for_a_set_to_empty
from rails_repo import REPO

_REPO = REPO


def test_no_wait_in_the_tree_loops_until_a_held_set_empties() -> None:
    """Read lazily, one file at a time, and only the files that gather in a loop at all."""
    offenders = []
    scanned = 0
    # The package is the suite's one source index; the tests folder, not one of its roots, is
    # walked and read here.
    package = [(path, True) for path in source_index.package_files()]
    tests = [(path, False) for path in sorted((_REPO / "tests").rglob("*.py"))]
    for path, indexed in (*package, *tests):
        source = source_index.text(path) if indexed else path.read_text(encoding="utf-8")
        if "gather(" not in source or "while " not in source:
            continue
        scanned += 1
        found = _waits_for_a_set_to_empty(source_index.tree(path) if indexed else source)
        offenders += [f"{path.relative_to(_REPO)}:{line}" for line in found]
    assert scanned >= 5, f"the scan read only {scanned} file(s) that gather in a loop"
    assert not offenders, (
        "these wait for a set of background tasks to empty, which spins forever once its last "
        "task has just finished; wait with `personalclaw.cancellation.settle(held)`: "
        + ", ".join(offenders)
    )
