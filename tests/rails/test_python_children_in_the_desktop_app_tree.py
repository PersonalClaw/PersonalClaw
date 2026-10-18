"""The source-tree checks split from
``tests/test_python_children_in_the_desktop_app.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from python_children_in_the_desktop_app_shared import _REPO, _declared

_SRC = _REPO / "src" / "personalclaw"


#: Each call site that starts a child with this interpreter, and what kind of child it is.
#: ``cli`` is this install's CLI (one place only); ``module`` a declared child module; anything
#: else is a child that needs a Python interpreter, with why, and does not start in the desktop app.
_CLASSIFIED: dict[str, str] = {
    "self_update.py::python_child_argv": "helper",
    "self_update.py::cli_argv": "cli",
    "sandbox.py::spawn_shim_argv": "module",
    "computer_use/service.py::_driver_argv": "module",
    "evals/runner.py::_spawn_cell": "module",
    "uploads/content_scan.py::scan_argv": "module",
    "_installer.py::_pip": (
        "interpreter: pip, run as a module of this interpreter's environment; refused there"
    ),
    "_installer.py::install_argv": (
        "interpreter: pip installs into this interpreter's environment; never reached in the "
        "desktop app, whose update is a new download (self_update.detect_install_kind)"
    ),
    "_installer.py::checkout_install_argv": "interpreter: installs a source checkout, never frozen",
    "apps/app_python.py::child_argv": (
        "interpreter: an app's own Python entry script, with the packages the app declared; "
        "refused with its app (python_children.app_refusal) and by both supervisors' starts"
    ),
    "apps/backend_runtime.py::BackendSupervisor._launch_cmd": (
        "interpreter: an app's own Python entry script; refused with its app and by "
        "BackendSupervisor.start and WorkerSupervisor._spawn"
    ),
    "apps/quality.py::run_bundle_tests": (
        "interpreter: an app's own tests, under pytest; refused there"
    ),
    "cli_doctor.py::_doctor": (
        "interpreter: checks the packages of this interpreter's environment; the desktop app's "
        "rows are read in-process instead (cli_doctor._bundle_rows)"
    ),
    "knowledge_providers/pack_parse.py::run_parse_script": (
        "interpreter: a generated parse script, run in isolated mode; refused there"
    ),
    "local_models/sidecar.py::SidecarInstall._step_venv": (
        "interpreter: makes a virtual environment from this interpreter; refused by Install "
        "engine (ModelDownloadRegistry.start_install) and SidecarInstall.run_one"
    ),
    "sandbox.py::namespace_argv": (
        "interpreter: the Linux namespace launcher, a generated script; refused there, and the "
        "command never runs without it"
    ),
}


def _is_sys_executable(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """The module-level ``NAME = "text"`` constants, which a call site may name its module by."""
    found: dict[str, str] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    found[target.id] = value.value
    return found


#: The one helper that starts a Python child running one of the package's modules.
_HELPER = "python_child_argv"


def _is_helper_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name == _HELPER


def _module_of(argv: list[str]) -> str | None:
    """The module an interpreter's argv runs with ``-m``, after any flags before it."""
    for flag, module in zip(argv, argv[1:]):
        if flag == "-m":
            return module
        if not flag.startswith("-"):
            return None
    return None


def _census() -> dict[str, list[list[str]]]:
    """Every list or tuple in ``src/personalclaw`` that starts with ``sys.executable``, by the
    ``file::qualname`` it sits in, as the text of its next three items (a module name resolved).
    A call of ``python_child_argv(<module>, …)`` is the argv it makes from an interpreter,
    ``-P -m <module>``."""
    sites: dict[str, list[list[str]]] = {}
    for path, tree in source_index.package_trees():
        constants = _module_constants(tree)
        rel = path.relative_to(_SRC).as_posix()
        scope: list[str] = []

        def _text(node: ast.AST) -> str:
            if isinstance(node, ast.Constant):
                return str(node.value)
            if isinstance(node, ast.Name) and node.id in constants:
                return constants[node.id]
            return ast.unparse(node)

        class _Visitor(ast.NodeVisitor):
            def _scoped(self, node: ast.AST, name: str) -> None:
                scope.append(name)
                self.generic_visit(node)
                scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._scoped(node, node.name)

            def _argv(self, node: ast.List | ast.Tuple) -> None:
                if node.elts and _is_sys_executable(node.elts[0]):
                    key = f"{rel}::{'.'.join(scope)}"
                    sites.setdefault(key, []).append([_text(e) for e in node.elts[1:4]])
                self.generic_visit(node)

            visit_List = _argv
            visit_Tuple = _argv

            def visit_Call(self, node: ast.Call) -> None:
                if _is_helper_call(node) and node.args:
                    key = f"{rel}::{'.'.join(scope)}"
                    sites.setdefault(key, []).append(["-P", "-m", _text(node.args[0])])
                self.generic_visit(node)

        _Visitor().visit(tree)
    return sites


def test_the_census_is_not_vacuous():
    """A floor: the walk must find the children it exists for, the shim's among them."""
    sites = _census()
    assert "sandbox.py::spawn_shim_argv" in sites
    assert "self_update.py::cli_argv" in sites
    assert "self_update.py::python_child_argv" in sites
    assert len(sites) >= 10, sorted(sites)


def test_every_interpreter_child_is_classified():
    sites = _census()
    assert sorted(sites) == sorted(_CLASSIFIED), (
        "a child started with this interpreter is either new (classify it in _CLASSIFIED: this "
        "install's CLI through self_update.cli_argv, a module declared in "
        "_frozen_child.CHILD_MODULES started through self_update.python_child_argv, or a child "
        "that needs an interpreter, and say why) or gone "
        f"(drop it): census-only {sorted(set(sites) - set(_CLASSIFIED))}, "
        f"classified-only {sorted(set(_CLASSIFIED) - set(sites))}"
    )


def test_only_cli_argv_spells_out_this_installs_cli():
    """``-m personalclaw`` is refused by the frozen bundle, so no other site may spell it."""
    spelled = sorted(
        key
        for key, argvs in _census().items()
        if any(_module_of(argv) == "personalclaw" for argv in argvs)
    )
    assert spelled == ["self_update.py::cli_argv"]


def test_every_package_module_child_ignores_its_working_folder():
    """🔴 ``-m`` alone puts the child's working folder first on its import path, so a folder
    holding a ``personalclaw`` package ran its code instead of this install's. Every child that
    runs one of the package's modules carries ``-P`` from an interpreter; only the helper itself
    spells the bundle's form, which has no working folder on its path to drop."""
    unsafe = sorted(
        key
        for key, argvs in _census().items()
        if key != f"self_update.py::{_HELPER}"
        for argv in argvs
        if (module := _module_of(argv)) is not None
        and (module == "personalclaw" or module.startswith("personalclaw."))
        and "-P" not in argv[: argv.index("-m")]
    )
    assert not unsafe, f"start these through self_update.{_HELPER}: {unsafe}"


def test_every_module_child_is_one_the_bundle_runs():
    """A child module the bundle's entry does not run reaches its CLI parser instead."""
    modules = {
        key: [module for argv in argvs if (module := _module_of(argv)) is not None]
        for key, argvs in _census().items()
        if _CLASSIFIED.get(key) == "module"
    }
    for key, named in modules.items():
        assert named, f"{key} is classified as a module child but starts no `-m <module>`"
        for module in named:
            assert module in _declared(), f"{key} starts {module}, not declared"
    used = {module for named in modules.values() for module in named}
    assert used == set(_declared()), "a declared module no call site starts"
