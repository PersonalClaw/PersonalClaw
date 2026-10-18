"""The source-tree checks split from
``tests/test_sandbox_providers.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index


def test_no_module_binds_the_spawn_when_it_is_imported():
    """A module that bound ``create_subprocess_limited`` at import kept whatever the name held when
    it was first imported. The sandbox providers load lazily, so a test that had replaced the
    spawn at that moment left its fake in the provider for the rest of the run, and an agent
    spawned in a later test called it. Each module reads the spawn from ``personalclaw.sandbox``
    when it runs, and a replacement lasts exactly as long as whoever made it."""
    import ast
    from pathlib import Path

    import personalclaw

    root = Path(personalclaw.__file__).parent
    bound = []
    for path, tree in source_index.package_trees():
        for node in tree.body:
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "personalclaw.sandbox"
                and any(alias.name == "create_subprocess_limited" for alias in node.names)
            ):
                bound.append(f"{path.relative_to(root)}:{node.lineno}")
    assert (
        not bound
    ), f"these bind the spawn at import; read sandbox.create_subprocess_limited: {bound}"
