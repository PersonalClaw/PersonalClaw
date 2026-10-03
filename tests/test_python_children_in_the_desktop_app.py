"""Every child PersonalClaw starts with its own interpreter is one the desktop app can start.

In the desktop app ``sys.executable`` is the frozen bundle's executable, whose entry script is the
CLI. A child started as an interpreter would start it (``<sys.executable> -m …``, ``-c …``, a
script path) reached that CLI's parser and exited at once with a usage error: a restart's new
gateway, ``personalclaw restart``'s and ``personalclaw run``'s gateways, the update's agent-config
refresh, and the resource-ceiling shim in front of every tool command, MCP server, app backend and
worker the gateway starts.

So there are two ways a child starts, and each call site takes one:

* **this install's CLI**, through ``self_update.cli_argv`` (the bundle's executable, or this
  interpreter's ``-m personalclaw``), and nowhere else;
* **one of the package's own child modules** as ``<sys.executable> -m <module>``, which the
  bundle's entry script runs the way ``python -m`` does, for exactly the modules
  ``_frozen_child.CHILD_MODULES`` declares (and which the bundle therefore carries).

What is left needs a real Python interpreter, which the desktop app does not have, and is named
below with the reason. The census is an AST walk over every list or tuple whose first item is
``sys.executable``: a new one reds :func:`test_every_interpreter_child_is_classified` until its
author says which kind it is.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw import self_update
from personalclaw.cli import build_parser

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "personalclaw"

#: Each call site that starts a child with this interpreter, and what kind of child it is.
#: ``cli`` is this install's CLI (one place only); ``module`` a declared child module; anything
#: else is a child that needs a Python interpreter, with why, and does not start in the desktop app.
_CLASSIFIED: dict[str, str] = {
    "self_update.py::cli_argv": "cli",
    "sandbox.py::spawn_shim_argv": "module",
    "computer_use/service.py::_driver_argv": "module",
    "evals/runner.py::_spawn_cell": "module",
    "_installer.py::_pip": "interpreter: pip, run as a module of this interpreter's environment",
    "_installer.py::install_argv": "interpreter: pip installs into this interpreter's environment",
    "_installer.py::checkout_install_argv": "interpreter: installs a source checkout, never frozen",
    "apps/app_python.py::child_argv": (
        "interpreter: an app's own Python entry script, with the packages the app declared"
    ),
    "apps/backend_runtime.py::BackendSupervisor._launch_cmd": (
        "interpreter: an app's own Python entry script"
    ),
    "apps/quality.py::run_bundle_tests": "interpreter: an app's own tests, under pytest",
    "cli_doctor.py::_doctor": "interpreter: checks the packages of this interpreter's environment",
    "knowledge_providers/pack_parse.py::run_parse_script": (
        "interpreter: a generated parse script, run in isolated mode"
    ),
    "local_models/sidecar.py::SidecarInstall._step_venv": (
        "interpreter: makes a virtual environment from this interpreter"
    ),
    "sandbox.py::namespace_argv": "interpreter: the Linux namespace launcher, a generated script",
}


def _declared() -> tuple[str, ...]:
    """The modules the bundle's entry runs (``_frozen_child.CHILD_MODULES``)."""
    from personalclaw import _frozen_child

    return _frozen_child.CHILD_MODULES


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


def _census() -> dict[str, list[list[str]]]:
    """Every list or tuple in ``src/personalclaw`` that starts with ``sys.executable``, by the
    ``file::qualname`` it sits in, as the text of its next two items (a module name resolved)."""
    sites: dict[str, list[list[str]]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
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
                    sites.setdefault(key, []).append([_text(e) for e in node.elts[1:3]])
                self.generic_visit(node)

            visit_List = _argv
            visit_Tuple = _argv

        _Visitor().visit(tree)
    return sites


def test_the_census_is_not_vacuous():
    """A floor: the walk must find the children it exists for, the shim's among them."""
    sites = _census()
    assert "sandbox.py::spawn_shim_argv" in sites
    assert "self_update.py::cli_argv" in sites
    assert len(sites) >= 10, sorted(sites)


def test_every_interpreter_child_is_classified():
    sites = _census()
    assert sorted(sites) == sorted(_CLASSIFIED), (
        "a child started with this interpreter is either new (classify it in _CLASSIFIED: this "
        "install's CLI through self_update.cli_argv, a module declared in "
        "_frozen_child.CHILD_MODULES, or a child that needs an interpreter, and say why) or gone "
        f"(drop it): census-only {sorted(set(sites) - set(_CLASSIFIED))}, "
        f"classified-only {sorted(set(_CLASSIFIED) - set(sites))}"
    )


def test_only_cli_argv_spells_out_this_installs_cli():
    """``-m personalclaw`` is refused by the frozen bundle, so no other site may spell it."""
    spelled = sorted(
        key
        for key, argvs in _census().items()
        if any(argv[:2] == ["-m", "personalclaw"] for argv in argvs)
    )
    assert spelled == ["self_update.py::cli_argv"]


def test_every_module_child_is_one_the_bundle_runs():
    """A child module the bundle's entry does not run reaches its CLI parser instead."""
    modules = {
        key: [argv[1] for argv in argvs if argv[:1] == ["-m"]]
        for key, argvs in _census().items()
        if _CLASSIFIED.get(key) == "module"
    }
    for key, named in modules.items():
        assert named, f"{key} is classified as a module child but starts no `-m <module>`"
        for module in named:
            assert module in _declared(), f"{key} starts {module}, not declared"
    used = {module for named in modules.values() for module in named}
    assert used == set(_declared()), "a declared module no call site starts"


def test_every_declared_module_is_a_module_of_the_package():
    missing = [module for module in _declared() if importlib.util.find_spec(module) is None]
    assert not missing, missing


def test_the_bundle_carries_every_declared_module():
    """Nothing imports them by name, so the bundle has them only because the spec asks."""
    spec = importlib.util.spec_from_file_location(
        "backend_bundle_manifest", _REPO / "scripts" / "backend_bundle_manifest.py"
    )
    assert spec and spec.loader
    manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest)
    assert manifest.child_modules(_REPO) == list(_declared())
    assert "manifest.child_modules()" in (_REPO / "personalclaw-backend.spec").read_text(
        encoding="utf-8"
    )


class TestTheEntryRunsOnlyDeclaredModules:
    def test_a_declared_module_is_run(self):
        from personalclaw import _frozen_child

        argv = ["/app/personalclaw-backend", "-m", "personalclaw._spawn_exec_shim", "{}", "--", "x"]
        assert _frozen_child.child_module(argv) == "personalclaw._spawn_exec_shim"

    @pytest.mark.parametrize(
        "argv",
        [
            ["/app/personalclaw-backend", "gateway", "--port", "auto"],
            ["/app/personalclaw-backend", "-m", "personalclaw", "gateway"],
            ["/app/personalclaw-backend", "-m", "os"],
            ["/app/personalclaw-backend", "-c", "pass"],
            ["/app/personalclaw-backend", "-m"],
            ["/app/personalclaw-backend"],
        ],
    )
    def test_anything_else_is_the_clis_to_decide(self, argv):
        from personalclaw import _frozen_child

        assert _frozen_child.child_module(argv) is None


def _frozen_entry(
    tmp_path: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the package's entry script as the frozen bundle runs it: as ``__main__``, in a process
    that reads as frozen, with ``sys.executable`` first on its command line and *args* after it.
    A scratch home, and the shim's own environment otherwise, so nothing reaches the real one."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    code = (
        "import runpy, sys\n"
        "sys.frozen = True\n"
        "sys.argv = [sys.executable, *sys.argv[1:]]\n"
        "runpy.run_module('personalclaw', run_name='__main__')\n"
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PERSONALCLAW_HOME": str(home / ".personalclaw"),
        **(env or {}),
    }
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(tmp_path),
    )


def test_the_frozen_entry_runs_the_ceiling_shim_like_python_dash_m(tmp_path):
    """🔴 The shim fronts every tool command, MCP server, app backend and worker. Frozen, its
    command line used to reach the CLI's parser, which refused it (exit 2), so none started."""
    done = _frozen_entry(
        tmp_path,
        "-m",
        "personalclaw._spawn_exec_shim",
        '{"limits": {}, "oom_score_adj": null}',
        "--",
        "/bin/echo",
        "the child ran",
    )
    assert (done.returncode, done.stdout) == (0, "the child ran\n"), done.stderr


def test_what_the_shim_runs_gets_the_environment_it_was_given(tmp_path):
    """The bundle's bootloader sets its own ``_PYI_…`` variables before any Python runs (measured
    on a built bundle: its archive and its process level). The shim used to hand them on to every
    command it ran, past the child environment the gateway had built without them."""
    done = _frozen_entry(
        tmp_path,
        "-m",
        "personalclaw._spawn_exec_shim",
        '{"limits": {}, "oom_score_adj": null}',
        "--",
        "/usr/bin/env",
        env={"_PYI_ARCHIVE_FILE": "/app/personalclaw-backend", "_PYI_PARENT_PROCESS_LEVEL": "1"},
    )
    assert done.returncode == 0, done.stderr
    names = {line.split("=", 1)[0] for line in done.stdout.splitlines()}
    assert "PERSONALCLAW_HOME" in names, "the floor: the environment it was given reached it"
    assert not {n for n in names if n.startswith("_PYI_")}, sorted(names)


class TestTheBundleGivesBackItsEnvironment:
    def test_the_bootloaders_own_variables_go_and_nothing_else(self, monkeypatch):
        from personalclaw import _frozen_child

        monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/app/personalclaw-backend")
        monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
        monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
        _frozen_child.restore_environment()
        assert not [name for name in os.environ if name.startswith("_PYI_")]
        assert os.environ["PERSONALCLAW_INSTALL_KIND"] == "desktop"

    def test_on_linux_the_library_path_it_replaced_goes_back(self, monkeypatch):
        from personalclaw import _frozen_child

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("sys._MEIPASS", "/opt/PersonalClaw/backend/_internal", raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/PersonalClaw/backend/_internal")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
        _frozen_child.restore_environment()
        assert os.environ["LD_LIBRARY_PATH"] == "/usr/local/lib"
        assert "LD_LIBRARY_PATH_ORIG" not in os.environ

    def test_on_linux_a_library_path_that_is_only_the_bundles_goes(self, monkeypatch):
        from personalclaw import _frozen_child

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("sys._MEIPASS", "/opt/PersonalClaw/backend/_internal", raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/PersonalClaw/backend/_internal")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
        _frozen_child.restore_environment()
        assert "LD_LIBRARY_PATH" not in os.environ

    def test_a_library_path_of_the_owners_own_is_left_alone(self, monkeypatch):
        from personalclaw import _frozen_child

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("sys._MEIPASS", "/opt/PersonalClaw/backend/_internal", raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/home/user/lib")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
        _frozen_child.restore_environment()
        assert os.environ["LD_LIBRARY_PATH"] == "/home/user/lib"


def test_the_frozen_entry_still_refuses_what_is_not_declared(tmp_path):
    """The control: an interpreter's command line naming anything else is the CLI's, and its
    parser refuses it as it always has."""
    done = _frozen_entry(tmp_path, "-m", "personalclaw.cli")
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "invalid choice: 'personalclaw.cli'" in done.stderr


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """This process reads as the desktop app's frozen bundle, with an executable to name."""
    bundle = tmp_path / "personalclaw-backend"
    bundle.write_text("#!/bin/sh\n", encoding="utf-8")
    bundle.chmod(0o755)
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr("sys.executable", str(bundle))
    return str(bundle)


def _accepted(argv: list[str], bundle: str) -> str:
    """*argv* starts the bundle and its CLI parser accepts the rest; returns the subcommand."""
    assert argv[0] == bundle, argv
    return build_parser().parse_args(argv[1:]).command  # a usage error raises SystemExit(2)


class TestEveryCliChildStartsInTheBundle:
    def test_the_cli_is_the_bundle_itself(self, frozen):
        assert self_update.cli_argv() == [frozen]

    def test_an_interpreter_runs_it_as_a_module(self):
        assert self_update.cli_argv() == [sys.executable, "-m", "personalclaw"]

    def test_the_availability_probe(self, frozen):
        from personalclaw.providers import availability

        assert _accepted(availability.probe_argv(["some-app"]), frozen) == "availability-probe"

    def test_the_upload_content_scan(self, frozen):
        from personalclaw.uploads import content_scan

        assert _accepted(content_scan.scan_argv(), frozen) == "content-scan"

    def test_personalclaw_restarts_detached_gateway(self, frozen, tmp_path, monkeypatch):
        """🔴 ``personalclaw restart`` with no service spawned ``<bundle> -m personalclaw``."""
        from personalclaw import cli_server

        monkeypatch.setattr(cli_server, "config_dir", lambda: tmp_path)
        with patch("personalclaw.cli_server.subprocess.Popen") as popen:
            cli_server._spawn_detached_gateway(10123)
        assert _accepted(list(popen.call_args.args[0]), frozen) == "gateway"

    def test_personalclaw_runs_transient_gateway(self, frozen):
        """🔴 ``personalclaw run`` with no gateway running spawned ``<bundle> -m personalclaw``."""
        from personalclaw import cli_run

        with (
            patch(
                "personalclaw.cli_run.subprocess.Popen", side_effect=OSError("stop here")
            ) as popen,
            pytest.raises(OSError),
        ):
            cli_run.start_transient_gateway()
        assert _accepted(list(popen.call_args.args[0]), frozen) == "gateway"

    def test_the_updates_agent_config_refresh(self, frozen, tmp_path):
        """🔴 It ran ``<bundle> -m personalclaw setup --agent-only``."""
        from personalclaw import cli_server

        with patch("personalclaw.cli_server.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            cli_server._refresh_agent_config(str(tmp_path))
        assert _accepted(list(run.call_args.args[0]), frozen) == "setup"
