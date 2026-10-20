"""The source-tree checks split from
``tests/test_each_home_has_its_own_tmux_server.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

import personalclaw

SRC = Path(personalclaw.__file__).resolve().parent


def test_no_tmux_command_is_built_outside_the_substrate():
    """Three private copies in the terminal handler kept naming the machine-wide socket."""
    stray = []
    for py, tree in source_index.package_trees():
        rel = py.relative_to(SRC).as_posix()
        if rel == "tmux_substrate.py" or rel.startswith("skills/bundled/"):
            continue
        for node in ast.walk(tree):
            args = []
            if isinstance(node, (ast.List, ast.Tuple)):
                args = node.elts
            elif isinstance(node, ast.Call):
                args = node.args
            first = args[0] if args else None
            if isinstance(first, ast.Constant) and first.value == "tmux":
                stray.append(f"{rel}:{node.lineno}")
    assert stray == [], stray
