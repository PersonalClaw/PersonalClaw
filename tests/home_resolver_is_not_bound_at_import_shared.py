"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_home_resolver_is_not_bound_at_import.py``

Its checks:
``tests/rails/test_home_resolver_is_not_bound_at_import_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import personalclaw

SRC = Path(personalclaw.__file__).parent


LOADER_MODULE = "personalclaw.config.loader"


HOME_RESOLVERS = {"config_dir", "config_path"}


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    return ("personalclaw." + str(rel).replace("/", ".")).replace(".__init__", "")


def _binders(trees: list[tuple[Path, ast.Module]]) -> list[str]:
    """Modules with a MODULE-LEVEL ``from ...loader import config_dir/config_path``."""
    hits = []
    for f, tree in trees:
        for node in tree.body:  # module level only — a nested import resolves per call
            if isinstance(node, ast.ImportFrom) and node.module == LOADER_MODULE:
                if {a.name for a in node.names} & HOME_RESOLVERS:
                    hits.append(_module_name(f))
    return hits


def _aliasers(trees: list[tuple[Path, ast.Module]]) -> list[str]:
    """Modules doing ``config_dir = <something>.config_dir`` at module level.

    An alias captures the object exactly like the import did, so it is the same defect wearing a
    different spelling — and it would slip straight past the import check.
    """
    hits = []
    for f, tree in trees:
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if not names & HOME_RESOLVERS:
                continue
            if isinstance(node.value, ast.Attribute) and node.value.attr in HOME_RESOLVERS:
                hits.append(f"{_module_name(f)} ({node.value.attr})")
    return hits
