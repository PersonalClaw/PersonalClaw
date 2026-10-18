"""The source-tree checks split from
``tests/test_triggers_status_vocabulary.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from triggers_status_vocabulary_shared import SRC


def test_the_writer_file_census_is_pinned() -> None:
    """A NEW writer file is the drift no per-file scan can see.

    Every module that assigns `.last_status` or constructs a `ScheduleRun` is enumerated here, so a
    fifth writer reds this test and forces the question "which table translates what it writes?"
    instead of inheriting a silent fallback.
    """
    attr_files: set[str] = set()
    call_files: set[str] = set()
    settle_files: set[str] = set()
    for path, tree in source_index.package_trees():
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "last_status":
                        attr_files.add(rel)
            if isinstance(node, ast.Call):
                called = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", "")
                )
                if called == "ScheduleRun":
                    call_files.add(rel)
                if called == "settle_sync":
                    settle_files.add(rel)

    assert attr_files == {
        "hooks.py",
        # `web_poll.py` writes an HTTP status CODE (an int) to a different entity's field. Named
        # here so its absence from the tables is a decision on the record, not an oversight.
        "triggers/web_poll.py",
        # A report definition carries its OWN `last_status` ("ok" | "error" | ""), which
        # is not the trigger status vocabulary this file pins — it is the report's own two-value
        # run outcome, read by the reports list to say "last run failed" without losing the run
        # time. Named here for the same reason as `web_poll.py`: a same-named field on a different
        # entity should be a decision on the record rather than a silent census drift.
        "knowledge/research_reports.py",
    }, f"the set of modules assigning .last_status changed: {sorted(attr_files)}"
    assert call_files == {
        "gateway.py",
        "triggers/service.py",
        "triggers/run_record.py",
        # The reaper's passes, which close a run whose action never returned (`WRITERS` above).
        "triggers/reaper.py",
    }, f"the set of modules constructing a ScheduleRun changed: {sorted(call_files)}"
    # A row a launched run left is rewritten in one place, whose statuses the writer above pins.
    assert settle_files == {
        "triggers/settle.py"
    }, f"the set of modules settling a run's row changed: {sorted(settle_files)}"
