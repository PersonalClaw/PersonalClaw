"""The source-tree checks split from
``tests/test_the_cli_banner_is_personalclaw.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from the_cli_banner_is_personalclaw_shared import _drawn_rows

from personalclaw import cli

PACKAGE = Path(cli.__file__).resolve().parent


def test_the_banner_is_the_only_drawing_in_the_package():
    """A second drawing is how the two copies came to say different things. Every string
    constant in every module is read, so a drawing under another name is found too."""
    drawings = []
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _drawn_rows(node.value) >= 3:
                    drawings.append(f"{path.relative_to(PACKAGE).as_posix()}:{node.lineno}")
    assert drawings == [f"constants.py:{_banner_line()}"], drawings


def _banner_line() -> int:
    """Where `BANNER`'s string starts in `constants.py`, or 0 when it defines none."""
    tree = source_index.tree(PACKAGE / "constants.py")
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "BANNER" for t in node.targets
        ):
            return node.value.lineno
    return 0
