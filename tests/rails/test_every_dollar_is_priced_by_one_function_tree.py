"""The source-tree checks split from
``tests/test_every_dollar_is_priced_by_one_function.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import every_dollar_is_priced_by_one_function_shared
import pytest
import source_index

import personalclaw

_clear_rate_caches = every_dollar_is_priced_by_one_function_shared._clear_rate_caches


SRC = Path(personalclaw.__file__).parent


def _imported_modules(path: Path, *, root: Path = SRC.parent) -> set[str]:
    """Every module *path* imports, relative imports resolved against its own package (*path*
    sits under *root* as the package's own dotted path)."""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    package = parts if parts[-1] == "__init__" else parts[:-1]
    if package and package[-1] == "__init__":
        package = package[:-1]
    found: set[str] = set()
    # A package module's tree is the suite's one source index; a fixture root's is parsed here.
    if path.is_relative_to(SRC) and path.suffix == ".py":
        tree = source_index.tree(path)
    else:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def _readers_of_the_shipped_table() -> set[str]:
    return {
        str(path.relative_to(SRC))
        for path in source_index.package_files()
        if "personalclaw.pricing" in _imported_modules(path) and path.name != "pricing.py"
    }


def test_only_the_rate_table_reads_the_shipped_price_table():
    """A module that priced from ``personalclaw.pricing`` priced by the table alone, which never
    sees a rate the owner set, a local model's zero or an app's declared rate."""
    assert _readers_of_the_shipped_table() == {"routing/rates.py"}


@pytest.mark.parametrize(
    "spelling",
    [
        "import personalclaw.pricing",
        "from personalclaw.pricing import estimate_cost",
        "from personalclaw import pricing",
        "from .. import pricing",
        "from ..pricing import has_pricing",
    ],
)
def test_the_census_sees_every_spelling_of_the_import(tmp_path, spelling):
    """Positive control: each way a module in the package can import the table is found, by the
    same resolution the census runs over the real tree (a probe module in a copy of its layout)."""
    probe = tmp_path / "personalclaw" / "routing" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(spelling + "\n", encoding="utf-8")

    assert "personalclaw.pricing" in _imported_modules(probe, root=tmp_path)
    probe.write_text("from personalclaw.routing import rates\n", encoding="utf-8")
    assert "personalclaw.pricing" not in _imported_modules(probe, root=tmp_path)
