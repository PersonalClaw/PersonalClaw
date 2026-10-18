"""The source-tree checks split from
``tests/test_stats.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import ast
import unittest

import source_index
from stats_shared import _WRITERS


def _called_methods() -> set[str]:
    """Every ``.method(...)`` name invoked anywhere in src/personalclaw, excluding stats.py itself.

    Parsed rather than grepped: a grep for the method name also matches its own ``def`` line and
    any mention in a comment, which is how a writerless counter can look wired.
    """
    names: set[str] = set()
    for path, tree in source_index.package_trees():
        if path.name == "stats.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


class TestStatsWriters(unittest.TestCase):
    """The contract that keeps a dead counter from shipping as a measurement."""

    def test_every_counter_has_a_writer(self) -> None:
        called = _called_methods()
        orphans = {counter: helper for counter, helper in _WRITERS.items() if helper not in called}
        assert not orphans, (
            f"Counter(s) whose incrementer is never called on any runtime path: {orphans}. "
            "A counter with no writer renders a confident 0 forever. Either wire the call site "
            "or delete the counter — do not ship it as an aspirational field."
        )
