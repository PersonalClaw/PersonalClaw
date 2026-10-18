"""The source-tree checks split from
``tests/test_app_catalog.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import app_catalog_shared
import source_index

_isolate = app_catalog_shared._isolate


def _scanner_gate_call_sites(token: str) -> set[tuple[str, str]]:
    """Census ``token`` across the shipped package → {(module path, enclosing def)}.

    Enclosing *function* rather than line number on purpose: a gate can be moved into
    a different function while the line count stays put, and the function is what the
    caller reaches. Uses ``ast`` so a match inside a string or comment still resolves
    to a real def (or to ``<module>`` if it is not inside one at all).
    """
    import ast

    import personalclaw

    pkg_root = Path(personalclaw.__file__).resolve().parent
    files = list(source_index.package_files())
    # Vacuity floor: a broken glob (wrong root, wrong suffix) reads as "no call sites"
    # and would make every assertion below trivially true.
    assert len(files) > 100, f"census walked only {len(files)} files under {pkg_root}"

    sites: set[tuple[str, str]] = set()
    for path in files:
        text = source_index.text(path)
        if token not in text:
            continue
        tree = source_index.tree(path)
        owners: list[tuple[int, int, str]] = [
            (n.lineno, n.end_lineno or n.lineno, n.name)
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for lineno, line in enumerate(text.splitlines(), start=1):
            if token not in line:
                continue
            enclosing = [name for start, end, name in owners if start <= lineno <= end]
            sites.add(
                (
                    path.relative_to(pkg_root).as_posix(),
                    enclosing[-1] if enclosing else "<module>",
                )
            )
    return sites


def test_the_install_scanner_gate_has_exactly_three_call_sites():
    """No new install path — pinned by census, not by reading a diff.

    ``default_scanner.scan(`` is the supply-chain gate over a STAGED tree. The whole
    package reaches it from two places, and only one of them is the app lifecycle:

    * ``app_manager._review`` — the ONE gate every app lifecycle path crosses before
      consent. It used to be inlined twice (``install`` and ``update``); the consent
      review (``preview``) made it three readers of one gate, and three copies of a gate
      are three chances to disagree about what a bundle is. Its callers are pinned below.
    * ``supply_chain.scan_dir`` — the module-level re-export, whose only production
      caller is ``skills/marketplace.py`` (a SKILL, not an app). Listed so the census
      is the real one rather than a filtered one; it is not an app install path.

    Adding a registry-aware install route (the tempting shortcut is "it came from the
    curated registry, skip the scan") must either add a call site — which reds this —
    or route around the scanner entirely, which reds
    ``test_a_registry_listed_app_still_hits_the_scanner_gate``. Between the two rails
    there is no third way to add an ungated install path.

    If you are here because this test went red: the fix is not to widen the expected
    set. It is to justify the new install path, and then widen it deliberately.
    """
    expected = {
        ("apps/app_manager.py", "_review"),
        ("supply_chain.py", "scan_dir"),
    }
    assert _scanner_gate_call_sites("default_scanner.scan(") == expected
    # …and the one lifecycle gate is reached by exactly the three lifecycle entry points:
    # first install, update over an installed app, and the read-only consent review.
    # (The call shape, so the `def _review(staged: Path, …)` line is not counted as a caller.)
    assert _scanner_gate_call_sites("_review(staged, origin") == {
        ("apps/app_manager.py", "install"),
        ("apps/app_manager.py", "update"),
        ("apps/app_manager.py", "preview"),
    }

    # Vacuity control: the same census over a token that is not in the package must
    # come back empty. Without this, a walker that silently reads nothing (or an
    # `ast.parse` that raised into a swallowed except) would look like agreement.
    assert _scanner_gate_call_sites("default_scanner.scan_every_registry_app(") == set()
