"""The source-tree checks split from
``tests/test_cron_is_stepped_on_the_wall_clock.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import pathlib

import source_index
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


def _constructions(path: pathlib.Path) -> list[int]:
    tree = source_index.tree(path)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "croniter"
    ]


def test_a_cron_is_stepped_in_one_place():
    """Stepping one anywhere else hands croniter an aware start again. `is_valid` and `match`
    (attribute calls) are not steps, and stay where they are."""
    elsewhere = {
        path.relative_to(SRC).as_posix(): lines
        for path in source_index.package_files()
        if path.name != "cron_clock.py" and (lines := _constructions(path))
    }
    assert elsewhere == {}
    assert _constructions(SRC / "cron_clock.py"), "the scan found the one place it steps"
