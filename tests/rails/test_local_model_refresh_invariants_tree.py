"""The source-tree checks split from
``tests/test_local_model_refresh_invariants.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

import personalclaw


def _modules_defining_refresh_providers() -> set[str]:
    """Every module in the shipped package that defines a module-level ``refresh_providers``.

    DISCOVERED from source rather than enumerated. The invariant is "a refreshing registry
    may only drop a NAMED population", and a hand-written list cannot state that about a
    registry nobody remembered to add to the list — the check would report clean for the one
    case it exists to catch. Cheap text prefilter, then an AST confirmation, so a mention in
    a docstring or a call site is never mistaken for a definition.
    """
    pkg_root = Path(personalclaw.__file__).resolve().parent
    found: set[str] = set()
    for path in source_index.package_files():
        if "def refresh_providers" not in source_index.text(path):
            continue
        tree = source_index.tree(path)
        if not any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "refresh_providers"
            for n in tree.body
        ):
            continue
        rel = path.relative_to(pkg_root).with_suffix("")
        found.add(".".join(("personalclaw", *rel.parts)))
    return found


def test_every_use_case_registry_that_refreshes_declares_a_transient_population():
    """The invariant stated structurally: a registry may only drop a NAMED population.

    Any future ``refresh_providers()`` that clears ``_providers`` wholesale would have to
    delete one of these markers to pass, which is the point — the regression is that the
    drop was untargeted.

    The population it checks is DERIVED (see ``_modules_defining_refresh_providers``) and
    reconciled against this map in both directions, so a fifth registry that grows a
    ``refresh_providers`` is a failure here rather than a silent gap: it inherits the
    invariant on the day it is written, not on the day someone remembers this file.
    """
    import importlib

    markers = {
        "personalclaw.stt.registry": "_remote_names",
        "personalclaw.tts.registry": "_remote_names",
        "personalclaw.video_gen.registry": "_scanner_names",
        "personalclaw.image_gen.registry": "_auto_registered",
        "personalclaw.embedding_providers.registry": "_scanned",
    }

    discovered = _modules_defining_refresh_providers()
    # Vacuity: an empty discovery would make the subset check below trivially true. The
    # four known refreshers are the floor, and stt is asserted by name because it is the
    # registry the original two-population bug actually broke.
    assert len(discovered) >= 4, f"the refresh_providers scan found only {sorted(discovered)}"
    assert "personalclaw.stt.registry" in discovered, sorted(discovered)

    undeclared = discovered - set(markers)
    assert not undeclared, (
        f"{sorted(undeclared)} define refresh_providers() but are not covered by the "
        f"two-population invariant. Add each to this map with "
        f"the name of the transient population it is allowed to drop, and give it a case "
        f"above asserting the app-contributed population survives."
    )
    gone = set(markers) - discovered
    assert not gone, (
        f"{sorted(gone)} no longer define refresh_providers() — a rail entry matching "
        f"nothing looks clean. Drop the stale entries."
    )

    for mod_path, marker in markers.items():
        mod = importlib.import_module(mod_path)
        assert hasattr(mod, "refresh_providers"), mod_path
        assert hasattr(mod, marker), f"{mod_path} lost its transient-population marker"
