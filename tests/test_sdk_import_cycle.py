"""``personalclaw.sdk.*`` must import cleanly whichever submodule an app reaches for FIRST.

``personalclaw.sdk`` is the only sanctioned import path for an app bundle
(``tests/test_apps_import_boundary.py`` pins that direction), so an SDK submodule that
loads only when a SIBLING was imported before it is a boundary defect, not a style nit.

The defect this file exists to keep out: ``sdk/provider_helpers.py`` imported its core
machinery from the sibling facade ``sdk/model.py``, which re-exports
``register_branded_app`` from ``provider_helpers`` — a module-scope cycle. From a cold
interpreter ``import personalclaw.sdk.model`` worked and ``import
personalclaw.sdk.provider_helpers`` raised ``ImportError: cannot import name
'register_branded_app' from partially initialized module``. Nothing in the SDK chose that
winner: ``model`` merely sorts before ``provider_helpers``, and the one app that imports
both happened to name ``sdk.model`` first. An import-sorter change or one new app importing
``provider_helpers`` first would have broken every branded provider app.

Why a subprocess per order rather than two imports in one test: by the time pytest has
collected this file ``sys.modules`` already holds both modules, imported in whatever order
the rest of the suite established. A test that imports both in-process passes on the broken
tree and proves nothing. The call site that matters is a COLD interpreter, so each order
gets its own.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import personalclaw

#: Both SDK modules that were the two halves of the cycle. Each must load FIRST.
_SDK_MODULES = ("personalclaw.sdk.model", "personalclaw.sdk.provider_helpers")

#: The sibling facade ``provider_helpers`` must never import at module scope — that edge is
#: the cycle. ``sdk.model`` imports ``provider_helpers``; the traffic runs one way only.
_FACADE = "personalclaw.sdk.model"
_HELPERS_PATH = Path(personalclaw.__file__).resolve().parent / "sdk" / "provider_helpers.py"


def _src_root() -> str:
    """The directory that must be on a child interpreter's path for ``personalclaw`` to
    import. Derived from the LOADED package rather than from ``__file__`` here, so this
    works both for an editable install (``<repo>/src``) and a site-packages install."""
    return str(Path(personalclaw.__file__).resolve().parent.parent)


def _cold_import(module: str, *, extra_path: str | None = None, home: Path) -> tuple[int, str]:
    """Import ``module`` FIRST in a brand-new interpreter. Returns ``(returncode, stderr)``.

    ``PERSONALCLAW_HOME`` is pointed at ``home`` so a child that touches state on import can
    never reach the real home. Not ``-I``: isolated mode drops ``PYTHONPATH``, which is how
    the child finds an uninstalled source tree.
    """
    path = _src_root() if extra_path is None else os.pathsep.join([extra_path, _src_root()])
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": path, "PERSONALCLAW_HOME": str(home)},
    )
    return proc.returncode, proc.stderr


@pytest.mark.parametrize("module", _SDK_MODULES)
def test_each_sdk_module_imports_first_from_a_cold_interpreter(module, tmp_path):
    """The whole point: BOTH orders, each from its own cold interpreter. On the broken tree
    ``provider_helpers`` reds here and ``model`` stays green — which is exactly why one
    module is not enough and why the parametrisation must not be collapsed."""
    code, stderr = _cold_import(module, home=tmp_path)
    assert code == 0, (
        f"`import {module}` fails from a cold interpreter while its sibling "
        f"{[m for m in _SDK_MODULES if m != module][0]!r} presumably still works — that is a "
        "module-scope import cycle inside the SDK, whose winner is decided by whichever "
        "module an app happens to name first. Break the cycle by importing the shared core "
        "machinery from `personalclaw.llm.*` directly; do NOT defer the import to a "
        "different position, which only moves which order happens to work.\n\n"
        f"child stderr:\n{stderr}"
    )


def test_the_cold_import_probe_reds_on_a_real_cycle(tmp_path):
    """VACUITY: prove the probe above can FAIL, rather than being green because subprocess
    errors are swallowed. Builds two throwaway modules in the exact shape that was broken —
    each importing a name the other defines below its own import — and asserts the SAME
    helper reports the SAME ``partially initialized module`` ImportError. If this test ever
    passes trivially (or the helper stops surfacing a non-zero code), the real probe is
    decoration."""
    pkg = tmp_path / "cyclic_probe_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "alpha.py").write_text(
        "from cyclic_probe_pkg.beta import NAME_IN_BETA\n\nNAME_IN_ALPHA = 'a'\n",
        encoding="utf-8",
    )
    (pkg / "beta.py").write_text(
        "from cyclic_probe_pkg.alpha import NAME_IN_ALPHA\n\nNAME_IN_BETA = 'b'\n",
        encoding="utf-8",
    )

    code, stderr = _cold_import("cyclic_probe_pkg.alpha", extra_path=str(tmp_path), home=tmp_path)
    assert code != 0, (
        "the cold-import helper reported SUCCESS for a module pair that is definitionally "
        f"circular — it cannot detect the defect it is here to detect. stderr:\n{stderr}"
    )
    assert "partially initialized module" in stderr, (
        "the helper failed for some reason OTHER than the circular import it must detect "
        f"(a path/env problem would also red this) — stderr:\n{stderr}"
    )

    # And the same helper is GREEN on a non-circular module in the same tree, so the red
    # above is attributable to the CYCLE and not to the throwaway package being unimportable.
    ok_code, ok_stderr = _cold_import("cyclic_probe_pkg", extra_path=str(tmp_path), home=tmp_path)
    assert ok_code == 0, f"the throwaway package is unimportable for its own reasons:\n{ok_stderr}"


def _module_scope_imports(path: Path) -> set[str]:
    """Absolute ``personalclaw.*`` modules imported at MODULE scope by ``path``.

    Parsed, not grepped: only top-level nodes count (a deliberately deferred import inside a
    function is not a cycle), and ``import x as y`` / relative forms are resolved rather than
    missed by a string match.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg_parts = path.resolve().parent.relative_to(Path(_src_root())).as_posix().split("/")
    found: set[str] = set()
    for node in tree.body:  # top level ONLY
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("personalclaw"))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module and node.module.startswith("personalclaw"):
                    found.add(node.module)
            else:
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                found.add(".".join([*base, *([node.module] if node.module else [])]))
    return found


def test_provider_helpers_does_not_import_the_sibling_facade_at_module_scope():
    """Pins the MECHANISM, so a reviewer sees the rule rather than inferring it from a
    subprocess red: ``sdk.model`` re-exports ``register_branded_app`` from
    ``provider_helpers``, so ``provider_helpers`` importing ``sdk.model`` back closes the
    loop. Both modules are thin re-export surfaces over ``personalclaw.llm.*`` — there is
    nothing to gain by routing one through the other."""
    imported = _module_scope_imports(_HELPERS_PATH)
    assert _FACADE not in imported, (
        f"{_HELPERS_PATH.name} imports the sibling facade {_FACADE!r} at module scope, which "
        "re-exports this module's `register_branded_app` — that is the cycle. Import the "
        "core machinery from `personalclaw.llm.*` directly instead."
    )
    # Vacuity: the parser sees this file's imports at all. A rule whose input is an empty set
    # is a rule that matches nothing and reads clean forever.
    assert {m for m in imported if m.startswith("personalclaw.llm")}, (
        f"parsed ZERO `personalclaw.llm.*` module-scope imports out of {_HELPERS_PATH} — the "
        "assertion above is vacuous; fix the parser before trusting its green"
    )
