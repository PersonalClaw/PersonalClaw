"""The source-tree checks split from ``tests/test_rooms_store.py``.

They run once per CI run, in the rails job. The module named above says what they hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

import personalclaw.rooms as rooms


def _imported(node: ast.Import | ast.ImportFrom, module: str) -> list[str]:
    """The modules *node* imports, written out in full: a relative import is resolved against
    *module*, the dotted name of the module it is written in."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = module.split(".")[: -node.level] if node.level else []
    named = ".".join([*base, node.module] if node.module else base)
    return [named] + [f"{named}.{alias.name}" for alias in node.names]


def test_the_rooms_package_never_imports_the_http_surface():
    """``rooms/`` is domain code: an import of ``dashboard/`` inverts the dependency.

    The defect this structure prevents: a domain module that reaches up into the HTTP surface can
    no longer be exercised without standing up the web app, which is how a feature ends up
    reachable only through one route. ``scripts/generate_structural_baseline.py``'s
    ``core-must-not-import-the-http-surface`` rule is the census; this is the local rail, so the
    failure names ``rooms/`` instead of arriving as a whole-tree counter that rose. Every import
    of every module in the package counts, a deferred one inside a function body and a relative
    one alike: hiding the import in a function, or spelling it ``from ..dashboard``, leaves the
    edge in place. Parsed, not grepped, so a docstring that names ``dashboard/`` (the store's
    own does) is prose.
    """
    folder = Path(rooms.__file__).resolve().parent
    files = source_index.under(source_index.package_files(), folder, (".py",))
    assert {"store.py", "turn.py"} <= {path.name for path in files}, "the walk missed the room code"
    offenders = []
    for path in files:
        module = (
            "personalclaw.rooms." + path.stem if path.stem != "__init__" else "personalclaw.rooms"
        )
        for node in ast.walk(source_index.tree(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for name in _imported(node, module):
                    if name == "personalclaw.dashboard" or name.startswith(
                        "personalclaw.dashboard."
                    ):
                        offenders.append(f"{path.name}:{node.lineno}: {name}")
    assert not offenders, offenders


def test_the_import_reader_names_a_deferred_and_a_relative_import_of_the_http_surface():
    """The vacuity proof for the rail above: a reader that resolved nothing would pass it."""
    tree = ast.parse(
        "def f():\n"
        "    from ..dashboard import state\n"
        "    import personalclaw.dashboard.chat_runner\n"
    )
    names = [
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in _imported(node, "personalclaw.rooms.store")
    ]
    assert names == [
        "personalclaw.dashboard",
        "personalclaw.dashboard.state",
        "personalclaw.dashboard.chat_runner",
    ], names
