"""The source-tree checks split from
``tests/test_session_key_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import pytest
import source_index
from session_key_census_shared import (
    READERS,
    SRC,
    Census,
    Mint,
    _function_named,
    hand_typed_reads,
    reads_the_table,
    violations,
)


def _tree_sources() -> dict[str, str | ast.Module]:
    """Every package module's tree by its dotted name, from the suite's one source index."""
    sources: dict[str, str | ast.Module] = {}
    for path, tree in source_index.package_trees():
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        sources[".".join(parts)] = tree
    return sources


@pytest.fixture(scope="module")
def tree_census() -> Census:
    return Census(_tree_sources())


def test_every_session_key_the_source_mints_names_a_classified_kind(tree_census):
    found = violations(tree_census)
    assert not found, "\n".join(found)


def test_the_census_sees_the_tile_and_app_mints_read_from_their_rows(tree_census):
    """The vacuity control for the rail above: a census that saw nothing would pass it. The mints
    of the two kinds this table was missing are found, and found reading their rows."""
    by_module: dict[str, list[Mint]] = {}
    for mint in tree_census.mints():
        by_module.setdefault(mint.where.split(":")[0], []).append(mint)
    for module in ("personalclaw.dashboard.tile_refresh", "personalclaw.dashboard.handlers.apps"):
        read = [m for m in by_module.get(module, []) if m.literal is None]
        assert read, f"the census sees no session key {module} mints"


@pytest.mark.parametrize(
    "module, function", [(module, f) for module, names in READERS.items() for f in names]
)
def test_a_reader_that_tells_kinds_apart_reads_them_from_the_table(tree_census, module, function):
    owner = tree_census.modules[module]
    node = _function_named(owner, function)
    assert node is not None, f"{module} has no {function}: name the reader where it is now"
    spelled = hand_typed_reads(tree_census, owner, node)
    assert spelled == [], (
        f"{module}.{function} spells a kind's prefix where it reads a key ({', '.join(spelled)}): "
        "read the kind from its row in session_keys"
    )
    # The vacuity control: a reader the scan saw nothing in passes the line above, so it is
    # seen telling the kinds apart through the table.
    assert reads_the_table(tree_census, owner, node), f"{module}.{function} reads no row"
