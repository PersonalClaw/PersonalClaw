"""Every third-party module the APP INSTALLER imports must be a declared core dependency.

The installer's dependency guard (`apps/app_manager.py::_reject_core_dependency_conflicts`,
EI-12 D3) proves that an app's declared `pythonDependencies` cannot move a gateway dependency,
and it **fails closed**: no evaluator means the install is refused. So a module it imports that
core does not declare is not a soft degradation — it refuses every app that declares any
`pythonDependencies` at all.

That is exactly what shipped. `packaging` was undeclared, and on a clean wheel install
`openrouter-models`, `claude-subscription` and both speech apps each failed with::

    cannot verify app openrouter-models's python dependencies against core's
    (No module named 'packaging'); refusing rather than risk moving a gateway dependency

🪤 **A dev checkout cannot see this defect.** pip's own build tooling installs `packaging` into
the venv transitively, so `import packaging` succeeds in development and in CI while being
absent from the wheel's declared dependency set. That is why these tests assert the
**declaration** in `pyproject.toml` and never importability — an importability test passes in
dev whether or not the bug is present, which makes it exactly the vacuous shape that let this
ship. Same family as a wheel that serves a placeholder SPA: the artifact is broken in a way the
source tree is not.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
APP_MANAGER = REPO / "src" / "personalclaw" / "apps" / "app_manager.py"
# The installer spans three modules: the lifecycle and its guard, where app packages go and
# how pip is run over them, and which pip runs. An undeclared import in any of them refuses
# app installs on a clean wheel exactly as one in app_manager.py did.
INSTALLER_MODULES = (
    APP_MANAGER,
    REPO / "src" / "personalclaw" / "apps" / "app_python.py",
    REPO / "src" / "personalclaw" / "_installer.py",
)

# Modules that are stdlib, or first-party, and so need no declaration.
_STDLIB_OR_FIRST_PARTY = {"importlib", "personalclaw", "__future__"}


def _declared_core_dependencies() -> set[str]:
    """The distribution names in `[project].dependencies`, normalised.

    Parsed out of the file rather than read from installed metadata on purpose: installed
    metadata reflects the environment, and the environment is the thing that hides this bug.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
    assert block, "could not find [project].dependencies in pyproject.toml"
    names: set[str] = set()
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue  # a comment, or the blank between entries
        spec = line.strip('",')
        name = re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0]
        if name:
            names.add(name.replace("_", "-").lower())
    return names


def _modules_imported_by(path: Path) -> set[str]:
    """Top-level module names imported anywhere in `path`, including inside functions.

    Function-local imports matter here: the installer guard imports `packaging` inside
    `_reject_core_dependency_conflicts` precisely so a missing evaluator is catchable, so a
    module-level-only scan would read zero and this rail would be vacuous.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


def test_packaging_is_a_declared_core_dependency() -> None:
    """The named instance, pinned by name so the regression has a specific test.

    `packaging` is not optional for the installer: the guard cannot evaluate a version
    specifier without it and therefore refuses the install.
    """
    assert "packaging" in _declared_core_dependencies(), (
        "packaging is imported by the app installer's dependency guard, which FAILS CLOSED "
        "— undeclared, it refuses every app that declares pythonDependencies. Declare it in "
        "[project].dependencies."
    )


@pytest.mark.parametrize("module", INSTALLER_MODULES, ids=lambda p: p.name)
def test_every_third_party_module_the_installer_imports_is_declared(module: Path) -> None:
    """The general rule, so the next such import cannot ship undeclared either."""
    imported = _modules_imported_by(module)
    assert (
        "packaging" in imported or module.name == "_installer.py"
    ), f"{module.name} no longer imports packaging — this rail's population moved; re-read it"
    declared = _declared_core_dependencies()
    missing = sorted(
        m
        for m in imported
        if m not in _STDLIB_OR_FIRST_PARTY
        and m.replace("_", "-").lower() not in declared
        and m not in _stdlib_names()
    )
    assert missing == [], (
        f"{module.name} imports {missing}, which [project].dependencies does not declare. "
        "The installer's dependency guard fails closed, so an undeclared import refuses app "
        "installs on a clean wheel while working fine in a dev checkout."
    )


def _stdlib_names() -> set[str]:
    import sys

    return set(sys.stdlib_module_names)


def test_the_guard_actually_runs_rather_than_refusing(tmp_path: Path) -> None:
    """Drive the guard. A declaration is necessary but not sufficient — the guard must EVALUATE.

    This is the behavioural half: it calls the real function with a requirement naming a real
    core dependency, and asserts it does not raise the "cannot verify" refusal. Without this,
    the two tests above could pass while the guard still failed for some other reason.
    """
    from personalclaw.apps.app_manager import _reject_core_dependency_conflicts
    from personalclaw.apps.manifest import AppManifest

    manifest = AppManifest(
        name="dep-guard-probe",
        version="1.0.0",
        displayName="Dep Guard Probe",
        description="A probe that names a core dependency so the guard must evaluate it.",
    )
    # `numpy` is a declared core dependency, so this requirement lands in the guard's
    # collision set — the population it actually evaluates — rather than being skipped.
    _reject_core_dependency_conflicts(manifest, ["numpy"])
