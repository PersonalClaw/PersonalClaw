"""The source-tree checks split from
``tests/test_a_step_runs_this_installs_personalclaw.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path


def test_the_optimize_module_is_not_a_second_entry_point() -> None:
    """One way in: the CLI's command. The module no longer runs on its own under ``-m``."""
    import ast

    import personalclaw.evals.optimize as optimize

    tree = ast.parse(Path(optimize.__file__).read_text(encoding="utf-8"))
    mains = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
    ]
    assert mains == []
