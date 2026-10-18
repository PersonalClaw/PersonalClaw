"""The source-tree checks split from
``tests/test_every_database_opens_through_one_sqlite.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from every_database_opens_through_one_sqlite_shared import _stdlib_driver_imports
from rails_repo import REPO

_SRC = REPO / "src"


#: The one module that may import the stdlib driver: it is where the choice between the two is made.
_EXEMPT = _SRC / "personalclaw" / "sqlite_compat.py"


def _imports_the_shared_driver(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == "personalclaw.sqlite_compat":
                return True
            if node.module == "personalclaw" and any(
                alias.name == "sqlite_compat" for alias in node.names
            ):
                return True
    return False


def test_no_module_under_src_imports_the_stdlib_driver() -> None:
    modules = list(source_index.package_files())
    offenders: list[str] = []
    shared = 0
    exempt_lines: list[int] = []
    for path in modules:
        tree = source_index.tree(path)
        lines = _stdlib_driver_imports(tree)
        if path == _EXEMPT:
            exempt_lines = lines
            continue
        shared += _imports_the_shared_driver(tree)
        offenders += [f"{path.relative_to(_SRC)}:{line}" for line in lines]
    assert len(modules) > 1000, f"the scan found {len(modules)} modules under {_SRC}"
    # The scan sees imports: it finds sqlite_compat's own import of the stdlib driver, and the
    # modules that import the shared one. Twenty-two do; a drop below twenty is worth a look.
    assert exempt_lines, f"no stdlib import found in {_EXEMPT.name}, so the scan proves nothing"
    assert shared >= 20, f"only {shared} modules import personalclaw.sqlite_compat"
    assert offenders == [], (
        "these import the stdlib sqlite3, a second copy of SQLite where pysqlite3 is installed; "
        "import it from personalclaw.sqlite_compat instead:\n  " + "\n  ".join(offenders)
    )
