"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_cache_counters_single_store.py``

Its checks:
``tests/rails/test_cache_counters_single_store_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

# Every spelling this repo uses for the prompt-cache quantity. ``cache_write`` is
# ``pricing.py``'s name for what the providers call cache CREATION.
_CACHE_NAMES = ("cache_read", "cache_creation", "cache_write")


def _target_names(node: ast.AST) -> list[str]:
    """Every identifier or string-subscript an assignment target mentions."""
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.append(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            names.append(sub.value)
    return names


def _accumulators(source: str | ast.Module, label: str) -> list[str]:
    """``x += ...`` (or ``-=``, ``|=`` &c.) onto a prompt-cache-named quantity.

    The TARGET is what makes a store: ``total += cache_read_tokens`` accumulates a
    turn total the caller already owns, while ``self._cache_read_tokens += n``
    accumulates the cache tally itself. Only the latter is a second store.
    """
    hits: list[str] = []
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AugAssign):
            continue
        if any(frag in name for name in _target_names(node.target) for frag in _CACHE_NAMES):
            hits.append(f"{label}:{node.lineno}")
    return hits
